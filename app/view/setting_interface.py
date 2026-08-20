# coding: utf-8
"""
设置界面模块
提供软件设置、关于信息、激活码管理和用户协议等功能
"""

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QBoxLayout,
    QFileDialog,
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
    ComboBox,
    FluentIcon,
    GroupHeaderCardWidget,
    HyperlinkLabel,
    IconWidget,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SwitchButton,
    VerticalSeparator,
    HyperlinkButton,
    Theme,
    setTheme,
)

from app.core.services import (
    GlobalTokenRefreshThread,
    HskTokenRefreshThread,
    stopwordService,
    systemInfoService,
)
from app.core.services.startup_database_service import StartupDatabaseService
from app.core.utils import cfg, qconfig, logger, signalBus
from app.core.utils.setting import INTERNAL_TEST_MODE
from app.view.widgets.prismatica_theme import pageBackgroundColor, shellPalette
from app.view.widgets.pricing_status_dialog import PricingStatusDialog
from app.view.widgets.resource_verification_dialog import (
    ResourceVerificationDialog,
)


_ACCENT = "#00B09C"
_ACCENT_SOFT = "#EAF8F6"
_TEXT = "#1F1F1F"
_MUTED = "#616161"


def _setChineseUiFont(widget: QWidget, size: int = 10, weight=QFont.Weight.Normal):
    """为设置页提供稳定的中西文字体回退。"""
    font = QFont("Segoe UI")
    font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
    font.setPointSize(size)
    font.setWeight(weight)
    widget.setFont(font)


def _accentIcon(icon):
    """把 Fluent 图标统一渲染为设计稿的品牌青色。"""
    if hasattr(icon, "icon"):
        return icon.icon(color=QColor(_ACCENT))
    return icon


def _createTransparentActionWidget(parent: QWidget) -> QWidget:
    """创建不绘制平台窗口底色的组合操作容器。"""
    wrapper = QWidget(parent)
    wrapper.setObjectName("settingActionWidget")
    wrapper.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    wrapper.setStyleSheet(
        "QWidget#settingActionWidget { background: transparent; border: none; }"
    )
    return wrapper


def _pathDisplayStyle(palette=None) -> str:
    """保存路径与提示词文件共用的主题化文本区域样式。"""
    palette = palette or shellPalette()
    return (
        f"color: {palette.mutedText.name()}; "
        f"background: {palette.surfaceAlt.name()}; "
        f"border: 1px solid {palette.border.name()}; "
        "border-radius: 6px; padding: 7px 10px;"
    )


class SettingStatusBadge(QLabel):
    """设置状态徽标，只表达配置状态，不暴露配置值。"""

    def __init__(self, configured=False, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(68)
        self.setFixedHeight(24)
        _setChineseUiFont(self, 9, QFont.Weight.DemiBold)
        self.setConfigured(configured)

    def setConfigured(
        self,
        configured: bool,
        configuredText: str = "已配置",
        missingText: str = "未配置",
    ) -> None:
        self.setProperty("configured", bool(configured))
        self.setText(f"● {configuredText if configured else missingText}")
        if configured:
            self.setStyleSheet(
                "QLabel { color: #107C10; background: #EAF5EA; "
                "border: 1px solid #CDE5CD; border-radius: 12px; padding: 0 9px; }"
            )
        else:
            self.setStyleSheet(
                "QLabel { color: #A17C00; background: #FBF7E6; "
                "border: 1px solid #E9DDA6; border-radius: 12px; padding: 0 9px; }"
            )


class OverviewGroupCard(GroupHeaderCardWidget):
    """设计稿中的设置概览卡片。"""

    def __init__(self, title: str, headerIcon, summary: str = "", parent=None):
        super().__init__(parent)
        self.setTitle(title)
        self.setMinimumWidth(0)
        self.setMaximumWidth(832)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setObjectName("overviewSettingCard")

        self.headerIconContainer = QWidget(self.headerView)
        self.headerIconContainer.setFixedSize(28, 28)
        self.headerIconContainer.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        self.headerIconContainer.setStyleSheet(
            f"background: {_ACCENT_SOFT}; border-radius: 6px;"
        )
        headerIconLayout = QHBoxLayout(self.headerIconContainer)
        headerIconLayout.setContentsMargins(6, 6, 6, 6)
        self.headerIcon = IconWidget(
            _accentIcon(headerIcon), self.headerIconContainer
        )
        self.headerIcon.setFixedSize(16, 16)
        headerIconLayout.addWidget(self.headerIcon)
        self.headerLayout.insertWidget(0, self.headerIconContainer)
        self.headerLayout.setSpacing(12)
        self.headerLayout.setContentsMargins(24, 0, 24, 0)
        self.headerView.setMinimumHeight(60)
        self.headerView.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        self.headerSummaryLabel = CaptionLabel(summary, self.headerView)
        self.headerSummaryLabel.setStyleSheet(f"color: {_MUTED};")
        self.headerSummaryLabel.setWordWrap(True)
        self.headerSummaryLabel.setMinimumWidth(0)
        self.headerSummaryLabel.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.headerLayout.addStretch(1)
        self.headerLayout.addWidget(self.headerSummaryLabel)

        _setChineseUiFont(self.headerLabel, 11, QFont.Weight.DemiBold)
        self.setBorderRadius(8)
        self._isCompact = False
        self._applyCardStyle()
        qconfig.themeChangedFinished.connect(self._applyCardStyle)
        # 不使用 QGraphicsDropShadowEffect：设置页在导航的 300ms 切页动画中
        # 会连续重绘四张大卡片，实时模糊阴影会让每帧开销接近翻倍。
        # 设计稿的层级感由 1px 边框和浅色背景保留，避免首次进入与滚动卡顿。

    def _applyCardStyle(self) -> None:
        """主题刷新后恢复卡片样式，并保持所有文字子控件透明。"""
        palette = shellPalette()
        cardColor = palette.surface.name()
        borderColor = palette.border.name()
        titleColor = palette.text.name()
        summaryColor = palette.mutedText.name()
        self.headerIconContainer.setStyleSheet(
            f"background: {palette.accentSurface.name()}; border-radius: 6px;"
        )
        self.headerSummaryLabel.setStyleSheet(
            f"color: {summaryColor}; background-color: transparent;"
        )
        self.setStyleSheet(
            f"#overviewSettingCard {{ background: {cardColor}; "
            f"border: 1px solid {borderColor}; border-radius: 8px; }}"
            f"#overviewSettingCard > #headerView {{ background: {cardColor}; "
            "border-top-left-radius: 8px; border-top-right-radius: 8px; }"
            "#overviewSettingCard > #view { background: transparent; }"
            "#overviewSettingCard FluentLabelBase { background-color: transparent; }"
            "#overviewSettingCard > #headerView > #headerLabel { "
            f"color: {titleColor}; background-color: transparent; }}"
        )
        for group in self.groupWidgets:
            group.iconContainer.setStyleSheet(
                f"background: {palette.accentSurface.name()}; border-radius: 8px;"
            )
            group.titleLabel.setStyleSheet(
                f"color: {titleColor}; background-color: transparent;"
            )
            group.contentLabel.setStyleSheet(
                f"color: {summaryColor}; background-color: transparent;"
            )

    def setHeaderSummary(self, text: str) -> None:
        self.headerSummaryLabel.setText(text)

    def addGroup(self, icon, title, content, widget, stretch=0):
        group = super().addGroup(icon, title, content, widget, stretch)
        group.setMinimumHeight(0)
        group.hBoxLayout.setContentsMargins(24, 18, 24, 18)
        group.hBoxLayout.setSpacing(16)
        group.textLayout.setSpacing(3)
        group.textLayout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        group.titleLabel.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        group.contentLabel.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        # 将图标和文字组成可伸缩的前导区域。原组件把文字、spacer 和右侧控件
        # 放在同一行，窗口变窄后会优先把副文字压到几像素宽。
        group.hBoxLayout.removeWidget(group.iconWidget)
        for index in range(group.hBoxLayout.count() - 1, -1, -1):
            item = group.hBoxLayout.itemAt(index)
            if item.layout() is group.textLayout or item.spacerItem() is not None:
                group.hBoxLayout.takeAt(index)

        group.iconContainer = QWidget(group)
        group.iconContainer.setFixedSize(QSize(36, 36))
        group.iconContainer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        group.iconContainer.setStyleSheet(
            f"background: {_ACCENT_SOFT}; border-radius: 8px;"
        )
        iconLayout = QHBoxLayout(group.iconContainer)
        iconLayout.setContentsMargins(8, 8, 8, 8)
        group.iconWidget.setIcon(_accentIcon(icon))
        group.iconWidget.setParent(group.iconContainer)
        group.iconWidget.setFixedSize(QSize(20, 20))
        group.iconWidget.setStyleSheet("background: transparent;")
        iconLayout.addWidget(group.iconWidget)

        group.leadingWidget = QWidget(group)
        group.leadingWidget.setObjectName("settingGroupLeadingWidget")
        group.leadingWidget.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        group.leadingWidget.setStyleSheet(
            "QWidget#settingGroupLeadingWidget { "
            "background-color: transparent; border: none; }"
        )
        group.leadingWidget.setMinimumWidth(0)
        group.leadingWidget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        group.leadingLayout = QHBoxLayout(group.leadingWidget)
        group.leadingLayout.setContentsMargins(0, 0, 0, 0)
        group.leadingLayout.setSpacing(16)
        group.leadingLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        group.leadingLayout.addWidget(
            group.iconContainer, 0, Qt.AlignmentFlag.AlignTop
        )
        group.leadingLayout.addLayout(group.textLayout, 1)
        group.hBoxLayout.insertWidget(0, group.leadingWidget, 1)

        group.controlWidget = widget
        group.titleLabel.setMinimumWidth(0)
        group.titleLabel.setWordWrap(True)
        group.titleLabel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        group.contentLabel.setWordWrap(True)
        group.contentLabel.setMinimumWidth(0)
        group.contentLabel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
        )
        group.contentLabel.setTextColor(QColor(_MUTED), QColor("#A8B0BC"))
        _setChineseUiFont(group.titleLabel, 10, QFont.Weight.DemiBold)
        _setChineseUiFont(group.contentLabel, 9)
        self._applyGroupLayout(group, self._isCompact)
        self._applyCardStyle()
        return group

    def setCompactLayout(self, isCompact: bool) -> None:
        """在窄窗口中把设置控件移到说明文字下方，避免副文字被横向挤压。"""
        if self._isCompact == isCompact:
            return
        self._isCompact = isCompact
        for group in self.groupWidgets:
            self._applyGroupLayout(group, isCompact)
        self.updateGeometry()

    @staticmethod
    def _applyGroupLayout(group, isCompact: bool) -> None:
        direction = (
            QBoxLayout.Direction.TopToBottom
            if isCompact
            else QBoxLayout.Direction.LeftToRight
        )
        group.hBoxLayout.setDirection(direction)
        group.hBoxLayout.setContentsMargins(
            16 if isCompact else 24,
            16 if isCompact else 18,
            16 if isCompact else 24,
            16 if isCompact else 18,
        )
        group.hBoxLayout.setSpacing(12 if isCompact else 16)
        group.hBoxLayout.setStretch(0, 1)
        group.hBoxLayout.setAlignment(
            group.controlWidget,
            Qt.AlignmentFlag(0)
            if isCompact
            else Qt.AlignmentFlag.AlignVCenter,
        )
        group.contentLabel.updateGeometry()
        group.titleLabel.updateGeometry()
        group.updateGeometry()

    def addInfoBanner(self, text: str) -> QLabel:
        bannerWrapper = QWidget(self.view)
        bannerLayout = QVBoxLayout(bannerWrapper)
        bannerLayout.setContentsMargins(24, 16, 24, 16)
        banner = QLabel(text, bannerWrapper)
        banner.setWordWrap(True)
        banner.setMinimumHeight(50)
        banner.setContentsMargins(14, 8, 14, 8)
        banner.setStyleSheet(
            f"QLabel {{ color: #087B70; background: {_ACCENT_SOFT}; "
            "border: 1px solid #BFECE5; border-radius: 7px; }"
        )
        _setChineseUiFont(banner, 9)
        bannerLayout.addWidget(banner)
        self.groupLayout.addWidget(bannerWrapper)
        return banner


