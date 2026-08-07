"""P0-A 桌面端账户相关 widget 烟雾测试。

不真正打开窗口(会卡住),只检查 widget 能否被构造。
"""
from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication

# 必须在 import qfluentwidgets 之前保证 QApplication 存在
_app = QApplication.instance() or QApplication(sys.argv)


def test_login_dialog_can_be_constructed() -> None:
    from app.view.widgets.account.login_dialog import LoginDialog

    dlg = LoginDialog()
    assert dlg.windowTitle() == "登录 Prismatica 账号"
    assert dlg._loginBtn is not None
    assert dlg._registerBtn is not None
    assert dlg._forgotBtn is not None


def test_redeem_dialog_can_be_constructed() -> None:
    from app.view.widgets.account.redeem_dialog import RedeemDialog

    dlg = RedeemDialog()
    assert dlg.windowTitle() == "兑换码"


def test_subscription_card_can_be_constructed() -> None:
    from app.view.widgets.account.subscription_card import SubscriptionCard

    card = SubscriptionCard(
        {
            "planCode": "pro_monthly",
            "status": "active",
            "currentPeriodStart": "2026-01-01T00:00:00",
            "currentPeriodEnd": "2026-01-31T00:00:00",
            "expiresAt": "2026-01-31T00:00:00",
            "autoRenew": True,
            "monthlyQuota": 200,
        }
    )
    assert card._sub["planCode"] == "pro_monthly"


def test_account_nav_widget_starts_as_logged_out() -> None:
    from app.view.widgets.account.account_nav import AccountNavWidget
    from qfluentwidgets import NavigationBar, NavigationItemPosition

    nav = AccountNavWidget()
    assert nav.isSelectable is False
    assert nav._loggedIn is False
    assert nav._emailLabel.text() == "未登录"
    assert nav._badge.isHidden()

    navigation = NavigationBar()
    navigation.addWidget(
        "account-nav-test",
        nav,
        position=NavigationItemPosition.BOTTOM,
    )
    nav.clicked.emit()
    assert navigation.currentItem() is None


def test_account_nav_widget_set_logged_in_updates_labels() -> None:
    from app.view.widgets.account.account_nav import AccountNavWidget
    from app.core.services import getCloudAuth

    auth = getCloudAuth()
    auth._api.setSession(
        __import__("app.core.services.cloud_api", fromlist=["CloudSession"]).CloudSession(
            accessToken="dummy",
            refreshToken="dummy",
            userId=1,
            email="alice@example.com",
            displayName="Alice",
            tier="pro",
        )
    )
    nav = AccountNavWidget()
    nav.setLoggedIn(True)
    assert nav._loggedIn is True
    assert "alice@example.com" in nav._emailLabel.text()
    nav.setBalance(100)
    assert "100" in nav._subLabel.text()
    assert nav._badge.isHidden()


def test_account_nav_widget_low_balance_shows_badge() -> None:
    from app.view.widgets.account.account_nav import AccountNavWidget
    from app.core.services import getCloudAuth

    auth = getCloudAuth()
    auth._api.setSession(
        __import__("app.core.services.cloud_api", fromlist=["CloudSession"]).CloudSession(
            accessToken="dummy",
            refreshToken="dummy",
            userId=1,
            email="bob@example.com",
            displayName="Bob",
            tier="free",
        )
    )
    nav = AccountNavWidget()
    nav.setLoggedIn(True)
    nav.setBalance(5)  # 余额 < 30
    assert not nav._badge.isHidden()


def test_account_panel_can_be_constructed() -> None:
    from app.view.widgets.account.account_panel import AccountPanel

    panel = AccountPanel()
    assert panel.windowTitle() == "我的账户"
    # 子页签已添加
    assert panel._stacks.count() == 3
