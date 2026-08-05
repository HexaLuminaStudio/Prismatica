# coding: utf-8
"""账户激活/凭证损坏/沙箱密钥相关测试

覆盖本次 plan 中的:
    - BUG-1: license.enc 损坏时不再静默,会备份 + emit 信号
    - BUG-2: 沙箱环境不再用固定 fallback 密钥
    - BUG-3: 充值码失败原因细分(EXPIRED / ALREADY_USED / INVALID / NEED_ACTIVATION)
    - BUG-4: 重复激活时 code = ALREADY_AUTHENTICATED
    - UI-2: AccountInterface 含 reentry 按钮
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core.models.auth_models import AuthMode, InviteCode, License, RedeemResult
from app.core.services import account_db
from app.core.services.account_db import (
    registerRechargeCode,
    upsertAccount,
)
from app.core.services.auth_service import AuthService
from app.core.utils.signal_bus import signalBus
from app.core.utils.signed_code import (
    makeInviteCode,
    makeRechargeCode,
    makeTrialCode,
)


@pytest.fixture(autouse=True)
def reset_singletons():
    """每个 case 重置 AuthService 单例,避免 license.enc 跨 case 污染。"""
    import app.core.services.auth_service as auth_mod

    auth_mod._authServiceInstance = None
    yield
    auth_mod._authServiceInstance = None


# ---------------------------------------------------------------------------
# BUG-1: license.enc 损坏时备份 + emit licenseCorrupted
# ---------------------------------------------------------------------------


def test_corrupt_license_emits_signal_and_backup(tmp_path, monkeypatch):
    """损坏的 license.enc 应被备份,并 emit licenseCorrupted 信号。"""
    import app.core.services.auth_service as auth_mod
    import app.core.utils.setting as setting

    test_config = tmp_path / "config"
    test_config.mkdir()
    monkeypatch.setattr(setting, "CONFIG_FOLDER", test_config)
    monkeypatch.setattr(auth_mod, "LICENSE_FILE", test_config / "license.enc")

    # 先正常激活拿到一份合法 license.enc
    auth1 = AuthService(licenseFile=test_config / "license.enc")
    raw = makeInviteCode(grantedBalance=10)
    r1 = auth1.redeemCode(raw)
    assert r1.success

    # 现在手工把 license.enc 改成垃圾内容
    (test_config / "license.enc").write_text("not-a-valid-ciphertext", encoding="utf-8")

    # 订阅信号
    received: list[str] = []
    signalBus.licenseCorrupted.connect(lambda reason: received.append(reason))

    # 重新构造 AuthService → 触发 _load
    auth_mod._authServiceInstance = None
    auth2 = AuthService(licenseFile=test_config / "license.enc")

    # 应当:currentLicense=None + emit 信号 + 产生 backup 文件
    assert auth2.currentLicense() is None
    assert len(received) >= 1, "licenseCorrupted 信号未被触发"
    backup_files = list(test_config.glob("license.enc.corrupt.*"))
    assert len(backup_files) >= 1, "损坏文件未被备份"


# ---------------------------------------------------------------------------
# BUG-2: 沙箱环境密钥派生不再固定
# ---------------------------------------------------------------------------


def test_sandbox_key_is_random_not_fixed(monkeypatch, tmp_path):
    """设备特征不可用时,使用 secrets.token_bytes,而不是固定 SHA-256 字符串。"""
    import app.core.services.auth_service as auth_mod

    # 与 conftest 的 configDir 保持一致:tmp_path/prismatica_test/config
    test_config = tmp_path / "prismatica_test" / "config"
    test_config.mkdir(parents=True, exist_ok=True)

    # 重置 device 单例 + 让 collectDeviceFeatures 抛 RuntimeError(模拟沙箱)
    import app.core.utils.device_id as dev

    dev._deviceIdentifier = None  # 重置单例
    monkeypatch.setattr(dev.DeviceIdentifier, "collectDeviceFeatures",
                        lambda self: (_ for _ in ()).throw(RuntimeError("沙箱环境")))

    # 直接调用 _deriveKey() —— 模拟「_load 失败进入沙箱 fallback」场景
    auth = auth_mod.AuthService(licenseFile=test_config / "license.enc")
    key = auth._deriveKey()

    # 必须:32 字节随机,且不等于固定 fallback
    assert len(key) == 32
    import hashlib

    fixed_fallback = hashlib.sha256(b"prismatica.auth.fallback.key").digest()
    assert key != fixed_fallback

    # 持久化文件应已生成
    sandbox_file = test_config / ".sandbox-key"
    assert sandbox_file.exists()


# ---------------------------------------------------------------------------
# BUG-3 + BUG-4: RedeemResult.code 字段
# ---------------------------------------------------------------------------


def test_redeem_invalid_code_returns_invalid():
    """无效凭证 → code='INVALID'。"""
    AuthService()
    result = AuthService.instance().redeemCode("INV-NOT-EXIST-XX-XX-XX")
    assert not result.success
    assert result.code == "INVALID"


def test_redeem_empty_code_returns_invalid():
    AuthService()
    result = AuthService.instance().redeemCode("")
    assert not result.success
    assert result.code == "INVALID"


def test_redeem_invite_when_already_authenticated_returns_already_code():
    """已有凭证时再邀请码 → code='ALREADY_AUTHENTICATED'(BUG-4 修复)。"""
    auth = AuthService()
    raw1 = makeInviteCode(grantedBalance=50)
    r1 = auth.redeemCode(raw1)
    assert r1.success

    raw2 = makeInviteCode(grantedBalance=30)
    r2 = auth.redeemCode(raw2)
    assert not r2.success
    assert r2.code == "ALREADY_AUTHENTICATED"


def test_redeem_trial_when_already_authenticated_returns_already_code():
    auth = AuthService()
    r1 = auth.redeemCode(makeInviteCode())
    assert r1.success
    r2 = auth.redeemCode(makeTrialCode())
    assert not r2.success
    assert r2.code == "ALREADY_AUTHENTICATED"


def test_redeem_recharge_when_not_authenticated_returns_invalid():
    """未激活时充值 → code='INVALID' (语义上是 NEED_ACTIVATION 的分支,但 redeemCode 走 tryParseAnyCode
    先校验,本测试用 garbage 触发)。"""
    AuthService()
    r = AuthService.instance().redeemCode("RCH-NOT-EXIST-XX-XX")
    assert not r.success
    assert r.code in ("INVALID", "NEED_ACTIVATION")


def test_redeem_recharge_already_used_returns_already_used():
    """重复消费同一充值码 → code='ALREADY_USED'。"""
    from datetime import datetime, timedelta

    auth = AuthService()
    auth.redeemCode(makeInviteCode(grantedBalance=100))
    rechargeRaw = makeRechargeCode(amount=50)
    from app.core.utils.signed_code import parseSignedModel
    from app.core.models.auth_models import RechargeCode

    rc, _ = parseSignedModel(rechargeRaw, RechargeCode)
    registerRechargeCode(rc.code, rc.amount, rc.expireAt)

    # 第一次成功
    r1 = auth.redeemCode(rechargeRaw)
    assert r1.success
    # 第二次 → ALREADY_USED
    r2 = auth.redeemCode(rechargeRaw)
    assert not r2.success
    assert r2.code == "ALREADY_USED"


def test_redeem_recharge_expired_returns_expired():
    """过期充值码 → code='EXPIRED'。"""
    from datetime import datetime, timedelta

    auth = AuthService()
    auth.redeemCode(makeInviteCode(grantedBalance=50))

    # 直接构造一个已过期的充值码
    from app.core.models.auth_models import RechargeCode
    from app.core.utils.signed_code import encodeSignedModel

    rc = RechargeCode(
        code="RCH-EXPIRED-CODE-XX",
        amount=10,
        expireAt=datetime.utcnow() - timedelta(days=1),
    )
    raw = encodeSignedModel(rc)
    r = auth.redeemCode(raw)
    assert not r.success
    assert r.code == "EXPIRED"


# ---------------------------------------------------------------------------
# UI-2: AccountInterface 结构
# ---------------------------------------------------------------------------


def test_redeem_result_default_code_is_ok():
    """RedeemResult 默认 code='OK'(Pydantic model 默认值)。"""
    r = RedeemResult(success=True, message="ok")
    assert r.code == "OK"


def test_signal_bus_has_license_corrupted():
    """signalBus 必须含 licenseCorrupted 信号(供 AccountInterface 订阅)。"""
    assert hasattr(signalBus, "licenseCorrupted")


# ---------------------------------------------------------------------------
# 修复(2026-08-05):账户中心重新激活可用 + 过期凭证可自动清理
# ---------------------------------------------------------------------------


def test_expired_license_allows_redeem_without_already_authenticated():
    """本地凭证已过期时,再兑换邀请码应允许覆盖,而不是返回 ALREADY_AUTHENTICATED。

    修复前:expireAt 已过期的 license 也会让 _activateFromInvite 进入 ALREADY 分支,
    导致账户中心的「重新激活」按钮弹窗后用户无法激活。
    修复后:_activateFromInvite 检测到过期凭证自动 deactivate,允许新邀请码激活。
    """
    from datetime import timedelta

    from app.core.models.auth_models import License

    auth = AuthService()

    # 先正常激活拿到一份合法凭证
    raw1 = makeInviteCode(grantedBalance=20, grantedDays=30)
    r1 = auth.redeemCode(raw1)
    assert r1.success
    oldUserId = auth.currentUserId()
    assert oldUserId is not None

    # 模拟过期:把 _currentLicense 的 expireAt 改成昨天
    lic = auth._currentLicense
    assert lic is not None
    # Pydantic v2 兼容:用 model_copy 构造新对象再赋值
    expired = lic.model_copy(update={"expireAt": datetime.utcnow() - timedelta(days=1)})
    auth._currentLicense = expired
    # 现在 isAuthenticated 应该返回 False
    assert not auth.isAuthenticated()

    # 重新兑换邀请码:应该成功(而不是返回 ALREADY_AUTHENTICATED)
    raw2 = makeInviteCode(grantedBalance=50, grantedDays=30)
    r2 = auth.redeemCode(raw2)
    assert r2.success, f"应允许过期后重激活,实际返回 {r2.code}: {r2.message}"
    assert r2.code == "OK"
    # 新用户 id 应该不同
    assert auth.currentUserId() != oldUserId


def test_valid_license_still_blocks_duplicate_activation():
    """本地凭证**未过期**时,再兑换邀请码仍应返回 ALREADY_AUTHENTICATED。

    验证修复未破坏原有「未过期凭证不允许覆盖」的保护。
    """
    auth = AuthService()
    raw1 = makeInviteCode(grantedBalance=20)
    r1 = auth.redeemCode(raw1)
    assert r1.success

    raw2 = makeInviteCode(grantedBalance=30)
    r2 = auth.redeemCode(raw2)
    assert not r2.success
    assert r2.code == "ALREADY_AUTHENTICATED"


@pytest.mark.skipif(
    not pytest.importorskip("PySide6", reason="UI 测试需要 PySide6"),
    reason="需要 PySide6",
)
def test_account_interface_reactivate_btn_enabled_when_unauthenticated():
    """未激活状态下,AccountInterface 的「重新激活」按钮必须可用(用于进入激活流程)。"""
    pytest.importorskip("qfluentwidgets", reason="UI 测试需要 qfluentwidgets")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from app.view.account_interface import AccountInterface

    page = AccountInterface()
    try:
        # 关键断言:即使 _currentUserId() 为 None,reactivateBtn 必须可用
        assert page.reactivateBtn.isEnabled(), (
            "修复前 bug:未激活状态下「重新激活」按钮被禁用,导致用户无法激活"
        )
    finally:
        page.deleteLater()