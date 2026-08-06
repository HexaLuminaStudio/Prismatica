# coding: utf-8
"""鉴权服务(AuthService)- 2026-08-05 T4/T5 重构

变化摘要:
    - 删掉 `_activateFromInvite / _activateFromTrial / _redeemRecharge` 三个本地分支
    - 删掉 `redeemCode` 中「云端失败 → 走本地降级」的兜底逻辑
    - 删掉 `_ensureAccountWithGrant`(grant 完全由云端完成)
    - 新增 `clearTokens()` 方法(供 CloudApi logout 等场景使用)
    - 修 `setTokens` 在 `self._currentLicense is None` 时仍持久化 token

本服务在「强云端」决策下,只承担:
    1. License 模型 + license.enc 加密持久化
    2. JWT 内存缓存 + TokenStore 抽象实现(供 CloudApi 拿)
    3. 通过 AuthGateway 调云端(由 AuthGateway.redeem() 收敛云端路径)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from app.core.utils import logger

from app.core.models.auth_models import (
    AuthMode,
    License,
    RedeemResult,
    UserTier,
)
from app.core.utils.encryption import AESCipherGCM, hash256
from app.core.utils.signal_bus import signalBus


LICENSE_FILE: Path = None  # 启动期由 _deriveLicenseFile() 填实

# 全局单例
_authServiceInstance: Optional["AuthService"] = None


def _deriveLicenseFile() -> Path:
    """模块级默认 license.enc 路径(从 setting.py 取)。"""
    from app.core.utils.setting import CONFIG_FOLDER

    return CONFIG_FOLDER / "license.enc"


class InvalidCodeError(Exception):
    """凭证无效(签名错 / 已过期 / 格式错)"""


class AuthService:
    """鉴权主服务(2026-08-05 收缩为「凭证存储 + token 缓存」)"""

    def __init__(self, licenseFile: Optional[Path] = None):
        # 模块级 vs 实例级,monkeypatch 友好
        if licenseFile is not None:
            self._licenseFile = licenseFile
        else:
            global LICENSE_FILE
            if LICENSE_FILE is None:
                LICENSE_FILE = _deriveLicenseFile()
            self._licenseFile = LICENSE_FILE
        self._currentLicense: Optional[License] = None
        # 云端 JWT 缓存
        self._accessToken: Optional[str] = None
        self._refreshToken: Optional[str] = None
        self._tokenExpiresAt: Optional[datetime] = None
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

    # ---------- 云端 JWT ----------
    def getAccessToken(self) -> Optional[str]:
        return self._accessToken

    def getRefreshToken(self) -> Optional[str]:
        return self._refreshToken

    def setTokens(
        self,
        accessToken: str,
        refreshToken: str,
        expiresIn: int,
    ) -> None:
        """写入内存 + 持久化(2026-08-05 T5:即使 _currentLicense is None 也要写)。"""
        self._accessToken = accessToken
        self._refreshToken = refreshToken
        self._tokenExpiresAt = datetime.utcnow() + timedelta(seconds=max(1, int(expiresIn)))
        # 持久化到 license.enc(下次 _save 会带上)
        try:
            self._saveToDisk()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Auth] setTokens 持久化失败(忽略): {e}")

    def clearTokens(self) -> None:
        """2026-08-05 T1/T4 新增:仅清 token,保留 License(供 CloudApi.logout 使用)。"""
        self._accessToken = None
        self._refreshToken = None
        self._tokenExpiresAt = None
        try:
            self._saveToDisk()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Auth] clearTokens 持久化失败(忽略): {e}")

    def deactivate(self) -> None:
        """注销(清 license + token + 文件)。"""
        try:
            if self._licenseFile.exists():
                self._licenseFile.unlink()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Auth] 删除 license.enc 失败: {e}")
        self._currentLicense = None
        self._accessToken = None
        self._refreshToken = None
        self._tokenExpiresAt = None
        try:
            signalBus.activationStatusChanged.emit(False)
        except Exception:  # noqa: BLE001
            pass
        logger.info("[Auth] 已注销本地凭证")

    # ---------- 兑换(2026-08-05 委托给 AuthGateway) ----------
    def redeemCode(
        self,
        rawCode: str,
        displayName: Optional[str] = None,
    ) -> RedeemResult:
        """统一兑换入口:走 AuthGateway(不再 fallback 本地)。"""
        from app.core.services.auth_gateway import getAuthGateway

        gateway = getAuthGateway()
        result = gateway.redeem(rawCode=rawCode, displayName=displayName)

        # 成功:把云端返回的 License 写本地缓存
        if result.success and result.license is not None:
            self._currentLicense = result.license
            try:
                self._saveToDisk()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[Auth] redeem 写 license.enc 失败: {e}")
            try:
                signalBus.activationStatusChanged.emit(True)
            except Exception:  # noqa: BLE001
                pass
            logger.info(
                f"[Auth] redeem 成功 user={result.userId} "
                f"grantedBalance={result.grantedBalance}"
            )
            # 云端权威:兑换后立即拉 /me 同步真实 expireAt/tier(失败不阻断)
            self.restoreSession()

        return result

    # ---------- 会话恢复(云端权威) ----------
    def restoreSession(self) -> bool:
        """启动/兑换后调用:让本地凭证与云端一致。

        流程:
            1. 无本地凭证 → 返回 False
            2. access token 未过期 → 直接拉 /me 同步 tier/expireAt
            3. access token 过期/缺失 → 用 refresh token 恢复会话,再拉 /me 同步
            4. 任何失败 → 保留本地离线凭证,不弹错

        返回 True 表示会话已与云端确认(至少本地凭证可用)。
        """
        lic = self._currentLicense
        if lic is None:
            return False

        refreshed = self._isTokenUsable() or self._tryRefreshToken()

        # 云端权威:同步 tier / displayName / expireAt
        try:
            from app.core.services.billing_service import getBillingService

            account = getBillingService().refreshUserFromCloud(lic.userId)
            if account is not None:
                self._syncLicenseFromAccount(lic, account)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Auth] restoreSession /me 同步失败(忽略): {e}")

        return refreshed

    def _syncLicenseFromAccount(self, lic: License, account: Any) -> None:
        """把云端 Account 的关键字段回写本地 License(云端为准)。"""
        updated = False
        if account.displayName and account.displayName != lic.displayName:
            lic.displayName = account.displayName
            updated = True
        try:
            cloudTier = UserTier(account.tier)
            if cloudTier != UserTier.GUEST and cloudTier != lic.tier:
                lic.tier = cloudTier
                updated = True
        except Exception:  # noqa: BLE001
            pass
        if account.expireAt is not None and account.expireAt != lic.expireAt:
            # 云端为准:即使比本地早也采用(修正本地编造的 365 天兜底)
            lic.expireAt = account.expireAt
            updated = True
        if updated:
            try:
                self._saveToDisk()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[Auth] restoreSession 写盘失败(忽略): {e}")
            try:
                signalBus.activationStatusChanged.emit(True)
            except Exception:  # noqa: BLE001
                pass

    def _isTokenUsable(self) -> bool:
        """access token 是否仍可用(存在且未过期)。"""
        if not self._accessToken:
            return False
        if self._tokenExpiresAt is None:
            return False
        return self._tokenExpiresAt > datetime.utcnow()

    def _tryRefreshToken(self) -> bool:
        """用 refresh token 从云端恢复会话(失败静默返回 False)。"""
        if not self._refreshToken:
            return False
        try:
            from app.core.services.auth_gateway import getAuthGateway

            ok = getAuthGateway().refreshSession()
            if ok:
                logger.info("[Auth] 会话已通过 refresh token 恢复")
            return ok
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Auth] 会话刷新失败(忽略): {e}")
            return False

    # ---------- 内部:凭证存储 ----------
    def _saveToDisk(self) -> None:
        """统一存盘入口(供 setTokens / clearTokens / redeem 复用)。"""
        self._licenseFile.parent.mkdir(parents=True, exist_ok=True)
        try:
            key = self._deriveKey()
            cipher = AESCipherGCM(key)
            envelope: dict[str, Any] = {}
            if self._currentLicense is not None:
                envelope["license"] = json.loads(self._currentLicense.model_dump_json())
            if self._accessToken:
                envelope["accessToken"] = self._accessToken
            if self._refreshToken:
                envelope["refreshToken"] = self._refreshToken
            if self._tokenExpiresAt:
                envelope["tokenExpiresAt"] = self._tokenExpiresAt.isoformat()
            if not envelope:
                # 没有任何数据,不写空文件
                return
            payload = json.dumps(envelope, ensure_ascii=False)
            encrypted = cipher.encrypt(payload)
            self._licenseFile.write_text(encrypted, encoding="utf-8")
            logger.debug(f"[Auth] license 已加密保存: {self._licenseFile}")
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[Auth] 保存 license 失败: {e}")
            raise

    def _saveLicense(self, license: License) -> None:
        """兼容旧调用(保留一段时间,后续删)。

        新代码请用 _saveToDisk()。
        """
        self._currentLicense = license
        self._saveToDisk()

    def _load(self) -> None:
        if not self._licenseFile.exists():
            return
        loadError: Optional[Exception] = None
        try:
            key = self._deriveKey()
            envelope = self._decryptEnvelope(key)
        except Exception as e:  # noqa: BLE001
            envelope = None
            loadError = e

        # 迁移:老版本 license.enc 用「设备指纹/沙箱密钥」加密,升级后尝试旧密钥解密
        migrated = False
        if envelope is None:
            try:
                legacyKey = self._legacyDeriveKey()
                envelope = self._decryptEnvelope(legacyKey)
                migrated = True
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[Auth] 旧密钥解密失败,标记损坏: {e}")

        if envelope is None:
            # 1) 备份损坏文件 2) emit licenseCorrupted 信号让 UI 提示用户
            self._currentLicense = None
            self._backupCorruptedFile()
            logger.warning(f"[Auth] 加载 license.enc 失败: {loadError}")
            try:
                signalBus.licenseCorrupted.emit(str(loadError))
            except Exception:  # noqa: BLE001
                pass
            return

        if isinstance(envelope, dict) and "license" in envelope:
            licPayload = envelope.get("license")
            if licPayload:
                self._currentLicense = License.model_validate(licPayload)
            self._accessToken = envelope.get("accessToken")
            self._refreshToken = envelope.get("refreshToken")
            expires = envelope.get("tokenExpiresAt")
            if expires:
                try:
                    self._tokenExpiresAt = datetime.fromisoformat(expires)
                except Exception:  # noqa: BLE001
                    self._tokenExpiresAt = None
        elif isinstance(envelope, dict):
            # 兼容老格式(整段就是 License)
            self._currentLicense = License.model_validate(envelope)
        logger.info(
            f"[Auth] 已加载本地 license user={self._currentLicense.userId if self._currentLicense else None} "
            f"hasJwt={bool(self._accessToken)}"
        )

        # 迁移成功:立即用新持久化密钥重加密,后续统一走新密钥
        if migrated:
            try:
                self._saveToDisk()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[Auth] 迁移重加密失败(忽略): {e}")

    def _decryptEnvelope(self, key: bytes) -> Optional[dict]:
        """用指定密钥解密 license.enc 并解析为 dict;失败抛异常。"""
        cipher = AESCipherGCM(key)
        decrypted = cipher.decrypt(self._licenseFile.read_text(encoding="utf-8"))
        envelope = json.loads(decrypted)
        return envelope if isinstance(envelope, dict) else None

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
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Auth] 备份损坏凭证失败: {e}")

    def _deriveKey(self) -> bytes:
        """主密钥:持久化随机密钥文件(<CONFIG_FOLDER>/.license-key)。

        首次生成后固定不变,不再依赖硬件特征 —— 硬件变化/特征采集失败
        都不会再导致 license.enc 无法解密(修复「刷新后即消失」)。
        """
        return self._getOrCreateLicenseKey()

    def _getOrCreateLicenseKey(self) -> bytes:
        """获取或创建持久化随机密钥(32 字节)。文件缺失/写盘失败即报错,不再生成一次性随机密钥。"""
        import secrets

        keyFile = self._licenseFile.parent / ".license-key"
        keyFile.parent.mkdir(parents=True, exist_ok=True)
        if keyFile.exists():
            data = keyFile.read_bytes()
            if len(data) >= 32:
                return data[:32]
        key = secrets.token_bytes(32)
        keyFile.write_bytes(key)
        logger.info(f"[Auth] 已生成持久化密钥: {keyFile}")
        return key

    def _legacyDeriveKey(self) -> bytes:
        """旧版密钥(设备指纹 → 沙箱密钥),仅供迁移解密存量 license.enc。"""
        try:
            from app.core.utils.device_id import getDeviceIdentifier

            device = getDeviceIdentifier()
            if not device.deviceFeatures:
                device.collectDeviceFeatures()
            sortedFeatures = sorted(device.deviceFeatures.items())
            combined = "|".join(f"{k}:{v}" for k, v in sortedFeatures)
            salt = hash256(combined).encode()[:32]
            import hashlib as _h

            return _h.pbkdf2_hmac(
                "sha256", combined.encode(), salt, 100000, dklen=32
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Auth] 设备特征不可用({e}),尝试旧沙箱密钥")
            return self._readSandboxKey()

    def _readSandboxKey(self) -> bytes:
        """读取旧沙箱密钥(仅迁移用);缺失则报错,不再生成一次性随机密钥。"""
        keyFile = self._licenseFile.parent / ".sandbox-key"
        if keyFile.exists():
            data = keyFile.read_bytes()
            if len(data) >= 32:
                return data[:32]
        raise RuntimeError("旧沙箱密钥缺失,无法迁移解密")


def getAuthService() -> AuthService:
    """获取全局 AuthService 单例。"""
    return AuthService.instance()


__all__ = [
    "AuthService",
    "InvalidCodeError",
    "getAuthService",
]
