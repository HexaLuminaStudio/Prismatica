# coding: utf-8
"""Prismatica 桌面端应用外壳视觉令牌。"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor
from qfluentwidgets import isDarkTheme


ACCENT = QColor("#00B09C")
LIGHT_PAGE_BACKGROUND = QColor("#F6F8FA")
DARK_PAGE_BACKGROUND = QColor("#202428")


@dataclass(frozen=True)
class ShellPalette:
    window: QColor
    titleBar: QColor
    navigation: QColor
    content: QColor
    border: QColor
    text: QColor
    mutedText: QColor


def shellPalette(dark: bool | None = None) -> ShellPalette:
    dark = isDarkTheme() if dark is None else bool(dark)
    if dark:
        return ShellPalette(
            window=QColor("#181B1E"),
            titleBar=QColor("#1B1E21"),
            navigation=QColor(27, 30, 33, 238),
            content=pageBackgroundColor(True),
            border=QColor("#343B40"),
            text=QColor("#F3F6F7"),
            mutedText=QColor("#AEB9BF"),
        )

    return ShellPalette(
        window=QColor("#EEF3F6"),
        titleBar=QColor("#F6F8FA"),
        navigation=QColor(247, 249, 250, 238),
        content=pageBackgroundColor(False),
        border=QColor("#D6DEE3"),
        text=QColor("#20262C"),
        mutedText=QColor("#596873"),
    )


def pageBackgroundColor(dark: bool | None = None) -> QColor:
    """返回所有业务页面共用的主题背景色。"""
    dark = isDarkTheme() if dark is None else bool(dark)
    return QColor(DARK_PAGE_BACKGROUND if dark else LIGHT_PAGE_BACKGROUND)


__all__ = [
    "ACCENT",
    "DARK_PAGE_BACKGROUND",
    "LIGHT_PAGE_BACKGROUND",
    "ShellPalette",
    "pageBackgroundColor",
    "shellPalette",
]