class DisplaySettingWidget(OverviewGroupCard):
    """界面主题与缩放设置。"""

    _THEME_OPTIONS = (
        ("跟随系统（推荐）", Theme.AUTO),
        ("明亮", Theme.LIGHT),
        ("暗黑", Theme.DARK),
    )

    _SCALE_OPTIONS = (
        ("跟随系统（推荐）", "Auto"),
        ("100%", 1.0),
        ("125%", 1.25),
        ("150%", 1.5),
        ("175%", 1.75),
        ("200%", 2.0),
    )

    def __init__(self, parent=None):
        super().__init__(
            "外观与缩放",
            FluentIcon.PALETTE,
            "主题 · Windows DPI",
            parent,
        )
        self.themeModeComboBox = ComboBox(self)
        for label, value in self._THEME_OPTIONS:
            self.themeModeComboBox.addItem(label, userData=value)
        self._syncThemeSelection()
        self.themeModeComboBox.setFixedSize(172, 32)
        self.themeModeComboBox.setAccessibleName("界面主题模式")
        self.themeModeComboBox.setAccessibleDescription(
            "选择明亮、暗黑或跟随 Windows 系统主题"
        )
        self.themeModeComboBox.currentIndexChanged.connect(
            self._onThemeModeChanged
        )

        self.dpiScaleComboBox = ComboBox(self)
        for label, value in self._SCALE_OPTIONS:
            self.dpiScaleComboBox.addItem(label, userData=value)
        currentIndex = self.dpiScaleComboBox.findData(qconfig.get(cfg.DpiScale))
        self.dpiScaleComboBox.setCurrentIndex(max(0, currentIndex))
        self.dpiScaleComboBox.setFixedSize(172, 32)
        self.dpiScaleComboBox.setAccessibleName("界面显示缩放比例")
        self.dpiScaleComboBox.currentIndexChanged.connect(self._onDpiScaleChanged)

        self.addGroup(
            FluentIcon.BRIGHTNESS,
            "界面主题",
            "立即切换明暗外观；跟随系统会随 Windows 主题自动变化",
            self.themeModeComboBox,
        )
        self.addGroup(
            FluentIcon.LAYOUT,
            "界面缩放",
            "自动模式会跟随 Windows，并在不同 DPI 的显示器之间平滑切换",
            self.dpiScaleComboBox,
        )
        self.groupWidgets[0].setSeparatorVisible(True)
        QWidget.setTabOrder(self.themeModeComboBox, self.dpiScaleComboBox)
        self.addInfoBanner(
            "小屏幕或高分屏建议使用“跟随系统”。修改缩放比例后需重启软件，"
            "主窗口仍会自动限制在当前屏幕的可用区域内。"
        )
        qconfig.themeChangedFinished.connect(self._syncThemeSelection)

    def _onThemeModeChanged(self, _index: int) -> None:
        theme = self.themeModeComboBox.currentData()
        if not isinstance(theme, Theme) or theme == qconfig.get(cfg.themeMode):
            return
        setTheme(theme, save=True)

    def _syncThemeSelection(self, *_args) -> None:
        currentTheme = qconfig.get(cfg.themeMode)
        currentIndex = self.themeModeComboBox.findData(currentTheme)
        if currentIndex < 0 or currentIndex == self.themeModeComboBox.currentIndex():
            return
        self.themeModeComboBox.blockSignals(True)
        self.themeModeComboBox.setCurrentIndex(currentIndex)
        self.themeModeComboBox.blockSignals(False)

    def _onDpiScaleChanged(self, _index: int) -> None:
        scale = self.dpiScaleComboBox.currentData()
        if scale == qconfig.get(cfg.DpiScale):
            return
        qconfig.set(cfg.DpiScale, scale)
        InfoBar.info(
            title="重启后生效",
            content="界面缩放比例已保存，重启软件后将应用新的显示设置。",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self.window(),
        )


