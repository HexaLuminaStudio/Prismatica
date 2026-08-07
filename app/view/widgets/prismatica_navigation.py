# coding: utf-8
"""Prismatica 主窗口侧边导航。

保持 qfluentwidgets ``NavigationBar`` 的路由协议，只替换展开态的视觉、
分组与折叠表现，避免业务页面感知导航实现变化。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon,
    FluentIconBase,
    NavigationBar,
    NavigationItemPosition,
    NavigationPushButton,
    drawIcon,
    isDarkTheme,
    qconfig,
    setFont,
)

from app.core.utils.setting import VERSION
from app.view.widgets.prismatica_theme import ACCENT, shellPalette


SIDEBAR_WIDTH = 250
SIDEBAR_COMPACT_WIDTH = 64
NAV_ITEM_WIDTH = 226
NAV_ITEM_COMPACT_WIDTH = 48
EXPANSION_DURATION_MS = 240
STATE_DURATION_MS = 140


def _lerp(start: float, end: float, progress: float) -> float:
    progress = max(0.0, min(1.0, float(progress)))
    return start + (end - start) * progress


def _mixColor(start: QColor, end: QColor, progress: float) -> QColor:
    progress = max(0.0, min(1.0, float(progress)))
    return QColor(
        round(_lerp(start.red(), end.red(), progress)),
        round(_lerp(start.green(), end.green(), progress)),
        round(_lerp(start.blue(), end.blue(), progress)),
        round(_lerp(start.alpha(), end.alpha(), progress)),
    )


def _versionText() -> str:
    version = str(VERSION or "1.0")
    if version.lower().startswith("v"):
        version = version[1:]
    return f"v{version}"


class BrandLogo(QWidget):
    """28px 品牌图形，不依赖 QSS 背景绘制。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ACCENT)
        painter.drawRoundedRect(self.rect(), 8, 8)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                QColor("white"),
                1.7,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(14, 6),
                    QPointF(22, 10),
                    QPointF(14, 14),
                    QPointF(6, 10),
                ]
            )
        )
        painter.drawPolyline(
            QPolygonF([QPointF(6, 14), QPointF(14, 18), QPointF(22, 14)])
        )
        painter.drawPolyline(
            QPolygonF([QPointF(6, 18), QPointF(14, 22), QPointF(22, 18)])
        )


class SidebarCollapseButton(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = True
        self._expansionProgress = 1.0
        self._hovered = False
        self._hoverProgress = 0.0
        self._pressed = False
        self._hoverAnimation = QVariantAnimation(self)
        self._hoverAnimation.setDuration(STATE_DURATION_MS)
        self._hoverAnimation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hoverAnimation.valueChanged.connect(self._setHoverProgress)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def setExpanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.setExpansionProgress(1.0 if expanded else 0.0)

    def setExpansionProgress(self, progress: float) -> None:
        self._expansionProgress = max(0.0, min(1.0, float(progress)))
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._animateHover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._pressed = False
        self._animateHover(0.0)
        super().leaveEvent(event)

    def _setHoverProgress(self, value) -> None:
        self._hoverProgress = float(value)
        self.update()

    def _animateHover(self, target: float) -> None:
        self._hoverAnimation.stop()
        if not self.isVisible() or not QApplication.isEffectEnabled(
            Qt.UIEffect.UI_General
        ):
            self._setHoverProgress(target)
            return
        self._hoverAnimation.setStartValue(self._hoverProgress)
        self._hoverAnimation.setEndValue(float(target))
        self._hoverAnimation.start()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        activate = (
            self._pressed
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        )
        self._pressed = False
        self.update()
        if activate:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        focusProgress = 1.0 if self.hasFocus() else 0.0
        surfaceProgress = max(self._hoverProgress, focusProgress)
        if surfaceProgress > 0.01:
            painter.setPen(Qt.PenStyle.NoPen)
            alpha = round(
                (42 if isDarkTheme() else 16) * surfaceProgress
            )
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(self.rect(), 8, 8)
        color = QColor("#B3B3B3" if isDarkTheme() else "#616161")
        painter.setPen(
            QPen(
                color,
                1.8,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        direction = _lerp(1, -1, self._expansionProgress)
        for offset in (-3, 3):
            centerX = 16 + offset
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(centerX - direction * 3, 11),
                        QPointF(centerX + direction * 1, 16),
                        QPointF(centerX - direction * 3, 21),
                    ]
                )
            )


