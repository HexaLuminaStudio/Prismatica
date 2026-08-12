# coding: utf-8
"""
构式搭配强度分析面板

UI 风格与 collocation_widget.py / word_analysis_widget.py 等子页面保持一致:
    - 20px 外边距、12px 卡片内边距
    - SubtitleLabel 标题、CaptionLabel 灰色说明
    - Pivot 选项卡 + ScrollArea 包裹
    - CardWidget + StrongBodyLabel 分组
    - ResultSummary 4 列指标卡
    - 表格交替行 + 数值右对齐 + MI 强关联行高亮
    - 后台 QThread 异步分析,UI 不阻塞
    - CSV 导出按钮

子页面结构:
    [ 参数区 ]
        - POS Pattern 输入框(含示例与提示)
        - 左跨距 / 右跨距 SpinBox
        - 最低频次 / Top-N
        - MI 关联强度展示阈值
        - [开始分析] [取消] 按钮
    [ 结果摘要卡 ] 4 个指标(构式频次 / 强关联 slot 数 / 跨距搭配数 / 耗时)
    [ Pivot 选项卡 ]
        - Slot 填充词表(每个 slot 的高频词 + MI/LogDice/Z)
        - 内部 slot 对贴合度(slot_i vs slot_j 的联合 MI)
        - 跨距搭配词表(构式整体作为节点的共现词)
"""

from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal
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
    TableWidget,
)

from app.core.models.project import RESOURCE_TYPE_CONSTRUCTION
from app.view.widgets.freq_analyzer.construction_engine import (
    ConstructionEngine,
    ConstructionResult,
    ConstructionSlotEntry,
    CollocateEntry,
    InternalSlotPair,
)
from app.view.widgets.freq_analyzer.ai_insight_mixin import AiInsightMixin
from app.view.widgets.freq_analyzer.freq_engine import TextSegmenter
from app.view.widgets.freq_analyzer.resource_sink_mixin import ResourceSinkMixin
from app.view.widgets.freq_analyzer.token_cache import TokenCache
from app.view.widgets.freq_analyzer.ui_helpers import (
    _makeSwitchButton,
    _showInfoBar,
)
from app.view.widgets.prismatica_theme import setThemeRole

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from app.core.utils import logger
from app.core.services import beginPaidAnalysisExport


# ---------------------------------------------------------------------------
# 后台分析线程
# ---------------------------------------------------------------------------
class ConstructionWorker(QThread):
    """构式搭配强度分析后台线程"""

    progress = Signal(int, str)  # (percent, status)
    finished = Signal(object)  # ConstructionResult
    failed = Signal(str)  # 错误信息

    def __init__(
        self,
        corpusStore,
        segmenter: TextSegmenter,
        patternStr: str,
        leftSpan: int,
        rightSpan: int,
        minFreq: int,
        topN: int,
        slotMiThreshold: float,
    ):
        super().__init__()
        self._corpusStore = corpusStore
        self._segmenter = segmenter
        self._patternStr = patternStr
        self._leftSpan = leftSpan
        self._rightSpan = rightSpan
        self._minFreq = minFreq
        self._topN = topN
        self._slotMiThreshold = slotMiThreshold
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

            self.progress.emit(10, f"共 {n} 个文件,开始分词与词性标注...")

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
            allPosTags: List[str] = []

            from app.view.widgets.freq_analyzer.freq_engine import posTag

            for idx, name in enumerate(fileNames, start=1):
                if self._cancel:
                    return
                text = fileToText.get(name, "")
                if not text:
                    continue

                # 走缓存或直接分词
                tokens = self._segmentJieba(
                    text=text,
                    tokenCache=tokenCache,
                    modelVer=modelVer,
                )
                # POS 标注:对同一段文本用 jieba.posseg 再标注一次
                # (POS 标签未走缓存,因为文本量相对小且 jieba.posseg 较快)
                taggedPairs = posTag(text)
                posTags = [tag for (_, tag) in taggedPairs]
                if len(posTags) != len(tokens):
                    # 防御:长度不一致时取较短,避免后续越界
                    minLen = min(len(tokens), len(posTags))
                    tokens = tokens[:minLen]
                    posTags = posTags[:minLen]
                allTokens.extend(tokens)
                allPosTags.extend(posTags)

                pct = 10 + int(70 * idx / n)
                self.progress.emit(pct, f"分词 {idx}/{n}")

            if self._cancel:
                return

            self.progress.emit(85, f"正在分析构式「{self._patternStr}」...")

            engine = ConstructionEngine()
            result = engine.analyze(
                tokens=allTokens,
                posTags=allPosTags,
                patternStr=self._patternStr,
                leftSpan=self._leftSpan,
                rightSpan=self._rightSpan,
                minFreq=self._minFreq,
                topN=self._topN,
                slotMiThreshold=self._slotMiThreshold,
            )

            if self._cancel:
                return

            self.progress.emit(100, "完成!")
            self.finished.emit(result)

        except Exception as e:
            import traceback

            logger.exception(f"[ConstructionWorker] 失败: {e}")
            self.failed.emit(f"{e}\n{traceback.format_exc()}")

    def _segmentJieba(
        self,
        text: str,
        tokenCache: Optional[TokenCache],
        modelVer: str,
    ) -> List[str]:
        """对单段文本做 jieba 分词(走 cache 加速)"""
        if tokenCache is not None:
            tokens = tokenCache.getOrCompute(
                text=text,
                backendName="jieba",
                modelVersion=modelVer,
                computeFn=lambda t: self._segmenter.cutJieba(t),
            )
        else:
            tokens = self._segmenter.cutJieba(text)
        return tokens