class AnalysisSettingWidget(OverviewGroupCard):
    """所有分析页面共用的规则设置。"""

    def __init__(self, parent=None):
        super().__init__(
            "分析设置",
            FluentIcon.DICTIONARY,
            "全局停用词规则",
            parent,
        )

        self.stopwordSwitch = SwitchButton("", self)
        self.stopwordSwitch.setOnText("已启用")
        self.stopwordSwitch.setOffText("已停用")
        self.stopwordSwitch.setChecked(stopwordService.isEnabled())
        self.stopwordSwitch.setAccessibleName("启用全局停用词过滤")
        self.stopwordSwitch.checkedChanged.connect(
            self._onStopwordEnabledChanged
        )

        self.stopwordCountLabel = CaptionLabel(self)
        self.stopwordCountLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stopwordCountLabel.setMinimumWidth(68)
        self.stopwordCountLabel.setFixedHeight(26)
        self.manageStopwordsButton = PushButton("管理停用词", self)
        self.manageStopwordsButton.setIcon(FluentIcon.DICTIONARY)
        self.manageStopwordsButton.setFixedHeight(32)
        self.manageStopwordsButton.setAccessibleName("打开全局停用词管理器")
        self.manageStopwordsButton.clicked.connect(self._openStopwordManager)

        filterIcon = getattr(FluentIcon, "FILTER", FluentIcon.SEARCH)
        self.addGroup(
            filterIcon,
            "停用词过滤",
            "统一应用于词频、关键词和共现网络分析；修改后对新任务生效",
            self.stopwordSwitch,
        )
        self.addGroup(
            FluentIcon.DICTIONARY,
            "停用词词表",
            "集中导入、编辑、恢复默认或导出当前词表",
            self._buildStopwordAction(),
        )

        self._refreshStopwordSummary()
        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)

    def _buildStopwordAction(self) -> QWidget:
        wrapper = _createTransparentActionWidget(self)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.stopwordCountLabel)
        layout.addWidget(self.manageStopwordsButton)
        return wrapper

    def _onStopwordEnabledChanged(self, isChecked: bool) -> None:
        stopwordService.setEnabled(isChecked)
        self._refreshStopwordSummary()

    def _openStopwordManager(self) -> None:
        from app.view.widgets.freq_analyzer.dialogs import StopwordsDialog

        result = StopwordsDialog.edit(
            currentWords=stopwordService.words(),
            parent=self.window(),
        )
        if result is None:
            return
        savedWords = stopwordService.saveWords(result)
        self._refreshStopwordSummary()
        stateHint = (
            "已应用于后续分析任务"
            if stopwordService.isEnabled()
            else "当前过滤停用，可在上方启用"
        )
        InfoBar.success(
            title="停用词已保存",
            content=f"共 {len(savedWords)} 个，{stateHint}。",
            parent=self.window(),
            position=InfoBarPosition.TOP,
            duration=2500,
        )

    def _refreshStopwordSummary(self) -> None:
        wordCount = len(stopwordService.words())
        stateText = "已启用" if stopwordService.isEnabled() else "已停用"
        self.stopwordCountLabel.setText(f"{wordCount} 个")
        self.setHeaderSummary(f"{stateText} · {wordCount} 个停用词")

    def _applyTheme(self, *_args) -> None:
        palette = shellPalette()
        self.stopwordCountLabel.setStyleSheet(
            "QLabel {"
            f" color: {palette.accentText.name()};"
            f" background: {palette.accentSurface.name()};"
            f" border: 1px solid {palette.border.name()};"
            " border-radius: 10px; padding: 2px 8px;"
            "}"
        )


