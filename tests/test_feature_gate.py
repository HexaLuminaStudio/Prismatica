"""P0-A FeatureGate 单元测试。"""
from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)


class _FakeAuth:
    """Fake CloudAuth:仅暴露 feature_gate 需要的 _api.isLoggedIn()。"""

    def __init__(self, loggedIn: bool) -> None:
        class _Api:
            def isLoggedIn(self_inner):
                return loggedIn

        self._api = _Api()


class _DummyApi:
    """替代 getCloudApi:暴露 isLoggedIn + get 行为。"""

    def __init__(self, loggedIn: bool = True, online: bool = True) -> None:
        self._loggedIn = loggedIn
        self._online = online

    def isLoggedIn(self) -> bool:
        return self._loggedIn

    def get(self, *args, **kw) -> None:
        from app.core.services.cloud_api import CloudApiError

        if not self._online:
            raise CloudApiError("NETWORK_ERROR", "offline")


@pytest.fixture()
def authLoggedOut(monkeypatch):
    """未登录状态。"""
    from app.core.services import cloud_auth
    from app.core.services import cloud_api
    from app.core.services import feature_gate

    monkeypatch.setattr(cloud_auth, "getCloudAuth", lambda: _FakeAuth(loggedIn=False))
    monkeypatch.setattr(feature_gate, "getCloudApi", lambda: _DummyApi(loggedIn=False))
    yield


@pytest.fixture()
def authLoggedInNoBalance(monkeypatch):
    """已登录但余额不足(estimate 时返回 affordable=False)。"""
    from app.core.services.cloud_api import CloudApiError
    from app.core.services import feature_gate

    # 必须 patch feature_gate 模块内部的 getCloudAuth / getCloudApi / getCloudBilling
    monkeypatch.setattr(feature_gate, "getCloudAuth", lambda: _FakeAuth(loggedIn=True))
    monkeypatch.setattr(feature_gate, "getCloudApi", lambda: _DummyApi(loggedIn=True, online=True))

    class _FakeBillingWithErr:
        def estimate(self, *args, **kw):
            return {
                "actionType": "ai_insight",
                "estimatedCost": 100,
                "currentBalance": 50,
                "balanceAfter": 0,
                "affordable": False,
            }

        def preauth(self, *args, **kw):
            raise CloudApiError(
                "INSUFFICIENT_BALANCE",
                "余额不足",
                details={"required": 100, "currentBalance": 50},
            )

    monkeypatch.setattr(feature_gate, "getCloudBilling", _FakeBillingWithErr)
    yield


# _FakeAuth 在 fixture 之后定义会被 pytest 早期 fixture 失败;已经在文件顶。


def test_require_feature_unauthenticated_returns_login_required(authLoggedOut) -> None:
    from app.core.services.feature_gate import getFeatureGate

    gate = getFeatureGate()
    result = gate.requireFeature("ai_insight", resourceUsed=1000)
    assert result.ok is False
    assert result.reason == "login_required"


def test_require_feature_insufficient_balance(authLoggedInNoBalance) -> None:
    from app.core.services.feature_gate import getFeatureGate

    gate = getFeatureGate()
    result = gate.requireFeature("ai_insight", resourceUsed=1000)
    assert result.ok is False
    assert result.reason == "insufficient_balance"


def test_handle_block_reason_emits_signal(monkeypatch) -> None:
    """handleBlockReason 触发 featureBlocked 信号。"""
    from app.core.services.feature_gate import GateResult, getFeatureGate

    gate = getFeatureGate()
    captured: list[tuple[str, str]] = []
    gate.featureBlocked.connect(lambda reason, msg: captured.append((reason, msg)))
    try:
        gate.handleBlockReason(GateResult(ok=False, reason="login_required", message="需要登录"))
    finally:
        gate.featureBlocked.disconnect()
    assert captured == [("login_required", "需要登录")]
