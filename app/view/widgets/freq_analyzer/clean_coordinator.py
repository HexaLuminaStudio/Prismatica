# coding: utf-8
"""清洗规则异步协调器

需求文档:
    test/6D-CorpusClient_需求文档_v3.md §2.3 自建语料库与语料清洗
    NFR-USA-004:所有耗时超过 3 秒的操作须有进度提示

设计目标:
    - 解决「勾选/编辑清洗规则时 UI 卡顿」的问题
    - 规则变更(UI 端) → 去抖 300ms → 后台线程保存到 SQLite + 重新清洗
    - 前台读取 effectiveTexts() 时:
        * 若后台还在清洗,返回上次缓存(避免阻塞 UI)
        * 后台清洗完成后,自动切换为新结果并触发 cleanRuleChanged

模块拆分:
    - CleanWorker(QRunnable): 后台执行「SQLite 持久化 + 全量重洗」
    - CleanCoordinator(QObject): 负责去抖调度 + 任务编排 + 进度信号
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

logger = logging.getLogger(__name__)


class CleanSignals(QObject):
    """后台任务信号(QRunnable 不能直接发 Qt 信号,需要单独的 QObject)"""

    started = Signal()  # 任务开始
    progress = Signal(int, str)  # 0-100, 描述
    finished = Signal(float, int)  # (耗时秒, 清洗字符数)
    failed = Signal(str)  # 错误信息


class CleanWorker(QRunnable):
    """后台清洗任务

    执行流程:
        1. 读取所有原文 + 应用新规则 → 清洗
        2. 批量写回 SQLite clean_cache
        3. 持久化新规则 hash 到 corpus_meta
        4. 不修改 _cleanRule / 不 emit 信号(由 Coordinator 在成功后统一处理)
        5. 发送 finished(耗时, 字符数)

    设计要点:
        - Worker 期间 CorpusStore._cleanRule 仍指向「旧规则」
        - effectiveTexts() 仍命中「旧 hash」的 cache → UI 不阻塞
        - 完成后 Coordinator 原子地切换规则 + 持久化 + emit
    """

    def __init__(
        self,
        corpusStore,  # CorpusStore 实例
        rule,  # CleanRule
        enabled: bool,  # 是否启用清洗
        ruleHash: str,
        oldRuleHash: str,  # 用于旧 cache 清理
    ):
        super().__init__()
        self._store = corpusStore
        self._rule = rule
        self._enabled = enabled
        self._ruleHash = ruleHash
        self._oldRuleHash = oldRuleHash
        self.signals = CleanSignals()
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            start = time.time()
            self.signals.started.emit()
            self.signals.progress.emit(5, "读取语料...")

            if not self._enabled:
                # 关闭清洗:不需要重洗,直接完成(Coordinator 会清缓存 + 切换规则)
                self.signals.progress.emit(100, "已停用清洗")
                self.signals.finished.emit(time.time() - start, 0)
                return

            # 读取所有原文
            with self._store._lock:
                cur = self._store._conn.execute(
                    "SELECT file_name, raw_text FROM documents ORDER BY file_name"
                )
                rows = cur.fetchall()

            if not rows:
                self.signals.progress.emit(100, "语料为空")
                self.signals.finished.emit(time.time() - start, 0)
                return

            # 创建独立 cleaner(避免与主线程共享)
            from app.view.widgets.freq_analyzer.freq_engine import TextCleaner

            cleaner = TextCleaner(self._rule)

            total = len(rows)
            toCache: list = []
            totalChars = 0
            chunkEmitAt = max(1, total // 20)  # 每 5% 报一次进度

            for i, row in enumerate(rows):
                if self._cancel:
                    logger.warning("[CleanWorker] 任务被取消")
                    return
                fileName = row["file_name"]
                raw = row["raw_text"]
                cleaned = cleaner.clean(raw)
                toCache.append((fileName, self._ruleHash, cleaned))
                totalChars += len(cleaned)
                if (i + 1) % chunkEmitAt == 0 or (i + 1) == total:
                    pct = int(5 + (i + 1) / total * 90)
                    self.signals.progress.emit(pct, f"清洗中 ({i + 1}/{total})...")

            # 清理旧 hash 的 cache(避免脏数据堆积)
            if self._oldRuleHash and self._oldRuleHash != self._ruleHash:
                try:
                    with self._store._lock:
                        self._store._conn.execute(
                            "DELETE FROM clean_cache WHERE rule_hash = ?",
                            (self._oldRuleHash,),
                        )
                        self._store._conn.commit()
                except Exception as e:
                    logger.warning(f"[CleanWorker] 清理旧 cache 失败: {e}")

            # 批量写回新 cache
            self.signals.progress.emit(97, "写入缓存...")
            with self._store._lock:
                self._store._conn.executemany(
                    """
                    INSERT INTO clean_cache(file_name, rule_hash, cleaned_text)
                    VALUES(?, ?, ?)
                    ON CONFLICT(file_name, rule_hash) DO UPDATE SET
                        cleaned_text = excluded.cleaned_text,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    toCache,
                )
                self._store._conn.commit()

            elapsed = time.time() - start
            self.signals.progress.emit(100, f"清洗完成: {totalChars:,} 字符")
            logger.info(
                f"[CleanWorker] 完成: 文件={total} 字符={totalChars:,} 耗时={elapsed:.2f}s"
            )
            self.signals.finished.emit(elapsed, totalChars)
        except Exception as e:
            import traceback

            logger.exception(f"[CleanWorker] 失败: {e}")
            self.signals.failed.emit(f"{e}\n{traceback.format_exc()}")


