"""P0-A 桌面端账户相关 widget 烟雾测试。

不真正打开窗口(会卡住),只检查 widget 能否被构造。
"""
from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

# 必须在 import qfluentwidgets 之前保证 QApplication 存在
_app = QApplication.instance() or QApplication(sys.argv)


def test_login_interface_can_be_constructed() -> None:
    from app.view.widgets.account.login_dialog import LoginInterface

    page = LoginInterface()
    assert page.objectName() == "accountAuthInterface"
    assert page._loginBtn is not None
    assert page._registerBtn is not None
    assert not hasattr(page, "_forgotBtn")
    assert page._stack.currentIndex() == 0
    assert (page._shell.width(), page._shell.height()) == (560, 660)


def test_login_interface_switches_to_complete_register_form() -> None:
    from app.view.widgets.account.login_dialog import LoginInterface

    page = LoginInterface()
    page._loginEmailEdit.setText("reader@example.com")
    page._switchTab(1, animate=False)

    assert page._stack.currentIndex() == 1
    assert (page._shell.width(), page._shell.height()) == (560, 660)
    assert page._regEmailEdit.text() == "reader@example.com"
    assert page._regConfirmEdit is not None
    assert page._passwordStrength is not None
    assert page._agreementCheck is not None
    assert not hasattr(page, "_regDisplayEdit")


def test_login_interface_hover_click_and_transition_keep_geometry_stable() -> None:
    from app.view.widgets.account.login_dialog import LoginInterface

    host = QWidget()
    host.resize(1000, 720)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    page = LoginInterface(host)
    page._baseUrl = "https://example.invalid"
    page._refreshOfflineState()
    layout.addWidget(page)
    host.show()
    QApplication.processEvents()

    baseline = page._shell.geometry()
    assert page._loginEmailEdit.height() == 40
    assert page._shell.graphicsEffect() is None
    assert page._stack.graphicsEffect() is None
    assert page._transitionOverlay.testAttribute(Qt.WA_TransparentForMouseEvents)

    for widget in (page._loginEmailEdit, page._loginBtn, page._toRegisterBtn):
        QTest.mouseMove(widget, QPoint(widget.width() // 2, widget.height() // 2))
        QTest.qWait(30)
        assert page._shell.geometry() == baseline

    host.move(140, 90)
    QTest.qWait(60)
    assert page._shell.geometry() == baseline

    QTest.mouseClick(page._loginEmailEdit, Qt.LeftButton)
    assert page._loginEmailEdit.hasFocus()
    QTest.keyClicks(page._loginEmailEdit, "reader@example.com")
    assert page._loginEmailEdit.text() == "reader@example.com"

    QTest.mouseClick(page._toRegisterBtn, Qt.LeftButton)
    QTest.qWait(200)
    assert page._stack.currentIndex() == 1
    assert page._shell.geometry() == baseline
    assert page._transitionOverlay.isHidden()
    host.close()


def test_register_form_validates_confirmation_before_request() -> None:
    from app.view.widgets.account.login_dialog import LoginInterface

    page = LoginInterface()
    page._baseUrl = "https://example.invalid"
    page._refreshOfflineState()
    page._switchTab(1, animate=False)
    page._regEmailEdit.setText("reader@example.com")
    page._regPasswordEdit.setText("StrongPass123")
    page._regConfirmEdit.setText("StrongPass456")
    page._agreementCheck.setChecked(True)

    page._onRegister()

    assert "不一致" in page._registerStatus.text()
    assert page._registerBtn.isEnabled()


def test_register_password_strength_updates_with_input() -> None:
    from app.view.widgets.account.login_dialog import LoginInterface

    page = LoginInterface()
    page._switchTab(1, animate=False)
    page._regPasswordEdit.setText("abc")
    weak_score = page._passwordStrength.value()
    page._regPasswordEdit.setText("StrongPass123!")

    assert page._passwordStrength.value() > weak_score
    assert page._strengthLabel.text() == "强"


def test_register_request_does_not_submit_display_name(monkeypatch) -> None:
    from app.view.widgets.account import login_dialog

    calls = []

    class _AuthStub:
        def register(self, email: str, password: str, display_name: str) -> None:
            calls.append((email, password, display_name))

    monkeypatch.setattr(login_dialog, "getCloudAuth", lambda: _AuthStub())
    page = login_dialog.LoginInterface()
    page._baseUrl = "https://example.invalid"
    page._switchTab(1, animate=False)
    page._regEmailEdit.setText("reader@example.com")
    page._regPasswordEdit.setText("StrongPass123!")
    page._regConfirmEdit.setText("StrongPass123!")
    page._agreementCheck.setChecked(True)

    page._onRegister()

    assert calls == [("reader@example.com", "StrongPass123!", "")]


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