class SidebarBrandHeader(QWidget):
    collapseRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = True
        self.setFixedSize(234, 56)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(8, 0, 4, 0)
        self.layout.setSpacing(10)

        self.logo = BrandLogo(self)
        self._logoOpacity = QGraphicsOpacityEffect(self.logo)
        self.logo.setGraphicsEffect(self._logoOpacity)
        self.layout.addWidget(self.logo)

        self.textWidget = QWidget(self)
        self._textOpacity = QGraphicsOpacityEffect(self.textWidget)
        self.textWidget.setGraphicsEffect(self._textOpacity)
        textLayout = QVBoxLayout(self.textWidget)
        textLayout.setContentsMargins(0, 0, 0, 0)
        textLayout.setSpacing(1)
        self.titleLabel = QLabel("棱溯客户端", self.textWidget)
        self.subtitleLabel = QLabel(f"Prismatica · {_versionText()}", self.textWidget)
        titleFont = QFont("Segoe UI")
        titleFont.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
        titleFont.setPixelSize(14)
        titleFont.setWeight(QFont.Weight.DemiBold)
        self.titleLabel.setFont(titleFont)
        subtitleFont = QFont(titleFont)
        subtitleFont.setPixelSize(11)
        subtitleFont.setWeight(QFont.Weight.Normal)
        self.subtitleLabel.setFont(subtitleFont)
        textLayout.addWidget(self.titleLabel)
        textLayout.addWidget(self.subtitleLabel)
        self.layout.addWidget(self.textWidget, 1)

        self.collapseButton = SidebarCollapseButton(self)
        self.collapseButton.setFixedSize(32, 32)
        self.collapseButton.setToolTip("折叠侧边栏")
        self.collapseButton.setAccessibleName("折叠侧边栏")
        self.collapseButton.clicked.connect(self.collapseRequested)
        self.layout.addWidget(self.collapseButton)
        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)

    def _applyTheme(self) -> None:
        palette = shellPalette()
        foreground = palette.text.name()
        muted = palette.mutedText.name()
        self.titleLabel.setStyleSheet(
            f"QLabel {{ color: {foreground}; background: transparent; }}"
        )
        self.subtitleLabel.setStyleSheet(
            f"QLabel {{ color: {muted}; background: transparent; }}"
        )

    def setExpanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.setExpansionProgress(1.0 if expanded else 0.0)
        self.collapseButton.setExpanded(expanded)
        label = "折叠侧边栏" if expanded else "展开侧边栏"
        self.collapseButton.setToolTip(label)
        self.collapseButton.setAccessibleName(label)

    def setExpansionProgress(self, progress: float) -> None:
        progress = max(0.0, min(1.0, float(progress)))
        width = round(_lerp(NAV_ITEM_COMPACT_WIDTH, 234, progress))
        contentProgress = max(0.0, min(1.0, (progress - 0.28) / 0.72))
        self.setFixedWidth(width)
        self.logo.setVisible(progress > 0.28)
        self.textWidget.setVisible(progress > 0.28)
        self._logoOpacity.setOpacity(contentProgress)
        self._textOpacity.setOpacity(contentProgress)
        self.collapseButton.setExpansionProgress(progress)
        self.layout.setContentsMargins(
            8,
            0,
            round(_lerp(8, 4, progress)),
            0,
        )
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(shellPalette().border, 1))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


