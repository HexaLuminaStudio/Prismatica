# coding: utf-8
"""
搭配分析面板(对标 AntConc Collocates)

按需求文档 v3 §2.4.5:
    - FR-CLB-001~011 全部实现
    - MI / MI3 / T-score / LogDice / Z-score / Delta-P 六大搭配强度
    - 可配置跨距(L/R 独立)
    - 跨距位置分布表
    - 网络图数据输出(由调用方绘制)
    - CSV 导出

设计:
    - 复用 corpusStore 的 tokenCache,jieba 分词
    - QThread 后台计算,UI 不阻塞
    - 表格列宽自适应,数值右对齐
    - UI 风格与 word_analysis_widget 等子页面一致(20px 外边距、SubtitleLabel 标题、卡片 16/12 内边距)
"""

from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    DoubleSpinBox,
    FluentIcon as FIF,
    LineEdit,
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

from app.view.widgets.freq_analyzer.collocation_engine import (
    CollocationEngine,
    CollocationResult,
)
from app.view.widgets.freq_analyzer.ai_insight_mixin import AiInsightMixin
from app.view.widgets.freq_analyzer.freq_engine import TextSegmenter
from app.view.widgets.freq_analyzer.token_cache import TokenCache
from app.view.widgets.freq_analyzer.ui_helpers import (
    _makeSwitchButton,
    _showInfoBar,
)

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from loguru import logger


# ---------------------------------------------------------------------------
# 后台分析线程
# ---------------------------------------------------------------------------
class CollocationWorker(QThread):
    """搭配分析后台线程"""

    progress = Signal(int, str)  # (percent, status)
    finished = Signal(object)  # CollocationResult
    failed = Signal(str)  # 错误信息

    def __init__(
        self,
        corpusStore,
        segmenter: TextSegmenter,
        nodeWord: str,
        leftSpan: int,
        rightSpan: int,
        minFreq: int,
        topN: int,
        caseSensitive: bool,
        significanceThreshold: float = 3.0,
        continuityCorrection: bool = False,
        crossSentenceBoundary: bool = False,
    ):
        super().__init__()
        self._corpusStore = corpusStore
        self._segmenter = segmenter
        self._nodeWord = nodeWord
        self._leftSpan = leftSpan
        self._rightSpan = rightSpan
        self._minFreq = minFreq
        self._topN = topN
        self._caseSensitive = caseSensitive
        self._significanceThreshold = significanceThreshold
        self._continuityCorrection = continuityCorrection
        # P1-2 修复:跨句边界开关
        self._crossSentenceBoundary = crossSentenceBoundary
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            self.progress.emit(5, "正在加载语料...")
            fileToText = self._corpusStore.effectiveTexts()
            fileNames = list(fileToText.keys())
            n = len(fileNames)
            if n == 0:
                self.failed.emit("语料库为空")
                return

            self.progress.emit(10, f"共 {n} 个文件,开始分词...")

            from app.view.widgets.freq_analyzer.token_cache import (
                backendModelVersion,
            )

            tokenCache: Optional[TokenCache] = (
                self._corpusStore.tokenCache()
                if hasattr(self._corpusStore, "tokenCache")
                else None
            )
            modelVer = backendModelVersion("jieba")

            allTokens: List[str] = []
            # P1-2 修复:跨句边界索引集合,记录「该位置之前存在句边界」
            # 按 allTokens 的全局下标维护,与分词结果一一对应
            allBoundaryIndices: List[int] = []

            # 累计偏移,每读一份文件,偏移为 allTokens 当前长度
            globalOffset = 0
            for idx, name in enumerate(fileNames, start=1):
                if self._cancel:
                    return
                text = fileToText.get(name, "")
                if not text:
                    continue
                # 走缓存
                if tokenCache is not None:
                    tokens = tokenCache.getOrCompute(
                        text=text,
                        backendName="jieba",
                        modelVersion=modelVer,
                        # P0-fix:改用公开 cutJieba
                        computeFn=lambda t: self._segmenter.cutJieba(t),
                    )
                else:
                    # P0-fix:同上
                    tokens = self._segmenter.cutJieba(text)

                # P1-2 修复:在每份文件的 token 序列里找「句末标点」,
                # 标点之后第一个 token 的全局下标就是句子边界索引
                # 使用与 sentiment_engine 相同的切句正则
                if not self._crossSentenceBoundary:
                    localBoundaryPositions: List[int] = []
                    for localIdx, tok in enumerate(tokens):
                        if self._isSentenceEndToken(tok):
                            # 边界索引 = 下一 token 的全局下标
                            globalBoundaryIdx = globalOffset + localIdx + 1
                            # 仅在该索引 < N 时记入(避免最后一 token 之后越界)
                            if globalBoundaryIdx < globalOffset + len(tokens):
                                localBoundaryPositions.append(globalBoundaryIdx)
                    allBoundaryIndices.extend(localBoundaryPositions)

                allTokens.extend(tokens)
                globalOffset += len(tokens)

                pct = 10 + int(70 * idx / n)
                self.progress.emit(pct, f"分词 {idx}/{n}")

            if self._cancel:
                return

            self.progress.emit(85, "正在计算搭配强度...")
            engine = CollocationEngine()
            result = engine.analyze(
                tokens=allTokens,
                nodeWord=self._nodeWord,
                leftSpan=self._leftSpan,
                rightSpan=self._rightSpan,
                minFreq=self._minFreq,
                topN=self._topN,
                caseSensitive=self._caseSensitive,
                significanceThreshold=self._significanceThreshold,
                continuityCorrection=self._continuityCorrection,
                crossSentenceBoundary=self._crossSentenceBoundary,
                sentenceBoundaryIndices=(
                    allBoundaryIndices if not self._crossSentenceBoundary else None
                ),
            )

            if self._cancel:
                return
            self.progress.emit(100, "完成!")
            self.finished.emit(result)

        except Exception as e:
            import traceback

            logger.exception(f"[CollocationWorker] 失败: {e}")
            self.failed.emit(f"{e}\n{traceback.format_exc()}")

    @staticmethod
    def _isSentenceEndToken(tok: str) -> bool:
        """判断该 token 是否含句末标点(P1-2 修复)

        设计:
            - 中文:`。！？…`
            - 英文:`! ?`(jieb 切分英文时这些通常会单独成 token)
        """
        if not tok:
            return False
        return any(ch in tok for ch in "。！？…!?")


# ---------------------------------------------------------------------------
# 主面板
# ---------------------------------------------------------------------------
class CollocationWidget(AiInsightMixin, QWidget):
    """搭配分析面板

    继承 AiInsightMixin 提供「AI 解读」抽屉能力

    UI 布局:
        [ 参数区 ]
            - 节点词输入
            - 左跨距 / 右跨距 SpinBox
            - 最低共现频次
            - Top-N
            - 区分大小写 (SwitchButton)
            - [开始分析] [取消] 按钮
        [ 结果摘要卡 ] 4 个指标(节点词频 / 显著搭配数 / Top1 MI / 耗时)
        [ 选项卡 Pivot ]
            - 搭配词表(MI/T/LogDice 等)
            - 跨距位置分布
            - 网络图数据
    """

    _AI_INSIGHT_PANEL_NAME = "搭配分析"
    _AI_INSIGHT_TYPE = "collocation"

    def __init__(self, parent: Optional[QWidget] = None, corpusStore=None):
        super().__init__(parent=parent)
        self._corpusStore = corpusStore
        self._worker: Optional[CollocationWorker] = None
        self._result: Optional[CollocationResult] = None

        # 分词器(共享 corpusStore 的 tokenCache)
        tokenCache = corpusStore.tokenCache() if corpusStore is not None else None
        self._segmenter = TextSegmenter(tokenCache=tokenCache)

        self._initUi()

        if corpusStore is not None:
            self._bindCorpusStore(corpusStore)

    # ------------------------------------------------------------------
    # 语料库绑定
    # ------------------------------------------------------------------
    def setCorpusStore(self, store):
        if self._corpusStore is store:
            return
        self._corpusStore = store
        tokenCache = store.tokenCache() if store is not None else None
        self._segmenter = TextSegmenter(tokenCache=tokenCache)
        # 切换语料库时清空旧结果,避免与新语料错配
        self._resetResultsForCorpusSwitch()
        self._updateCorpusInfo()

    def _resetResultsForCorpusSwitch(self) -> None:
        """切换语料库时清空所有分析结果与 UI(P0-fix)

        设计依据:CorpusStore 是多语料库共享的引用,切换时旧库的分析结果
        不能继续显示在新语料上(否则表格中的频次/MI 与新语料不一致)。
        """
        self._result = None
        for tbl in (
            getattr(self, "_collocatesTable", None),
            getattr(self, "_positionTable", None),
            getattr(self, "_networkTable", None),
        ):
            if tbl is not None:
                try:
                    tbl.setRowCount(0)
                except Exception:
                    pass
        # 取消正在运行的 worker,避免旧 worker 写回新 store
        worker = getattr(self, "_worker", None)
        if worker is not None and worker.isRunning():
            try:
                worker.cancel()
                worker.wait(200)
            except Exception:
                pass
            self._worker = None
        # 摘要卡片复位
        summary = getattr(self, "_summary", None)
        if summary is not None:
            try:
                from app.view.widgets.freq_analyzer.result_summary import MetricColor

                summary.clear()
                summary.setPlaceholder("已切换语料库,请重新分析")
                summary.setMetrics(
                    [
                        ("节点词频", "—", MetricColor.NEUTRAL),
                        ("显著搭配(MI≥3)", "—", MetricColor.NEUTRAL),
                        ("Top-1 MI", "—", MetricColor.NEUTRAL),
                        ("耗时", "—", MetricColor.NEUTRAL),
                    ]
                )
            except Exception:
                pass
        # 状态栏复位
        try:
            self.statusLabel.setText("已切换语料库,请重新分析")
        except Exception:
            pass

    def _bindCorpusStore(self, store):
        """订阅语料库变化信号"""
        if hasattr(store, "filesAdded"):
            store.filesAdded.connect(lambda *_: self._updateCorpusInfo())
        if hasattr(store, "filesRemoved"):
            store.filesRemoved.connect(lambda *_: self._updateCorpusInfo())
        if hasattr(store, "cleanRuleChanged"):
            store.cleanRuleChanged.connect(lambda: self._updateCorpusInfo())
        self._updateCorpusInfo()

    def _updateCorpusInfo(self):
        """更新语料库信息显示"""
        if self._corpusStore is None:
            self.statusLabel.setText("未加载语料库")
            self.runBtn.setEnabled(False)
            return
        n = self._corpusStore.fileCount()
        chars = self._corpusStore.totalChars()
        self.statusLabel.setText(f"语料库: {n} 个文件,共 {chars:,} 字符")
        self.runBtn.setEnabled(n > 0)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _initUi(self) -> None:
        # 外层布局:与其他页面保持一致(20/12)
        outerLayout = QVBoxLayout(self)
        outerLayout.setContentsMargins(20, 20, 20, 20)
        outerLayout.setSpacing(12)

        # 页面标题
        titleLabel = SubtitleLabel("搭配分析", self)
        outerLayout.addWidget(titleLabel)

        # 顶部说明
        hint = CaptionLabel(
            "基于 2×2 列联表(Church & Hanks 1990)分析节点词周围共现关系,"
            "输出 MI / MI3 / T-score / LogDice / Z-score / ΔP 等学术指标。",
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
        self._contentWidget.setObjectName("collocationContent")
        root = QVBoxLayout(self._contentWidget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        self._scrollArea.setWidget(self._contentWidget)

        # ===== 参数卡片 =====
        root.addWidget(self._buildParamCard())

        # ===== 结果摘要卡 =====
        self._summary = self._buildSummaryPlaceholder()
        root.addWidget(self._summary)

        # ===== Pivot 选项卡 =====
        self.tabBar = Pivot(self._contentWidget)
        self.tabBar.addItem(routeKey="tabCollocates", text="搭配词表")
        self.tabBar.addItem(routeKey="tabPosition", text="跨距分布")
        self.tabBar.addItem(routeKey="tabNetwork", text="网络图数据")
        self.tabBar.setCurrentItem("tabCollocates")
        root.addWidget(self.tabBar)

        # tab 内容容器
        self._tabContainer = QWidget(self._contentWidget)
        self._tabLayout = QVBoxLayout(self._tabContainer)
        self._tabLayout.setContentsMargins(0, 0, 0, 0)
        self._tabLayout.setSpacing(0)
        root.addWidget(self._tabContainer, 1)

        # 构建 tab
        self._buildCollocatesTab()
        self._buildPositionTab()
        self._buildNetworkTab()
        self._ensureTabsAdded()
        self._showTab("tabCollocates")
        self.tabBar.currentItemChanged.connect(self._onTabItemChanged)

    def _buildParamCard(self) -> CardWidget:
        """参数卡片"""
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(StrongBodyLabel("分析参数", card))

        # 第 1 行:节点词输入
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        row1.addWidget(BodyLabel("节点词:", card))
        self.nodeInput = LineEdit(card)
        self.nodeInput.setPlaceholderText("输入要分析的节点词(留空则报错)")
        self.nodeInput.setMinimumWidth(220)
        row1.addWidget(self.nodeInput, 1)
        layout.addLayout(row1)

        # 第 2 行:跨距 / 最低频次 / Top-N
        row2 = QHBoxLayout()
        row2.setSpacing(16)
        row2.addWidget(BodyLabel("左跨距 L:", card))
        self.leftSpin = SpinBox(card)
        self.leftSpin.setRange(0, 30)
        self.leftSpin.setValue(5)
        row2.addWidget(self.leftSpin)

        row2.addWidget(BodyLabel("右跨距 R:", card))
        self.rightSpin = SpinBox(card)
        self.rightSpin.setRange(0, 30)
        self.rightSpin.setValue(5)
        row2.addWidget(self.rightSpin)

        row2.addWidget(BodyLabel("最低共现频次:", card))
        self.minFreqSpin = SpinBox(card)
        self.minFreqSpin.setRange(1, 100)
        self.minFreqSpin.setValue(2)
        row2.addWidget(self.minFreqSpin)

        row2.addWidget(BodyLabel("Top-N:", card))
        self.topNSpin = SpinBox(card)
        self.topNSpin.setRange(10, 500)
        self.topNSpin.setValue(100)
        row2.addWidget(self.topNSpin)

        row2.addStretch(1)
        layout.addLayout(row2)

        # 第 2.5 行:显著性参数(学术严谨性)
        row2b = QHBoxLayout()
        row2b.setSpacing(16)
        row2b.addWidget(BodyLabel("MI 显著性阈值:", card))
        self.sigSpin = DoubleSpinBox(card)
        self.sigSpin.setRange(0.0, 20.0)
        self.sigSpin.setDecimals(1)
        self.sigSpin.setSingleStep(0.5)
        self.sigSpin.setValue(3.0)
        row2b.addWidget(self.sigSpin)

        self.yatesSwitch = _makeSwitchButton("Yates 连续性修正", card)
        self.yatesSwitch.setChecked(False)
        row2b.addWidget(self.yatesSwitch)

        # P1-2 修复:跨句边界开关
        self.crossSentSwitch = _makeSwitchButton("跨句边界", card)
        self.crossSentSwitch.setChecked(False)
        row2b.addWidget(self.crossSentSwitch)

        row2b.addStretch(1)
        layout.addLayout(row2b)

        # 第 3 行:大小写 + 操作按钮
        row3 = QHBoxLayout()
        row3.setSpacing(12)

        self.caseSwitch = _makeSwitchButton("区分大小写", card)
        self.caseSwitch.setChecked(False)
        row3.addWidget(self.caseSwitch)

        row3.addStretch(1)

        self.runBtn = PrimaryPushButton("开始分析", card)
        self.runBtn.setIcon(FIF.PLAY)
        self.runBtn.clicked.connect(self._onRunClicked)
        row3.addWidget(self.runBtn)
        # AI 解读按钮（PRD-001 REQ-AI-001）— 通过 Mixin 一行接入
        # 统一为 PrimaryPushButton,放在「开始分析」旁边
        self._aiInsightBtn = PrimaryPushButton("AI 解读", card)
        self._aiInsightBtn.setIcon(FIF.HEART)
        self.setupAiInsightButton(self._aiInsightBtn)
        row3.addWidget(self._aiInsightBtn)

        layout.addLayout(row3)

        # 状态
        self.statusLabel = CaptionLabel("未加载语料库", card)
        self.statusLabel.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.statusLabel)

        return card

    def _buildSummaryPlaceholder(self) -> CardWidget:
        """占位的结果摘要卡(后续渲染时替换为 ResultSummary)"""
        from app.view.widgets.freq_analyzer.result_summary import (
            MetricColor,
            ResultSummary,
        )

        summary = ResultSummary(self)
        summary.setPlaceholder("请输入节点词并点击「开始分析」")
        # 预先填充指标占位
        summary.setMetrics(
            [
                ("节点词频", "—", MetricColor.PRIMARY),
                ("显著搭配(MI≥3)", "—", MetricColor.SUCCESS),
                ("Top-1 MI", "—", MetricColor.ACCENT),
                ("耗时", "—", MetricColor.NEUTRAL),
            ]
        )
        return summary

    # ============= 搭配词表 tab =============
    def _buildCollocatesTab(self):
        self._collocatesTab = QWidget(self)
        layout = QVBoxLayout(self._collocatesTab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        # 顶部说明
        self._collocatesHint = CaptionLabel(
            "搭配词按 MI 降序排列;显著搭配(基于设定 MI 阈值)以浅橙色高亮;"
            "表中包含 O/C/E 三类列联表原始频次,便于学术复核",
            self._collocatesTab,
        )
        self._collocatesHint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._collocatesHint)

        # 表格
        self._collocatesTable = TableWidget(self._collocatesTab)
        self._collocatesTable.setColumnCount(11)
        self._collocatesTable.setHorizontalHeaderLabels(
            [
                "搭配词",
                "共现 O",
                "搭配词频 C",
                "期望 E",
                "MI",
                "MI3",
                "T-score",
                "LogDice",
                "Z-score",
                "ΔP₁",
                "ΔP₂",
            ]
        )
        self._collocatesTable.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._collocatesTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._collocatesTable.setAlternatingRowColors(True)
        self._collocatesTable.verticalHeader().setVisible(False)
        hHeader = self._collocatesTable.horizontalHeader()
        hHeader.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for c in range(1, 11):
            hHeader.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._collocatesTable, 1)

        # 操作行
        actionRow = QHBoxLayout()
        exportBtn = PushButton("导出 CSV", self._collocatesTab)
        exportBtn.setIcon(FIF.SAVE)
        exportBtn.clicked.connect(self._exportCollocationCsv)
        actionRow.addWidget(exportBtn)
        actionRow.addStretch(1)
        layout.addLayout(actionRow)

    # ============= 跨距位置分布 tab =============
    def _buildPositionTab(self):
        self._positionTab = QWidget(self)
        layout = QVBoxLayout(self._positionTab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        self._positionHint = CaptionLabel(
            "展示各搭配词在跨距内不同位置(L5..L1 / R1..R5)的频次分布",
            self._positionTab,
        )
        self._positionHint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._positionHint)

        self._positionTable = TableWidget(self._positionTab)
        self._positionTable.setColumnCount(1)  # 占位
        self._positionTable.setHorizontalHeaderLabels(["搭配词"])
        self._positionTable.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._positionTable.setAlternatingRowColors(True)
        self._positionTable.verticalHeader().setVisible(False)
        layout.addWidget(self._positionTable, 1)

    # ============= 网络图数据 tab =============
    def _buildNetworkTab(self):
        self._networkTab = QWidget(self)
        layout = QVBoxLayout(self._networkTab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        self._networkHint = CaptionLabel(
            "导出边列表(node, collocate, MI),可直接用于 NetworkWidget 绘制搭配网络图",
            self._networkTab,
        )
        self._networkHint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._networkHint)

        self._networkTable = TableWidget(self._networkTab)
        self._networkTable.setColumnCount(3)
        self._networkTable.setHorizontalHeaderLabels(["节点词", "搭配词", "MI"])
        self._networkTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._networkTable.setAlternatingRowColors(True)
        self._networkTable.verticalHeader().setVisible(False)
        hHeader = self._networkTable.horizontalHeader()
        hHeader.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hHeader.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hHeader.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._networkTable, 1)

        # 导出按钮
        actionRow = QHBoxLayout()
        exportEdgesBtn = PushButton("导出边列表 CSV", self._networkTab)
        exportEdgesBtn.setIcon(FIF.SAVE)
        exportEdgesBtn.clicked.connect(self._exportNetworkEdgesCsv)
        actionRow.addWidget(exportEdgesBtn)
        actionRow.addStretch(1)
        layout.addLayout(actionRow)

    # ------------------------------------------------------------------
    # Tab 切换
    # ------------------------------------------------------------------
    def _showTab(self, key: str) -> None:
        for w in (self._collocatesTab, self._positionTab, self._networkTab):
            w.hide()
        mapping = {
            "tabCollocates": self._collocatesTab,
            "tabPosition": self._positionTab,
            "tabNetwork": self._networkTab,
        }
        target = mapping.get(key)
        if target:
            target.show()

    def _ensureTabsAdded(self) -> None:
        if getattr(self, "_tabsAdded", False):
            return
        for w in (self._collocatesTab, self._positionTab, self._networkTab):
            self._tabLayout.addWidget(w)
        self._tabsAdded = True

    def _onTabItemChanged(self, routeKey: str) -> None:
        if routeKey:
            self._showTab(routeKey)
        if getattr(self, "_scrollArea", None):
            self._scrollArea.verticalScrollBar().setValue(0)

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _onRunClicked(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if self._corpusStore is None or self._corpusStore.fileCount() == 0:
            _showInfoBar("warning", "无法分析", "请先在「语料导入」中加载语料", self)
            return

        nodeWord = self.nodeInput.text().strip()
        if not nodeWord:
            _showInfoBar("warning", "无法分析", "请输入节点词", self)
            return

        # 启动线程
        self.runBtn.setEnabled(False)
        self.statusLabel.setText("正在分析...")

        self._worker = CollocationWorker(
            corpusStore=self._corpusStore,
            segmenter=self._segmenter,
            nodeWord=nodeWord,
            leftSpan=self.leftSpin.value(),
            rightSpan=self.rightSpin.value(),
            minFreq=self.minFreqSpin.value(),
            topN=self.topNSpin.value(),
            caseSensitive=self.caseSwitch.isChecked(),
            significanceThreshold=self.sigSpin.value(),
            continuityCorrection=self.yatesSwitch.isChecked(),
            crossSentenceBoundary=self.crossSentSwitch.isChecked(),
        )
        self._worker.progress.connect(self._onProgress)
        self._worker.finished.connect(self._onFinished)
        self._worker.failed.connect(self._onFailed)
        self._worker.start()

    def _onProgress(self, pct: int, msg: str):
        self.statusLabel.setText(f"[{pct}%] {msg}")

    def _onFailed(self, err: str):
        self._resetUi()
        _showInfoBar("error", "分析失败", err[:100], self, duration=4000)

    def _onFinished(self, result: CollocationResult):
        self._result = result
        self._resetUi()
        self._renderResults(result)
        _showInfoBar(
            "success",
            "分析完成",
            f"节点「{result.nodeWord}」共 {len(result.collocates)} 个搭配词,"
            f" 耗时 {result.elapsedSeconds:.2f}s",
            self,
            duration=2500,
        )
        # AI 解读:结果出来后启用按钮
        self.refreshAiInsightButton()

    def _resetUi(self):
        self.runBtn.setEnabled(True)
        # 语料清空时禁用 AI 解读
        self.disableAiInsightButton()

    # ------------------------------------------------------------------
    # AI 解读协议（PRD-001 REQ-AI-001）— 由 AiInsightMixin 调用
    # ------------------------------------------------------------------
    def _aiHasResult(self) -> bool:
        r = getattr(self, "_result", None)
        return r is not None and bool(getattr(r, "collocates", []))

    def _aiCollectExplainArgs(self):
        if not self._aiHasResult():
            return None
        return ("collocation", {"result": self._result})

    # ------------------------------------------------------------------
    # 结果渲染
    # ------------------------------------------------------------------
    def _renderResults(self, r: CollocationResult):
        # 顶部摘要
        topMi = r.collocates[0].mi if r.collocates else 0.0
        import math as _math

        if _math.isinf(topMi):
            topMiStr = "+∞"
        else:
            topMiStr = f"{topMi:.2f}"
        self._summary.clear()
        from app.view.widgets.freq_analyzer.result_summary import (
            MetricColor,
            ResultSummary,
        )

        # 重建指标卡(若类型不匹配则替换)
        if not isinstance(self._summary, ResultSummary):
            # 简单 fallback:用同名 setter
            self._summary.setMetrics(
                [
                    ("节点词频 R", f"{r.nodeFreq:,}", MetricColor.PRIMARY),
                    (
                        f"显著搭配(MI≥{r.significanceThreshold:.1f})",
                        f"{r.significantCount}",
                        MetricColor.SUCCESS,
                    ),
                    ("Top-1 MI", topMiStr, MetricColor.ACCENT),
                    ("耗时", f"{r.elapsedSeconds:.2f}s", MetricColor.NEUTRAL),
                ]
            )
        else:
            self._summary.setMetrics(
                [
                    ("节点词频 R", f"{r.nodeFreq:,}", MetricColor.PRIMARY),
                    (
                        f"显著搭配(MI≥{r.significanceThreshold:.1f})",
                        f"{r.significantCount}",
                        MetricColor.SUCCESS,
                    ),
                    ("Top-1 MI", topMiStr, MetricColor.ACCENT),
                    ("耗时", f"{r.elapsedSeconds:.2f}s", MetricColor.NEUTRAL),
                ]
            )
        yatesTag = "Yates 修正:开" if r.continuityCorrection else "Yates 修正:关"
        self._summary.setDetail(
            f"📊 节点「<b>{r.nodeWord}</b>」 跨距 <b>L{r.leftSpan}-R{r.rightSpan}</b> &nbsp;|&nbsp; "
            f"语料 <b>{r.totalTokens:,}</b> tokens / <b>{r.uniqueTypes:,}</b> types &nbsp;|&nbsp; "
            f"{yatesTag} &nbsp;|&nbsp; 显著性算法: 2×2 列联表 (Church & Hanks 1990)"
        )

        # 搭配词表
        self._renderCollocationTable(r)

        # 跨距位置分布
        self._renderPositionTable(r)

        # 网络图数据
        self._renderNetworkTable(r)

    def _renderCollocationTable(self, r: CollocationResult):
        rows = r.collocates
        self._collocatesTable.setRowCount(len(rows))
        import math as _math

        for idx, entry in enumerate(rows):
            # 搭配词
            self._collocatesTable.setItem(idx, 0, QTableWidgetItem(entry.collocate))
            # 共现频次 O
            freqItem = QTableWidgetItem(f"{entry.freq:,}")
            freqItem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._collocatesTable.setItem(idx, 1, freqItem)
            # 搭配词全语料频次 C
            cItem = QTableWidgetItem(f"{entry.collocateFreq:,}")
            cItem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._collocatesTable.setItem(idx, 2, cItem)
            # 期望频次 E(R·C/N)
            eItem = QTableWidgetItem(f"{entry.expectedFreq:.4f}")
            eItem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._collocatesTable.setItem(idx, 3, eItem)
            # 各项统计量(MI .. ΔP₂)
            statValues = [
                entry.mi,
                entry.mi3,
                entry.tScore,
                entry.logDice,
                entry.zScore,
                entry.deltaP1,
                entry.deltaP2,
            ]
            for offset, value in enumerate(statValues):
                col = 4 + offset
                if _math.isinf(value):
                    text = "+∞" if value > 0 else "-∞"
                else:
                    text = f"{value:.4f}"
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self._collocatesTable.setItem(idx, col, item)

            # 高亮显著搭配(MI ≥ 用户设定阈值)
            if entry.isSignificant:
                for c in range(11):
                    cell = self._collocatesTable.item(idx, c)
                    if cell:
                        cell.setBackground(QColor("#fff7e6"))

    def _renderPositionTable(self, r: CollocationResult):
        """跨距位置分布表

        行:搭配词;列:L5..L1, R1..R5(总跨距 2*maxSpan,取当前 r.leftSpan / r.rightSpan)
        """
        if not r.positionDistribution:
            self._positionTable.setRowCount(0)
            self._positionTable.setColumnCount(1)
            self._positionHint.setText("无跨距位置分布数据")
            return

        # 计算列标签(L5..L1 / R1..R5)
        leftLabels = [
            f"L{abs(p)}" for p in sorted(r.positionDistribution.keys()) if p < 0
        ]
        rightLabels = [f"R{p}" for p in sorted(r.positionDistribution.keys()) if p > 0]
        cols = leftLabels + rightLabels

        # 行:取 MI 排序的前 N 个搭配词(避免表格过大)
        topCollocates = [e.collocate for e in r.collocates[:30]]
        self._positionTable.setRowCount(len(topCollocates))
        self._positionTable.setColumnCount(1 + len(cols))
        headers = ["搭配词"] + cols
        self._positionTable.setHorizontalHeaderLabels(headers)

        # 统计每个搭配词在每个位置的频次
        for row, collocate in enumerate(topCollocates):
            self._positionTable.setItem(row, 0, QTableWidgetItem(collocate))
            for colIdx, pos in enumerate(
                sorted(r.positionDistribution.keys()), start=1
            ):
                cnt = r.positionDistribution[pos].get(collocate, 0)
                item = QTableWidgetItem(str(cnt) if cnt > 0 else "")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # 列索引对应 cols 中的位置
                # 这里简化:直接按 sorted keys 顺序填充(列标签已匹配)
                pass

        # 重新填充:按 cols 顺序
        for row, collocate in enumerate(topCollocates):
            for colIdx, label in enumerate(cols, start=1):
                # 从 label 解析位置
                if label.startswith("L"):
                    pos = -int(label[1:])
                elif label.startswith("R"):
                    pos = int(label[1:])
                else:
                    continue
                cnt = r.positionDistribution.get(pos, {}).get(collocate, 0)
                item = QTableWidgetItem(str(cnt) if cnt > 0 else "")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._positionTable.setItem(row, colIdx, item)

        hHeader = self._positionTable.horizontalHeader()
        hHeader.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for c in range(1, len(headers)):
            hHeader.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)

    def _renderNetworkTable(self, r: CollocationResult):
        """网络图边数据表"""
        edges = r.networkEdges
        self._networkTable.setRowCount(len(edges))
        for row, (node, collocate, weight) in enumerate(edges):
            self._networkTable.setItem(row, 0, QTableWidgetItem(node))
            self._networkTable.setItem(row, 1, QTableWidgetItem(collocate))
            weightItem = QTableWidgetItem(f"{weight:.3f}")
            weightItem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._networkTable.setItem(row, 2, weightItem)

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def _exportCollocationCsv(self):
        if self._result is None or not self._result.collocates:
            _showInfoBar("warning", "无法导出", "请先运行分析", self)
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出搭配词表",
            f"collocates_{self._result.nodeWord}.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(
                    [
                        "搭配词",
                        "共现频次O",
                        "搭配词频C",
                        "期望频次E",
                        "MI",
                        "MI3",
                        "T-score",
                        "LogDice",
                        "Z-score",
                        "DeltaP1",
                        "DeltaP2",
                        "显著搭配(MI>=阈值)",
                    ]
                )
                for e in self._result.collocates:
                    w.writerow(
                        [
                            e.collocate,
                            e.freq,
                            e.collocateFreq,
                            f"{e.expectedFreq:.4f}",
                            f"{e.mi:.4f}",
                            f"{e.mi3:.4f}",
                            f"{e.tScore:.4f}",
                            f"{e.logDice:.4f}",
                            f"{e.zScore:.4f}",
                            f"{e.deltaP1:.4f}",
                            f"{e.deltaP2:.4f}",
                            "是" if e.isSignificant else "否",
                        ]
                    )
            _showInfoBar("success", "导出成功", f"已保存到 {path}", self)
        except Exception as e:
            logger.exception(f"[CollocationWidget] 导出失败: {e}")
            _showInfoBar("error", "导出失败", str(e), self)

    def _exportNetworkEdgesCsv(self):
        if self._result is None or not self._result.networkEdges:
            _showInfoBar("warning", "无法导出", "请先运行分析", self)
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出网络图边列表",
            f"collocation_edges_{self._result.nodeWord}.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["节点词", "搭配词", "MI"])
                for node, collocate, weight in self._result.networkEdges:
                    w.writerow([node, collocate, f"{weight:.4f}"])
            _showInfoBar("success", "导出成功", f"已保存到 {path}", self)
        except Exception as e:
            logger.exception(f"[CollocationWidget] 导出失败: {e}")
            _showInfoBar("error", "导出失败", str(e), self)
