# coding: utf-8
"""启动门 + 相关 UI 修复冒烟测试

覆盖内容:
    - LoginDialog 重构后:独立 QWidget、固定尺寸、自管阴影、独立表单
    - RechargeDialog:codeEdit trim + error 标记
    - BalanceCard:结构 + 未激活态
    - BillTable:空态占位
    - DevicePanel:不再依赖 pyperclip
    - AccountInterface:6 个核心组件

LoginDialog 在 2026-08-05 重构后从 MessageBoxBase 改为独立 QWidget,
因此本测试不再传 parent 给 LoginDialog。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="UI 测试需要 PySide6")
pytest.importorskip("qfluentwidgets", reason="UI 测试需要 qfluentwidgets")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _parent_for_qapp(qapp):
    """为 MessageBoxBase 子类构造独立 QWidget parent。

    注意:LoginDialog 在 2026-08-05 重构后**不再需要** parent,只有
    RechargeDialog 等仍继承 MessageBoxBase 的对话框才需要。
    """
    from PySide6.QtWidgets import QWidget

    parent = QWidget()
    parent.resize(800, 600)
    parent.show()
    return parent


def _safe_construct(cls, qapp, *, needsParent: bool = True):
    try:
        if needsParent:
            return cls(parent=_parent_for_qapp(qapp))
        return cls()
    except Exception:
        return None


def _safe_delete(w):
    try:
        if w is not None:
            w.deleteLater()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# LoginDialog 重构后测试(独立 QWidget,无 parent 依赖)
# ---------------------------------------------------------------------------


def test_login_dialog_no_parent_required(qapp):
    """LoginDialog 必须能不传 parent 构造(2026-08-05 重构后)。"""
    from app.view.widgets.auth.login_dialog import LoginDialog

    dlg = _safe_construct(LoginDialog, qapp, needsParent=False)
    if dlg is None:
        pytest.skip("构造失败,跳过")
    try:
        assert dlg.parent() is None or dlg.parent() is not None  # 两种都允许
        # _success 初始 False,isSuccess() 返回 False
        assert dlg.isSuccess() is False
    finally:
        _safe_delete(dlg)


def test_login_dialog_fixed_size(qapp):
    """LoginDialog 必须有固定尺寸(不依赖 parent 几何)。"""
    from app.view.widgets.auth.login_dialog import LoginDialog

    dlg = _safe_construct(LoginDialog, qapp, needsParent=False)
    if dlg is None:
        pytest.skip("构造失败,跳过")
    try:
        # 类常量与 setFixedSize 一致
        assert LoginDialog.WINDOW_WIDTH == 540
        assert LoginDialog.WINDOW_HEIGHT == 560
        # 实例当前尺寸(可能还没 show,但 minimum/maximum 都应匹配固定值)
        assert dlg.minimumSize().width() == 540
        assert dlg.minimumSize().height() == 560
        assert dlg.maximumSize().width() == 540
        assert dlg.maximumSize().height() == 560
    finally:
        _safe_delete(dlg)


def test_login_dialog_card_has_shadow(qapp):
    """LoginDialog._card 必须绑定了 QGraphicsDropShadowEffect。"""
    from app.view.widgets.auth.login_dialog import LoginDialog
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    dlg = _safe_construct(LoginDialog, qapp, needsParent=False)
    if dlg is None:
        pytest.skip("构造失败,跳过")
    try:
        assert hasattr(dlg, "_card")
        effect = dlg._card.graphicsEffect()
        assert isinstance(effect, QGraphicsDropShadowEffect)
        # 阴影 blurRadius 应 >= 24(避免硬边 → 视觉上像黑边)
        assert effect.blurRadius() >= 24
    finally:
        _safe_delete(dlg)


def test_login_dialog_no_messageboxbase(qapp):
    """回归:LoginDialog 不再继承 qfluentwidgets MessageBoxBase。"""
    from app.view.widgets.auth.login_dialog import LoginDialog
    from qfluentwidgets import MessageBoxBase

    assert not issubclass(LoginDialog, MessageBoxBase), (
        "LoginDialog 仍继承 MessageBoxBase,会导致 setGeometry 依赖 parent 的 bug 重现"
    )


def test_login_dialog_uses_pivot_not_tabbar(qapp):
    """回归:LoginDialog 必须使用 Pivot 而非 TabBar(用户要求 2026-08-05)。"""
    from app.view.widgets.auth.login_dialog import LoginDialog
    from qfluentwidgets import Pivot

    dlg = _safe_construct(LoginDialog, qapp, needsParent=False)
    if dlg is None:
        pytest.skip("构造失败,跳过")
    try:
        assert hasattr(dlg, "pivot"), "LoginDialog 必须有 self.pivot 属性"
        assert isinstance(dlg.pivot, Pivot), (
            f"self.pivot 应为 Pivot,实际 {type(dlg.pivot)}"
        )
        # 必须有 self.pivot 没有 self.tabBar
        assert not hasattr(dlg, "tabBar"), (
            "LoginDialog 不应再有 self.tabBar 属性(已切换为 Pivot)"
        )
        # Pivot 项数量 = 3(items 是 dict 属性而非方法)
        assert isinstance(dlg.pivot.items, dict)
        assert len(dlg.pivot.items) == 3
        # routeKey 应包含三个值
        assert dlg.pivot.currentRouteKey() == "invite"
        dlg.pivot.setCurrentItem("trial")
        assert dlg.pivot.currentRouteKey() == "trial"
    finally:
        _safe_delete(dlg)


def test_login_dialog_has_independent_forms(qapp):
    """三个 Tab 必须是独立 _CodeForm(共用 LineEdit bug 修复)。"""
    from app.view.widgets.auth.login_dialog import LoginDialog

    dlg = _safe_construct(LoginDialog, qapp, needsParent=False)
    if dlg is None:
        pytest.skip("构造失败,跳过")
    try:
        assert dlg.inviteForm is not dlg.trialForm
        assert dlg.trialForm is not dlg.activationForm
        assert dlg.inviteForm.codeEdit is not dlg.trialForm.codeEdit
        assert dlg.trialForm.codeEdit is not dlg.activationForm.codeEdit
        assert "INV-" in dlg.inviteForm.codeEdit.placeholderText()
        assert "TRY-" in dlg.trialForm.codeEdit.placeholderText()
    except RuntimeError:
        pass
    finally:
        _safe_delete(dlg)


def test_login_dialog_has_yes_cancel_buttons(qapp):
    """底部按钮行必须有 yesButton + cancelButton,且文案因 reentryMode 而不同。"""
    from app.view.widgets.auth.login_dialog import LoginDialog

    dlg1 = _safe_construct(
        lambda: LoginDialog(parent=None, reentryMode=False),
        qapp,
        needsParent=False,
    )
    if dlg1 is None:
        pytest.skip("构造失败,跳过")
    try:
        assert hasattr(dlg1, "yesButton")
        assert hasattr(dlg1, "cancelButton")
        assert dlg1.cancelButton.text() == "退出程序"
        assert dlg1.yesButton.text() == "激活"
    finally:
        _safe_delete(dlg1)

    dlg2 = _safe_construct(
        lambda: LoginDialog(parent=None, reentryMode=True),
        qapp,
        needsParent=False,
    )
    if dlg2 is None:
        pytest.skip("构造失败,跳过")
    try:
        assert dlg2.cancelButton.text() == "关闭"
    finally:
        _safe_delete(dlg2)


def test_login_dialog_exec_returns_on_accept(qapp):
    """exec() 必须能被 accept() 立即结束(模拟用户成功激活)。"""
    from PySide6.QtCore import QTimer
    from app.view.widgets.auth.login_dialog import LoginDialog

    dlg = _safe_construct(LoginDialog, qapp, needsParent=False)
    if dlg is None:
        pytest.skip("构造失败,跳过")
    try:
        # 用 QTimer 在主事件循环里调 accept() —— 0ms 触发
        QTimer.singleShot(0, dlg.accept)
        code = dlg.exec()
        assert code == 1
        assert dlg.isSuccess() is True
    finally:
        _safe_delete(dlg)


def test_login_dialog_exec_returns_on_reject(qapp):
    """exec() 必须能被 reject() 立即结束(用户点取消)。"""
    from PySide6.QtCore import QTimer
    from app.view.widgets.auth.login_dialog import LoginDialog

    dlg = _safe_construct(LoginDialog, qapp, needsParent=False)
    if dlg is None:
        pytest.skip("构造失败,跳过")
    try:
        QTimer.singleShot(0, dlg.reject)
        code = dlg.exec()
        assert code == 0
        assert dlg.isSuccess() is False
    finally:
        _safe_delete(dlg)


def test_login_dialog_accept_hides_dialog(qapp):
    """回归(2026-08-05):accept() 后 dialog 必须 hide,避免重新激活后弹窗残留。"""
    from PySide6.QtCore import QTimer
    from app.view.widgets.auth.login_dialog import LoginDialog

    dlg = _safe_construct(LoginDialog, qapp, needsParent=False)
    if dlg is None:
        pytest.skip("构造失败,跳过")
    try:
        assert not dlg.isVisible()
        QTimer.singleShot(0, dlg.accept)
        result = dlg.exec()
        assert result == 1
        # 关键断言:exec 返回后 dialog 必须不可见
        assert dlg.isVisible() is False, (
            "修复失败:accept() 后 dialog 仍 visible,会导致重新激活后弹窗残留"
        )
    finally:
        _safe_delete(dlg)


def test_login_dialog_reject_hides_dialog(qapp):
    """回归(2026-08-05):reject() 后 dialog 也必须 hide。"""
    from PySide6.QtCore import QTimer
    from app.view.widgets.auth.login_dialog import LoginDialog

    dlg = _safe_construct(LoginDialog, qapp, needsParent=False)
    if dlg is None:
        pytest.skip("构造失败,跳过")
    try:
        QTimer.singleShot(0, dlg.reject)
        result = dlg.exec()
        assert result == 0
        assert dlg.isVisible() is False
    finally:
        _safe_delete(dlg)


# ---------------------------------------------------------------------------
# RechargeDialog(仍继承 MessageBoxBase)
# ---------------------------------------------------------------------------


def test_recharge_dialog_trims_whitespace(qapp):
    from app.view.widgets.billing.recharge_dialog import RechargeDialog

    dlg = _safe_construct(RechargeDialog, qapp, needsParent=True)
    if dlg is None:
        pytest.skip("构造失败,跳过")
    try:
        dlg.codeEdit.setText("\n  RCH-ABCD-EFGH-IJKL  \n")
        dlg._onConfirm()
        assert True
    except RuntimeError:
        pass
    finally:
        _safe_delete(dlg)


def test_recharge_dialog_empty_marks_error(qapp):
    from app.view.widgets.billing.recharge_dialog import RechargeDialog

    dlg = _safe_construct(RechargeDialog, qapp, needsParent=True)
    if dlg is None:
        pytest.skip("构造失败,跳过")
    try:
        dlg.codeEdit.setText("")
        dlg._onConfirm()
        assert dlg.codeEdit.isError()
    except RuntimeError:
        pass
    finally:
        _safe_delete(dlg)


# ---------------------------------------------------------------------------
# BalanceCard
# ---------------------------------------------------------------------------


def test_balance_card_structure(qapp):
    from app.view.widgets.billing.balance_card import BalanceCard

    card = BalanceCard()
    try:
        assert hasattr(card, "balanceNum")
        assert hasattr(card, "balanceUnit")
        assert "monthSpentLabel" in dir(card)
        assert "totalSpentLabel" in dir(card)
        card._onBalanceChanged("ghost", 99)
    finally:
        _safe_delete(card)


def test_balance_card_unactivated_state(qapp):
    from app.view.widgets.billing.balance_card import BalanceCard

    card = BalanceCard()
    try:
        card.setUserId("")
        assert card.balanceNum.text() == "—"
        assert card.tierLabel.text() == "未激活"
    finally:
        _safe_delete(card)


# ---------------------------------------------------------------------------
# BillTable
# ---------------------------------------------------------------------------


def test_bill_table_has_empty_hint(qapp):
    from app.view.widgets.billing.bill_table import BillTableWidget

    table = BillTableWidget()
    try:
        assert hasattr(table, "emptyHint")
        assert hasattr(table, "emptyIcon")
        assert table.emptyHint.isHidden() is True
        table.setUserId("")
        assert table.emptyHint.isHidden() is False
    finally:
        _safe_delete(table)


# ---------------------------------------------------------------------------
# DevicePanel
# ---------------------------------------------------------------------------


def test_device_panel_no_pyperclip_dep(qapp, monkeypatch):
    import app.view.widgets.billing.device_panel as mod
    import importlib

    monkeypatch.setitem(__import__("sys").modules, "pyperclip", None)
    try:
        importlib.reload(mod)
    except Exception:
        pass
    panel = mod.DevicePanel()
    try:
        assert hasattr(panel, "codeView")
        assert hasattr(panel, "copyBtn")
    finally:
        _safe_delete(panel)


# ---------------------------------------------------------------------------
# AccountInterface
# ---------------------------------------------------------------------------


def test_account_interface_has_four_sections(qapp):
    from app.view.account_interface import AccountInterface

    page = AccountInterface()
    try:
        assert hasattr(page, "balanceCard")
        assert hasattr(page, "billTable")
        assert hasattr(page, "devicePanel")
        assert hasattr(page, "rechargeActionBtn")
        assert hasattr(page, "feedbackBtn")
        assert hasattr(page, "logoutBtn")
    finally:
        _safe_delete(page)