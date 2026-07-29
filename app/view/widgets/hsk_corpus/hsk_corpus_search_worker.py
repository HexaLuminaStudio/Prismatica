# coding:utf-8
"""
HSK 语料后台检索 Worker(QThread 子类)
======================================

继承自 [CancellableWorker](file:///e:/Prismatica/app/view/widgets/freq_analyzer/worker_utils.py#L49-L118),
自带取消双保险(标志位 + QThread.requestInterruption)。

线程模型(关键修复):
    - rows 累积**只在子线程**(`self._rowsBuffer: List[Dict]`)
    - 子线程每读完一页只发 `dataReady()`(无参数),通知主线程去拉取
    - 主线程通过 QTimer(60ms)节流调用 `worker.snapshot()` 拿 rows 快照
    - 子线程持锁,主线程通过 RLock 短暂持有 → setAllRows → 解锁
    - 期间不向 UI 推送任何大数据对象,信号队列零负担

支持两种检索模式:
    1. 单条件(兼容旧接口): column + keyword / column + scoreRange
    2. 多条件组合(AND):     conditions(列表)

Signals(本类 + 继承):
    progress(int, str):          (pct, status)
    dataReady():                 子线程累积了一页(请主线程拉)
    finishedWithResult(object):  成功 → int(总命中数)
    failed(str):                 失败消息
    cancelledClean():            已取消
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

from PySide6.QtCore import Signal

from app.view.widgets.freq_analyzer.worker_utils import CancellableWorker


def _searchableColumns() -> List[str]:
    from app.core.services.hsk_corpus_service import HskCorpusService

    return HskCorpusService.instance().availableColumns()


class HskCorpusSearchWorker(CancellableWorker):
    """HSK 语料后台检索 Worker(流式 / 无上限 / 多条件)。"""

    # 子线程 → 主线程:有新数据可拉
    dataReady = Signal()

    def __init__(
        self,
        dbPath: str,
        column: str = "",
        keyword: str = "",
        pageSize: int = 1000,
        parent=None,
        scoreRange: Optional[Dict[str, Optional[int]]] = None,
        conditions: Optional[List[Dict]] = None,
    ) -> None:
        super().__init__(parent)
        self._dbPath = dbPath
        self._column = column or ""
        self._keyword = keyword or ""
        self._scoreRange: Optional[Dict[str, Optional[int]]] = scoreRange
        self._pageSize = max(1, min(int(pageSize), 5000))
        # 多条件模式:传 conditions(优先于 column/keyword/scoreRange)
        self._conditions: Optional[List[Dict]] = conditions

        # 数据缓冲:子线程累积,主线程拉
        self._rowsBuffer: List[Dict] = []
        self._bufferLock = threading.RLock()
        self._totalCount = 0
        self._finished = False  # 子线程标记检索结束

    # ------------------------------------------------------------------
    # 主入口(基类已包 try/except)
    # ------------------------------------------------------------------
    def runImpl(self) -> None:
        from app.core.services.hsk_corpus_service import HskCorpusService

        svc = HskCorpusService.instance()
        isMultiMode = self._conditions is not None
        isScoreMode = (
            (not isMultiMode) and self._column and svc.isScoreColumn(self._column)
        )

        # 参数校验
        if isMultiMode:
            # 多条件模式:必须至少有一个有效条件(由 UI 过滤)
            valid = [c for c in (self._conditions or []) if c]
            if not valid:
                self.reportProgress(100, "无有效条件,无需检索")
                self.finishedWithResult.emit(0)
                return
        elif isScoreMode:
            if not self._scoreRange:
                self.reportProgress(100, "区间为空,无需检索")
                self.finishedWithResult.emit(0)
                return
            lo = self._scoreRange.get("min")
            hi = self._scoreRange.get("max")
            if lo is None and hi is None:
                self.reportProgress(100, "区间为空,无需检索")
                self.finishedWithResult.emit(0)
                return
        else:
            if not self._keyword:
                self.reportProgress(100, "关键词为空,无需检索")
                self.finishedWithResult.emit(0)
                return
            allowed = _searchableColumns()
            if self._column not in allowed:
                self.failed.emit(f"非法列名 {self._column!r}")
                return

        self.reportProgress(5, "开始检索 ...")
        if self.isCancelled():
            return

        # 选择流式接口
        if isMultiMode:
            iterFn = lambda: svc.iterSearchByConditions(
                conditions=self._conditions or [],
                pageSize=self._pageSize,
                maxRows=None,
            )
        elif isScoreMode:
            iterFn = lambda: svc.iterSearchByScore(
                column=self._column,
                rangeDict=self._scoreRange or {},
                pageSize=self._pageSize,
                maxRows=None,
            )
        else:
            iterFn = lambda: svc.iterSearch(
                column=self._column,
                keyword=self._keyword,
                pageSize=self._pageSize,
                maxRows=None,
            )

        # 流式消费,rows 全程只在子线程累积
        try:
            for pageRows in iterFn():
                if self.isCancelled():
                    return
                # 写入缓冲(短锁)
                with self._bufferLock:
                    self._rowsBuffer.extend(pageRows)
                    self._totalCount += len(pageRows)
                # 通知主线程:有新数据可拉(零数据量传输)
                self.dataReady.emit()
                self.reportProgress(
                    min(95, 5 + self._totalCount // 50),
                    f"已读取 {self._totalCount:,} 行",
                )
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")
            return

        self._finished = True
        self.reportProgress(100, f"完成:命中 {self._totalCount:,} 条")
        self.finishedWithResult.emit(self._totalCount)

    # ------------------------------------------------------------------
    # 主线程通过 QTimer 调用此方法,拿当前累积快照(不复制 list)
    # ------------------------------------------------------------------
    def snapshot(self) -> tuple[List[Dict], int, bool]:
        """返回 (rows 列表引用, totalCount, finished 标记)。

        注:返回的 rows 引用由调用方在主线程立即消费,不持有。
        子线程下一次 snapshot 之前不会再写(此处只读)。
        实际我们 lock 保护避免竞态。
        """
        with self._bufferLock:
            # 返回 list 副本,避免主线程持锁期间子线程阻塞写入
            return list(self._rowsBuffer), self._totalCount, self._finished
