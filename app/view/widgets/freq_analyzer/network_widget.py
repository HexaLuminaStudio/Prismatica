# coding: utf-8
"""
词语共现网络图 UI 主面板

需求文档:
    test/6D-CorpusClient_需求文档_v3.md §2.5.2

功能覆盖:
    - FR-CON-001 滑动窗口共现矩阵构建(±N 词)
    - FR-CON-002 力导向布局渲染(Fruchterman-Reingold / spring_layout)
    - FR-CON-003 交互:Matplotlib NavigationToolbar(平移/缩放/主页/保存)
                     + 悬停提示节点信息与连接数
    - FR-CON-004 节点大小映射词频,边粗细映射共现频次
    - FR-CON-005 社区发现着色(greedy modularity)
    - FR-CON-006 筛选:最低词频 / 最低共现频次 / Top-K / 关键词
    - FR-CON-007 导出:PNG / SVG / GEXF / GraphML

设计要点:
    - 复用 CorpusStore / FreqAnalyzerWidget / ConcordanceWidget 的语料通道
    - 共现计算在后台线程执行(NetworkBuildWorker),完成后切回 UI 线程绘图
    - Matplotlib 后端使用 QtAgg,通过 FigureCanvasQTAgg 嵌入 Qt
"""

from __future__ import annotations

import math
import os
import traceback
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
import numpy as np
from app.core.services import beginPaidAnalysisExport, stopwordService

# matplotlib 后端必须在 from matplotlib import pyplot 之前显式指定
# P0-A3 fix 2026-07-18:严格 import 顺序 + force=True
# 错误顺序:先 import matplotlib.font_manager / matplotlib.pyplot 会触发
#           matplotlib 自动选择后端,此时再调 matplotlib.use() 会被默认行为覆盖。
# 正确顺序:import matplotlib → matplotlib.use(...) → from matplotlib import pyplot

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
)

# 必须在 import matplotlib.figure 之前显式指定后端为 QtAgg,
# 否则在未创建 QApplication 之前 import 会触发 "Turning interactive mode on"
# 警告,或在某些环境下选择错误后端导致程序启动失败。
import matplotlib  # noqa: E402

# P0-A3 fix 2026-07-18:force=True 强制覆盖已锁定的默认后端
matplotlib.use("QtAgg", force=True)

# 延迟到模块下方 _availableCjkFonts() 定义后再设置 rcParams,
# 确保 matplotlib font_manager 已就绪。这里先 import。
import matplotlib.font_manager as fm  # noqa: E402,F401
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.backends.backend_qtagg import (  # noqa: E402
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar,
)

from app.view.widgets.freq_analyzer.network_engine import (
    CooccurrenceEngine,
    CooccurrenceNetwork,
    NetworkBuildParams,
    colorForCommunity,
)
from app.view.widgets.freq_analyzer.ui_helpers import _showInfoBar

from app.core.utils import cfg, qconfig  # AI 解读配置（PRD-001 REQ-AI-001）

# AI 解读 Mixin（PRD-001 REQ-AI-001）
from app.view.widgets.freq_analyzer.ai_insight_mixin import AiInsightMixin
from app.view.widgets.freq_analyzer.resource_sink_mixin import ResourceSinkMixin
from app.view.widgets.prismatica_theme import setThemeRole, shellPalette
from app.core.models.project import RESOURCE_TYPE_NETWORK

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from app.core.utils import logger


# ---------------------------------------------------------------------------
# 字体辅助:动态检测系统中可用的 CJK 字体,避免 matplotlib 报
# "findfont: Font family 'X' not found" 警告
# ---------------------------------------------------------------------------
_CJK_FONT_CANDIDATES: Tuple[str, ...] = (
    "Microsoft YaHei",
    "Microsoft YaHei UI",
    "SimHei",
    "SimSun",
    "Yu Gothic",
    "Malgun Gothic",
    "Noto Sans CJK SC",
    "Source Han Sans CN",
    "PingFang SC",
    "Heiti SC",
    "WenQuanYi Micro Hei",
    "Arial Unicode MS",
    "Noto Sans",
)


