# coding: utf-8
"""主窗口中的独立个人中心页面。

THESIS: 将账户状态与常用操作放回应用导航流，拒绝把个人中心包进模态窗口。
OWN-WORLD: Fluent 中性表面、青绿色主色、轻边框与紧凑状态标签。
STORY: 用户先确认身份和余额，再切换订阅、设备与账单，操作后留在同一页面。
FIRST VIEWPORT: 居中的宽内容面板，上方身份区，下方四页签与余额主卡。
FORM: 设计稿 PrismaticaAccount 的页面化适配；保留其信息层级并扩展为自适应布局。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    IconWidget,
    MessageBox,
    Pivot,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    TitleLabel,
    isDarkTheme,
    qconfig,
)

from app.core.services import CloudApiError, getCloudAccount, getCloudAuth
from app.core.utils import logger, signalBus
from app.core.utils.application_lifecycle import isApplicationShuttingDown
from app.view.widgets.prismatica_theme import pageBackgroundColor


def _clearLayout(layout: QVBoxLayout) -> None:
    """删除动态列表中的旧控件。"""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item else None
        child = item.layout() if item else None
        if widget is not None:
            widget.deleteLater()
        elif child is not None:
            _clearLayout(child)  # type: ignore[arg-type]


def _date(value: Any) -> str:
    text = str(value or "")
    return text[:10] if text else "—"


def _dateTime(value: Any) -> str:
    text = str(value or "")
    if not text:
        return "—"
    return text[:19].replace("T", " ")


class _TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)


class _AccountTask(QRunnable):
    """在全局线程池中执行账户网络请求，结果通过 Qt 信号回到 UI 线程。"""

    def __init__(self, operation) -> None:
        super().__init__()
        self._operation = operation
        self.signals = _TaskSignals()

    @Slot()
    def run(self) -> None:
        if isApplicationShuttingDown():
            return
        try:
            result = self._operation()
        except CloudApiError as exc:
            self._emitSafely(self.signals.failed, exc.message)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[AccountInterface] 后台请求失败: {exc}")
            self._emitSafely(
                self.signals.failed,
                "无法连接到账户服务，请稍后重试",
            )
        else:
            self._emitSafely(self.signals.succeeded, result)

    @staticmethod
    def _emitSafely(signal, *args) -> None:
        """退出过程中接收对象可能已销毁，此时直接丢弃任务结果。"""
        if isApplicationShuttingDown():
            return
        try:
            signal.emit(*args)
        except RuntimeError:
            logger.debug("[AccountInterface] 应用退出中，已丢弃后台任务结果")


def _runAccountTask(operation, onSuccess, onFailure) -> None:
    task = _AccountTask(operation)
    task.signals.succeeded.connect(onSuccess)
    task.signals.failed.connect(onFailure)
    QThreadPool.globalInstance().start(task)


class _ElidedLabel(QLabel):
    """保留完整 tooltip，并在横向空间不足时显示尾部省略号。"""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._fullText = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._fullText = str(text)
        self.setToolTip(self._fullText)
        self._updateElision()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._updateElision()

    def _updateElision(self) -> None:
        width = max(self.contentsRect().width(), 1)
        QLabel.setText(
            self,
            self.fontMetrics().elidedText(
                self._fullText, Qt.TextElideMode.ElideRight, width
            ),
        )


class _SurfaceCard(QFrame):
    def __init__(self, objectName: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName(objectName)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)


class _StatusChip(CaptionLabel):
    def __init__(
        self,
        text: str,
        tone: str = "muted",
        parent: Optional[QWidget] = None,
    ) -> None:
        # FluentLabelBase 的重载构造器不支持从子类透传 (text, parent)。
        super().__init__(parent)
        self.setText(text)
        self.setObjectName("accountStatusChip")
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setContentsMargins(8, 2, 8, 2)


class _OverviewPage(QWidget):
    openBillsRequested = Signal()
    accountLoaded = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("accountOverviewPage")
        self._loading = False
        self._buildUi()
        signalBus.balanceChanged.connect(self._onBalanceChanged)

    def _buildUi(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        layout.setSpacing(16)

        self._balanceCard = _SurfaceCard("accountBalanceCard", self)
        cardLayout = QVBoxLayout(self._balanceCard)
        cardLayout.setContentsMargins(22, 20, 22, 20)
        cardLayout.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(CaptionLabel("当前可用余额"))
        top.addStretch(1)
        self._refreshButton = PushButton(FluentIcon.SYNC, "刷新", self._balanceCard)
        self._refreshButton.setObjectName("accountSmallButton")
        self._refreshButton.clicked.connect(self.refresh)
        top.addWidget(self._refreshButton)
        cardLayout.addLayout(top)

        amountRow = QHBoxLayout()
        amountRow.setSpacing(8)
        self._balanceLabel = QLabel("—")
        self._balanceLabel.setObjectName("accountBalanceNumber")
        amountRow.addWidget(self._balanceLabel)
        amountRow.addWidget(BodyLabel("积分"), alignment=Qt.AlignmentFlag.AlignBottom)
        amountRow.addStretch(1)
        cardLayout.addLayout(amountRow)

        self._balanceMeta = CaptionLabel("预占：—  ·  可用：—  ·  订阅：—")
        self._balanceMeta.setWordWrap(True)
        cardLayout.addWidget(self._balanceMeta)

        billsRow = QHBoxLayout()
        billsRow.addStretch(1)
        self._viewBillsButton = PushButton(
            FluentIcon.CHEVRON_RIGHT_MED, "查看流水", self._balanceCard
        )
        self._viewBillsButton.setObjectName("accountLinkButton")
        self._viewBillsButton.clicked.connect(self.openBillsRequested)
        billsRow.addWidget(self._viewBillsButton)
        cardLayout.addLayout(billsRow)
        layout.addWidget(self._balanceCard)

        secondary = QHBoxLayout()
        secondary.setSpacing(10)
        self._changePasswordButton = PushButton(FluentIcon.FINGERPRINT, "修改密码", self)
        self._deleteAccountButton = PushButton(FluentIcon.REMOVE_FROM, "注销账号", self)
        self._deleteAccountButton.setObjectName("accountDangerButton")
        self._changePasswordButton.setMinimumHeight(38)
        self._deleteAccountButton.setMinimumHeight(38)
        self._changePasswordButton.clicked.connect(self._onChangePassword)
        self._deleteAccountButton.clicked.connect(self._onDeleteAccount)
        secondary.addWidget(self._changePasswordButton, 1)
        secondary.addWidget(self._deleteAccountButton, 1)
        layout.addLayout(secondary)

        deviceRow = QHBoxLayout()
        deviceIcon = IconWidget(FluentIcon.IOT, self)
        deviceIcon.setFixedSize(18, 18)
        deviceRow.addWidget(deviceIcon)
        self._devicesHint = CaptionLabel("已激活 — / — 台")
        deviceRow.addWidget(self._devicesHint)
        deviceRow.addStretch(1)
        layout.addLayout(deviceRow)
        layout.addStretch(1)

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._refreshButton.setEnabled(False)
        self._devicesHint.setText("正在刷新账户信息…")
        _runAccountTask(
            lambda: (getCloudAccount().me(), getCloudAccount().listDevices()),
            self._onRefreshSucceeded,
            self._onRefreshFailed,
        )

    @Slot(object)
    def _onRefreshSucceeded(self, result) -> None:
        data, devices = result
        self._updateFrom(data)
        self.accountLoaded.emit(data)
        self._devicesHint.setText(
            f"已激活 {int(devices.get('activeCount', 0) or 0)} / "
            f"{int(devices.get('maxActive', 3) or 3)} 台"
        )
        self._finishRefresh()

    @Slot(str)
    def _onRefreshFailed(self, message: str) -> None:
        self._devicesHint.setText(f"加载失败：{message}，请稍后重试")
        self._finishRefresh()

    def _finishRefresh(self) -> None:
        self._loading = False
        self._refreshButton.setEnabled(True)

    def _updateFrom(self, data: Dict[str, Any]) -> None:
        balance = int(data.get("balance", 0) or 0)
        reserved = int(data.get("reserved", data.get("frozenBalance", 0)) or 0)
        self._balanceLabel.setText(f"{balance:,}")

        subscription = data.get("subscription") or {}
        plan = str(subscription.get("planCode") or data.get("tier") or "FREE").upper()
        period = ""
        if subscription:
            period = (
                f"（{_date(subscription.get('currentPeriodStart'))} → "
                f"{_date(subscription.get('currentPeriodEnd'))}）"
            )
        self._balanceMeta.setText(
            f"预占：{reserved:,}  ·  可用：{max(balance - reserved, 0):,}  ·  "
            f"订阅：{plan}{period}"
        )

    def _onBalanceChanged(self, balance: int) -> None:
        self._balanceLabel.setText(f"{int(balance):,}")

    def _onChangePassword(self) -> None:
        oldPassword, accepted = QInputDialog.getText(
            self, "修改密码", "当前密码：", QLineEdit.EchoMode.Password
        )
        if not accepted or not oldPassword:
            return
        newPassword, accepted = QInputDialog.getText(
            self,
            "修改密码",
            "新密码（至少 10 位，含字母和数字）：",
            QLineEdit.EchoMode.Password,
        )
        if not accepted or not newPassword:
            return
        confirmation, accepted = QInputDialog.getText(
            self,
            "确认新密码",
            "请再次输入新密码：",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return
        if newPassword != confirmation:
            MessageBox("两次输入不一致", "请重新输入并确认新密码。", self).exec()
            return
        self._changePasswordButton.setEnabled(False)
        self._changePasswordButton.setText("正在修改…")
        _runAccountTask(
            lambda: getCloudAuth().changePassword(oldPassword, newPassword),
            self._onChangePasswordSucceeded,
            self._onChangePasswordFailed,
        )

    @Slot(object)
    def _onChangePasswordSucceeded(self, _result) -> None:
        self._changePasswordButton.setEnabled(True)
        self._changePasswordButton.setText("修改密码")
        MessageBox("密码已更新", "其他设备需要重新登录。", self).exec()

    @Slot(str)
    def _onChangePasswordFailed(self, message: str) -> None:
        self._changePasswordButton.setEnabled(True)
        self._changePasswordButton.setText("修改密码")
        MessageBox("修改失败", message, self).exec()

    def _onDeleteAccount(self) -> None:
        if (
            QMessageBox.warning(
                self,
                "注销账号",
                "账号将在 30 天后永久删除，此操作会立即退出所有设备。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        password, accepted = QInputDialog.getText(
            self, "确认注销", "请输入当前密码：", QLineEdit.EchoMode.Password
        )
        if not accepted or not password:
            return
        self._deleteAccountButton.setEnabled(False)
        self._deleteAccountButton.setText("正在提交…")
        _runAccountTask(
            lambda: getCloudAccount().deleteAccount(password),
            self._onDeleteAccountSucceeded,
            self._onDeleteAccountFailed,
        )

    @Slot(object)
    def _onDeleteAccountSucceeded(self, result) -> None:
        self._deleteAccountButton.setEnabled(True)
        self._deleteAccountButton.setText("注销账号")
        scheduled = result.get("scheduledHardDeleteAt", "30 天后")
        MessageBox("已提交注销", f"账号预计于 {scheduled} 永久删除。", self).exec()

    @Slot(str)
    def _onDeleteAccountFailed(self, message: str) -> None:
        self._deleteAccountButton.setEnabled(True)
        self._deleteAccountButton.setText("注销账号")
        MessageBox("注销失败", message, self).exec()


class _SubscriptionRow(_SurfaceCard):
    def __init__(self, data: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__("accountListCard", parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(7)

        titleRow = QHBoxLayout()
        title = StrongBodyLabel(str(data.get("planDisplayName") or data.get("planCode") or "订阅"))
        titleRow.addWidget(title)
        status = str(data.get("status") or "active").lower()
        statusText = {
            "active": "生效中",
            "pending": "待生效",
            "expired": "已到期",
            "canceled": "已取消",
            "past_due": "待处理",
        }.get(status, status.upper())
        tone = "success" if status == "active" else "warning" if status in {"pending", "past_due"} else "muted"
        titleRow.addWidget(_StatusChip(statusText, tone, self))
        titleRow.addStretch(1)
        layout.addLayout(titleRow)

        start = _date(data.get("currentPeriodStart"))
        end = _date(data.get("currentPeriodEnd") or data.get("expiresAt"))
        layout.addWidget(CaptionLabel(f"订阅周期：{start} → {end}"))
        quota = int(data.get("monthlyQuota", 0) or 0)
        autoRenew = "自动续费" if data.get("autoRenew") else "到期后停止"
        layout.addWidget(CaptionLabel(f"周期额度：{quota:,} 积分  ·  {autoRenew}"))


class _SubscriptionsPage(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(TitleLabel("订阅"))
        header.addStretch(1)
        self._countChip = _StatusChip("0 项订阅", "muted", self)
        header.addWidget(self._countChip)
        self._refreshButton = PushButton(FluentIcon.SYNC, "刷新", self)
        self._refreshButton.clicked.connect(self.refresh)
        header.addWidget(self._refreshButton)
        layout.addLayout(header)

        self._listLayout = QVBoxLayout()
        self._listLayout.setSpacing(10)
        layout.addLayout(self._listLayout)
        self._emptyLabel = CaptionLabel("暂无订阅。")
        self._emptyLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._emptyLabel.setObjectName("accountEmptyState")
        layout.addWidget(self._emptyLabel)
        layout.addStretch(1)

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._refreshButton.setEnabled(False)
        _clearLayout(self._listLayout)
        self._emptyLabel.setText("正在加载订阅…")
        self._emptyLabel.show()
        _runAccountTask(
            lambda: getCloudAccount().listSubscriptions(limit=20),
            self._onRefreshSucceeded,
            self._onRefreshFailed,
        )

    @Slot(object)
    def _onRefreshSucceeded(self, data) -> None:
        items = list(data.get("items", []) or [])
        self._countChip.setText(f"{len(items)} 项订阅")
        self._emptyLabel.setText("暂无订阅。")
        self._emptyLabel.setVisible(not items)
        for item in items:
            self._listLayout.addWidget(_SubscriptionRow(item, self))
        self._finishRefresh()

    @Slot(str)
    def _onRefreshFailed(self, message: str) -> None:
        self._emptyLabel.setText(f"订阅加载失败：{message}，请稍后重试")
        self._emptyLabel.show()
        self._finishRefresh()

    def _finishRefresh(self) -> None:
        self._loading = False
        self._refreshButton.setEnabled(True)


class _DeviceRow(_SurfaceCard):
    revokeRequested = Signal(int)

    def __init__(self, data: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__("accountDeviceRow", parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        iconHost = QFrame(self)
        iconHost.setObjectName("accountRoundIcon")
        iconHost.setFixedSize(38, 38)
        iconLayout = QVBoxLayout(iconHost)
        iconLayout.setContentsMargins(9, 9, 9, 9)
        icon = IconWidget(FluentIcon.IOT, iconHost)
        iconLayout.addWidget(icon)
        layout.addWidget(iconHost)

        text = QVBoxLayout()
        text.setSpacing(3)
        nameRow = QHBoxLayout()
        nameRow.setSpacing(8)
        name = str(data.get("deviceName") or "未命名设备")
        platform = str(data.get("platform") or "未知平台")
        nameLabel = _ElidedLabel(f"{name} · {platform}", self)
        nameLabel.setObjectName("accountStrongLabel")
        nameRow.addWidget(nameLabel, 1)
        isCurrent = bool(data.get("isCurrent", False))
        if isCurrent:
            nameRow.addWidget(_StatusChip("当前设备", "success", self))
        nameRow.addStretch(1)
        text.addLayout(nameRow)
        publicId = str(data.get("devicePublicId") or "")
        maskedId = f"{publicId[:12]}…" if len(publicId) > 12 else publicId or "—"
        text.addWidget(CaptionLabel(f"设备标识：{maskedId}"))
        text.addWidget(CaptionLabel(f"最近活跃：{_dateTime(data.get('lastSeenAt'))}"))
        layout.addLayout(text, 1)

        button = PushButton("撤销", self)
        button.setEnabled(not isCurrent)
        deviceId = int(data.get("deviceId", 0) or 0)
        button.clicked.connect(lambda: self.revokeRequested.emit(deviceId))
        layout.addWidget(button)


class _DevicesPage(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._loading = False
        self._revokeInProgress = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(TitleLabel("已登录设备"))
        header.addStretch(1)
        self._refreshButton = PushButton(FluentIcon.SYNC, "刷新", self)
        self._refreshButton.clicked.connect(self.refresh)
        header.addWidget(self._refreshButton)
        layout.addLayout(header)

        self._hint = CaptionLabel("已激活 — / — 台")
        layout.addWidget(self._hint)
        self._listLayout = QVBoxLayout()
        self._listLayout.setSpacing(8)
        layout.addLayout(self._listLayout)
        self._emptyLabel = CaptionLabel("当前没有已登录设备。")
        self._emptyLabel.setObjectName("accountEmptyState")
        self._emptyLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._emptyLabel)
        layout.addStretch(1)

        signalBus.devicesChanged.connect(self.refresh)
        signalBus.maxDevicesReached.connect(self._onMaxDevicesReached)

    def refresh(self) -> None:
        if self._loading or self._revokeInProgress:
            return
        self._loading = True
        self._refreshButton.setEnabled(False)
        _clearLayout(self._listLayout)
        self._hint.setText("正在加载设备…")
        _runAccountTask(
            getCloudAccount().listDevices,
            self._onRefreshSucceeded,
            self._onRefreshFailed,
        )

    @Slot(object)
    def _onRefreshSucceeded(self, data) -> None:
        items = list(data.get("items", []) or [])
        active = int(data.get("activeCount", 0) or 0)
        maximum = int(data.get("maxActive", 3) or 3)
        self._hint.setText(f"已激活 {active} / {maximum} 台")
        self._emptyLabel.setVisible(not items)
        for item in items:
            row = _DeviceRow(item, self)
            row.revokeRequested.connect(self._revokeDevice)
            self._listLayout.addWidget(row)
        self._finishRefresh()

    @Slot(str)
    def _onRefreshFailed(self, message: str) -> None:
        self._hint.setText(f"设备加载失败：{message}，请稍后重试")
        self._emptyLabel.hide()
        self._finishRefresh()

    def _finishRefresh(self) -> None:
        self._loading = False
        self._refreshButton.setEnabled(True)

    def _onMaxDevicesReached(self, limit: int) -> None:
        self._hint.setText(f"设备数量已达上限（{int(limit)} 台），请撤销不再使用的设备")

    def _revokeDevice(self, deviceId: int) -> None:
        if not deviceId or self._revokeInProgress:
            return
        if (
            QMessageBox.question(
                self,
                "撤销设备",
                "撤销后，该设备需要重新登录。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._revokeInProgress = True
        self._setDeviceRowsEnabled(False)
        self._hint.setText("正在撤销设备…")
        _runAccountTask(
            lambda: getCloudAccount().revokeDevice(deviceId),
            self._onRevokeSucceeded,
            self._onRevokeFailed,
        )

    @Slot(object)
    def _onRevokeSucceeded(self, _result) -> None:
        self._revokeInProgress = False
        self.refresh()

    @Slot(str)
    def _onRevokeFailed(self, message: str) -> None:
        self._revokeInProgress = False
        self._setDeviceRowsEnabled(True)
        self._hint.setText(f"撤销失败：{message}")

    def _setDeviceRowsEnabled(self, enabled: bool) -> None:
        for index in range(self._listLayout.count()):
            widget = self._listLayout.itemAt(index).widget()
            if isinstance(widget, _DeviceRow):
                widget.setEnabled(enabled)


class _BillRow(_SurfaceCard):
    def __init__(self, data: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__("accountBillRow", parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        iconHost = QFrame(self)
        iconHost.setObjectName("accountRoundIcon")
        iconHost.setFixedSize(38, 38)
        iconLayout = QVBoxLayout(iconHost)
        iconLayout.setContentsMargins(9, 9, 9, 9)
        iconLayout.addWidget(IconWidget(FluentIcon.DOCUMENT, iconHost))
        layout.addWidget(iconHost)

        text = QVBoxLayout()
        text.setSpacing(3)
        title = str(data.get("actionDisplayName") or data.get("actionType") or "积分消费")
        titleLabel = _ElidedLabel(title, self)
        titleLabel.setObjectName("accountStrongLabel")
        text.addWidget(titleLabel)
        description = str(data.get("description") or data.get("actionType") or "账户账单")
        tokenParts = []
        if data.get("inputTokens") is not None:
            tokenParts.append(f"输入 {int(data.get('inputTokens') or 0):,} Token")
        if data.get("outputTokens") is not None:
            tokenParts.append(f"输出 {int(data.get('outputTokens') or 0):,} Token")
        version = str(data.get("pricingVersion") or "")
        details = [description, *tokenParts]
        if version:
            details.append(f"价格版本 {version}")
        details.append(_dateTime(data.get("createdAt")))
        metaLabel = _ElidedLabel(
            " · ".join(details), self
        )
        metaLabel.setObjectName("accountCaptionLabel")
        text.addWidget(metaLabel)
        layout.addLayout(text, 1)

        status = str(data.get("status") or "pending").lower()
        statusText = {"settled": "已结算", "pending": "处理中", "refunded": "已退款"}.get(
            status, status
        )
        tone = "success" if status == "settled" else "warning" if status == "pending" else "muted"
        layout.addWidget(_StatusChip(statusText, tone, self))
        cost = int(data.get("realCost", data.get("estimatedCost", 0)) or 0)
        amount = QLabel(f"-{abs(cost):,} 点")
        amount.setObjectName("accountBillAmount")
        layout.addWidget(amount)


class _BillsPage(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._showAll = False
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(TitleLabel("最近账单"))
        header.addStretch(1)
        self._countChip = _StatusChip("最近 3 条", "muted", self)
        header.addWidget(self._countChip)
        self._allButton = PushButton("查看全部", self)
        self._allButton.clicked.connect(self._showAllBills)
        header.addWidget(self._allButton)
        self._refreshButton = PushButton(FluentIcon.SYNC, "刷新", self)
        self._refreshButton.clicked.connect(self.refresh)
        header.addWidget(self._refreshButton)
        layout.addLayout(header)

        self._description = CaptionLabel("显示最近 3 条积分流水。")
        layout.addWidget(self._description)
        self._listLayout = QVBoxLayout()
        self._listLayout.setSpacing(8)
        layout.addLayout(self._listLayout)
        self._emptyLabel = CaptionLabel("暂无账单。完成一次计费任务后会显示在这里。")
        self._emptyLabel.setObjectName("accountEmptyState")
        self._emptyLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._emptyLabel)
        layout.addStretch(1)

    def _showAllBills(self) -> None:
        self._showAll = True
        self._allButton.hide()
        self.refresh()

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._refreshButton.setEnabled(False)
        _clearLayout(self._listLayout)
        limit = 50 if self._showAll else 3
        self._emptyLabel.setText("正在加载账单…")
        self._emptyLabel.show()
        _runAccountTask(
            lambda: getCloudAccount().listBills(limit=limit),
            self._onRefreshSucceeded,
            self._onRefreshFailed,
        )

    @Slot(object)
    def _onRefreshSucceeded(self, data) -> None:
        items = list(data.get("items", []) or [])
        self._countChip.setText(f"{len(items)} 条记录")
        self._description.setText(
            "显示当前账户的最近账单。" if self._showAll else "显示最近 3 条积分流水。"
        )
        self._emptyLabel.setText("暂无账单。完成一次计费任务后会显示在这里。")
        self._emptyLabel.setVisible(not items)
        for item in items:
            self._listLayout.addWidget(_BillRow(item, self))
        self._finishRefresh()

    @Slot(str)
    def _onRefreshFailed(self, message: str) -> None:
        self._emptyLabel.setText(f"账单加载失败：{message}，请稍后重试")
        self._emptyLabel.show()
        self._finishRefresh()

    def _finishRefresh(self) -> None:
        self._loading = False
        self._refreshButton.setEnabled(True)


class AccountInterface(ScrollArea):
    """可加入主窗口导航栈的个人中心页面。"""

    loggedOut = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("accountInterface")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._loadedTabs: set[str] = set()
        self._buildUi()
        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)

    def _buildUi(self) -> None:
        self._canvas = QWidget(self)
        self._canvas.setObjectName("accountCanvas")
        canvasLayout = QHBoxLayout(self._canvas)
        canvasLayout.setContentsMargins(28, 28, 28, 32)
        canvasLayout.addStretch(1)

        self._surface = _SurfaceCard("accountSurface", self._canvas)
        self._surface.setMinimumWidth(560)
        self._surface.setMaximumWidth(860)
        surfaceLayout = QVBoxLayout(self._surface)
        surfaceLayout.setContentsMargins(0, 0, 0, 24)
        surfaceLayout.setSpacing(0)

        header = QFrame(self._surface)
        header.setObjectName("accountHeader")
        headerLayout = QHBoxLayout(header)
        headerLayout.setContentsMargins(24, 20, 24, 18)
        headerLayout.setSpacing(16)

        avatar = QFrame(header)
        avatar.setObjectName("accountAvatar")
        avatar.setFixedSize(56, 56)
        avatarLayout = QVBoxLayout(avatar)
        avatarLayout.setContentsMargins(14, 14, 14, 14)
        self._avatarIcon = IconWidget(FluentIcon.PEOPLE, avatar)
        avatarLayout.addWidget(self._avatarIcon)
        headerLayout.addWidget(avatar)

        identity = QVBoxLayout()
        identity.setSpacing(3)
        titleRow = QHBoxLayout()
        titleRow.setSpacing(8)
        self._emailLabel = _ElidedLabel("—", header)
        self._emailLabel.setObjectName("accountEmailLabel")
        titleRow.addWidget(self._emailLabel, 1)
        self._tierChip = _StatusChip("FREE", "brand", header)
        titleRow.addWidget(self._tierChip)
        titleRow.addStretch(1)
        identity.addLayout(titleRow)
        self._displayNameLabel = CaptionLabel("未设置昵称")
        identity.addWidget(self._displayNameLabel)
        headerLayout.addLayout(identity, 1)

        self._logoutButton = PushButton(FluentIcon.POWER_BUTTON, "退出登录", header)
        self._logoutButton.setObjectName("accountLogoutButton")
        self._logoutButton.clicked.connect(self._onLogout)
        headerLayout.addWidget(self._logoutButton)
        surfaceLayout.addWidget(header)

        body = QWidget(self._surface)
        body.setObjectName("accountBody")
        bodyLayout = QVBoxLayout(body)
        bodyLayout.setContentsMargins(24, 0, 24, 0)
        bodyLayout.setSpacing(0)

        self._pivot = Pivot(body)
        self._pivot.addItem("overview", "概览")
        self._pivot.addItem("subs", "订阅")
        self._pivot.addItem("devices", "设备")
        self._pivot.addItem("bills", "账单")
        self._pivot.setCurrentItem("overview")
        self._pivot.currentItemChanged.connect(self._onTabChanged)
        bodyLayout.addWidget(self._pivot)

        self._stack = QStackedWidget(body)
        self._stack.setObjectName("accountStack")
        self._stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self._overview = _OverviewPage(self._stack)
        self._subscriptions = _SubscriptionsPage(self._stack)
        self._devices = _DevicesPage(self._stack)
        self._bills = _BillsPage(self._stack)
        for page in (self._overview, self._subscriptions, self._devices, self._bills):
            self._stack.addWidget(page)
        self._overview.openBillsRequested.connect(lambda: self.showTab("bills"))
        self._overview.accountLoaded.connect(self._updateIdentity)
        bodyLayout.addWidget(self._stack, 1)
        surfaceLayout.addWidget(body, 1)

        # 中央内容在窄窗口保持设计稿的 560px 下限，在常见桌面窗口扩展到约 700–860px。
        canvasLayout.addWidget(self._surface, 4)
        canvasLayout.addStretch(1)
        self.setWidget(self._canvas)

    def _onTabChanged(self, key: str) -> None:
        mapping = {"overview": 0, "subs": 1, "devices": 2, "bills": 3}
        index = mapping.get(key, 0)
        self._stack.setCurrentIndex(index)
        if key not in self._loadedTabs:
            (self._overview, self._subscriptions, self._devices, self._bills)[index].refresh()
            self._loadedTabs.add(key)

    def showTab(self, key: str) -> None:
        if key not in {"overview", "subs", "devices", "bills"}:
            key = "overview"
        self._pivot.setCurrentItem(key)
        self._onTabChanged(key)

    def refresh(self) -> None:
        """页面每次进入时刷新身份与当前页签。"""
        # 先用本地会话即时更新抬头；概览的单次 /me 请求会随后补全服务端信息。
        self._updateIdentity({})
        currentKey = self._pivot.currentRouteKey() or "overview"
        self._loadedTabs.discard(currentKey)
        self._onTabChanged(currentKey)

    def _updateIdentity(self, data: Dict[str, Any]) -> None:
        session = getCloudAuth()._api.getSession()
        email = data.get("email") or getattr(session, "email", None) or "—"
        displayName = data.get("displayName") or getattr(session, "displayName", None)
        tier = data.get("tier") or getattr(session, "tier", None) or "free"
        self._emailLabel.setText(str(email))
        self._displayNameLabel.setText(str(displayName or "未设置昵称"))
        self._tierChip.setText(str(tier).upper())

    def _onLogout(self) -> None:
        if (
            QMessageBox.question(
                self,
                "退出登录",
                "确认退出当前账号？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._logoutButton.setEnabled(False)
        self._logoutButton.setText("正在退出…")
        _runAccountTask(
            getCloudAuth().logout,
            self._onLogoutSucceeded,
            self._onLogoutFailed,
        )

    @Slot(object)
    def _onLogoutSucceeded(self, _result) -> None:
        self._logoutButton.setEnabled(True)
        self._logoutButton.setText("退出登录")
        self._loadedTabs.clear()
        self.loggedOut.emit()

    @Slot(str)
    def _onLogoutFailed(self, message: str) -> None:
        self._logoutButton.setEnabled(True)
        self._logoutButton.setText("退出登录")
        MessageBox("退出失败", message, self).exec()

    def _applyTheme(self) -> None:
        dark = isDarkTheme()
        page = pageBackgroundColor(dark).name()
        surface = "#2B2B2B" if dark else "#FFFFFF"
        muted = "#383838" if dark else "#F5F5F5"
        border = "#454545" if dark else "#E5E5E5"
        text = "#F5F5F5" if dark else "#1F1F1F"
        secondary = "#B9B9B9" if dark else "#616161"
        brandSoft = "rgba(0, 176, 156, 0.16)" if dark else "rgba(0, 176, 156, 0.09)"
        readableAccent = "#56D6C5" if dark else "#007368"
        readableDanger = "#FF7A7E" if dark else "#B3261E"
        dangerBorder = "#B65A60" if dark else "#C97772"
        dangerSurface = "#33282A" if dark else "#FFF7F6"
        dangerHover = "#422A2D" if dark else "#FDEDEA"
        dangerPressed = "#512D31" if dark else "#F9DED9"
        dangerDisabledBorder = "#4A4A4A" if dark else "#D8D8D8"
        dangerDisabledSurface = "#303030" if dark else "#F3F3F3"
        dangerDisabledText = "#777777" if dark else "#9B9B9B"
        readableSuccess = "#72D572" if dark else "#107C10"
        readableWarning = "#F4D35E" if dark else "#725A00"
        self.setStyleSheet(
            f"""
            QScrollArea#accountInterface {{ background: transparent; border: none; }}
            QWidget#accountCanvas {{ background: {page}; }}
            QFrame#accountSurface {{
                background: {surface}; border: 1px solid {border}; border-radius: 14px;
            }}
            QFrame#accountHeader {{
                background: transparent; border: none; border-bottom: 1px solid {border};
            }}
            QWidget#accountBody, QStackedWidget#accountStack {{
                background: transparent; border: none;
            }}
            QFrame#accountAvatar {{
                background: {brandSoft}; border: none; border-radius: 28px;
            }}
            QFrame#accountRoundIcon {{
                background: {muted}; border: none; border-radius: 19px;
            }}
            QFrame#accountBalanceCard, QFrame#accountListCard,
            QFrame#accountDeviceRow, QFrame#accountBillRow {{
                background: {surface}; border: 1px solid {border}; border-radius: 10px;
            }}
            QLabel {{ color: {text}; }}
            QLabel#accountEmailLabel {{
                color: {text}; font-size: 18px; font-weight: 600;
            }}
            QLabel#accountStrongLabel {{
                color: {text}; font-size: 14px; font-weight: 600;
            }}
            QLabel#accountCaptionLabel {{
                color: {secondary}; font-size: 12px;
            }}
            QLabel#accountBalanceNumber {{
                color: {readableAccent}; font-size: 34px; font-weight: 600;
            }}
            QLabel#accountBillAmount {{
                color: {readableDanger}; font-size: 13px; font-weight: 600;
            }}
            QLabel#accountStatusChip {{
                padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600;
                background: {muted}; color: {secondary};
            }}
            QLabel#accountStatusChip[tone="brand"] {{ background: {brandSoft}; color: {readableAccent}; }}
            QLabel#accountStatusChip[tone="success"] {{ background: rgba(16, 124, 16, 0.12); color: {readableSuccess}; }}
            QLabel#accountStatusChip[tone="warning"] {{ background: rgba(193, 156, 0, 0.14); color: {readableWarning}; }}
            QLabel#accountEmptyState {{
                color: {secondary}; padding: 42px 16px; background: {muted}; border-radius: 10px;
            }}
            QPushButton#accountLinkButton {{ color: {readableAccent}; border: none; background: transparent; }}
            QPushButton#accountLinkButton:hover {{ background: {brandSoft}; }}
            """
        )
        dangerButtonStyle = f"""
            PushButton {{
                color: {readableDanger}; background: {dangerSurface};
                border: 1px solid {dangerBorder}; border-radius: 5px;
                padding: 5px 12px 6px 12px;
            }}
            PushButton[hasIcon=true] {{
                padding: 5px 12px 6px 36px;
            }}
            PushButton:hover {{
                background: {dangerHover}; border-color: {readableDanger};
            }}
            PushButton:pressed {{
                background: {dangerPressed}; border-color: {readableDanger};
            }}
            PushButton:disabled {{
                color: {dangerDisabledText}; background: {dangerDisabledSurface};
                border-color: {dangerDisabledBorder};
            }}
        """
        self._logoutButton.setStyleSheet(dangerButtonStyle)
        self._overview._deleteAccountButton.setStyleSheet(dangerButtonStyle)
        self._logoutButton.setIcon(
            FluentIcon.POWER_BUTTON.icon(color=QColor(readableDanger))
        )
        self._overview._deleteAccountButton.setIcon(
            FluentIcon.REMOVE_FROM.icon(color=QColor(readableDanger))
        )
        self._avatarIcon.setStyleSheet(
            f"color: {readableAccent}; background: transparent;"
        )


__all__ = ["AccountInterface"]
