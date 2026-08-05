# coding: utf-8
"""AuthGateway 单测(2026-08-05 T8)

覆盖:
    - redeem 成功 → 返回 success + license
    - redeem 失败(ENVELOPE ERROR)→ 返回 success=False + 透传 code/message
    - redeem 网络异常 → success=False + NETWORK_ERROR
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.services.auth_gateway import AuthGateway, resetAuthGatewayForTesting
from app.core.services.cloud_api import CloudApiError


@pytest.fixture(autouse=True)
def _reset():
    resetAuthGatewayForTesting()
    yield
    resetAuthGatewayForTesting()


def test_redeem_success_returns_license():
    api = MagicMock()
    api.redeem.return_value = {
        "mode": "invite",
        "user": {
            "userId": "user-uuid-1",
            "displayName": "测试用户",
            "tier": "beta",
            "createdAt": "2026-08-05T00:00:00Z",
        },
        "balance": {
            "balance": 200,
            "frozenBalance": 0,
            "totalSpent": 0,
            "totalRecharged": 200,
        },
        "tokens": {
            "accessToken": "a1",
            "refreshToken": "r1",
            "expiresIn": 3600,
        },
    }
    gw = AuthGateway(api=api)
    result = gw.redeem(rawCode="INV-XXXX-XXXX-XXXX-XXXX")
    assert result.success is True
    assert result.code == "OK"
    assert result.userId == "user-uuid-1"
    assert result.grantedBalance == 200
    assert "激活成功" in result.message


def test_redeem_invalid_code_passes_through():
    api = MagicMock()
    api.redeem.side_effect = CloudApiError(
        code="INVALID_CODE", message="码无效或已损坏", httpStatus=400
    )
    gw = AuthGateway(api=api)
    result = gw.redeem(rawCode="BAD-CODE")
    assert result.success is False
    assert result.code == "INVALID_CODE"
    assert "码无效" in result.message


def test_redeem_already_used_passes_through():
    api = MagicMock()
    api.redeem.side_effect = CloudApiError(
        code="ALREADY_USED", message="该凭证已被使用", httpStatus=409
    )
    gw = AuthGateway(api=api)
    result = gw.redeem(rawCode="INV-XXX-XXX")
    assert result.success is False
    assert result.code == "ALREADY_USED"


def test_redeem_network_error():
    api = MagicMock()
    api.redeem.side_effect = ConnectionError("云端不可达")
    gw = AuthGateway(api=api)
    result = gw.redeem(rawCode="INV-XXX")
    assert result.success is False
    assert result.code == "NETWORK_ERROR"


def test_redeem_empty_code_returns_invalid():
    api = MagicMock()
    gw = AuthGateway(api=api)
    result = gw.redeem(rawCode="")
    assert result.success is False
    assert result.code == "INVALID"
    # 不应该调云端
    api.redeem.assert_not_called()


def test_redeem_uses_cloud_expire_at():
    """云端权威:user.expireAt 必须原样进入 License(不再本地编造 365 天)。"""
    from datetime import datetime

    api = MagicMock()
    api.redeem.return_value = {
        "mode": "invite",
        "user": {
            "userId": "user-uuid-1",
            "displayName": "测试用户",
            "tier": "beta",
            "createdAt": "2026-08-05T00:00:00Z",
            "expireAt": "2026-12-31T23:59:59Z",
        },
        "balance": {
            "balance": 200,
            "frozenBalance": 0,
            "totalSpent": 0,
            "totalRecharged": 200,
        },
        "tokens": {
            "accessToken": "a1",
            "refreshToken": "r1",
            "expiresIn": 3600,
        },
    }
    gw = AuthGateway(api=api)
    result = gw.redeem(rawCode="INV-XXXX-XXXX-XXXX-XXXX")
    assert result.success is True
    expected = datetime(2026, 12, 31, 23, 59, 59)  # naive UTC
    assert result.license.expireAt == expected
    assert result.expireAt == expected


def test_recharge_success_path_in_billing_gateway_smoke():
    """顺便冒烟 BillingGateway.recharge 成功路径(供 PR 检查 import 可用)。"""
    from app.core.services.billing_gateway import BillingGateway

    api = MagicMock()
    api.redeem.return_value = {
        "mode": "recharge",
        "balance": {"balance": 50},
        "tokens": {},
    }
    gw = BillingGateway(api=api)
    res = gw.recharge(code="RCH-ABCD")
    # Redeem 是 INV/TRY/RCH 通用入口,recharge 模式由上层 Business 模块判断,
    # Gateway 层只是透传 demo;这里不深究语义,保证 import 不报错即可
    assert res is not None
