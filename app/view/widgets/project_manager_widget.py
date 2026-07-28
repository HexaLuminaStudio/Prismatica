# coding: utf-8
"""项目管理列表组件（PRD-002 REQ-PROJ-001）

展示所有项目 + 资源数 + 操作（打开/重命名/删除）+ 顶栏新建按钮。

布局:
    ┌────────────────────────────────────────────────────────┐
    │ [➕ 新建项目]  [🔄 刷新]            当前项目: V都V了研究 │
    ├────────────────────────────────────────────────────────┤
    │  名称            标签      资源数   状态   操作         │
    │  V都V了研究      构式/口语  12 资源   进行   [打开][编辑][删除] │
    │  偏误统计        偏误      8 资源    写作   [打开][编辑][删除] │
    │  语体对比        语体      5 资源    暂停   [打开][编辑][删除] │
    └────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QVBoxLayout,
    QWidget,
    QTableWidgetItem,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
    ToolButton,
)

from app.core.models.project import Project
from app.core.services import projectManager
from app.core.utils import logger
from app.view.widgets.project_manager_dialogs import (
    NewProjectDialog,
    RenameProjectDialog,
)


class ProjectManagerWidget(QWidget):
    """项目管理列表(QWidget 子组件,挂到 ProjectInterface)"""

    projectSwitchRequested = Signal(str)  # 用户在表中点「打开」,参数 projectId

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectManagerWidget")
        self._buildUi()
        self._connectSignals()
        self.refresh()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _buildUi(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        # 顶栏
        topBar = QHBoxLayout()
        topBar.setSpacing(8)

        self.titleLabel = SubtitleLabel("项目管理", self)
        topBar.addWidget(self.titleLabel)

        topBar.addStretch(1)

        self.activeLabel = CaptionLabel("当前项目: —", self)
        topBar.addWidget(self.activeLabel)

        self.newButton = PrimaryPushButton(FluentIcon.ADD, "新建项目", self)
        topBar.addWidget(self.newButton)

        self.refreshButton = ToolButton(FluentIcon.SYNC, self)
        self.refreshButton.setToolTip("刷新列表")
        topBar.addWidget(self.refreshButton)

        outer.addLayout(topBar)

        # 表格
        self.table = TableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["名称", "标签", "资源数", "状态", "操作"])
        self.table.setEditTriggers(TableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(TableWidget.SelectRows)
        self.table.setSelectionMode(TableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        # 列宽自适应
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        outer.addWidget(self.table, 1)

        # 提示
        self.hintLabel = BodyLabel(
            "MVP 阶段：项目用于把分析结果和笔记归档到一起,可在顶栏下拉切换。",
            self,
        )
        self.hintLabel.setStyleSheet("color: gray;")
        outer.addWidget(self.hintLabel)

    # ------------------------------------------------------------------
    # 信号连接
    # ------------------------------------------------------------------
    def _connectSignals(self) -> None:
        self.newButton.clicked.connect(self._onNewClicked)
        self.refreshButton.clicked.connect(self.refresh)
        projectManager.projectListChanged.connect(self.refresh)
        projectManager.activeProjectChanged.connect(self._onActiveProjectChanged)
        # 表格操作列按钮的点击由 _fillRow() 内逐按钮 connected,无需依赖 cellClicked

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """重新从 projectManager 拉数据,重建表格"""
        try:
            projects = projectManager.listProjects()
            self.table.setRowCount(len(projects))
            for row, project in enumerate(projects):
                self._fillRow(row, project)
            self._updateActiveLabel()
        except Exception as e:
            logger.exception(f"[ProjectManagerWidget] refresh 失败: {e}")

    def _fillRow(self, row: int, project: Project) -> None:
        # 名称

        nameItem = QTableWidgetItem(project.name)
        # 用 UserRole 存项目 id,便于后续按 id 找行
        nameItem.setData(Qt.UserRole, project.id)
        self.table.setItem(row, 0, nameItem)
        # 标签
        tagsText = " / ".join(project.tags) if project.tags else "—"
        tagsItem = QTableWidgetItem(tagsText)
        self.table.setItem(row, 1, tagsItem)
        # 资源数
        count = len(project.resources)
        countItem = QTableWidgetItem(f"{count} 资源")
        countItem.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 2, countItem)
        # 状态
        statusMap = {
            "active": "进行中",
            "paused": "暂停",
            "archived": "已归档",
        }
        statusText = statusMap.get(project.status, project.status)
        statusItem = QTableWidgetItem(statusText)
        statusItem.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 3, statusItem)
        # 操作列(三个按钮)
        from qfluentwidgets import TransparentPushButton

        opWidget = QWidget(self.table)
        opLayout = QHBoxLayout(opWidget)
        opLayout.setContentsMargins(4, 0, 4, 0)
        opLayout.setSpacing(4)
        openBtn = TransparentPushButton("打开", opWidget)
        renameBtn = TransparentPushButton("重命名", opWidget)
        deleteBtn = TransparentPushButton("删除", opWidget)
        openBtn.setFixedHeight(24)
        renameBtn.setFixedHeight(24)
        deleteBtn.setFixedHeight(24)
        # 用闭包把 projectId 绑到按钮的 clicked 槽 — 不依赖 focusWidget / cellClicked
        # (旧实现靠 self.table.focusWidget() 拿 button,在 TableWidget 中焦点常常
        #  落在表格本体而非按钮,导致点击无反应)
        openBtn.clicked.connect(
            lambda _checked=False, pid=project.id: self._doOpen(pid)
        )
        renameBtn.clicked.connect(
            lambda _checked=False, pid=project.id: self._doRename(pid)
        )
        deleteBtn.clicked.connect(
            lambda _checked=False, pid=project.id: self._doDelete(pid)
        )
        opLayout.addWidget(openBtn)
        opLayout.addWidget(renameBtn)
        opLayout.addWidget(deleteBtn)
        opLayout.addStretch(0)
        self.table.setCellWidget(row, 4, opWidget)

    def _updateActiveLabel(self) -> None:
        active = projectManager.activeProject()
        if active is None:
            self.activeLabel.setText("当前项目: —")
        else:
            self.activeLabel.setText(f"当前项目: {active.name}")

    # ------------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------------
    def _onNewClicked(self) -> None:
        try:
            dialog = NewProjectDialog(self.window())
            if not dialog.exec():
                return
            result = dialog.getResult()
            name = result["name"]
            template = result["template"]
            description = result["description"]
            # 进入「创建中」状态:禁用按钮 + 提示,避免重复点击
            self._setCreatingState(True, name)
            # 异步创建项目(磁盘 I/O 跑在子线程,不阻塞 UI)
            projectManager.createProjectAsync(
                name=name,
                template=template,
                description=description,
                onSuccess=self._onProjectCreated,
                onError=self._onProjectCreateFailed,
            )
        except Exception as e:
            logger.exception(f"[ProjectManagerWidget] 发起新建项目失败: {e}")
            InfoBar.error(
                title="创建失败",
                content=str(e),
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )
            self._setCreatingState(False)

    def _setCreatingState(self, creating: bool, name: str = "") -> None:
        """切换「正在创建项目」的 UI 状态(禁用按钮 / 显示进度)。"""
        try:
            self.newButton.setEnabled(not creating)
            self.refreshButton.setEnabled(not creating)
            if creating:
                self._creatingInfoBar = InfoBar.info(
                    title="正在创建",
                    content=f"正在创建项目「{name}」,请稍候…",
                    parent=self,
                    duration=-1,  # 不自动关闭
                    position=InfoBarPosition.TOP,
                )
            else:
                # 关闭进行中的 InfoBar
                bar = getattr(self, "_creatingInfoBar", None)
                if bar is not None:
                    try:
                        bar.close()
                    except Exception:
                        pass
                    self._creatingInfoBar = None
        except Exception as e:
            logger.warning(f"[ProjectManagerWidget] _setCreatingState 异常: {e}")

    def _onProjectCreated(self, project: Project) -> None:
        """异步创建成功回调(主线程)。"""
        self._setCreatingState(False)
        logger.info(f"[ProjectManagerWidget] 新建项目: {project.name}")
        InfoBar.success(
            title="已创建",
            content=f"项目「{project.name}」已创建并自动激活",
            parent=self,
            duration=2500,
            position=InfoBarPosition.TOP,
        )

    def _onProjectCreateFailed(self, errMsg: str) -> None:
        """异步创建失败回调(主线程)。"""
        self._setCreatingState(False)
        logger.warning(f"[ProjectManagerWidget] 新建项目失败: {errMsg}")
        InfoBar.error(
            title="创建失败",
            content=errMsg,
            parent=self,
            duration=3000,
            position=InfoBarPosition.TOP,
        )

    def _onActiveProjectChanged(self, projectId: str) -> None:
        self._updateActiveLabel()

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _doOpen(self, projectId: str) -> None:
        project = projectManager.getProject(projectId)
        if project is None:
            logger.warning(f"[ProjectManagerWidget] _doOpen: 找不到项目 id={projectId}")
            InfoBar.error(
                title="打开失败",
                content=f"找不到该项目(可能已被删除):{projectId}",
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )
            return
        logger.info(
            f"[ProjectManagerWidget] _doOpen: project={project.name} (id={projectId})"
        )
        changed = projectManager.setActiveProject(projectId)
        # 关键:无论 setActiveProject 是否实际改变(重复点击同一项目
        # 会返回 False),只要用户点了"打开",就强制通知 ProjectInterface
        # 切到仪表盘 — 否则会出现"InfoBar 显示但页面不切"的体验问题。
        InfoBar.success(
            title="已切换",
            content=f"已切换到项目「{project.name}」",
            parent=self,
            duration=2000,
            position=InfoBarPosition.TOP,
        )
        # 显式触发切换信号(此前依赖 activeProjectChanged 间接驱动,
        # 在重复点击同一项目时该信号不会重发,导致切页失效)。
        self.projectSwitchRequested.emit(projectId)

    def _doRename(self, projectId: str) -> None:
        project = projectManager.getProject(projectId)
        if project is None:
            return
        try:
            dialog = RenameProjectDialog(self.window(), currentName=project.name)
            if dialog.exec():
                newName = dialog.getResult()
                if newName and projectManager.renameProject(projectId, newName):
                    InfoBar.success(
                        title="已重命名",
                        content=f"项目已重命名为「{newName}」",
                        parent=self,
                        duration=2000,
                        position=InfoBarPosition.TOP,
                    )
        except Exception as e:
            logger.exception(f"[ProjectManagerWidget] 重命名失败: {e}")

    def _doDelete(self, projectId: str) -> None:
        project = projectManager.getProject(projectId)
        if project is None:
            return
        # 二次确认(防止误删)
        msgBox = MessageBox(
            "确认删除",
            f"确定要删除项目「{project.name}」吗？\n\n"
            f"该操作会删除项目文件夹（含 {len(project.resources)} 个资源快照）,"
            f"且不可恢复。",
            self.window(),
        )
        msgBox.yesButton.setText("删除")
        msgBox.cancelButton.setText("取消")
        if msgBox.exec():
            if projectManager.deleteProject(projectId):
                InfoBar.success(
                    title="已删除",
                    content=f"项目「{project.name}」已删除",
                    parent=self,
                    duration=2000,
                    position=InfoBarPosition.TOP,
                )


__all__ = ["ProjectManagerWidget"]