def _availableCjkFonts() -> List[str]:
    """返回当前系统中实际存在的 CJK 字体列表(按优先级排序)

    实现细节:
        - 从 matplotlib font_manager 读取全部已注册字体名
        - 过滤 _CJK_FONT_CANDIDATES,只保留真实可用的字体
        - 不再回退到 DejaVu Sans——它是 matplotlib 默认 Latin-only 字体,
          缺少 CJK 字形,在词云/网络图中显示中文时会变成「豆腐块」(□)

    Returns:
        系统中可用的 CJK 字体名列表;若无任何候选字体,返回空列表,
        由调用方根据场景选择 fallback(网络图走 matplotlib 默认 sans-serif,
        词云走 wordcloud 内置字体)。
    """
    try:
        installed = {f.name for f in fm.fontManager.ttflist}
    except Exception:
        return []
    result: List[str] = []
    for name in _CJK_FONT_CANDIDATES:
        if name in installed:
            result.append(name)
    if not result:
        logger.warning(
            "[NetworkWidget] 系统未安装任何已知的 CJK 字体,"
            "网络图中的中文节点标签可能无法正常显示"
        )
    return result


# 初始化时同步一次 rcParams,过滤掉系统中不存在的字体,
# 避免后续 plt.title/plt.text 等任何调用触发 findfont 警告
# P0-A3 fix 2026-07-18:plt 已在模块上方 import,直接复用
plt.rcParams["font.sans-serif"] = _availableCjkFonts()
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["axes.unicode_minus"] = False


# ===========================================================================
# 后台构建线程
# ===========================================================================
class NetworkBuildWorker(QThread):
    """后台共现网络构建线程

    Signals:
        progress(str)         阶段提示
        finished(CooccurrenceNetwork)
        failed(str)           错误信息
    """

    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        engine: CooccurrenceEngine,
        fileToText: Dict[str, str],
        params: NetworkBuildParams,
        parent=None,
    ):
        super().__init__(parent)
        self._engine = engine
        self._fileToText = fileToText
        self._params = params

    def cancel(self) -> None:
        """请求取消任务(由 UI 线程调用)"""
        self.requestInterruption()

    def run(self):
        try:
            # P1-3 修复:把 progress 通过回调转发到 signal,worker 线程 → UI 线程
            def _onProgress(stageMsg: str):
                if self.isInterruptionRequested():
                    return
                self.progress.emit(stageMsg)

            if self.isInterruptionRequested():
                return
            network = self._engine.build(
                self._fileToText, self._params, progressCallback=_onProgress
            )
            if self.isInterruptionRequested():
                return
            self.progress.emit(
                f"构建完成: 节点={network.nodeCount} 边={network.edgeCount}"
            )
            self.finished.emit(network)
        except Exception as e:
            logger.exception("[NetworkBuildWorker] 构建异常")
            self.failed.emit(f"{e}\n{traceback.format_exc()}")


# ===========================================================================
# 节点悬停气泡(FR-CON-003)
# ===========================================================================
class HoverAnnotation:
    """Matplotlib 注释对象,用于悬停时显示节点信息"""

    def __init__(self, ax):
        self.ax = ax
        self.annotation = ax.annotate(
            "",
            xy=(0, 0),
            xytext=(15, 15),
            textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.5", fc="#ffffe0", ec="#999", alpha=0.95),
                    arrowprops=dict(
                        arrowstyle="->",
                        color=shellPalette().mutedText.name(),
                    ),
            fontsize=10,
            zorder=10,
        )
        self.annotation.set_visible(False)

    def update(self, x: float, y: float, text: str) -> None:
        self.annotation.xy = (x, y)
        self.annotation.set_text(text)
        self.annotation.set_visible(True)
        self.ax.figure.canvas.draw_idle()

    def hide(self) -> None:
        if self.annotation.get_visible():
            self.annotation.set_visible(False)
            self.ax.figure.canvas.draw_idle()