class SoftwareSettingWidget(OverviewGroupCard):
    """软件设置组件"""

    def __init__(self, parent=None):
        super().__init__("下载功能设置", FluentIcon.DOWNLOAD, "HskDataFetcher", parent)

        # 下载保存路径按钮
        self.downloadPathButton = PushButton("选择保存路径", self)
        self.downloadPathButton.setIcon(FluentIcon.FOLDER)
        self.downloadPathButton.setFixedHeight(32)
        self.downloadPathButton.clicked.connect(self._selectDownloadPath)
        self.downloadPathLabel = CaptionLabel(
            qconfig.get(cfg.DownloadSavePath), self
        )
        self.downloadPathLabel.setToolTip(qconfig.get(cfg.DownloadSavePath))
        self.downloadPathLabel.setMinimumWidth(0)
        self.downloadPathLabel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        pathFont = QFont("Cascadia Mono")
        pathFont.setFamilies(["Cascadia Mono", "Consolas", "Microsoft YaHei UI"])
        pathFont.setPointSize(9)
        self.downloadPathLabel.setFont(pathFont)
        self.downloadPathLabel.setWordWrap(True)
        self.downloadPathLabel.setStyleSheet(_pathDisplayStyle())

        # 每页数量选择
        self.pageNumsComboBox = ComboBox(self)
        self._fillNumericCombo(
            self.pageNumsComboBox,
            (10, 20, 50, 100),
            qconfig.get(cfg.NumberPerDownloads),
            "{} 条 / 页",
        )
        self.pageNumsComboBox.currentIndexChanged.connect(self._onPageNumsChanged)

        # 下载线程数选择
        self.threadsComboBox = ComboBox(self)
        self._fillNumericCombo(
            self.threadsComboBox,
            range(1, 7),
            qconfig.get(cfg.ThreadPerDownloads),
            "{} 线程",
        )
        self.threadsComboBox.currentIndexChanged.connect(self._onThreadsChanged)

        # 最大重试次数选择
        self.maxTriesComboBox = ComboBox(self)
        self._fillNumericCombo(
            self.maxTriesComboBox,
            range(1, 11),
            qconfig.get(cfg.MaximumAttempts),
            "{} 次",
        )
        self.maxTriesComboBox.currentIndexChanged.connect(self._onMaxTriesChanged)

        # HSK Token刷新按钮
        self.hskRefreshButton = PushButton("刷新", self)
        self.hskRefreshButton.setIcon(FluentIcon.SYNC)
        self.hskRefreshButton.setFixedHeight(32)
        self.hskRefreshButton.clicked.connect(self._onHskRefresh)
        self.hskTokenBadge = SettingStatusBadge(bool(qconfig.get(cfg.HSKLoginToken)), self)

        # Global Token刷新按钮
        self.globalRefreshButton = PushButton("刷新", self)
        self.globalRefreshButton.setIcon(FluentIcon.SYNC)
        self.globalRefreshButton.setFixedHeight(32)
        self.globalRefreshButton.clicked.connect(self._onGlobalRefresh)
        self.globalTokenBadge = SettingStatusBadge(
            bool(qconfig.get(cfg.GlobalLoginToken)), self
        )

        # HSK 作文数据库资源校验
        self.resourceVerifyButton = PushButton("校验资源", self)
        self.resourceVerifyButton.setIcon(FluentIcon.CHECKBOX)
        self.resourceVerifyButton.setFixedHeight(32)
        self.resourceVerifyButton.setAccessibleName("校验 HSK 作文资源文件")
        self.resourceVerifyButton.clicked.connect(self._onResourceActionClicked)
        self.resourceVerifyBadge = SettingStatusBadge(False, self)
        self.resourceVerifyBadge.setConfigured(False, missingText="待校验")
        self._resourceService = StartupDatabaseService()

        # 添加设置组
        self._addSettingGroups()

        for combo in (
            self.pageNumsComboBox,
            self.threadsComboBox,
            self.maxTriesComboBox,
        ):
            combo.setFixedSize(140, 32)
        qconfig.themeChangedFinished.connect(self._applyTheme)

    def _applyTheme(self, *_args) -> None:
        self.downloadPathLabel.setStyleSheet(_pathDisplayStyle())

    @staticmethod
    def _fillNumericCombo(combo, values, current, labelTemplate):
        for value in values:
            combo.addItem(labelTemplate.format(value), userData=value)
        index = combo.findData(current)
        combo.setCurrentIndex(max(0, index))

    def _buildPathAction(self) -> QWidget:
        wrapper = _createTransparentActionWidget(self)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.downloadPathLabel)
        layout.addWidget(self.downloadPathButton)
        return wrapper

    def _buildTokenAction(self, badge, button) -> QWidget:
        wrapper = _createTransparentActionWidget(self)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(badge)
        layout.addWidget(button)
        return wrapper

    def _buildResourceAction(self) -> QWidget:
        wrapper = _createTransparentActionWidget(self)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.resourceVerifyBadge)
        layout.addWidget(self.resourceVerifyButton)
        return wrapper

    def _addSettingGroups(self):
        """添加设置组"""
        self.addGroup(
            FluentIcon.FOLDER,
            "保存路径",
            "语料文件、词表与图片的本地存储根目录",
            self._buildPathAction(),
        )
        self.addGroup(
            FluentIcon.MENU,
            "每页检索数量",
            "单次请求返回的语料条目上限",
            self.pageNumsComboBox,
        )
        self.addGroup(
            FluentIcon.LIBRARY,
            "下载线程数",
            "并发拉取任务的工作线程数 · 1 ～ 6",
            self.threadsComboBox,
        )
        self.addGroup(
            FluentIcon.SYNC,
            "最大尝试次数",
            "下载失败后的最大重试次数",
            self.maxTriesComboBox,
        )
        self.addGroup(
            FluentIcon.VPN,
            "HSK Token",
            "用于访问 HSK 语料接口的身份令牌",
            self._buildTokenAction(self.hskTokenBadge, self.hskRefreshButton),
        )
        self.addGroup(
            FluentIcon.IOT,
            "Global Token",
            "用于访问公共语料网关的身份令牌",
            self._buildTokenAction(self.globalTokenBadge, self.globalRefreshButton),
        )
        self.addGroup(
            FluentIcon.CHECKBOX,
            "资源文件校验",
            "检查 HSK 作文数据表与正文库的 SQLite 完整性",
            self._buildResourceAction(),
        )

    def _onResourceActionClicked(self) -> None:
        """在 Fluent MessageBoxBase 中执行校验、修复与复检。"""
        dialog = ResourceVerificationDialog(
            self._resourceService,
            self.window(),
        )
        dialog.exec()
        if not dialog.hasVerified:
            return
        if dialog.allResourcesValid:
            self.resourceVerifyBadge.setConfigured(True, configuredText="完整")
            self.resourceVerifyButton.setText("再次校验")
            return
        self.resourceVerifyBadge.setConfigured(False, missingText="需修复")
        self.resourceVerifyButton.setText(
            "再次校验" if INTERNAL_TEST_MODE else "继续处理"
        )

    def _showSuccessMessage(self, title: str, content: str):
        """显示成功提示"""
        InfoBar.success(
            title,
            content,
            Qt.Orientation.Horizontal,
            True,
            2000,
            InfoBarPosition.TOP_RIGHT,
            self.window(),
        )

    def _showErrorMessage(self, title: str, content: str):
        """显示错误提示"""
        InfoBar.error(
            title,
            content,
            Qt.Orientation.Horizontal,
            True,
            3000,
            InfoBarPosition.TOP_RIGHT,
            self.window(),
        )

    def _selectDownloadPath(self):
        """选择下载保存路径"""
        folderPath = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folderPath:
            qconfig.set(cfg.DownloadSavePath, folderPath)
            self.downloadPathLabel.setText(folderPath)
            self.downloadPathLabel.setToolTip(folderPath)
            logger.info(f"[Setting] 下载保存路径已修改: {folderPath}")
            self._showSuccessMessage("O(∩_∩)O 修改成功", "下载保存路径修改成功")
        else:
            logger.debug("[Setting] 用户取消选择下载保存路径")

    def _onPageNumsChanged(self, *args):
        """每页数量变更处理"""
        value = self.pageNumsComboBox.currentData()
        if value is None:
            return
        qconfig.set(cfg.NumberPerDownloads, value)
        logger.info(f"[Setting] 每页检索数量已修改: {value}")
        self._showSuccessMessage("O(∩_∩)O 修改成功", "每页检索数量修改成功")

    def _onThreadsChanged(self, *args):
        """线程数变更处理"""
        value = self.threadsComboBox.currentData()
        if value is None:
            return
        qconfig.set(cfg.ThreadPerDownloads, value)
        logger.info(f"[Setting] 下载线程数量已修改: {value}")
        self._showSuccessMessage("O(∩_∩)O 修改成功", "线程数量修改成功")

    def _onMaxTriesChanged(self, *args):
        """最大重试次数变更处理"""
        value = self.maxTriesComboBox.currentData()
        if value is None:
            return
        qconfig.set(cfg.MaximumAttempts, value)
        logger.info(f"[Setting] 最大尝试次数已修改: {value}")
        self._showSuccessMessage("O(∩_∩)O 修改成功", "最大尝试次数修改成功")

    def _onHskRefresh(self):
        """刷新HSK Token"""
        logger.info("[Setting] 开始刷新HSK Token...")

        # 获取已保存的凭证
        savedUsername = qconfig.get(cfg.HSKLoginUsername)
        savedPassword = qconfig.get(cfg.HSKLoginPassword)

        # 显示登录对话框，自动填充已保存的凭证
        from app.view.widgets.token_refresh_dialog import TokenRefreshDialog

        dialog = TokenRefreshDialog(
            "HSK登录", savedUsername or "", savedPassword or "", self.window()
        )
        dialog.usernameEdit.setPlaceholderText("请输入HSK账号邮箱")
        dialog.passwordEdit.setPlaceholderText("请输入HSK密码")

        if dialog.exec():
            credentials = dialog.getCredentials()
            username = credentials["username"]
            password = credentials["password"]

            if not username or not password:
                self._showErrorMessage("输入错误", "用户名和密码不能为空")
                return

            # 保存用户名密码到配置
            qconfig.set(cfg.HSKLoginUsername, username)
            qconfig.set(cfg.HSKLoginPassword, password)

            self.hskRefreshButton.setEnabled(False)
            self.hskRefreshButton.setText("刷新中...")

            # 创建并启动线程，传入凭证
            self.hskThread = HskTokenRefreshThread(username, password)
            self.hskThread.finished.connect(self._onHskRefreshFinished)
            self.hskThread.error.connect(self._onHskRefreshError)
            self.hskThread.start()
        else:
            logger.info("[Setting] 用户取消HSK Token刷新")

    def _onHskRefreshFinished(self, token: str):
        """HSK Token刷新完成"""
        self.hskRefreshButton.setEnabled(True)
        self.hskRefreshButton.setText("刷新")

        # 保存Token
        qconfig.set(cfg.HSKLoginToken, token)

        self.hskTokenBadge.setConfigured(True)

        # 发送信号
        signalBus.hskTokenRefreshSignal.emit(token)

        logger.info("[Setting] HSK Token刷新并保存成功")
        self._showSuccessMessage("O(∩_∩)O 刷新成功", "HSK-Token已刷新")

    def _onHskRefreshError(self, error: str):
        """HSK Token刷新错误"""
        self.hskRefreshButton.setEnabled(True)
        self.hskRefreshButton.setText("刷新")

        logger.error(f"[Setting] HSK Token刷新失败: {error}")
        self._showErrorMessage("刷新失败", error)

    def _onGlobalRefresh(self):
        """刷新Global Token"""
        logger.info("[Setting] 开始刷新Global Token...")

        # 获取已保存的凭证
        savedUserId = qconfig.get(cfg.GlobalLoginUsername)
        savedPassword = qconfig.get(cfg.GlobalLoginPassword)

        # 显示登录对话框，自动填充已保存的凭证
        from app.view.widgets.token_refresh_dialog import TokenRefreshDialog

        dialog = TokenRefreshDialog(
            "Global登录", savedUserId or "", savedPassword or "", self.window()
        )
        dialog.usernameEdit.setPlaceholderText("请输入Global UserID")
        dialog.passwordEdit.setPlaceholderText("请输入Global Password")

        if dialog.exec():
            credentials = dialog.getCredentials()
            userId = credentials["username"]
            password = credentials["password"]

            if not userId or not password:
                self._showErrorMessage("输入错误", "UserID和密码不能为空")
                return

            # 保存凭证到配置
            qconfig.set(cfg.GlobalLoginUsername, userId)
            qconfig.set(cfg.GlobalLoginPassword, password)

            self.globalRefreshButton.setEnabled(False)
            self.globalRefreshButton.setText("刷新中...")

            # 创建并启动线程
            self.globalThread = GlobalTokenRefreshThread(userId, password)
            self.globalThread.finished.connect(self._onGlobalRefreshFinished)
            self.globalThread.error.connect(self._onGlobalRefreshError)
            self.globalThread.start()
        else:
            logger.info("[Setting] 用户取消Global Token刷新")

    def _onGlobalRefreshFinished(self, token: str):
        """Global Token刷新完成"""
        self.globalRefreshButton.setEnabled(True)
        self.globalRefreshButton.setText("刷新")

        # 保存Token
        qconfig.set(cfg.GlobalLoginToken, token)

        self.globalTokenBadge.setConfigured(True)

        # 发送信号
        signalBus.globalTokenRefreshSignal.emit(token)

        logger.info("[Setting] Global Token刷新并保存成功")
        self._showSuccessMessage("O(∩_∩)O 刷新成功", "Global-Token已刷新")

    def _onGlobalRefreshError(self, error: str):
        """Global Token刷新错误"""
        self.globalRefreshButton.setEnabled(True)
        self.globalRefreshButton.setText("刷新")

        logger.error(f"[Setting] Global Token刷新失败: {error}")
        self._showErrorMessage("刷新失败", error)