class CleanCoordinator(QObject):
    """清洗协调器

    职责:
        1. 接收 UI 端的清洗规则变更(去抖)
        2. 调度后台 CleanWorker 执行持久化 + 重洗
        3. 通过信号通知 UI 进度
        4. 协调 CorpusStore 的 enable 状态切换

    Signals:
        cleanStarted()        清洗开始(整体,包括去抖之后)
        cleanProgress(int, str)
        cleanFinished(float, int)  整体完成
        cleanFailed(str)
        cleanBusyChanged(bool)     是否有任务在跑(供 UI 灰化按钮)
    """

    cleanStarted = Signal()
    cleanProgress = Signal(int, str)
    cleanFinished = Signal(float, int)
    cleanFailed = Signal(str)
    cleanBusyChanged = Signal(bool)

    # 去抖延迟(用户停止输入后等多久再推送)
    DEBOUNCE_MS = 300

    def __init__(self, corpusStore, parent=None):
        super().__init__(parent)
        self._store = corpusStore
        self._pool = QThreadPool.globalInstance()
        # 控制并发:清洗是高 CPU 操作,只允许 1 个并发任务
        self._pool.setMaxThreadCount(max(1, self._pool.maxThreadCount()))
        self._busy = False
        self._currentWorker: Optional[CleanWorker] = None
        self._currentHash: Optional[str] = None
        self._pendingRule: Optional[Tuple[Any, bool, str]] = None

        # 去抖 timer
        self._debounceTimer = QTimer(self)
        self._debounceTimer.setSingleShot(True)
        self._debounceTimer.timeout.connect(self._flushPending)

    # ---------------- 公开 API ----------------
    def scheduleClean(self, rule, enabled: bool):
        """UI 端调用:请求应用新规则(去抖)

        Args:
            rule:     新的 CleanRule
            enabled:  是否启用清洗
        """
        ruleHash = self._store._ruleHash(rule)
        # 如果 hash 与当前一致,跳过(防御性)
        if self._currentHash == ruleHash and enabled == self._store.cleanEnabled:
            logger.debug(f"[CleanCoordinator] 规则未变化,跳过 (hash={ruleHash[:8]})")
            return
        self._pendingRule = (rule, enabled, ruleHash)
        # 启动 / 重置去抖 timer
        self._debounceTimer.start(self.DEBOUNCE_MS)

    def cancelPending(self):
        """取消正在等待去抖的请求(用于程序关闭等场景)"""
        self._debounceTimer.stop()
        self._pendingRule = None

    def isBusy(self) -> bool:
        return self._busy

    # ---------------- 内部 ----------------
    def _flushPending(self):
        if self._pendingRule is None:
            return
        rule, enabled, ruleHash = self._pendingRule
        self._pendingRule = None

        # 已经在跑:等待完成后再处理(避免并发任务互相覆盖)
        if self._busy:
            logger.debug("[CleanCoordinator] 上一次任务尚未完成,排队等待")
            # 重新排队:再次 scheduleClean,但会再次进入 _flushPending
            # 这里用一次性 200ms 轮询直到不 busy
            QTimer.singleShot(200, self._flushPending)
            return

        oldHash = self._currentHash
        self._currentHash = ruleHash
        self._setBusy(True)
        self.cleanStarted.emit()

        worker = CleanWorker(
            corpusStore=self._store,
            rule=rule,
            enabled=enabled,
            ruleHash=ruleHash,
            oldRuleHash=oldHash or "",
        )
        self._currentWorker = worker
        worker.signals.progress.connect(self._onWorkerProgress)
        worker.signals.finished.connect(self._onWorkerFinished)
        worker.signals.failed.connect(self._onWorkerFailed)
        self._pool.start(worker)

    def _onWorkerProgress(self, pct: int, msg: str):
        self.cleanProgress.emit(pct, msg)

    def _onWorkerFinished(self, elapsed: float, totalChars: int):
        """Worker 完成后:原子地切换规则 + 持久化 + emit

        这一步必须在 UI 线程执行(因为 _cleanRule 是被多线程共享状态)。
        """
        self._setBusy(False)
        self._currentWorker = None

        # 取出最后一次 pending 的最终目标(防止 worker 期间多次 schedule 丢失)
        target = self._pendingRule
        self._pendingRule = None

        try:
            # 1) 通过 applyCleanRuleAsync 原子切换内存中的规则
            if target is not None:
                finalRule, finalEnabled, finalHash = target
            else:
                # 没新 pending:回退到上次记录
                finalRule = self._store._cleanRule
                finalEnabled = self._store._cleanEnabled
                finalHash = self._currentHash or self._store._ruleHash(finalRule)

            self._store.applyCleanRuleAsync(finalRule, finalEnabled, ruleHash=finalHash)

            # 2) 持久化新规则 hash 与 enabled 标志
            self._store._saveCleanRule(finalRule)
            self._store._saveCleanEnabled(finalEnabled)

            # 3) 更新内部状态
            self._currentHash = self._store._ruleHash(finalRule)

            # 4) 通知所有订阅者
            self._store.cleanRuleChanged.emit()

            logger.info(
                f"[CleanCoordinator] 规则已生效: hash={self._currentHash[:8]} "
                f"enabled={finalEnabled} 字符={totalChars:,} 耗时={elapsed:.2f}s"
            )
        except Exception as e:
            logger.exception(f"[CleanCoordinator] 应用新规则失败: {e}")
            self.cleanFailed.emit(str(e))

        self.cleanFinished.emit(elapsed, totalChars)

        # 处理期间又有新规则进来(worker 期间多次 schedule)
        if self._pendingRule is not None:
            self._flushPending()

    def _onWorkerFailed(self, err: str):
        self._setBusy(False)
        self._currentWorker = None
        self.cleanFailed.emit(err)
        logger.error(f"[CleanCoordinator] 清洗失败: {err}")

        # 即使失败,也尝试处理 pending(用户已经停了)
        if self._pendingRule is not None:
            self._flushPending()

    def _setBusy(self, busy: bool):
        if busy != self._busy:
            self._busy = busy
            self.cleanBusyChanged.emit(busy)
