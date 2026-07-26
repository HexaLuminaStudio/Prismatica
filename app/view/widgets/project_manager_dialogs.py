# coding: utf-8
"""项目管理对话框（PRD-002 REQ-PROJ-001）

- NewProjectDialog: 新建项目（名称 + 描述 + 标签 + 模板选择）
- RenameProjectDialog: 重命名项目（仅名称）

MVP 阶段不实现模板细节,只保留 UI 占位（默认下拉 +「自定义」）。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    LineEdit,
    MessageBoxBase,
    PlainTextEdit,
    StrongBodyLabel,
    SubtitleLabel,
)

# 内置模板占位（PRD §4.2 F6）
_BUILTIN_TEMPLATES = [
    "空白项目",
    "中介语偏误分析",
    "构式语法研究",
    "语体对比研究",
]


class _BaseProjectDialog(MessageBoxBase):
    """对话框基类（统一样式）"""

    def __init__(self, parent: Optional[QWidget] = None, title: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        # 避免主窗口尺寸变更/动画卡住 exec() 返回
        if parent is not None and parent.isVisible():
            self.setGeometry(0, 0, parent.width(), parent.height())
        self._buildUi()

    def done(self, code) -> None:
        """禁用 qfluentwidgets 的淡出动画,直接关闭。

        背景:MaskDialogBase.done 会启动 opacity 动画并在 finished 后才调
        QDialog.done(),在 Windows 上偶发 finished 信号丢失,导致 dialog.exec()
        永不返回,主窗口表现为「整个软件无响应」。这里直接走基类关闭流程。
        """
        from PySide6.QtWidgets import QDialog

        QDialog.done(self, code)


class NewProjectDialog(_BaseProjectDialog):
    """新建项目对话框"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, title="新建研究项目")

    def _buildUi(self) -> None:
        view = self.viewLayout
        parent = self.widget

        view.addWidget(StrongBodyLabel("项目名称", parent))
        self.nameEdit = LineEdit(parent)
        self.nameEdit.setPlaceholderText("例如：V都V了构式研究")
        view.addWidget(self.nameEdit)

        view.addSpacing(8)
        view.addWidget(StrongBodyLabel("一句话描述", parent))
        self.descEdit = LineEdit(parent)
        self.descEdit.setPlaceholderText("可选项 - 简要说明本项目的研究目的")
        view.addWidget(self.descEdit)

        view.addSpacing(8)
        view.addWidget(StrongBodyLabel("模板", parent))
        self.templateCombo = ComboBox(parent)
        for tpl in _BUILTIN_TEMPLATES:
            self.templateCombo.addItem(tpl)
        self.templateCombo.setCurrentIndex(0)
        view.addWidget(self.templateCombo)

        view.addSpacing(4)
        hint = BodyLabel(
            "提示：MVP 阶段模板仅作为占位标识,不会自动初始化分析模块。",
            parent,
        )
        hint.setStyleSheet("color: gray; font-size: 11px;")
        view.addWidget(hint)

        # 默认按钮文本
        self.yesButton.setText("创建")
        self.cancelButton.setText("取消")
        # 名称必填校验:yesButton 默认启用,在 nameEdit 变化时切换
        self.nameEdit.textChanged.connect(self._onNameChanged)
        self._onNameChanged(self.nameEdit.text())

    def _onNameChanged(self, text: str) -> None:
        self.yesButton.setEnabled(bool(text.strip()))

    def getResult(self) -> dict:
        """返回 (name, description, template) 元组;若用户取消返回 None"""
        return {
            "name": self.nameEdit.text().strip(),
            "description": self.descEdit.text().strip(),
            "template": self.templateCombo.currentText(),
        }


class RenameProjectDialog(_BaseProjectDialog):
    """重命名项目对话框"""

    def __init__(self, parent: Optional[QWidget] = None, currentName: str = "") -> None:
        # 必须先于 super().__init__() 赋值:_BaseProjectDialog.__init__ 会立刻调
        # self._buildUi(),而 _buildUi 要用 self._currentName 给 LineEdit 填初值。
        self._currentName = currentName
        super().__init__(parent, title="重命名项目")
        # 基类 __init__ 已经调过一次 _buildUi(),此处无需重复调用。

    def _buildUi(self) -> None:
        view = self.viewLayout
        parent = self.widget

        view.addWidget(StrongBodyLabel("新名称", parent))
        self.nameEdit = LineEdit(parent)
        self.nameEdit.setText(self._currentName)
        self.nameEdit.selectAll()
        view.addWidget(self.nameEdit)

        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")
        self.nameEdit.textChanged.connect(self._onNameChanged)
        self._onNameChanged(self.nameEdit.text())

    def _onNameChanged(self, text: str) -> None:
        newName = text.strip()
        self.yesButton.setEnabled(bool(newName) and newName != self._currentName)

    def getResult(self) -> Optional[str]:
        name = self.nameEdit.text().strip()
        if not name or name == self._currentName:
            return None
        return name


__all__ = ["NewProjectDialog", "RenameProjectDialog"]
