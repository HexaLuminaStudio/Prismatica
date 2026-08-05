# coding: utf-8
"""AuthService 单元测试"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.core.services import account_db
from app.core.services.account_db import consumeRechargeCode, registerRechargeCode, upsertAccount
from app.core.services.auth_service import AuthService, getAuthService
from app.core.services.billing_service import getBillingService
from app.core.utils.signed_code import makeInviteCode, makeRechargeCode, makeTrialCode


@pytest.fixture(autouse=True)
def reset_auth_singleton():
    """每个 case 重置 AuthService 单例(避免污染)。"""
    import app.core.services.auth_service as mod

    mod._authServiceInstance = None
    yield
    mod._authServiceInstance = None


def test_redeem_invite_creates_account():
    raw = makeInviteCode(grantedBalance=100, grantedDays=30)
    auth = getAuthService()
    result = auth.redeemCode(raw, displayName="张三")
    assert result.success
    assert auth.isAuthenticated()
    userId = auth.currentUserId()
    assert userId is not None

    # 余额应为赠送额
    balance = getBillingService().getBalance(userId)
    assert balance == 100


def test_redeem_trial_grants_smaller_balance():
    raw = makeTrialCode(grantedBalance=20, grantedDays=7)
    auth = getAuthService()
    result = auth.redeemCode(raw, displayName="体验用户")
    assert result.success
    assert getBillingService().getBalance(auth.currentUserId()) == 20


def test_recharge_code_requires_existing_account():
    raw = makeRechargeCode(amount=50)
    auth = getAuthService()
    # 没激活时直接充值应失败
    result = auth.redeemCode(raw)
    assert not result.success
    assert "请先激活" in result.message


def test_recharge_code_after_invite():
    inviteRaw = makeInviteCode(grantedBalance=100)
    rechargeRaw = makeRechargeCode(amount=50)
    auth = getAuthService()
    auth.redeemCode(inviteRaw)
    userId = auth.currentUserId()
    # 充值码去重表(本地)
    from app.core.services.account_db import getRechargeCode

    # 直接解码充值码,获取 amount 用于测试
    from app.core.utils.signed_code import parseSignedModel
    from app.core.models.auth_models import RechargeCode

    rcode, _ = parseSignedModel(rechargeRaw, RechargeCode)
    registerRechargeCode(rcode.code, rcode.amount, rcode.expireAt)

    result = auth.redeemCode(rechargeRaw)
    assert result.success
    assert result.grantedBalance == 50

    balance = getBillingService().getBalance(userId)
    assert balance == 150


def test_invalid_code_rejected():
    auth = getAuthService()
    result = auth.redeemCode("INV-NOT-EXIST-XX-XX-XX")
    assert not result.success
    assert not auth.isAuthenticated()


def test_double_activation_rejected():
    auth = getAuthService()
    raw1 = makeInviteCode()
    raw2 = makeInviteCode()
    r1 = auth.redeemCode(raw1)
    assert r1.success
    r2 = auth.redeemCode(raw2)
    assert not r2.success


def test_deactivate_clears_license(tmp_path, monkeypatch):
    # 重写 license.enc 路径
    import app.core.services.auth_service as mod
    import app.core.utils.setting as setting

    test_config = tmp_path / "config"
    test_config.mkdir()
    monkeypatch.setattr(setting, "CONFIG_FOLDER", test_config)
    mod._authServiceInstance = None

    auth = mod.AuthService(licenseFile=test_config / "license.enc")
    raw = makeInviteCode(grantedBalance=10)
    auth.redeemCode(raw)
    assert auth.isAuthenticated()
    auth.deactivate()
    assert not auth.isAuthenticated()