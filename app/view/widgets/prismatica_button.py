# coding: utf-8
"""Prismatica 项目内置按钮组件。"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QWidget,
)

from app.core.utils import qconfig
from app.view.widgets.prismatica_theme import ACCENT, shellPalette


class PrismaticaProgressButton(QPushButton):
    """带原生绘制加载指示器的主操作按钮。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._loading = False
        self._spinnerAngle = 0
        self._spinnerTimer = QTimer(self)
        self._spinnerTimer.setInterval(50)
        self._spinnerTimer.timeout.connect(self._advanceSpinner)

        self.setProperty("prismaticaPrimary", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        qconfig.themeChanged.connect(self._applyTheme)
        self._applyTheme()

    @property
    def loading(self) -> bool:
        """返回当前是否处于加载状态。"""
        return self._loading

    def load(self) -> None:
        """进入不确定时长的加载状态。"""
        if self._loading:
            return
        self._loading = True
        self.setProperty("loading", True)
        self.setAccessibleDescription("正在处理")
        self._spinnerTimer.start()
        self.update()

    def normal(self) -> None:
        """恢复普通按钮状态。"""
        self._loading = False
        self.setProperty("loading", False)
        self.setAccessibleDescription("")
        self._spinnerTimer.stop()
        self._spinnerAngle = 0
        self.update()

    def isLoading(self) -> bool:
        """兼容原加载按钮的状态查询接口。"""
        return self._loading

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self.setAccessibleName(text)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._loading:
            super().paintEvent(event)
            return

        option = QStyleOptionButton()
        self.initStyleOption(option)
        text = option.text
        option.text = ""

        stylePainter = QStylePainter(self)
        stylePainter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        stylePainter.end()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        metrics = painter.fontMetrics()
        spinnerSize = 16
        spacing = 8
        textWidth = metrics.horizontalAdvance(text)
        contentWidth = spinnerSize + spacing + textWidth
        startX = max(12, (self.width() - contentWidth) // 2)
        centerY = self.height() / 2

        spinnerRect = QRectF(
            startX,
            centerY - spinnerSize / 2,
            spinnerSize,
            spinnerSize,
        )
        spinnerColor = QColor("#FFFFFF") if self.isEnabled() else shellPalette().mutedText
        pen = QPen(spinnerColor, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(spinnerRect, self._spinnerAngle * 16, 270 * 16)

        painter.setPen(spinnerColor)
        textRect = QRectF(
            startX + spinnerSize + spacing,
            0,
            textWidth + 2,
            self.height(),
        )
        painter.drawText(
            textRect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )
        painter.end()

    def _advanceSpinner(self) -> None:
        self._spinnerAngle = (self._spinnerAngle - 24) % 360
        self.update()

    def _applyTheme(self, *_args) -> None:
        palette = shellPalette()
        hoverColor = ACCENT.lighter(108).name()
        pressedColor = ACCENT.darker(112).name()
        self.setStyleSheet(
            f"""
            QPushButton[prismaticaPrimary="true"] {{
                background-color: {ACCENT.name()};
                color: #FFFFFF;
                border: 1px solid {ACCENT.name()};
                border-radius: 8px;
                padding: 6px 16px;
                font-size: 14px;
                font-weight: 600;
            }}
            QPushButton[prismaticaPrimary="true"]:hover {{
                background-color: {hoverColor};
                border-color: {hoverColor};
            }}
            QPushButton[prismaticaPrimary="true"]:pressed {{
                background-color: {pressedColor};
                border-color: {pressedColor};
            }}
            QPushButton[prismaticaPrimary="true"]:focus {{
                border: 2px solid {palette.accentText.name()};
            }}
            QPushButton[prismaticaPrimary="true"]:disabled {{
                background-color: {palette.surfaceAlt.name()};
                color: {palette.mutedText.name()};
                border-color: {palette.border.name()};
            }}
            """
        )


__all__ = ["PrismaticaProgressButton"]
