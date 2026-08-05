# coding: utf-8
"""账户中心主界面

修复与美化(2026-08-05):
    1. 原来 BalanceCard 占满宽度,BillTable + DevicePanel 比例失衡
       → 改 5:4 比例,且 BalanceCard 顶部 hero,下方分两栏
    2. 标题层级不清
       → 顶部 H1 + 副标题 + 三块 SettingCard 风格的小节
    3. 反馈按钮孤零零
       → 收纳到"账户信息"小节,与其他动作按钮并列
    4. 缺少滚动容器设置的最大高度
       → 显式设置,避免内容溢出
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSizePolicy,
)
from qfluentwidgets import (
    ScrollArea,
    FluentIcon as FIF,
    StrongBodyLabel,
    BodyLabel,
    CaptionLabel,
    PushButton,
    HyperlinkButton,
    IconWidget,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    ElevatedCardWidget,
)

from app.core.services.auth_service import getAuthService
from app.core.utils import logger
from app.core.utils.signal_bus import signalBus
from app.view.widgets.auth.login_dialog import LoginDialog
from app.view.widgets.billing.balance_card import BalanceCard
from app.view.widgets.billing.bill_table import BillTableWidget
from app.view.widgets.billing.device_panel import DevicePanel
from app.view.widgets.billing.recharge_dialog import RechargeDialog 


class _SectionCard(ElevatedCardWidget):
    """带标题的卡片容器,统一小节视觉"""

    def __init__(self, icon: FIF, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.icon = IconWidget(icon, self)
        self.icon.setFixedSize(20, 20)
        header.addWidget(self.icon)

        self.titleLabel = StrongBodyLabel(title, self)
        self.titleLabel.setStyleSheet("font-size: 15px;")
        header.addWidget(self.titleLabel)
        header.addStretch(1)
        outer.addLayout(header)

        # 内容容器
        self.contentLayout = QVBoxLayout()
        self.contentLayout.setContentsMargins(0, 0, 0, 0)
        self.contentLayout.setSpacing(8)
        outer.addLayout(self.contentLayout)


class AccountInterface(QWidget):
    """账户中心(底部导航)"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("accountInterface")

        # 外层布局
        outerLayout = QVBoxLayout(self)
        outerLayout.setContentsMargins(0, 0, 0, 0)

        # 滚动区
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.enableTransparentBackground()
        outerLayout.addWidget(scroll)

        container = QWidget(scroll)
        containerLayout = QVBoxLayout(container)
        containerLayout.setContentsMargins(28, 24, 28, 24)
        containerLayout.setSpacing(18)

        # ---------- 顶部 hero(标题 + 副标题 + 重新激活链接) ----------
        hero = QVBoxLayout()
        hero.setSpacing(4)

        heroTop = QHBoxLayout()
        title = StrongBodyLabel("账户中心", container)
        title.setStyleSheet("font-size: 24px;")
        heroTop.addWidget(title)
        heroTop.addStretch(1)
        # 重新激活链接(右侧)
        self.reactivateBtn = HyperlinkButton(
            url="", text="↻ 重新激活", parent=container
        )
        self.reactivateBtn.clicked.connect(self._onReactivate)
        heroTop.addWidget(self.reactivateBtn)
        hero.addLayout(heroTop)

        subtitle = CaptionLabel("管理你的内测凭证、查看余额与账单流水", container)
        hero.addWidget(subtitle)

        # 凭证损坏提示条(默认隐藏)
        self.corruptBanner = CaptionLabel("", container)
        self.corruptBanner.setStyleSheet(
            "color: #b00; background: rgba(187,0,0,8%); "
            "padding: 6px 10px; border-radius: 6px;"
        )
        self.corruptBanner.setWordWrap(True)
        self.corruptBanner.hide()
        hero.addWidget(self.corruptBanner)

        # 会话失效提示条(2026-08-05 F5):refresh token 失效时显示,
        # 引导用户走「重新激活」。默认隐藏。
        self.sessionBanner = CaptionLabel("", container)
        self.sessionBanner.setStyleSheet(
            "color: #b67c2b; background: rgba(255,180,0,10%); "
            "padding: 6px 10px; border-radius: 6px;"
        )
        self.sessionBanner.setWordWrap(True)
        self.sessionBanner.hide()
        hero.addWidget(self.sessionBanner)

        containerLayout.addLayout(hero)

        # ---------- 第一块:余额 ----------
        self.balanceCard = BalanceCard(container)
        self.balanceCard.rechargeRequested.connect(self._onRecharge)
        containerLayout.addWidget(self.balanceCard)

        # ---------- 第二块:账单 + 设备(左右分栏) ----------
        midRow = QHBoxLayout()
        midRow.setSpacing(18)

        billCard = _SectionCard(FIF.DOCUMENT, "账单流水", container)
        self.billTable = BillTableWidget(container)
        self.billTable.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        billCard.contentLayout.addWidget(self.billTable)
        billCard.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        deviceCard = _SectionCard(FIF.HEART, "本机信息", container)
        self.devicePanel = DevicePanel(container)
        deviceCard.contentLayout.addWidget(self.devicePanel)
        # 修复(2026-08-05):不让 deviceCard 固定宽度,而是让 DevicePanel 在卡片内
        # 自适应宽度。强制 320px 会让卡片内部 hover/折叠区布局紧张,
        # 改用 Preferred 即可。stretch 仍为 4,让账单占 5/9、设备占 4/9。
        deviceCard.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        deviceCard.setMinimumWidth(280)

        midRow.addWidget(billCard, stretch=5)
        midRow.addWidget(deviceCard, stretch=4)
        containerLayout.addLayout(midRow)

        # ---------- 第三块:操作(反馈 / 注销 / 充值) ----------
        actionCard = _SectionCard(FIF.SETTING, "操作", container)
        actionRow = QHBoxLayout()
        actionRow.setSpacing(12)
        actionRow.setContentsMargins(0, 0, 0, 0)

        self.rechargeActionBtn = PushButton("输入充值码", actionCard, FIF.SHOPPING_CART)
        self.rechargeActionBtn.clicked.connect(self._onRecharge)
        actionRow.addWidget(self.rechargeActionBtn)

        self.feedbackBtn = PushButton("提交内测反馈", actionCard, FIF.HEART)
        self.feedbackBtn.clicked.connect(self._onFeedback)
        actionRow.addWidget(self.feedbackBtn)

        self.logoutBtn = PushButton("注销本地凭证", actionCard, FIF.CANCEL)
        self.logoutBtn.clicked.connect(self._onLogout)
        actionRow.addWidget(self.logoutBtn)

        actionRow.addStretch(1)
        actionCard.contentLayout.addLayout(actionRow)

        # 注销前给个轻微警告说明
        warnLabel = CaptionLabel(
            "⚠ 注销后会清空本地 license.enc 与账户," "需要重新输入凭证才能继续使用。",
            actionCard,
        )
        warnLabel.setStyleSheet("color: #b67c2b;")
        actionCard.contentLayout.addWidget(warnLabel)

        containerLayout.addWidget(actionCard)

        containerLayout.addStretch(1)
        scroll.setWidget(container)

        # 信号
        signalBus.activationStatusChanged.connect(self._onAuthChanged)
        signalBus.licenseCorrupted.connect(self._onLicenseCorrupted)
        signalBus.sessionExpired.connect(self._onSessionExpired)
        auth = getAuthService()
        if auth.currentUserId():
            self._setUser(auth.currentUserId())
            # 修复(2026-08-05 F4):启动后从云端拉取最新账户信息,刷新余额/账单。
            # 网络失败不弹错(账户页本地有缓存可看),仅记日志。
            try:
                from app.core.services.billing_service import getBillingService

                getBillingService().refreshUserFromCloud(auth.currentUserId())
            except Exception:
                logger.warning("[AccountInterface] 云端拉取账户信息失败,使用本地缓存")
            # 刷新账单列表
            try:
                # 直接调 listBills → setUserId 会触发 refresh
                self.billTable.setUserId(auth.currentUserId())
                self.billTable.refresh()
            except Exception:
                logger.warning("[AccountInterface] 云端拉取账单失败")
        else:
            # 修复(2026-08-05):未激活时仍允许点击「重新激活」,
            # 让用户能从账户中心直接进入激活流程,无需重启走启动门。
            self._setUser(None)

    # ---------- 槽 ----------
    def _onAuthChanged(self, activated: bool) -> None:
        auth = getAuthService()
        self._setUser(auth.currentUserId() if activated else None)

    def _setUser(self, userId: Optional[str]) -> None:
        self.balanceCard.setUserId(userId or "")
        self.billTable.setUserId(userId or "")
        # 操作按钮根据鉴权状态启用
        isAuth = bool(userId)
        self.rechargeActionBtn.setEnabled(isAuth)
        self.logoutBtn.setEnabled(isAuth)
        # 修复(2026-08-05):重新激活按钮在未激活状态下也必须可用,
        # 让用户无需重启即可进入激活流程。其他按钮(充值/注销)保持原行为。
        self.reactivateBtn.setEnabled(True)

    def _onLicenseCorrupted(self, reason: str) -> None:
        """凭证损坏时显示顶部红色横幅。"""
        msg = (
            "⚠ 检测到凭证文件已损坏,已自动备份。"
            f"原因:{reason}。请点击右上角「重新激活」重新输入凭证。"
        )
        self.corruptBanner.setText(msg)
        self.corruptBanner.show()

    def _onSessionExpired(self, reason: str) -> None:
        """会话失效(refresh token 过期)时显示橙色横幅,引导重新激活。

        2026-08-05 F5:云端 /v1/auth/refresh 也 401 时触发,
        之前只写 WARNING 日志,用户感知不到。
        """
        msg = (
            f"⚠ {reason}。"
            "请点击右上角「重新激活」重新输入凭证后再使用。"
        )
        self.sessionBanner.setText(msg)
        self.sessionBanner.show()

    def _onReactivate(self) -> None:
        """重新激活入口:弹 LoginDialog(reentryMode=True)。"""
        auth = getAuthService()
        # 若当前已激活,先二次确认
        if auth.isAuthenticated():
            confirm = MessageBox(
                "确认重新激活",
                "重新激活将清空当前本地凭证与账户记录。\n"
                "已下载的语料与历史账单不会被删除。",
                self,
            )
            if not confirm.exec():
                return
            auth.deactivate()
        else:
            # 修复(2026-08-05):过期凭证也算 _currentLicense is not None,
            # 弹一个温和提示,让用户知道当前是过期状态需要重新激活。
            lic = auth.currentLicense()
            if lic is not None:
                InfoBar.warning(
                    title="凭证已过期",
                    content="本地凭证已过期,请输入新凭证重新激活",
                    parent=self,
                    duration=3000,
                    position=InfoBarPosition.TOP,
                )
        # 弹出 reentry 模式的启动门
        dialog = LoginDialog(parent=self, reentryMode=True)
        # 修复(2026-08-05):接住 exec() 返回值(int,1=激活成功 / 0=取消或失败),
        # 用于在重新激活成功后给用户明确反馈 InfoBar。
        result = dialog.exec()
        # 弹窗关闭后刷新用户状态
        self._setUser(getAuthService().currentUserId())
        if result == 1:
            InfoBar.success(
                title="重新激活成功",
                content="凭证已更新,账户页已自动刷新",
                parent=self,
                duration=2500,
                position=InfoBarPosition.TOP,
            )

    def _onRecharge(self) -> None:
        if not getAuthService().isAuthenticated():
            InfoBar.warning(
                title="请先激活",
                content="使用充值码前需要先激活(邀请码/体验码)",
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )
            return
        dialog = RechargeDialog(self)
        dialog.exec()

    def _onFeedback(self) -> None:
        from app.view.widgets.billing.feedback_entry import FeedbackDialog

        dialog = FeedbackDialog(self)
        dialog.exec()

    def _onLogout(self) -> None:
        from qfluentwidgets import MessageBox

        if not getAuthService().isAuthenticated():
            return
        confirm = MessageBox(
            "确认注销",
            "注销后将清空本地 license.enc 和账户记录,需要重新输入凭证。\n"
            "已下载的语料与历史账单不会被删除。",
            self,
        )
        if confirm.exec():
            getAuthService().deactivate()
            InfoBar.success(
                title="已注销",
                content="本地凭证已清除,重新激活后可继续使用",
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )
