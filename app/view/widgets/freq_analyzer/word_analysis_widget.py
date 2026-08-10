# coding: utf-8
"""
词语分析面板(融合高频词分析)

按需求文档 v3 §2.4.3 + §2.4.4:
    - 词汇指标卡: 词汇密度、平均词长、TTR/Guiraud/Herdan/Uber
    - 词汇增长曲线: Type-Token Curve
    - 高频词列表: 含累计 % + 50/80/90% 覆盖率标记
    - 词汇分布: 按子库 / 文件统计

设计:
    - 使用 QThread 后台计算,UI 不阻塞
    - tokenCache 复用,避免重复分词
    - ResultSummary 共享样式
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import matplotlib
from app.core.services import beginPaidAnalysisExport

matplotlib.use("Agg", force=True)  # 后台线程用 Agg 渲染
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    ScrollArea,
    CheckBox,
    SubtitleLabel,
    TableWidget,
)

from app.core.models.project import RESOURCE_TYPE_WORD_ANALYSIS
from app.view.widgets.freq_analyzer.freq_engine import (
    TextSegmenter,
    posTag,
)
from app.view.widgets.freq_analyzer.result_summary import MetricColor, ResultSummary
from app.view.widgets.freq_analyzer.ai_insight_mixin import AiInsightMixin
from app.view.widgets.freq_analyzer.resource_sink_mixin import ResourceSinkMixin
from app.view.widgets.freq_analyzer.token_cache import TokenCache
from app.view.widgets.freq_analyzer.ui_helpers import _makeSwitchButton, _showInfoBar
from app.view.widgets.freq_analyzer.word_analysis_engine import (
    POS_COARSE_CATEGORY,
    WordAnalysisEngine,
    WordMetrics,
)

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from app.core.utils import logger


# ---------------------------------------------------------------------------
# 后台分析线程
# ---------------------------------------------------------------------------
class WordAnalysisWorker(QThread):
    """词语分析后台线程"""

    progress = Signal(int, str)  # (percent, status)
    finished = Signal(object)  # WordMetrics
    failed = Signal(str)  # 错误信息

    def __init__(
        self,
        corpusStore,
        segmenter: TextSegmenter,
        topN: int = 100,
        minWordLength: int = 1,
        minFreq: int = 1,
        posFilter: Optional[List[str]] = None,
        includePos: bool = True,
    ):
        super().__init__()
        self._corpusStore = corpusStore
        self._segmenter = segmenter
        self._topN = topN
        self._minWordLength = minWordLength
        self._minFreq = minFreq
        self._posFilter = posFilter
        self._includePos = includePos
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            # 1) 收集所有 token(共享 TokenCache)
            self.progress.emit(5, "正在加载语料...")
            # effectiveTexts 是方法,需要调用才能得到 dict
            fileToText = self._corpusStore.effectiveTexts()
            fileNames = list(fileToText.keys())
            n = len(fileNames)
            if n == 0:
                self.failed.emit("语料库为空")
                return

            self.progress.emit(10, f"共 {n} 个文件,开始分词...")
            allTokens: List[str] = []
            allPosTags: List[str] = []
            fileToTokens: Dict[str, List[str]] = {}

            from app.view.widgets.freq_analyzer.token_cache import (
                backendModelVersion,
                hashText,
            )

            tokenCache: Optional[TokenCache] = (
                self._corpusStore.tokenCache()
                if hasattr(self._corpusStore, "tokenCache")
                else None
            )
            modelVer = backendModelVersion("jieba")

            for idx, name in enumerate(fileNames, start=1):
                if self._cancel:
                    return
                text = fileToText.get(name, "")
                if not text:
                    continue
                # 分词(走 cache)
                if tokenCache is not None:
                    tokens = tokenCache.getOrCompute(
                        text=text,
                        backendName="jieba",
                        modelVersion=modelVer,
                        # P0-fix:改用公开 cutJieba,不再访问 _jiebaCut 私有方法
                        computeFn=lambda t: self._segmenter.cutJieba(t),
                    )
                else:
                    # P0-fix:同上
                    tokens = self._segmenter.cutJieba(text)

                fileToTokens[name] = tokens
                allTokens.extend(tokens)

                # POS 标注(可选)
                if self._includePos:
                    posList = posTag(text)
                    # 取与 tokens 等长的部分(防御性)
                    pos_for_text = [p for (w, p) in posList[: len(tokens)]]
                    # 若 posList 短于 tokens(罕见),用 "x" 填充
                    while len(pos_for_text) < len(tokens):
                        pos_for_text.append("x")
                    allPosTags.extend(pos_for_text)

                pct = 10 + int(80 * idx / n)
                self.progress.emit(
                    pct, f"分词 {idx}/{n}: {os.path.basename(name)[:20]}"
                )

            if self._cancel:
                return

            self.progress.emit(92, "正在计算指标...")

            # 2) 引擎分析
            engine = WordAnalysisEngine()
            metrics = engine.analyze(
                tokens=allTokens,
                posTags=allPosTags if self._includePos else None,
                topN=self._topN,
                minWordLength=self._minWordLength,
                minFreq=self._minFreq,
                posFilter=self._posFilter,
                fileCount=n,
            )

            # 3) 词汇分布(FR-WDA-001)
            if self._cancel:
                return
            self.progress.emit(96, "正在统计词汇分布...")
            distribution = engine.analyzeDistribution(
                fileToTokens, topN=min(30, self._topN)
            )
            metrics.__dict__["distribution"] = distribution

            if self._cancel:
                return
            self.progress.emit(100, "完成!")
            self.finished.emit(metrics)

        except Exception as e:
            import traceback

            logger.exception(f"[WordAnalysisWorker] 失败: {e}")
            self.failed.emit(f"{e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# 主面板
# ---------------------------------------------------------------------------
class WordAnalysisWidget(AiInsightMixin, ResourceSinkMixin, QWidget):
    """词语分析面板

    继承 AiInsightMixin 提供「AI 解读」抽屉能力
    继承 ResourceSinkMixin 提供分析结果自动归档到当前激活项目的能力

    UI 布局:
        [ 参数区 ]
            - Top-N (SpinBox 50-500)
            - 最小词长 (SpinBox 1-10)
            - 最小频次 (SpinBox 1-100)
            - 词性过滤 (多选 ComboBox)
            - 包含 POS 标注 (SwitchButton)
            - [开始分析] [AI 解读] 按钮
        [ 结果摘要卡 ] 4 个指标(总词数 / Type / TTR / 词汇密度)
        [ 选项卡 ]
            - 词汇指标(指标卡 + 词汇增长曲线图)
            - 高频词列表(Top-N + 累计 % + 覆盖率)
            - 词汇分布(子库对比)
    """

    _AI_INSIGHT_PANEL_NAME = "词语分析"
    _AI_INSIGHT_TYPE = "word_analysis"

    # PRD-002 资源归档配置(REQ-PROJ-001)
    _RESOURCE_TYPE = RESOURCE_TYPE_WORD_ANALYSIS
    _RESOURCE_TITLE_PREFIX = "词语分析"

    def __init__(self, parent: Optional[QWidget] = None, corpusStore=None):
        super().__init__(parent=parent)
        self._corpusStore = corpusStore
        self._boundCorpusStore = None
        self._worker: Optional[WordAnalysisWorker] = None
        self._metrics: Optional[WordMetrics] = None

        # 分词器(共享 corpusStore 的 tokenCache)
        tokenCache = corpusStore.tokenCache() if corpusStore is not None else None
        self._segmenter = TextSegmenter(tokenCache=tokenCache)

        # matplotlib figure
        self._figure: Optional[Figure] = None
        self._canvas: Optional[FigureCanvasQTAgg] = None
        self._ax = None

        self._initUi()

        if corpusStore is not None:
            self._bindCorpusStore(corpusStore)

    # ------------------------------------------------------------------
    # 语料库绑定
    # ------------------------------------------------------------------
    def setCorpusStore(self, store):
        if self._corpusStore is store:
            self._onCorpusChanged()
            return
        self._corpusStore = store
        tokenCache = store.tokenCache() if store is not None else None
        self._segmenter = TextSegmenter(tokenCache=tokenCache)
        self._bindCorpusStore(store)
        # 切换语料库时清空旧分析结果,避免与新语料错配
        self._resetResultsForCorpusSwitch()
        self._updateCorpusInfo()

    def _resetResultsForCorpusSwitch(self) -> None:
        """切换语料库时清空所有分析结果与 UI(P0-fix)

        设计依据:CorpusStore 是多语料库共享引用,切换时旧库结果不能
        继续显示在新语料上(否则表格中的频次/TTR/MTLD 与新语料不一致)。
        """
        self._lastMetrics = None
        # 清空表格
        for tbl in (
            getattr(self, "_highFreqTable", None),
            getattr(self, "_distTable", None),
        ):
            if tbl is not None:
                try:
                    tbl.setRowCount(0)
                except Exception:
                    pass
        # 清空 type-token 曲线
        ax = getattr(self, "_ax", None)
        canvas = getattr(self, "_canvas", None)
        if ax is not None:
            try:
                ax.clear()
                ax.set_title("词汇增长曲线 (Type-Token Curve)", fontsize=12)
                ax.set_xlabel("Tokens")
                ax.set_ylabel("Types")
                ax.grid(True, linestyle="--", alpha=0.5)
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
        # 取消正在运行的 worker
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
                summary.clear()
                summary.setPlaceholder("已切换语料库,请重新分析")
            except Exception:
                pass
        mc = getattr(self, "_metricsCards", None)
        if mc is not None:
            try:
                mc.clear()
                mc.setPlaceholder("已切换语料库,请重新分析")
            except Exception:
                pass
        # 高频词覆盖率标签复位
        cov = getattr(self, "coverageLabel", None)
        if cov is not None:
            try:
                cov.setText("")
            except Exception:
                pass
        # 导出按钮复位
        try:
            if hasattr(self, "exportBtn"):
                self.exportBtn.setEnabled(False)
        except Exception:
            pass

    def _bindCorpusStore(self, store):
        """订阅语料库变化信号"""
        if self._boundCorpusStore is store:
            return
        oldStore = self._boundCorpusStore
        if oldStore is not None:
            for signal in (oldStore.textsChanged, oldStore.cleanRuleChanged):
                try:
                    signal.disconnect(self._onCorpusChanged)
                except (RuntimeError, TypeError):
                    pass
        store.textsChanged.connect(self._onCorpusChanged)
        store.cleanRuleChanged.connect(self._onCorpusChanged)
        self._boundCorpusStore = store
        self._updateCorpusInfo()

    def _onCorpusChanged(self) -> None:
        self._resetResultsForCorpusSwitch()
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
        # ===== 外层布局:与其他页面保持一致(边距 20px、间距 12px) =====
        outerLayout = QVBoxLayout(self)
        outerLayout.setContentsMargins(20, 20, 20, 20)
        outerLayout.setSpacing(12)

        # ===== 页面标题(L0:SubtitleLabel) =====
        titleLabel = SubtitleLabel("词语分析", self)
        outerLayout.addWidget(titleLabel)

        # ===== QScrollArea 容器 =====
        # 当窗口较小或内容溢出时可滚动
        self._scrollArea = ScrollArea(self)
        self._scrollArea.setWidgetResizable(True)
        self._scrollArea.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scrollArea.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        # 平滑滚动(与其他页面一致)
        self._scrollArea.setStyleSheet("border: none; background: transparent;")
        outerLayout.addWidget(self._scrollArea, 1)

        # ===== 滚动内容容器(实际所有控件都加到这里) =====
        # 注意:此处的 root 实际是 contentLayout,但命名沿用旧代码,
        # 后续 root.addWidget(...) / root.addLayout(...) 不变。
        self._contentWidget = QWidget(self._scrollArea)
        self._contentWidget.setStyleSheet(
            "QWidget { background-color: transparent; border: none; }"
        )
        self._contentWidget.setObjectName("wordAnalysisContent")
        root = QVBoxLayout(self._contentWidget)
        # scrollContent 内部边距 0,避免与 outerLayout 重复(与其他页面一致)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self._scrollArea.setWidget(self._contentWidget)

        # ===== 参数区 =====
        paramCard = CardWidget(self)
        paramLayout = QVBoxLayout(paramCard)
        paramLayout.setContentsMargins(16, 12, 16, 12)
        paramLayout.setSpacing(8)

        title = StrongBodyLabel("参数设置", paramCard)
        paramLayout.addWidget(title)

        # 第 1 行: Top-N / 最小词长 / 最小频次
        row1 = QHBoxLayout()
        row1.setSpacing(16)

        row1.addWidget(BodyLabel("Top-N:", paramCard))
        self.topNSpin = SpinBox(paramCard)
        self.topNSpin.setRange(10, 1000)
        self.topNSpin.setValue(100)
        row1.addWidget(self.topNSpin)

        row1.addWidget(BodyLabel("最小词长:", paramCard))
        self.minLenSpin = SpinBox(paramCard)
        self.minLenSpin.setRange(1, 10)
        self.minLenSpin.setValue(1)
        row1.addWidget(self.minLenSpin)

        row1.addWidget(BodyLabel("最小频次:", paramCard))
        self.minFreqSpin = SpinBox(paramCard)
        self.minFreqSpin.setRange(1, 100)
        self.minFreqSpin.setValue(1)
        row1.addWidget(self.minFreqSpin)

        row1.addStretch(1)
        paramLayout.addLayout(row1)

        # 第 2 行: 词性过滤(多选 ComboBox,简化版用复选框)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(BodyLabel("词性过滤(留空=全部):", paramCard))

        # 提供常用 POS 多选
        posFrame = QWidget(paramCard)
        posLayout = QHBoxLayout(posFrame)
        posLayout.setContentsMargins(0, 0, 0, 0)
        posLayout.setSpacing(4)
        self._posCheckBoxes: Dict[str, CheckBox] = {}
        # 常用词性
        commonPos = [
            ("n", "名词"),
            ("v", "动词"),
            ("a", "形容词"),
            ("d", "副词"),
            ("r", "代词"),
            ("m", "数词"),
            ("p", "介词"),
        ]
        for tag, label in commonPos:
            cb = CheckBox(label, posFrame)
            cb.setProperty("posTag", tag)
            self._posCheckBoxes[tag] = cb
            posLayout.addWidget(cb)
        posLayout.addStretch(1)
        row2.addWidget(posFrame, 1)

        row2.addStretch(1)
        paramLayout.addLayout(row2)

        # 第 3 行: 包含 POS 标注 + 开始按钮
        row3 = QHBoxLayout()
        row3.setSpacing(12)

        self.includePosSwitch = _makeSwitchButton("包含词性标注", paramCard)
        self.includePosSwitch.setChecked(True)
        row3.addWidget(self.includePosSwitch)

        row3.addStretch(1)

        self.runBtn = PrimaryPushButton("开始分析", paramCard)
        self.runBtn.setIcon(FIF.PLAY)
        self.runBtn.clicked.connect(self._onRunClicked)
        row3.addWidget(self.runBtn)
        # AI 解读按钮（PRD-001 REQ-AI-001）— 通过 Mixin 一行接入
        # 统一为 PrimaryPushButton,放在「开始分析」旁边
        self._aiInsightBtn = PrimaryPushButton("AI 解读", paramCard)
        self._aiInsightBtn.setIcon(FIF.HEART)
        self.setupAiInsightButton(self._aiInsightBtn)
        row3.addWidget(self._aiInsightBtn)

        paramLayout.addLayout(row3)

        # 状态
        self.statusLabel = CaptionLabel("未加载语料库", paramCard)
        self.statusLabel.setStyleSheet("color: #666; font-size: 11px;")
        paramLayout.addWidget(self.statusLabel)

        root.addWidget(paramCard)

        # ===== 结果摘要卡 =====
        self._summary = ResultSummary(self)
        self._summary.setPlaceholder("请先加载语料并点击「开始分析」")
        root.addWidget(self._summary)

        # ===== 选项卡(Pivot) =====
        from qfluentwidgets import Pivot

        self.tabBar = Pivot(self)
        self.tabBar.addItem(routeKey="tabMetrics", text="词汇指标")
        self.tabBar.addItem(routeKey="tabHighFreq", text="高频词列表")
        self.tabBar.addItem(routeKey="tabDistribution", text="词汇分布")
        self.tabBar.setCurrentItem("tabMetrics")
        root.addWidget(self.tabBar)

        # 选项卡内容容器(必须放在滚动区域内)
        self._tabContainer = QWidget(self._contentWidget)
        self._tabLayout = QVBoxLayout(self._tabContainer)
        self._tabLayout.setContentsMargins(0, 0, 0, 0)
        self._tabLayout.setSpacing(0)
        root.addWidget(self._tabContainer, 1)

        # 构建各 tab 内容(初始隐藏)
        self._buildMetricsTab()
        self._buildHighFreqTab()
        self._buildDistributionTab()
        # 把 3 个 tab 加入 _tabLayout(只一次,后续切换仅 show/hide)
        self._ensureTabsAdded()
        self._showTab("tabMetrics")

        self.tabBar.currentItemChanged.connect(self._onTabItemChanged)

    def _showTab(self, key: str) -> None:
        # 隐藏所有,然后只显示目标
        for w in (self._metricsTab, self._highFreqTab, self._distributionTab):
            w.hide()
        mapping = {
            "tabMetrics": self._metricsTab,
            "tabHighFreq": self._highFreqTab,
            "tabDistribution": self._distributionTab,
        }
        target = mapping.get(key)
        if target:
            target.show()

    def _ensureTabsAdded(self) -> None:
        """首次初始化时,把 3 个 tab 加入 _tabLayout(仅调用一次)"""
        if getattr(self, "_tabsAdded", False):
            return
        for w in (self._metricsTab, self._highFreqTab, self._distributionTab):
            self._tabLayout.addWidget(w)
        self._tabsAdded = True

    def _onTabItemChanged(self, routeKey: str) -> None:
        """Pivot.currentItemChanged 回调:routeKey 直接对应面板 key"""
        if routeKey:
            self._showTab(routeKey)
        # 滚动到顶部,方便用户查看
        if getattr(self, "_scrollArea", None):
            self._scrollArea.verticalScrollBar().setValue(0)

    # ============= 词汇指标 tab =============
    def _buildMetricsTab(self):
        self._metricsTab = QWidget(self)
        layout = QVBoxLayout(self._metricsTab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        # 指标卡片(6 项: 词汇密度 / 平均词长 / TTR / Guiraud / Herdan / Uber)
        self._metricsCards = ResultSummary(self._metricsTab)
        self._metricsCards.setPlaceholder("尚未分析")
        layout.addWidget(self._metricsCards)

        # 词汇增长曲线
        chartCard = CardWidget(self._metricsTab)
        chartLayout = QVBoxLayout(chartCard)
        chartLayout.setContentsMargins(16, 12, 16, 12)
        chartLayout.setSpacing(8)

        chartTitle = StrongBodyLabel("词汇增长曲线 (Type-Token Curve)", chartCard)
        chartLayout.addWidget(chartTitle)

        chartHint = CaptionLabel(
            "横轴: 累计 Token 数;纵轴: 累计 Type 数。曲线越陡说明新词越多。",
            chartCard,
        )
        chartHint.setStyleSheet("color: #888; font-size: 11px;")
        chartLayout.addWidget(chartHint)

        # matplotlib canvas
        self._figure = Figure(figsize=(8, 4), dpi=100)
        self._figure.patch.set_facecolor("#fafafa")
        self._ax = self._figure.add_subplot(111)
        self._ax.set_xlabel("Tokens")
        self._ax.set_ylabel("Types")
        self._ax.grid(True, linestyle="--", alpha=0.5)
        self._canvas = FigureCanvasQTAgg(self._figure)
        chartLayout.addWidget(self._canvas)

        # 初始提示
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
        self._canvas.draw()

        layout.addWidget(chartCard, 1)

    # ============= 高频词 tab =============
    def _buildHighFreqTab(self):
        self._highFreqTab = QWidget(self)
        layout = QVBoxLayout(self._highFreqTab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        # 覆盖率提示
        self.coverageLabel = CaptionLabel("", self._highFreqTab)
        self.coverageLabel.setStyleSheet("color: #1890ff; font-size: 11px;")
        layout.addWidget(self.coverageLabel)

        # 表格
        self._highFreqTable = TableWidget(self._highFreqTab)
        self._highFreqTable.setColumnCount(6)
        self._highFreqTable.setHorizontalHeaderLabels(
            [
                "排名",
                "词语",
                "频次",
                "频率",
                "累计频次",
                "累计 %",
            ]
        )
        self._highFreqTable.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._highFreqTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._highFreqTable.setAlternatingRowColors(True)
        self._highFreqTable.verticalHeader().setVisible(False)
        hHeader = self._highFreqTable.horizontalHeader()
        hHeader.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hHeader.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hHeader.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hHeader.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hHeader.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hHeader.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._highFreqTable, 1)

        # 导出按钮
        actionRow = QHBoxLayout()
        exportBtn = PushButton("导出 CSV", self._highFreqTab)
        exportBtn.setIcon(FIF.SAVE)
        exportBtn.clicked.connect(self._exportHighFreq)
        actionRow.addWidget(exportBtn)
        actionRow.addStretch(1)
        layout.addLayout(actionRow)

    # ============= 词汇分布 tab =============
    def _buildDistributionTab(self):
        self._distributionTab = QWidget(self._tabContainer)
        layout = QVBoxLayout(self._distributionTab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        self.distHint = CaptionLabel(
            "展示总频率 Top-30 词语在各子库/文件中的分布(行:词语,列:文件)",
            self._distributionTab,
        )
        self.distHint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.distHint)

        self._distTable = TableWidget(self._distributionTab)
        self._distTable.setColumnCount(1)  # 占位
        self._distTable.setHorizontalHeaderLabels(["词语"])
        self._distTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._distTable.setAlternatingRowColors(True)
        self._distTable.verticalHeader().setVisible(False)
        layout.addWidget(self._distTable, 1)

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _onRunClicked(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if self._corpusStore is None or self._corpusStore.fileCount() == 0:
            _showInfoBar("warning", "无法分析", "请先在「语料导入」中加载语料", self)
            return

        # 读取参数
        topN = self.topNSpin.value()
        minLen = self.minLenSpin.value()
        minFreq = self.minFreqSpin.value()
        posFilter = [t for t, cb in self._posCheckBoxes.items() if cb.isChecked()]
        includePos = self.includePosSwitch.isChecked()

        # 启动线程
        self.runBtn.setEnabled(False)
        self.statusLabel.setText("正在分析...")
        self._summary.setPlaceholder("分析中...")

        self._worker = WordAnalysisWorker(
            corpusStore=self._corpusStore,
            segmenter=self._segmenter,
            topN=topN,
            minWordLength=minLen,
            minFreq=minFreq,
            posFilter=posFilter if posFilter else None,
            includePos=includePos,
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

    def _onFinished(self, metrics: WordMetrics):
        self._metrics = metrics
        self._resetUi()
        self._renderResults(metrics)
        _showInfoBar(
            "success",
            "分析完成",
            f"共 {metrics.totalTokens:,} token / {metrics.totalTypes:,} type,"
            f" 耗时 {metrics.elapsedSeconds:.2f}s",
            self,
            duration=2500,
        )
        # AI 解读:有结果后启用按钮
        self.refreshAiInsightButton()
        # PRD-002:归档到当前激活项目(若有)
        if metrics.totalTokens > 0:
            self.notifyResourceCreated()

    def _resetUi(self):
        self.runBtn.setEnabled(True)
        # 语料清空时禁用 AI 解读
        self.disableAiInsightButton()

    # ------------------------------------------------------------------
    # AI 解读协议（PRD-001 REQ-AI-001）— 由 AiInsightMixin 调用
    # ------------------------------------------------------------------
    def _aiHasResult(self) -> bool:
        m = getattr(self, "_metrics", None)
        return m is not None and getattr(m, "totalTokens", 0) > 0

    def _aiCollectExplainArgs(self):
        if not self._aiHasResult():
            return None
        return ("word_analysis", {"result": self._metrics})

    # ------------------------------------------------------------------
    # PRD-002 资源归档钩子(ResourceSinkMixin)
    # ------------------------------------------------------------------
    def _collectResourcePayload(self):
        """词语分析结果 → 项目资源 payload"""
        m: WordMetrics = getattr(self, "_metrics", None)
        if m is None or getattr(m, "totalTokens", 0) == 0:
            return None
        try:
            summary = (
                f"词语分析 {m.fileCount} 文件 / {m.totalTokens:,} tokens /"
                f" {m.totalTypes:,} types;"
                f" TTR={m.ttr:.3f},词汇密度={m.density:.2%},平均词长={m.avgLength:.2f}"
            )
        except Exception:
            summary = "词语分析结果"
        snapshotData = {
            "totalTokens": m.totalTokens,
            "totalTypes": m.totalTypes,
            "fileCount": m.fileCount,
            "contentWordCount": m.contentWordCount,
            "density": m.density,
            "avgLength": m.avgLength,
            "ttr": m.ttr,
            "guiraud": getattr(m, "guiraud", 0.0),
            "herdan": getattr(m, "herdan", 0.0),
            "uber": getattr(m, "uber", 0.0),
            "mtld": getattr(m, "mtld", 0.0),
        }
        parameters = {
            "fileCount": m.fileCount,
            "totalTokens": m.totalTokens,
            "totalTypes": m.totalTypes,
        }
        ts = self._buildDefaultTitle().split(" ", 1)[1]
        return {
            "title": f"词语分析 ({m.fileCount} 文件) ({ts})",
            "summary": summary[:200],
            "parameters": parameters,
            "snapshotData": snapshotData,
        }

    # ------------------------------------------------------------------
    # 结果渲染
    # ------------------------------------------------------------------
    def _renderResults(self, m: WordMetrics):
        # 顶部摘要卡
        self._summary.clear()
        self._summary.setMetrics(
            [
                ("总 Token", f"{m.totalTokens:,}", MetricColor.PRIMARY),
                ("Type 数", f"{m.totalTypes:,}", MetricColor.SUCCESS),
                ("TTR", f"{m.ttr:.4f}", MetricColor.ACCENT),
                ("词汇密度", f"{m.density:.4f}", MetricColor.WARNING),
            ]
        )
        self._summary.setDetail(
            f"📊 词语分析完成 &nbsp;|&nbsp; "
            f"文件数 <b>{m.fileCount}</b> &nbsp;|&nbsp; "
            f"平均词长 <b>{m.avgLength:.2f}</b> &nbsp;|&nbsp; "
            f"耗时 <b>{m.elapsedSeconds:.2f}s</b>"
        )

        # 词汇指标 tab
        self._metricsCards.clear()
        self._metricsCards.setMetrics(
            [
                ("词汇密度", f"{m.density:.4f}", MetricColor.PRIMARY),
                ("平均词长", f"{m.avgLength:.2f}", MetricColor.SUCCESS),
                ("TTR", f"{m.ttr:.4f}", MetricColor.ACCENT),
                ("MATTR", f"{m.mattr:.4f}", MetricColor.WARNING),
                ("MTLD", f"{m.mtld:.1f}", MetricColor.PRIMARY),
                ("Guiraud", f"{m.guiraud:.2f}", MetricColor.SUCCESS),
                ("Herdan", f"{m.herdAN:.4f}", MetricColor.ACCENT),
                ("Uber", f"{m.uber:.2f}", MetricColor.WARNING),
            ]
        )
        self._metricsCards.setDetail(
            f"实词数 <b>{m.contentWordCount:,}</b> · "
            f"Type/Token = {m.totalTypes:,}/{m.totalTokens:,} · "
            f"MATTR 窗口 = {m.mattrWindow} · MTLD 阈值 = {m.mtldThreshold}"
        )

        # 词汇增长曲线
        self._plotTypeTokenCurve(m)

        # 高频词 tab
        self._renderHighFreqTable(m)

        # 词汇分布 tab
        self._renderDistribution(m)

    def _plotTypeTokenCurve(self, m: WordMetrics):
        if self._ax is None:
            return
        self._ax.clear()
        if not m.typeTokenCurve:
            self._ax.text(
                0.5,
                0.5,
                "无数据",
                ha="center",
                va="center",
                transform=self._ax.transAxes,
                color="#999",
            )
        else:
            xs = [pt.tokenCount for pt in m.typeTokenCurve]
            ys = [pt.typeCount for pt in m.typeTokenCurve]
            self._ax.plot(xs, ys, color="#1890ff", linewidth=2)
            self._ax.fill_between(xs, ys, alpha=0.2, color="#1890ff")
            self._ax.set_xlabel("Tokens")
            self._ax.set_ylabel("Types")
            self._ax.grid(True, linestyle="--", alpha=0.5)
            self._ax.set_title(
                f"Type-Token 增长曲线 (最终: {m.totalTypes:,} types / "
                f"{m.totalTokens:,} tokens)",
                fontsize=11,
            )
        self._canvas.draw()

    def _renderHighFreqTable(self, m: WordMetrics):
        # 顶部覆盖率提示
        coverage_text = []
        if m.coverageAt50 > 0:
            coverage_text.append(f"50% 覆盖率: <b>{m.coverageAt50}</b> 个词")
        if m.coverageAt80 > 0:
            coverage_text.append(f"80% 覆盖率: <b>{m.coverageAt80}</b> 个词")
        if m.coverageAt90 > 0:
            coverage_text.append(f"90% 覆盖率: <b>{m.coverageAt90}</b> 个词")
        if coverage_text:
            self.coverageLabel.setText("&nbsp;|&nbsp; ".join(coverage_text))
            self.coverageLabel.setVisible(True)
        else:
            self.coverageLabel.setVisible(False)

        # 填充表格
        rows = m.highFreqWords
        self._highFreqTable.setRowCount(len(rows))
        for r, entry in enumerate(rows):
            rank_item = QTableWidgetItem(str(entry.rank))
            rank_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._highFreqTable.setItem(r, 0, rank_item)

            word_item = QTableWidgetItem(entry.word)
            self._highFreqTable.setItem(r, 1, word_item)

            freq_item = QTableWidgetItem(f"{entry.freq:,}")
            freq_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._highFreqTable.setItem(r, 2, freq_item)

            pct_item = QTableWidgetItem(f"{entry.freqPct * 100:.2f}%")
            pct_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._highFreqTable.setItem(r, 3, pct_item)

            cum_item = QTableWidgetItem(f"{entry.cumFreq:,}")
            cum_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._highFreqTable.setItem(r, 4, cum_item)

            cum_pct_item = QTableWidgetItem(f"{entry.cumPct * 100:.2f}%")
            cum_pct_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._highFreqTable.setItem(r, 5, cum_pct_item)

            # 覆盖率行高亮
            rank = entry.rank
            if (
                rank == m.coverageAt50
                or rank == m.coverageAt80
                or rank == m.coverageAt90
            ):
                for c in range(6):
                    item = self._highFreqTable.item(r, c)
                    if item:
                        item.setBackground(QColor("#fff7e6"))  # 浅橙

    def _renderDistribution(self, m: WordMetrics):
        dist = getattr(m, "distribution", {})
        if not dist:
            self.distHint.setText("无词汇分布数据")
            return

        words = list(dist.keys())
        fileNames = sorted({fn for d in dist.values() for fn in d.keys()})

        self._distTable.setColumnCount(1 + len(fileNames))
        headers = ["词语"] + [os.path.basename(fn)[:20] for fn in fileNames]
        self._distTable.setHorizontalHeaderLabels(headers)
        self._distTable.setRowCount(len(words))

        for r, word in enumerate(words):
            self._distTable.setItem(r, 0, QTableWidgetItem(word))
            for c, fn in enumerate(fileNames, start=1):
                cnt = dist[word].get(fn, 0)
                item = QTableWidgetItem(str(cnt))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._distTable.setItem(r, c, item)

        hHeader = self._distTable.horizontalHeader()
        hHeader.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for c in range(1, len(headers)):
            hHeader.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def _exportHighFreq(self):
        if self._metrics is None or not self._metrics.highFreqWords:
            _showInfoBar("warning", "无法导出", "请先运行分析", self)
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出高频词",
            "high_freq_words.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        transaction = beginPaidAnalysisExport(self, "高频词表 CSV")
        if transaction is None:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["排名", "词语", "频次", "频率%", "累计频次", "累计%"])
                for entry in self._metrics.highFreqWords:
                    w.writerow(
                        [
                            entry.rank,
                            entry.word,
                            entry.freq,
                            f"{entry.freqPct * 100:.4f}",
                            entry.cumFreq,
                            f"{entry.cumPct * 100:.4f}",
                        ]
                    )
            if not transaction.commit():
                raise RuntimeError("导出文件已生成，但计费结算失败；本次费用已释放")
            _showInfoBar("success", "导出成功", f"已保存到 {path}", self)
        except Exception as e:
            transaction.refund()
            logger.exception(f"[WordAnalysisWidget] 导出失败: {e}")
            _showInfoBar("error", "导出失败", str(e), self)
