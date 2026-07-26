# coding: utf-8
"""项目仪表盘容器（PRD-002 REQ-PROJ-001 / F3 项目仪表盘）

把三个面板组装成 3 栏布局:

    ┌──────────┬────────────────┬────────────────┐
    │ 资源池   │ 资源详情       │ 笔记 + AI 解读 │
    │ (左)     │ (中)           │ (右,占位)      │
    │ 240px    │ 自适应          │ 280px          │
    └──────────┴────────────────┴────────────────┘

面板之间通过容器协调:
    - 资源池选中 → 详情面板更新 + 笔记面板聚焦「资源级笔记」(后续)
    - 跳转按钮 → 容器路由到对应分析模块(emit jumpToModule(type) 信号)

当前激活项目切换时:
    - 监听 projectManager.activeProjectChanged → 自动 setProject(id)
    - 监听 projectManager.projectListChanged → 自动 refresh
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, PushButton

from app.core.models.project import Resource
from app.core.services import projectManager
from app.core.utils import logger

from .project_dashboard_widgets import (
    AiInsightsPanel,
    ResourceDetailPanel,
    ResourcePoolPanel,
)


# 资源类型 → 对应分析模块的路由表(供跳转按钮使用)
# 后续可扩展;此处映射对齐现有 freq_analyzer_interface 等命名
_RESOURCE_TYPE_TO_MODULE = {
    "freq": "freq_analyzer",  # 词频分析
    "collocation": "freq_analyzer",  # 搭配(同一模块的子视图)
    "network": "freq_analyzer",  # 共现网络
    "kwic": "freq_analyzer",  # KWIC 检索
    "construction": "freq_analyzer",  # 构式识别
    "dependency": "freq_analyzer",  # 句法依存
    "keyword_list": "freq_analyzer",  # 关键词
    "ngram_cluster": "freq_analyzer",  # N-gram
    "sentiment": "freq_analyzer",  # 情感分析
    "word_cloud": "freq_analyzer",  # 词云
    "word_analysis": "freq_analyzer",  # 词语分析
}


class ProjectDashboardWidget(QWidget):
    """项目仪表盘容器 — 3 栏布局。

    Signals:
        jumpToModule(str): 用户点击详情面板的「跳转分析模块」时发射,
                          参数为目标模块的 key(参见 _RESOURCE_TYPE_TO_MODULE)
        backRequested():  用户点击顶部 banner 的「← 返回列表」时发射,
                          让父容器切回 ProjectManagerWidget 列表页。
                          注:**不**修改 activeProjectId,用户再次进入仪表盘
                          时仍在同一项目上下文。
    """

    jumpToModule = Signal(str)
    backRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectDashboardWidget")
        self._currentProjectId: str = ""
        self._buildUi()
        self._connectSignals()
        self._syncFromManager()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _buildUi(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 顶部 banner:项目名 + 状态 + 返回按钮
        self.banner = QWidget(self)
        self.banner.setObjectName("dashboardBanner")
        self.banner.setStyleSheet(
            "QWidget#dashboardBanner { background: #f5f7fa; border-bottom: 1px solid #e5e7eb; }"
        )
        bannerLayout = QHBoxLayout(self.banner)
        bannerLayout.setContentsMargins(16, 10, 16, 10)
        bannerLayout.setSpacing(8)
        # 返回按钮(在最左侧)— 让用户能从仪表盘回到项目管理列表
        self.backButton = PushButton("← 返回列表", self.banner)
        self.backButton.setToolTip("返回项目管理列表(不改变当前激活项目)")
        self.backButton.clicked.connect(self.backRequested.emit)
        bannerLayout.addWidget(self.backButton)
        self.projectNameLabel = BodyLabel("当前项目:—", self.banner)
        self.projectNameLabel.setStyleSheet("font-size: 14px; font-weight: bold;")
        bannerLayout.addWidget(self.projectNameLabel)
        bannerLayout.addStretch(1)
        self.projectMetaLabel = BodyLabel("", self.banner)
        self.projectMetaLabel.setStyleSheet("color: gray; font-size: 12px;")
        bannerLayout.addWidget(self.projectMetaLabel)
        outer.addWidget(self.banner)

        # 3 栏主体
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # 左:资源池(固定宽度)
        self.poolPanel = ResourcePoolPanel(self)
        self.poolPanel.setMinimumWidth(220)
        self.poolPanel.setMaximumWidth(320)
        self.poolPanel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        # 中右之间加竖向分隔
        self.poolPanel.setStyleSheet(
            "QWidget#resourcePoolPanel { border-right: 1px solid #e5e7eb; }"
        )
        body.addWidget(self.poolPanel)

        # 中:详情(自适应宽度)
        self.detailPanel = ResourceDetailPanel(self)
        self.detailPanel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body.addWidget(self.detailPanel, 1)

        # 右:笔记 + AI 解读(固定宽度)
        self.aiInsightsPanel = AiInsightsPanel(self)
        self.aiInsightsPanel.setFixedWidth(550)
        self.aiInsightsPanel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.aiInsightsPanel.setStyleSheet(
            "QWidget#aiInsightsPanel { border-left: 1px solid #e5e7eb; }"
        )
        body.addWidget(self.aiInsightsPanel)

        outer.addLayout(body, 1)

    # ------------------------------------------------------------------
    # 信号
    # ------------------------------------------------------------------
    def _connectSignals(self) -> None:
        # 内部联动
        self.poolPanel.resourceSelected.connect(self._onResourceSelected)
        self.poolPanel.resourceDoubleClicked.connect(self._onResourceDoubleClicked)
        self.detailPanel.jumpRequested.connect(self._onJumpRequested)
        # 全局事件
        projectManager.activeProjectChanged.connect(self._onActiveProjectChanged)
        projectManager.projectListChanged.connect(self._syncFromManager)

    # ------------------------------------------------------------------
    # 同步 ProjectManager 状态
    # ------------------------------------------------------------------
    def _syncFromManager(self) -> None:
        """从 ProjectManager 拉取当前激活项目 id,同步给所有面板。"""
        try:
            active = projectManager.activeProject()
            newId = active.id if active is not None else ""
            self._applyProject(newId)
        except Exception as e:
            logger.warning(f"[ProjectDashboard] _syncFromManager 失败: {e}")

    def _applyProject(self, projectId: str) -> None:
        """应用新项目到三个面板 + 顶部 banner。"""
        if projectId == self._currentProjectId:
            # 项目未变,只需确保各面板刷新一次
            self.poolPanel.refresh()
            self.aiInsightsPanel.refresh()
            self._renderBanner()
            return
        self._currentProjectId = projectId
        # 切换项目 → 清空详情面板选中态
        self.detailPanel.setResource(None)
        self.poolPanel.setProject(projectId)
        self.aiInsightsPanel.setProject(projectId)
        self._renderBanner()

    def _renderBanner(self) -> None:
        """顶部 banner 显示当前项目名 + 元信息。"""
        if not self._currentProjectId:
            self.projectNameLabel.setText("当前项目:—")
            self.projectMetaLabel.setText("请先在项目管理中创建或选择一个项目")
            return
        try:
            project = projectManager.getProject(self._currentProjectId)
            if project is None:
                self.projectNameLabel.setText("当前项目:—")
                self.projectMetaLabel.setText("项目已被删除")
                return
            self.projectNameLabel.setText(f"当前项目:{project.name}")
            metaParts = []
            metaParts.append(f"状态:{project.status or 'active'}")
            metaParts.append(f"资源:{len(project.resources)} 个")
            metaParts.append(f"AI 解读:{len(project.aiInsights)} 条")
            if project.template:
                metaParts.append(f"模板:{project.template}")
            self.projectMetaLabel.setText("  ·  ".join(metaParts))
        except Exception as e:
            logger.warning(f"[ProjectDashboard] _renderBanner 失败: {e}")

    # ------------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------------
    def _onActiveProjectChanged(self, _projectId: str) -> None:
        self._syncFromManager()

    def _onResourceSelected(self, resourceId: str) -> None:
        """资源池选中 → 更新详情面板 + 通知 AI 报告范围。

        笔记面板已下放到 Word,这里不再随资源切换 scope;
        但 AI 解读面板的「生成研究报告」按钮范围会跟随资源切换:
        - 选中资源 → 报告仅基于该资源(以及项目级笔记)
        - 取消选中 → 报告基于全项目
        """
        try:
            if not resourceId:
                self.detailPanel.setResource(None)
                self.aiInsightsPanel.setResourceScope(None)
                return
            # 从当前 poolPanel 的 _resources 中找(避免再次访问 DB)
            resource: Optional[Resource] = None
            for r in self.poolPanel._resources:  # noqa: SLF001 — 同模块访问合理
                if r.id == resourceId:
                    resource = r
                    break
            self.detailPanel.setResource(resource)
            # AI 报告范围跟随资源(用于「✨ 生成研究报告」按钮)
            self.aiInsightsPanel.setResourceScope(resourceId)
        except Exception as e:
            logger.warning(f"[ProjectDashboard] _onResourceSelected 失败: {e}")

    def _onResourceDoubleClicked(self, resourceId: str) -> None:
        """双击资源池条目 → 等同于「跳转」动作。"""
        try:
            if not resourceId:
                return
            for r in self.poolPanel._resources:  # noqa: SLF001
                if r.id == resourceId:
                    if r.type:
                        moduleKey = _RESOURCE_TYPE_TO_MODULE.get(
                            r.type, "freq_analyzer"
                        )
                        self.jumpToModule.emit(moduleKey)
                    return
        except Exception as e:
            logger.warning(f"[ProjectDashboard] _onResourceDoubleClicked 失败: {e}")

    def _onJumpRequested(self, resourceType: str) -> None:
        """详情面板的「🚀 跳转分析模块」按钮 → 路由到对应模块。"""
        try:
            moduleKey = _RESOURCE_TYPE_TO_MODULE.get(resourceType, "freq_analyzer")
            self.jumpToModule.emit(moduleKey)
        except Exception as e:
            logger.warning(f"[ProjectDashboard] _onJumpRequested 失败: {e}")


__all__ = ["ProjectDashboardWidget"]
