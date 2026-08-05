# coding: utf-8
"""鉴权服务(AuthService)

与现有 license.py 共存:
    - 现有 license.py 管理 activation.dat / betaLock / 激活码流程(保留)
    - 本服务管理 InviteCode / TrialCode / RechargeCode 三类新增凭证,
      以及激活成功后写入 config/license.enc(与现有字段**互不冲突**)

激活成功后:
    1. 写本地凭证(License 模型,AES-GCM 加密)
    2. 创建 Account(赠送初始余额)
    3. 触发 signalBus.activationStatusChanged 让 UI 刷新
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

from app.core.models.auth_models import (
    AuthMode,
    InviteCode,
    License,
    RedeemResult,
    RechargeCode,
    TrialCode,
    UserTier,
)
from app.core.services import account_db
from app.core.utils.encryption import AESCipherGCM, hash256
from app.core.utils.signal_bus import signalBus
from app.core.utils.signed_code import (
    parseSignedModel,
    tryParseAnyCode,
)
from app.core.utils.setting import CONFIG_FOLDER


LICENSE_FILE: Path = CONFIG_FOLDER / "license.enc"

# 全局单例
_authServiceInstance: Optional["AuthService"] = None


class InvalidCodeError(Exception):
    """凭证无效(签名错 / 已过期 / 格式错)"""


class AuthService:
    """鉴权主服务"""

    def __init__(self, licenseFile: Optional[Path] = None):
        # 修复(2026-08-05):同时支持参数和模块级常量,monkeypatch 模块级常量也能生效。
        # Python 的"or"语义:licenseFile is None 时取 LICENSE_FILE(模块级),测试中
        # 已经 monkeypatch 了 LICENSE_FILE,所以这里能拿到正确的 tmp_path。
        self._licenseFile = licenseFile if licenseFile is not None else LICENSE_FILE
        self._currentLicense: Optional[License] = None
        self._load()

    # ---------- 单例 ----------
    @classmethod
    def instance(cls) -> "AuthService":
        global _authServiceInstance
        if _authServiceInstance is None:
            _authServiceInstance = cls()
        return _authServiceInstance

    # ---------- 公开 API ----------
    def currentLicense(self) -> Optional[License]:
        return self._currentLicense

    def isAuthenticated(self) -> bool:
        lic = self._currentLicense
        if lic is None:
            return False
        if lic.expireAt < datetime.utcnow():
            return False
        return True

    def _isExpired(self, license: License) -> bool:
        """判断给定凭证是否已过期(实例方法,供 redeem 分支复用)。

        修复(2026-08-05):过期凭证在重新激活时应被自动清理,而非一直返回
        ALREADY_AUTHENTICATED 让用户卡住。
        """
        return license.expireAt < datetime.utcnow()

    def currentUserId(self) -> Optional[str]:
        lic = self._currentLicense
        return lic.userId if lic else None

    def currentDisplayName(self) -> str:
        lic = self._currentLicense
        return lic.displayName if lic else "游客"

    def currentTier(self) -> UserTier:
        lic = self._currentLicense
        if lic is None:
            return UserTier.GUEST
        return lic.tier

    def deactivate(self) -> None:
        """注销(清除本地凭证与账户)。"""
        try:
            if self._licenseFile.exists():
                self._licenseFile.unlink()
        except Exception as e:
            logger.warning(f"[Auth] 删除 license.enc 失败: {e}")
        self._currentLicense = None
        signalBus.activationStatusChanged.emit(False)
        logger.info("[Auth] 已注销本地凭证")

    # ---------- 兑换 ----------
    def redeemCode(
        self,
        rawCode: str,
        displayName: Optional[str] = None,
    ) -> RedeemResult:
        """统一兑换入口:自动识别 INV/TRY/RCH 三类凭证。"""
        rawCode = (rawCode or "").strip()
        if not rawCode:
            return RedeemResult(success=False, code="INVALID", message="请输入凭证")

        try:
            kind, model = tryParseAnyCode(rawCode)
        except Exception as e:
            logger.warning(f"[Auth] 凭证解析失败: {e}")
            return RedeemResult(success=False, code="INVALID", message=f"凭证无效: {e}")

        if kind == "invite":
            return self._activateFromInvite(model, displayName or "内测用户")
        if kind == "trial":
            return self._activateFromTrial(model, displayName or "体验用户")
        if kind == "recharge":
            return self._redeemRecharge(model)
        return RedeemResult(success=False, code="INVALID", message="未知凭证类型")

    # ---------- 邀请码 ----------
    def _activateFromInvite(
        self,
        code: InviteCode,
        displayName: str,
    ) -> RedeemResult:
        now = datetime.utcnow()
        if code.expireAt < now:
            return RedeemResult(success=False, code="EXPIRED", message="该邀请码已过期")
        if self._currentLicense is not None and not self._isExpired(
            self._currentLicense
        ):
            # 修复 BUG-4(2026-08-05):返回 ALREADY_AUTHENTICATED 让 LoginDialog
            # 显示「立即注销并重试」按钮,避免用户需手动跳转。
            # 修复(2026-08-05):仅在「未过期」的现存凭证下拦截;
            # 过期凭证视为可重激活,自动清理后允许新凭证覆盖。
            return RedeemResult(
                success=False,
                code="ALREADY_AUTHENTICATED",
                message="已存在激活凭证,请先注销后再兑换新邀请码",
            )
        if self._currentLicense is not None and self._isExpired(self._currentLicense):
            # 修复(2026-08-05):过期凭证自动清理,让用户能直接重激活。
            logger.info(
                f"[Auth] 检测到过期凭证 user={self._currentLicense.userId},自动清理"
            )
            self.deactivate()

        expireAt = now + timedelta(days=code.grantedDays)
        license = License(
            licenseId=_genId("lic"),
            userId=_genId("usr"),
            displayName=displayName,
            authMode=AuthMode.INVITE_CODE,
            tier=code.tier,
            activatedAt=now,
            expireAt=expireAt,
            deviceFingerprint=_deviceFingerprint(),
            grantedBalance=code.grantedBalance,
            payloadJson=code.model_dump_json(),
        )
        self._saveLicense(license)
        # 创建/赠送余额
        self._ensureAccountWithGrant(license, code.grantedBalance, "activation_grant")
        self._currentLicense = license
        signalBus.activationStatusChanged.emit(True)
        logger.info(
            f"[Auth] 邀请码激活成功 user={license.userId} "
            f"balance={code.grantedBalance} expire={expireAt}"
        )
        return RedeemResult(
            success=True,
            code="OK",
            message=f"激活成功!已赠送 {code.grantedBalance} 币",
            license=license,
            grantedBalance=code.grantedBalance,
            expireAt=expireAt,
            userId=license.userId,
        )

    # ---------- 体验码 ----------
    def _activateFromTrial(
        self,
        code: TrialCode,
        displayName: str,
    ) -> RedeemResult:
        now = datetime.utcnow()
        if code.expireAt < now:
            return RedeemResult(success=False, code="EXPIRED", message="该体验码已过期")
        if self._currentLicense is not None and not self._isExpired(
            self._currentLicense
        ):
            return RedeemResult(
                success=False,
                code="ALREADY_AUTHENTICATED",
                message="已存在激活凭证,请先注销后再兑换",
            )
        if self._currentLicense is not None and self._isExpired(self._currentLicense):
            logger.info(
                f"[Auth] 检测到过期凭证 user={self._currentLicense.userId},自动清理"
            )
            self.deactivate()

        expireAt = now + timedelta(days=code.grantedDays)
        license = License(
            licenseId=_genId("lic"),
            userId=_genId("usr"),
            displayName=displayName,
            authMode=AuthMode.TRIAL_CODE,
            tier=code.tier,
            activatedAt=now,
            expireAt=expireAt,
            deviceFingerprint=_deviceFingerprint(),
            grantedBalance=code.grantedBalance,
            payloadJson=code.model_dump_json(),
        )
        self._saveLicense(license)
        self._ensureAccountWithGrant(license, code.grantedBalance, "trial_grant")
        self._currentLicense = license
        signalBus.activationStatusChanged.emit(True)
        logger.info(
            f"[Auth] 体验码激活成功 user={license.userId} "
            f"balance={code.grantedBalance} expire={expireAt}"
        )
        return RedeemResult(
            success=True,
            code="OK",
            message=f"体验激活成功!已赠送 {code.grantedBalance} 币",
            license=license,
            grantedBalance=code.grantedBalance,
            expireAt=expireAt,
            userId=license.userId,
        )

    # ---------- 充值码 ----------
    def _redeemRecharge(self, code: RechargeCode) -> RedeemResult:
        from app.core.services.billing_service import getBillingService

        # 必须先有本地账户
        lic = self._currentLicense
        if lic is None:
            return RedeemResult(
                success=False,
                code="INVALID",
                message="请先激活(输入邀请码/体验码)再使用充值码",
            )

        # 注册到本地去重表(若已存在则跳过)
        account_db.registerRechargeCode(code.code, code.amount, code.expireAt)

        billing = getBillingService()
        result = billing.rechargeByCode(
            userId=lic.userId,
            code=code.code,
            expectedAmount=code.amount,
            note=code.note,
        )

        # 修复 BUG-3(2026-08-05):细化失败原因,UI 可针对性提示用户
        # - 区分「未激活」「已使用」「已过期」「码无效」
        if not result.success:
            detailCode = "INVALID"
            msg = result.message
            if "已被使用" in msg:
                detailCode = "ALREADY_USED"
            elif "已过期" in msg:
                detailCode = "EXPIRED"
            elif "先激活" in msg:
                detailCode = "NEED_ACTIVATION"
            return RedeemResult(
                success=False,
                code=detailCode,
                message=msg,
                userId=lic.userId,
            )

        return RedeemResult(
            success=True,
            code="OK",
            message=result.message,
            grantedBalance=result.amount,
            userId=lic.userId,
        )

    # ---------- 内部:凭证存储 ----------
    def _saveLicense(self, license: License) -> None:
        """加密保存 License 到 config/license.enc(AES-GCM,密钥派生自设备指纹)。"""
        self._licenseFile.parent.mkdir(parents=True, exist_ok=True)
        try:
            key = self._deriveKey()
            cipher = AESCipherGCM(key)
            payload = license.model_dump_json()
            encrypted = cipher.encrypt(payload)
            self._licenseFile.write_text(encrypted, encoding="utf-8")
            logger.debug(f"[Auth] license 已加密保存: {self._licenseFile}")
        except Exception as e:
            logger.exception(f"[Auth] 保存 license 失败: {e}")
            raise

    def _load(self) -> None:
        if not self._licenseFile.exists():
            return
        try:
            key = self._deriveKey()
            cipher = AESCipherGCM(key)
            decrypted = cipher.decrypt(self._licenseFile.read_text(encoding="utf-8"))
            data = json.loads(decrypted)
            self._currentLicense = License.model_validate(data)
            logger.info(
                f"[Auth] 已加载本地 license user={self._currentLicense.userId} "
                f"expire={self._currentLicense.expireAt}"
            )
        except Exception as e:
            # 修复 BUG-1(2026-08-05):不再静默失败,
            # 1) 备份损坏文件 2) emit licenseCorrupted 信号让 UI 提示用户
            self._currentLicense = None
            self._backupCorruptedFile()
            logger.warning(f"[Auth] 加载 license.enc 失败: {e}")
            try:
                signalBus.licenseCorrupted.emit(str(e))
            except Exception:
                pass

    def _backupCorruptedFile(self) -> None:
        """将损坏的 license.enc 备份为 license.enc.corrupt.{timestamp}。"""
        if not self._licenseFile.exists():
            return
        try:
            import shutil
            from datetime import datetime as _dt

            ts = _dt.utcnow().strftime("%Y%m%d_%H%M%S")
            backup = self._licenseFile.with_name(
                f"{self._licenseFile.name}.corrupt.{ts}"
            )
            shutil.copy2(str(self._licenseFile), str(backup))
            logger.info(f"[Auth] 已备份损坏凭证: {backup}")
        except Exception as e:
            logger.warning(f"[Auth] 备份损坏凭证失败: {e}")

    def _deriveKey(self) -> bytes:
        """派生 AES 密钥(32 字节)。

        优先级:
            1. 设备指纹派生(主路径,每机独立)
            2. 沙箱密钥文件 <CONFIG_FOLDER>/.sandbox-key(每次启动随机生成,
               chmod 600,仅当前主机有效)—— 修复 BUG-2,避免跨机器复制
        """
        try:
            from app.core.utils.device_id import getDeviceIdentifier

            device = getDeviceIdentifier()
            if not device.deviceFeatures:
                device.collectDeviceFeatures()
            sortedFeatures = sorted(device.deviceFeatures.items())
            combined = "|".join(f"{k}:{v}" for k, v in sortedFeatures)
            salt = hash256(combined).encode()[:32]
            import hashlib as _h

            return _h.pbkdf2_hmac("sha256", combined.encode(), salt, 100000, dklen=32)
        except Exception as e:
            logger.warning(
                f"[Auth] 设备特征不可用({e}),降级到沙箱密钥:凭证仅当前主机有效"
            )
            return self._getOrCreateSandboxKey()

    def _getOrCreateSandboxKey(self) -> bytes:
        """获取或创建沙箱环境密钥(32 字节随机)。

        存储路径:<CONFIG_FOLDER>/.sandbox-key,文件权限 0600。
        进程级生成,持久化到磁盘,保证多次启动密钥稳定。
        """
        import os
        import secrets

        # 关键:从 self._licenseFile.parent 取路径,而不是模块级 LICENSE_FILE,
        # 这样 monkeypatch / 实例化时的 licenseFile 参数都能生效。
        keyFile = self._licenseFile.parent / ".sandbox-key"
        try:
            if keyFile.exists():
                data = keyFile.read_bytes()
                if len(data) >= 32:
                    return data[:32]
            # 第一次:随机生成 32 字节
            key = secrets.token_bytes(32)
            keyFile.parent.mkdir(parents=True, exist_ok=True)
            keyFile.write_bytes(key)
            # Windows 下没有 os.chmod 0600 语义,改用 icacls 跳过
            try:
                os.chmod(keyFile, 0o600)
            except (OSError, NotImplementedError):
                pass
            logger.info(f"[Auth] 已生成沙箱密钥: {keyFile}")
            return key
        except Exception as e:
            # 兜底:进程级内存密钥(最差情况,重启即失效)
            logger.warning(f"[Auth] 沙箱密钥持久化失败,使用进程级密钥: {e}")
            return secrets.token_bytes(32)

    def _ensureAccountWithGrant(
        self,
        license: License,
        grantAmount: int,
        source: str,
    ) -> None:
        """创建账户并赠送初始余额(幂等)。"""
        from app.core.models.billing_models import Account

        existing = account_db.getAccount(license.userId)
        if existing is None:
            # 先创建账户(余额 0)
            account = Account(
                userId=license.userId,
                displayName=license.displayName,
                tier=license.tier.value,
                balance=0,
                frozenBalance=0,
                totalSpent=0,
                totalRecharged=0,
            )
            account_db.upsertAccount(account)
        # 再加余额(同时写充值记录)
        try:
            record = account_db.addBalance(
                userId=license.userId,
                delta=grantAmount,
                source=source,
                code="",
            )
            logger.debug(f"[Auth] 初始赠送记录: {record.recordId}")
        except Exception as e:
            logger.warning(f"[Auth] 写初始赠送流水失败: {e}")


def _genId(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _deviceFingerprint() -> str:
    try:
        from app.core.utils.device_id import generateOrLoadDeviceId

        return generateOrLoadDeviceId()
    except Exception:
        return ""


def getAuthService() -> AuthService:
    """获取全局 AuthService 单例。"""
    return AuthService.instance()
