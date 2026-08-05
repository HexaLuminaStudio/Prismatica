# coding: utf-8
"""@charged 装饰器集成测试"""

from __future__ import annotations

import pytest

from app.core.models.auth_models import AuthMode, License, UserTier
from app.core.models.billing_models import Account, ActionType, BillStatus
from app.core.services import account_db
from app.core.services.account_db import upsertAccount
from app.core.services.auth_service import AuthService
from app.core.services.billing_service import getBillingService
from app.core.services.decorators import charged
from app.core.utils.signed_code import makeInviteCode


class FakeService:
    """承载被 @charged 装饰方法的伪 service。"""

    def __init__(self):
        self.callCount = 0

    @charged(ActionType.FREQ_ANALYZE, resourceFrom="arg.resource", confirmRequired=False)
    def runWithArg(self, resource: int):
        self.callCount += 1
        return {"ok": True, "charCount": resource}

    @charged(ActionType.WORD_CLOUD, resourceFrom="result.charCount", confirmRequired=False)
    def runWithResult(self):
        self.callCount += 1
        class _R:
            charCount = 5000
        return _R()

    @charged(ActionType.KWIC_SEARCH, resourceFrom="arg.resource", confirmRequired=False)
    def runFailing(self, resource: int):
        self.callCount += 1
        raise RuntimeError("业务执行失败")


class _StubAuthService:
    """可注入当前 userId 的 AuthService stub。"""

    def __init__(self, userId):
        self._userId = userId

    def currentUserId(self):
        return self._userId


@pytest.fixture
def activated_user(monkeypatch):
    """创建本地账户并通过 monkeypatch 把 AuthService 替换为 stub。"""
    upsertAccount(
        Account(userId="u_dec", displayName="测试", tier="beta", balance=200)
    )
    # 关键:装饰器内部用 `from .auth_service import getAuthService`,
    # 该模块在 decorator 加载时已绑定。monkeypatch 必须改模块里的引用。
    import app.core.services.auth_service as auth_mod
    import app.core.services.decorators as dec_mod

    stub = _StubAuthService("u_dec")
    monkeypatch.setattr(auth_mod, "getAuthService", lambda: stub)
    # decorator 模块顶部已 `from app.core.services.auth_service import getAuthService`,
    # 也需要 patch 装饰器模块里的引用。
    monkeypatch.setattr(dec_mod, "getAuthService", lambda: stub)
    return "u_dec"


@pytest.fixture
def anonymous_user(monkeypatch):
    """未鉴权场景。"""
    import app.core.services.auth_service as auth_mod
    import app.core.services.decorators as dec_mod

    stub = _StubAuthService(None)
    monkeypatch.setattr(auth_mod, "getAuthService", lambda: stub)
    monkeypatch.setattr(dec_mod, "getAuthService", lambda: stub)
    return None


def test_charged_runs_and_settles(activated_user):
    svc = FakeService()
    result = svc.runWithArg(resource=5000)
    assert result["ok"] is True
    assert svc.callCount == 1

    # 扣费应已结算
    bills = getBillingService().listBills("u_dec", limit=10)
    assert len(bills) >= 1
    bill = bills[0]
    assert bill.status == BillStatus.SETTLED
    assert bill.actionType == ActionType.FREQ_ANALYZE


def test_charged_extracts_from_result(activated_user):
    svc = FakeService()
    svc.runWithResult()
    bills = getBillingService().listBills("u_dec", limit=10)
    assert any(b.realCost > 0 for b in bills)


def test_charged_refunds_on_exception(activated_user):
    svc = FakeService()
    with pytest.raises(RuntimeError):
        svc.runFailing(resource=5000)

    # 余额应保持 200(全额退款)
    acc = account_db.getAccount("u_dec")
    assert acc.balance == 200
    assert acc.frozenBalance == 0

    # 账单存在且已退款
    bills = getBillingService().listBills("u_dec", limit=10)
    refundBills = [b for b in bills if b.status == BillStatus.REFUNDED]
    assert len(refundBills) >= 1


def test_charged_skips_when_not_authenticated(anonymous_user):
    """未激活时直接放行,不扣费。"""
    svc = FakeService()
    result = svc.runWithArg(resource=5000)
    assert result["ok"] is True
    # 不会产生账单
    bills = getBillingService().listBills("u_dec", limit=10)
    assert len(bills) == 0