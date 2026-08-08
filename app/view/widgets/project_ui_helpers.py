# coding: utf-8
"""项目管理界面的按钮尺寸与图标布局约束。"""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QAbstractButton


CONTROL_HEIGHT = 34
PRIMARY_HEIGHT = 36
ICON_SIZE = 16


def normalizeButton(
    button: QAbstractButton,
    *,
    height: int = CONTROL_HEIGHT,
    minimumWidth: int = 0,
    square: bool = False,
    iconSize: int = ICON_SIZE,
) -> None:
    """防止局部布局或样式把 Fluent 按钮压缩到不可用尺寸。"""
    if square:
        button.setFixedSize(height, height)
    else:
        button.setMinimumHeight(height)
        if minimumWidth:
            button.setMinimumWidth(minimumWidth)
    if not button.icon().isNull():
        button.setIconSize(QSize(iconSize, iconSize))


__all__ = [
    "CONTROL_HEIGHT",
    "ICON_SIZE",
    "PRIMARY_HEIGHT",
    "normalizeButton",
]
