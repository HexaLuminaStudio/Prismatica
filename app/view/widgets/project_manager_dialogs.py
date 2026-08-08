# coding: utf-8
"""项目管理 MessageBox 表单。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QAbstractButton, QDialog, QHBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    LineEdit,
    MessageBoxBase,
    PlainTextEdit,
    SegmentedWidget,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

from app.view.widgets.project_ui_helpers import PRIMARY_HEIGHT, normalizeButton


_TEMPLATES = [
    ("blank", "空白", FluentIcon.DOCUMENT),
    ("teaching", "教学研究", FluentIcon.EDUCATION),
    ("academic", "学术研究", FluentIcon.LIBRARY),
    ("custom", "自定义", FluentIcon.SETTING),
]


class _BaseProjectDialog(MessageBoxBase):
    """统一使用 Fluent MessageBox 容器，表单只填充其 ``viewLayout``。"""

    def __init__(self, parent: Optional[QWidget] = None, title: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.widget.setMinimumWidth(560)
        self.buttonGroup.setFixedHeight(92)
        if parent is not None and parent.isVisible():
            self.setGeometry(0, 0, parent.width(), parent.height())
        self._buildUi()

    def done(self, code) -> None:
        # 避免 Windows 下遮罩淡出动画偶发不结束，保留 MessageBox 外观与模态语义。
        QDialog.done(self, code)


class NewProjectDialog(_BaseProjectDialog):
    """设计稿对应的新建项目 MessageBox。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        self._templateKey = "teaching"
        super().__init__(parent, title="新建项目")

    def _buildUi(self) -> None:
        view = self.viewLayout
        parent = self.widget
        view.setSpacing(8)

        title = TitleLabel("新建项目", parent)
        view.addWidget(title)
        subtitle = BodyLabel("几秒钟创建，之后可随时补充资源与研究说明", parent)
        view.addWidget(subtitle)
        view.addSpacing(6)

        view.addWidget(StrongBodyLabel("项目名称 *", parent))
        self.nameEdit = LineEdit(parent)
        self.nameEdit.setPlaceholderText("例如：现代汉语口语语料研究")
        self.nameEdit.setClearButtonEnabled(True)
        view.addWidget(self.nameEdit)
        nameMeta = QHBoxLayout()
        self.nameHint = CaptionLabel("3–50 字", parent)
        self.nameCount = CaptionLabel("0 / 50", parent)
        nameMeta.addWidget(self.nameHint)
        nameMeta.addStretch(1)
        nameMeta.addWidget(self.nameCount)
        view.addLayout(nameMeta)

        view.addSpacing(6)
        view.addWidget(StrongBodyLabel("来源模板", parent))
        self.templatePicker = SegmentedWidget(parent)
        for key, label, icon in _TEMPLATES:
            self.templatePicker.addItem(
                key,
                label,
                icon=icon,
                onClick=lambda _checked=False, route=key: self._setTemplate(route),
            )
        self.templatePicker.setCurrentItem(self._templateKey)
        self.templatePicker.setMinimumHeight(40)
        for item in self.templatePicker.findChildren(QAbstractButton):
            normalizeButton(item, height=36)
        view.addWidget(self.templatePicker)

        view.addSpacing(6)
        view.addWidget(StrongBodyLabel("描述（可选）", parent))
        self.descEdit = PlainTextEdit(parent)
        self.descEdit.setPlaceholderText("简要描述项目目标、语料来源与分析方向")
        self.descEdit.setFixedHeight(92)
        view.addWidget(self.descEdit)

        view.addSpacing(6)
        view.addWidget(StrongBodyLabel("标签", parent))
        self.tagsEdit = LineEdit(parent)
        self.tagsEdit.setPlaceholderText("例如：语料库，口语，教学（使用逗号分隔）")
        self.tagsEdit.setClearButtonEnabled(True)
        view.addWidget(self.tagsEdit)
        tagHint = CaptionLabel("标签用于项目搜索与筛选，最多保留 8 个。", parent)
        view.addWidget(tagHint)

        self.yesButton.setText("创建项目")
        self.yesButton.setIcon(FluentIcon.ACCEPT)
        normalizeButton(self.yesButton, height=PRIMARY_HEIGHT, minimumWidth=130)
        self.cancelButton.setText("取消")
        normalizeButton(self.cancelButton, height=PRIMARY_HEIGHT, minimumWidth=96)
        self.nameEdit.textChanged.connect(self._onNameChanged)
        self._onNameChanged("")

    def _setTemplate(self, key: str) -> None:
        self._templateKey = key

    def _onNameChanged(self, text: str) -> None:
        length = len(text.strip())
        self.nameCount.setText(f"{min(length, 99)} / 50")
        valid = 3 <= length <= 50
        self.yesButton.setEnabled(valid)
        if not text:
            self.nameHint.setText("3–50 字")
        elif length < 3:
            self.nameHint.setText("项目名称至少需要 3 个字")
        elif length > 50:
            self.nameHint.setText("项目名称不能超过 50 个字")
        else:
            self.nameHint.setText("名称可用")

    def _tags(self) -> list[str]:
        raw = self.tagsEdit.text().replace("，", ",")
        tags: list[str] = []
        for item in raw.split(","):
            tag = item.strip()
            if tag and tag not in tags:
                tags.append(tag)
            if len(tags) >= 8:
                break
        return tags

    def getResult(self) -> dict:
        templateMap = {key: label for key, label, _icon in _TEMPLATES}
        return {
            "name": self.nameEdit.text().strip(),
            "description": self.descEdit.toPlainText().strip(),
            "template": templateMap.get(self._templateKey, "教学研究"),
            "tags": self._tags(),
        }


class RenameProjectDialog(_BaseProjectDialog):
    """重命名项目 MessageBox。"""

    def __init__(self, parent: Optional[QWidget] = None, currentName: str = "") -> None:
        self._currentName = currentName
        super().__init__(parent, title="重命名项目")

    def _buildUi(self) -> None:
        view = self.viewLayout
        parent = self.widget
        view.addWidget(SubtitleLabel("重命名项目", parent))
        view.addWidget(StrongBodyLabel("新名称", parent))
        self.nameEdit = LineEdit(parent)
        self.nameEdit.setText(self._currentName)
        self.nameEdit.selectAll()
        view.addWidget(self.nameEdit)
        self.yesButton.setText("保存")
        self.yesButton.setIcon(FluentIcon.SAVE)
        normalizeButton(self.yesButton, height=PRIMARY_HEIGHT, minimumWidth=110)
        self.cancelButton.setText("取消")
        normalizeButton(self.cancelButton, height=PRIMARY_HEIGHT, minimumWidth=96)
        self.nameEdit.textChanged.connect(self._onNameChanged)
        self._onNameChanged(self.nameEdit.text())

    def _onNameChanged(self, text: str) -> None:
        newName = text.strip()
        self.yesButton.setEnabled(
            3 <= len(newName) <= 50 and newName != self._currentName
        )

    def getResult(self) -> Optional[str]:
        name = self.nameEdit.text().strip()
        if not 3 <= len(name) <= 50 or name == self._currentName:
            return None
        return name


__all__ = ["NewProjectDialog", "RenameProjectDialog"]
