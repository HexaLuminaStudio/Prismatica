# coding: utf-8
"""BillingService 单元测试"""

from __future__ import annotations

import pytest

from app.core.models.billing_models import Account, ActionType, BillStatus
from app.core.services import account_db
from app.core.services.account_db import (
    registerRechargeCode,
    upsertAccount,
)
from app.core.services.billing_service import (
    InsufficientBalanceError,
    getBillingService,
)
from app.core.services.pricing_service import getPricingService


@pytest.fixture
def account():
    upsertAccount(
        Account(userId="u_bill", displayName="测试", tier="beta", balance=100)
    )
    return "u_bill"


def test_estimate_returns_positive(account):
    billing = getBillingService()
    cost = billing.estimate(ActionType.FREQ_ANALYZE, 5000)
    assert cost > 0


def test_preview_includes_balance(account):
    billing = getBillingService()
    preview = billing.preview(ActionType.FREQ_ANALYZE, 5000, account)
    assert preview.currentBalance == 100
    assert preview.affordable is True


def test_frozen_preauth_creates_bill_and_deducts(account):
    billing = getBillingService()
    result = billing.frozenPreauth(
        userId=account,
        action=ActionType.FREQ_ANALYZE,
        resourceUsed=5000,
        taskId="task_x",
    )
    assert result.success
    assert result.billId is not None
    acc = account_db.getAccount(account)
    assert acc.balance == 100 - result.estimatedCost
    assert acc.frozenBalance == result.estimatedCost


def test_settle_refunds_difference(account):
    billing = getBillingService()
    preauth = billing.frozenPreauth(
        userId=account,
        action=ActionType.FREQ_ANALYZE,
        resourceUsed=10_000,  # 估算 10 币
        taskId="task_y",
    )
    assert preauth.success
    # 实际只用了 1000 字 → 2 币
    bill = billing.settle(preauth.billId, realResourceUsed=1000)
    assert bill.status == BillStatus.SETTLED
    # 退还差额 8 币
    acc = account_db.getAccount(account)
    assert acc.balance == 100 - bill.realCost
    assert acc.frozenBalance == 0


def test_refund_restores_full_amount(account):
    billing = getBillingService()
    preauth = billing.frozenPreauth(
        userId=account,
        action=ActionType.FREQ_ANALYZE,
        resourceUsed=5000,
        taskId="task_z",
    )
    bill = billing.refund(preauth.billId, reason="test")
    assert bill.status == BillStatus.REFUNDED
    acc = account_db.getAccount(account)
    assert acc.balance == 100
    assert acc.frozenBalance == 0


def test_preauth_insufficient_balance(account):
    # 余额清零
    acc = account_db.getAccount(account)
    with account_db.connection() as conn:
        conn.execute("UPDATE accounts SET balance = 0 WHERE userId = ?", (account,))
    billing = getBillingService()
    result = billing.frozenPreauth(
        userId=account,
        action=ActionType.DEPENDENCY_PARSE,
        resourceUsed=50_000,
        taskId="task_q",
    )
    assert not result.success
    assert "余额不足" in result.message


def test_recharge_by_code_increases_balance(account):
    from datetime import datetime, timedelta

    registerRechargeCode(
        "RCH-TEST-RECHARGE-XX",
        amount=50,
        expireAt=datetime.utcnow() + timedelta(days=30),
    )
    billing = getBillingService()
    result = billing.rechargeByCode(
        userId=account, code="RCH-TEST-RECHARGE-XX", expectedAmount=50
    )
    assert result.success
    assert result.amount == 50
    acc = account_db.getAccount(account)
    assert acc.balance == 150
    # totalRecharged 累加本次充值的金额(初始 0 + 50)
    assert acc.totalRecharged == 50


def test_recharge_by_code_rejects_duplicate(account):
    from datetime import datetime, timedelta

    registerRechargeCode(
        "RCH-TEST-DUP-XX-XX",
        amount=20,
        expireAt=datetime.utcnow() + timedelta(days=30),
    )
    billing = getBillingService()
    r1 = billing.rechargeByCode(account, "RCH-TEST-DUP-XX-XX", expectedAmount=20)
    r2 = billing.rechargeByCode(account, "RCH-TEST-DUP-XX-XX", expectedAmount=20)
    assert r1.success
    assert not r2.success


def test_settle_after_settle_no_op(account):
    """已结算的账单重复 settle 不应再次扣费。"""
    billing = getBillingService()
    preauth = billing.frozenPreauth(
        userId=account,
        action=ActionType.WORD_CLOUD,
        resourceUsed=1,
        taskId="task_w",
    )
    bill1 = billing.settle(preauth.billId, realResourceUsed=1)
    bill2 = billing.settle(preauth.billId, realResourceUsed=1)
    assert bill1.status == BillStatus.SETTLED
    assert bill2.status == BillStatus.SETTLED
    # totalSpent 只应增加一次
    acc = account_db.getAccount(account)
    assert acc.totalSpent == bill1.realCost