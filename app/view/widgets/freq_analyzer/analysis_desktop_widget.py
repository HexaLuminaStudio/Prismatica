"""语料分析功能桌面。

仅负责展示真实语料状态并将用户路由到现有分析面板，不承载分析业务。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from app.core.utils import logger

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    IconWidget,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    isDarkTheme,
    qconfig,
)

from app.view.widgets.prismatica_theme import pageBackgroundColor


@dataclass(frozen=True)
class AnalysisModule:
    routeKey: str
    title: str
    description: str
    tags: Sequence[str]
    icon: FluentIcon


_FOUNDATION_MODULES = (
    AnalysisModule(
        "freqAnalyzer",
        "词频分析",
        "生成主词频表，并继续查看 Zipf 曲线与 N-gram 统计。",
        ("词频", "Zipf", "N-gram"),
        FluentIcon.PIE_SINGLE,
    ),
    AnalysisModule(
        "wordAnalysis",
        "词语分析",
        "查看词汇指标、高频词、词汇分布与增长曲线。",
        ("词汇指标", "分布"),
        FluentIcon.DOCUMENT,
    ),
    AnalysisModule(
        "keywordList",
        "主题词分析",
        "通过 LogLikelihood、LogRatio 与占比差识别特征词。",
        ("关键词", "LogRatio"),
        FluentIcon.HIGHTLIGHT,
    ),
    AnalysisModule(
        "concordance",
        "语境分析",
        "使用 KWIC 检索关键词，并观察跨文件语境分布。",
        ("KWIC", "语境"),
        FluentIcon.SEARCH,
    ),
)

_ADVANCED_MODULES = (
    AnalysisModule(
        "collocation",
        "搭配分析",
        "计算 MI、LogDice、T-score 等词语搭配指标。",
        ("搭配", "关联强度"),
        FluentIcon.LINK,
    ),
    AnalysisModule(
        "construction",
        "构式搭配强度",
        "分析构式槽位中的搭配分布与关联强度。",
        ("构式", "槽位"),
        FluentIcon.TILES,
    ),
    AnalysisModule(
        "sentiment",
        "情感分析",
        "评估语料情感倾向，并查看模型状态与结果。",
        ("情感", "模型"),
        FluentIcon.CHAT,
    ),
    AnalysisModule(
        "network",
        "共现网络图",
        "把词语共现关系整理为可交互网络。",
        ("共现", "网络"),
        FluentIcon.CONNECT,
    ),
    AnalysisModule(
        "wordCloud",
        "词语云图",
        "按真实词频生成中文词云并调节显示权重。",
        ("词云", "可视化"),
        FluentIcon.CLOUD,
    ),
    AnalysisModule(
        "dependency",
        "句法依存图",
        "分析句子依存关系并输出结构化句法图。",
        ("句法", "依存"),
        FluentIcon.SHARE,
    ),
)


class _ModuleCard(CardWidget):
    openRequested = Signal(str)

    def __init__(self, module: AnalysisModule, parent: QWidget) -> None:
        super().__init__(parent)
        self.module = module
        self.setObjectName("analysisModuleCard")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(module.title)
        self.setAccessibleDescription(module.description)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)

        iconHost = QFrame(self)
        iconHost.setObjectName("analysisModuleIconHost")
        iconHost.setFixedSize(42, 42)
        iconLayout = QVBoxLayout(iconHost)
        iconLayout.setContentsMargins(10, 10, 10, 10)
        self.iconWidget = IconWidget(module.icon, iconHost)
        self.iconWidget.setFixedSize(22, 22)
        iconLayout.addWidget(self.iconWidget)
        layout.addWidget(iconHost, 0, Qt.AlignmentFlag.AlignLeft)

        title = StrongBodyLabel(module.title, self)
        title.setObjectName("analysisModuleTitle")
        layout.addWidget(title)

        description = BodyLabel(module.description, self)
        description.setObjectName("analysisModuleDescription")
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.setSpacing(6)
        for value in module.tags:
            chip = QLabel(value, self)
            chip.setObjectName("analysisTagChip")
            footer.addWidget(chip)
        footer.addStretch(1)
        openButton = PushButton("打开", self)
        openButton.setObjectName("analysisCardOpenButton")
        openButton.setIcon(FluentIcon.CHEVRON_RIGHT_MED)
        openButton.setMinimumHeight(32)
        openButton.clicked.connect(lambda: self.openRequested.emit(module.routeKey))
        footer.addWidget(openButton)
        layout.addLayout(footer)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.openRequested.emit(self.module.routeKey)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.openRequested.emit(self.module.routeKey)
            event.accept()
            return
        super().keyPressEvent(event)


class AnalysisDesktopWidget(QWidget):
    """用卡片组织全部现有分析入口，并显示当前活动语料状态。"""

    moduleRequested = Signal(str)

    def __init__(self, parent=None, corpusStore=None, corpusManager=None) -> None:
        super().__init__(parent)
        self.setObjectName("analysisDesktop")
        self._corpusStore = None
        self._corpusManager = corpusManager
        self._cards: list[_ModuleCard] = []
        self._sectionHosts: list[QWidget] = []
        self._sectionGrids: list[tuple[QGridLayout, list[_ModuleCard]]] = []
        self._emptyState: QWidget = None  # type: ignore[assignment]
        self._emptyIconWidget: IconWidget = None  # type: ignore[assignment]
        self._buildUi()
        self.setCorpusStore(corpusStore)
        if corpusManager is not None:
            corpusManager.activeCorpusChanged.connect(self.refresh)
            corpusManager.registryChanged.connect(self.refresh)
        qconfig.themeChangedFinished.connect(self._applyTheme)
        self._applyTheme()
        QTimer.singleShot(0, self._reflowCards)

    def _buildUi(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = ScrollArea(self)
        scroll.setObjectName("analysisDesktopScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll)

        self.page = QWidget(scroll)
        self.page.setObjectName("analysisDesktopPage")
        scroll.setWidget(self.page)
        pageLayout = QVBoxLayout(self.page)
        pageLayout.setContentsMargins(28, 26, 28, 32)
        pageLayout.setSpacing(22)

        header = QHBoxLayout()
        headerText = QVBoxLayout()
        headerText.setSpacing(4)
        title = SubtitleLabel("语料分析", self.page)
        title.setObjectName("analysisDesktopTitle")
        subtitle = BodyLabel(
            "从数据准备开始，或直接进入适合当前研究问题的分析模块。",
            self.page,
        )
        subtitle.setObjectName("analysisDesktopSubtitle")
        headerText.addWidget(title)
        headerText.addWidget(subtitle)
        header.addLayout(headerText, 1)
        self.corpusNameLabel = CaptionLabel("当前语料库", self.page)
        self.corpusNameLabel.setObjectName("analysisCorpusName")
        header.addWidget(self.corpusNameLabel, 0, Qt.AlignmentFlag.AlignTop)
        pageLayout.addLayout(header)

        self.summaryPanel = QFrame(self.page)
        self.summaryPanel.setObjectName("analysisSummaryPanel")
        summaryLayout = QHBoxLayout(self.summaryPanel)
        summaryLayout.setContentsMargins(22, 18, 22, 18)
        summaryLayout.setSpacing(22)
        summaryText = QVBoxLayout()
        summaryText.setSpacing(4)
        summaryTitle = StrongBodyLabel("当前语料准备状态", self.summaryPanel)
        summaryTitle.setObjectName("analysisSummaryTitle")
        self.summaryHint = BodyLabel("尚未导入语料，请先完成数据准备。", self.summaryPanel)
        self.summaryHint.setObjectName("analysisSummaryHint")
        self.summaryHint.setWordWrap(True)
        summaryText.addWidget(summaryTitle)
        summaryText.addWidget(self.summaryHint)
        summaryLayout.addLayout(summaryText, 1)
        self.fileMetric = self._metric("0", "文件", self.summaryPanel)
        self.charMetric = self._metric("0", "字符", self.summaryPanel)
        self.cleanMetric = self._metric("未启用", "清洗", self.summaryPanel)
        summaryLayout.addWidget(self.fileMetric)
        summaryLayout.addWidget(self.charMetric)
        summaryLayout.addWidget(self.cleanMetric)
        prepareButton = PrimaryPushButton("导入与清洗", self.summaryPanel)
        prepareButton.setIcon(FluentIcon.DOWNLOAD)
        prepareButton.setMinimumSize(132, 36)
        prepareButton.clicked.connect(lambda: self.moduleRequested.emit("corpusImport"))
        summaryLayout.addWidget(prepareButton)
        pageLayout.addWidget(self.summaryPanel)

        # 空状态面板:无有效语料时显示,引导用户先导入+清洗
        self._emptyState = self._buildEmptyState(self.page)
        pageLayout.addWidget(self._emptyState)

        self._addSection(
            pageLayout,
            "核心分析",
            "从词频、词语、主题词与语境四个角度快速进入语料。",
            _FOUNDATION_MODULES,
        )
        self._addSection(
            pageLayout,
            "进阶分析与可视化",
            "所有入口继续使用原有面板与分析逻辑。",
            _ADVANCED_MODULES,
        )
        pageLayout.addStretch(1)

    def _metric(self, value: str, label: str, parent: QWidget) -> QFrame:
        frame = QFrame(parent)
        frame.setObjectName("analysisMetric")
        frame.setMinimumWidth(90)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(1)
        valueLabel = QLabel(value, frame)
        valueLabel.setObjectName("analysisMetricValue")
        labelWidget = QLabel(label, frame)
        labelWidget.setObjectName("analysisMetricLabel")
        layout.addWidget(valueLabel, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(labelWidget, 0, Qt.AlignmentFlag.AlignHCenter)
        frame.valueLabel = valueLabel
        return frame

    def _addSection(
        self,
        pageLayout: QVBoxLayout,
        title: str,
        description: str,
        modules: Sequence[AnalysisModule],
    ) -> None:
        # 整个 section 封装到一个 host 里,便于「语料未就绪」时整段隐藏
        sectionHost = QWidget(self.page)
        sectionHost.setObjectName("analysisSectionHost")
        sectionLayout = QVBoxLayout(sectionHost)
        sectionLayout.setContentsMargins(0, 0, 0, 0)
        sectionLayout.setSpacing(10)

        heading = QHBoxLayout()
        headingText = QVBoxLayout()
        headingText.setSpacing(2)
        titleLabel = StrongBodyLabel(title, sectionHost)
        titleLabel.setObjectName("analysisSectionTitle")
        hint = CaptionLabel(description, sectionHost)
        hint.setObjectName("analysisSectionHint")
        headingText.addWidget(titleLabel)
        headingText.addWidget(hint)
        heading.addLayout(headingText)
        heading.addStretch(1)
        sectionLayout.addLayout(heading)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        sectionCards: list[_ModuleCard] = []
        for index, module in enumerate(modules):
            card = _ModuleCard(module, sectionHost)
            card.openRequested.connect(self.moduleRequested)
            self._cards.append(card)
            sectionCards.append(card)
            grid.addWidget(card, index // 3, index % 3)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        sectionLayout.addLayout(grid)

        pageLayout.addWidget(sectionHost)
        self._sectionHosts.append(sectionHost)
        self._sectionGrids.append((grid, sectionCards))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflowCards()

    def _reflowCards(self) -> None:
        columns = 3 if self.width() >= 1050 else 2
        for grid, cards in self._sectionGrids:
            for card in cards:
                grid.removeWidget(card)
            for index, card in enumerate(cards):
                grid.addWidget(card, index // columns, index % columns)
            for column in range(3):
                grid.setColumnStretch(column, 1 if column < columns else 0)

    def setCorpusStore(self, store) -> None:
        if self._corpusStore is store:
            self.refresh()
            return
        if self._corpusStore is not None:
            try:
                self._corpusStore.textsChanged.disconnect(self.refresh)
                self._corpusStore.cleanRuleChanged.disconnect(self.refresh)
            except (RuntimeError, TypeError):
                pass
        self._corpusStore = store
        if store is not None:
            store.textsChanged.connect(self.refresh)
            store.cleanRuleChanged.connect(self.refresh)
        self.refresh()

    def refresh(self, *_args) -> None:
        store = self._corpusStore
        fileCount = store.fileCount() if store is not None else 0
        totalChars = store.totalChars() if store is not None else 0
        cleanEnabled = bool(store.cleanEnabled) if store is not None else False
        self.fileMetric.valueLabel.setText(f"{fileCount:,}")
        self.charMetric.valueLabel.setText(f"{totalChars:,}")
        self.cleanMetric.valueLabel.setText("已启用" if cleanEnabled else "未启用")

        active = self._corpusManager.activeCorpus() if self._corpusManager else None
        corpusName = getattr(active, "name", "当前语料库") if active else "当前语料库"
        self.corpusNameLabel.setText(f"当前语料：{corpusName}")

        ready = self._hasReadyCorpus()
        # 根据「有效活跃语料库」状态切换顶部提示文案
        if not fileCount:
            self.summaryHint.setText("尚未导入语料，请先完成数据准备。")
        elif not cleanEnabled:
            self.summaryHint.setText(
                f"已导入 {fileCount} 个文件，共 {totalChars:,} 字符；"
                f"当前分析将使用原始文本，也可进入数据准备启用清洗。"
            )
        elif not ready:
            self.summaryHint.setText(
                f"已导入 {fileCount} 个文件，共 {totalChars:,} 字符；"
                f"清洗规则已启用,正在后台预热缓存,请稍候。"
            )
        else:
            self.summaryHint.setText(
                f"{fileCount} 个文件已就绪，共 {totalChars:,} 字符；清洗规则已启用。"
            )

        # 仅在「语料已就绪」时显示功能卡片,其余场景隐藏并展示空状态引导
        self._applyReadyState(ready)

    def _hasReadyCorpus(self) -> bool:
        """判定当前是否存在「有效活跃语料库」。

        判定条件(全部满足):
            1. CorpusStore 已注入(fileCount 可读)
            2. fileCount() > 0(已导入文件)
            3. 未启用清洗时直接使用原始文本
            4. 已启用清洗时 effectiveChars() 非空(缓存已覆盖全部文件)

        Returns:
            True 表示语料已就绪,可进入功能卡片;False 表示尚未就绪。
        """
        store = self._corpusStore
        if store is None:
            return False
        try:
            fileCount = store.fileCount()
            if fileCount <= 0:
                return False
            if not bool(store.cleanEnabled):
                return True
            # effectiveChars() 返回 None 表示「清洗未启用 或 缓存未就绪」
            eff = store.effectiveChars()
            if eff is None:
                return False
        except Exception as e:
            logger.warning(f"[AnalysisDesktop] 语料就绪状态判定失败: {e}")
            return False
        return True

    def _applyReadyState(self, ready: bool) -> None:
        """根据就绪状态切换功能卡片与空状态面板的可见性。

        - ready=True :显示所有 section 卡片,隐藏空状态
        - ready=False:隐藏所有 section 卡片,显示空状态引导
        """
        for host in self._sectionHosts:
            if host is not None:
                host.setVisible(ready)
        if self._emptyState is not None:
            self._emptyState.setVisible(not ready)

    def _buildEmptyState(self, parent: QWidget) -> QWidget:
        """构造空状态面板:无有效语料时显示,引导用户先导入+清洗。"""
        frame = QFrame(parent)
        frame.setObjectName("analysisEmptyState")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(40, 56, 40, 56)
        layout.setSpacing(14)

        iconHost = QFrame(frame)
        iconHost.setObjectName("analysisEmptyIconHost")
        iconHost.setFixedSize(72, 72)
        iconLayout = QVBoxLayout(iconHost)
        iconLayout.setContentsMargins(18, 18, 18, 18)
        self._emptyIconWidget = IconWidget(FluentIcon.DICTIONARY, iconHost)
        self._emptyIconWidget.setFixedSize(36, 36)
        iconLayout.addWidget(self._emptyIconWidget)
        layout.addWidget(iconHost, 0, Qt.AlignmentFlag.AlignHCenter)

        title = StrongBodyLabel("暂无可分析的语料", frame)
        title.setObjectName("analysisEmptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        hint = BodyLabel(
            "请先在「导入与清洗」中导入文件,启用并完成清洗后,此处将开放全部功能模块。",
            frame,
        )
        hint.setObjectName("analysisEmptyHint")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint, 0, Qt.AlignmentFlag.AlignHCenter)

        action = PrimaryPushButton("前往导入与清洗", frame)
        action.setIcon(FluentIcon.DOWNLOAD)
        action.setMinimumSize(160, 38)
        action.clicked.connect(lambda: self.moduleRequested.emit("corpusImport"))
        layout.addWidget(action, 0, Qt.AlignmentFlag.AlignHCenter)

        return frame

    def _applyTheme(self) -> None:
        dark = isDarkTheme()
        background = pageBackgroundColor(dark).name()
        surface = "#2B3035" if dark else "#FFFFFF"
        surfaceAlt = "#343B40" if dark else "#F0F5F4"
        border = "#465058" if dark else "#DDE4E2"
        text = "#F3F6F7" if dark else "#1E252B"
        muted = "#B8C2C8" if dark else "#596873"
        accent = "#56D6C5" if dark else "#007C70"
        iconColor = QColor(accent)
        for card in self._cards:
            card.iconWidget.setIcon(card.module.icon.icon(color=iconColor))
        if self._emptyIconWidget is not None:
            self._emptyIconWidget.setIcon(FluentIcon.DICTIONARY.icon(color=iconColor))
        self.setStyleSheet(
            f"""
            QWidget#analysisDesktopPage {{ background: {background}; }}
            QScrollArea#analysisDesktopScroll {{ border: none; background: {background}; }}
            QLabel {{ color: {text}; }}
            QLabel#analysisDesktopTitle {{ font-size: 28px; font-weight: 700; }}
            QLabel#analysisDesktopSubtitle, QLabel#analysisSummaryHint,
            QLabel#analysisSectionHint, QLabel#analysisModuleDescription {{ color: {muted}; }}
            QLabel#analysisCorpusName {{ color: {accent}; background: {surfaceAlt};
                padding: 5px 10px; border-radius: 6px; font-weight: 600; }}
            QFrame#analysisSummaryPanel {{ background: {surface}; border: 1px solid {border};
                border-radius: 12px; }}
            QFrame#analysisMetric {{ background: transparent; border: none;
                border-left: 1px solid {border}; }}
            QLabel#analysisMetricValue {{ color: {accent}; font-size: 18px; font-weight: 700; }}
            QLabel#analysisMetricLabel {{ color: {muted}; font-size: 11px; }}
            QLabel#analysisSectionTitle {{ font-size: 17px; font-weight: 700; }}
            QFrame#analysisModuleCard {{ background: {surface}; border: 1px solid {border};
                border-radius: 12px; }}
            QFrame#analysisModuleCard:hover {{ border-color: {accent}; background: {surfaceAlt}; }}
            QFrame#analysisModuleCard:focus {{ border: 2px solid {accent}; }}
            QFrame#analysisModuleIconHost {{ background: {surfaceAlt}; border: none;
                border-radius: 10px; }}
            QLabel#analysisModuleTitle {{ font-size: 16px; font-weight: 700; }}
            QLabel#analysisTagChip {{ color: {muted}; background: {surfaceAlt};
                padding: 3px 7px; border-radius: 4px; font-size: 11px; }}
            QPushButton#analysisCardOpenButton {{ min-width: 62px; }}
            QFrame#analysisEmptyState {{ background: {surface}; border: 1px dashed {border};
                border-radius: 12px; }}
            QFrame#analysisEmptyIconHost {{ background: {surfaceAlt}; border: none;
                border-radius: 36px; }}
            QLabel#analysisEmptyTitle {{ font-size: 18px; font-weight: 700; color: {text}; }}
            QLabel#analysisEmptyHint {{ color: {muted}; }}
            """
        )


__all__ = ["AnalysisDesktopWidget", "AnalysisModule"]
