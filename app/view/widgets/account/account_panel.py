# coding: utf-8
"""P0-A 桌面端 「我的账户」抽屉。

子页签:
    1. 概览   — 用户头像 / 邮箱 / tier / 余额
    2. 订阅   — subscription_card
    3. 设备   — 设备列表 + 撤销
    4. 安全   — 修改密码 / 注销

只读账单元数据都从 CloudAccount.me() 拉;余额变化订阅 signalBus.balanceChanged。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    HyperlinkButton,
    IconWidget,
    LargeTitleLabel,
    MessageBox,
    Pivot,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

from app.core.services import (
    CloudApiError,
    getCloudAccount,
    getCloudAuth,
    getCloudBilling,
)
from app.core.utils import logger, qconfig, signalBus
from app.view.widgets.prismatica_theme import setThemeRole, shellPalette

from .subscription_card import SubscriptionCard


# ---------------------------------------------------------------------------
# 子页签:概览
# ---------------------------------------------------------------------------


class _OverviewPage(QWidget):
    """概览:头像 + 邮箱 + tier + 余额。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._buildUi()
        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)
        signalBus.balanceChanged.connect(self._onBalanceChanged)
        signalBus.sessionChanged.connect(self._onSessionChanged)

    def _buildUi(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # 头像 + 邮箱
        head = QHBoxLayout()
        head.setSpacing(14)
        self._avatar = IconWidget(FluentIcon.PEOPLE)
        self._avatar.setFixedSize(56, 56)
        head.addWidget(self._avatar)
        textCol = QVBoxLayout()
        textCol.setSpacing(2)
        self._emailLabel = SubtitleLabel("—")
        self._displayLabel = CaptionLabel("—")
        self._tierBadge = CaptionLabel("—")
        textCol.addWidget(self._emailLabel)
        textCol.addWidget(self._displayLabel)
        textCol.addWidget(self._tierBadge)
        head.addLayout(textCol)
        head.addStretch(1)
        layout.addLayout(head)

        # 余额卡片
        self._balanceCard = QFrame()
        card = QVBoxLayout(self._balanceCard)
        card.setContentsMargins(20, 18, 20, 18)
        card.setSpacing(4)
        card.addWidget(CaptionLabel("当前可用余额"))
        self._balanceLabel = LargeTitleLabel("—")
        card.addWidget(self._balanceLabel)
        self._reservedHint = CaptionLabel("预占:—  ·  订阅周期:—")
        card.addWidget(self._reservedHint)
        self._refreshBalBtn = PushButton("刷新")
        self._refreshBalBtn.clicked.connect(self.refresh)
        card.addWidget(self._refreshBalBtn, alignment=Qt.AlignRight)
        layout.addWidget(self._balanceCard)

        # 快捷操作
        quick = QHBoxLayout()
        quick.setSpacing(10)
        changePwBtn = PushButton("修改密码")
        changePwBtn.clicked.connect(self._onChangePassword)
        quick.addWidget(changePwBtn)
        deleteBtn = PushButton("注销账号")
        setThemeRole(deleteBtn, "danger")
        deleteBtn.clicked.connect(self._onDeleteAccount)
        quick.addWidget(deleteBtn)
        quick.addStretch(1)
        layout.addLayout(quick)

        # 设备上限提示
        self._devicesHint = CaptionLabel(" ")
        setThemeRole(self._devicesHint, "danger")
        layout.addWidget(self._devicesHint)

        layout.addStretch(1)

    def _applyTheme(self, *_args) -> None:
        palette = shellPalette()
        self._tierBadge.setStyleSheet(
            f"color: {palette.text.name()}; background: {palette.surfaceAlt.name()}; "
            "padding: 2px 8px; border-radius: 8px;"
        )
        self._balanceCard.setStyleSheet(
            f"QFrame {{ background: {palette.surfaceAlt.name()}; "
            f"border: 1px solid {palette.border.name()}; border-radius: 12px; }}"
        )

    def refresh(self) -> None:
        try:
            data = getCloudAccount().me()
        except CloudApiError as exc:
            self._devicesHint.setText(f"加载失败:{exc.message}")
            return
        self._updateFrom(data)

    def _updateFrom(self, data: Dict[str, Any]) -> None:
        self._emailLabel.setText(data.get("email", "—"))
        displayName = data.get("displayName") or "(未设置昵称)"
        self._displayLabel.setText(displayName)
        tier = (data.get("tier") or "free").upper()
        self._tierBadge.setText(f"  {tier}  ")
        balance = int(data.get("balance", 0) or 0)
        reserved = int(data.get("reserved", 0) or 0)
        self._balanceLabel.setText(f"{balance} 积分")
        self._reservedHint.setText(f"预占:{reserved}  ·  可用:{balance - reserved}")

        sub = data.get("subscription") or {}
        if sub:
            start = sub.get("currentPeriodStart", "")
            end = sub.get("currentPeriodEnd", "")
            self._reservedHint.setText(
                f"预占:{reserved}  ·  订阅:{sub.get('planCode')}  ({start} → {end})"
            )

    def _onBalanceChanged(self, balance: int) -> None:
        self._balanceLabel.setText(f"{balance} 积分")

    def _onSessionChanged(self, loggedIn: bool) -> None:
        if not loggedIn:
            self._emailLabel.setText("—")
            self._displayLabel.setText("—")
            self._tierBadge.setText("—")
            self._balanceLabel.setText("—")

    def _onChangePassword(self) -> None:
        old, ok1 = QInputDialog.getText(
            self, "修改密码", "当前密码:", QLineEdit.Password
        )
        if not ok1 or not old:
            return
        new, ok2 = QInputDialog.getText(
            self, "修改密码", "新密码(至少 10 位,含字母+数字):", QLineEdit.Password
        )
        if not ok2 or not new:
            return
        try:
            getCloudAuth().changePassword(old, new)
        except CloudApiError as exc:
            MessageBox("修改失败", exc.message, self.window()).exec()
            return
        MessageBox("完成", "密码已修改,其他设备需重新登录。", self.window()).exec()

    def _onDeleteAccount(self) -> None:
        confirm = QMessageBox.warning(
            self,
            "注销账号",
            "此操作不可撤销(30 天内可申请恢复)。\n请输入密码确认:",
            QMessageBox.Yes | QMessageBox.Cancel,
        )
        if confirm != QMessageBox.Yes:
            return
        password, ok = QInputDialog.getText(
            self, "注销账号", "当前密码:", QLineEdit.Password
        )
        if not ok or not password:
            return
        try:
            result = getCloudAccount().deleteAccount(password)
        except CloudApiError as exc:
            MessageBox("注销失败", exc.message, self.window()).exec()
            return
        scheduled = result.get("scheduledHardDeleteAt", "30 天后")
        MessageBox(
            "已提交注销",
            f"账号已置为 deleted 状态,{scheduled} 将被硬删。\n所有 refresh_token 已撤销。",
            self.window(),
        ).exec()


# ---------------------------------------------------------------------------
# 子页签:订阅
# ---------------------------------------------------------------------------


class _SubscriptionsPage(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._buildUi()

    def _buildUi(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(TitleLabel("订阅"))
        self._cardHost = QVBoxLayout()
        self._cardHost.setSpacing(10)
        layout.addLayout(self._cardHost)
        self._emptyLabel = CaptionLabel("暂无订阅")
        layout.addWidget(self._emptyLabel)
        self._refreshBtn = PushButton("刷新")
        self._refreshBtn.clicked.connect(self.refresh)
        layout.addWidget(self._refreshBtn, alignment=Qt.AlignRight)
        layout.addStretch(1)

    def refresh(self) -> None:
        # 清空旧 card
        while self._cardHost.count():
            item = self._cardHost.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        try:
            data = getCloudAccount().listSubscriptions(limit=20)
        except CloudApiError as exc:
            self._emptyLabel.setText(f"加载失败:{exc.message}")
            return
        items: List[Dict[str, Any]] = data.get("items", []) or []
        if not items:
            self._emptyLabel.show()
            return
        self._emptyLabel.hide()
        for sub in items:
            self._cardHost.addWidget(SubscriptionCard(sub))


# ---------------------------------------------------------------------------
# 子页签:设备
# ---------------------------------------------------------------------------


class _DevicesPage(QWidget):
    devicesChanged = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._buildUi()
        signalBus.devicesChanged.connect(self.refresh)
        signalBus.maxDevicesReached.connect(self._onMaxDevices)

    def _buildUi(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(TitleLabel("已登录设备"))
        head.addStretch(1)
        self._refreshBtn = PushButton("刷新")
        self._refreshBtn.clicked.connect(self.refresh)
        head.addWidget(self._refreshBtn)
        layout.addLayout(head)

        self._hint = CaptionLabel(" ")
        layout.addWidget(self._hint)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { border: 1px solid #e5e7eb; border-radius: 8px; }"
        )
        layout.addWidget(self._list, 1)
        self._revokeBtn = PrimaryPushButton("撤销选中设备")
        self._revokeBtn.setEnabled(False)
        self._revokeBtn.clicked.connect(self._onRevoke)
        layout.addWidget(self._revokeBtn, alignment=Qt.AlignRight)

        self._list.currentItemChanged.connect(self._onSelectionChanged)

    def _onSelectionChanged(self, *args) -> None:
        item = self._list.currentItem()
        self._revokeBtn.setEnabled(item is not None and not item.data(Qt.UserRole + 1))

    def _onMaxDevices(self, limit: int) -> None:
        self._hint.setText(f"⚠ 已达到设备上限 {limit},请先撤销其他设备")

    def refresh(self) -> None:
        self._list.clear()
        try:
            data = getCloudAccount().listDevices()
        except CloudApiError as exc:
            self._hint.setText(f"加载失败:{exc.message}")
            return
        maxActive = int(data.get("maxActive", 3) or 3)
        activeCount = int(data.get("activeCount", 0) or 0)
        self._hint.setText(f"已激活 {activeCount} / {maxActive} 台")
        for d in data.get("items", []):
            label = (
                f"{d.get('deviceName') or '(未命名设备)'}  ·  {d.get('platform') or '—'}\n"
                f"{d.get('devicePublicId', '')[:12]}…  ·  最近活跃 {d.get('lastSeenAt', '')[:19]}"
            )
            if d.get("isCurrent"):
                label += "  ·  (当前设备)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, int(d.get("deviceId", 0) or 0))
            item.setData(Qt.UserRole + 1, bool(d.get("isCurrent", False)))
            self._list.addItem(item)

    def _onRevoke(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        deviceRecordId = int(item.data(Qt.UserRole) or 0)
        if item.data(Qt.UserRole + 1):
            MessageBox("无法撤销", "不能撤销当前正在使用的设备。", self.window()).exec()
            return
        ok = QMessageBox.question(self, "撤销设备", "确认撤销该设备?它的 refresh_token 将立即失效。")
        if ok != QMessageBox.Yes:
            return
        try:
            result = getCloudAccount().revokeDevice(deviceRecordId)
        except CloudApiError as exc:
            MessageBox("撤销失败", exc.message, self.window()).exec()
            return
        MessageBox(
            "已撤销",
            f"该设备的 {result.get('revokedRefreshTokens', 0)} 个 refresh_token 已被撤销。",
            self.window(),
        ).exec()
        self.refresh()
        self.devicesChanged.emit()


# ---------------------------------------------------------------------------
# 主 AccountPanel(抽屉)
# ---------------------------------------------------------------------------


class AccountPanel(QDialog):
    """「我的账户」抽屉 — 主对话框。

    通过 Pivot 切换 3 个子页签:概览 / 订阅 / 设备。
    """

    closed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("我的账户")
        self.resize(560, 640)
        self._buildUi()
        self._refreshAll()

    def _buildUi(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # Header
        head = QHBoxLayout()
        head.addWidget(LargeTitleLabel("我的账户"))
        head.addStretch(1)
        logoutBtn = PushButton("退出登录")
        logoutBtn.clicked.connect(self._onLogout)
        head.addWidget(logoutBtn)
        root.addLayout(head)

        # Pivot
        self._pivot = Pivot()
        self._stacks = QStackedWidget()
        self._pivot.addItem("overview", "概览")
        self._pivot.addItem("subs", "订阅")
        self._pivot.addItem("devices", "设备")
        self._pivot.currentItemChanged.connect(
            lambda k: self._stacks.setCurrentIndex(
                {"overview": 0, "subs": 1, "devices": 2}.get(k, 0)
            )
        )
        root.addWidget(self._pivot)
        root.addWidget(self._stacks, 1)

        # 子页
        self._overview = _OverviewPage(self)
        self._subs = _SubscriptionsPage(self)
        self._devices = _DevicesPage(self)
        self._stacks.addWidget(self._overview)
        self._stacks.addWidget(self._subs)
        self._stacks.addWidget(self._devices)

    def _refreshAll(self) -> None:
        self._overview.refresh()
        self._subs.refresh()
        self._devices.refresh()

    def _onLogout(self) -> None:
        ok = QMessageBox.question(self, "退出登录", "确认退出当前账号?")
        if ok != QMessageBox.Yes:
            return
        try:
            getCloudAuth().logout()
        except Exception:
            logger.exception("[AccountPanel] logout 失败")
        self.closed.emit()
        self.accept()

    # 提供外部手动刷新入口
    def refresh(self) -> None:
        self._refreshAll()


__all__ = ["AccountPanel", "_OverviewPage", "_SubscriptionsPage", "_DevicesPage"]
