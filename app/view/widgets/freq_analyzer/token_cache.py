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
class TokenCache:
    """分词结果缓存管理器

    用法:
        cache = TokenCache(sqliteConnection, lock)
        tokens = cache.getOrCompute(
            text="今天天气很好",
            backendName="jieba",
            computeFn=lambda t: backend.tokenize(t),
        )
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        """初始化缓存管理器

        Args:
            conn: SQLite 连接(由 CorpusStore 提供)
            lock: SQLite 写锁(由 CorpusStore 提供)
        """
        self._conn = conn
        self._lock = lock

        # L1 cache: text_hash -> tokens (进程内)
        # key: (text_hash, backend_name, model_version)
        self._l1: Dict[tuple, List[str]] = {}

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

        # L1: 进程内 dict
        if cacheKey in self._l1:
            return self._l1[cacheKey]

        # L2: SQLite 缓存
        tokens = self._readFromDb(textHash, backendName, modelVersion)
        if tokens is not None:
            self._l1[cacheKey] = tokens  # 填充 L1
            return tokens

        # L3: 真实分词
        start = time.time()
        tokens = computeFn(text)
        durationMs = (time.time() - start) * 1000.0

        # 填充 L1 (立即可用)
        self._l1[cacheKey] = tokens

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

    def stats(self) -> Dict[str, int]:
        """获取缓存统计"""
        l1Size = len(self._l1)
        pending = len(self._writeQueue)
        return {
            "l1Size": l1Size,
            "pendingWrites": pending,
        }

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