class AiInsightSettingWidget(OverviewGroupCard):
    """AI 解读设置组件（PRD-001 REQ-AI-001）

    与「AI 聊天设置」共用同一套 LLM 配置（API Key / Base URL / 模型 ID），
    本卡只暴露解读独有的设置项:
        - 解读风格（学术 / 通俗 / 简洁）

    注意：API Key / Base URL / 模型 请在「AI 聊天设置」中配置，本卡不重复。
    """

    def __init__(self, parent=None):
        super().__init__("AI 解读设置", FluentIcon.EXPRESSIVE_INPUT_ENTRY, "语料解读风格", parent)

        # ---- 字段 ----
        self.styleCombo = ComboBox()
        styleLabels = {
            "学术": "学术 · 引经据典术语规范",
            "通俗": "通俗 · 清晰自然易理解",
            "简洁": "简洁 · 聚焦核心结论",
        }
        for value, label in styleLabels.items():
            self.styleCombo.addItem(label, userData=value)
        currentStyle = qconfig.get(cfg.AiInsightStyle) or "学术"
        self.styleCombo.setCurrentIndex(max(0, self.styleCombo.findData(currentStyle)))
        self.styleCombo.setFixedSize(184, 32)
        self.styleCombo.currentIndexChanged.connect(
            lambda _index: qconfig.set(
                cfg.AiInsightStyle, self.styleCombo.currentData()
            )
        )

        # ---- 添加设置组 ----
        self.addGroup(
            FluentIcon.PALETTE,
            "解读风格",
            "控制 AI 解读语料时的语气与详细程度",
            self.styleCombo,
        )
        self.statusLabel = self.addInfoBanner(self._summaryText())

        # 字段变化 → 刷新状态条
        self.styleCombo.currentIndexChanged.connect(self._refreshStatus)

    def _refreshStatus(self, *_args) -> None:
        self.statusLabel.setText(self._summaryText())

    def _summaryText(self) -> str:
        return "ⓘ  AI 解读使用 Prismatica 平台模型，按服务端记录的输入与输出 Token 独立计费。"


