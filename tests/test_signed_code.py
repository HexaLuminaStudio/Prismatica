# coding: utf-8
"""凭证签名/验签单元测试"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.core.models.auth_models import InviteCode, RechargeCode, TrialCode, UserTier
from app.core.utils.signed_code import (
    decodeSignedCode,
    encodeSignedModel,
    makeInviteCode,
    makeRechargeCode,
    makeTrialCode,
    parseSignedModel,
    signPayload,
    tryParseAnyCode,
    verifyPayload,
)


def test_sign_and_verify_roundtrip():
    payload = {"a": 1, "b": "中文测试", "c": [1, 2, 3]}
    sig = signPayload(payload)
    assert verifyPayload(payload, sig) is True
    # 改一个字符应失败
    tampered = dict(payload)
    tampered["a"] = 2
    assert verifyPayload(tampered, sig) is False


def test_invite_code_roundtrip():
    raw = makeInviteCode(
        maxUses=1, grantedBalance=100, grantedDays=30, tier="beta"
    )
    code, sig = parseSignedModel(raw, InviteCode)
    assert code.grantedBalance == 100
    assert code.tier == UserTier.BETA
    # 类型识别
    kind, model = tryParseAnyCode(raw)
    assert kind == "invite"
    assert isinstance(model, InviteCode)


def test_trial_code_roundtrip():
    raw = makeTrialCode(grantedBalance=20, grantedDays=7)
    code, sig = parseSignedModel(raw, TrialCode)
    assert code.grantedBalance == 20
    assert code.grantedDays == 7
    kind, _ = tryParseAnyCode(raw)
    assert kind == "trial"


def test_recharge_code_roundtrip():
    raw = makeRechargeCode(amount=50)
    code, sig = parseSignedModel(raw, RechargeCode)
    assert code.amount == 50
    kind, _ = tryParseAnyCode(raw)
    assert kind == "recharge"


def test_tampered_code_rejected():
    raw = makeInviteCode()
    # 篡改最后一字符
    tampered = raw[:-1] + ("A" if raw[-1] != "A" else "B")
    with pytest.raises(Exception):
        parseSignedModel(tampered, InviteCode)


def test_garbage_code_raises():
    with pytest.raises(Exception):
        tryParseAnyCode("not-a-base64-string")


def test_unknown_prefix_rejected():
    raw = makeRechargeCode(amount=10)
    # 把 RCH 改成 XXX,绕过前缀识别
    fake = "XXX" + raw[3:]
    with pytest.raises(Exception):
        tryParseAnyCode(fake)


def test_decode_signed_code_returns_dict():
    raw = makeInviteCode()
    data = decodeSignedCode(raw)
    assert isinstance(data, dict)
    assert "signature" in data
    assert "code" in data
    assert "grantedBalance" in data