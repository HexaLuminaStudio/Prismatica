# coding: utf-8
"""项目详情仪表盘。"""
from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
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
    FluentIcon,
    IconWidget,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    MessageBoxBase,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    TransparentPushButton,
    ToolButton,
    isDarkTheme,
    qconfig,
)

from app.core.models.project import AiInsight, Project, Resource
from app.core.services import projectManager
from app.core.services.research_report_service import researchReportService
from app.core.utils import logger
from app.view.widgets.project_manager_dialogs import RenameProjectDialog
from app.view.widgets.project_ui_helpers import PRIMARY_HEIGHT, normalizeButton
from app.view.widgets.prismatica_theme import pageBackgroundColor


_RESOURCE_TYPE_TO_MODULE = {
    key: "freq_analyzer"
    for key in (
        "freq",
        "collocation",
        "network",
        "kwic",
        "construction",
        "dependency",
        "keyword_list",
        "ngram_cluster",
        "sentiment",
        "word_cloud",
        "word_analysis",
    )
}
_RESOURCE_LABELS: Dict[str, str] = {
    "freq": "词频分析",
    "collocation": "搭配分析",
    "network": "共现网络",
    "kwic": "KWIC 检索",
    "construction": "构式识别",
    "dependency": "句法依存",
    "keyword_list": "关键词表",
    "ngram_cluster": "N-gram 聚类",
    "sentiment": "情感分析",
    "word_cloud": "词云",
    "word_analysis": "词语分析",
}
_RESOURCE_ICONS = {
    "all": FluentIcon.FOLDER,
    "freq": FluentIcon.PIE_SINGLE,
    "collocation": FluentIcon.LINK,
    "network": FluentIcon.CONNECT,
    "kwic": FluentIcon.SEARCH,
    "construction": FluentIcon.TILES,
    "dependency": FluentIcon.CONNECT,
    "keyword_list": FluentIcon.MENU,
    "ngram_cluster": FluentIcon.TILES,
    "sentiment": FluentIcon.CHAT,
    "word_cloud": FluentIcon.CLOUD,
    "word_analysis": FluentIcon.DOCUMENT,
}
_STATUS_LABELS = {
    "new": "新建",
    "candidate": "候选",
    "selected": "已采用",
    "rejected": "已弃用",
    "pending": "处理中",
}


def _date(value: str) -> str:
    if not value:
        return "时间未知"
    return value[:16].replace("T", " ")


def _preview(value: str, length: int = 120) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= length else f"{text[:length].rstrip()}…"


def _accentIcon(icon: FluentIcon):
    color = QColor("#20B8A6" if isDarkTheme() else "#007C70")
    return icon.icon(color=color)


class _InsightMessageBox(MessageBoxBase):
    """在 Fluent MessageBox 中展示完整 AI 解读。"""

    def __init__(self, insight: AiInsight, parent: QWidget) -> None:
        super().__init__(parent)
        self.widget.setMinimumWidth(620)
        self.buttonGroup.setFixedHeight(92)
        title = SubtitleLabel("AI 研究解读", self)
        meta = CaptionLabel(
            f"{_date(insight.createdAt)}  ·  {insight.model or '默认模型'}", self
        )
        content = PlainTextEdit(self)
        content.setReadOnly(True)
        content.setPlainText(insight.content or "暂无内容")
        content.setMinimumHeight(340)
        self.viewLayout.addWidget(title)
        self.viewLayout.addWidget(meta)
        self.viewLayout.addWidget(content)
        self.yesButton.setText("关闭")
        normalizeButton(self.yesButton, height=PRIMARY_HEIGHT, minimumWidth=110)
        self.cancelButton.hide()


