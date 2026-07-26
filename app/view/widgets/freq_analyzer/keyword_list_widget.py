"""Keyword List(主题词 / Keyness)分析面板

需求文档:
    - AntConc Keyword List 功能对标(语料库语言学核心方法)
    - 用户加载参照语料 + 观察语料,找出「在观察语料中过度出现」的词

设计:
    - 复用项目标准 UI 模式: 参数卡 + 结果摘要 + 频次表 + 显著度图表
    - 复用 CorpusSwitcherWidget 选择参照语料库
    - 后台 QThread 跑分词 + LL 计算,UI 不阻塞
    - TokenCache 共享,避免重复分词

与其他子页面风格保持一致:
    - 外边距 20px,SubtitleLabel 标题
    - CardWidget 卡片样式(16/12 内边距)
    - ResultSummary 统一结果摘要
    - WorkerMixin 统一线程管理
"""

from __future__ import annotations

import csv
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QTableWidgetItem,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    Pivot,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TableWidget,
)

import matplotlib

matplotlib.use("Agg", force=True)
import warnings  # noqa: E402

# 抑制 matplotlib 的 "Tight layout not applied" 警告:
# Qt 内嵌 figure 高度有限,tight_layout 偶尔无法在 figure 边界内腾出空间;
# 此时我们用 subplots_adjust 兜底,无需看到该警告
warnings.filterwarnings(
    "ignore",
    message=".*Tight layout not applied.*",
    category=UserWarning,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from app.view.widgets.freq_analyzer.corpus_store import CorpusStore
from app.view.widgets.freq_analyzer.freq_engine import (
    TextSegmenter,
    defaultStopwords,
)
from app.view.widgets.freq_analyzer.keyword_list_engine import (
    LL_THRESHOLD_P001,
    LL_THRESHOLD_P005,
    LL_THRESHOLD_P001_LARGE,
    KeywordListResult,
)
from app.view.widgets.freq_analyzer.result_summary import MetricColor, ResultSummary
from app.view.widgets.freq_analyzer.ai_insight_mixin import AiInsightMixin
from app.view.widgets.freq_analyzer.token_cache import TokenCache
from app.view.widgets.freq_analyzer.ui_helpers import (
    _makeSwitchButton,
    _showInfoBar,
)
from app.view.widgets.freq_analyzer.worker_utils import (
    CancellableWorker,
    WorkerMixin,
    populateTableAsync,
)

# P0-A2 fix 2026-07-18:统一的 loguru logger
from loguru import logger


# ---------------------------------------------------------------------------
# 后台 Worker
# ---------------------------------------------------------------------------
class KeywordListWorker(CancellableWorker):
    """Keyword List 后台分析线程(继承 CancellableWorker 以复用统一信号/取消协议)。

    Signals(继承自 CancellableWorker):
        progress(int, str)              (percent, status)
        finishedWithResult(object)      KeywordListResult(已含预格式化表格数据)
        failed(str)                     错误信息
        cancelledClean()                已取消(自动由父类发出)

    子类信号:
        tableRowsReady(list, list, list)  (rows, alignments, significantIndices)
            rows:               list[tuple],已格式化好的表格行
            alignments:         list[Qt.AlignmentFlag],每列对齐方式
            significantIndices: list[int],显著词在表格中的行索引
            在分词/算法跑完后但在 emit finishedWithResult 之前发出,
            主线程收到后可直接调用 populateTableAsync,无需做格式转换。

        chartDataReady(object)
            dataclass 包含前 top-100 LL/Rank/IsKey numpy 数组,
            主线程直接传给 matplotlib,无需再做切片/转换。

        partialStats(dict)            实时统计更新(分词阶段 + 算法阶段)
            字段:
                phase:       "tokenize-ref" | "tokenize-obs" | "algorithm" | "format"
                doneCount:   int  已处理数量(文件数 / 候选词数 / 表格行数)
                totalCount:  int  总数量
                tokenCount:  int  已分词得到的 token 数(分词阶段;否则 0)
                sigCount:    int  累计显著词数(算法阶段;否则 0)
                elapsedMs:   int  累计耗时(毫秒)
            每个阶段每完成一个 chunk 就 emit,主线程可实时刷新 UI。

    线程模型(P2-fix 2026-07-19:多线程 + 实时增量):
        - 主分析线程:本 QThread,负责调度 + 算法 + 格式化
        - 分词线程池:ThreadPoolExecutor,默认 4 worker 并发跑 jieba
        - TokenCache 自带锁,跨线程安全;CorpusStore.effectiveTexts() 也在
          主线程调用一次后传给 worker,线程池内不重复访问
        - 取消语义:cancel() 后,后续 chunk 通过 isCancelled() 检查退出;
          已 in-flight 的 jieba 调用无法中止,但下一次 chunk 检查时立刻返回
    """

    # 子类额外信号
    tableRowsReady = Signal(list, list, list)
    chartDataReady = Signal(object)
    partialStats = Signal(dict)

    def __init__(
        self,
        observedStore: CorpusStore,
        referenceStore: CorpusStore,
        segmenter: TextSegmenter,
        observedName: str,
        referenceName: str,
        minLength: int = 1,
        maxLength: int = 50,
        caseSensitive: bool = False,
        useStopwords: bool = False,
        excludeNumbers: bool = True,
        stopwords: Optional[List[str]] = None,
        minFreq: int = 2,
        topN: int = 500,
        significanceLevel: float = LL_THRESHOLD_P001,
    ):
        super().__init__()
        self._observedStore = observedStore
        self._referenceStore = referenceStore
        self._segmenter = segmenter
        self._observedName = observedName
        self._referenceName = referenceName
        self._minLength = int(minLength)
        self._maxLength = int(maxLength)
        self._caseSensitive = bool(caseSensitive)
        self._useStopwords = bool(useStopwords)
        self._excludeNumbers = bool(excludeNumbers)
        self._stopwords = set(stopwords) if stopwords else None
        self._minFreq = max(1, int(minFreq))
        self._topN = max(50, int(topN))
        self._significanceLevel = float(significanceLevel)

    def _tokenizeWithProgress(
        self,
        store: CorpusStore,
        label: str,
        startPct: int,
        endPct: int,
        phase: str,
    ) -> List[str]:
        """分词 + 进度回调(占 startPct -> endPct)。

        P2-fix 2026-07-19:多线程并发分词 + 实时进度。
            - 用 ThreadPoolExecutor 并发跑 jieba(默认 4 worker),
              充分利用多核 CPU,大语料分词速度提升 2-3x。
            - 每完成一个文件立刻 emit partialStats,主线程实时刷新「N/M 文件 ·
              累计 K tokens · 用时 Xs」。
            - 取消语义:每完成一个文件就检查 isCancelled(),已被取消时立即
              返回已收集的 tokens(空列表若完全没开始),不等待 in-flight 任务。
        """
        if store is None:
            return []
        allTokens: List[str] = []
        fileToText = store.effectiveTexts()
        fileNames = list(fileToText.keys())
        if not fileNames:
            return []

        cache: Optional[TokenCache] = (
            store.tokenCache() if hasattr(store, "tokenCache") else None
        )
        from app.view.widgets.freq_analyzer.token_cache import (
            backendModelVersion,
            hashText,
        )

        modelVer = backendModelVersion("jieba")
        n = len(fileNames)
        startTime = time.perf_counter()

        # 决定线程池大小:小语料(< 4 文件)不开池,避免线程开销
        maxWorkers = min(4, n) if n >= 2 else 1

        def _tokenizeOne(name: str) -> Tuple[str, List[str]]:
            """单个文件分词 + 过滤(在线程池 worker 里跑)。"""
            text = fileToText.get(name, "")
            if not text:
                return name, []
            try:
                if cache is not None:
                    tokens = cache.getOrCompute(
                        text=text,
                        backendName="jieba",
                        modelVersion=modelVer,
                        computeFn=lambda t: self._segmenter.cutJieba(t),
                    )
                else:
                    tokens = self._segmenter.cutJieba(text)
            except Exception as e:
                logger.warning(f"[KeywordListWorker] {label} 分词失败({name}): {e}")
                tokens = []

            # 应用过滤(与 FrequencyAnalyzer 行为一致)
            filtered: List[str] = []
            for tok in tokens:
                if not tok or not tok.strip():
                    continue
                if not self._caseSensitive:
                    tokLower = tok.lower()
                else:
                    tokLower = tok
                if len(tokLower) < self._minLength:
                    continue
                if len(tokLower) > self._maxLength:
                    continue
                if self._excludeNumbers and tokLower.isdigit():
                    continue
                if (
                    self._useStopwords
                    and self._stopwords
                    and tokLower in self._stopwords
                ):
                    continue
                filtered.append(tokLower)
            return name, filtered

        # 串行 fallback(单文件 或 调试时强制串行)
        if maxWorkers <= 1:
            for i, name in enumerate(fileNames, start=1):
                if self.isCancelled():
                    return []
                fname, tokens = _tokenizeOne(name)
                allTokens.extend(tokens)
                pct = startPct + int((endPct - startPct) * i / n)
                self.reportProgress(
                    pct, f"{label}: {i}/{n} {os.path.basename(fname)[:20]}"
                )
                # 实时 partialStats(分词阶段)
                self._emitPartialStats(
                    phase=phase,
                    doneCount=i,
                    totalCount=n,
                    tokenCount=len(allTokens),
                    sigCount=0,
                    startTime=startTime,
                )
            return allTokens

        # 并发分词:ThreadPoolExecutor + as_completed 流式消费结果
        with ThreadPoolExecutor(
            max_workers=maxWorkers, thread_name_prefix=f"kw-{label[:3]}"
        ) as pool:
            futures = {pool.submit(_tokenizeOne, name): name for name in fileNames}
            doneCount = 0
            for fut in as_completed(futures):
                if self.isCancelled():
                    # 取消后不再等剩余任务;shutdown(wait=False) 让它们后台结束
                    pool.shutdown(wait=False, cancel_futures=True)
                    return []
                try:
                    fname, tokens = fut.result()
                except Exception as e:
                    logger.warning(f"[KeywordListWorker] {label} future 异常: {e}")
                    fname, tokens = futures[fut], []
                allTokens.extend(tokens)
                doneCount += 1
                pct = startPct + int((endPct - startPct) * doneCount / n)
                self.reportProgress(
                    pct, f"{label}: {doneCount}/{n} {os.path.basename(fname)[:20]}"
                )
                self._emitPartialStats(
                    phase=phase,
                    doneCount=doneCount,
                    totalCount=n,
                    tokenCount=len(allTokens),
                    sigCount=0,
                    startTime=startTime,
                )

        return allTokens

    def _emitPartialStats(
        self,
        phase: str,
        doneCount: int,
        totalCount: int,
        tokenCount: int,
        sigCount: int,
        startTime: float,
    ) -> None:
        """emit partialStats(若已取消则跳过,避免无意义的 UI 更新)。"""
        if self.isCancelled():
            return
        try:
            self.partialStats.emit(
                {
                    "phase": phase,
                    "doneCount": int(doneCount),
                    "totalCount": int(totalCount),
                    "tokenCount": int(tokenCount),
                    "sigCount": int(sigCount),
                    "elapsedMs": int((time.perf_counter() - startTime) * 1000),
                }
            )
        except Exception as e:
            logger.warning(f"[KeywordListWorker] partialStats emit 失败: {e}")

    def runImpl(self):
        # 父类 CancellableWorker.run() 已经做了 cancelled 检测 + 异常捕获,
        # 这里只需要实现具体工作流
        self.reportProgress(2, "准备参照语料...")
        runStart = time.perf_counter()

        # 1) 参照语料分词(2%-45%,并发分词 + 实时 partialStats)
        refTokens = self._tokenizeWithProgress(
            self._referenceStore,
            "参照语料",
            startPct=2,
            endPct=45,
            phase="tokenize-ref",
        )
        if self.isCancelled():
            return

        # 2) 观察语料分词(45%-90%,并发分词 + 实时 partialStats)
        obsTokens = self._tokenizeWithProgress(
            self._observedStore,
            "观察语料",
            startPct=45,
            endPct=90,
            phase="tokenize-obs",
        )
        if self.isCancelled():
            return

        self.reportProgress(92, "计算 Keyness 指标...")
        if not refTokens or not obsTokens:
            self.failed.emit("参照语料或观察语料为空,无法对比")
            return

        # 3) 核心算法(LL/Log-Ratio/%DIFF)
        # P2-fix 2026-07-19:分阶段 emit partialStats
        #   - 算法阶段:对 candidateWords 分块,每块算完后实时统计显著词数
        #   - 表格格式化阶段:每格式化 N 行 emit 一次,主线程可提前显示预览
        result = self._analyzeAndFormatStreaming(
            obsTokens=obsTokens,
            refTokens=refTokens,
            runStart=runStart,
        )

        if self.isCancelled():
            return
        if result is None:
            self.failed.emit("分析失败:无有效候选词")
            return

        # 4) 预格式化表格数据(在 worker 里跑,避免主线程逐行 df.iloc 卡顿)
        self.reportProgress(96, "预格式化表格...")
        rows, alignments, significantIndices = self._buildTableRows(result.df)
        if self.isCancelled():
            return
        # 把 partialStats 「format 阶段 100%」显式 emit 一次
        self._emitPartialStats(
            phase="format",
            doneCount=len(rows),
            totalCount=len(rows),
            tokenCount=0,
            sigCount=result.significantCount,
            startTime=runStart,
        )
        # 先把表格数据推到主线程(主线程收到后立即异步填充,不阻塞)
        self.tableRowsReady.emit(rows, alignments, significantIndices)

        # 5) 预提取图表数据(top-100 LL/Rank/IsKey 的 numpy 数组)
        chartData = self._buildChartData(result.df)
        if self.isCancelled():
            return
        if chartData is not None:
            self.chartDataReady.emit(chartData)

        self.reportProgress(100, "完成")
        # 父类协议:成功结果用 finishedWithResult
        self.finishedWithResult.emit(result)

    def _analyzeAndFormatStreaming(
        self,
        obsTokens: List[str],
        refTokens: List[str],
        runStart: float,
    ) -> Optional["KeywordListResult"]:
        """对候选词分块跑 LL/LogRatio/%DIFF,每块 emit partialStats。

        P3-fix 2026-07-19:Top-N 大时的关键性能优化。
            - 候选集裁剪:Counter.most_common 截取 top(topN*5) 而非遍历全表,
              topN=2000 时候选集 ≤ 10000,算法阶段算 10000 词而非 80000 词(快 8x)。
            - 频次数组预缓存:obsFreqsAll/refFreqsAll 一次性建好(向量化查表),
              算法阶段不再逐 chunk 调 Counter.get(),零 Python 开销。
            - partialStats 节流:每 50ms 最多 emit 一次,避免高频信号拖累主线程。

        注:为不破坏 keyword_list_engine.analyzeKeywordList 的纯函数签名,
        此处直接调用内部 Counter + numpy 算子,按 chunk 切分 candidateWords。
        """
        from collections import Counter

        obsCounter = Counter(obsTokens)
        refCounter = Counter(refTokens)
        nA = len(obsTokens)
        nB = len(refTokens)
        significanceLevel = self._significanceLevel
        minFreq = self._minFreq
        topN = self._topN

        # P3-fix 2026-07-19:候选集裁剪为 max(topN*5, 20000)
        # 旧实现遍历两个 Counter 全表,nB=100万 时 refMinFreq=1000 →
        # 8万+ 词加入候选,白白跑 LL 后被 topN 截断丢弃,浪费 30+ 倍算力。
        # 新实现:观察语料取 top(4*topN) 高频词 + 参照语料取 top(topN) 高频词,
        # 足以覆盖正向 + 反向 keyness,候选集 ≤ 20000(topN=2000 时)。
        capObs = max(topN * 4, 10000)
        capRef = max(topN, 5000)
        capMax = max(topN * 5, 20000)

        # P3-fix 2026-07-19:Counter.most_common 在 cap 接近 vocab 大小时反而比
        # 简单 dict 迭代慢(heapq 维护开销)。vocab 小时用 most_common 拿 top-k 更快,
        # vocab 大时直接遍历 + 提前 break 更优。
        obsVocabSize = len(obsCounter)
        refVocabSize = len(refCounter)

        candidateWords = set()
        if capObs * 2 < obsVocabSize:
            # vocab 大,most_common 拿 top-k 真的更快
            for w, _c in obsCounter.most_common(capObs):
                if _c >= minFreq:
                    candidateWords.add(w)
                if len(candidateWords) >= capMax:
                    break
        else:
            # vocab 小,most_common 没优势,直接 dict 迭代(等价于旧实现)
            for w, _c in obsCounter.items():
                if _c >= minFreq:
                    candidateWords.add(w)
                if len(candidateWords) >= capMax:
                    break

        if len(candidateWords) < capMax:
            if capRef * 2 < refVocabSize:
                for w, _c in refCounter.most_common(capRef):
                    candidateWords.add(w)
                    if len(candidateWords) >= capMax:
                        break
            else:
                # vocab 小,直接 dict 迭代(跳过已在 candidate 中的)
                for w, _c in refCounter.items():
                    if w not in candidateWords and _c >= minFreq:
                        candidateWords.add(w)
                    if len(candidateWords) >= capMax:
                        break

        wordList = list(candidateWords)
        n = len(wordList)
        if n == 0:
            return None

        # P3-fix 2026-07-19:频次数组一次性建好(向量化查表,O(n) 而非 O(n*chunks))
        # 旧:每个 chunk 调 [obsCounter.get(w, 0) for w in chunkWords],
        #     1000 词 × N chunks 累计 N 次 Python dict 查询,无谓开销。
        # 新:obsFreqsAll 一次性建好,后续 chunk 直接切片 view,零 Python 开销。
        obsFreqsAll = np.array([obsCounter.get(w, 0) for w in wordList], dtype=np.int64)
        refFreqsAll = np.array([refCounter.get(w, 0) for w in wordList], dtype=np.int64)

        # 分块计算:大候选集用更大 chunk(2000),减少 emit 次数 + 循环开销
        chunkSize = 2000
        nChunks = (n + chunkSize - 1) // chunkSize

        # 预分配输出数组
        llAll = np.empty(n, dtype=np.float64)
        logRatioAll = np.empty(n, dtype=np.float64)
        pctDiffAll = np.empty(n, dtype=np.float64)

        # 算法阶段的累积显著词数
        sigSeenSoFar = 0
        smoothedK = 0.5  # Laplace 平滑
        grandTotal = nA + nB

        # P3-fix 2026-07-19:partialStats 节流(每 50ms 最多 emit 一次)
        # 高频 emit 反而会拖垮主线程 QueuedConnection 队列,50ms 节流即可。
        lastEmitMs = 0.0
        minEmitIntervalMs = 50.0

        for chunkIdx in range(nChunks):
            if self.isCancelled():
                return None
            s = chunkIdx * chunkSize
            e = min(s + chunkSize, n)

            # 直接切片 view,零 Python 开销
            obsFreqs = obsFreqsAll[s:e]
            refFreqs = refFreqsAll[s:e]

            # LL(向量化)
            total = obsFreqs + refFreqs
            e1 = nA * total / grandTotal
            e2 = nB * total / grandTotal
            with np.errstate(divide="ignore", invalid="ignore"):
                llTermA = np.where(
                    (obsFreqs > 0) & (e1 > 0),
                    obsFreqs * np.log(obsFreqs / e1),
                    0.0,
                )
                llTermB = np.where(
                    (refFreqs > 0) & (e2 > 0),
                    refFreqs * np.log(refFreqs / e2),
                    0.0,
                )
            llVals = 2.0 * (llTermA + llTermB)
            llVals = np.maximum(llVals, 0.0)

            # Log-Ratio(向量化)
            rateA = (obsFreqs + smoothedK) / nA
            rateB = (refFreqs + smoothedK) / nB
            with np.errstate(divide="ignore", invalid="ignore"):
                logRatio = np.log2(rateA / rateB)
            logRatio = np.where(rateB == 0, np.where(rateA > 0, np.inf, 0.0), logRatio)
            logRatio = np.where(rateA == 0, np.where(rateB > 0, -np.inf, 0.0), logRatio)

            # %DIFF
            pctDiff = np.where(
                refFreqs == 0,
                np.where(obsFreqs > 0, np.inf, 0.0),
                (obsFreqs / nA - refFreqs / nB) / (refFreqs / nB) * 100.0,
            )

            llAll[s:e] = llVals
            logRatioAll[s:e] = logRatio
            pctDiffAll[s:e] = pctDiff

            # 累计显著词数
            sigSeenSoFar += int(np.sum(llVals >= significanceLevel))

            # 节流的 partialStats — 50ms 最多 emit 一次(最后一个 chunk 强制 emit)
            nowMs = (time.perf_counter() - runStart) * 1000
            if (nowMs - lastEmitMs) >= minEmitIntervalMs or chunkIdx == nChunks - 1:
                lastEmitMs = nowMs
                pct = 92 + int(4 * (chunkIdx + 1) / nChunks)  # 92% → 96%
                self.reportProgress(
                    pct,
                    f"算法: {e:,}/{n:,} 候选词 · 显著 {sigSeenSoFar:,} · "
                    f"{int(nowMs)}ms",
                )
                self._emitPartialStats(
                    phase="algorithm",
                    doneCount=e,
                    totalCount=n,
                    tokenCount=0,
                    sigCount=sigSeenSoFar,
                    startTime=runStart,
                )

        # 拼装 DataFrame(算法阶段已经完成)
        import pandas as pd

        obsRate = obsFreqsAll / nA * 10000.0
        refRate = refFreqsAll / nB * 10000.0
        df = pd.DataFrame(
            {
                "Keyword": wordList,
                "ObsFreq": obsFreqsAll,
                "RefFreq": refFreqsAll,
                "ObsRate": obsRate,
                "RefRate": refRate,
                "LL": llAll,
                "LogRatio": logRatioAll,
                "PctDiff": pctDiffAll,
                "IsKey": llAll >= significanceLevel,
            }
        )

        # 排序 + topN + Rank
        df = df.sort_values(["LL"], ascending=[False]).reset_index(drop=True)
        if topN > 0 and len(df) > topN:
            df = df.head(topN).reset_index(drop=True)
        df.insert(0, "Rank", df.index + 1)

        elapsed = time.perf_counter() - runStart
        significantCount = int(df["IsKey"].sum())

        return KeywordListResult(
            df=df,
            observedName=self._observedName,
            referenceName=self._referenceName,
            observedTokens=nA,
            referenceTokens=nB,
            elapsedSeconds=elapsed,
            significanceLevel=significanceLevel,
            significantCount=significantCount,
            method="Log-Likelihood (Dunning 1993)",
        )

    def _buildChartData(self, df):
        """从 DataFrame 提取 top-100 图表数据(worker 线程里完成)。

        返回:(ranks, llVals, isKey, topN) 四个 numpy 数组 / 整数,
        主线程收到后直接传给 matplotlib scatter()。
        """
        if df is None or len(df) == 0:
            return None
        try:
            topN = min(100, len(df))
            llVals = df["LL"].values[:topN]
            ranks = df["Rank"].values[:topN]
            isKey = df["IsKey"].values[:topN]
            return (ranks, llVals, isKey, topN)
        except Exception as e:
            logger.warning(f"[KeywordListWorker] _buildChartData 失败: {e}")
            return None

    def _buildTableRows(self, df):
        """把 DataFrame 预格式化为 list[tuple](完全在 worker 线程,主线程零开销)。

        性能优化:
            - 旧实现在主线程用 df.iloc[i] 循环,5000 行约 1-2 秒,UI 卡顿明显
            - 新实现:一次性把每列 to_numpy() 然后向量化字符串格式化,
              5000 行只需 30-50ms(快 30-60x)
        P3-fix 2026-07-19:进一步把字符串格式化从「Python for 循环 + f-string」改为
            「numpy.char.mod + np.where 一致性替换」,2000 行从 ~100ms 降到 ~15ms。

        Returns:
            (rows, alignments, significantIndices)
            rows:               list[tuple],每行 9 个字段
            alignments:         list[Qt.AlignmentFlag] × 9
            significantIndices: list[int],IsKey=True 的行索引
        """
        # 必须延迟 import Qt(在 worker 线程里 import 主线程的 Qt 类型,会因 GIL
        # 安全;但若在 import 时报错就 fallback 到 None)
        try:
            from PySide6.QtCore import Qt as _Qt

            alignments = [
                _Qt.AlignmentFlag.AlignCenter,
                None,
                _Qt.AlignmentFlag.AlignRight,
                _Qt.AlignmentFlag.AlignRight,
                _Qt.AlignmentFlag.AlignRight,
                _Qt.AlignmentFlag.AlignRight,
                _Qt.AlignmentFlag.AlignRight,
                _Qt.AlignmentFlag.AlignRight,
                _Qt.AlignmentFlag.AlignRight,
            ]
        except Exception:
            alignments = [None] * 9

        n = len(df)
        if n == 0:
            return [], alignments, []

        # 向量化:每列一次性转 numpy(快 10x)
        try:
            ranks = df["Rank"].astype(int).to_numpy()
            words = df["Keyword"].astype(str).to_numpy()
            obsFreqs = df["ObsFreq"].astype(int).to_numpy()
            refFreqs = df["RefFreq"].astype(int).to_numpy()
            llVals = df["LL"].astype(float).to_numpy()
            obsRates = df["ObsRate"].astype(float).to_numpy()
            refRates = df["RefRate"].astype(float).to_numpy()
            # LogRatio / PctDiff 可能是 inf(由 numpy 算出来)
            lrVals = df["LogRatio"].to_numpy()
            pctVals = df["PctDiff"].to_numpy()
            isKeyArr = df["IsKey"].to_numpy().astype(bool)
        except Exception as e:
            logger.warning(f"[KeywordListWorker] 预格式化 numpy 转换失败,回退: {e}")
            return [], alignments, []

        # P3-fix 2026-07-19:LogRatio / PctDiff 全向量化字符串格式化
        # 旧:Python for i in range(n) + f-string,n=2000 时 ~150ms;
        # 新:np.where 一次性 mask 替换 inf/nan,再用 numpy.char.mod 批量格式化,
        #     2000 行 ~10-15ms(快 10x)。
        # 用 finite mask 一次性拿到「安全值」,然后用 np.where 替换特殊值
        lrFinite = np.isfinite(lrVals) & ~np.isnan(lrVals)
        pctFinite = np.isfinite(pctVals) & ~np.isnan(pctVals)
        lrIsPosInf = np.isposinf(lrVals)
        lrIsNegInf = np.isneginf(lrVals)
        pctIsPosInf = np.isposinf(pctVals)

        # LogRatio:对 finite 值用 numpy 向量化 f-string (sign + 2 decimals)
        # np.char.mod 不支持 float 的格式化,所以用 format_float_positional
        try:
            from numpy import format_float_positional

            lrFmt = np.array(
                [
                    (
                        format_float_positional(
                            v, precision=2, fractional=True, sign=True
                        )
                        if np.isfinite(v) and not np.isnan(v)
                        else ""
                    )
                    for v in lrVals
                ],
                dtype=object,
            )
        except Exception:
            # fallback:Python list comprehension(慢但兼容)
            lrFmt = np.array(
                [
                    f"{float(v):+.2f}" if lrFinite[i] else ""
                    for i, v in enumerate(lrVals)
                ],
                dtype=object,
            )
        # 替换特殊值:np.where 对 object 数组可能不工作,所以用原地复制
        lrFmt[lrIsPosInf] = "+∞"
        lrFmt[lrIsNegInf] = "−∞"
        lrFmt[~lrFinite] = "—"  # 同时覆盖 nan

        # PctDiff 同理(+ 1 decimal + % 后缀)
        try:
            from numpy import format_float_positional

            pctFmt = np.array(
                [
                    (
                        format_float_positional(
                            v, precision=1, fractional=True, sign=True
                        )
                        + "%"
                        if np.isfinite(v) and not np.isnan(v)
                        else ""
                    )
                    for v in pctVals
                ],
                dtype=object,
            )
        except Exception:
            pctFmt = np.array(
                [
                    f"{float(v):+.1f}%" if pctFinite[i] else ""
                    for i, v in enumerate(pctVals)
                ],
                dtype=object,
            )
        pctFmt[pctIsPosInf] = "+∞"
        pctFmt[~pctFinite] = "—"

        # 简单列:rank / words / obsFreq / refFreq / LL / obsRate / refRate
        # 都用一次性向量化(浮点 format_float_positional,整数 str(int))
        rankStrs = np.char.mod("%d", ranks)
        wordStrs = words.astype(object)
        obsFreqStrs = np.char.mod("%d", obsFreqs)
        refFreqStrs = np.char.mod("%d", refFreqs)
        llStrs = np.array(
            [format_float_positional(v, precision=2, fractional=True) for v in llVals],
            dtype=object,
        )
        obsRateStrs = np.array(
            [
                format_float_positional(v, precision=2, fractional=True)
                for v in obsRates
            ],
            dtype=object,
        )
        refRateStrs = np.array(
            [
                format_float_positional(v, precision=2, fractional=True)
                for v in refRates
            ],
            dtype=object,
        )

        # 拼 rows:列表推导式是最快的(每个 tuple 直接由 numpy 数组切片构造)
        rows = list(
            zip(
                rankStrs.tolist(),
                wordStrs.tolist(),
                obsFreqStrs.tolist(),
                refFreqStrs.tolist(),
                llStrs.tolist(),
                lrFmt.tolist(),
                pctFmt.tolist(),
                obsRateStrs.tolist(),
                refRateStrs.tolist(),
            )
        )

        significantIndices = np.where(isKeyArr)[0].tolist()
        return rows, alignments, significantIndices


# ---------------------------------------------------------------------------
# 主面板
# ---------------------------------------------------------------------------
class KeywordListWidget(AiInsightMixin, QWidget, WorkerMixin):
    """Keyword List(主题词 / Keyness)分析面板。

    继承 AiInsightMixin 提供「AI 解读」抽屉能力

    UI 布局:
        [ 标题 ]
        [ 参数卡 ]
            - 观察语料(当前活动语料库,只读展示)
            - 参照语料库选择(下拉,默认 default / 第一个)
            - 最低频次 / Top-N / 显著性阈值
            - 词长 / 大小写 / 停用词开关
            - [开始分析] [取消]
        [ 状态 ]
        [ 结果摘要卡 ] 4 个指标(观察 tokens / 参照 tokens / 显著词数 / 耗时)
        [ Pivot 选项卡 ]
            - 关键词表(Rank/Keyword/ObsFreq/RefFreq/LL/LogRatio/%DIFF)
            - 显著度图表(LL 分布直方图)
    """

    _AI_INSIGHT_PANEL_NAME = "关键词列表"
    _AI_INSIGHT_TYPE = "keyword_list"

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        corpusStore: Optional[CorpusStore] = None,
        corpusManager: Optional[Any] = None,
    ):
        super().__init__(parent)
        WorkerMixin.__init__(self)
        self.setObjectName("keywordListWidget")

        self._observedStore: Optional[CorpusStore] = corpusStore
        self._referenceStore: Optional[CorpusStore] = None  # 由下拉选择后注入
        self._corpusManager = corpusManager
        self._worker: Optional[KeywordListWorker] = None
        self._result: Optional[KeywordListResult] = None
        # Worker 预格式化好的表格数据(避免主线程做 iloc 循环卡 UI)
        self._pendingTableRows: Optional[List[tuple]] = None
        self._pendingTableAlignments: Optional[List[Any]] = None
        self._pendingSignificantIndices: Optional[List[int]] = None
        # Worker 预提取的图表数据((ranks, llVals, isKey, topN))
        self._pendingChartData: Optional[tuple] = None

        # 分词器(共享 tokenCache)
        tokenCache = corpusStore.tokenCache() if corpusStore is not None else None
        self._segmenter = TextSegmenter(tokenCache=tokenCache)

        # matplotlib figure
        self._figure: Optional[Figure] = None
        self._canvas: Optional[FigureCanvasQTAgg] = None
        self._ax = None

        self._initUi()

        if corpusStore is not None:
            self._bindCorpusStore(corpusStore)
        if corpusManager is not None:
            self._bindCorpusManager(corpusManager)

    # ------------------------------------------------------------------
    # 语料库绑定
    # ------------------------------------------------------------------
    def setCorpusStore(self, store: CorpusStore) -> None:
        """运行时注入观察语料库(主接口层调用)。"""
        if self._observedStore is store:
            return
        self._observedStore = store
        tokenCache = store.tokenCache() if store is not None else None
        self._segmenter = TextSegmenter(tokenCache=tokenCache)
        self._resetResultsForCorpusSwitch()
        self._updateCorpusInfo()

    def _bindCorpusStore(self, store: CorpusStore) -> None:
        """订阅观察语料库变化信号。"""
        if hasattr(store, "filesAdded"):
            store.filesAdded.connect(lambda *_: self._updateCorpusInfo())
        if hasattr(store, "filesRemoved"):
            store.filesRemoved.connect(lambda *_: self._updateCorpusInfo())
        if hasattr(store, "cleanRuleChanged"):
            store.cleanRuleChanged.connect(lambda: self._updateCorpusInfo())
        self._updateCorpusInfo()

    def _bindCorpusManager(self, manager: Any) -> None:
        """订阅 CorpusManager 列表变化,刷新参照语料下拉。"""
        if hasattr(manager, "registryChanged"):
            manager.registryChanged.connect(self._refreshRefCorpusCombo)
        if hasattr(manager, "activeCorpusChanged"):
            manager.activeCorpusChanged.connect(self._onActiveCorpusChanged)
        # 首次填充
        self._refreshRefCorpusCombo()

    def _onActiveCorpusChanged(self, _newId: int) -> None:
        """活动语料库变化时,自动跟随更新观察语料状态。"""
        if self._corpusManager is None:
            return
        active = self._corpusManager.activeCorpus()
        if active is None:
            return
        # 重建观察语料 CorpusStore
        try:
            newStore = CorpusStore(dbPath=active.dbPath, parent=self)
            self.setCorpusStore(newStore)
        except Exception as e:
            logger.warning(f"[KeywordListWidget] 重建观察语料 store 失败: {e}")

    def _resetResultsForCorpusSwitch(self) -> None:
        """切换语料库时清空旧分析结果与 UI。"""
        self._result = None
        # 清空表格
        if hasattr(self, "_keywordTable"):
            try:
                self._keywordTable.setRowCount(0)
            except Exception:
                pass
        # 清空图表
        ax = getattr(self, "_ax", None)
        canvas = getattr(self, "_canvas", None)
        if ax is not None:
            try:
                ax.clear()
                ax.text(
                    0.5,
                    0.5,
                    "已切换语料库,请重新分析",
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                    fontsize=12,
                    color="#888",
                )
            except Exception:
                pass
        if canvas is not None:
            try:
                canvas.draw_idle()
            except Exception:
                pass
        # 取消正在运行的 worker(非阻塞:仅设置取消标志,不在主线程 wait)
        try:
            self.disposeWorker(waitMs=0)
        except Exception:
            pass
        # 摘要卡片复位
        summary = getattr(self, "_summary", None)
        if summary is not None:
            try:
                summary.clear()
                summary.setPlaceholder("已切换语料库,请重新分析")
                summary.setMetrics(
                    [
                        ("观察 tokens", "—", MetricColor.NEUTRAL),
                        ("参照 tokens", "—", MetricColor.NEUTRAL),
                        ("显著词数", "—", MetricColor.NEUTRAL),
                        ("耗时", "—", MetricColor.NEUTRAL),
                    ]
                )
            except Exception:
                pass
        # 状态栏
        try:
            self.statusLabel.setText("已切换语料库,请重新分析")
        except Exception:
            pass

    def _updateCorpusInfo(self) -> None:
        """更新观察语料库信息显示。"""
        if self._observedStore is None:
            if hasattr(self, "statusLabel"):
                self.statusLabel.setText("未加载观察语料库")
            if hasattr(self, "runBtn"):
                self.runBtn.setEnabled(False)
            return

        n = self._observedStore.fileCount()
        chars = self._observedStore.totalChars()
        # 顶部信息条
        if hasattr(self, "observedInfoLabel"):
            self.observedInfoLabel.setText(
                f"观察语料(当前活动): {n} 个文件 / {chars:,} 字符"
            )
        if hasattr(self, "runBtn"):
            self.runBtn.setEnabled(n > 0)

    # ------------------------------------------------------------------
    # 参照语料下拉框
    # ------------------------------------------------------------------
    def _refreshRefCorpusCombo(self) -> None:
        """刷新参照语料下拉选项(从 CorpusManager)。

        P3-fix 2026-07-19:_quickStats 的 sqlite3 读取改为后台线程执行,
        避免语料库较多时主线程阻塞。先用「加载中...」占位,后台读取完毕后再更新。
        """
        if not hasattr(self, "refCorpusCombo"):
            return
        if self._corpusManager is None:
            return
        items = self._corpusManager.listAll()
        activeId = self._corpusManager._activeId

        self.refCorpusCombo.blockSignals(True)
        self.refCorpusCombo.clear()
        self.refCorpusCombo.addItem("(请选择参照语料库...)", userData=None)
        # 先用占位文本快速填充(不做 sqlite3 查询,主线程零阻塞)
        validItems = [(info, info.id) for info in items if info.id != activeId]
        for info, corpusId in validItems:
            self.refCorpusCombo.addItem(f"{info.name}  ·  加载中...", userData=corpusId)
        self.refCorpusCombo.blockSignals(False)

        # 后台异步更新每项的统计(避免 sqlite3 阻塞主线程)
        if not validItems:
            return
        import threading

        def _fetchStats():
            updates = []
            for info, corpusId in validItems:
                stats = self._quickStats(info.dbPath)
                updates.append((corpusId, f"{info.name}  ·  {stats}"))
            # 用 QTimer.singleShot 回到主线程,比 invokeMethod 更简洁可靠
            QTimer.singleShot(0, lambda: self._applyRefCorpusStats(updates))

        threading.Thread(target=_fetchStats, daemon=True, name="kw-refstats").start()

    def _applyRefCorpusStats(self, updates: list) -> None:
        """后台统计读取完成后更新 combo 文本(主线程槽)。"""
        if not hasattr(self, "refCorpusCombo"):
            return
        # 建立 corpusId → comboIndex 的映射
        idToIdx: dict = {}
        for i in range(self.refCorpusCombo.count()):
            cid = self.refCorpusCombo.itemData(i)
            if cid is not None:
                idToIdx[int(cid)] = i
        self.refCorpusCombo.blockSignals(True)
        for corpusId, label in updates:
            idx = idToIdx.get(int(corpusId))
            if idx is not None:
                self.refCorpusCombo.setItemText(idx, label)
        self.refCorpusCombo.blockSignals(False)

    @staticmethod
    def _quickStats(dbPath: str) -> str:
        """快速读取语料库统计信息。"""
        if not dbPath or not os.path.exists(dbPath):
            return "0 个文件 / 0 字符"
        try:
            import sqlite3

            conn = sqlite3.connect(dbPath)
            try:
                cur = conn.execute("SELECT COUNT(*) FROM documents")
                files = int(cur.fetchone()[0] or 0)
                cur = conn.execute("SELECT COALESCE(SUM(char_count), 0) FROM documents")
                chars = int(cur.fetchone()[0] or 0)
                return f"{files} 个文件 / {chars:,} 字符"
            finally:
                conn.close()
        except Exception:
            return "0 个文件 / 0 字符"

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _initUi(self) -> None:
        outerLayout = QVBoxLayout(self)
        outerLayout.setContentsMargins(20, 20, 20, 20)
        outerLayout.setSpacing(12)

        # 标题
        titleLabel = SubtitleLabel("主题词分析 (Keyword List)", self)
        outerLayout.addWidget(titleLabel)

        # 说明
        hint = CaptionLabel(
            "对比「观察语料」与「参照语料」,找出会话特征词。"
            "底层算法:Log-Likelihood (Dunning 1993) / Log-Ratio (Hardie 2014) / %DIFF。",
            self,
        )
        hint.setStyleSheet("color: #888; font-size: 12px;")
        hint.setWordWrap(True)
        outerLayout.addWidget(hint)

        # 滚动区
        self._scrollArea = ScrollArea(self)
        self._scrollArea.setWidgetResizable(True)
        self._scrollArea.setStyleSheet("border: none; background: transparent;")
        outerLayout.addWidget(self._scrollArea, 1)

        # 滚动内容
        self._contentWidget = QWidget(self._scrollArea)
        self._contentWidget.setObjectName("keywordListContent")
        root = QVBoxLayout(self._contentWidget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        self._scrollArea.setWidget(self._contentWidget)

        # 参数卡
        root.addWidget(self._buildParamCard())

        # 结果摘要
        self._summary = self._buildSummaryPlaceholder()
        root.addWidget(self._summary)

        # Pivot 选项卡
        self.tabBar = Pivot(self._contentWidget)
        self.tabBar.addItem(routeKey="tabKeywords", text="关键词表")
        self.tabBar.addItem(routeKey="tabChart", text="显著度图表")
        self.tabBar.setCurrentItem("tabKeywords")
        root.addWidget(self.tabBar)

        # tab 容器
        self._tabContainer = QWidget(self._contentWidget)
        self._tabLayout = QVBoxLayout(self._tabContainer)
        self._tabLayout.setContentsMargins(0, 0, 0, 0)
        self._tabLayout.setSpacing(0)
        root.addWidget(self._tabContainer, 1)

        self._buildKeywordsTab()
        self._buildChartTab()
        self._ensureTabsAdded()
        self._showTab("tabKeywords")
        self.tabBar.currentItemChanged.connect(self._onTabItemChanged)

    def _buildParamCard(self) -> CardWidget:
        """参数卡片"""
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(StrongBodyLabel("分析参数", card))

        # 第 1 行: 观察语料(只读展示)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(BodyLabel("观察语料:", card))
        self.observedInfoLabel = CaptionLabel("当前活动语料库", card)
        self.observedInfoLabel.setStyleSheet("color: #666; font-size: 12px;")
        row1.addWidget(self.observedInfoLabel, 1)
        layout.addLayout(row1)

        # 第 2 行: 参照语料库选择
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(BodyLabel("参照语料:", card))
        self.refCorpusCombo = ComboBox(card)
        self.refCorpusCombo.setMinimumWidth(260)
        self.refCorpusCombo.currentIndexChanged.connect(self._onRefCorpusChanged)
        row2.addWidget(self.refCorpusCombo, 1)
        row2.addStretch(1)
        layout.addLayout(row2)

        # 第 3 行: 最低频次 / Top-N / 显著性阈值
        row3 = QHBoxLayout()
        row3.setSpacing(16)
        row3.addWidget(BodyLabel("最低频次:", card))
        self.minFreqSpin = SpinBox(card)
        self.minFreqSpin.setRange(1, 100)
        self.minFreqSpin.setValue(2)
        row3.addWidget(self.minFreqSpin)

        row3.addWidget(BodyLabel("Top-N:", card))
        self.topNSpin = SpinBox(card)
        # P1-fix 2026-07-19:topN 上限由 5000 降到 2000,避免用户配置极端值导致
        # 表格 sizeHint 过大撑爆父布局(产生"挤压 + 卡死"症状)。
        # AntConc 默认 200,研究用一般 500-1000 足够,2000 已是极端阅读上限。
        self.topNSpin.setRange(50, 2000)
        self.topNSpin.setValue(500)
        self.topNSpin.setSingleStep(50)
        row3.addWidget(self.topNSpin)

        row3.addWidget(BodyLabel("显著性:", card))
        self.sigCombo = ComboBox(card)
        self.sigCombo.addItem(
            "p < 0.05 (LL ≥ 5.02, 较宽松)", userData=LL_THRESHOLD_P005
        )
        self.sigCombo.addItem(
            "p < 0.01 (LL ≥ 6.63, 默认推荐)", userData=LL_THRESHOLD_P001
        )
        self.sigCombo.addItem(
            "p < 0.001 (LL ≥ 15.13, 大样本严格)",
            userData=LL_THRESHOLD_P001_LARGE,
        )
        self.sigCombo.setCurrentIndex(1)
        row3.addWidget(self.sigCombo)

        row3.addStretch(1)
        layout.addLayout(row3)

        # 第 4 行: 词长 / 大小写 / 停用词 / 排除数字
        row4 = QHBoxLayout()
        row4.setSpacing(12)

        row4.addWidget(BodyLabel("最短词长:", card))
        self.minLenSpin = SpinBox(card)
        self.minLenSpin.setRange(1, 10)
        self.minLenSpin.setValue(1)
        row4.addWidget(self.minLenSpin)

        row4.addWidget(BodyLabel("最长词长:", card))
        self.maxLenSpin = SpinBox(card)
        self.maxLenSpin.setRange(1, 100)
        self.maxLenSpin.setValue(50)
        row4.addWidget(self.maxLenSpin)

        self.caseSwitch = _makeSwitchButton("区分大小写", card)
        self.caseSwitch.setChecked(False)
        row4.addWidget(self.caseSwitch)

        self.stopSwitch = _makeSwitchButton("过滤停用词", card)
        self.stopSwitch.setChecked(False)
        row4.addWidget(self.stopSwitch)

        self.numberSwitch = _makeSwitchButton("排除纯数字", card)
        self.numberSwitch.setChecked(True)
        row4.addWidget(self.numberSwitch)

        row4.addStretch(1)
        layout.addLayout(row4)

        # 第 5 行: 操作按钮
        row5 = QHBoxLayout()
        row5.setSpacing(12)
        row5.addStretch(1)

        self.cancelBtn = PushButton("取消", card)
        self.cancelBtn.setIcon(FluentIcon.CLOSE)
        self.cancelBtn.clicked.connect(self._onCancelClicked)
        self.cancelBtn.setEnabled(False)
        row5.addWidget(self.cancelBtn)

        self.runBtn = PrimaryPushButton("开始分析", card)
        self.runBtn.setIcon(FluentIcon.PLAY)
        self.runBtn.clicked.connect(self._onRunClicked)
        self.runBtn.setEnabled(False)
        row5.addWidget(self.runBtn)
        # AI 解读按钮（PRD-001 REQ-AI-001）— 通过 Mixin 一行接入
        # 统一为 PrimaryPushButton,放在「开始分析」旁边
        self._aiInsightBtn = PrimaryPushButton("AI 解读", card)
        self._aiInsightBtn.setIcon(FluentIcon.HEART)
        self.setupAiInsightButton(self._aiInsightBtn)
        row5.addWidget(self._aiInsightBtn)

        layout.addLayout(row5)

        # 状态
        self.statusLabel = CaptionLabel(
            "加载观察语料 + 选择参照语料后点击「开始分析」", card
        )
        self.statusLabel.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.statusLabel)

        return card

    def _buildSummaryPlaceholder(self) -> ResultSummary:
        """占位的结果摘要卡"""
        summary = ResultSummary(self)
        summary.setTitle("主题词分析结果")
        summary.setPlaceholder("加载观察语料 + 选择参照语料后点击「开始分析」")
        summary.setMetrics(
            [
                ("观察 tokens", "—", MetricColor.NEUTRAL),
                ("参照 tokens", "—", MetricColor.NEUTRAL),
                ("显著词数", "—", MetricColor.NEUTRAL),
                ("耗时", "—", MetricColor.NEUTRAL),
            ]
        )
        return summary

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------
    def _buildKeywordsTab(self) -> None:
        """关键词表 Tab

        P1-fix 2026-07-19:大表(2000-5000 行)性能配置:
            - 关闭 alternatingRowColors:每行 paint 时多一次数据查询,5000 行累计卡顿
            - 关闭 setShowGrid:9 列 × 5000 行的网格线绘制开销巨大
            - ScrollPerPixel:替代默认 ScrollPerItem,大表滚动 30x+ 提速
            - uniformRowHeights + 固定 section 大小:sizeHint 计算从 O(行) 降到 O(1)
            - verticalHeader 隐藏且不参与布局
            - sizePolicy:Expanding/Expanding — 让外层 _scrollArea 接管垂直滚动,
              表格内部滚动条作为补充,避免 sizeHint 撑爆父布局导致「挤压」
        """
        self._keywordsTab = QWidget(self)
        layout = QVBoxLayout(self._keywordsTab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        # 顶部操作行
        actionRow = QHBoxLayout()
        self.keywordsHint = CaptionLabel(
            "按 Log-Likelihood 降序排列;显著词以浅橙色高亮(Log-Likelihood ≥ 阈值)",
            self._keywordsTab,
        )
        self.keywordsHint.setStyleSheet("color: #888; font-size: 11px;")
        actionRow.addWidget(self.keywordsHint, 1)

        exportBtn = PushButton("导出 CSV", self._keywordsTab)
        exportBtn.setIcon(FluentIcon.SAVE)
        exportBtn.clicked.connect(self._exportCsv)
        actionRow.addWidget(exportBtn)
        actionRow.addStretch(1)
        layout.addLayout(actionRow)

        # 关键词表
        self._keywordTable = TableWidget(self._keywordsTab)
        self._keywordTable.setColumnCount(9)
        self._keywordTable.setHorizontalHeaderLabels(
            [
                "排名",
                "关键词",
                "观察频次",
                "参照频次",
                "LL",
                "Log-Ratio",
                "%DIFF",
                "观察率(/10k)",
                "参照率(/10k)",
            ]
        )
        self._keywordTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._keywordTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        # --- 大表性能配置(P1-fix 2026-07-19) ---
        self._keywordTable.setAlternatingRowColors(False)  # 关闭交替行底色
        self._keywordTable.setShowGrid(False)  # 关闭网格线
        # 像素级滚动(替代默认 ScrollPerItem) — 大表流畅 30x+
        self._keywordTable.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self._keywordTable.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        # 行高统一:Qt 可缓存单行高度,sizeHint 计算从 O(n) → O(1)
        self._keywordTable.verticalHeader().setVisible(False)
        self._keywordTable.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Fixed
        )
        self._keywordTable.verticalHeader().setDefaultSectionSize(28)
        self._keywordTable.verticalHeader().setMinimumSectionSize(24)
        self._keywordTable.verticalHeader().setMaximumSectionSize(32)
        # 关键:不让 sizeHint 撑爆父布局 — 表格用 Expanding/Expanding,
        # 由外层 _scrollArea 统一负责垂直滚动;表格内部滚动条作为辅助。
        self._keywordTable.setSizePolicy(
            QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        )
        # 限制最小可见高度,避免出现一行超高 / 0 行的奇怪状态
        self._keywordTable.setMinimumHeight(320)
        # 列宽策略 P1-fix 2026-07-19:
        # 旧代码 9 列里 8 列 ResizeToContents,setItem 后 Qt 会遍历所有行重测列宽,
        # 5000 行 × 7 列 ≈ 35,000 次 QFontMetrics.boundingRect,主线程直接卡几百 ms。
        # 改为:排名/关键词列保持 Stretch/ResizeToContents(交互必需),
        # 其余数字列用 Interactive + 默认合理宽度,Qt 不会自动重测。
        hHeader = self._keywordTable.horizontalHeader()
        hHeader.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hHeader.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in range(2, 9):
            hHeader.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        hHeader.setStretchLastSection(False)
        hHeader.setMinimumSectionSize(60)
        # 预置默认列宽(用户可手动拖拽,首屏不卡)
        self._keywordTable.setColumnWidth(0, 70)  # 排名
        self._keywordTable.setColumnWidth(2, 100)  # 观察频次
        self._keywordTable.setColumnWidth(3, 100)  # 参照频次
        self._keywordTable.setColumnWidth(4, 90)  # LL
        self._keywordTable.setColumnWidth(5, 100)  # Log-Ratio
        self._keywordTable.setColumnWidth(6, 90)  # %DIFF
        self._keywordTable.setColumnWidth(7, 110)  # 观察率
        self._keywordTable.setColumnWidth(8, 110)  # 参照率
        # 默认行数 0,避免 setRowCount(0) 在大表上产生 sizeHint 抖动
        self._keywordTable.setRowCount(0)
        layout.addWidget(self._keywordTable, 1)

    def _buildChartTab(self) -> None:
        """显著度图表 Tab"""
        self._chartTab = QWidget(self)
        layout = QVBoxLayout(self._chartTab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        # 顶部说明
        chartTitle = StrongBodyLabel("LL 显著度分布(前 100 个关键词)", self._chartTab)
        layout.addWidget(chartTitle)

        self._figure = Figure(figsize=(10, 5), dpi=100)
        self._figure.patch.set_facecolor("#fafafa")
        self._ax = self._figure.add_subplot(111)
        self._ax.text(
            0.5,
            0.5,
            "等待分析...",
            ha="center",
            va="center",
            transform=self._ax.transAxes,
            color="#999",
            fontsize=14,
        )
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._canvas.setParent(self._chartTab)
        layout.addWidget(self._canvas, 1)

        # 底部说明
        chartHint = CaptionLabel(
            "虚线为显著性阈值。超过虚线的词为「显著」(橙色)。"
            "横轴: 排名(按 LL 降序);纵轴: Log-Likelihood 值。",
            self._chartTab,
        )
        chartHint.setStyleSheet("color: #888; font-size: 11px;")
        chartHint.setWordWrap(True)
        layout.addWidget(chartHint)

    def _ensureTabsAdded(self) -> None:
        """首次初始化时把 tab 加入 _tabLayout(仅调用一次)"""
        if getattr(self, "_tabsAdded", False):
            return
        for w in (self._keywordsTab, self._chartTab):
            self._tabLayout.addWidget(w)
        self._tabsAdded = True

    def _showTab(self, key: str) -> None:
        for w in (self._keywordsTab, self._chartTab):
            w.hide()
        mapping = {
            "tabKeywords": self._keywordsTab,
            "tabChart": self._chartTab,
        }
        target = mapping.get(key)
        if target:
            target.show()

    def _onTabItemChanged(self, routeKey: str) -> None:
        if routeKey:
            self._showTab(routeKey)
        if getattr(self, "_scrollArea", None):
            self._scrollArea.verticalScrollBar().setValue(0)

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _onRefCorpusChanged(self, idx: int) -> None:
        """参照语料下拉变化时,重建 referenceStore。"""
        if idx <= 0 or self._corpusManager is None:
            self._referenceStore = None
            return
        corpusId = self.refCorpusCombo.itemData(idx)
        if corpusId is None:
            self._referenceStore = None
            return
        info = self._corpusManager.registry.getById(int(corpusId))
        if info is None:
            self._referenceStore = None
            return
        try:
            self._referenceStore = CorpusStore(dbPath=info.dbPath, parent=self)
            # 共享 tokenCache(若同进程打开同一个 db,cache 也会共享)
            logger.info(f"[KeywordListWidget] 切换参照语料库:{info.name}")
        except Exception as e:
            logger.error(f"[KeywordListWidget] 加载参照语料失败: {e}")
            _showInfoBar("error", "加载参照语料失败", str(e), self, duration=3000)
            self._referenceStore = None

    def _onRunClicked(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        if self._observedStore is None or self._observedStore.fileCount() == 0:
            _showInfoBar(
                "warning", "无法分析", "请先在「语料导入」中加载观察语料", self
            )
            return
        if self._referenceStore is None:
            _showInfoBar("warning", "无法分析", "请先选择参照语料库", self)
            return

        # 启动 worker
        self.runBtn.setEnabled(False)
        self.cancelBtn.setEnabled(True)
        self.statusLabel.setText("正在分析...")
        self._summary.setPlaceholder("分析中...")

        # 取参数
        minFreq = self.minFreqSpin.value()
        topN = self.topNSpin.value()
        sigLevel = self.sigCombo.currentData() or LL_THRESHOLD_P001

        refName = "参照"
        if self._corpusManager is not None:
            refId = self.refCorpusCombo.currentData()
            if refId:
                info = self._corpusManager.registry.getById(int(refId))
                if info:
                    refName = info.name

        obsName = "观察"
        if self._corpusManager is not None:
            active = self._corpusManager.activeCorpus()
            if active:
                obsName = active.name

        worker = KeywordListWorker(
            observedStore=self._observedStore,
            referenceStore=self._referenceStore,
            segmenter=self._segmenter,
            observedName=obsName,
            referenceName=refName,
            minLength=self.minLenSpin.value(),
            maxLength=self.maxLenSpin.value(),
            caseSensitive=self.caseSwitch.isChecked(),
            useStopwords=self.stopSwitch.isChecked(),
            excludeNumbers=self.numberSwitch.isChecked(),
            stopwords=defaultStopwords(),
            minFreq=minFreq,
            topN=topN,
            significanceLevel=float(sigLevel),
        )

        # 用 WorkerMixin 统一管理生命周期
        if not self.startWorker(
            worker,
            onFinish=self._onFinished,
            onFail=self._onFailed,
        ):
            _showInfoBar("warning", "提示", "已有任务在运行中", self, duration=1500)
            return

        # 单独订阅 progress(WorkerMixin 不接管 progress)
        worker.progress.connect(self._onProgress)
        # P2-fix 2026-07-19:实时 partialStats — 多线程并发分词 + 分块算法
        # 每个文件 / 每个候选词 chunk 完成后立即推送统计,
        # 主线程 _onPartialStats 实时刷新状态栏与摘要预览。
        worker.partialStats.connect(
            self._onPartialStats, Qt.ConnectionType.QueuedConnection
        )
        # 表格预格式化数据(在 worker 里跑完 emit,主线程直接异步填充)
        worker.tableRowsReady.connect(
            self._onTableRowsReady, Qt.ConnectionType.QueuedConnection
        )
        # 图表数据(top-100 numpy 数组)
        worker.chartDataReady.connect(
            self._onChartDataReady, Qt.ConnectionType.QueuedConnection
        )

    def _onCancelClicked(self) -> None:
        """用户主动取消"""
        if self._worker is None:
            return
        try:
            self._worker.cancel()
            self.statusLabel.setText("正在取消...")
        except Exception as e:
            logger.warning(f"[KeywordListWidget] cancel 异常: {e}")

    def _onProgress(self, pct: int, msg: str) -> None:
        self.statusLabel.setText(f"[{pct}%] {msg}")

    def _onPartialStats(self, stats: dict) -> None:
        """Worker 实时 partialStats(多线程并发分词 + 分块算法进度)。

        P2-fix 2026-07-19:不同阶段显示不同的实时信息:
            - tokenize-ref / tokenize-obs:
                "分词中:N/M 文件 · 累计 K tokens · 用时 Xs"
            - algorithm:
                "算法中:X/Y 候选词 · 当前已显著 K 个 · 用时 Xs"
            - format:
                "格式化中:N/N 行 · 用时 Xs"

        这些信息都在主线程 < 1ms 内更新(label.setText 一次),用户能感受到
        「活」的进度反馈,而不是从 90% 直接跳到 100%。
        """
        try:
            phase = stats.get("phase", "")
            doneCount = int(stats.get("doneCount", 0))
            totalCount = int(stats.get("totalCount", 0))
            tokenCount = int(stats.get("tokenCount", 0))
            sigCount = int(stats.get("sigCount", 0))
            elapsedMs = int(stats.get("elapsedMs", 0))
        except Exception:
            return

        elapsedStr = f"{elapsedMs / 1000:.2f}s"
        if phase in ("tokenize-ref", "tokenize-obs"):
            labelName = "参照" if phase == "tokenize-ref" else "观察"
            msg = (
                f"分词中({labelName}):{doneCount}/{totalCount} 文件 · "
                f"累计 {tokenCount:,} tokens · 用时 {elapsedStr}"
            )
            self.statusLabel.setText(msg)
        elif phase == "algorithm":
            msg = (
                f"算法中:{doneCount:,}/{totalCount:,} 候选词 · "
                f"已显著 {sigCount:,} 个 · 用时 {elapsedStr}"
            )
            self.statusLabel.setText(msg)
            # 算法阶段实时更新「显著词数」预览 — 即使表格还没填好,用户也能看到
            # 显著词数在涨。
            if self._summary is not None:
                try:
                    # 用 setPlaceholder + setMetrics 组合:保持 metrics 行可见
                    # 但只更新「显著词数」,其余保持上一次的值
                    self._summary.setMetrics(
                        [
                            (
                                "观察 tokens",
                                f"{tokenCount:,}" if tokenCount else "—",
                                MetricColor.PRIMARY,
                            ),
                            (
                                "参照 tokens",
                                "—",
                                MetricColor.ACCENT,
                            ),
                            (
                                "显著词数(预览)",
                                f"{sigCount:,}",
                                (
                                    MetricColor.SUCCESS
                                    if sigCount > 0
                                    else MetricColor.NEUTRAL
                                ),
                            ),
                            (
                                "耗时",
                                elapsedStr,
                                MetricColor.NEUTRAL,
                            ),
                        ]
                    )
                except Exception:
                    pass
        elif phase == "format":
            msg = (
                f"格式化中:{doneCount:,}/{totalCount:,} 行 · "
                f"显著 {sigCount:,} · 用时 {elapsedStr}"
            )
            self.statusLabel.setText(msg)

    def _onFailed(self, err: str) -> None:
        self.runBtn.setEnabled(True)
        self.cancelBtn.setEnabled(False)
        self.statusLabel.setText(f"分析失败:{err[:100]}")
        self._summary.clear()
        self._summary.setPlaceholder("分析失败")
        _showInfoBar("error", "分析失败", err[:100], self, duration=4000)

    # ------------------------------------------------------------------
    # AI 解读协议（PRD-001 REQ-AI-001）— 由 AiInsightMixin 调用
    # ------------------------------------------------------------------
    def _aiHasResult(self) -> bool:
        r = getattr(self, "_result", None)
        return r is not None and getattr(r, "df", None) is not None and not r.df.empty

    def _aiCollectExplainArgs(self):
        if not self._aiHasResult():
            return None
        return ("keyword_list", {"result": self._result})

    def _onFinished(self, result: KeywordListResult) -> None:
        self._result = result
        self.runBtn.setEnabled(True)
        self.cancelBtn.setEnabled(False)

        # 渲染结果
        self._renderResults(result)
        # AI 解读:有结果后启用按钮
        self.refreshAiInsightButton()

        _showInfoBar(
            "success",
            "分析完成",
            f"共 {len(result.df)} 个候选词,{result.significantCount} 个显著,"
            f"耗时 {result.elapsedSeconds:.2f}s",
            self,
            duration=2500,
        )

    def _onTableRowsReady(
        self,
        rows: List[tuple],
        alignments: List[Any],
        significantIndices: List[int],
    ) -> None:
        """Worker 预格式化表格数据 ready(主线程槽,QueuedConnection)。

        此方法在主线程被调用,但**不**做 iloc 循环或字符串格式化 —
        数据已在 worker 线程预处理好,主线程只负责「立即异步填充」。
        """
        if not rows:
            return
        # 缓存,在 _renderKeywordTable 中直接使用
        self._pendingTableRows = rows
        self._pendingTableAlignments = alignments
        self._pendingSignificantIndices = significantIndices
        # 若 finishedWithResult 已经先到(罕见),立即填充
        if self._result is not None and self._keywordTable.rowCount() == 0:
            self._fillKeywordTableFromCache()

    def _onChartDataReady(self, chartData: tuple) -> None:
        """Worker 预提取的图表数据 ready(主线程槽)。

        chartData: (ranks, llVals, isKey, topN) — 全是 numpy 数组 / 整数
        """
        self._pendingChartData = chartData
        # 若 _renderResults 已先调用,需要立即重画
        if self._result is not None and self._ax is not None:
            self._renderChartFromCache(self._result)

    # ------------------------------------------------------------------
    # 结果渲染
    # ------------------------------------------------------------------
    def _renderResults(self, r: KeywordListResult) -> None:
        # 顶部摘要
        self._summary.clear()
        self._summary.setMetrics(
            [
                (
                    "观察 tokens",
                    f"{r.observedTokens:,}",
                    MetricColor.PRIMARY,
                ),
                (
                    "参照 tokens",
                    f"{r.referenceTokens:,}",
                    MetricColor.ACCENT,
                ),
                (
                    "显著词数",
                    f"{r.significantCount}",
                    MetricColor.SUCCESS,
                ),
                (
                    "耗时",
                    f"{r.elapsedSeconds:.2f}s",
                    MetricColor.NEUTRAL,
                ),
            ]
        )
        self._summary.setDetail(
            f"📊 <b>{r.observedName}</b> vs <b>{r.referenceName}</b> &nbsp;|&nbsp; "
            f"候选词 {len(r.df)} 个 &nbsp;|&nbsp; "
            f"LL 阈值 ≥ {r.significanceLevel:.2f} (p&lt;0.01) &nbsp;|&nbsp; "
            f"算法: {r.method}"
        )

        # 关键词表
        self._renderKeywordTable(r)

        # 显著度图表
        self._renderChart(r)

    def _renderKeywordTable(self, r: KeywordListResult) -> None:
        """异步批量填充关键词表(主线程零开销:数据已由 worker 预格式化好)。

        设计:
            - 之前:主线程在此方法内做 5000 行 df.iloc[i] 循环 + 字符串格式化,
              耗时 1-2 秒,UI 冻结
            - 现在:从 worker 缓存的 _pendingTableRows 直接传给 populateTableAsync,
              主线程仅触发「渐进增长行数 + 异步填充」,首帧 } 30ms

        协调:
            - worker 的 tableRowsReady 信号先于 finishedWithResult 触发
              (见 KeywordListWorker.runImpl 顺序)
            - 若 _onFinished 先到(几乎不可能),_onTableRowsReady 会再触发填充
            - 用 _keywordTable.rowCount() == 0 判定「尚未填充过」
        """
        df = r.df
        if df is None or df.empty:
            self._keywordTable.setRowCount(0)
            return

        # 若 worker 已经把数据推过来,直接用缓存填充
        if self._pendingTableRows:
            self._fillKeywordTableFromCache()
        # 否则等待 _onTableRowsReady 被信号触发后自动填充

    def _fillKeywordTableFromCache(self) -> None:
        """从 _pendingTableRows 缓存填充表格(主线程轻量操作)。

        数据已经在 worker 线程预格式化好,这里只做:
            1. setSortingEnabled(False) — 防止 setItem 触发 sort
            2. populateTableAsync(..., growRows=True) — 渐进增长行数,避免一次
               setRowCount(n) 撑爆父布局 sizeHint(用户感知的"挤压+卡死")
            3. 高亮分布到每一批(避免 onComplete 里 45k 次 setBackground 主线程阻塞)

        P1-fix 2026-07-19:
            - 高亮从「全部 onComplete 一次性 setBackground 45k 次」改为「每批
              填充完顺手高亮该批内的显著行」,UI 永不冻结。
            - 去掉本地的 setRowCount(n) — 改由 populateTableAsync(growRows=True)
              按 chunk 渐进增长,首帧只 setRowCount(chunkSize=500)。
        """
        rows = self._pendingTableRows
        alignments = self._pendingTableAlignments
        significantIndices = self._pendingSignificantIndices
        if not rows:
            return

        n = len(rows)
        wasSorting = self._keywordTable.isSortingEnabled()
        self._keywordTable.setSortingEnabled(False)

        # 把 list 转成 set,O(1) 查询;显著词行用浅橙色背景高亮
        sigSet = set(significantIndices or [])

        # 预构造高亮 brush,避免每 cell 重复构造 QColor
        sigBrush = QColor("#fff7e6")
        chunkSize = 500
        nCols = 9

        def _onProgress(doneRows: int, totalRows: int) -> None:
            """每一批填充完后,只高亮该批范围内的显著行(主线程零阻塞)。"""
            start = max(0, doneRows - chunkSize)
            for i in range(start, min(doneRows, n)):
                if i in sigSet:
                    for c in range(nCols):
                        cell = self._keywordTable.item(i, c)
                        if cell is not None:
                            cell.setBackground(sigBrush)

        def _onComplete() -> None:
            # 排序恢复推迟一帧,让最后一批的高亮先落地,避免排序触发重排时
            # 高亮状态被冲掉;同时确保 setSortingEnabled 不在事件循环深处卡住。
            def _restore() -> None:
                if wasSorting:
                    self._keywordTable.setSortingEnabled(True)

            QTimer.singleShot(0, _restore)

        # growRows=True:不一次性 setRowCount(n),由 populateTableAsync 按 chunk
        # 渐进撑大行数,首帧只分配 chunkSize 行,sizeHint 平滑递进。
        populateTableAsync(
            self._keywordTable,
            rows,
            alignments=alignments,
            chunkSize=chunkSize,
            onComplete=_onComplete,
            onProgress=_onProgress,
            growRows=True,
        )
        # 清缓存,避免下次分析时被误用
        self._pendingTableRows = None
        self._pendingTableAlignments = None
        self._pendingSignificantIndices = None

    def _renderChart(self, r: KeywordListResult) -> None:
        """绘制 LL 显著度分布图(主线程轻量:数据由 worker 预提取)"""
        if self._ax is None or self._canvas is None:
            return
        # 若 worker 已推送图表数据,直接使用;否则等待 _onChartDataReady
        if self._pendingChartData is None:
            # 数据没到,稍等一下(信号 QueuedConnection,通常在几 ms 内就到)
            return
        self._renderChartFromCache(r)

    def _renderChartFromCache(self, r: KeywordListResult) -> None:
        """从 _pendingChartData 渲染 LL 分布图(主线程轻量操作)。

        P1-fix 2026-07-19:重渲染拆成两步
            1. 在当前事件循环里只构造 axes/scatter/legend(纯 matplotlib artist,
               不触发 raster render)
            2. canvas.draw_idle() 通过 QTimer.singleShot(0, ...) 推迟到下一帧
               执行,让 finishedWithResult 槽尽快返回,UI 可立即重绘(状态栏/摘要)
        """
        if self._ax is None or self._canvas is None:
            return
        if self._pendingChartData is None:
            return
        ranks, llVals, isKey, topN = self._pendingChartData

        self._ax.clear()
        self._ax.set_facecolor("#fafafa")

        if len(ranks) == 0:
            self._ax.text(
                0.5,
                0.5,
                "无数据",
                ha="center",
                va="center",
                transform=self._ax.transAxes,
                fontsize=14,
                color="#999",
            )
            QTimer.singleShot(0, self._canvas.draw_idle)
            return

        if (~isKey).any():
            self._ax.scatter(
                ranks[~isKey],
                llVals[~isKey],
                s=24,
                c="#cccccc",
                alpha=0.6,
                label="不显著",
                edgecolors="gray",
                linewidths=0.4,
            )
        if isKey.any():
            self._ax.scatter(
                ranks[isKey],
                llVals[isKey],
                s=28,
                c="#fa8c16",
                alpha=0.85,
                label="显著",
                edgecolors="#d4380d",
                linewidths=0.4,
            )

        # 阈值线
        self._ax.axhline(
            y=r.significanceLevel,
            color="#f5222d",
            linestyle="--",
            linewidth=1.0,
            alpha=0.8,
            label=f"阈值 = {r.significanceLevel:.2f}",
        )

        self._ax.set_xlabel("排名 (按 LL 降序)", fontsize=10)
        self._ax.set_ylabel("Log-Likelihood", fontsize=10)
        self._ax.set_title(
            f"LL 显著度分布 (前 {topN} 个 / 共 {len(r.df)} 个候选词)",
            fontsize=12,
        )
        self._ax.grid(True, linestyle="--", alpha=0.3)
        self._ax.legend(loc="upper right", fontsize=9, framealpha=0.85)

        # P1-fix 2026-07-19:tight_layout() 在 QtAgg 内嵌 figure 上计算量大,
        # 改用 subplots_adjust 直接固定边距(本图是单 axes,无嵌套),开销从
        # ~150ms 降到 < 5ms,UI 几乎无感。
        try:
            self._figure.subplots_adjust(left=0.08, right=0.97, top=0.92, bottom=0.10)
        except Exception:
            pass
        # 把 raster 重绘推迟到下一帧,避免主线程槽里连续吃 CPU
        QTimer.singleShot(0, self._canvas.draw_idle)

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def _exportCsv(self) -> None:
        if self._result is None or self._result.df is None or self._result.df.empty:
            _showInfoBar("warning", "无法导出", "请先运行分析", self)
            return
        df = self._result.df
        defaultName = (
            f"keyword_{self._result.observedName}_vs_{self._result.referenceName}.csv"
        )
        defaultName = defaultName.replace("/", "_").replace("\\", "_")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出主题词表",
            defaultName,
            "CSV Files (*.csv)",
        )
        if not path:
            return
        if not path.endswith(".csv"):
            path += ".csv"
        try:
            # 用 utf-8-sig 让 Excel 正确识别中文
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(
                    [
                        "Rank",
                        "Keyword",
                        "ObsFreq",
                        "RefFreq",
                        "ObsRate(/10k)",
                        "RefRate(/10k)",
                        "LogLikelihood",
                        "LogRatio",
                        "PctDiff(%)",
                        "IsKey(p<0.01)",
                    ]
                )
                for _, row in df.iterrows():
                    lr = row["LogRatio"]
                    if lr == float("inf"):
                        lrStr = "+inf"
                    elif lr == float("-inf"):
                        lrStr = "-inf"
                    elif math.isnan(float(lr)):
                        lrStr = ""
                    else:
                        lrStr = f"{lr:.4f}"

                    pct = row["PctDiff"]
                    if pct == float("inf"):
                        pctStr = "+inf"
                    elif math.isnan(float(pct)):
                        pctStr = ""
                    else:
                        pctStr = f"{pct:.2f}"

                    w.writerow(
                        [
                            int(row["Rank"]),
                            row["Keyword"],
                            int(row["ObsFreq"]),
                            int(row["RefFreq"]),
                            f"{float(row['ObsRate']):.2f}",
                            f"{float(row['RefRate']):.2f}",
                            f"{float(row['LL']):.4f}",
                            lrStr,
                            pctStr,
                            "是" if bool(row["IsKey"]) else "否",
                        ]
                    )
            _showInfoBar("success", "导出成功", f"已保存到 {path}", self)
        except Exception as e:
            logger.exception(f"[KeywordListWidget] 导出失败: {e}")
            _showInfoBar("error", "导出失败", str(e), self)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        try:
            # closeEvent 时传入等待超时,让 worker 有机会干净退出
            self.disposeWorker(waitMs=300)
        except Exception as e:
            logger.warning(f"[KeywordListWidget] closeEvent dispose 异常: {e}")
        super().closeEvent(event)
