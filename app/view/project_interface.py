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
    # 仪表盘 busy 状态变化(AI 报告生成期间 True)—— 透传给 MainWindow,
    # 用来锁定导航切换 + 拦截主窗口关闭。
    busyChanged = Signal(bool)

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
        # 透传 busy 状态(AI 报告生成时锁住页面交互)
        self.dashboard.busyChanged.connect(self.busyChanged)
        self.stack.addWidget(self.dashboard)  # idx=0

        # 列表/引导页
        self.managerWidget = ProjectManagerWidget(self)
        # 列表点「打开」→ 强制切到仪表盘(无论激活项目是否变化)。
        # P0-fix 2026-07-27:此前仅依赖 activeProjectChanged 间接驱动,
        # 在重复点击同一项目时该信号不会重发,导致"InfoBar 显示但页面不切"。
        self.managerWidget.projectSwitchRequested.connect(
            self._onProjectSwitchRequested
        )
        self.stack.addWidget(self.managerWidget)  # idx=1

        # 监听激活项目变化 → 默认跟随切页
        projectManager.activeProjectChanged.connect(self._onActiveProjectChanged)
        self._syncStack()

    # ------------------------------------------------------------------
    # busy 状态查询(供 MainWindow 使用)
    # ------------------------------------------------------------------
    def isBusy(self) -> bool:
        """当前是否处于 AI 报告生成中(锁页交互)。"""
        try:
            return bool(self.dashboard.isBusy())
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 切页
    # ------------------------------------------------------------------
    def _syncStack(self) -> None:
        """根据是否有激活项目切换 stack 当前页(默认跟随)。"""
        try:
            active = projectManager.activeProject()
            if active is not None:
                logger.info(
                    f"[ProjectInterface] _syncStack → 切到仪表盘 (active={active.id})"
                )
                self.stack.setCurrentIndex(0)
            else:
                logger.info("[ProjectInterface] _syncStack → 切到列表页")
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

    def _onProjectSwitchRequested(self, _projectId: str) -> None:
        """列表点「打开」→ 强制切到仪表盘。

        设计要点:
            - 与 _onActiveProjectChanged 互补,后者在激活项目**变更**时切页,
              但用户在列表里点"打开"已激活项目时,setActiveProject 会返回
              False 且不发信号,导致页面不切。
            - 此槽直接根据当前是否有激活项目切到仪表盘,只要用户点了"打开",
              就一定能看到仪表盘内容(即使点击的就是已激活项目)。
        """
        try:
            if self.stack.currentIndex() != 0:
                logger.info("[ProjectInterface] _onProjectSwitchRequested → 切到仪表盘")
                self.stack.setCurrentIndex(0)
            # 强制让 dashboard 重新同步(防止用户已切换过项目但 dashboard
            # 内部缓存指向旧 id)
            try:
                self.dashboard._syncFromManager()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"[ProjectInterface] _onProjectSwitchRequested 失败: {e}")

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
