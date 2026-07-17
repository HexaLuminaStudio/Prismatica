# coding: utf-8
"""
分词结果缓存管理器

需求:
    即使是 jieba 这种相对较快的分词器,重复分词同一文本也是浪费。
    通过 SQLite 持久化缓存,可将二次分析降至毫秒级。

设计:
    三层缓存:
        - L1: 进程内 dict (最快,但不跨进程共享)
        - L2: SQLite token_cache 表 (跨会话持久化)
        - L3: 真实后端分词 (cache miss 时)

缓存键:
    (text_hash, backend_name, model_version)
    - text_hash: sha1(text)[:16] (16 字符 hex = 64 位,冲突概率极低)
    - backend_name: 当前后端名('jieba'/'char')
    - model_version: 后端模型版本(jieba 版本变更时自动失效)

性能:
    - cache hit: ~0.5ms (L1) / ~2ms (L2)
    - cache miss: ~32ms (jieba)
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def hashText(text: str) -> str:
    """计算文本的短哈希(16 字符 hex = 64 位)"""
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def backendModelVersion(backendName: str) -> str:
    """获取后端的模型版本字符串

    Args:
        backendName: 'jieba' / 'char'

    Returns:
        版本字符串(版本变更后,旧 cache 自动失效)
    """
    if backendName == "jieba":
        try:
            import jieba

            return f"jieba-{jieba.__version__}"
        except Exception:
            return "jieba-unknown"
    elif backendName == "char":
        return "char-v1"
    return f"{backendName}-unknown"


# ===========================================================================
# TokenCache
# ===========================================================================

# L1 缓存上限(条目数)。
# 设计依据:
#   - 经验值:jieba 平均分词结果 ~ 词 list 引用 ~ 数十~数百 B/条,
#     50000 条 ≈ 几十 MB,处于合理内存预算内;
#   - 超过此数后,冷数据的二次命中率已显著降低,可安全淘汰;
#   - 上限可通过环境变量 TOKEN_CACHE_L1_CAPACITY 覆盖(便于运维调优)。
import os as _os

_DEFAULT_L1_CAPACITY = 50000
try:
    L1_CAPACITY = int(_os.environ.get("TOKEN_CACHE_L1_CAPACITY", _DEFAULT_L1_CAPACITY))
    if L1_CAPACITY < 1024:
        # 容量过小会大幅降低命中率;此处给一个合理下限
        L1_CAPACITY = 1024
except Exception:
    L1_CAPACITY = _DEFAULT_L1_CAPACITY


class TokenCache:
    """分词结果缓存管理器

    用法:
        cache = TokenCache(sqliteConnection, lock)
        tokens = cache.getOrCompute(
            text="今天天气很好",
            backendName="jieba",
            computeFn=lambda t: backend.tokenize(t),
        )

    内存安全:
        L1 进程内缓存使用 OrderedDict 实现 LRU(最近访问在末尾,
        容量耗尽时淘汰最久未使用项),容量上限默认为 L1_CAPACITY(50000 条);
        CorpusStore.close() 会调用 TokenCache.clear() 立即释放内存。
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        """初始化缓存管理器

        Args:
            conn: SQLite 连接(由 CorpusStore 提供)
            lock: SQLite 写锁(由 CorpusStore 提供)
        """
        self._conn = conn
        self._lock = lock

        # L1 cache: text_hash -> tokens (进程内,LRU)
        # key: (text_hash, backend_name, model_version)
        # 使用 OrderedDict 实现 LRU:
        #   - get 时 move_to_end (标记最近使用)
        #   - put 时若超容,popitem(last=False) 淘汰最久未用项
        self._l1: "OrderedDict[tuple, List[str]]" = OrderedDict()
        self._l1Capacity: int = L1_CAPACITY
        self._l1Lock = threading.Lock()
        self._evictedCount = 0  # 累计淘汰次数(诊断用)

        # 待写入 DB 的队列(异步批量写)
        self._writeQueue: List[tuple] = []
        self._writeQueueLock = threading.Lock()
        self._lastWriteTime = time.time()

    def getOrCompute(
        self,
        text: str,
        backendName: str,
        modelVersion: str,
        computeFn,
    ) -> List[str]:
        """获取 token,如果不在 cache 中则调用 computeFn 计算

        Args:
            text: 原始文本
            backendName: 后端名称
            modelVersion: 模型版本
            computeFn: cache miss 时调用的分词函数 fn(text) -> List[str]

        Returns:
            token 列表
        """
        textHash = hashText(text)
        cacheKey = (textHash, backendName, modelVersion)

        # L1: 进程内 LRU 缓存
        with self._l1Lock:
            if cacheKey in self._l1:
                # 命中:移动到末尾(MRU 端)
                self._l1.move_to_end(cacheKey)
                return self._l1[cacheKey]

        # L2: SQLite 缓存
        tokens = self._readFromDb(textHash, backendName, modelVersion)
        if tokens is not None:
            self._putL1(cacheKey, tokens)  # 填充 L1
            return tokens

        # L3: 真实分词
        start = time.time()
        tokens = computeFn(text)
        durationMs = (time.time() - start) * 1000.0

        # 填充 L1 (立即可用)
        self._putL1(cacheKey, tokens)

        # 异步写入 L2 (不阻塞当前调用)
        self._enqueueWrite(textHash, backendName, modelVersion, tokens, durationMs)

        return tokens

    def flush(self, maxWait: float = 0.5):
        """刷新待写入队列到 DB

        Args:
            maxWait: 最长等待时间(秒)
        """
        deadline = time.time() + maxWait
        while time.time() < deadline:
            if not self._writeQueue:
                break
            self._drainQueue()

    def clear(self) -> None:
        """清空 L1 进程内缓存,释放内存。

        使用场景:
            - CorpusStore.close():释放对应语料库的 L1 引用
            - 用户手动「重置缓存」操作
            - 切换大语料库前,避免 L1 持有旧语料 keys 撑爆内存
        """
        with self._l1Lock:
            self._l1.clear()
        logger.info("[TokenCache] L1 缓存已清空")

    def close(self) -> None:
        """关闭缓存管理器:flush 待写入 + 清空 L1。

        CorpusStore.close() 应调用此方法,确保:
            1. 待写入 DB 的 token 全部落盘(避免丢失)
            2. L1 进程内缓存立即释放(避免 OOM 残留)
        """
        try:
            self.flush(maxWait=1.0)
        except Exception as e:
            logger.warning(f"[TokenCache] flush 失败: {e}")
        self.clear()

    def stats(self) -> Dict[str, int]:
        """获取缓存统计"""
        l1Size = len(self._l1)
        pending = len(self._writeQueue)
        return {
            "l1Size": l1Size,
            "l1Capacity": self._l1Capacity,
            "l1Evicted": self._evictedCount,
            "pendingWrites": pending,
        }

    # ---------------- 内部 ----------------
    def _putL1(self, cacheKey: tuple, tokens: List[str]) -> None:
        """LRU-safe 写入 L1:容量耗尽时淘汰最久未使用项。

        线程安全:全程持有 _l1Lock,确保多线程下 LRU 顺序不被破坏。
        """
        with self._l1Lock:
            if cacheKey in self._l1:
                # 已存在 → move_to_end (相当于覆盖 + 标记 MRU)
                self._l1.move_to_end(cacheKey)
                self._l1[cacheKey] = tokens
                return
            # 新增
            self._l1[cacheKey] = tokens
            # 容量控制:超过上限则从 LRU 端淘汰
            while len(self._l1) > self._l1Capacity:
                try:
                    evictedKey, _ = self._l1.popitem(last=False)
                    self._evictedCount += 1
                    logger.debug(
                        f"[TokenCache] LRU 淘汰: {evictedKey[0]}... "
                        f"(已累计 {self._evictedCount} 次)"
                    )
                except KeyError:
                    break

    # ---------------- 内部 ----------------
    def _readFromDb(
        self, textHash: str, backendName: str, modelVersion: str
    ) -> Optional[List[str]]:
        """从 SQLite 读取 token"""
        try:
            with self._lock:
                cur = self._conn.execute(
                    """
                    SELECT tokens_json FROM token_cache
                    WHERE text_hash = ? AND backend_name = ? AND model_version = ?
                    """,
                    (textHash, backendName, modelVersion),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return json.loads(row[0])
        except Exception as e:
            logger.warning(f"[TokenCache] 读取失败: {e}")
            return None

    def _enqueueWrite(
        self,
        textHash: str,
        backendName: str,
        modelVersion: str,
        tokens: List[str],
        durationMs: float,
    ):
        """加入待写入队列"""
        self._writeQueue.append(
            (textHash, backendName, modelVersion, tokens, durationMs)
        )

        # 队列达到阈值或时间到 → 立即批量写入
        if len(self._writeQueue) >= 50 or (time.time() - self._lastWriteTime) > 2.0:
            self._drainQueue()

    def _drainQueue(self):
        """批量写入队列到 DB"""
        with self._writeQueueLock:
            if not self._writeQueue:
                return
            batch = self._writeQueue[:]
            self._writeQueue.clear()

        try:
            with self._lock:
                self._conn.executemany(
                    """
                    INSERT INTO token_cache(
                        text_hash, backend_name, model_version,
                        tokens_json, token_count, duration_ms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(text_hash, backend_name, model_version) DO UPDATE SET
                        tokens_json = excluded.tokens_json,
                        token_count = excluded.token_count,
                        duration_ms = excluded.duration_ms,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    [
                        (h, b, v, json.dumps(t, ensure_ascii=False), len(t), d)
                        for h, b, v, t, d in batch
                    ],
                )
                self._conn.commit()
                self._lastWriteTime = time.time()
                logger.debug(f"[TokenCache] 批量写入 {len(batch)} 条 token cache")
        except Exception as e:
            logger.error(f"[TokenCache] 批量写入失败: {e}")