# ===========================================================================
# 主面板
# ===========================================================================
class NetworkWidget(AiInsightMixin, ResourceSinkMixin, QWidget):
    # PRD-002 资源归档配置(REQ-PROJ-001)
    _RESOURCE_TYPE = RESOURCE_TYPE_NETWORK
    _RESOURCE_TITLE_PREFIX = "共现网络"
    """词语共现网络图主面板

    用法:
        - 与 FreqAnalyzerWidget / ConcordanceWidget 共用 CorpusStore
        - 顶层 FreqAnalyzerInterface 负责将其加入 segmented 面板
        - 继承 AiInsightMixin 提供「AI 解读」抽屉能力
    """

    _AI_INSIGHT_PANEL_NAME = "共现网络"
    _AI_INSIGHT_TYPE = "network"

    def __init__(self, parent=None, corpusStore=None):
        super().__init__(parent)
        self.setObjectName("networkWidget")

        self._corpusStore = corpusStore
        self._boundCorpusStore = None
        self.fileToText: Dict[str, str] = {}
        self._network: Optional[CooccurrenceNetwork] = None
        self._pos: Dict[str, Tuple[float, float]] = {}
        self._worker: Optional[NetworkBuildWorker] = None
        self._nodeScatter = None  # 当前 scatter 对象(用于悬停事件)
        # _hover/_figure/_ax/_canvas 都在 _buildChartCard 中创建,
        # 因为 Figure 的创建依赖 QtAgg 后端,而后端在 QApplication 之后才稳定。
        self._hover: Optional[HoverAnnotation] = None
        self._figure: Optional[Figure] = None
        self._ax = None
        self._canvas: Optional[FigureCanvas] = None

        # 注入 token cache(加速重复分词)
        tokenCache = (
            self._corpusStore.tokenCache() if self._corpusStore is not None else None
        )
        self._engine = CooccurrenceEngine(
            useJieba=True, caseSensitive=False, tokenCache=tokenCache
        )
        self._initUi()

        if self._corpusStore is not None:
            self._bindCorpusStore(self._corpusStore)

    # ------------------------------------------------------------------
    # 语料状态绑定
    # ------------------------------------------------------------------
    def setCorpusStore(self, store) -> None:
        if self._corpusStore is store:
            self._onCorpusChanged()
            return
        self._corpusStore = store
        self._bindCorpusStore(store)
        self._onCorpusChanged()

    def _bindCorpusStore(self, store) -> None:
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

    def _onCorpusChanged(self) -> None:
        self.fileToText = {}
        # 语料变更 → 清空旧图
        self._network = None
        self._pos = {}
        if hasattr(self, "_resultSummary"):
            self._resultSummary.setPlaceholder("语料已变更,请点击「构建网络」")
        self._drawPlaceholder("请配置参数后点击「构建网络」")

    def _reloadEffectiveTexts(self) -> bool:
        if self._corpusStore is None:
            return True
        try:
            coverage = self._corpusStore.cacheCoverage()
            if self._corpusStore.cleanEnabled and coverage["coverage"] < 1.0:
                _showInfoBar(
                    "info", "语料准备中", "清洗缓存完成后即可构建网络", self
                )
                return False
            self.fileToText = self._corpusStore.effectiveTextsFromCacheOnly()
            return True
        except Exception as exc:
            logger.exception(f"[NetworkWidget] 读取语料失败: {exc}")
            _showInfoBar("error", "读取失败", str(exc), self)
            return False

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _initUi(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        # 标题
        title = SubtitleLabel("词语共现网络图", self)
        outer.addWidget(title)

        # 滚动容器
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")
        outer.addWidget(scroll, 1)

        content = QWidget()
        scroll.setWidget(content)
        contentLayout = QVBoxLayout(content)
        contentLayout.setContentsMargins(0, 0, 0, 0)
        contentLayout.setSpacing(12)

        # 语料状态卡(只读)已移除

        # 参数卡片
        contentLayout.addWidget(self._buildParamCard())

        # 图表卡片
        contentLayout.addWidget(self._buildChartCard(), 1)

    def _buildParamCard(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(StrongBodyLabel("构建参数", card))

        # 行 1:窗口 + 最小词频 + 最小共现 + Top-K
        row1 = QHBoxLayout()
        row1.addWidget(BodyLabel("窗口 ±N 词:", card))
        self.windowSpin = SpinBox(card)
        self.windowSpin.setRange(1, 30)
        self.windowSpin.setValue(5)
        row1.addWidget(self.windowSpin)

        row1.addSpacing(12)
        row1.addWidget(BodyLabel("最小词频:", card))
        self.minFreqSpin = SpinBox(card)
        self.minFreqSpin.setRange(1, 100)
        self.minFreqSpin.setValue(2)
        row1.addWidget(self.minFreqSpin)

        row1.addSpacing(12)
        row1.addWidget(BodyLabel("最小共现:", card))
        self.minCoSpin = SpinBox(card)
        self.minCoSpin.setRange(1, 100)
        self.minCoSpin.setValue(2)
        row1.addWidget(self.minCoSpin)

        row1.addSpacing(12)
        row1.addWidget(BodyLabel("Top K 节点:", card))
        self.topKSpin = SpinBox(card)
        self.topKSpin.setRange(5, 500)
        self.topKSpin.setValue(80)
        row1.addWidget(self.topKSpin)

        layout.addLayout(row1)

        # 行 2:统一过滤模式(FR-CON-010 P0-fix 2026-07-20)
        # 合并旧版「关键词过滤」与「词性组合」,用户可在同一输入框中表达复合筛选
        row2 = QHBoxLayout()
        row2.addWidget(BodyLabel("过滤模式:", card))
        self.filterExprEdit = LineEdit(card)
        self.filterExprEdit.setPlaceholderText(
            "留空 = 全量。示例:「学习」/「V 都 V 了」/「学习:V 都 V 了」/「学习,工作」"
        )
        row2.addWidget(self.filterExprEdit, 1)

        self.communityCheck = _makeSwitch("社区发现着色", card)
        self.communityCheck.setChecked(True)
        row2.addWidget(self.communityCheck)

        self.caseCheck = CheckBox("区分大小写", card)
        row2.addWidget(self.caseCheck)

        layout.addLayout(row2)

        # 行 2.5:边权方案(FR-CON-008 P0-fix 2026-07-20)
        row25 = QHBoxLayout()
        row25.addWidget(BodyLabel("边权方案:", card))
        from app.view.widgets.freq_analyzer.network_engine import EdgeWeight

        self.edgeWeightCombo = ComboBox(card)
        self.edgeWeightCombo.addItem(
            "共现频次 (Frequency)", userData=EdgeWeight.FREQUENCY.value
        )
        self.edgeWeightCombo.addItem("PMI", userData=EdgeWeight.PMI.value)
        self.edgeWeightCombo.addItem(
            "NPMI (Bouma 2009)", userData=EdgeWeight.NPMI.value
        )
        self.edgeWeightCombo.addItem("Dice 系数", userData=EdgeWeight.DICE.value)
        self.edgeWeightCombo.addItem(
            "LogDice (Rychlý 2008)", userData=EdgeWeight.LOG_DICE.value
        )
        self.edgeWeightCombo.addItem("Jaccard", userData=EdgeWeight.JACCARD.value)
        self.edgeWeightCombo.setCurrentIndex(0)
        self.edgeWeightCombo.setToolTip(
            "选择边权重计算方式:\n"
            "• Frequency:绝对共现频次(默认,受高频词偏置)\n"
            "• PMI/NPMI:信息论指标,识别非偶然共现\n"
            "• Dice/LogDice/Jaccard:归一化相似度\n"
            "归一化方案能显著降低功能词 hub 偏置"
        )
        row25.addWidget(self.edgeWeightCombo)

        self.biasHintLabel = CaptionLabel(
            "⚠️ Frequency 模式受高频词偏置,建议学术分析使用 PMI/LogDice",
            card,
        )
        setThemeRole(self.biasHintLabel, "warning", "font-size: 11px;")
        row25.addWidget(self.biasHintLabel, 1)

        layout.addLayout(row25)

        # 行 3:操作按钮
        row3 = QHBoxLayout()
        self.buildBtn = PrimaryPushButton("构建网络", card)
        self.buildBtn.setIcon(FluentIcon.SEARCH)
        self.buildBtn.clicked.connect(self._onBuildClicked)
        row3.addWidget(self.buildBtn)
        # AI 解读按钮（PRD-001 REQ-AI-001）— 通过 Mixin 一行接入
        # 统一为 PrimaryPushButton,放在「构建网络」旁边
        self._aiInsightBtn = PrimaryPushButton("AI 解读", card)
        self._aiInsightBtn.setIcon(FluentIcon.HEART)
        self.setupAiInsightButton(self._aiInsightBtn)
        row3.addWidget(self._aiInsightBtn)

        row3.addStretch(1)

        self.exportPngBtn = PushButton("导出 PNG", card)
        self.exportPngBtn.setIcon(FluentIcon.SAVE)
        self.exportPngBtn.clicked.connect(lambda: self._export("png"))
        self.exportPngBtn.setEnabled(False)
        row3.addWidget(self.exportPngBtn)

        self.exportSvgBtn = PushButton("导出 SVG", card)
        self.exportSvgBtn.setIcon(FluentIcon.SAVE)
        self.exportSvgBtn.clicked.connect(lambda: self._export("svg"))
        self.exportSvgBtn.setEnabled(False)
        row3.addWidget(self.exportSvgBtn)

        self.exportGexfBtn = PushButton("导出 GEXF", card)
        self.exportGexfBtn.setIcon(FluentIcon.SAVE)
        self.exportGexfBtn.clicked.connect(lambda: self._export("gexf"))
        self.exportGexfBtn.setEnabled(False)
        row3.addWidget(self.exportGexfBtn)

        self.exportGraphMLBtn = PushButton("导出 GraphML", card)
        self.exportGraphMLBtn.setIcon(FluentIcon.SAVE)
        self.exportGraphMLBtn.clicked.connect(lambda: self._export("graphml"))
        self.exportGraphMLBtn.setEnabled(False)
        row3.addWidget(self.exportGraphMLBtn)

        layout.addLayout(row3)

        return card

    def _buildChartCard(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(StrongBodyLabel("网络图", card))

        # 摘要(优化:统一大指标卡)
        from app.view.widgets.freq_analyzer.result_summary import (
            MetricColor,
            ResultSummary,
        )

        self._resultSummary = ResultSummary(self)
        self._resultSummary.setTitle("共现网络摘要")
        self._resultSummary.setPlaceholder("请配置参数后点击「构建网络」")
        layout.addWidget(self._resultSummary)
        # 兼容旧代码
        self.summaryLabel = self._resultSummary._detailLabel

        # Matplotlib Figure
        self._figure = Figure(
            figsize=(8, 6),
            dpi=100,
            facecolor=shellPalette().surface.name(),
        )
        self._canvas = FigureCanvas(self._figure)
        self._canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._toolbar = NavigationToolbar(self._canvas, card)
        self._toolbar.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(self._toolbar)
        layout.addWidget(self._canvas, 1)

        # 状态栏
        self.statusLabel = CaptionLabel("", card)
        setThemeRole(self.statusLabel, "muted", "font-size: 11px;")
        layout.addWidget(self.statusLabel)

        # 初始化空图
        self._ax = self._figure.add_subplot(111)
        self._drawPlaceholder("请配置参数后点击「构建网络」")

        # 绑定悬停事件(FR-CON-003)
        self._hover = HoverAnnotation(self._ax)
        self._canvas.mpl_connect("motion_notify_event", self._onMouseMove)

        return card

    # ------------------------------------------------------------------
    # 行为:构建网络
    # ------------------------------------------------------------------
    def _onBuildClicked(self):
        if not self._reloadEffectiveTexts():
            return
        if not self.fileToText:
            _showInfoBar(
                "warning",
                "提示",
                "请先在「语料导入与清洗」标签加载语料",
                self,
                duration=2500,
            )
            return
        if self._worker and self._worker.isRunning():
            _showInfoBar("info", "提示", "正在构建中,请稍候", self, duration=1500)
            return

        params = self._collectParams()
        self._engine.caseSensitive = params.caseSensitive

        self.buildBtn.setEnabled(False)
        self.statusLabel.setText("构建中...")

        self._worker = NetworkBuildWorker(
            engine=self._engine,
            fileToText=self.fileToText,
            params=params,
            parent=self,
        )
        self._worker.progress.connect(self._onBuildProgress)
        self._worker.finished.connect(self._onBuildFinished)
        self._worker.failed.connect(self._onBuildFailed)
        self._worker.start()

    def _collectParams(self) -> NetworkBuildParams:
        stopwords = (
            set(stopwordService.words())
            if stopwordService.isEnabled()
            else set()
        )

        return NetworkBuildParams(
            windowSize=self.windowSpin.value(),
            minWordFreq=self.minFreqSpin.value(),
            minCoFreq=self.minCoSpin.value(),
            keepTopK=self.topKSpin.value(),
            useJieba=True,
            caseSensitive=self.caseCheck.isChecked(),
            stopwords=stopwords,
            keyword="",  # 向后兼容,filterExpr 接管
            enableCommunity=self.communityCheck.isChecked(),
            # FR-CON-008 P0-fix 2026-07-20:边权方案
            edgeWeight=self.edgeWeightCombo.currentData(),
            # FR-CON-010 P0-fix 2026-07-20:统一过滤模式
            filterExpr=self.filterExprEdit.text().strip(),
        )

    def _onBuildProgress(self, msg: str):
        self.statusLabel.setText(msg)

    def _onBuildFailed(self, err: str):
        self.buildBtn.setEnabled(True)
        self.statusLabel.setText("构建失败")
        logger.error(f"[NetworkWidget] 构建失败: {err}")
        _showInfoBar("error", "构建失败", err[:200], self, duration=4000)

    def _onBuildFinished(self, network: CooccurrenceNetwork):
        self.buildBtn.setEnabled(True)
        self._network = network
        self._pos = self._engine.computeLayout(network.graph)
        self._renderNetwork()
        self._updateSummary()

        for btn in (
            self.exportPngBtn,
            self.exportSvgBtn,
            self.exportGexfBtn,
            self.exportGraphMLBtn,
        ):
            btn.setEnabled(True)

        # AI 解读入口：网络非空时启用
        if hasattr(self, "_aiInsightBtn"):
            self._aiInsightBtn.setEnabled(network is not None and network.nodeCount > 0)

        if network.nodeCount == 0:
            _showInfoBar(
                "warning",
                "无可用结果",
                "当前参数下没有满足条件的节点,请放宽阈值后重试",
                self,
                duration=3000,
            )
        else:
            _showInfoBar(
                "success",
                "构建成功",
                f"节点 {network.nodeCount} / 边 {network.edgeCount}",
                self,
                duration=2500,
            )

        # PRD-002:归档到当前激活项目(若有)
        self.notifyResourceCreated()

    # ------------------------------------------------------------------
    # PRD-002 资源归档钩子(ResourceSinkMixin)
    # ------------------------------------------------------------------
    def _collectResourcePayload(self):
        """共现网络结果 → 项目资源 payload"""
        network = getattr(self, "_network", None)
        if network is None or getattr(network, "nodeCount", 0) == 0:
            return None
        try:
            # 收集 Top-10 节点(按 degree)
            import networkx as nx

            graph = network.graph
            degrees = sorted(graph.degree(), key=lambda x: x[1], reverse=True)[:10]
            topText = "、".join(f"{n}({d})" for n, d in degrees)
            summary = (
                f"网络 {network.nodeCount} 节点 / {network.edgeCount} 边。"
                f"Top 度数:{topText}"
            )
        except Exception:
            summary = f"网络 {network.nodeCount} 节点 / {network.edgeCount} 边"
        # 边权重指标:widget 没有 self.metric,只有 edgeWeightCombo
        # (CooccurrenceNetwork 上也没有 edgeMetric 字段,从 widget 控件取)
        metricStr = ""
        try:
            combo = getattr(self, "edgeWeightCombo", None)
            if combo is not None:
                metricStr = str(combo.currentData() or combo.currentText() or "")
        except Exception:
            metricStr = ""
        snapshotData = {
            "nodeCount": network.nodeCount,
            "edgeCount": network.edgeCount,
            "metric": metricStr,
        }
        parameters = {
            "windowSize": getattr(self, "windowSize", 5),
            "metric": metricStr,
            "threshold": getattr(self, "threshold", 0),
            "topK": getattr(self, "topK", 50),
        }
        return {
            "title": f"共现网络 ({self._buildDefaultTitle().split(' ', 1)[1]})",
            "summary": summary[:200],
            "parameters": parameters,
            "snapshotData": snapshotData,
        }

    # ------------------------------------------------------------------
    # 行为:绘制网络图
    # ------------------------------------------------------------------
    def _renderNetwork(self):
        """根据 self._network 与 self._pos 渲染网络(FR-CON-002/004/005)"""
        self._ax.clear()
        if self._network is None or self._network.nodeCount == 0:
            self._drawPlaceholder("无节点可显示")
            return

        graph = self._network.graph
        pos = self._pos

        # 计算节点大小(按词频,做开方压缩)
        nodeFreqs = [graph.nodes[n].get("freq", 1) for n in graph.nodes]
        maxFreq = max(nodeFreqs) if nodeFreqs else 1
        # 节点直径范围: 60 ~ 900 平方点 ≈ 8 ~ 30 pt
        nodeSizes = [60 + 840 * math.sqrt(f / maxFreq) for f in nodeFreqs]

        # 边宽度(按共现频次)
        edges = list(graph.edges(data=True))
        weights = [d.get("weight", 1) for _, _, d in edges] if edges else [1]
        maxW = max(weights) if weights else 1
        edgeWidths = [0.3 + 4.0 * (w / maxW) for w in weights]

        # 颜色:按社区着色(FR-CON-005)
        if self._network.params.enableCommunity and self._network.communities:
            colors = [
                colorForCommunity(self._network.communities.get(n, 0))
                for n in graph.nodes
            ]
        else:
            colors = ["#1f77b4"] * graph.number_of_nodes()

        # 节点 labels
        labels = {n: n for n in graph.nodes}

        # 动态选择系统中实际可用的中文字体,避免 matplotlib findfont 警告
        cjkFonts = _availableCjkFonts()

        # 边
        if edges:
            nx.draw_networkx_edges(
                graph,
                pos,
                ax=self._ax,
                width=edgeWidths,
                edge_color=shellPalette().border.name(),
                alpha=0.55,
            )

        # 节点(scatter,便于悬停事件)
        xy = np.array([pos[n] for n in graph.nodes])
        self._nodeScatter = self._ax.scatter(
            xy[:, 0],
            xy[:, 1],
            s=nodeSizes,
            c=colors,
            alpha=0.85,
            edgecolors="white",
            linewidths=1.0,
            zorder=3,
            picker=False,  # 使用 motion_notify_event 自行判断
        )

        # labels
        nx.draw_networkx_labels(
            graph,
            pos,
            labels=labels,
            ax=self._ax,
            font_size=9,
            font_family=cjkFonts,
                font_color=shellPalette().text.name(),
        )

        self._ax.set_axis_off()
        self._ax.set_xlim(-0.05, 1.05)
        self._ax.set_ylim(-0.05, 1.05)
        self._ax.set_title(
            f"词语共现网络  ·  节点 {graph.number_of_nodes()} / 边 {graph.number_of_edges()}",
            fontsize=12,
            pad=10,
        )
        self._ax.figure.tight_layout()
        self._canvas.draw_idle()

        # 重建 hover annotation(ax 已重建)
        self._hover = HoverAnnotation(self._ax)

    def _updateSummary(self):
        if self._network is None:
            return
        n = self._network
        communityCount = (
            len(set(n.communities.values()))
            if n.params.enableCommunity and n.communities
            else 0
        )
        density = (
            (2.0 * n.edgeCount / (n.nodeCount * (n.nodeCount - 1)))
            if n.nodeCount > 1
            else 0.0
        )

        from app.view.widgets.freq_analyzer.result_summary import MetricColor

        self._resultSummary.clear()
        self._resultSummary.setMetrics(
            [
                ("节点数", f"{n.nodeCount:,}", MetricColor.PRIMARY),
                ("边数", f"{n.edgeCount:,}", MetricColor.SUCCESS),
                ("社区数", f"{communityCount}", MetricColor.ACCENT),
                ("密度", f"{density:.3f}", MetricColor.NEUTRAL),
            ]
        )
        filterExpr = n.params.filterExpr or n.params.keyword or "(无)"
        self._resultSummary.setDetail(
            f"🕸️ 窗口 <b>±{n.params.windowSize}</b> &nbsp;|&nbsp; "
            f"最小词频 <b>{n.params.minWordFreq}</b> &nbsp;|&nbsp; "
            f"最小共现 <b>{n.params.minCoFreq}</b> &nbsp;|&nbsp; "
            f"过滤模式 <b>{filterExpr}</b>"
        )
        self.summaryLabel.setText("")  # 兼容旧引用
        self.statusLabel.setText(
            "悬停节点查看详细信息;使用顶部工具栏可缩放、平移、保存图像"
        )

    def _drawPlaceholder(self, msg: str):
        self._ax.clear()
        self._ax.set_axis_off()
        self._ax.text(
            0.5,
            0.5,
            msg,
            ha="center",
            va="center",
            fontsize=14,
                color=shellPalette().mutedText.name(),
            transform=self._ax.transAxes,
        )
        self._canvas.draw_idle()

    # ------------------------------------------------------------------
    # 行为:鼠标悬停(FR-CON-003)
    # ------------------------------------------------------------------
    def _onMouseMove(self, event):
        """悬停节点 → 显示词语信息与连接数"""
        if (
            self._network is None
            or self._network.nodeCount == 0
            or self._nodeScatter is None
            or self._hover is None
            or self._ax is None
        ):
            return
        if event.inaxes != self._ax:
            self._hover.hide()
            return

        # 将屏幕坐标转换为数据坐标
        contains, ind = self._nodeScatter.contains(event)
        if not contains:
            self._hover.hide()
            return

        idx = ind["ind"][0]
        nodes = list(self._network.graph.nodes)
        if idx < 0 or idx >= len(nodes):
            return
        node = nodes[idx]
        freq = self._network.graph.nodes[node].get("freq", 0)
        degree = self._network.graph.degree(node)
        # 共现强度总和
        neighbors = list(self._network.graph.neighbors(node))
        weightSum = sum(
            self._network.graph[node][nbr].get("weight", 0) for nbr in neighbors
        )
        topNeighbors = sorted(
            neighbors,
            key=lambda x: self._network.graph[node][x].get("weight", 0),
            reverse=True,
        )[:5]
        cid = self._network.communities.get(node, -1)
        communityInfo = f"\n社区: {cid}" if self._network.params.enableCommunity else ""
        text = (
            f"{node}\n"
            f"词频: {freq}\n"
            f"连接数: {degree}\n"
            f"共现总强度: {weightSum}{communityInfo}"
        )
        if topNeighbors:
            text += "\nTop 邻居: " + ", ".join(
                f"{nbr}({self._network.graph[node][nbr].get('weight', 0)})"
                for nbr in topNeighbors
            )
        # 数据坐标
        x, y = self._pos[node]
        self._hover.update(x, y, text)

    # ------------------------------------------------------------------
    # 行为:导出(FR-CON-007)
    # ------------------------------------------------------------------
    def _export(self, fmt: str):
        if self._network is None or self._network.nodeCount == 0:
            _showInfoBar("warning", "提示", "暂无可导出结果", self, duration=2000)
            return
        ext = fmt
        if fmt == "gexf":
            defaultName = "cooccurrence.gexf"
            filt = "GEXF Files (*.gexf)"
        elif fmt == "graphml":
            defaultName = "cooccurrence.graphml"
            filt = "GraphML Files (*.graphml)"
        elif fmt == "svg":
            defaultName = "cooccurrence.svg"
            filt = "SVG Files (*.svg)"
        else:
            defaultName = "cooccurrence.png"
            filt = "PNG Files (*.png)"
        path, _ = QFileDialog.getSaveFileName(
            self, f"导出 {fmt.upper()}", defaultName, filt
        )
        if not path:
            return
        if not path.lower().endswith(f".{ext}"):
            path += f".{ext}"
        transaction = beginPaidAnalysisExport(self.window(), f"共现网络 {fmt.upper()}")
        if transaction is None:
            return
        try:
            if fmt in ("png", "svg"):
                self._figure.savefig(
                    path,
                    format=fmt,
                    dpi=300,
                    bbox_inches="tight",
                    facecolor="white",
                )
            elif fmt == "gexf":
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._engine.exportGexf(self._network))
            elif fmt == "graphml":
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._engine.exportGraphML(self._network))
            if not transaction.commit():
                raise RuntimeError("导出文件已生成，但计费结算失败；本次费用已释放")
            _showInfoBar("success", "导出成功", f"已保存:{path}", self, duration=2500)
        except Exception as e:
            transaction.refund()
            logger.error(f"[NetworkWidget] {fmt} 导出失败: {e}")
            _showInfoBar("error", "导出失败", str(e), self, duration=3000)

    # ------------------------------------------------------------------
    # AI 解读协议（PRD-001 REQ-AI-001）— 由 AiInsightMixin 调用
    # ------------------------------------------------------------------
    def _aiHasResult(self) -> bool:
        return self._network is not None and self._network.nodeCount > 0

    def _aiCollectExplainArgs(self):
        if not self._aiHasResult():
            return None
        params = {
            "windowSize": self.windowSpin.value(),
            "metric": str(self.edgeWeightCombo.currentData()),
        }
        return (
            "network",
            {
                "network": self._network,
                "windowSize": params["windowSize"],
                "metric": params["metric"],
            },
        )

    def _collectCorpusMeta(self) -> Dict[str, Any]:
        """汇总语料元信息"""
        meta: Dict[str, Any] = {
            "corpusName": "当前语料",
            "fileCount": 0,
            "totalChars": 0,
        }
        store = getattr(self, "_corpusStore", None)
        if store is not None:
            try:
                meta["fileCount"] = store.fileCount()
                meta["totalChars"] = store.totalChars()
            except Exception:
                pass
            try:
                from pathlib import Path as _Path

                meta["corpusName"] = _Path(store.dbPath).stem or "当前语料"
            except Exception:
                pass
        return meta

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        """关闭前停止后台线程,避免线程悬挂或泄漏(P0-fix)"""
        worker = self._worker
        if worker is not None:
            try:
                if hasattr(worker, "cancel"):
                    worker.cancel()
                if worker.isRunning():
                    worker.wait(2000)
            except Exception:
                pass
            worker.deleteLater()
            self._worker = None
        super().closeEvent(event)


# 工具:统一创建 SwitchButton
def _makeSwitch(text: str, parent: QWidget) -> "SwitchButton":
    btn = SwitchButton(text, parent)
    btn.setOnText(text)
    btn.setOffText(text)
    return btn
