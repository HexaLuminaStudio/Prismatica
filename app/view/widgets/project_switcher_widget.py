# coding: utf-8
"""顶栏项目切换器（PRD-002 REQ-PROJ-001）

嵌入 TitleBar hBoxLayout 的 ComboBox:
    - 显示当前激活项目名(无激活时显示「未选择项目」,不可选的占位项)
    - 下拉项:最近 N 个项目 + 「📁 项目管理…」入口 + 「➕ 新建项目…」入口
    - 选项目 → projectManager.setActiveProject(id)
    - 监听 activeProjectChanged → 刷新当前显示文本(避免回环)

设计原则:
    - 复用 qfluentwidgets.ComboBox,不引入新组件
    - 失败容错:任何异常仅 log,不抛给上层
    - 不在切换项目时清空当前分析结果(避免破坏现有用户体验)
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import ComboBox

from app.core.services import projectManager


# 切换器中的特殊项数据(用字符串 sentinel 与正常项目 id 区分)
_SENTINEL_MANAGE = "__manage__"
_SENTINEL_NEW = "__new__"
_SENTINEL_NO_ACTIVE = "__no_active__"  # 无激活项目时的占位项,不可选

# 「未选择项目」占位文本(同时用作 ComboBox 当前显示文本)
_PLACEHOLDER_NO_ACTIVE = "未选择项目"


class ProjectSwitcher(QWidget):
    """顶栏项目切换器(嵌入 TitleBar)"""

    manageRequested = Signal()  # 用户选择「项目管理」入口
    newRequested = Signal()  # 用户选择「新建项目」入口

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._suppressEmit = False
        self._buildUi()
        self._connectSignals()
        self.refresh()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _buildUi(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 项目图标标识(纯装饰,用文字 emoji,不依赖额外图标资源)
        from qfluentwidgets import CaptionLabel

        self._iconLabel = CaptionLabel("📁", self)
        self._iconLabel.setFixedWidth(16)
        layout.addWidget(self._iconLabel)

        self._comboBox = ComboBox(self)
        self._comboBox.setMinimumWidth(160)
        self._comboBox.setMaximumWidth(220)
        self._comboBox.setToolTip("切换当前研究项目")
        layout.addWidget(self._comboBox)

    # ------------------------------------------------------------------
    # 信号连接
    # ------------------------------------------------------------------
    def _connectSignals(self) -> None:
        # 用户切换 ComboBox
        self._comboBox.currentIndexChanged.connect(self._onComboChanged)
        # 监听 ProjectManager 信号,刷新 UI
        projectManager.activeProjectChanged.connect(self._onActiveProjectChanged)
        projectManager.projectListChanged.connect(self.refresh)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """从 projectManager 拉取最新数据,重建下拉项

        重建策略:
            1. 当前激活项目(若存在)始终在第 0 位
            2. 其余项目按 updated_at DESC 取最近 N-1 个
            3. 末尾固定追加「📁 项目管理…」与「➕ 新建项目…」
        """
        try:
            self._suppressEmit = True
            try:
                # 先清空
                self._comboBox.blockSignals(True)
                self._comboBox.clear()

                # 当前激活项目
                activeProject = projectManager.activeProject()
                allProjects = projectManager.listProjects()

                # 当前激活放最前
                ordered: List = []
                seenIds = set()
                if activeProject is not None:
                    ordered.append(activeProject)
                    seenIds.add(activeProject.id)
                # 其余按 updated_at DESC 补全
                for p in allProjects:
                    if p.id in seenIds:
                        continue
                    ordered.append(p)
                    seenIds.add(p.id)
                # 截断到最多 5 个(避免下拉过长)
                visibleProjects = ordered[:5]
                # 无激活时:先在第 0 位插入「未选择项目」占位项(不可选)
                if activeProject is None:
                    self._comboBox.addItem(
                        _PLACEHOLDER_NO_ACTIVE, userData=_SENTINEL_NO_ACTIVE
                    )
                for p in visibleProjects:
                    self._comboBox.addItem(p.name, userData=p.id)
                # 当前激活项目的显示文本
                currentText = (
                    activeProject.name
                    if activeProject is not None
                    else _PLACEHOLDER_NO_ACTIVE
                )
                # 末尾追加两个特殊入口
                self._comboBox.addItem("──────────────", userData="__sep__")
                self._comboItemManage = self._comboBox.addItem(
                    "📁 项目管理…", userData=_SENTINEL_MANAGE
                )
                self._comboItemNew = self._comboBox.addItem(
                    "➕ 新建项目…", userData=_SENTINEL_NEW
                )
                # 设置当前项
                if activeProject is not None:
                    idx = self._comboBox.findData(activeProject.id)
                    if idx >= 0:
                        self._comboBox.setCurrentIndex(idx)
                    else:
                        # 激活项目不在前 5 个里(罕见),把当前显示设为 active 名
                        self._comboBox.setCurrentIndex(0)
                        self._comboBox.setItemText(0, activeProject.name)
                else:
                    # 无激活:把 ComboBox 选中「未选择项目」占位项
                    idx = self._comboBox.findData(_SENTINEL_NO_ACTIVE)
                    if idx >= 0:
                        self._comboBox.setCurrentIndex(idx)
                    else:
                        self._comboBox.setCurrentIndex(0)
                self._comboBox.setCurrentText(currentText)
            finally:
                self._comboBox.blockSignals(False)
                self._suppressEmit = False
        except Exception as e:
            from loguru import logger

            logger.warning(f"[ProjectSwitcher] refresh 失败: {e}")

    # ------------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------------
    def _onComboChanged(self, index: int) -> None:
        if self._suppressEmit:
            return
        try:
            data = self._comboBox.itemData(index)
        except Exception:
            data = None
        if data is None or data == "__sep__":
            return
        if data == _SENTINEL_NO_ACTIVE:
            # 「未选择项目」占位项,不可选,直接忽略
            return
        if data == _SENTINEL_MANAGE:
            # 「项目管理」入口:发信号后保持当前选择(避免下拉空白)
            self.manageRequested.emit()
            # 还原下拉为当前激活
            self.refresh()
            return
        if data == _SENTINEL_NEW:
            self.newRequested.emit()
            self.refresh()
            return
        # 正常项目切换
        projectManager.setActiveProject(str(data))

    def _onActiveProjectChanged(self, projectId: str) -> None:
        """ProjectManager 信号回调 — 刷新显示文本"""
        try:
            self._suppressEmit = True
            self._comboBox.blockSignals(True)
            currentProject = projectManager.activeProject()
            if currentProject is None:
                # 无激活时显示「未选择项目」(兜底,正常情况 refresh() 已处理)
                idx = self._comboBox.findData(_SENTINEL_NO_ACTIVE)
                if idx >= 0:
                    self._comboBox.setCurrentIndex(idx)
                self._comboBox.setCurrentText(_PLACEHOLDER_NO_ACTIVE)
            else:
                # 找当前项,改名
                idx = self._comboBox.findData(currentProject.id)
                if idx >= 0:
                    self._comboBox.setCurrentIndex(idx)
                else:
                    # 不在下拉里(被截断到 5 个以外)→ 重建
                    # 先解 block 再 refresh(避免 refresh 内 setCurrentText 二次触发)
                    self._comboBox.blockSignals(False)
                    self._suppressEmit = False
                    self.refresh()
                    return
        except Exception as e:
            from loguru import logger

            logger.warning(f"[ProjectSwitcher] _onActiveProjectChanged 失败: {e}")
        finally:
            self._comboBox.blockSignals(False)
            self._suppressEmit = False


__all__ = ["ProjectSwitcher"]