class _ResourceRow(QFrame):
    openRequested = Signal(str)
    selected = Signal(str)

    def __init__(self, resource: Resource, parent: QWidget) -> None:
        super().__init__(parent)
        self.resource = resource
        self.setObjectName("dashboardResourceRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(72)
        self.setMaximumHeight(72)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 8)
        layout.setSpacing(10)
        iconHost = QFrame(self)
        iconHost.setObjectName("resourceRowIconHost")
        iconHost.setFixedSize(32, 32)
        iconLayout = QVBoxLayout(iconHost)
        iconLayout.setContentsMargins(7, 7, 7, 7)
        iconLayout.addWidget(
            IconWidget(
                _accentIcon(
                    _RESOURCE_ICONS.get(resource.type, FluentIcon.DOCUMENT)
                ),
                iconHost,
            )
        )
        layout.addWidget(iconHost)

        textBox = QVBoxLayout()
        textBox.setSpacing(3)
        title = StrongBodyLabel(resource.title or "未命名资源", self)
        title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        textBox.addWidget(title)
        meta = CaptionLabel(
            f"{_RESOURCE_LABELS.get(resource.type, resource.type or '其他')} · "
            f"{_preview(resource.summary, 46) or '暂无摘要'}",
            self,
        )
        meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        textBox.addWidget(meta)
        layout.addLayout(textBox, 1)

        status = QLabel(_STATUS_LABELS.get(resource.status, resource.status or "新建"))
        status.setObjectName("resourceStatusChip")
        status.setProperty("status", resource.status or "new")
        status.setFixedHeight(20)
        layout.addWidget(status, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(
            CaptionLabel(_date(resource.createdAt)[:10], self),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        openButton = ToolButton(FluentIcon.CHEVRON_RIGHT_MED, self)
        openButton.setToolTip("打开对应分析模块")
        normalizeButton(openButton, height=30, square=True, iconSize=14)
        openButton.clicked.connect(
            lambda: self.openRequested.emit(self.resource.type)
        )
        layout.addWidget(openButton, 0, Qt.AlignmentFlag.AlignVCenter)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.resource.id)
        super().mousePressEvent(event)


class _InsightCard(QFrame):
    openRequested = Signal(object)

    def __init__(self, insight: AiInsight, parent: QWidget) -> None:
        super().__init__(parent)
        self.insight = insight
        self.setObjectName("dashboardInsightCard")
        self.setMinimumHeight(118)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        model = QLabel(_preview(insight.model, 16) or "AI 研究报告")
        model.setObjectName("insightModelChip")
        model.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(model)
        header.addStretch(1)
        confidenceText = {
            "high": "高置信",
            "medium": "中置信",
            "low": "低置信",
        }.get(insight.confidence, "中置信")
        confidence = QLabel(confidenceText)
        confidence.setObjectName("insightConfidenceChip")
        confidence.setProperty("confidence", insight.confidence or "medium")
        header.addWidget(confidence)
        layout.addLayout(header)
        title = StrongBodyLabel(_preview(insight.content, 28) or "暂无解读内容", self)
        title.setWordWrap(True)
        layout.addWidget(title)
        footer = QHBoxLayout()
        relation = "关联：全部资源" if not insight.resourceId else "关联：指定资源"
        footer.addWidget(CaptionLabel(relation, self))
        footer.addWidget(CaptionLabel(_date(insight.createdAt)[:10], self))
        footer.addStretch(1)
        link = TransparentPushButton("查看", self)
        link.setObjectName("insightLinkButton")
        link.setIcon(_accentIcon(FluentIcon.CHEVRON_RIGHT_MED))
        normalizeButton(link, height=30, minimumWidth=72, iconSize=14)
        link.clicked.connect(lambda: self.openRequested.emit(self.insight))
        footer.addWidget(link)
        layout.addLayout(footer)


class ProjectDashboardWidget(QWidget):
    """与项目列表视觉系统一致的三栏详情页。"""

    jumpToModule = Signal(str)
    backRequested = Signal()
    busyChanged = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectDashboardWidget")
        self._currentProjectId = ""
        self._resourceScope: Optional[str] = None
        self._resourceFilter = "all"
        self._busy = False
        self._buildUi()
        self._connectSignals()
        self._applyTheme()
        self._syncFromManager()

    def _buildUi(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scrollArea.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.page = QWidget(self.scrollArea)
        self.page.setObjectName("projectDashboardPage")
        pageLayout = QVBoxLayout(self.page)
        pageLayout.setContentsMargins(0, 0, 0, 0)
        pageLayout.setSpacing(0)

        self._buildHeader(pageLayout)
        self.contentHost = QWidget(self.page)
        self.contentHost.setObjectName("projectDashboardContent")
        self.contentHost.setMaximumWidth(1280)
        self.contentHost.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        contentLayout = QVBoxLayout(self.contentHost)
        contentLayout.setContentsMargins(30, 14, 30, 32)
        contentLayout.setSpacing(14)
        self.projectDescriptionLabel = CaptionLabel("", self.contentHost)
        self.projectDescriptionLabel.setWordWrap(True)
        contentLayout.addWidget(self.projectDescriptionLabel)
        divider = QFrame(self.contentHost)
        divider.setObjectName("projectHeaderDivider")
        divider.setFixedHeight(1)
        contentLayout.addWidget(divider)
        self.columns = QGridLayout()
        self.columns.setHorizontalSpacing(16)
        self.columns.setVerticalSpacing(16)
        self.leftPanel = self._buildResourceSummary()
        self.centerPanel = self._buildRecentResources()
        self.rightPanel = self._buildInsights()
        contentLayout.addLayout(self.columns)
        contentLayout.addStretch(1)
        pageLayout.addWidget(self.contentHost, 1)
        self.scrollArea.setWidget(self.page)
        root.addWidget(self.scrollArea)
        self._panelColumns = 0
        self._reflowPanels(force=True)

    def _buildHeader(self, root: QVBoxLayout) -> None:
        self.headerHost = QFrame(self.page)
        self.headerHost.setObjectName("projectDashboardHeader")
        self.headerHost.setMinimumHeight(72)
        self.headerGrid = QGridLayout(self.headerHost)
        self.headerGrid.setContentsMargins(30, 8, 30, 8)
        self.headerGrid.setHorizontalSpacing(14)
        self.headerGrid.setVerticalSpacing(6)

        self.backButton = TransparentPushButton("返回列表", self.headerHost)
        self.backButton.setIcon(FluentIcon.LEFT_ARROW)
        normalizeButton(self.backButton, minimumWidth=88)
        self.backButton.clicked.connect(self._requestBack)
        self.headerInfoHost = QWidget(self.headerHost)
        info = QVBoxLayout(self.headerInfoHost)
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(4)
        titleRow = QHBoxLayout()
        self.projectNameLabel = QLabel("项目详情")
        self.projectNameLabel.setObjectName("dashboardProjectTitle")
        self.projectNameLabel.setWordWrap(True)
        self.projectNameLabel.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self.projectNameLabel.setMinimumWidth(0)
        self.projectNameLabel.setMaximumWidth(16777215)
        titleRow.addWidget(self.projectNameLabel)
        self.projectStatusLabel = QLabel("进行中")
        self.projectStatusLabel.setObjectName("dashboardStatusChip")
        titleRow.addWidget(self.projectStatusLabel)
        titleRow.addStretch(1)
        info.addLayout(titleRow)
        self.tagsHost = QWidget(self.headerInfoHost)
        self.tagsLayout = QHBoxLayout(self.tagsHost)
        self.tagsLayout.setContentsMargins(0, 0, 0, 0)
        self.tagsLayout.setSpacing(6)
        self.tagsLayout.addStretch(1)
        info.addWidget(self.tagsHost)

        self.headerActionsHost = QWidget(self.headerHost)
        actions = QHBoxLayout(self.headerActionsHost)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self.renameButton = PushButton("重命名", self.page)
        self.renameButton.setIcon(FluentIcon.EDIT)
        normalizeButton(self.renameButton, minimumWidth=96)
        self.renameButton.clicked.connect(self._renameProject)
        self.archiveButton = PushButton("归档", self.page)
        self.archiveButton.setIcon(FluentIcon.ZIP_FOLDER)
        normalizeButton(self.archiveButton, minimumWidth=88)
        self.archiveButton.clicked.connect(self._toggleArchive)
        self.deleteButton = TransparentPushButton("删除", self.headerHost)
        self.deleteButton.setObjectName("projectDangerButton")
        self.deleteButton.setIcon(
            FluentIcon.DELETE.icon(color=QColor("#D13438"))
        )
        deletePalette = self.deleteButton.palette()
        deletePalette.setColor(QPalette.ColorRole.ButtonText, QColor("#D13438"))
        self.deleteButton.setPalette(deletePalette)
        normalizeButton(self.deleteButton, minimumWidth=82)
        self.deleteButton.clicked.connect(self._deleteProject)
        actions.addWidget(self.renameButton)
        actions.addWidget(self.archiveButton)
        actions.addWidget(self.deleteButton)
        self.headerGrid.addWidget(self.backButton, 0, 0)
        self.headerGrid.addWidget(self.headerInfoHost, 0, 1)
        self.headerGrid.addWidget(self.headerActionsHost, 0, 2)
        self.headerGrid.setColumnStretch(1, 1)
        root.addWidget(self.headerHost)

    def _panel(self) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame(self.page)
        panel.setObjectName("dashboardPanel")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        return panel, layout

    def _buildResourceSummary(self) -> QFrame:
        panel, layout = self._panel()
        head = QHBoxLayout()
        head.addWidget(SubtitleLabel("资源", panel))
        self.resourceCountLabel = QLabel("0")
        self.resourceCountLabel.setObjectName("panelCountChip")
        head.addWidget(self.resourceCountLabel)
        head.addStretch(1)
        layout.addLayout(head)
        goButton = PushButton("前往分析模块", panel)
        goButton.setIcon(FluentIcon.ADD)
        normalizeButton(goButton, minimumWidth=128)
        goButton.clicked.connect(lambda: self._jump("freq"))
        layout.addWidget(goButton, 0, Qt.AlignmentFlag.AlignLeft)
        self.categoryLayout = QVBoxLayout()
        self.categoryLayout.setSpacing(4)
        layout.addLayout(self.categoryLayout)
        return panel

    def _buildRecentResources(self) -> QFrame:
        panel, layout = self._panel()
        head = QHBoxLayout()
        head.addWidget(SubtitleLabel("最近资源", panel))
        head.addStretch(1)
        self.resourceScopeLabel = CaptionLabel("报告范围：全部", panel)
        self.resourceScopeLabel.setObjectName("scopeLabel")
        head.addWidget(self.resourceScopeLabel)
        layout.addLayout(head)
        self.resourceLayout = QVBoxLayout()
        self.resourceLayout.setSpacing(8)
        layout.addLayout(self.resourceLayout)
        return panel

    def _buildInsights(self) -> QFrame:
        panel, layout = self._panel()
        head = QHBoxLayout()
        head.addWidget(SubtitleLabel("AI 解读", panel))
        self.insightCountLabel = QLabel("0")
        self.insightCountLabel.setObjectName("panelCountChip")
        head.addWidget(self.insightCountLabel)
        head.addStretch(1)
        layout.addLayout(head)
        self.generateButton = PrimaryPushButton("生成新解读", panel)
        self.generateButton.setIcon(FluentIcon.ROBOT)
        normalizeButton(self.generateButton, height=PRIMARY_HEIGHT)
        self.generateButton.clicked.connect(self._generateReport)
        layout.addWidget(self.generateButton)
        self.insightLayout = QVBoxLayout()
        self.insightLayout.setSpacing(8)
        layout.addLayout(self.insightLayout)
        return panel

    def _connectSignals(self) -> None:
        projectManager.activeProjectChanged.connect(self._onActiveProjectChanged)
        projectManager.projectListChanged.connect(self._syncFromManager)
        researchReportService.reportStarted.connect(self._onReportStarted)
        researchReportService.reportFinished.connect(self._onReportFinished)
        researchReportService.reportFailed.connect(self._onReportFailed)
        qconfig.themeChanged.connect(self._applyTheme)

    def _syncFromManager(self) -> None:
        try:
            active = projectManager.activeProject()
            projectId = active.id if active else ""
            if projectId != self._currentProjectId:
                self._resourceScope = None
                self._resourceFilter = "all"
            self._currentProjectId = projectId
            self._render()
        except Exception as error:
            logger.warning(f"[ProjectDashboard] 同步失败: {error}")

    def _render(self) -> None:
        project = projectManager.getProject(self._currentProjectId)
        if project is None:
            self.projectNameLabel.setText("项目详情")
            self.projectDescriptionLabel.setText("请从项目列表选择一个项目")
            self._clearLayout(self.categoryLayout)
            self._clearLayout(self.resourceLayout)
            self._clearLayout(self.insightLayout)
            return
        self._renderHeader(project)
        resources = list(projectManager.listResources(project.id))
        insights = list(projectManager.listAiInsights(project.id))
        self._renderCategories(resources)
        self._renderResources(resources)
        self._renderInsights(insights)

    def _renderHeader(self, project: Project) -> None:
        status = {"active": "进行中", "paused": "已暂停", "archived": "已归档"}
        self.projectNameLabel.setText(_preview(project.name, 24))
        self.projectNameLabel.setToolTip(project.name)
        self.projectStatusLabel.setText(status.get(project.status, project.status))
        self.projectStatusLabel.setProperty("status", project.status)
        self.projectStatusLabel.style().unpolish(self.projectStatusLabel)
        self.projectStatusLabel.style().polish(self.projectStatusLabel)
        self._clearLayout(self.tagsLayout)
        tags = list(project.tags[:4])
        if not tags and project.template:
            tags = [project.template]
        for tag in tags:
            chip = QLabel(tag, self.tagsHost)
            chip.setObjectName("projectHeaderTag")
            self.tagsLayout.addWidget(chip)
        self.tagsLayout.addStretch(1)
        self.projectDescriptionLabel.setText(
            f"{project.description or '暂无项目描述'}  ·  "
            f"创建于 {_date(project.createdAt)[:10]}"
        )
        archived = project.status == "archived"
        self.archiveButton.setText("恢复项目" if archived else "归档")
        self.archiveButton.setIcon(FluentIcon.PLAY if archived else FluentIcon.ZIP_FOLDER)

    def _renderCategories(self, resources: Iterable[Resource]) -> None:
        self._clearLayout(self.categoryLayout)
        counts = Counter(r.type or "other" for r in resources)
        self.resourceCountLabel.setText(str(sum(counts.values())))
        allButton = self._categoryButton("all", "全部资源", sum(counts.values()))
        self.categoryLayout.addWidget(allButton)
        for resourceType, count in sorted(
            counts.items(), key=lambda item: _RESOURCE_LABELS.get(item[0], item[0])
        ):
            button = self._categoryButton(
                resourceType,
                _RESOURCE_LABELS.get(resourceType, resourceType or "其他"),
                count,
            )
            self.categoryLayout.addWidget(button)

    def _categoryButton(self, resourceType: str, label: str, count: int) -> PushButton:
        button = TransparentPushButton("", self.leftPanel)
        button.setObjectName("resourceCategoryButton")
        button.setProperty("selected", self._resourceFilter == resourceType)
        normalizeButton(button, height=32)
        content = QHBoxLayout(button)
        content.setContentsMargins(8, 0, 8, 0)
        content.setSpacing(10)
        iconHost = QFrame(button)
        iconHost.setObjectName("resourceCategoryIconHost")
        iconHost.setFixedSize(24, 24)
        iconLayout = QVBoxLayout(iconHost)
        iconLayout.setContentsMargins(5, 5, 5, 5)
        iconLayout.addWidget(
            IconWidget(
                _accentIcon(
                    _RESOURCE_ICONS.get(resourceType, FluentIcon.DOCUMENT)
                ),
                iconHost,
            )
        )
        nameLabel = BodyLabel(label, button)
        countLabel = CaptionLabel(str(count), button)
        countLabel.setObjectName("resourceCategoryCount")
        countLabel.setMinimumWidth(24)
        countLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for child in (iconHost, nameLabel, countLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        content.addWidget(iconHost)
        content.addWidget(nameLabel)
        content.addStretch(1)
        content.addWidget(countLabel)
        button.clicked.connect(
            lambda _checked=False, key=resourceType: self._selectCategory(key)
        )
        return button

    def _renderResources(self, resources: list[Resource]) -> None:
        self._clearLayout(self.resourceLayout)
        visible = resources
        if self._resourceFilter != "all":
            visible = [r for r in visible if r.type == self._resourceFilter]
        visible.sort(key=lambda r: r.createdAt or "", reverse=True)
        if not visible:
            empty = BodyLabel("该分类暂无资源\n可前往分析模块生成研究成果", self.centerPanel)
            empty.setWordWrap(True)
            empty.setMinimumWidth(0)
            empty.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setObjectName("dashboardEmptyText")
            self.resourceLayout.addWidget(empty)
        for resource in visible[:6]:
            row = _ResourceRow(resource, self.centerPanel)
            row.openRequested.connect(self._jump)
            row.selected.connect(self._selectResource)
            row.setProperty("selected", resource.id == self._resourceScope)
            self.resourceLayout.addWidget(row)
        scope = "全部"
        if self._resourceScope:
            match = next((r for r in resources if r.id == self._resourceScope), None)
            scope = match.title if match else "全部"
        self.resourceScopeLabel.setText(f"报告范围：{scope}")

    def _renderInsights(self, insights: list[AiInsight]) -> None:
        self._clearLayout(self.insightLayout)
        insights.sort(key=lambda item: item.createdAt or "", reverse=True)
        self.insightCountLabel.setText(str(len(insights)))
        if not insights:
            empty = BodyLabel("还没有 AI 解读\n生成首份研究报告，沉淀项目结论", self.rightPanel)
            empty.setWordWrap(True)
            empty.setMinimumWidth(0)
            empty.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setObjectName("dashboardEmptyText")
            self.insightLayout.addWidget(empty)
            return
        for insight in insights[:3]:
            card = _InsightCard(insight, self.rightPanel)
            card.openRequested.connect(self._openInsight)
            self.insightLayout.addWidget(card)

    def _selectCategory(self, resourceType: str) -> None:
        self._resourceFilter = resourceType
        self._render()

    def _selectResource(self, resourceId: str) -> None:
        self._resourceScope = None if self._resourceScope == resourceId else resourceId
        self._render()

    def _jump(self, resourceType: str) -> None:
        if self._busy:
            return
        self.jumpToModule.emit(_RESOURCE_TYPE_TO_MODULE.get(resourceType, "freq_analyzer"))

    def _requestBack(self) -> None:
        if not self._busy:
            self.backRequested.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        QTimer.singleShot(0, self._reflowPanels)

    def _reflowPanels(self, force: bool = False) -> None:
        panelColumns = 3 if self.scrollArea.viewport().width() >= 1120 else 1
        if not force and panelColumns == self._panelColumns:
            return
        self._panelColumns = panelColumns
        if panelColumns == 3:
            positions = ((self.leftPanel, 0, 0), (self.centerPanel, 0, 1), (self.rightPanel, 0, 2))
            stretches = (0, 1, 0)
            self.leftPanel.setMinimumWidth(280)
            self.leftPanel.setMaximumWidth(280)
            self.rightPanel.setMinimumWidth(320)
            self.rightPanel.setMaximumWidth(320)
            self.headerGrid.addWidget(
                self.backButton, 0, 0, Qt.AlignmentFlag.AlignLeft
            )
            self.headerGrid.addWidget(self.headerInfoHost, 0, 1)
            self.headerGrid.addWidget(
                self.headerActionsHost, 0, 2, Qt.AlignmentFlag.AlignRight
            )
            self.headerGrid.setColumnStretch(0, 0)
            self.headerGrid.setColumnStretch(1, 1)
            self.headerGrid.setColumnStretch(2, 0)
        else:
            positions = ((self.leftPanel, 0, 0), (self.centerPanel, 1, 0), (self.rightPanel, 2, 0))
            stretches = (1,)
            self.leftPanel.setMinimumWidth(0)
            self.leftPanel.setMaximumWidth(16777215)
            self.rightPanel.setMinimumWidth(0)
            self.rightPanel.setMaximumWidth(16777215)
            self.headerGrid.addWidget(
                self.backButton, 0, 0, Qt.AlignmentFlag.AlignLeft
            )
            self.headerGrid.addWidget(
                self.headerActionsHost, 0, 1, Qt.AlignmentFlag.AlignRight
            )
            self.headerGrid.addWidget(self.headerInfoHost, 1, 0, 1, 2)
            self.headerGrid.setColumnStretch(0, 1)
            self.headerGrid.setColumnStretch(1, 0)
            self.headerGrid.setColumnStretch(2, 0)
        for panel, row, column in positions:
            self.columns.addWidget(
                panel, row, column, Qt.AlignmentFlag.AlignTop
            )
        for column in range(3):
            self.columns.setColumnStretch(column, 0)
        for column, stretch in enumerate(stretches):
            self.columns.setColumnStretch(column, stretch)

    def _renameProject(self) -> None:
        project = projectManager.getProject(self._currentProjectId)
        if project is None:
            return
        dialog = RenameProjectDialog(self.window(), project.name)
        if dialog.exec():
            newName = dialog.getResult()
            if newName and projectManager.renameProject(project.id, newName):
                self._render()

    def _toggleArchive(self) -> None:
        project = projectManager.getProject(self._currentProjectId)
        if project is None:
            return
        nextStatus = "active" if project.status == "archived" else "archived"
        if projectManager.setProjectStatus(project.id, nextStatus):
            self._render()

    def _deleteProject(self) -> None:
        project = projectManager.getProject(self._currentProjectId)
        if project is None:
            return
        box = MessageBox(
            "删除项目",
            f"确定删除“{project.name}”吗？项目内资源与 AI 解读将一并删除。",
            self.window(),
        )
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if box.exec() and projectManager.deleteProject(project.id):
            self.backRequested.emit()

    def _generateReport(self) -> None:
        if not self._currentProjectId or self._busy:
            return
        if not researchReportService.generate(
            self._currentProjectId, resourceScope=self._resourceScope
        ):
            InfoBar.warning(
                title="暂时无法生成",
                content="请确认项目有效且当前没有其他报告任务。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )

    def _openInsight(self, insight: AiInsight) -> None:
        _InsightMessageBox(insight, self.window()).exec()

    def isBusy(self) -> bool:
        return self._busy

    def _setBusy(self, busy: bool) -> None:
        if busy == self._busy:
            return
        self._busy = busy
        self.generateButton.setEnabled(not busy)
        self.generateButton.setText("正在生成研究报告…" if busy else "生成新解读")
        self.renameButton.setEnabled(not busy)
        self.archiveButton.setEnabled(not busy)
        self.deleteButton.setEnabled(not busy)
        self.busyChanged.emit(busy)

    def _onReportStarted(self) -> None:
        self._setBusy(True)

    def _onReportFinished(self, _insightId: str, _content: str) -> None:
        self._setBusy(False)
        self._render()
        InfoBar.success(
            title="研究报告已归档",
            content="新的 AI 解读已加入项目洞察。",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2500,
        )

    def _onReportFailed(self, error: str) -> None:
        self._setBusy(False)
        InfoBar.error(
            title="报告生成失败",
            content=error or "请稍后重试",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )

    def _onActiveProjectChanged(self, _projectId: str) -> None:
        self._syncFromManager()

    @staticmethod
    def _clearLayout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _applyTheme(self) -> None:
        dark = isDarkTheme()
        background = pageBackgroundColor(dark).name()
        surface = "#2B2B2B" if dark else "#FFFFFF"
        border = "#3A3A3A" if dark else "#E5E5E5"
        text = "#F5F5F5" if dark else "#1F1F1F"
        muted = "#B3B3B3" if dark else "#616161"
        hover = "#383838" if dark else "#F5F5F5"
        subtle = "#353535" if dark else "#F0F0F0"
        accent = "#20B8A6" if dark else "#007C70"
        self.page.setStyleSheet(
            f"""
            QWidget#projectDashboardPage {{ background: {background}; }}
            QFrame#projectDashboardHeader {{ background: {surface};
                border-bottom: 1px solid {border}; }}
            QLabel#dashboardProjectTitle {{ color: {text}; font-size: 26px;
                font-weight: 700; }}
            QLabel#dashboardStatusChip {{ color: #05655C; background: #DDF6F1;
                border-radius: 10px; padding: 3px 10px; font-weight: 600; }}
            QLabel#dashboardStatusChip[status="archived"] {{ color: {muted}; background: {hover}; }}
            QLabel#projectHeaderTag {{ background: {subtle}; color: {muted};
                border-radius: 4px; padding: 2px 8px; }}
            QPushButton#projectDangerButton {{ color: #D13438; border: none;
                background: transparent; }}
            QFrame#projectHeaderDivider {{ background: {border}; border: none; }}
            QFrame#dashboardPanel {{ background: {surface}; border: 1px solid {border};
                border-radius: 8px; }}
            QLabel#panelCountChip, QLabel#resourceCategoryCount {{ background: {hover};
                color: {muted}; border-radius: 4px; padding: 2px 6px; }}
            QFrame#dashboardResourceRow, QFrame#dashboardInsightCard {{ background: {surface};
                border: 1px solid {border}; border-radius: 8px; }}
            QFrame#dashboardResourceRow[selected="true"] {{ border: 2px solid {accent}; }}
            QFrame#dashboardResourceRow:hover {{ border-color: {accent}; }}
            QFrame#resourceRowIconHost, QFrame#resourceCategoryIconHost {{
                background: rgba(0, 176, 156, 0.10); color: {accent};
                border: none; border-radius: 4px; }}
            QPushButton#resourceCategoryButton {{ padding: 0; text-align: left;
                border: none; border-radius: 7px; background: transparent; color: {text}; }}
            QPushButton#resourceCategoryButton:hover {{ background: {hover}; }}
            QPushButton#resourceCategoryButton[selected="true"] {{ color: {accent};
                background: {hover}; font-weight: 600; }}
            QLabel#resourceStatusChip {{ border-radius: 4px; padding: 2px 7px;
                background: {hover}; color: {muted}; }}
            QLabel#resourceStatusChip[status="candidate"] {{ color: #725A00;
                background: #FFF7D6; }}
            QLabel#resourceStatusChip[status="selected"] {{ color: #107C10;
                background: #E7F4E7; }}
            QLabel#resourceStatusChip[status="rejected"] {{ color: #A4262C;
                background: #FDE7E9; }}
            QLabel#insightModelChip {{ background: {hover}; color: {muted};
                border-radius: 4px; padding: 2px 6px; }}
            QLabel#insightConfidenceChip {{ color: #107C10; background: #E7F4E7;
                border-radius: 4px; padding: 2px 6px; }}
            QLabel#insightConfidenceChip[confidence="medium"] {{ color: #725A00;
                background: #FFF7D6; }}
            QPushButton#insightLinkButton {{ border: none; color: {accent};
                background: transparent; padding: 2px 0; }}
            QLabel#scopeLabel {{ color: {muted}; }}
            QLabel#dashboardEmptyText {{ color: {muted}; padding: 34px 8px; }}
            """
        )


__all__ = ["ProjectDashboardWidget"]
