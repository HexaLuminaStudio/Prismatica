# coding: utf-8
"""Prismatica 项目内置侧边抽屉。"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QRect, QPropertyAnimation, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QVBoxLayout,
    QWidget,
)

from app.core.utils import qconfig
from app.view.widgets.prismatica_theme import shellPalette


class PrismaticaDrawer(QWidget):
    """覆盖在父控件右侧、支持展开与收起动画的抽屉。"""

    _DEFAULT_WIDTH = 440
    _ANIMATION_DURATION_MS = 220

    def __init__(self, view: QWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self._view = view
        self._expanded = False
        self._hiddenOnClickOutside = False
        self._customLightBackground = None
        self._customDarkBackground = None
        self._borderRadii = (12, 0, 0, 12)

        self.setObjectName("prismaticaDrawer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.setView(view)

        self._animation = QPropertyAnimation(self, b"geometry", self)
        self._animation.setDuration(self._ANIMATION_DURATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._onAnimationFinished)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setColor(QColor(0, 0, 0, 55))
        shadow.setOffset(-3, 1)
        self.setGraphicsEffect(shadow)

        parent.installEventFilter(self)
        qconfig.themeChanged.connect(self._applyTheme)
        self._applyTheme()
        self.setGeometry(self._hiddenGeometry())
        self.hide()

    @property
    def view(self) -> QWidget:
        return self._view

    def setView(self, view: QWidget) -> None:
        """替换抽屉内容视图。"""
        if self._layout.count():
            oldView = self._layout.takeAt(0).widget()
            if oldView is not None and oldView is not view:
                oldView.setParent(None)

        self._view = view
        view.setParent(self)
        self._layout.addWidget(view)

    def setHiddenOnClickOutside(self, isHidden: bool) -> None:
        """设置点击抽屉外部时是否自动收起。"""
        self._hiddenOnClickOutside = bool(isHidden)

    def isHiddenOnClickedOutside(self) -> bool:
        """返回点击外部自动收起设置。"""
        return self._hiddenOnClickOutside

    def setCustomBackgroundColor(self, light, dark) -> None:
        """设置明暗主题下的自定义背景色。"""
        self._customLightBackground = light
        self._customDarkBackground = dark
        self._applyTheme()

    def setBorderRadius(
        self,
        topLeft: int,
        topRight: int,
        bottomLeft: int,
        bottomRight: int,
    ) -> None:
        """保留四角参数接口；Qt 样式使用最大的左侧圆角。"""
        self._borderRadii = (
            max(0, int(topLeft)),
            max(0, int(topRight)),
            max(0, int(bottomLeft)),
            max(0, int(bottomRight)),
        )
        self._applyTheme()

    def expand(self) -> None:
        """展开抽屉。"""
        self._expanded = True
        self._animation.stop()
        if not self.isVisible():
            self.setGeometry(self._hiddenGeometry())
            self.show()
        self.raise_()
        self._animateTo(self._shownGeometry())

    def collapse(self) -> None:
        """收起抽屉。"""
        if not self.isVisible():
            self._expanded = False
            return
        self._expanded = False
        self._animation.stop()
        self._animateTo(self._hiddenGeometry())

    def toggle(self) -> None:
        """切换抽屉展开状态。"""
        if self._expanded:
            self.collapse()
        else:
            self.expand()

    def isExpanded(self) -> bool:
        return self._expanded

    def eventFilter(self, watched, event: QEvent) -> bool:  # noqa: N802
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._animation.stop()
            self.setGeometry(
                self._shownGeometry() if self._expanded else self._hiddenGeometry()
            )
        return super().eventFilter(watched, event)

    def _drawerWidth(self) -> int:
        parent = self.parentWidget()
        if parent is None:
            return self._DEFAULT_WIDTH
        preferredWidth = max(self._DEFAULT_WIDTH, self._view.minimumWidth())
        return max(1, min(preferredWidth, parent.width()))

    def _shownGeometry(self) -> QRect:
        parent = self.parentWidget()
        if parent is None:
            return QRect()
        width = self._drawerWidth()
        return QRect(parent.width() - width, 0, width, parent.height())

    def _hiddenGeometry(self) -> QRect:
        parent = self.parentWidget()
        if parent is None:
            return QRect()
        width = self._drawerWidth()
        return QRect(parent.width(), 0, width, parent.height())

    def _animateTo(self, geometry: QRect) -> None:
        self._animation.setStartValue(self.geometry())
        self._animation.setEndValue(geometry)
        self._animation.start()

    def _onAnimationFinished(self) -> None:
        if not self._expanded:
            self.hide()

    def _applyTheme(self, *_args) -> None:
        palette = shellPalette()
        background = palette.surface
        if palette.window.lightness() < 128 and self._customDarkBackground is not None:
            background = self._customDarkBackground
        elif palette.window.lightness() >= 128 and self._customLightBackground is not None:
            background = self._customLightBackground
        radius = max(self._borderRadii[0], self._borderRadii[2])
        backgroundColor = QColor(background)
        self.setStyleSheet(
            f"""
            QWidget#prismaticaDrawer {{
                background-color: {backgroundColor.name()};
                border-left: 1px solid {palette.border.name()};
                border-top-left-radius: {radius}px;
                border-bottom-left-radius: {radius}px;
            }}
            """
        )


__all__ = ["PrismaticaDrawer"]
