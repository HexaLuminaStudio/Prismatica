"""词频分析模块的 UI 工具函数集合

包含:
    - _showInfoBar:  统一 InfoBar 调用
    - _makeDialogHeader: 构造弹窗标题栏
    - _makeScrollArea: 将 widget 包裹进透明无边框 ScrollArea
    - _setupDialogClose: 在弹窗底部加关闭按钮并隐藏默认 buttonGroup
    - _makeAlignedItem: 创建右对齐 + 垂直居中的表格项
    - _makeSwitchButton: 创建 SwitchButton（on/off 文本一致）
"""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QTableWidgetItem,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    MessageBoxBase,
    PushButton,
    ScrollArea,
    SubtitleLabel,
    SwitchButton,
    TransparentToggleToolButton,
)

if TYPE_CHECKING:
    pass


def _showInfoBar(
    kind: str,
    title: str,
    content: str,
    parent: QWidget,
    duration: int = 2500,
) -> None:
    """统一 InfoBar 调用，避免重复传递固定参数。

    Args:
        kind: "success" | "error" | "warning" | "info"
        title: 通知标题
        content: 通知正文
        parent: 父组件
        duration: 显示时长（毫秒），默认 2500
    """
    getattr(InfoBar, kind)(
        title,
        content,
        Qt.Orientation.Horizontal,
        True,
        duration,
        InfoBarPosition.TOP_RIGHT,
        parent,
    )


def _makeDialogHeader(
    dialog: "MessageBoxBase",
    iconPath: str,
    title: str,
    onClose,
) -> QHBoxLayout:
    """构造弹窗标题栏（图标 + 标题 + 弹性 + 关闭按钮），并追加到 viewLayout。"""
    iconLabel = QSvgWidget(iconPath, dialog)
    iconLabel.setFixedSize(20, 20)
    titleLabel = SubtitleLabel(title, dialog)
    titleLabel.setAlignment(Qt.AlignmentFlag.AlignLeft)
    closeBtn = TransparentToggleToolButton(FluentIcon.CLOSE, dialog)
    closeBtn.clicked.connect(onClose)

    headerLayout = QHBoxLayout()
    headerLayout.addWidget(iconLabel, 0, Qt.AlignmentFlag.AlignLeft)
    headerLayout.addWidget(titleLabel, 0, Qt.AlignmentFlag.AlignLeft)
    headerLayout.addStretch()
    headerLayout.addWidget(closeBtn, 0, Qt.AlignmentFlag.AlignRight)
    dialog.viewLayout.addLayout(headerLayout)
    return headerLayout


def _makeScrollArea(dialog: "MessageBoxBase", widget: QWidget) -> ScrollArea:
    """将 widget 包裹进透明无边框 ScrollArea 并返回。"""
    scrollArea = ScrollArea(dialog)
    scrollArea.setWidget(widget)
    scrollArea.setWidgetResizable(True)
    scrollArea.setStyleSheet("border: none; background: transparent;")
    return scrollArea


def _setupDialogClose(dialog: "MessageBoxBase", width: int = 640) -> None:
    """在弹窗底部加关闭按钮并隐藏默认 buttonGroup，设置固定宽度。"""
    closeBtn = PushButton("关闭", dialog)
    closeBtn.clicked.connect(dialog.accept)
    dialog.buttonLayout.addWidget(closeBtn)
    dialog.buttonGroup.hide()
    dialog.widget.setFixedWidth(width)


def _makeAlignedItem(text: str) -> QTableWidgetItem:
    """创建右对齐 + 垂直居中的表格项。"""
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


def _makeSwitchButton(text: str, parent: QWidget) -> "SwitchButton":
    """创建 SwitchButton，并固定 on/off 文本一致，避免勾选后变 "On"。

    qfluentwidgets 的 SwitchButton 默认 on/off 显示为 "On"/"Off"，
    通过同时调用 setOnText/setOffText 为同一文本，可保持 UI 文字稳定。
    """
    btn = SwitchButton(text, parent)
    btn.setOnText(text)
    btn.setOffText(text)
    return btn