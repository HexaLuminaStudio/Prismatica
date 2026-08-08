"""P0-A 桌面端账户相关 widget 烟雾测试。

不真正打开窗口(会卡住),只检查 widget 能否被构造。
"""
from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget

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
        def register(self, email: str, password: str, displayName: str, rememberMe: bool = True) -> None:
            calls.append((email, password, displayName, rememberMe))

    monkeypatch.setattr(login_dialog, "getCloudAuth", lambda: _AuthStub())
    page = login_dialog.LoginInterface()
    page._baseUrl = "https://example.invalid"
    page._rememberCheck.setChecked(False)
    page._switchTab(1, animate=False)
    page._regEmailEdit.setText("reader@example.com")
    page._regPasswordEdit.setText("StrongPass123!")
    page._regConfirmEdit.setText("StrongPass123!")
    page._agreementCheck.setChecked(True)

    page._onRegister()

    assert calls == [("reader@example.com", "StrongPass123!", "", False)]


def test_login_remember_choice_is_forwarded(monkeypatch) -> None:
    from app.view.widgets.account import login_dialog

    calls = []

    class _AuthStub:
        def login(self, email: str, password: str, rememberMe: bool = True) -> None:
            calls.append((email, password, rememberMe))

    monkeypatch.setattr(login_dialog, "getCloudAuth", lambda: _AuthStub())
    page = login_dialog.LoginInterface()
    page._baseUrl = "https://example.invalid"
    page._rememberCheck.setChecked(True)
    page._refreshOfflineState()
    page._loginEmailEdit.setText("reader@example.com")
    page._loginPasswordEdit.setText("StrongPass123!")
    page._onLogin()

    assert calls == [("reader@example.com", "StrongPass123!", True)]


def test_login_button_resets_after_successful_login(monkeypatch) -> None:
    from app.view.widgets.account import login_dialog

    class _AuthStub:
        def login(self, email: str, password: str, rememberMe: bool = True) -> None:
            pass

    monkeypatch.setattr(login_dialog, "getCloudAuth", lambda: _AuthStub())
    page = login_dialog.LoginInterface()
    page._baseUrl = "https://example.invalid"
    page._refreshOfflineState()
    page._loginEmailEdit.setText("reader@example.com")
    page._loginPasswordEdit.setText("StrongPass123!")

    signals = []
    page.loginSucceeded.connect(lambda: signals.append("ok"))
    page._onLogin()

    assert signals == ["ok"]
    assert page._loginBtn.text() == "登录"
    assert page._loginBtn.isEnabled()


def test_register_button_resets_after_successful_register(monkeypatch) -> None:
    from app.view.widgets.account import login_dialog

    class _AuthStub:
        def register(self, email: str, password: str, displayName: str, rememberMe: bool = True) -> None:
            return None

    monkeypatch.setattr(login_dialog, "getCloudAuth", lambda: _AuthStub())
    page = login_dialog.LoginInterface()
    page._baseUrl = "https://example.invalid"
    page._refreshOfflineState()
    page._switchTab(1, animate=False)
    page._regEmailEdit.setText("reader@example.com")
    page._regPasswordEdit.setText("StrongPass123!")
    page._regConfirmEdit.setText("StrongPass123!")
    page._agreementCheck.setChecked(True)

    signals = []
    page.loginSucceeded.connect(lambda: signals.append("ok"))
    page._onRegister()

    assert signals == ["ok"]
    assert page._registerBtn.text() == "创建账号并登录"
    assert page._registerBtn.isEnabled()


def test_show_event_restores_action_buttons() -> None:
    from app.view.widgets.account.login_dialog import LoginInterface

    page = LoginInterface()
    page._baseUrl = "https://example.invalid"
    page._refreshOfflineState()
    page._loginBtn.setEnabled(False)
    page._loginBtn.setText("登录中…")
    page._registerBtn.setEnabled(False)
    page._registerBtn.setText("创建中…")

    page.show()

    assert page._loginBtn.text() == "登录"
    assert page._loginBtn.isEnabled()
    assert page._registerBtn.text() == "创建账号并登录"
    assert page._registerBtn.isEnabled()


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
    assert nav.isSelected() is False
    nav.setSelected(True)
    assert nav.isSelected() is True
    nav.setSelected(False)
    assert nav.isSelected() is False
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


