# coding: utf-8
"""HSK / Global 下载页面共用的任务工作台。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    CompactSpinBox,
    FluentIcon,
    IconWidget,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    isDarkTheme,
    qconfig,
)

from app.view.widgets.prismatica_theme import shellPalette


@dataclass(frozen=True)
class DownloadMode:
    """一项真实可用的下载检索方式。"""

    routeKey: str
    title: str
    description: str
    icon: FluentIcon


class DownloadModeRail(QWidget):
    """可访问的纵向检索方式选择器。"""

    currentItemChanged = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("downloadModeRail")
        self._currentRouteKey = ""
        self._buttons: Dict[str, PushButton] = {}
        self._routeKeys: List[str] = []
        self._isCompact = False
        self._buttonGroup = QButtonGroup(self)
        self._buttonGroup.setExclusive(True)

        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)

    def addItem(
        self,
        routeKey: str,
        title: str,
        description: str = "",
        icon: FluentIcon = FluentIcon.SEARCH,
    ) -> None:
        """添加一个检索方式。"""
        if routeKey in self._buttons:
            return
        text = title if not description else f"{title}\n{description}"
        button = PushButton(text, self, icon)
        button.setObjectName("downloadModeButton")
        button.setCheckable(True)
        button.setMinimumHeight(64)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setAccessibleName(title)
        button.setAccessibleDescription(description)
        button.clicked.connect(
            lambda _checked=False, key=routeKey: self.setCurrentItem(key)
        )
        self._buttonGroup.addButton(button)
        self._buttons[routeKey] = button
        self._routeKeys.append(routeKey)
        self._reflow()

    def setCurrentItem(self, routeKey: str) -> None:
        """切换当前检索方式。"""
        button = self._buttons.get(routeKey)
        if button is None:
            return
        button.setChecked(True)
        if routeKey == self._currentRouteKey:
            return
        self._currentRouteKey = routeKey
        self.currentItemChanged.emit(routeKey)

    def currentRouteKey(self) -> str:
        """返回当前检索方式路由键。"""
        return self._currentRouteKey

    def button(self, routeKey: str) -> Optional[PushButton]:
        """返回指定检索方式按钮。"""
        return self._buttons.get(routeKey)

    def setCompact(self, isCompact: bool) -> None:
        """在窄布局中把检索方式重排为两列。"""
        if self._isCompact == isCompact:
            return
        self._isCompact = isCompact
        self._reflow()

    def _reflow(self) -> None:
        while self._layout.count():
            self._layout.takeAt(0)
        for index, routeKey in enumerate(self._routeKeys):
            row = index // 2 if self._isCompact else index
            column = index % 2 if self._isCompact else 0
            self._layout.addWidget(self._buttons[routeKey], row, column)
        self._layout.setColumnStretch(0, 1)
        self._layout.setColumnStretch(1, 1 if self._isCompact else 0)


class DownloadTaskWorkbench(QWidget):
    """下载任务的统一三栏工作台。"""

    modeChanged = Signal(str)
    summaryRefreshRequested = Signal()

    def __init__(
        self,
        *,
        title: str,
        subtitle: str,
        sourceName: str,
        sourceCaption: str,
        pageIcon: FluentIcon,
        modes: List[DownloadMode],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DownloadTaskWorkbench")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._modes = {mode.routeKey: mode for mode in modes}
        self._searchWidgets: Dict[str, QWidget] = {}
        self._watchedObjects = set()
        self._workspaceLayout: Optional[QBoxLayout] = None
        self._modePanel: Optional[CardWidget] = None
        self._editorPanel: Optional[CardWidget] = None
        self._summaryPanel: Optional[CardWidget] = None
        self._summaryRows: Optional[QVBoxLayout] = None

        self.modeRail = DownloadModeRail(self)
        self.typeSegmentedWidget = self.modeRail
        self.searchStack = QStackedWidget(self)
        self.searchStack.setObjectName("downloadSearchStack")
        self.searchStack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.advancedHost = QVBoxLayout()
        self.advancedHost.setContentsMargins(0, 0, 0, 0)
        self.advancedHost.setSpacing(0)

        self.runTaskButton = PrimaryPushButton(
            "创建下载任务",
            self,
            FluentIcon.CLOUD_DOWNLOAD,
        )
        self.batchAddButton = PushButton(
            "加入批量清单",
            self,
            FluentIcon.ADD,
        )
        self.batchDownloadButton = PushButton("批量下载 (0)", self)
        self.batchDownloadButton.setEnabled(False)
        for button in (
            self.runTaskButton,
            self.batchAddButton,
            self.batchDownloadButton,
        ):
            button.setMinimumHeight(40)

        self._modeTitleLabel = StrongBodyLabel("", self)
        self._modeCaptionLabel = CaptionLabel("", self)
        self._modeCaptionLabel.setWordWrap(True)
        self._sourceValueLabel = StrongBodyLabel(sourceName, self)
        self._sourceCaptionLabel = CaptionLabel(sourceCaption, self)
        self._sourceCaptionLabel.setWordWrap(True)
        self._summaryHintLabel = CaptionLabel(
            "填写条件后，这里会生成本次下载任务摘要。",
            self,
        )
        self._summaryHintLabel.setWordWrap(True)
        self._batchCaptionLabel = CaptionLabel(
            "当前来源还没有待提交任务。",
            self,
        )
        self._batchCaptionLabel.setWordWrap(True)

        self._initUi(title, subtitle, pageIcon, modes)
        self._connectSignals()
        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)

        if modes:
            self.modeRail.setCurrentItem(modes[0].routeKey)

    def _initUi(
        self,
        title: str,
        subtitle: str,
        pageIcon: FluentIcon,
        modes: List[DownloadMode],
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scrollArea = ScrollArea(self)
        scrollArea.setObjectName("downloadWorkbenchScroll")
        scrollArea.setWidgetResizable(True)
        scrollArea.setFrameShape(QFrame.Shape.NoFrame)
        scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scrollArea)

        content = QWidget(scrollArea)
        content.setObjectName("downloadWorkbenchPage")
        contentLayout = QVBoxLayout(content)
        contentLayout.setContentsMargins(28, 22, 28, 24)
        contentLayout.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(12)
        iconHost = QFrame(content)
        iconHost.setObjectName("downloadTitleIconHost")
        iconHost.setFixedSize(44, 44)
        iconLayout = QVBoxLayout(iconHost)
        iconLayout.setContentsMargins(11, 11, 11, 11)
        iconWidget = IconWidget(pageIcon, iconHost)
        iconWidget.setFixedSize(22, 22)
        iconLayout.addWidget(iconWidget)
        header.addWidget(iconHost, 0, Qt.AlignmentFlag.AlignTop)

        headerText = QVBoxLayout()
        headerText.setSpacing(3)
        titleLabel = SubtitleLabel(title, content)
        headerText.addWidget(titleLabel)
        subtitleLabel = BodyLabel(subtitle, content)
        subtitleLabel.setObjectName("downloadPageSubtitle")
        subtitleLabel.setWordWrap(True)
        subtitleLabel.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        headerText.addWidget(subtitleLabel)
        header.addLayout(headerText, 1)

        statusChip = CaptionLabel("远程数据源", content)
        statusChip.setObjectName("downloadSourceChip")
        header.addWidget(statusChip, 0, Qt.AlignmentFlag.AlignTop)
        contentLayout.addLayout(header)

        workspace = QWidget(content)
        workspace.setObjectName("downloadWorkspace")
        self._workspaceLayout = QBoxLayout(
            QBoxLayout.Direction.LeftToRight,
            workspace,
        )
        self._workspaceLayout.setContentsMargins(0, 0, 0, 0)
        self._workspaceLayout.setSpacing(16)

        self._modePanel = self._buildModePanel(workspace, modes)
        self._editorPanel = self._buildEditorPanel(workspace)
        self._summaryPanel = self._buildSummaryPanel(workspace)
        self._workspaceLayout.addWidget(self._modePanel)
        self._workspaceLayout.addWidget(self._editorPanel, 1)
        self._workspaceLayout.addWidget(self._summaryPanel)
        self._workspaceLayout.setStretch(0, 0)
        self._workspaceLayout.setStretch(1, 1)
        self._workspaceLayout.setStretch(2, 0)
        contentLayout.addWidget(workspace, 1)
        scrollArea.setWidget(content)

    def _buildModePanel(self, parent: QWidget, modes: List[DownloadMode]) -> CardWidget:
        panel = CardWidget(parent)
        panel.setObjectName("downloadModePanel")
        panel.setMinimumWidth(220)
        panel.setMaximumWidth(244)
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)
        layout.addWidget(StrongBodyLabel("检索方式", panel))
        hint = CaptionLabel("选择与研究目标匹配的检索方式。", panel)
        hint.setObjectName("downloadMutedText")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        for mode in modes:
            self.modeRail.addItem(
                mode.routeKey,
                mode.title,
                mode.description,
                mode.icon,
            )
        layout.addWidget(self.modeRail)
        layout.addStretch(1)
        return panel

    def _buildEditorPanel(self, parent: QWidget) -> CardWidget:
        panel = CardWidget(parent)
        panel.setObjectName("downloadEditorPanel")
        panel.setMinimumWidth(420)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)

        header = QVBoxLayout()
        header.setSpacing(3)
        header.addWidget(self._modeTitleLabel)
        self._modeCaptionLabel.setObjectName("downloadMutedText")
        header.addWidget(self._modeCaptionLabel)
        layout.addLayout(header)

        separator = QFrame(panel)
        separator.setObjectName("downloadSeparator")
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)

        formScroll = ScrollArea(panel)
        formScroll.setObjectName("downloadFormScroll")
        formScroll.setWidgetResizable(True)
        formScroll.setFrameShape(QFrame.Shape.NoFrame)
        formScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        formHost = QWidget(formScroll)
        formHost.setObjectName("downloadFormHost")
        formLayout = QVBoxLayout(formHost)
        formLayout.setContentsMargins(0, 0, 4, 0)
        formLayout.setSpacing(12)
        formLayout.addWidget(self.searchStack)
        formLayout.addLayout(self.advancedHost)
        formLayout.addStretch(1)
        formScroll.setWidget(formHost)
        layout.addWidget(formScroll, 1)
        return panel

    def _buildSummaryPanel(self, parent: QWidget) -> CardWidget:
        panel = CardWidget(parent)
        panel.setObjectName("downloadSummaryPanel")
        panel.setMinimumWidth(286)
        panel.setMaximumWidth(324)
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)

        layout.addWidget(StrongBodyLabel("任务摘要", panel))
        self._sourceValueLabel.setObjectName("downloadSourceName")
        layout.addWidget(self._sourceValueLabel)
        self._sourceCaptionLabel.setObjectName("downloadMutedText")
        layout.addWidget(self._sourceCaptionLabel)

        separator = QFrame(panel)
        separator.setObjectName("downloadSeparator")
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)

        self._summaryRows = QVBoxLayout()
        self._summaryRows.setContentsMargins(0, 0, 0, 0)
        self._summaryRows.setSpacing(8)
        layout.addLayout(self._summaryRows)
        self._summaryHintLabel.setObjectName("downloadMutedText")
        layout.addWidget(self._summaryHintLabel)
        layout.addStretch(1)

        layout.addWidget(self.runTaskButton)
        layout.addWidget(self.batchAddButton)

        batchSeparator = QFrame(panel)
        batchSeparator.setObjectName("downloadSeparator")
        batchSeparator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(batchSeparator)
        layout.addWidget(StrongBodyLabel("批量清单", panel))
        self._batchCaptionLabel.setObjectName("downloadMutedText")
        layout.addWidget(self._batchCaptionLabel)
        layout.addWidget(self.batchDownloadButton)
        return panel

    def _connectSignals(self) -> None:
        self.modeRail.currentItemChanged.connect(self._onModeChanged)

    def addSearchWidget(self, routeKey: str, widget: QWidget) -> None:
        """把一个真实检索表单挂入工作台。"""
        if routeKey in self._searchWidgets:
            return
        widget.setObjectName("downloadSearchForm")
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._searchWidgets[routeKey] = widget
        self.searchStack.addWidget(widget)
        self.watchInputs(widget)
        if routeKey == self.modeRail.currentRouteKey():
            self.searchStack.setCurrentWidget(widget)
            self._syncSearchStackHeight()

    def setAdvancedWidget(self, widget: QWidget) -> None:
        """设置当前页面的高级筛选表单。"""
        widget.setObjectName("downloadAdvancedForm")
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.advancedHost.addWidget(widget)
        self.watchInputs(widget)

    def watchInputs(self, root: QWidget) -> None:
        """监听表单值变化并请求刷新任务摘要。"""
        signalSpecs = (
            (LineEdit, "textChanged"),
            (ComboBox, "currentIndexChanged"),
            (CompactSpinBox, "valueChanged"),
            (CheckBox, "stateChanged"),
        )
        for widgetType, signalName in signalSpecs:
            for widget in root.findChildren(widgetType):
                if id(widget) in self._watchedObjects:
                    continue
                signal = getattr(widget, signalName, None)
                if signal is not None:
                    signal.connect(lambda *_args: self.summaryRefreshRequested.emit())
                    self._watchedObjects.add(id(widget))

    def _onModeChanged(self, routeKey: str) -> None:
        widget = self._searchWidgets.get(routeKey)
        if widget is not None:
            self.searchStack.setCurrentWidget(widget)
            self._syncSearchStackHeight()
        mode = self._modes.get(routeKey)
        if mode is not None:
            self._modeTitleLabel.setText("条件设置")
            self._modeCaptionLabel.setText(f"{mode.title} · {mode.description}")
        self.modeChanged.emit(routeKey)
        self.summaryRefreshRequested.emit()

    def _syncSearchStackHeight(self) -> None:
        """让当前检索表单按内容高度贴顶显示，避免单字段表单被纵向拉伸。"""
        widget = self.searchStack.currentWidget()
        if widget is None:
            return
        widget.ensurePolished()
        targetHeight = max(132, min(560, widget.sizeHint().height()))
        self.searchStack.setFixedHeight(targetHeight)

    def setCurrentMode(self, routeKey: str) -> None:
        """从页面控制器切换检索方式。"""
        self.modeRail.setCurrentItem(routeKey)

    def currentRouteKey(self) -> str:
        """返回当前检索方式路由键。"""
        return self.modeRail.currentRouteKey()

    def setSummary(self, entries: List[Tuple[str, str]]) -> None:
        """使用用户可读字段刷新任务摘要。"""
        if self._summaryRows is None:
            return
        while self._summaryRows.count():
            item = self._summaryRows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        mode = self._modes.get(self.currentRouteKey())
        visibleEntries = []
        if mode is not None:
            visibleEntries.append(("检索方式", mode.title))
        visibleEntries.extend(entries)
        self._summaryHintLabel.setVisible(not entries)
        for label, value in visibleEntries:
            row = QFrame(self)
            row.setObjectName("downloadSummaryRow")
            rowLayout = QHBoxLayout(row)
            rowLayout.setContentsMargins(10, 8, 10, 8)
            rowLayout.setSpacing(8)
            labelWidget = CaptionLabel(label, row)
            labelWidget.setObjectName("downloadSummaryKey")
            labelWidget.setFixedWidth(72)
            valueWidget = BodyLabel(value, row)
            valueWidget.setObjectName("downloadSummaryValue")
            valueWidget.setWordWrap(True)
            valueWidget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            rowLayout.addWidget(labelWidget)
            rowLayout.addWidget(valueWidget, 1)
            self._summaryRows.addWidget(row)

    def setBatchCount(self, count: int) -> None:
        """刷新当前来源的批量任务数量。"""
        self.batchDownloadButton.setText(f"批量下载 ({count})")
        self.batchDownloadButton.setEnabled(count > 0)
        if count > 0:
            self._batchCaptionLabel.setText(
                f"已有 {count} 个待提交任务，仅包含当前数据来源。"
            )
        else:
            self._batchCaptionLabel.setText("当前来源还没有待提交任务。")

    def setBusy(self, isBusy: bool) -> None:
        """切换创建任务按钮的忙碌状态。"""
        self.runTaskButton.setEnabled(not isBusy)
        self.runTaskButton.setText("正在准备任务…" if isBusy else "创建下载任务")

    def _applyTheme(self) -> None:
        palette = shellPalette()
        if isDarkTheme():
            surfaceMuted = "#292F33"
            accentSurface = "rgba(0, 176, 156, 0.18)"
            accentText = "#5DE0CF"
        else:
            surfaceMuted = "#F3F7F7"
            accentSurface = "rgba(0, 176, 156, 0.12)"
            accentText = "#007C70"
        self.setStyleSheet(
            f"""
            QWidget#downloadWorkbenchPage {{
                background: {palette.window.name()};
            }}
            QScrollArea#downloadWorkbenchScroll,
            QScrollArea#downloadWorkbenchScroll > QWidget > QWidget,
            QScrollArea#downloadFormScroll,
            QScrollArea#downloadFormScroll > QWidget > QWidget,
            QWidget#downloadFormHost,
            QWidget#downloadWorkspace,
            QWidget#downloadModeRail,
            QStackedWidget#downloadSearchStack {{
                background: transparent;
                border: none;
            }}
            QWidget#downloadModePanel,
            QWidget#downloadEditorPanel,
            QWidget#downloadSummaryPanel {{
                background: {palette.content.name()};
                border: 1px solid {palette.border.name()};
                border-radius: 12px;
            }}
            QFrame#downloadTitleIconHost {{
                background: {accentSurface};
                border: none;
                border-radius: 10px;
            }}
            QLabel#downloadPageSubtitle,
            QLabel#downloadMutedText,
            QLabel#downloadSummaryKey {{
                color: {palette.mutedText.name()};
            }}
            QLabel#downloadSourceChip {{
                color: {accentText};
                background: {accentSurface};
                border-radius: 6px;
                padding: 6px 10px;
            }}
            QLabel#downloadSourceName {{
                color: {accentText};
            }}
            QFrame#downloadSeparator {{
                color: {palette.border.name()};
                background: {palette.border.name()};
                border: none;
                max-height: 1px;
            }}
            QPushButton#downloadModeButton {{
                text-align: left;
                padding: 8px 10px;
                border: 1px solid transparent;
                border-radius: 8px;
                background: transparent;
            }}
            QPushButton#downloadModeButton:hover {{
                background: {surfaceMuted};
            }}
            QPushButton#downloadModeButton:checked {{
                color: {accentText};
                background: {accentSurface};
                border: 1px solid {palette.border.name()};
            }}
            QFrame#downloadSummaryRow {{
                background: {surfaceMuted};
                border: none;
                border-radius: 8px;
            }}
            QWidget#downloadSearchForm,
            QWidget#downloadAdvancedForm {{
                background: transparent;
                border: none;
            }}
            """
        )

    def _applyResponsiveLayout(self) -> None:
        if not all(
            (
                self._workspaceLayout,
                self._modePanel,
                self._editorPanel,
                self._summaryPanel,
            )
        ):
            return
        isCompact = self.width() < 1120
        direction = (
            QBoxLayout.Direction.TopToBottom
            if isCompact
            else QBoxLayout.Direction.LeftToRight
        )
        self._workspaceLayout.setDirection(direction)
        if isCompact:
            for panel in (self._modePanel, self._editorPanel, self._summaryPanel):
                panel.setMinimumWidth(0)
                panel.setMaximumWidth(16777215)
            self._modePanel.setMinimumHeight(230)
            self._modePanel.setMaximumHeight(280)
            self.modeRail.setCompact(True)
            self.modeRail.setMaximumWidth(16777215)
            self._editorPanel.setMinimumHeight(520)
            self._summaryPanel.setMinimumHeight(480)
        else:
            self._modePanel.setMinimumWidth(220)
            self._modePanel.setMaximumWidth(244)
            self._modePanel.setMaximumHeight(16777215)
            self.modeRail.setCompact(False)
            self.modeRail.setMaximumWidth(16777215)
            self._editorPanel.setMinimumWidth(420)
            self._summaryPanel.setMinimumWidth(286)
            self._summaryPanel.setMaximumWidth(324)
            for panel in (self._modePanel, self._editorPanel, self._summaryPanel):
                panel.setMinimumHeight(610)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._applyResponsiveLayout()


__all__ = ["DownloadMode", "DownloadModeRail", "DownloadTaskWorkbench"]