class AiChatSettingWidget(OverviewGroupCard):
    """AI 聊天设置组件

    平台统一保管 API Key 与模型配置；用户只配置多轮上下文和系统提示词。

    所有配置项通过 ``qconfig`` 持久化,与 ``cfg`` 中对应键双向同步,
    设置变更后底部状态条实时刷新摘要。
    """

    def __init__(self, parent=None):
        super().__init__("AI 聊天", FluentIcon.CHAT, "平台 AI · Token 计费", parent)

        self.platformAiLabel = CaptionLabel("由 Prismatica 云端安全提供，无需填写 API Key")

        self.maxHistoryCombo = ComboBox()
        for n in (5, 10, 20, 50):
            self.maxHistoryCombo.addItem(f"{n} 轮", userData=n)
        historyIndex = self.maxHistoryCombo.findData(
            qconfig.get(cfg.AiMaxHistory) or 10
        )
        self.maxHistoryCombo.setCurrentIndex(max(0, historyIndex))
        self.maxHistoryCombo.setFixedSize(140, 32)
        self.maxHistoryCombo.currentIndexChanged.connect(
            lambda _index: qconfig.set(
                cfg.AiMaxHistory, self.maxHistoryCombo.currentData()
            )
        )

        # 系统提示词:不再用输入框,改为本地文件上传(.txt / .md / .json 等文本文件)
        self.systemPromptFileLabel = CaptionLabel(self._systemPromptText())
        self.systemPromptFileLabel.setMinimumWidth(0)
        self.systemPromptFileLabel.setMinimumHeight(32)
        self.systemPromptFileLabel.setMaximumWidth(297)
        self.systemPromptFileLabel.setWordWrap(True)
        self.systemPromptFileLabel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.systemPromptFileLabel.setAccessibleName("当前系统提示词文件路径")
        self.systemPromptFileLabel.setToolTip(self._systemPromptText())
        promptPathFont = QFont("Cascadia Mono")
        promptPathFont.setFamilies(
            ["Cascadia Mono", "Consolas", "Microsoft YaHei UI"]
        )
        promptPathFont.setPointSize(9)
        self.systemPromptFileLabel.setFont(promptPathFont)
        self.systemPromptFileButton = PushButton("选择提示词")
        self.systemPromptFileButton.setIcon(FluentIcon.DOCUMENT)
        self.systemPromptFileButton.setFixedSize(152, 32)
        self.systemPromptClearButton = PushButton("清除")
        self.systemPromptClearButton.setIcon(FluentIcon.CLOSE)
        self.systemPromptClearButton.setFixedSize(76, 32)
        self.systemPromptFileButton.clicked.connect(self._chooseSystemPromptFile)
        self.systemPromptClearButton.clicked.connect(self._clearSystemPromptFile)

        # ---- 状态展示 ----
        self.statusLabel = CaptionLabel("当前 LLM:")

        # ---- 添加设置组 ----
        self.addGroup(
            FluentIcon.CLOUD,
            "平台 AI 服务",
            "API Key 和模型仅由云端保管；每次调用按真实 Token 账单结算",
            self.platformAiLabel,
        )
        self.addGroup(
            FluentIcon.HISTORY,
            "历史轮数",
            "聊天上下文中保留的最大消息往返数",
            self.maxHistoryCombo,
        )
        systemPromptGroup = self.addGroup(
            FluentIcon.DOCUMENT,
            "系统提示词",
            "作为AI提示词文件",
            self._buildSystemPromptWidget(),
        )
        systemPromptGroup.contentLabel.setWordWrap(True)
        systemPromptGroup.setMinimumHeight(92)

        # ---- 设计稿中的紧凑状态栏 ----
        self.statusFooter = QFrame(self.view)
        self.statusFooter.setObjectName("aiStatusFooter")
        self.statusFooter.setMinimumHeight(44)
        self.statusFooter.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.statusRowLayout = QHBoxLayout(self.statusFooter)
        self.statusRowLayout.setContentsMargins(24, 10, 24, 10)
        self.statusRowLayout.setSpacing(8)
        statusIcon = IconWidget(_accentIcon(FluentIcon.SPEED_MEDIUM), self.statusFooter)
        statusIcon.setFixedSize(16, 16)
        self.statusRowLayout.addWidget(statusIcon)
        self.statusRowLayout.addWidget(self.statusLabel)
        self.modelPill = QLabel(self.statusFooter)
        self.historyPrefixLabel = CaptionLabel("·  历史", self.statusFooter)
        self.historyPill = QLabel(self.statusFooter)
        self.statusRowLayout.addWidget(self.modelPill)
        self.statusRowLayout.addWidget(self.historyPrefixLabel)
        self.statusRowLayout.addWidget(self.historyPill)
        self.statusRowLayout.addStretch(1)
        self.effectiveLabel = CaptionLabel("设置保存后立即生效", self.statusFooter)
        self.statusRowLayout.addWidget(self.effectiveLabel)
        self.groupLayout.addWidget(self.statusFooter)

        # 字段变更 → 刷新状态条
        for sig in (self.maxHistoryCombo.currentIndexChanged,):
            sig.connect(self._refreshStatus)

        self._refreshStatus()
        self._applyStatusTheme()
        qconfig.themeChangedFinished.connect(self._applyStatusTheme)

    def _applyStatusTheme(self, *_args) -> None:
        palette = shellPalette()
        self.systemPromptFileLabel.setStyleSheet(_pathDisplayStyle(palette))
        self.statusFooter.setStyleSheet(
            f"#aiStatusFooter {{ background: {palette.surfaceAlt.name()}; "
            f"border-top: 1px solid {palette.border.name()}; "
            "border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; }"
        )
        pillStyle = (
            f"QLabel {{ color: {palette.text.name()}; "
            f"background: {palette.surface.name()}; "
            f"border: 1px solid {palette.border.name()}; "
            "border-radius: 10px; padding: 2px 8px; }"
        )
        self.modelPill.setStyleSheet(pillStyle)
        self.historyPill.setStyleSheet(pillStyle)
        for label in (
            self.platformAiLabel,
            self.statusLabel,
            self.historyPrefixLabel,
            self.effectiveLabel,
        ):
            label.setStyleSheet(f"color: {palette.mutedText.name()};")

    def setCompactLayout(self, isCompact: bool) -> None:
        super().setCompactLayout(isCompact)
        self.systemPromptLayout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if isCompact
            else QBoxLayout.Direction.LeftToRight
        )
        self.systemPromptFileLabel.setMaximumWidth(
            16777215 if isCompact else 297
        )
        self.systemPromptLayout.setAlignment(
            self.systemPromptButtonHost, Qt.AlignmentFlag.AlignLeft
        )
        self.systemPromptLayout.invalidate()
        self.systemPromptFileLabel.updateGeometry()
        self.statusRowLayout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if isCompact
            else QBoxLayout.Direction.LeftToRight
        )
        self.statusRowLayout.setContentsMargins(
            16 if isCompact else 24,
            12 if isCompact else 10,
            16 if isCompact else 24,
            12 if isCompact else 10,
        )
        for label in (self.statusLabel, self.effectiveLabel):
            label.setWordWrap(isCompact)
            label.setMinimumWidth(0)
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
        self.statusFooter.updateGeometry()

    def _refreshStatus(self, *_args) -> None:
        maxHistory = qconfig.get(cfg.AiMaxHistory) or 10
        self.modelPill.setText("平台模型")
        self.historyPill.setText(f"{maxHistory} 轮")
        self.setHeaderSummary("平台 AI · Token 计费")

    def _summaryText(self) -> str:
        maxHist = qconfig.get(cfg.AiMaxHistory) or 10
        return f"平台 AI · 历史 {maxHist} 轮 · 按真实 Token 计费"

    # ---- 系统提示词(文件上传)----
    def _buildSystemPromptWidget(self) -> QWidget:
        """组装系统提示词控件:状态标签 + 选择/清除按钮"""
        wrapper = _createTransparentActionWidget(self)
        wrapper.setMinimumWidth(0)
        wrapper.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.systemPromptLayout = QHBoxLayout(wrapper)
        self.systemPromptLayout.setContentsMargins(0, 0, 0, 0)
        self.systemPromptLayout.setSpacing(8)
        self.systemPromptLayout.addWidget(self.systemPromptFileLabel, 1)

        self.systemPromptButtonHost = _createTransparentActionWidget(wrapper)
        buttonLayout = QHBoxLayout(self.systemPromptButtonHost)
        buttonLayout.setContentsMargins(0, 0, 0, 0)
        buttonLayout.setSpacing(8)
        buttonLayout.addWidget(self.systemPromptFileButton)
        buttonLayout.addWidget(self.systemPromptClearButton)
        self.systemPromptLayout.addWidget(self.systemPromptButtonHost)
        return wrapper

    def _systemPromptText(self) -> str:
        """当前已选择的提示词文件路径(简短描述)"""
        raw = (qconfig.get(cfg.AiSystemPrompt) or "").strip()
        if not raw:
            return "默认提示词"
        if "\n" in raw or not Path(raw).is_file():
            # 早期版本残留的纯文本,提示用户清除或重新选择
            firstLine = raw.splitlines()[0][:60]
            return f"检测到旧版文本提示词(首行:{firstLine}…),建议清除后重新选择文件。"
        return raw

    def _chooseSystemPromptFile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择系统提示词文件",
            "",
            "文本文件 (*.txt *.md *.json *.yaml *.yml);;所有文件 (*)",
        )
        if not path:
            return
        try:
            # 读取一次以校验可访问性(以及提前排除空文件)
            content = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
            if not content:
                InfoBar.warning(
                    title="",
                    content="所选文件为空,将使用默认提示词。",
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                    duration=2500,
                )
        except OSError as e:
            logger.error(f"[Setting] 读取提示词文件失败: {e}")
            InfoBar.error(
                title="读取失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=3000,
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return
        qconfig.set(cfg.AiSystemPrompt, path)
        self.systemPromptFileLabel.setText(self._systemPromptText())
        self.systemPromptFileLabel.setToolTip(path)
        self.systemPromptFileLabel.updateGeometry()
        InfoBar.success(
            title="",
            content=f"已设置提示词文件:{Path(path).name}",
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=2500,
        )

    def _clearSystemPromptFile(self) -> None:
        qconfig.set(cfg.AiSystemPrompt, "")
        self.systemPromptFileLabel.setText(self._systemPromptText())
        self.systemPromptFileLabel.setToolTip(self._systemPromptText())
        self.systemPromptFileLabel.updateGeometry()
        InfoBar.success(
            title="",
            content="已清除提示词文件,将使用默认提示词。",
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=2500,
        )




class AboutSettingWidget(OverviewGroupCard):
    """关于软件设置组件"""

    def __init__(self, parent=None):
        # 导入设置信息
        from app.core.utils.setting import VERSION, APP_NAME, YEAR
        super().__init__("关于软件", FluentIcon.INFO, f"{APP_NAME} · Desktop Client", parent)

        # 重新查看引导按钮(2026-07-28 新增)
        # - 点击后会重置 cfg.MainTourShown=False,并在主窗口上重新弹出引导遮罩
        self.retourButton = PrimaryPushButton("重新查看引导", self)
        self.retourButton.setIcon(FluentIcon.PLAY.icon(color=QColor("white")))
        self.retourButton.setFixedHeight(32)
        self.retourButton.clicked.connect(self._restartMainTour)

        self.openBadge = SettingStatusBadge(True, self)
        self.openBadge.setConfigured(True, "内测开放")

        # 获取系统信息
        systemInfo = systemInfoService.getItems()
        displayVersion = VERSION if str(VERSION).lower().startswith("v") else f"v{VERSION}"

        # 添加版本号组
        self.addGroup(
            FluentIcon.ZIP_FOLDER,
            f"棱溯客户端 {displayVersion}",
            f"{YEAR} · 贵州六棱光界科技工作室",
            self.openBadge,
        )

        # 添加「重新查看引导」组(2026-07-28 新增)
        self.addGroup(
            FluentIcon.LAYOUT,
            "主窗口引导",
            "重新播放首次启动时的功能引导，熟悉主界面布局",
            self.retourButton,
        )

        self.groupWidgets[-1].setSeparatorVisible(True)
        self.systemInfoSection = self._buildSystemInfoSection(systemInfo)
        self.groupLayout.addWidget(self.systemInfoSection)

    def _buildSystemInfoSection(self, infoItems) -> QWidget:
        section = QWidget(self.view)
        sectionLayout = QVBoxLayout(section)
        sectionLayout.setContentsMargins(24, 18, 24, 18)
        sectionLayout.setSpacing(10)

        header = QWidget(section)
        headerLayout = QHBoxLayout(header)
        headerLayout.setContentsMargins(0, 0, 0, 0)
        headerLayout.setSpacing(8)
        icon = IconWidget(_accentIcon(FluentIcon.DEVELOPER_TOOLS), header)
        icon.setFixedSize(16, 16)
        title = BodyLabel("系统信息", header)
        _setChineseUiFont(title, 10, QFont.Weight.DemiBold)
        healthBadge = SettingStatusBadge(True, header)
        healthBadge.setConfigured(True, "健康")
        headerLayout.addWidget(icon)
        headerLayout.addWidget(title)
        headerLayout.addStretch(1)
        headerLayout.addWidget(healthBadge)
        sectionLayout.addWidget(header)

        gridWidget = QWidget(section)
        self.systemInfoGrid = QGridLayout(gridWidget)
        self.systemInfoGrid.setContentsMargins(0, 0, 0, 0)
        self.systemInfoGrid.setHorizontalSpacing(24)
        self.systemInfoGrid.setVerticalSpacing(4)
        self.systemInfoCells = []
        self.systemInfoKeyLabels = []
        self.systemInfoValueLabels = []
        for index, (key, value) in enumerate(infoItems):
            cell = QFrame(gridWidget)
            cell.setStyleSheet(
                "QFrame { border: none; border-bottom: 1px dashed #E5E5E5; }"
            )
            cellLayout = QHBoxLayout(cell)
            cellLayout.setContentsMargins(0, 6, 0, 6)
            keyLabel = CaptionLabel(key, cell)
            keyLabel.setMinimumWidth(0)
            keyLabel.setWordWrap(True)
            keyLabel.setStyleSheet(f"color: {_MUTED}; border: none;")
            valueLabel = CaptionLabel(value, cell)
            valueLabel.setMinimumWidth(0)
            valueLabel.setWordWrap(True)
            valueLabel.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            valueLabel.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            valueLabel.setStyleSheet(f"color: {_TEXT}; border: none;")
            valueLabel.setAccessibleName(f"{key}信息")
            cellLayout.addWidget(keyLabel, 0)
            cellLayout.addWidget(valueLabel, 1)
            self.systemInfoCells.append(cell)
            self.systemInfoKeyLabels.append(keyLabel)
            self.systemInfoValueLabels.append(valueLabel)
            self.systemInfoGrid.addWidget(cell, index // 2, index % 2)
        sectionLayout.addWidget(gridWidget)
        self._applySystemInfoTheme()
        qconfig.themeChangedFinished.connect(self._applySystemInfoTheme)
        return section

    def _applySystemInfoTheme(self, *_args) -> None:
        palette = shellPalette()
        for cell in self.systemInfoCells:
            cell.setStyleSheet(
                "QFrame { border: none; "
                f"border-bottom: 1px dashed {palette.border.name()}; }}"
            )
        for label in self.systemInfoKeyLabels:
            label.setStyleSheet(
                f"color: {palette.mutedText.name()}; border: none;"
            )
        for label in self.systemInfoValueLabels:
            label.setStyleSheet(f"color: {palette.text.name()}; border: none;")

    def setCompactLayout(self, isCompact: bool) -> None:
        super().setCompactLayout(isCompact)
        for index, cell in enumerate(self.systemInfoCells):
            row = index if isCompact else index // 2
            column = 0 if isCompact else index % 2
            self.systemInfoGrid.addWidget(cell, row, column)
        self.systemInfoGrid.invalidate()
        self.systemInfoSection.updateGeometry()

    def _restartMainTour(self):
        """重新启动主窗口引导遮罩(2026-07-28 新增)。

        流程:
            1) 把 cfg.MainTourShown 设为 False(允许下次启动也弹)
            2) 在主窗口上构造并启动 MainTourOverlay
            3) 提示用户引导已开始,可在「跳过引导」中提前结束
        """
        try:
            # 1) 重置持久化标记
            qconfig.set(cfg.MainTourShown, False)
            logger.info("[Setting] 用户点击「重新查看引导」,重置 cfg.MainTourShown")

            # 2) 找到顶层主窗口(向上查找 QWidget 链)
            mainWindow = self.window()
            # window() 在 widget 尚未 show 时可能返回 None,
            # 退而求其次查找 QApplication.activeWindow()
            if mainWindow is None or not isinstance(mainWindow, QWidget):
                try:
                    from PySide6.QtWidgets import QApplication

                    mainWindow = QApplication.activeWindow()
                except Exception:
                    pass
            if mainWindow is None:
                # 最后的兜底:遍历 topLevelWidgets
                try:
                    from PySide6.QtWidgets import QApplication

                    for w in QApplication.topLevelWidgets():
                        if w.isVisible():
                            mainWindow = w
                            break
                except Exception:
                    pass

            if mainWindow is None:
                logger.warning("[Setting] 重启引导失败:找不到顶层主窗口")
                InfoBar.warning(
                    title="无法启动引导",
                    content="请重新打开主窗口后再试。",
                    parent=self,
                    duration=2500,
                    position=InfoBarPosition.TOP,
                )
                return

            # 3) 检查 MainTourOverlay 是否已经存在(避免重复叠加)
            try:
                from app.view.widgets.main_tour_overlay import MainTourOverlay

                existing = mainWindow.findChild(MainTourOverlay)
                if existing is not None:
                    logger.info("[Setting] 已存在引导遮罩,先关闭再重启")
                    existing._completeTour(writeCfg=False)
            except Exception as e:
                logger.warning(f"[Setting] 检查已有引导遮罩失败: {e}")

            # 4) 构造并启动新引导
            try:
                overlay = MainTourOverlay(mainWindow)
                overlay.start()
                logger.info("[Setting] 主窗口引导遮罩已重新弹出")
                InfoBar.success(
                    title="引导已重新启动",
                    content="跟随卡片提示浏览全部主窗口功能。",
                    parent=self,
                    duration=2000,
                    position=InfoBarPosition.TOP,
                )
            except Exception as e:
                logger.warning(f"[Setting] 启动引导遮罩失败: {e}")
                InfoBar.error(
                    title="启动引导失败",
                    content=str(e),
                    parent=self,
                    duration=3000,
                    position=InfoBarPosition.TOP,
                )
        except Exception as e:
            logger.exception(f"[Setting] 重新查看引导失败: {e}")

class AgreementLabelWidget(QWidget):
    """当前定价与用户协议入口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setupLayout()

    def _setupLayout(self):
        """设置布局"""
        hBoxLayout = QHBoxLayout(self)
        hBoxLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.pricingStatusLabel = HyperlinkLabel("当前定价", self)
        self.pricingStatusLabel.setAccessibleName("查看当前定价")
        self.pricingStatusLabel.clicked.connect(self._showPricingStatus)
        self.pricingStatusLabel.setVisible(not INTERNAL_TEST_MODE)

        # 用户协议链接
        self.userAgreementLabel = HyperlinkLabel("用户协议", self)
        self.userAgreementLabel.setUrl("https://docs.qq.com/pdf/DTkhGeXVsWXBGTWN4")

        # 分隔符
        self.separator = VerticalSeparator(self)
        self.separator.setFixedHeight(15)
        self.separator.setVisible(not INTERNAL_TEST_MODE)

        # 内测本地模式不展示任何定价入口。
        if not INTERNAL_TEST_MODE:
            hBoxLayout.addWidget(self.pricingStatusLabel, 0, Qt.AlignmentFlag.AlignCenter)
            hBoxLayout.addSpacing(10)
            hBoxLayout.addWidget(self.separator)
            hBoxLayout.addSpacing(10)
        hBoxLayout.addWidget(self.userAgreementLabel, 0, Qt.AlignmentFlag.AlignCenter)

    def _showPricingStatus(self) -> None:
        dialog = PricingStatusDialog(parent=self.window())
        dialog.exec()


class SettingInterface(ScrollArea):
    """设置界面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._initScrollWidget()
        self._initComponents()
        self._initWidget()
        self._initLayout()
        self._connectSignals()

    def _initScrollWidget(self):
        """初始化滚动区域"""
        self.scrollWidget = QWidget()
        self.expandLayout = QVBoxLayout(self.scrollWidget)
        self.contentWidget = QWidget(self.scrollWidget)
        self.contentWidget.setMinimumWidth(0)
        self.contentWidget.setMaximumWidth(832)
        self.contentWidget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.contentLayout = QVBoxLayout(self.contentWidget)
        self.contentLayout.setContentsMargins(0, 0, 0, 0)
        self.contentLayout.setSpacing(32)

    def _initComponents(self):
        """初始化组件"""
        self.heroWidget = QWidget(self.contentWidget)
        heroLayout = QVBoxLayout(self.heroWidget)
        heroLayout.setContentsMargins(0, 24, 0, 10)
        heroLayout.setSpacing(0)
        heroLayout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.heroIconContainer = QWidget(self.heroWidget)
        self.heroIconContainer.setFixedSize(56, 56)
        self.heroIconContainer.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        self.heroIconContainer.setStyleSheet(
            f"background: {_ACCENT_SOFT}; border-radius: 28px;"
        )
        heroIconLayout = QHBoxLayout(self.heroIconContainer)
        heroIconLayout.setContentsMargins(16, 16, 16, 16)
        self.iconLabel = IconWidget(
            _accentIcon(FluentIcon.SETTING), self.heroIconContainer
        )
        self.iconLabel.setFixedSize(24, 24)
        heroIconLayout.addWidget(self.iconLabel)

        self.titleLabel = BodyLabel("设置", self.heroWidget)
        _setChineseUiFont(self.titleLabel, 22, QFont.Weight.Bold)
        self.titleLabel.setStyleSheet(f"color: {_TEXT};")
        self.titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        from app.core.utils.setting import VERSION

        displayVersion = VERSION if str(VERSION).lower().startswith("v") else f"v{VERSION}"
        self.subtitleLabel = CaptionLabel(
            f"棱溯客户端 {displayVersion} · 个性化你的工作流", self.heroWidget
        )
        _setChineseUiFont(self.subtitleLabel, 10)
        self.subtitleLabel.setStyleSheet(f"color: {_MUTED};")
        self.subtitleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heroLayout.addWidget(
            self.heroIconContainer, 0, Qt.AlignmentFlag.AlignHCenter
        )
        heroLayout.addSpacing(22)
        heroLayout.addWidget(self.titleLabel, 0, Qt.AlignmentFlag.AlignHCenter)
        heroLayout.addSpacing(8)
        heroLayout.addWidget(self.subtitleLabel, 0, Qt.AlignmentFlag.AlignHCenter)
        heroLayout.addSpacing(4)

        # 显示与缩放设置组件
        self.displaySettingWidget = DisplaySettingWidget(self.scrollWidget)

        # 软件设置组件
        self.softwareSettingWidget = SoftwareSettingWidget(self.scrollWidget)

        # 分析规则设置组件
        self.analysisSettingWidget = AnalysisSettingWidget(self.scrollWidget)

        self.aiChatSettingWidget = None
        self.aiInsightSettingWidget = None
        if not INTERNAL_TEST_MODE:
            # AI 聊天设置组件
            self.aiChatSettingWidget = AiChatSettingWidget(self.scrollWidget)

            # AI 解读设置组件（PRD-001 REQ-AI-001）
            self.aiInsightSettingWidget = AiInsightSettingWidget(self.scrollWidget)

        # 关于设置组件
        self.aboutSettingWidget = AboutSettingWidget(self.scrollWidget)

        # 用户协议组件
        self.agreementLabelWidget = AgreementLabelWidget(self.scrollWidget)

        # 版权信息
        self.infoLabel = BodyLabel(
            "©2026 棱溯 · 贵州六棱光界科技工作室",
            self.scrollWidget,
        )
        self.footerWidget = QWidget(self.contentWidget)
        footerLayout = QVBoxLayout(self.footerWidget)
        footerLayout.setContentsMargins(0, 0, 0, 0)
        footerLayout.setSpacing(8)
        footerLayout.addWidget(self.agreementLabelWidget)
        footerLayout.addWidget(self.infoLabel)

    def _initWidget(self):
        """初始化部件属性"""
        self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setObjectName("SettingInterface")

        self.scrollWidget.setObjectName("scrollWidget")
        self._applyPageTheme()
        qconfig.themeChangedFinished.connect(self._applyPageTheme)
        self.setStyleSheet("background:transparent;border:none;")

        self.infoLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.infoLabel.setStyleSheet("color:gray;font-size:12px;")

    def _applyPageTheme(self, *_args) -> None:
        """让设置页画布与其他业务页面使用同一背景令牌。"""
        palette = shellPalette()
        self.scrollWidget.setStyleSheet(
            f"background:{pageBackgroundColor().name()};border:none;"
        )
        self.heroIconContainer.setStyleSheet(
            f"background: {palette.accentSurface.name()}; border-radius: 28px;"
        )
        self.titleLabel.setStyleSheet(
            f"color: {palette.text.name()}; background: transparent;"
        )
        self.subtitleLabel.setStyleSheet(
            f"color: {palette.mutedText.name()}; background: transparent;"
        )
        self.infoLabel.setStyleSheet(
            f"color: {palette.mutedText.name()}; font-size: 12px;"
        )

    def _initLayout(self):
        """初始化布局"""
        self.expandLayout.setContentsMargins(24, 40, 24, 40)
        self.expandLayout.setSpacing(0)
        self.expandLayout.addWidget(
            self.contentWidget, 0, Qt.AlignmentFlag.AlignTop
        )

        self.contentLayout.addWidget(self.heroWidget)
        self.contentLayout.addWidget(self.displaySettingWidget)
        self.contentLayout.addWidget(self.analysisSettingWidget)
        self.contentLayout.addWidget(self.softwareSettingWidget)
        if self.aiChatSettingWidget is not None:
            self.contentLayout.addWidget(self.aiChatSettingWidget)
        if self.aiInsightSettingWidget is not None:
            self.contentLayout.addWidget(self.aiInsightSettingWidget)
        self.contentLayout.addWidget(self.aboutSettingWidget)
        self.contentLayout.addWidget(self.footerWidget)

    def _connectSignals(self):
        """连接信号槽"""
        pass

    def resizeEvent(self, event) -> None:
        """宽屏居中展示，窄屏改为纵向设置行并完整展示换行文字。"""
        super().resizeEvent(event)
        viewportWidth = self.viewport().width()
        isCompact = viewportWidth < 760
        sideMargin = 8 if isCompact else max(24, (viewportWidth - 832) // 2)
        self.expandLayout.setContentsMargins(sideMargin, 40, sideMargin, 40)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        showSummary = not isCompact
        cards = (
            self.displaySettingWidget,
            self.analysisSettingWidget,
            self.softwareSettingWidget,
            self.aiChatSettingWidget,
            self.aiInsightSettingWidget,
            self.aboutSettingWidget,
        )
        for card in (item for item in cards if item is not None):
            card.headerSummaryLabel.setVisible(showSummary)
            card.setCompactLayout(isCompact)