# ---------------------------------------------------------------------------
# 主面板
# ---------------------------------------------------------------------------
class ConstructionWidget(AiInsightMixin, ResourceSinkMixin, QWidget):
    """构式搭配强度分析面板

    继承 AiInsightMixin 提供「AI 解读」抽屉能力
    继承 ResourceSinkMixin 提供分析结果自动归档到当前激活项目的能力
    """

    _AI_INSIGHT_PANEL_NAME = "构式分析"
    _AI_INSIGHT_TYPE = "construction"

    # PRD-002 资源归档配置(REQ-PROJ-001)
    _RESOURCE_TYPE = RESOURCE_TYPE_CONSTRUCTION
    _RESOURCE_TITLE_PREFIX = "构式分析"

    # POS Pattern 常用示例(下拉框可选)
    PATTERN_PRESETS: List[str] = [
        "<V> 都 <V> 了",
        "<V> <N> <V>",
        "<N> 的 <N>",
        "<V> 到 <N>",
        "<D> <A>",
        "<R> 是 <V>",
    ]

    def __init__(self, parent: Optional[QWidget] = None, corpusStore=None):
        super().__init__(parent=parent)
        self._corpusStore = corpusStore
        self._boundCorpusStore = None
        self._worker: Optional[ConstructionWorker] = None
        self._result: Optional[ConstructionResult] = None

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
            self._onCorpusChanged()
            return
        self._corpusStore = store
        tokenCache = store.tokenCache() if store is not None else None
        self._segmenter = TextSegmenter(tokenCache=tokenCache)
        self._bindCorpusStore(store)
        self._resetResultsForCorpusSwitch()
        self._updateCorpusInfo()

    def _resetResultsForCorpusSwitch(self) -> None:
        """切换语料库时清空所有分析结果与 UI(与 collocation_widget 一致)"""
        self._result = None
        for tbl in (
            getattr(self, "_slotTable", None),
            getattr(self, "_internalTable", None),
            getattr(self, "_collocatesTable", None),
        ):
            if tbl is not None:
                try:
                    tbl.setRowCount(0)
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
                from app.view.widgets.freq_analyzer.result_summary import (
                    MetricColor,
                    ResultSummary,
                )

                summary.clear()
                summary.setPlaceholder("已切换语料库,请重新分析")
                summary.setMetrics(
                    [
                        ("构式频次", "—", MetricColor.NEUTRAL),
                        ("强关联 slot 词", "—", MetricColor.NEUTRAL),
                        ("跨距搭配数", "—", MetricColor.NEUTRAL),
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
        outerLayout = QVBoxLayout(self)
        outerLayout.setContentsMargins(20, 20, 20, 20)
        outerLayout.setSpacing(12)

        # 页面标题
        titleLabel = SubtitleLabel("构式搭配强度", self)
        outerLayout.addWidget(titleLabel)

        # 顶部说明
        hint = CaptionLabel(
            "基于 POS Pattern 匹配多词构式节点(如 「V 都 V 了」「N 的 N」),"
            "计算每个 slot 的填充词强度(slot-level MI/LogDice/Z)、"
            "slot 之间的内部贴合度(internal collexeme analysis),"
            "以及构式整体作为节点的跨距搭配强度。"
            "参考 Stefanowitsch & Gries (2003) 的 collexeme / colostruct",
            self,
        )
        setThemeRole(hint, "muted", "font-size: 12px;")
        hint.setWordWrap(True)
        outerLayout.addWidget(hint)

        # 滚动区
        self._scrollArea = ScrollArea(self)
        self._scrollArea.setWidgetResizable(True)
        self._scrollArea.setStyleSheet("border: none; background: transparent;")
        outerLayout.addWidget(self._scrollArea, 1)

        # 滚动内容
        self._contentWidget = QWidget(self._scrollArea)
        self._contentWidget.setObjectName("constructionContent")
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
        self.tabBar.addItem(routeKey="tabSlot", text="Slot 填充词")
        self.tabBar.addItem(routeKey="tabInternal", text="内部 slot 贴合")
        self.tabBar.addItem(routeKey="tabColl", text="跨距搭配词")
        self.tabBar.setCurrentItem("tabSlot")
        root.addWidget(self.tabBar)

        # tab 内容容器
        self._tabContainer = QWidget(self._contentWidget)
        self._tabLayout = QVBoxLayout(self._tabContainer)
        self._tabLayout.setContentsMargins(0, 0, 0, 0)
        self._tabLayout.setSpacing(0)
        root.addWidget(self._tabContainer, 1)

        # 构建 tab
        self._buildSlotTab()
        self._buildInternalTab()
        self._buildCollTab()
        self._ensureTabsAdded()
        self._showTab("tabSlot")
        self.tabBar.currentItemChanged.connect(self._onTabItemChanged)

    def _buildParamCard(self) -> CardWidget:
        """参数卡片"""
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(StrongBodyLabel("分析参数", card))

        # 第 1 行:POS Pattern 输入 + 预设下拉
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        row1.addWidget(BodyLabel("POS 构式:", card))
        self.patternInput = LineEdit(card)
        self.patternInput.setPlaceholderText(
            "输入 POS Pattern,例如: <V> 都 <V> 了  /  <N> 的 <N>"
        )
        self.patternInput.setMinimumWidth(280)
        self.patternInput.setText(self.PATTERN_PRESETS[0])
        row1.addWidget(self.patternInput, 1)

        # 预设下拉
        self.presetCombo = ComboBox(card)
        for preset in self.PATTERN_PRESETS:
            self.presetCombo.addItem(preset)
        self.presetCombo.setCurrentIndex(0)
        self.presetCombo.setFixedWidth(200)
        self.presetCombo.currentTextChanged.connect(self._onPresetSelected)
        row1.addWidget(self.presetCombo)
        layout.addLayout(row1)

        # 语法说明
        patternHint = CaptionLabel(
            "语法说明: <V> 表示动词占位符,<N> 名词,<A> 形容词,<D> 副词,"
            "<R> 代词,<P> 介词/连词;可与字面词混合(如「都」「了」「的」);"
            "支持 * (任意 1 词) 与 *+ (任意 ≥0 词)。",
            card,
        )
        setThemeRole(patternHint, "muted", "font-size: 11px;")
        patternHint.setWordWrap(True)
        layout.addWidget(patternHint)

        # 第 2 行:跨距 / 最低频次 / Top-N
        row2 = QHBoxLayout()
        row2.setSpacing(16)
        row2.addWidget(BodyLabel("左跨距 L:", card))
        self.leftSpin = SpinBox(card)
        self.leftSpin.setRange(0, 30)
        self.leftSpin.setValue(3)
        row2.addWidget(self.leftSpin)

        row2.addWidget(BodyLabel("右跨距 R:", card))
        self.rightSpin = SpinBox(card)
        self.rightSpin.setRange(0, 30)
        self.rightSpin.setValue(3)
        row2.addWidget(self.rightSpin)

        row2.addWidget(BodyLabel("最低频次:", card))
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

        # 第 2.5 行:关联强度参数
        row2b = QHBoxLayout()
        row2b.setSpacing(16)
        row2b.addWidget(BodyLabel("Slot MI 强关联阈值:", card))
        self.slotMiSpin = DoubleSpinBox(card)
        self.slotMiSpin.setRange(0.0, 20.0)
        self.slotMiSpin.setDecimals(1)
        self.slotMiSpin.setSingleStep(0.5)
        self.slotMiSpin.setValue(3.0)
        row2b.addWidget(self.slotMiSpin)

        row2b.addStretch(1)
        layout.addLayout(row2b)

        # 第 3 行:操作按钮
        row3 = QHBoxLayout()
        row3.setSpacing(12)
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
        setThemeRole(self.statusLabel, "muted", "font-size: 11px;")
        layout.addWidget(self.statusLabel)

        return card

    def _buildSummaryPlaceholder(self) -> CardWidget:
        """占位的结果摘要卡"""
        from app.view.widgets.freq_analyzer.result_summary import (
            MetricColor,
            ResultSummary,
        )

        summary = ResultSummary(self)
        summary.setPlaceholder("请输入 POS 构式并点击「开始分析」")
        summary.setMetrics(
            [
                ("构式频次", "—", MetricColor.PRIMARY),
                ("强关联 slot 词", "—", MetricColor.SUCCESS),
                ("跨距搭配数", "—", MetricColor.ACCENT),
                ("耗时", "—", MetricColor.NEUTRAL),
            ]
        )
        return summary

    def _onPresetSelected(self, text: str):
        """预设下拉变化时,同步到输入框"""
        if text:
            self.patternInput.setText(text)

    # ============= Slot 填充词 tab =============
    def _buildSlotTab(self):
        self._slotTab = QWidget(self)
        layout = QVBoxLayout(self._slotTab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        self._slotHint = CaptionLabel(
            "展示构式每个 slot (PLACEHOLDER 位置) 的高频填充词及其强度指标;"
            "MI ≥ 设定阈值的行以浅橙色高亮。可视为 collexeme 表。",
            self._slotTab,
        )
        setThemeRole(self._slotHint, "muted", "font-size: 11px;")
        layout.addWidget(self._slotHint)

        self._slotTable = TableWidget(self._slotTab)
        self._slotTable.setColumnCount(8)
        self._slotTable.setHorizontalHeaderLabels(
            [
                "Slot",
                "词性",
                "填充词",
                "频次",
                "P(w|slot)",
                "MI",
                "LogDice",
                "Z-score",
            ]
        )
        self._slotTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._slotTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._slotTable.setAlternatingRowColors(True)
        self._slotTable.verticalHeader().setVisible(False)
        hHeader = self._slotTable.horizontalHeader()
        hHeader.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hHeader.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hHeader.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for c in range(3, 8):
            hHeader.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._slotTable, 1)

        # 导出
        actionRow = QHBoxLayout()
        exportBtn = PushButton("导出 Slot 表 CSV", self._slotTab)
        exportBtn.setIcon(FIF.SAVE)
        exportBtn.clicked.connect(self._exportSlotCsv)
        actionRow.addWidget(exportBtn)
        actionRow.addStretch(1)
        layout.addLayout(actionRow)

    # ============= 内部 slot 对贴合 tab =============
    def _buildInternalTab(self):
        self._internalTab = QWidget(self)
        layout = QVBoxLayout(self._internalTab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        self._internalHint = CaptionLabel(
            "展示构式内部两个 slot 之间共同出现的具体词对的强度指标;"
            "MI ≥ 设定阈值的行以浅橙色高亮。对应 collexeme pair 分析。",
            self._internalTab,
        )
        setThemeRole(self._internalHint, "muted", "font-size: 11px;")
        layout.addWidget(self._internalHint)

        self._internalTable = TableWidget(self._internalTab)
        self._internalTable.setColumnCount(7)
        self._internalTable.setHorizontalHeaderLabels(
            [
                "Slot A",
                "Slot B",
                "词 A",
                "词 B",
                "共现频次",
                "MI",
                "LogDice",
            ]
        )
        self._internalTable.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._internalTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._internalTable.setAlternatingRowColors(True)
        self._internalTable.verticalHeader().setVisible(False)
        hHeader = self._internalTable.horizontalHeader()
        hHeader.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hHeader.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hHeader.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hHeader.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for c in range(4, 7):
            hHeader.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._internalTable, 1)

        actionRow = QHBoxLayout()
        exportBtn = PushButton("导出内部贴合表 CSV", self._internalTab)
        exportBtn.setIcon(FIF.SAVE)
        exportBtn.clicked.connect(self._exportInternalCsv)
        actionRow.addWidget(exportBtn)
        actionRow.addStretch(1)
        layout.addLayout(actionRow)

    # ============= 跨距搭配词 tab =============
    def _buildCollTab(self):
        self._collocatesTab = QWidget(self)
        layout = QVBoxLayout(self._collocatesTab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        self._collHint = CaptionLabel(
            "把构式整体作为节点,统计其左右跨距内高频搭配词及其强度。"
            "MI ≥ 设定阈值的搭配词以浅橙色高亮。",
            self._collocatesTab,
        )
        setThemeRole(self._collHint, "muted", "font-size: 11px;")
        layout.addWidget(self._collHint)

        self._collocatesTable = TableWidget(self._collocatesTab)
        self._collocatesTable.setColumnCount(9)
        self._collocatesTable.setHorizontalHeaderLabels(
            [
                "搭配词",
                "词性",
                "共现 O",
                "上下文机会 C",
                "MI",
                "LogDice",
                "T-score",
                "Z-score",
                "ΔP",
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
        hHeader.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for c in range(2, 9):
            hHeader.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._collocatesTable, 1)

        actionRow = QHBoxLayout()
        exportBtn = PushButton("导出搭配词表 CSV", self._collocatesTab)
        exportBtn.setIcon(FIF.SAVE)
        exportBtn.clicked.connect(self._exportCollCsv)
        actionRow.addWidget(exportBtn)
        actionRow.addStretch(1)
        layout.addLayout(actionRow)

    # ------------------------------------------------------------------
    # Tab 切换
    # ------------------------------------------------------------------
    def _showTab(self, key: str) -> None:
        for w in (self._slotTab, self._internalTab, self._collocatesTab):
            w.hide()
        mapping = {
            "tabSlot": self._slotTab,
            "tabInternal": self._internalTab,
            "tabColl": self._collocatesTab,
        }
        target = mapping.get(key)
        if target:
            target.show()

    def _ensureTabsAdded(self) -> None:
        if getattr(self, "_tabsAdded", False):
            return
        for w in (self._slotTab, self._internalTab, self._collocatesTab):
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

        patternStr = self.patternInput.text().strip()
        if not patternStr:
            _showInfoBar("warning", "无法分析", "请输入 POS 构式模式", self)
            return

        self.runBtn.setEnabled(False)
        self.statusLabel.setText("正在分析...")

        self._worker = ConstructionWorker(
            corpusStore=self._corpusStore,
            segmenter=self._segmenter,
            patternStr=patternStr,
            leftSpan=self.leftSpin.value(),
            rightSpan=self.rightSpin.value(),
            minFreq=self.minFreqSpin.value(),
            topN=self.topNSpin.value(),
            slotMiThreshold=self.slotMiSpin.value(),
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

    def _onFinished(self, result: ConstructionResult):
        self._result = result
        self._resetUi()
        self._renderResults(result)
        # AI 解读:有结果后启用按钮
        self.refreshAiInsightButton()
        # PRD-002:归档到当前激活项目(若有)
        if result.matchCount > 0:
            self.notifyResourceCreated()
        if result.matchCount == 0:
            _showInfoBar(
                "warning",
                "无匹配",
                f"构式「{result.patternRaw}」未在语料中匹配到任何区间,"
                f"请检查模式或换用更宽松的占位符",
                self,
                duration=4000,
            )
        else:
            _showInfoBar(
                "success",
                "分析完成",
                f"构式「{result.patternRaw}」匹配 {result.matchCount} 次,"
                f"耗时 {result.elapsedSeconds:.2f}s",
                self,
                duration=2500,
            )

    def _resetUi(self):
        self.runBtn.setEnabled(True)

    # ------------------------------------------------------------------
    # 结果渲染
    # ------------------------------------------------------------------
    # AI 解读协议（PRD-001 REQ-AI-001）— 由 AiInsightMixin 调用
    # ------------------------------------------------------------------
    def _aiHasResult(self) -> bool:
        r = getattr(self, "_result", None)
        return r is not None and (
            bool(getattr(r, "slotEntries", [])) or bool(getattr(r, "collocates", []))
        )

    def _aiCollectExplainArgs(self):
        if not self._aiHasResult():
            return None
        return ("construction", {"result": self._result})

    # ------------------------------------------------------------------
    # PRD-002 资源归档钩子(ResourceSinkMixin)
    # ------------------------------------------------------------------
    def _collectResourcePayload(self):
        """构式分析结果 → 项目资源 payload"""
        r = getattr(self, "_result", None)
        if r is None or getattr(r, "matchCount", 0) == 0:
            return None
        try:
            strongSlotCount = sum(1 for e in r.slotEntries if e.meetsMiThreshold)
            topSlots = r.slotEntries[:5]
            topText = "、".join(f"{e.slotLabel}={e.word}({e.mi:.1f})" for e in topSlots)
            summary = (
                f"构式「{r.patternRaw}」匹配 {r.matchCount} 次,"
                f"{strongSlotCount} 个 slot 词达到 MI 强关联阈值,"
                f"{len(r.collocates)} 个跨距搭配。"
                f"Top slot:{topText}"
            )
        except Exception:
            summary = f"构式「{r.patternRaw}」"
        try:
            topSlots = [
                {
                    "slotLabel": e.slotLabel,
                    "posTag": e.posTag,
                    "word": e.word,
                    "freq": e.freq,
                    "mi": e.mi,
                    "logDice": e.logDice,
                }
                for e in r.slotEntries[:200]
            ]
        except Exception:
            topSlots = []
        snapshotData = {
            "patternRaw": r.patternRaw,
            "matchCount": r.matchCount,
            "constructionFreq": r.constructionFreq,
            "overallInferenceAvailable": r.overallInferenceAvailable,
            "overallInferenceNote": r.overallInferenceNote,
            "topSlotEntries": topSlots,
            "internalPairsCount": len(r.internalPairs),
            "collocatesCount": len(r.collocates),
        }
        parameters = {
            "pattern": r.patternRaw,
            "leftSpan": r.leftSpan,
            "rightSpan": r.rightSpan,
            "slotMiThreshold": r.slotMiThreshold,
        }
        ts = self._buildDefaultTitle().split(" ", 1)[1]
        return {
            "title": f"构式「{r.patternRaw}」({ts})",
            "summary": summary[:200],
            "parameters": parameters,
            "snapshotData": snapshotData,
        }

    # ------------------------------------------------------------------
    def _renderResults(self, r: ConstructionResult):
        # 顶部摘要
        strongSlotCount = sum(1 for e in r.slotEntries if e.meetsMiThreshold)
        from app.view.widgets.freq_analyzer.result_summary import (
            MetricColor,
            ResultSummary,
        )

        self._summary.clear()
        if not isinstance(self._summary, ResultSummary):
            self._summary.setMetrics(
                [
                    ("构式频次", f"{r.constructionFreq:,}", MetricColor.PRIMARY),
                    (
                        f"强关联 slot 词(MI≥{r.slotMiThreshold:.1f})",
                        f"{strongSlotCount}",
                        MetricColor.SUCCESS,
                    ),
                    ("跨距搭配数", f"{len(r.collocates)}", MetricColor.ACCENT),
                    ("耗时", f"{r.elapsedSeconds:.2f}s", MetricColor.NEUTRAL),
                ]
            )
        else:
            self._summary.setMetrics(
                [
                    ("构式频次", f"{r.constructionFreq:,}", MetricColor.PRIMARY),
                    (
                        f"强关联 slot 词(MI≥{r.slotMiThreshold:.1f})",
                        f"{strongSlotCount}",
                        MetricColor.SUCCESS,
                    ),
                    ("跨距搭配数", f"{len(r.collocates)}", MetricColor.ACCENT),
                    ("耗时", f"{r.elapsedSeconds:.2f}s", MetricColor.NEUTRAL),
                ]
            )
        self._summary.setDetail(
            f"📐 构式 <b>{r.patternRaw}</b> &nbsp;|&nbsp; "
            f"匹配 <b>{r.matchCount:,}</b> 次 &nbsp;|&nbsp; "
            f"语料 <b>{r.totalTokens:,}</b> tokens / <b>{r.uniqueTypes:,}</b> types"
            f" &nbsp;|&nbsp; 整体推断:未计算(缺少独立基线)"
            f" &nbsp;|&nbsp; 跨距 L{r.leftSpan}-R{r.rightSpan}"
        )

        # 渲染各表格
        self._renderSlotTable(r)
        self._renderInternalTable(r)
        self._renderCollTable(r)

    def _renderSlotTable(self, r: ConstructionResult):
        rows = r.slotEntries
        self._slotTable.setRowCount(len(rows))
        import math as _math

        for idx, entry in enumerate(rows):
            # Slot
            slotItem = QTableWidgetItem(entry.slotLabel)
            self._slotTable.setItem(idx, 0, slotItem)
            # 词性
            posItem = QTableWidgetItem(entry.posTag)
            posItem.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            )
            self._slotTable.setItem(idx, 1, posItem)
            # 填充词
            wordItem = QTableWidgetItem(entry.word)
            self._slotTable.setItem(idx, 2, wordItem)
            # 频次
            for col, val, fmt in [
                (3, entry.freq, "{:,}"),
                (4, entry.prob, "{:.4f}"),
                (5, entry.mi, "{:.4f}"),
                (6, entry.logDice, "{:.4f}"),
                (7, entry.zScore, "{:.4f}"),
            ]:
                text = fmt.format(val) if val is not None else "—"
                if isinstance(val, float) and _math.isinf(val):
                    text = "+∞" if val > 0 else "-∞"
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self._slotTable.setItem(idx, col, item)

            # MI 关联强度达到展示阈值时高亮
            if entry.meetsMiThreshold:
                for c in range(8):
                    cell = self._slotTable.item(idx, c)
                    if cell is not None:
                        cell.setBackground(Qt.GlobalColor.lightGray)

    def _renderInternalTable(self, r: ConstructionResult):
        rows = r.internalPairs
        self._internalTable.setRowCount(len(rows))
        import math as _math

        for idx, entry in enumerate(rows):
            self._internalTable.setItem(idx, 0, QTableWidgetItem(entry.labelA))
            self._internalTable.setItem(idx, 1, QTableWidgetItem(entry.labelB))
            self._internalTable.setItem(idx, 2, QTableWidgetItem(""))
            self._internalTable.setItem(idx, 3, QTableWidgetItem(""))
            # 频次
            freqItem = QTableWidgetItem(f"{entry.pairFreq:,}")
            freqItem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._internalTable.setItem(idx, 4, freqItem)
            for col, val in [(5, entry.mi), (6, entry.logDice)]:
                if val is None:
                    text = "—"
                elif _math.isinf(val):
                    text = "+∞" if val > 0 else "-∞"
                else:
                    text = f"{val:.4f}"
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self._internalTable.setItem(idx, col, item)

            # MI 关联强度达到展示阈值时高亮
            if entry.meetsMiThreshold:
                for c in range(7):
                    cell = self._internalTable.item(idx, c)
                    if cell is not None:
                        cell.setBackground(Qt.GlobalColor.lightGray)

    def _renderCollTable(self, r: ConstructionResult):
        rows = r.collocates
        self._collocatesTable.setRowCount(len(rows))
        import math as _math

        for idx, entry in enumerate(rows):
            # 搭配词
            self._collocatesTable.setItem(idx, 0, QTableWidgetItem(entry.collocate))
            # 词性
            posItem = QTableWidgetItem(entry.posTag)
            posItem.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            )
            self._collocatesTable.setItem(idx, 1, posItem)
            # O
            freqItem = QTableWidgetItem(f"{entry.freq:,}")
            freqItem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._collocatesTable.setItem(idx, 2, freqItem)
            # C
            cItem = QTableWidgetItem(f"{entry.collocateFreq:,}")
            cItem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._collocatesTable.setItem(idx, 3, cItem)
            # 各统计量
            stats = [
                (4, entry.mi),
                (5, entry.logDice),
                (6, entry.tScore),
                (7, entry.zScore),
                (8, entry.deltaP),
            ]
            for col, val in stats:
                if val is None:
                    text = "—"
                elif _math.isinf(val):
                    text = "+∞" if val > 0 else "-∞"
                else:
                    text = f"{val:.4f}"
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self._collocatesTable.setItem(idx, col, item)

            # MI 关联强度达到展示阈值时高亮
            if entry.meetsMiThreshold:
                for c in range(9):
                    cell = self._collocatesTable.item(idx, c)
                    if cell is not None:
                        cell.setBackground(Qt.GlobalColor.lightGray)

    # ------------------------------------------------------------------
    # CSV 导出
    # ------------------------------------------------------------------
    def _exportSlotCsv(self):
        if not self._result or not self._result.slotEntries:
            _showInfoBar("warning", "无数据", "没有可导出的 slot 表", self)
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Slot 填充词表",
            f"construction_slot_{self._result.patternRaw.replace('<', '').replace('>', '').replace(' ', '_')}.csv",
            "CSV files (*.csv)",
        )
        if not path:
            return
        charge = beginPaidAnalysisExport(self.window(), "导出构式 Slot 填充词表")
        if charge is None:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fp:
                writer = csv.writer(fp)
                writer.writerow(
                    [
                        "Slot",
                        "POS",
                        "Word",
                        "Freq",
                        "WordCorpusFreq",
                        "P(w|slot)",
                        "MI",
                        "LogDice",
                        "Z-score",
                        "MeetsMiThreshold",
                    ]
                )
                for e in self._result.slotEntries:
                    writer.writerow(
                        [
                            e.slotLabel,
                            e.posTag,
                            e.word,
                            e.freq,
                            e.wordFreqInCorpus,
                            f"{e.prob:.6f}",
                            f"{e.mi:.4f}",
                            f"{e.logDice:.4f}",
                            f"{e.zScore:.4f}",
                            e.meetsMiThreshold,
                        ]
                    )
            if charge.commit():
                _showInfoBar("success", "导出成功", path, self)
        except Exception as e:
            charge.refund()
            logger.exception(f"[ConstructionWidget] 导出 Slot 表失败: {e}")
            _showInfoBar("error", "导出失败", str(e)[:80], self)

    def _exportInternalCsv(self):
        if not self._result or not self._result.internalPairs:
            _showInfoBar("warning", "无数据", "没有可导出的内部 slot 对表", self)
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出内部 slot 对贴合表",
            "construction_internal.csv",
            "CSV files (*.csv)",
        )
        if not path:
            return
        charge = beginPaidAnalysisExport(self.window(), "导出构式内部贴合表")
        if charge is None:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fp:
                writer = csv.writer(fp)
                writer.writerow(
                    [
                        "SlotA",
                        "SlotB",
                        "WordA",
                        "WordB",
                        "PairFreq",
                        "Expected",
                        "MI",
                        "LogDice",
                        "Z-score",
                        "MeetsMiThreshold",
                    ]
                )
                for e in self._result.internalPairs:
                    writer.writerow(
                        [
                            e.labelA,
                            e.labelB,
                            "",
                            "",
                            e.pairFreq,
                            f"{e.expectedFreq:.4f}",
                            f"{e.mi:.4f}",
                            f"{e.logDice:.4f}",
                            f"{e.zScore:.4f}",
                            e.meetsMiThreshold,
                        ]
                    )
            if charge.commit():
                _showInfoBar("success", "导出成功", path, self)
        except Exception as e:
            charge.refund()
            logger.exception(f"[ConstructionWidget] 导出内部表失败: {e}")
            _showInfoBar("error", "导出失败", str(e)[:80], self)

    def _exportCollCsv(self):
        if not self._result or not self._result.collocates:
            _showInfoBar("warning", "无数据", "没有可导出的跨距搭配表", self)
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出跨距搭配词表",
            "construction_collocates.csv",
            "CSV files (*.csv)",
        )
        if not path:
            return
        charge = beginPaidAnalysisExport(self.window(), "导出构式跨距搭配表")
        if charge is None:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fp:
                writer = csv.writer(fp)
                writer.writerow(
                    [
                        "Collocate",
                        "POS",
                        "CooccurFreq_O",
                        "CollocateFreq_C",
                        "Expected_E",
                        "MI",
                        "LogDice",
                        "T-score",
                        "Z-score",
                        "DeltaP1",
                        "MeetsMiThreshold",
                    ]
                )
                for e in self._result.collocates:
                    writer.writerow(
                        [
                            e.collocate,
                            e.posTag,
                            e.freq,
                            e.collocateFreq,
                            f"{e.expectedFreq:.4f}",
                            f"{e.mi:.4f}",
                            f"{e.logDice:.4f}",
                            f"{e.tScore:.4f}",
                            f"{e.zScore:.4f}",
                            f"{e.deltaP:.4f}",
                            e.meetsMiThreshold,
                        ]
                    )
            if charge.commit():
                _showInfoBar("success", "导出成功", path, self)
        except Exception as e:
            charge.refund()
            logger.exception(f"[ConstructionWidget] 导出搭配表失败: {e}")
            _showInfoBar("error", "导出失败", str(e)[:80], self)
