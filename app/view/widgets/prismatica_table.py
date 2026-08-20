# coding: utf-8
"""Prismatica 项目内置表格组件。"""
from __future__ import annotations

from PySide6.QtWidgets import QAbstractItemView, QTableWidget, QWidget

from app.core.utils import qconfig
from app.view.widgets.prismatica_theme import shellPalette


class PrismaticaTableWidget(QTableWidget):
    """不依赖 QFluentWidgets Pro 的项目内置数据表格。

    保留 ``QTableWidget`` 的完整接口，并提供原圆角表格调用方使用的
    ``setBorderVisible``、``setBorderRadius`` 兼容方法。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._borderVisible = True
        self._borderRadius = 12

        self.setProperty("prismaticaTable", True)
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.horizontalHeader().setHighlightSections(False)
        self.verticalHeader().setHighlightSections(False)
        self.verticalHeader().setDefaultSectionSize(38)
        self.horizontalHeader().setMinimumSectionSize(40)

        qconfig.themeChanged.connect(self._applyTheme)
        self._applyTheme()

    def setBorderVisible(self, isVisible: bool) -> None:
        """显示或隐藏表格外边框。"""
        self._borderVisible = bool(isVisible)
        self._applyTheme()

    def setBorderRadius(self, radius: int) -> None:
        """设置表格外框圆角半径。"""
        self._borderRadius = max(0, int(radius))
        self._applyTheme()

    def _applyTheme(self, *_args) -> None:
        palette = shellPalette()
        borderColor = palette.border.name() if self._borderVisible else "transparent"
        radius = self._borderRadius
        self.setStyleSheet(
            f"""
            QTableWidget[prismaticaTable="true"] {{
                background-color: {palette.surface.name()};
                color: {palette.text.name()};
                border: 1px solid {borderColor};
                border-radius: {radius}px;
                gridline-color: transparent;
                outline: none;
                selection-background-color: {palette.accentSurface.name()};
                selection-color: {palette.text.name()};
            }}
            QTableWidget[prismaticaTable="true"]::item {{
                border: none;
                padding: 6px 8px;
            }}
            QTableWidget[prismaticaTable="true"]::item:hover {{
                background-color: {palette.surfaceAlt.name()};
            }}
            QTableWidget[prismaticaTable="true"]::item:selected {{
                background-color: {palette.accentSurface.name()};
                color: {palette.text.name()};
            }}
            QTableWidget[prismaticaTable="true"] QHeaderView::section {{
                background-color: {palette.surfaceAlt.name()};
                color: {palette.mutedText.name()};
                border: none;
                border-bottom: 1px solid {palette.border.name()};
                padding: 7px 8px;
                font-weight: 600;
            }}
            QTableWidget[prismaticaTable="true"] QTableCornerButton::section {{
                background-color: {palette.surfaceAlt.name()};
                border: none;
                border-bottom: 1px solid {palette.border.name()};
            }}
            """
        )


__all__ = ["PrismaticaTableWidget"]