def test_account_interface_is_a_standalone_four_tab_page() -> None:
    from app.view.account_interface import AccountInterface

    page = AccountInterface()
    assert page.objectName() == "accountInterface"
    assert not isinstance(page, QDialog)
    assert page._stack.count() == 4
    assert page._pivot.currentRouteKey() == "overview"


def test_account_overview_refresh_uses_background_task(monkeypatch) -> None:
    from app.view import account_interface

    dispatched = {}

    def capture(operation, onSuccess, onFailure) -> None:
        dispatched.update(
            operation=operation,
            onSuccess=onSuccess,
            onFailure=onFailure,
        )

    monkeypatch.setattr(account_interface, "_runAccountTask", capture)
    page = account_interface._OverviewPage()
    page.refresh()

    assert page._loading is True
    assert not page._refreshButton.isEnabled()
    assert set(dispatched) == {"operation", "onSuccess", "onFailure"}

    dispatched["onSuccess"](
        (
            {"balance": 8420, "reserved": 120, "tier": "pro"},
            {"activeCount": 2, "maxActive": 3},
        )
    )
    assert page._loading is False
    assert page._refreshButton.isEnabled()
    assert page._balanceLabel.text() == "8,420"
    assert page._devicesHint.text() == "已激活 2 / 3 台"


def test_change_password_requires_matching_confirmation(monkeypatch) -> None:
    from app.view import account_interface

    answers = iter(
        [
            ("OldPassword123", True),
            ("NewPassword123", True),
            ("DifferentPassword123", True),
        ]
    )
    messages = []

    class _Message:
        def __init__(self, title: str, content: str, _parent) -> None:
            messages.append((title, content))

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(
        account_interface.QInputDialog,
        "getText",
        staticmethod(lambda *_args, **_kwargs: next(answers)),
    )
    monkeypatch.setattr(account_interface, "MessageBox", _Message)
    monkeypatch.setattr(
        account_interface,
        "_runAccountTask",
        lambda *_args: pytest.fail("密码不一致时不应提交网络请求"),
    )

    page = account_interface._OverviewPage()
    page._onChangePassword()

    assert messages == [("两次输入不一致", "请重新输入并确认新密码。")]


def test_device_revoke_is_locked_until_background_request_finishes(monkeypatch) -> None:
    from app.view import account_interface

    dispatched = []
    monkeypatch.setattr(
        account_interface.QMessageBox,
        "question",
        staticmethod(lambda *_args, **_kwargs: account_interface.QMessageBox.Yes),
    )
    monkeypatch.setattr(
        account_interface,
        "_runAccountTask",
        lambda operation, onSuccess, onFailure: dispatched.append(
            (operation, onSuccess, onFailure)
        ),
    )

    page = account_interface._DevicesPage()
    page._revokeDevice(7)
    page._revokeDevice(8)

    assert page._revokeInProgress is True
    assert len(dispatched) == 1
    dispatched[0][2]("网络不可用")
    assert page._revokeInProgress is False
    assert "网络不可用" in page._hint.text()


def test_logout_uses_background_task_and_recovers_button(monkeypatch) -> None:
    from app.view import account_interface

    dispatched = {}
    emitted = []
    monkeypatch.setattr(
        account_interface.QMessageBox,
        "question",
        staticmethod(lambda *_args, **_kwargs: account_interface.QMessageBox.Yes),
    )
    monkeypatch.setattr(
        account_interface,
        "_runAccountTask",
        lambda operation, onSuccess, onFailure: dispatched.update(
            operation=operation,
            onSuccess=onSuccess,
            onFailure=onFailure,
        ),
    )

    page = account_interface.AccountInterface()
    page.loggedOut.connect(lambda: emitted.append(True))
    page._onLogout()

    assert not page._logoutButton.isEnabled()
    assert page._logoutButton.text() == "正在退出…"
    dispatched["onSuccess"](None)
    assert page._logoutButton.isEnabled()
    assert page._logoutButton.text() == "退出登录"
    assert emitted == [True]
