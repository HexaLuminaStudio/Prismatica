# coding: utf-8
"""项目管理子界面（PRD-002 REQ-PROJ-001）

第 7 个 subInterface,挂在 NavigationItemPosition.SCROLL。
内部按当前是否有激活项目动态切换:

    - 有激活项目 → 显示 ProjectDashboardWidget(3 栏仪表盘:资源池 / 详情 / 笔记+AI 解读)
    - 无激活项目 → 显示 ProjectManagerWidget(列表 + 新建入口)

切换逻辑由 activeProjectChanged 信号驱动,无需手动调用。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import InfoBar, InfoBarPosition

from app.core.services import projectManager
from app.core.utils import logger

from app.view.widgets.project_dashboard_widget import ProjectDashboardWidget
from app.view.widgets.project_manager_widget import ProjectManagerWidget


class ProjectInterface(QWidget):
    """项目管理子界面(列表页 / 仪表盘 二合一容器)。

    Signals:
        jumpToModule(str): 用户在仪表盘点击「跳转分析模块」时发射,
                          参数为目标模块 key(供 main_window 路由)

    切页语义:
        - 默认跟随 activeProjectChanged:有激活 → 仪表盘,无激活 → 列表
        - 用户在仪表盘点「← 返回列表」:切回列表,**不**改动 activeProject
        - 用户在列表点「打开」:设 activeProject,触发上面的默认行为自动切仪表盘
    """

    jumpToModule = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent)
        self.setObjectName("projectInterface")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Stacked:0 = 仪表盘(有项目),1 = 列表/引导(无项目)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.stack)

        # 仪表盘页
        self.dashboard = ProjectDashboardWidget(self)
        self.dashboard.jumpToModule.connect(self._onDashboardJump)
        self.dashboard.backRequested.connect(self._onDashboardBack)
        self.stack.addWidget(self.dashboard)  # idx=0

        # 列表/引导页
        self.managerWidget = ProjectManagerWidget(self)
        # 列表点「打开」→ 项目被设为激活 → 我们的 _onActiveProjectChanged 会自动切仪表盘
        # 这里无需手动 connect projectSwitchRequested
        self.stack.addWidget(self.managerWidget)  # idx=1

        # 监听激活项目变化 → 默认跟随切页
        projectManager.activeProjectChanged.connect(self._onActiveProjectChanged)
        self._syncStack()

    # ------------------------------------------------------------------
    # 切页
    # ------------------------------------------------------------------
    def _syncStack(self) -> None:
        """根据是否有激活项目切换 stack 当前页(默认跟随)。"""
        try:
            active = projectManager.activeProject()
            if active is not None:
                self.stack.setCurrentIndex(0)
            else:
                self.stack.setCurrentIndex(1)
        except Exception as e:
            logger.warning(f"[ProjectInterface] _syncStack 失败: {e}")
            self.stack.setCurrentIndex(1)

    def _onActiveProjectChanged(self, _projectId: str) -> None:
        """激活项目变更 → 跟随切页(让用户进列表点打开后自动进仪表盘)。"""
        self._syncStack()

    def _onDashboardBack(self) -> None:
        """仪表盘的「← 返回列表」→ 切回列表页,**不**改变 activeProjectId。"""
        try:
            self.stack.setCurrentIndex(1)
        except Exception as e:
            logger.warning(f"[ProjectInterface] _onDashboardBack 失败: {e}")

    def _onDashboardJump(self, moduleKey: str) -> None:
        """仪表盘点击「跳转分析模块」→ 透传给 main_window 路由。"""
        try:
            self.jumpToModule.emit(moduleKey)
            # 给用户一个轻量反馈(可选 — 仪表盘自己有按钮反馈)
            InfoBar.success(
                title="跳转中",
                content=f"正在切到「{moduleKey}」模块…",
                parent=self,
                duration=1500,
                position=InfoBarPosition.TOP,
            )
        except Exception as e:
            logger.warning(f"[ProjectInterface] _onDashboardJump 失败: {e}")


__all__ = ["ProjectInterface"]
