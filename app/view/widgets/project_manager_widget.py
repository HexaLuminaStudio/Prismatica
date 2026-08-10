# coding: utf-8
"""项目管理列表、统计概览与空状态。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
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
    Action,
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    IconWidget,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    Pivot,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    ScrollArea,
    SearchLineEdit,
    StrongBodyLabel,
    TitleLabel,
    ToolButton,
    isDarkTheme,
    qconfig,
)

from app.core.models.project import Project
from app.core.services import projectManager
from app.core.utils import logger
from app.view.widgets.project_manager_dialogs import NewProjectDialog, RenameProjectDialog
from app.view.widgets.project_ui_helpers import PRIMARY_HEIGHT, normalizeButton
from app.view.widgets.prismatica_theme import pageBackgroundColor


_STATUS_TEXT = {"active": "进行中", "paused": "暂停", "archived": "已归档"}


def _compactDate(value: str) -> str:
    return value[:10] if value else "尚未更新"


class _MetricCard(QFrame):
    def __init__(self, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectMetricCard")
        self.setMinimumHeight(96)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(3)
        self.label = CaptionLabel(label, self)
        self.value = QLabel("0")
        self.value.setObjectName("projectMetricValue")
        self.hint = CaptionLabel("", self)
        self.hint.setWordWrap(True)
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)

    def setData(self, value: int, suffix: str, hint: str) -> None:
        self.value.setText(f"{value:,} {suffix}")
        self.hint.setText(hint)


class _ProjectCard(QFrame):
    openRequested = Signal(str)
    renameRequested = Signal(str)
    statusRequested = Signal(str, str)
    deleteRequested = Signal(str)

    def __init__(self, project: Project, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.project = project
        self.setObjectName("projectCard")
        self.setMinimumHeight(222)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._buildUi()

    def _buildUi(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        top = QHBoxLayout()
        self.statusChip = QLabel(_STATUS_TEXT.get(self.project.status, self.project.status))
        self.statusChip.setObjectName("projectStatusChip")
        self.statusChip.setProperty("status", self.project.status)
        top.addWidget(self.statusChip)
        top.addStretch(1)
        more = ToolButton(FluentIcon.MORE, self)
        more.setToolTip("更多项目操作")
        normalizeButton(more, square=True)
        more.clicked.connect(lambda: self._showMenu(more))
        top.addWidget(more)
        layout.addLayout(top)

        name = StrongBodyLabel(self.project.name, self)
        name.setObjectName("projectCardTitle")
        name.setWordWrap(True)
        layout.addWidget(name)

        description = BodyLabel(
            self.project.description or "尚未添加项目说明，可在研究过程中逐步补充。",
            self,
        )
        description.setObjectName("projectCardDescription")
        description.setWordWrap(True)
        description.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(description)

        tags = QHBoxLayout()
        tags.setSpacing(6)
        tagItems = list(self.project.tags[:3])
        if not tagItems and self.project.template:
            tagItems = [self.project.template]
        for text in tagItems:
            chip = QLabel(text)
            chip.setObjectName("projectTagChip")
            tags.addWidget(chip)
        tags.addStretch(1)
        layout.addLayout(tags)
        layout.addStretch(1)

        divider = QFrame(self)
        divider.setObjectName("projectCardDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(
            CaptionLabel(
                f"{len(self.project.resources)} 个资源  ·  "
                f"{len(self.project.aiInsights)} 个 AI 解读"
            )
        )
        footer.addStretch(1)
        footer.addWidget(CaptionLabel(_compactDate(self.project.updatedAt)))
        openButton = PushButton(FluentIcon.CHEVRON_RIGHT_MED, "打开", self)
        openButton.setObjectName("projectOpenButton")
        normalizeButton(openButton, minimumWidth=84)
        openButton.clicked.connect(lambda: self.openRequested.emit(self.project.id))
        footer.addWidget(openButton)
        layout.addLayout(footer)

    def _showMenu(self, anchor: QWidget) -> None:
        menu = RoundMenu(parent=self)
        menu.addAction(
            Action(
                FluentIcon.EDIT,
                "重命名",
                triggered=lambda: self.renameRequested.emit(self.project.id),
            )
        )
        if self.project.status == "archived":
            menu.addAction(
                Action(
                    FluentIcon.PLAY,
                    "恢复进行",
                    triggered=lambda: self.statusRequested.emit(self.project.id, "active"),
                )
            )
        else:
            nextStatus = "active" if self.project.status == "paused" else "paused"
            text = "继续项目" if self.project.status == "paused" else "暂停项目"
            menu.addAction(
                Action(
                    FluentIcon.PAUSE if nextStatus == "paused" else FluentIcon.PLAY,
                    text,
                    triggered=lambda: self.statusRequested.emit(self.project.id, nextStatus),
                )
            )
            menu.addAction(
                Action(
                    FluentIcon.ZIP_FOLDER,
                    "归档",
                    triggered=lambda: self.statusRequested.emit(self.project.id, "archived"),
                )
            )
        menu.addSeparator()
        menu.addAction(
            Action(
                FluentIcon.DELETE,
                "删除",
                triggered=lambda: self.deleteRequested.emit(self.project.id),
            )
        )
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


class _EmptyProjectState(QFrame):
    createRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectEmptyState")
        self.setMinimumWidth(0)
        self.setMaximumWidth(520)
        self.setMinimumHeight(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.emptyLayout = QVBoxLayout(self)
        self.emptyLayout.setContentsMargins(48, 38, 48, 38)
        self.emptyLayout.setSpacing(12)
        self.emptyLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        iconHost = QFrame(self)
        iconHost.setObjectName("projectEmptyIcon")
        iconHost.setFixedSize(80, 80)
        iconLayout = QVBoxLayout(iconHost)
        iconLayout.setContentsMargins(20, 20, 20, 20)
        iconLayout.addWidget(IconWidget(FluentIcon.FOLDER, iconHost))
        self.emptyLayout.addWidget(iconHost, alignment=Qt.AlignmentFlag.AlignHCenter)
        title = TitleLabel("还没有任何项目", self)
        self._prepareWrappedLabel(title, centered=True)
        self.emptyLayout.addWidget(title)
        description = BodyLabel(
            "项目用于组织语料、分析结果和 AI 解读，所有研究数据都保存在本地。",
            self,
        )
        self._prepareWrappedLabel(description, centered=True)
        self.emptyLayout.addWidget(description)
        bullets = BodyLabel(
            "• 自动归档词频、网络、KWIC 等分析结果\n"
            "• 一键生成并归档 AI 研究报告\n"
            "• 本地保存，登录后可使用跨设备能力",
            self,
        )
        self._prepareWrappedLabel(bullets)
        self.emptyLayout.addWidget(bullets)
        button = PrimaryPushButton(FluentIcon.ADD, "创建第一个项目", self)
        button.setObjectName("projectEmptyCreateButton")
        normalizeButton(button, height=PRIMARY_HEIGHT, minimumWidth=154)
        button.clicked.connect(self.createRequested)
        self.emptyLayout.addWidget(button, alignment=Qt.AlignmentFlag.AlignHCenter)

    @staticmethod
    def _prepareWrappedLabel(label: QLabel, centered: bool = False) -> None:
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
        )
        label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
            if centered
            else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        sideMargin = 24 if self.width() < 420 else 48
        self.emptyLayout.setContentsMargins(sideMargin, 38, sideMargin, 38)


class ProjectManagerWidget(QWidget):
    """项目列表页面：统计、筛选、卡片网格与空状态。"""

    projectSwitchRequested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectManagerWidget")
        self._allProjects: list[Project] = []
        self._cards: list[_ProjectCard] = []
        self._filter = "all"
        self._columns = 0
        self._layoutMode = ""
        self._creatingInfoBar = None
        self._buildUi()
        self._connectSignals()
        self._applyTheme()
        self._reflowLayout(force=True)
        qconfig.themeChangedFinished.connect(self._applyTheme)
        self.refresh()

    def _buildUi(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scrollArea.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self.scrollArea)

        self.canvas = QWidget(self.scrollArea)
        self.canvas.setObjectName("projectManagerCanvas")
        self.canvasLayout = QVBoxLayout(self.canvas)
        self.canvasLayout.setContentsMargins(30, 24, 30, 30)
        self.canvasLayout.setSpacing(20)

        header = QHBoxLayout()
        titleColumn = QVBoxLayout()
        titleColumn.setSpacing(2)
        self.titleLabel = TitleLabel("项目管理", self.canvas)
        titleColumn.addWidget(self.titleLabel)
        titleColumn.addWidget(BodyLabel("管理和组织你的语料研究项目", self.canvas))
        header.addLayout(titleColumn)
        header.addStretch(1)
        self.refreshButton = ToolButton(FluentIcon.SYNC, self.canvas)
        self.refreshButton.setToolTip("刷新项目列表")
        normalizeButton(self.refreshButton, square=True)
        header.addWidget(self.refreshButton)
        self.newButton = PrimaryPushButton(FluentIcon.ADD, "新建项目", self.canvas)
        self.newButton.setObjectName("projectNewButton")
        normalizeButton(self.newButton, height=PRIMARY_HEIGHT, minimumWidth=112)
        header.addWidget(self.newButton)
        self.canvasLayout.addLayout(header)

        self.metricsHost = QWidget(self.canvas)
        self.metricsLayout = QGridLayout(self.metricsHost)
        self.metricsLayout.setContentsMargins(0, 0, 0, 0)
        self.metricsLayout.setHorizontalSpacing(14)
        self.metricsLayout.setVerticalSpacing(14)
        self.totalMetric = _MetricCard("项目总数", self.canvas)
        self.monthMetric = _MetricCard("本月新增", self.canvas)
        self.resourceMetric = _MetricCard("资源总数", self.canvas)
        self.metricsLayout.addWidget(self.totalMetric, 0, 0)
        self.metricsLayout.addWidget(self.monthMetric, 0, 1)
        self.metricsLayout.addWidget(self.resourceMetric, 0, 2)
        self.canvasLayout.addWidget(self.metricsHost)

        self.toolbar = QFrame(self.canvas)
        self.toolbar.setObjectName("projectToolbar")
        self.toolbarLayout = QGridLayout(self.toolbar)
        self.toolbarLayout.setContentsMargins(12, 7, 8, 7)
        self.toolbarLayout.setHorizontalSpacing(10)
        self.toolbarLayout.setVerticalSpacing(8)
        self.filterHost = QWidget(self.toolbar)
        filterLayout = QHBoxLayout(self.filterHost)
        filterLayout.setContentsMargins(0, 0, 0, 0)
        filterLayout.setSpacing(0)
        self.filterPivot = Pivot(self.filterHost)
        for key, text in (
            ("all", "全部"),
            ("active", "进行中"),
            ("paused", "暂停"),
            ("archived", "归档"),
        ):
            self.filterPivot.addItem(key, text)
        self.filterPivot.setCurrentItem("all")
        self.filterPivot.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        filterLayout.addWidget(self.filterPivot)
        filterLayout.addStretch(1)
        self.toolbarLayout.addWidget(self.filterHost, 0, 0)
        self.searchEdit = SearchLineEdit(self.toolbar)
        self.searchEdit.setPlaceholderText("搜索项目名、标签或描述")
        self.searchEdit.setMinimumWidth(260)
        self.searchEdit.setMaximumWidth(380)
        self.searchEdit.setMinimumHeight(34)
        self.toolbarLayout.addWidget(self.searchEdit, 0, 1)
        self.sortCombo = ComboBox(self.toolbar)
        self.sortCombo.addItems(["最近更新", "最早创建", "名称排序"])
        self.sortCombo.setMinimumHeight(34)
        self.toolbarLayout.addWidget(self.sortCombo, 0, 2)
        self.canvasLayout.addWidget(self.toolbar)

        self.contentHost = QWidget(self.canvas)
        self.contentLayout = QVBoxLayout(self.contentHost)
        self.contentLayout.setContentsMargins(0, 0, 0, 0)
        self.contentLayout.setSpacing(14)
        self.cardGridHost = QWidget(self.contentHost)
        self.cardGrid = QGridLayout(self.cardGridHost)
        self.cardGrid.setContentsMargins(0, 0, 0, 0)
        self.cardGrid.setHorizontalSpacing(14)
        self.cardGrid.setVerticalSpacing(14)
        self.contentLayout.addWidget(self.cardGridHost)
        self.emptyHost = QWidget(self.contentHost)
        self.emptyHost.setMinimumHeight(360)
        emptyLayout = QVBoxLayout(self.emptyHost)
        emptyLayout.setContentsMargins(0, 0, 0, 0)
        emptyLayout.addStretch(1)
        self.emptyState = _EmptyProjectState(self.emptyHost)
        self.emptyState.createRequested.connect(self._onNewClicked)
        emptyLayout.addWidget(
            self.emptyState, alignment=Qt.AlignmentFlag.AlignHCenter
        )
        emptyLayout.addStretch(1)
        self.contentLayout.addWidget(self.emptyHost, 1)
        self.resultLabel = CaptionLabel("共 0 个项目", self.contentHost)
        self.resultLabel.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.contentLayout.addWidget(self.resultLabel)
        self.contentLayout.addStretch(1)
        self.canvasLayout.addWidget(self.contentHost, 1)
        self.scrollArea.setWidget(self.canvas)

    def _connectSignals(self) -> None:
        self.newButton.clicked.connect(self._onNewClicked)
        self.refreshButton.clicked.connect(self.refresh)
        self.filterPivot.currentItemChanged.connect(self._onFilterChanged)
        self.searchEdit.textChanged.connect(lambda _text: self._renderProjects())
        self.sortCombo.currentIndexChanged.connect(lambda _index: self._renderProjects())
        projectManager.projectListChanged.connect(self.refresh)
        projectManager.activeProjectChanged.connect(lambda _pid: self.refresh())

    def refresh(self) -> None:
        try:
            self._allProjects = projectManager.listProjects()
            self._updateMetrics()
            self._renderProjects()
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[ProjectManagerWidget] refresh 失败: {exc}")
            InfoBar.error(
                title="项目加载失败",
                content="无法读取本地项目数据，请稍后重试。",
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )

    def _updateMetrics(self) -> None:
        total = len(self._allProjects)
        active = sum(p.status == "active" for p in self._allProjects)
        resources = sum(len(p.resources) for p in self._allProjects)
        month = datetime.now().astimezone().strftime("%Y-%m")
        added = sum((p.createdAt or "").startswith(month) for p in self._allProjects)
        self.totalMetric.setData(total, "个项目", f"{active} 个进行中")
        self.monthMetric.setData(added, "个项目", month)
        self.resourceMetric.setData(resources, "个资源", "分析结果与研究归档")

    def _filteredProjects(self) -> list[Project]:
        query = self.searchEdit.text().strip().casefold()
        projects = [
            p
            for p in self._allProjects
            if self._filter == "all" or p.status == self._filter
        ]
        if query:
            projects = [
                p
                for p in projects
                if query
                in " ".join([p.name, p.description, p.template or "", *p.tags]).casefold()
            ]
        index = self.sortCombo.currentIndex()
        if index == 1:
            projects.sort(key=lambda p: p.createdAt or "")
        elif index == 2:
            projects.sort(key=lambda p: p.name.casefold())
        else:
            projects.sort(key=lambda p: p.updatedAt or "", reverse=True)
        return projects

    def _renderProjects(self) -> None:
        for card in self._cards:
            card.deleteLater()
        self._cards = []
        while self.cardGrid.count():
            self.cardGrid.takeAt(0)

        projects = self._filteredProjects()
        for project in projects:
            card = _ProjectCard(project, self.cardGridHost)
            card.openRequested.connect(self._doOpen)
            card.renameRequested.connect(self._doRename)
            card.statusRequested.connect(self._doSetStatus)
            card.deleteRequested.connect(self._doDelete)
            self._cards.append(card)

        noProjects = not self._allProjects
        noResults = bool(self._allProjects) and not projects
        self.metricsHost.setVisible(not noProjects)
        self.toolbar.setVisible(not noProjects)
        self.emptyHost.setVisible(noProjects)
        self.cardGridHost.setVisible(bool(projects))
        self.resultLabel.setText(
            "没有匹配的项目" if noResults else f"共 {len(projects)} 个项目"
        )
        self._reflowCards(force=True)

    def _onFilterChanged(self, key: str) -> None:
        self._filter = key or "all"
        self._renderProjects()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._reflowResponsive)

    def _reflowResponsive(self) -> None:
        try:
            self._reflowLayout()
            self._reflowCards()
        except RuntimeError:
            # 窗口关闭时可能仍有一次 singleShot 排队，Qt 子对象已销毁则直接退出。
            return

    def _reflowLayout(self, force: bool = False) -> None:
        width = self.scrollArea.viewport().width()
        mode = "compact" if width < 760 else "wide"
        if not force and mode == self._layoutMode:
            return
        self._layoutMode = mode

        if mode == "wide":
            metricPositions = (
                (self.totalMetric, 0, 0, 1, 1),
                (self.monthMetric, 0, 1, 1, 1),
                (self.resourceMetric, 0, 2, 1, 1),
            )
            toolbarPositions = (
                (self.filterHost, 0, 0, 1, 1),
                (self.searchEdit, 0, 1, 1, 1),
                (self.sortCombo, 0, 2, 1, 1),
            )
        else:
            metricPositions = (
                (self.totalMetric, 0, 0, 1, 1),
                (self.monthMetric, 0, 1, 1, 1),
                (self.resourceMetric, 1, 0, 1, 2),
            )
            toolbarPositions = (
                (self.filterHost, 0, 0, 1, 2),
                (self.searchEdit, 1, 0, 1, 1),
                (self.sortCombo, 1, 1, 1, 1),
            )

        for widget, row, column, rowSpan, columnSpan in metricPositions:
            self.metricsLayout.addWidget(widget, row, column, rowSpan, columnSpan)
        for widget, row, column, rowSpan, columnSpan in toolbarPositions:
            self.toolbarLayout.addWidget(widget, row, column, rowSpan, columnSpan)
        for column in range(3):
            self.metricsLayout.setColumnStretch(column, 0)
            self.toolbarLayout.setColumnStretch(column, 0)
        self.metricsLayout.setColumnStretch(0, 1)
        self.metricsLayout.setColumnStretch(1, 1)
        self.toolbarLayout.setColumnStretch(0, 1)
        if mode == "wide":
            self.metricsLayout.setColumnStretch(2, 1)

    def _reflowCards(self, force: bool = False) -> None:
        width = max(self.scrollArea.viewport().width() - 60, 1)
        columns = 3 if width >= 1220 else 2 if width >= 690 else 1
        if not force and columns == self._columns:
            return
        self._columns = columns
        while self.cardGrid.count():
            self.cardGrid.takeAt(0)
        for index, card in enumerate(self._cards):
            self.cardGrid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self.cardGrid.setColumnStretch(column, 1)

    def _onNewClicked(self) -> None:
        try:
            dialog = NewProjectDialog(self.window())
            if not dialog.exec():
                return
            result = dialog.getResult()
            self._setCreatingState(True, result["name"])
            projectManager.createProjectAsync(
                name=result["name"],
                description=result["description"],
                tags=result.get("tags", []),
                onSuccess=self._onProjectCreated,
                onError=self._onProjectCreateFailed,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[ProjectManagerWidget] 发起新建项目失败: {exc}")
            self._onProjectCreateFailed(str(exc))

    def _setCreatingState(self, creating: bool, name: str = "") -> None:
        self.newButton.setEnabled(not creating)
        self.refreshButton.setEnabled(not creating)
        if creating:
            self._creatingInfoBar = InfoBar.info(
                title="正在创建",
                content=f"正在创建项目「{name}」，请稍候…",
                parent=self,
                duration=-1,
                position=InfoBarPosition.TOP,
            )
        elif self._creatingInfoBar is not None:
            self._creatingInfoBar.close()
            self._creatingInfoBar = None

    def _onProjectCreated(self, project: Project) -> None:
        self._setCreatingState(False)
        InfoBar.success(
            title="已创建",
            content=f"项目「{project.name}」已创建并自动激活。",
            parent=self,
            duration=2500,
            position=InfoBarPosition.TOP,
        )

    def _onProjectCreateFailed(self, message: str) -> None:
        self._setCreatingState(False)
        InfoBar.error(
            title="创建失败",
            content=message or "无法创建项目，请稍后重试。",
            parent=self,
            duration=3000,
            position=InfoBarPosition.TOP,
        )

    def _doOpen(self, projectId: str) -> None:
        project = projectManager.getProject(projectId)
        if project is None:
            return
        projectManager.setActiveProject(projectId)
        self.projectSwitchRequested.emit(projectId)

    def _doRename(self, projectId: str) -> None:
        project = projectManager.getProject(projectId)
        if project is None:
            return
        dialog = RenameProjectDialog(self.window(), project.name)
        if dialog.exec():
            newName = dialog.getResult()
            if newName:
                projectManager.renameProject(projectId, newName)

    def _doSetStatus(self, projectId: str, status: str) -> None:
        projectManager.setProjectStatus(projectId, status)

    def _doDelete(self, projectId: str) -> None:
        project = projectManager.getProject(projectId)
        if project is None:
            return
        message = MessageBox(
            "确认删除项目",
            f"项目「{project.name}」及其中 {len(project.resources)} 个资源快照将被永久删除。",
            self.window(),
        )
        message.yesButton.setText("删除")
        message.cancelButton.setText("取消")
        if message.exec():
            projectManager.deleteProject(projectId)

    def _applyTheme(self) -> None:
        dark = isDarkTheme()
        page = pageBackgroundColor(dark).name()
        surface = "#2B3035" if dark else "#FFFFFF"
        muted = "#373E44" if dark else "#F3F5F6"
        border = "#465058" if dark else "#DDE3E7"
        text = "#F3F6F7" if dark else "#1E252B"
        secondary = "#B8C2C8" if dark else "#596873"
        accent = "#56D6C5" if dark else "#007368"
        self.setStyleSheet(
            f"""
            QWidget#projectManagerCanvas {{ background: {page}; }}
            QFrame#projectMetricCard, QFrame#projectCard, QFrame#projectEmptyState {{
                background: {surface}; border: 1px solid {border}; border-radius: 12px;
            }}
            QFrame#projectToolbar {{
                background: {surface}; border: 1px solid {border}; border-radius: 10px;
            }}
            QLabel {{ color: {text}; }}
            QLabel#projectMetricValue {{ color: {accent}; font-size: 24px; font-weight: 600; }}
            QLabel#projectCardTitle {{ color: {text}; font-size: 17px; font-weight: 600; }}
            QLabel#projectCardDescription {{ color: {secondary}; }}
            QLabel#projectTagChip {{
                background: {muted}; color: {secondary}; padding: 3px 7px; border-radius: 4px;
            }}
            QLabel#projectStatusChip {{
                background: {muted}; color: {secondary}; padding: 3px 8px; border-radius: 4px;
                font-size: 12px; font-weight: 600;
            }}
            QLabel#projectStatusChip[status="active"] {{ background: rgba(16,124,16,0.13); color: {'#72D572' if dark else '#107C10'}; }}
            QLabel#projectStatusChip[status="paused"] {{ background: rgba(193,156,0,0.14); color: {'#F4D35E' if dark else '#725A00'}; }}
            QLabel#projectStatusChip[status="archived"] {{ background: {muted}; color: {secondary}; }}
            QFrame#projectCardDivider {{ background: {border}; border: none; }}
            QPushButton#projectOpenButton {{ color: {accent}; border: none; background: transparent; }}
            QFrame#projectEmptyIcon {{ background: rgba(0,176,156,0.11); border: none; border-radius: 40px; }}
            """
        )
__all__ = ["ProjectManagerWidget"]