class SidebarSectionHeader(QWidget):
    """带顶部分隔线的导航分组标题。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.text = text
        self._expanded = True
        self._expansionProgress = 1.0
        self.setFixedSize(NAV_ITEM_WIDTH, 40)

    def setExpanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.setExpansionProgress(1.0 if expanded else 0.0)

    def setExpansionProgress(self, progress: float) -> None:
        self._expansionProgress = max(0.0, min(1.0, float(progress)))
        self.setFixedSize(
            round(
                _lerp(
                    NAV_ITEM_COMPACT_WIDTH,
                    NAV_ITEM_WIDTH,
                    self._expansionProgress,
                )
            ),
            round(_lerp(14, 40, self._expansionProgress)),
        )
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(QPen(shellPalette().border, 1))
        painter.drawLine(0, 0, self.width(), 0)
        if self._expansionProgress <= 0.02:
            return
        textColor = shellPalette().mutedText
        textColor.setAlpha(round(255 * self._expansionProgress))
        painter.setPen(textColor)
        font = self.font()
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.4)
        painter.setFont(font)
        painter.drawText(
            QRectF(16, 8, self.width() - 16, 26),
            Qt.AlignmentFlag.AlignVCenter,
            self.text,
        )


class PrismaticaNavigationButton(NavigationPushButton):
    """44px 展开态导航项，兼容 NavigationBar 的选择协议。"""

    def __init__(
        self,
        icon,
        text: str,
        isSelectable: bool = True,
        selectedIcon=None,
        parent=None,
    ):
        super().__init__(icon, text, isSelectable, parent)
        self._selectedIcon = selectedIcon
        self._expanded = True
        self._expansionProgress = 1.0
        self._hoverProgress = 0.0
        self._selectionProgress = 0.0
        self._badgeCount = 0
        self._hoverAnimation = QVariantAnimation(self)
        self._hoverAnimation.setDuration(STATE_DURATION_MS)
        self._hoverAnimation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hoverAnimation.valueChanged.connect(self._setHoverProgress)
        self._selectionAnimation = QVariantAnimation(self)
        self._selectionAnimation.setDuration(180)
        self._selectionAnimation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._selectionAnimation.valueChanged.connect(self._setSelectionProgress)
        self.selectedChanged.connect(self._onSelectedChanged)
        self.setFixedSize(NAV_ITEM_WIDTH, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(text)
        self.setAccessibleName(text)
        setFont(self, 14)

    def setExpanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.isCompacted = not expanded
        self.setExpansionProgress(1.0 if expanded else 0.0)

    def setExpansionProgress(self, progress: float) -> None:
        self._expansionProgress = max(0.0, min(1.0, float(progress)))
        self.setFixedSize(
            round(
                _lerp(
                    NAV_ITEM_COMPACT_WIDTH,
                    NAV_ITEM_WIDTH,
                    self._expansionProgress,
                )
            ),
            44,
        )
        self.update()

    def _animationsEnabled(self) -> bool:
        return self.isVisible() and QApplication.isEffectEnabled(
            Qt.UIEffect.UI_General
        )

    def _animateState(
        self,
        animation: QVariantAnimation,
        start: float,
        end: float,
    ) -> None:
        animation.stop()
        if not self._animationsEnabled():
            if animation is self._hoverAnimation:
                self._setHoverProgress(end)
            else:
                self._setSelectionProgress(end)
            return
        animation.setStartValue(float(start))
        animation.setEndValue(float(end))
        animation.start()

    def _setHoverProgress(self, value) -> None:
        self._hoverProgress = float(value)
        self.update()

    def _setSelectionProgress(self, value) -> None:
        self._selectionProgress = float(value)
        self.update()

    def _onSelectedChanged(self, selected: bool) -> None:
        self._animateState(
            self._selectionAnimation,
            self._selectionProgress,
            1.0 if selected else 0.0,
        )

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._animateState(self._hoverAnimation, self._hoverProgress, 1.0)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._animateState(self._hoverAnimation, self._hoverProgress, 0.0)

    def setBadgeCount(self, count: int) -> None:
        self._badgeCount = max(0, int(count))
        self.setAccessibleDescription(
            f"{self._badgeCount} 个进行中任务" if self._badgeCount else ""
        )
        self.update()

    def badgeCount(self) -> int:
        return self._badgeCount

    def setSelectedColor(self, light, dark) -> None:
        self.setIndicatorColor(light, dark)

    def indicatorRect(self):
        return QRectF(0, 8, 2, 28)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def _renderIcon(self, painter: QPainter, rect: QRectF, color: QColor) -> None:
        icon = self._selectedIcon if self.isSelected and self._selectedIcon else self._icon
        if isinstance(icon, FluentIconBase):
            icon.render(painter, rect, fill=color.name())
        else:
            painter.setOpacity(1.0 if self.isSelected else 0.72)
            drawIcon(icon, painter, rect)
            painter.setOpacity(1.0)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        painter.setPen(Qt.PenStyle.NoPen)

        dark = isDarkTheme()
        selectedProgress = max(
            self._selectionProgress,
            1.0 if self.isAboutSelected else 0.0,
        )
        hoverProgress = max(
            self._hoverProgress,
            0.45 if getattr(self, "isPressed", False) else 0.0,
        )
        surfaceAlpha = round(
            _lerp(0, 42 if dark else 26, selectedProgress)
            + _lerp(0, 24 if dark else 14, hoverProgress)
            * (1.0 - selectedProgress)
        )
        if surfaceAlpha:
            painter.setBrush(QColor(0, 176, 156, surfaceAlpha))
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)

        if selectedProgress > 0.01:
            indicator = QColor(ACCENT)
            indicator.setAlpha(round(255 * selectedProgress))
            painter.setBrush(indicator)
            painter.drawRoundedRect(self.indicatorRect(), 1, 1)

        iconBox = QRectF(8, 6, 32, 32)
        if selectedProgress > 0.01:
            iconSurface = QColor(0, 176, 156)
            iconSurface.setAlpha(
                round((52 if dark else 30) * selectedProgress)
            )
            painter.setBrush(iconSurface)
            painter.drawRoundedRect(iconBox, 7, 7)

        mutedIcon = QColor("#B3B3B3" if dark else "#5D6872")
        activeProgress = max(selectedProgress, hoverProgress * 0.7)
        iconColor = _mixColor(mutedIcon, ACCENT, activeProgress)
        self._renderIcon(
            painter,
            QRectF(iconBox.x() + 7, 13, 18, 18),
            iconColor,
        )

        if self._expansionProgress > 0.02:
            mutedText = QColor("#F5F5F5" if dark else "#20262C")
            textColor = _mixColor(mutedText, ACCENT, selectedProgress)
            textColor.setAlpha(round(255 * self._expansionProgress))
            painter.setPen(textColor)
            font = self.font()
            font.setWeight(
                QFont.Weight.DemiBold
                if selectedProgress > 0.5
                else QFont.Weight.Medium
            )
            painter.setFont(font)
            rightReserve = 38 if self._badgeCount else 12
            painter.drawText(
                QRectF(52, 0, self.width() - 52 - rightReserve, self.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.text(),
            )

        if self._badgeCount:
            badgeText = "99+" if self._badgeCount > 99 else str(self._badgeCount)
            badgeWidth = 24 if len(badgeText) > 1 else 18
            badgeRect = QRectF(
                self.width()
                - badgeWidth
                - _lerp(1, 12, self._expansionProgress),
                _lerp(4, 13, self._expansionProgress),
                badgeWidth,
                18,
            )
            painter.setBrush(QColor("#D13438"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(badgeRect, 9, 9)
            painter.setPen(QColor("white"))
            badgeFont = self.font()
            badgeFont.setPixelSize(11)
            badgeFont.setWeight(QFont.Weight.DemiBold)
            painter.setFont(badgeFont)
            painter.drawText(badgeRect, Qt.AlignmentFlag.AlignCenter, badgeText)


class PrismaticaNavigationBar(NavigationBar):
    """250px 展开态侧边栏，保留 NavigationBar 的路由/历史行为。"""

    expandedChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = True
        self._expansionProgress = 1.0
        self._animationsEnabled = True
        self.sectionHeaders: list[SidebarSectionHeader] = []
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setIndicatorAnimationEnabled(False)
        self._expansionAnimation = QPropertyAnimation(
            self,
            b"expansionProgress",
            self,
        )
        self._expansionAnimation.setDuration(EXPANSION_DURATION_MS)
        self._expansionAnimation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._expansionAnimation.finished.connect(self._finishExpansion)

        self.vBoxLayout.setContentsMargins(8, 8, 8, 8)
        self.vBoxLayout.setSpacing(4)
        self.topLayout.setContentsMargins(4, 0, 4, 0)
        self.scrollLayout.setContentsMargins(4, 0, 4, 0)
        self.bottomLayout.setContentsMargins(4, 0, 4, 0)
        self.topLayout.setSpacing(6)
        self.scrollLayout.setSpacing(6)
        self.bottomLayout.setSpacing(4)
        self.scrollLayout.setContentsMargins(4, 8, 4, 0)
        self.scrollArea.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        self.scrollWidget.setStyleSheet("background: transparent; border: none;")

        self.brandHeader = SidebarBrandHeader(self)
        self.brandHeader.collapseRequested.connect(self.toggleExpanded)
        self.topLayout.insertWidget(0, self.brandHeader, 0, Qt.AlignmentFlag.AlignHCenter)
        self.topLayout.insertSpacing(1, 4)
        qconfig.themeChangedFinished.connect(self.update)

    def insertItem(
        self,
        index: int,
        routeKey: str,
        icon,
        text: str,
        onClick=None,
        selectable=True,
        selectedIcon=None,
        position=NavigationItemPosition.TOP,
    ):
        if routeKey in self.items:
            return self.items[routeKey]
        button = PrismaticaNavigationButton(
            icon, text, selectable, selectedIcon, self
        )
        button.setSelectedColor(ACCENT, ACCENT)
        button.setExpanded(self._expanded)
        self.insertWidget(index, routeKey, button, onClick, position)
        return button

    def addSectionHeader(
        self, text: str, position: NavigationItemPosition
    ) -> SidebarSectionHeader:
        parent = self.scrollWidget if position == NavigationItemPosition.SCROLL else self
        header = SidebarSectionHeader(text, parent)
        header.setExpanded(self._expanded)
        self.sectionHeaders.append(header)
        if position == NavigationItemPosition.SCROLL:
            self.scrollLayout.addWidget(header, 0, Qt.AlignmentFlag.AlignHCenter)
        else:
            self.bottomLayout.addWidget(header, 0, Qt.AlignmentFlag.AlignHCenter)
        return header

    def addWidget(
        self,
        routeKey,
        widget,
        onClick=None,
        position=NavigationItemPosition.TOP,
    ):
        if hasattr(widget, "setExpanded"):
            widget.setExpanded(self._expanded)
        return super().addWidget(routeKey, widget, onClick, position)

    def clearCurrentItem(self) -> None:
        self._stopIndicatorAnimation()
        self._currentRouteKey = None
        for item in self.items.values():
            if item.isSelectable:
                item.setSelected(False)

    def isExpanded(self) -> bool:
        return self._expanded

    def setAnimationsEnabled(self, enabled: bool) -> None:
        self._animationsEnabled = bool(enabled)

    def toggleExpanded(self) -> None:
        self.setExpanded(not self._expanded)

    def setExpanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        label = "折叠侧边栏" if expanded else "展开侧边栏"
        self.brandHeader.collapseButton.setToolTip(label)
        self.brandHeader.collapseButton.setAccessibleName(label)
        for item in self.items.values():
            if isinstance(item, PrismaticaNavigationButton):
                item._expanded = expanded
                item.isCompacted = not expanded
        self.expandedChanged.emit(expanded)

        target = 1.0 if expanded else 0.0
        self._expansionAnimation.stop()
        shouldAnimate = (
            self._animationsEnabled
            and self.isVisible()
            and QApplication.isEffectEnabled(Qt.UIEffect.UI_General)
        )
        if not shouldAnimate:
            self._setExpansionProgress(target)
            self._finishExpansion()
            return

        self._expansionAnimation.setStartValue(self._expansionProgress)
        self._expansionAnimation.setEndValue(target)
        self._expansionAnimation.start()

    def _getExpansionProgress(self) -> float:
        return self._expansionProgress

    def _setExpansionProgress(self, progress: float) -> None:
        self._expansionProgress = max(0.0, min(1.0, float(progress)))
        width = round(
            _lerp(
                SIDEBAR_COMPACT_WIDTH,
                SIDEBAR_WIDTH,
                self._expansionProgress,
            )
        )
        self.setMinimumWidth(width)
        self.setMaximumWidth(width)
        self.brandHeader.setExpansionProgress(self._expansionProgress)
        for item in self.items.values():
            if hasattr(item, "setExpansionProgress"):
                item.setExpansionProgress(self._expansionProgress)
        for header in self.sectionHeaders:
            header.setExpansionProgress(self._expansionProgress)
        self.updateGeometry()
        self.update()

    expansionProgress = Property(
        float,
        _getExpansionProgress,
        _setExpansionProgress,
    )

    def _finishExpansion(self) -> None:
        target = 1.0 if self._expanded else 0.0
        if abs(self._expansionProgress - target) > 0.001:
            self._setExpansionProgress(target)
        self.brandHeader.setExpanded(self._expanded)
        for item in self.items.values():
            if hasattr(item, "setExpanded"):
                item.setExpanded(self._expanded)
        for header in self.sectionHeaders:
            header.setExpanded(self._expanded)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        dark = isDarkTheme()
        palette = shellPalette(dark)
        painter.fillRect(self.rect(), palette.navigation)

        atmosphere = QLinearGradient(0, 0, 0, 260)
        atmosphere.setColorAt(
            0.0,
            QColor(0, 176, 156, 18 if dark else 14),
        )
        atmosphere.setColorAt(1.0, QColor(0, 176, 156, 0))
        painter.fillRect(QRectF(0, 0, self.width(), 260), atmosphere)

        painter.setPen(QPen(palette.border, 1))
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())


__all__ = [
    "PrismaticaNavigationBar",
    "PrismaticaNavigationButton",
    "SidebarSectionHeader",
]
