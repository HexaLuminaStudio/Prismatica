# coding: utf-8
"""P0-A 桌面端 账户导航小部件(主窗口 BOTTOM 区域)。

根据登录状态展示:
    - 未登录:头像 + 「登录」字样
    - 已登录:头像 + 邮箱 / tier + 余额
    - 余额不足:头像右上角红点

点击后由主窗口决定弹 LoginDialog 或 AccountPanel。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEasingCurve, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import IconWidget, isDarkTheme, qconfig
from qfluentwidgets import FluentIcon as FI

from app.core.services import getCloudAuth, getCloudApi


def _lerp(start: float, end: float, progress: float) -> float:
    progress = max(0.0, min(1.0, float(progress)))
    return start + (end - start) * progress


class _AccountAvatar(QWidget):
    """设计稿中的青绿色圆形账户头像。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.icon = IconWidget(FI.PEOPLE.icon(color=QColor("#00B09C")), self)
        self.icon.setFixedSize(16, 16)
        layout.addWidget(self.icon)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 176, 156, 54 if isDarkTheme() else 34))
        painter.drawEllipse(self.rect())


class AccountNavWidget(QWidget):
    """M11/M13:导航条上的账户入口。"""

    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # NavigationBar._onWidgetClicked() requires every registered custom
        # widget to expose this flag.  The account entry opens a panel instead
        # of switching the current route, so it must remain non-selectable.
        self.isSelectable = False
        self.setFixedHeight(56)
        self.setCursor(Qt.PointingHandCursor)
        self._loggedIn = False
        self._balance = 0
        self._email = ""
        self._tier = "free"
        self._expanded = True
        self._expansionProgress = 1.0
        self._hovered = False
        self._hoverProgress = 0.0
        self._lowBalance = False
        self._hoverAnimation = QVariantAnimation(self)
        self._hoverAnimation.setDuration(140)
        self._hoverAnimation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hoverAnimation.valueChanged.connect(self._setHoverProgress)
        self._buildUi()
        # 启动时尝试恢复
        try:
            if getCloudAuth()._api.isLoggedIn():
                self.setLoggedIn(True)
        except Exception:
            pass

    def _buildUi(self) -> None:
        self.setFixedSize(226, 60)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        self._avatar = _AccountAvatar(self)
        layout.addWidget(self._avatar)

        self._textWidget = QWidget(self)
        self._textOpacity = QGraphicsOpacityEffect(self._textWidget)
        self._textWidget.setGraphicsEffect(self._textOpacity)
        text = QVBoxLayout(self._textWidget)
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self._emailLabel = QLabel("未登录", self._textWidget)
        emailFont = QFont("Segoe UI")
        emailFont.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
        emailFont.setPixelSize(13)
        emailFont.setWeight(QFont.Weight.DemiBold)
        self._emailLabel.setFont(emailFont)
        self._emailLabel.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        text.addWidget(self._emailLabel)

        self._subRow = QWidget(self._textWidget)
        subLayout = QHBoxLayout(self._subRow)
        subLayout.setContentsMargins(0, 0, 0, 0)
        subLayout.setSpacing(6)
        self._tierLabel = QLabel("FREE", self._subRow)
        self._tierLabel.hide()
        tierFont = QFont(emailFont)
        tierFont.setPixelSize(10)
        tierFont.setWeight(QFont.Weight.Bold)
        self._tierLabel.setFont(tierFont)
        self._balanceLabel = QLabel("点击登录 Prismatica 账号", self._subRow)
        # 保留旧组件字段名，避免账户烟雾测试和外部调用失效。
        self._subLabel = self._balanceLabel
        balanceFont = QFont(emailFont)
        balanceFont.setPixelSize(11)
        balanceFont.setWeight(QFont.Weight.Normal)
        self._balanceLabel.setFont(balanceFont)
        subLayout.addWidget(self._tierLabel)
        subLayout.addWidget(self._balanceLabel, 1)
        text.addWidget(self._subRow)
        layout.addWidget(self._textWidget, 1)

        # 红点(默认隐藏)
        self._badge = QWidget(self)
        self._badge.setFixedSize(8, 8)
        self._badge.setStyleSheet(
            "QWidget { background: #D13438; border-radius: 4px; }"
        )
        self._badge.hide()
        layout.addWidget(self._badge)
        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)

    def _applyTheme(self) -> None:
        foreground = "#F5F5F5" if isDarkTheme() else "#1F1F1F"
        muted = "#B3B3B3" if isDarkTheme() else "#616161"
        self._emailLabel.setStyleSheet(
            f"QLabel {{ color: {foreground}; background: transparent; }}"
        )
        self._balanceLabel.setStyleSheet(
            f"QLabel {{ color: {muted}; background: transparent; }}"
        )
        self._tierLabel.setStyleSheet(
            "QLabel { color: #00A18F; background: rgba(0, 176, 156, 28); "
            "border-radius: 7px; padding: 0 5px; }"
        )

    def setExpanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.setExpansionProgress(1.0 if expanded else 0.0)
        self.setToolTip("" if expanded else self._emailLabel.text())

    def setExpansionProgress(self, progress: float) -> None:
        self._expansionProgress = max(0.0, min(1.0, float(progress)))
        self.setFixedSize(round(_lerp(48, 226, self._expansionProgress)), 60)
        self.layout().setContentsMargins(
            round(_lerp(10, 12, self._expansionProgress)),
            6,
            round(_lerp(10, 12, self._expansionProgress)),
            6,
        )
        self._textWidget.setVisible(self._expansionProgress > 0.08)
        self._textOpacity.setOpacity(self._expansionProgress)
        self._badge.setVisible(
            self._expansionProgress > 0.82 and self._lowBalance
        )
        self.update()

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

    def setSelected(self, _selected: bool) -> None:
        """兼容 NavigationBar 的统一选择协议；账户入口不参与路由选中。"""
        return

    # ------------------------------------------------------------------
    # 状态切换
    # ------------------------------------------------------------------

    def setLoggedIn(self, loggedIn: bool) -> None:
        self._loggedIn = bool(loggedIn)
        if loggedIn:
            sess = getCloudAuth()._api.getSession()
            self._email = sess.email or "(已登录)"
            self._tier = sess.tier or "free"
            self._emailLabel.setText(self._email)
            self._tierLabel.setText(self._tier.upper())
            self._tierLabel.show()
            self._balanceLabel.setText("点击查看账户")
        else:
            self._emailLabel.setText("未登录")
            self._tierLabel.hide()
            self._balanceLabel.setText("点击登录 Prismatica 账号")
            self._balance = 0
            self._lowBalance = False
            self._badge.hide()

    def setBalance(self, balance: int) -> None:
        self._balance = int(balance)
        if not self._loggedIn:
            return
        # 余额 < 30 时显示红点
        self._lowBalance = self._balance < 30
        if self._lowBalance:
            self._badge.setVisible(self._expansionProgress > 0.82)
            self._balanceLabel.setText(f"余额 {self._balance}（不足）")
        else:
            self._badge.hide()
            self._balanceLabel.setText(f"余额 {self._balance:,}")

    # ------------------------------------------------------------------
    # 鼠标事件
    # ------------------------------------------------------------------

    def mouseReleaseEvent(self, event) -> None:  # noqa: D401
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:  # noqa: D401
        self._hovered = True
        self._animateHover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: D401
        self._hovered = False
        self._animateHover(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        if self._hoverProgress <= 0.01:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        alpha = round((26 if isDarkTheme() else 16) * self._hoverProgress)
        painter.setBrush(QColor(0, 176, 156, alpha))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)


__all__ = ["AccountNavWidget"]
