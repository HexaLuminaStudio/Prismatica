# coding: utf-8
"""P0-A 桌面端 CloudAuth(登录 / 注册 / refresh / logout / 找回密码)。"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from app.core.utils import logger, signalBus
from app.core.utils.encryption import AESCipherGCM, deriveKey, hash256

from .cloud_api import CloudApi, CloudApiError, CloudSession, getCloudApi

SESSION_FILE_NAME = "cloud_session.enc"
SESSION_FILE_VERSION = 1


class CloudAuth:
    """桌面端云端鉴权门面。

    - 登录 / 注册 / 退出 调云端 REST 接口
    - 维护本地加密的 CloudSession 文件(license.enc / cloud_session.enc)
    - 监听 access_token 过期(5min 前自动 refresh)
    """

    def __init__(self) -> None:
        self._api = getCloudApi()
        # 注入 refresh 回调
        self._api.setRefreshCallback(self._refreshAccessToken)
        self._sessionFile: Optional[Path] = None
        self._lastRefreshAt: float = 0.0

    # ------------------------------------------------------------------
    # 会话文件 IO(加密)
    # ------------------------------------------------------------------

    def _getSessionFile(self) -> Path:
        if self._sessionFile is not None:
            return self._sessionFile
        try:
            from app.core.utils.setting import DATA_FOLDER

            dataDir = Path(DATA_FOLDER)
        except Exception:
            dataDir = Path.home() / "AppData" / "Roaming" / "Prismatica"
        dataDir.mkdir(parents=True, exist_ok=True)
        self._sessionFile = dataDir / SESSION_FILE_NAME
        return self._sessionFile

    def _encryptionKey(self) -> bytes | None:
        """从设备特征派生加密密钥(失败则不加密,只留本地可读)。"""
        try:
            from app.core.utils.device_id import getDeviceIdentifier

            device = getDeviceIdentifier()
            if not device.deviceFeatures:
                device.collectDeviceFeatures()
            combined = "|".join(
                f"{k}:{v}" for k, v in sorted(device.deviceFeatures.items())
            )
            saltSource = combined
            fixedSalt = hash256(saltSource).encode()[:32]
            key, _ = deriveKey(combined, iterations=100000, keyLength=32, salt=fixedSalt)
            return key
        except Exception as exc:
            logger.debug(f"[CloudAuth] 设备特征不可用,会话将以明文存储: {exc}")
            return None

    def _saveSession(self) -> None:
        try:
            path = self._getSessionFile()
            payload = asdict(self._api.getSession())
            payload["__v"] = SESSION_FILE_VERSION
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            key = self._encryptionKey()
            if key is not None:
                cipher = AESCipherGCM(key)
                raw = cipher.encrypt(raw.decode("utf-8")).encode("utf-8")
            path.write_bytes(raw)
            logger.info(f"[CloudAuth] 会话已保存 → {path.name}")
        except Exception:
            logger.exception("[CloudAuth] 保存会话失败")

    def _loadSession(self) -> bool:
        try:
            path = self._getSessionFile()
            if not path.exists():
                return False
            raw = path.read_bytes().decode("utf-8")
            key = self._encryptionKey()
            if key is not None:
                cipher = AESCipherGCM(key)
                raw = cipher.decrypt(raw)
            payload = json.loads(raw)
            if payload.get("__v") != SESSION_FILE_VERSION:
                logger.warning("[CloudAuth] 会话文件版本不匹配,丢弃")
                return False
            self._api.setSession(
                CloudSession(
                    accessToken=payload.get("accessToken", ""),
                    refreshToken=payload.get("refreshToken", ""),
                    userId=int(payload.get("userId", 0) or 0),
                    email=payload.get("email", ""),
                    displayName=payload.get("displayName", ""),
                    tier=payload.get("tier", "free"),
                    issuedAt=int(payload.get("issuedAt", 0) or 0),
                    expiresAt=int(payload.get("expiresAt", 0) or 0),
                )
            )
            logger.info("[CloudAuth] 会话已恢复")
            return True
        except Exception:
            logger.exception("[CloudAuth] 加载会话失败")
            return False

    def _clearSession(self) -> None:
        self._api.clearSession()
        try:
            path = self._getSessionFile()
            if path.exists():
                path.unlink()
                logger.info("[CloudAuth] 会话文件已清除")
        except Exception:
            logger.exception("[CloudAuth] 清除会话文件失败")

    # ------------------------------------------------------------------
    # 注册 / 登录 / 退出
    # ------------------------------------------------------------------

    def register(
        self,
        email: str,
        password: str,
        displayName: str = "",
    ) -> dict:
        try:
            data = self._api.post(
                "/v1/auth/register",
                body={
                    "email": email,
                    "password": password,
                    "displayName": displayName,
                },
                withAuth=False,
            )
        except CloudApiError:
            raise
        logger.info(f"[CloudAuth] 注册成功 email={email}")
        # 注册成功后自动登录
        return self.login(email, password)

    def login(self, email: str, password: str) -> dict:
        try:
            data = self._api.post(
                "/v1/auth/login",
                body={
                    "email": email,
                    "password": password,
                },
                withAuth=False,
            )
        except CloudApiError:
            raise
        self._applyLoginResponse(data)
        return data

    def logout(self) -> None:
        try:
            if self._api.getSession().refreshToken:
                self._api.post(
                    "/v1/auth/logout",
                    body={"refreshToken": self._api.getSession().refreshToken},
                    withAuth=False,
                )
        except CloudApiError as exc:
            # 即便云端失败,本地也清掉
            logger.warning(f"[CloudAuth] 云端 logout 失败: {exc}")
        self._clearSession()
        try:
            signalBus.sessionChanged.emit(False)
        except Exception:
            pass
        logger.info("[CloudAuth] 已退出")

    # ------------------------------------------------------------------
    # refresh(自动 + 手动)
    # ------------------------------------------------------------------

    def _applyLoginResponse(self, data: dict) -> None:
        """把 /login 或 /refresh 返回的 tokens 写入本地 session。"""
        if not data:
            return
        tokens = data.get("tokens") or {}
        user = data.get("user") or {}
        sess = CloudSession(
            accessToken=tokens.get("accessToken", ""),
            refreshToken=tokens.get("refreshToken", ""),
            userId=int(user.get("userId", 0) or 0),
            email=user.get("email", ""),
            displayName=user.get("displayName", ""),
            tier=user.get("tier", "free"),
            issuedAt=int(time.time()),
            expiresAt=int(time.time()) + int(tokens.get("expiresIn", 3600) or 3600),
        )
        self._api.setSession(sess)
        self._saveSession()
        try:
            signalBus.sessionChanged.emit(True)
        except Exception:
            pass

    def _refreshAccessToken(self) -> bool:
        """CloudApi 在 401 时调用的回调:用 refresh_token 换新 token。"""
        now = time.time()
        if now - self._lastRefreshAt < 2.0:
            # 防止 refresh 死循环
            return False
        self._lastRefreshAt = now

        sess = self._api.getSession()
        if not sess.refreshToken:
            return False
        try:
            data = self._api.post(
                "/v1/auth/refresh",
                body={"refreshToken": sess.refreshToken},
                withAuth=False,
            )
        except CloudApiError as exc:
            logger.warning(f"[CloudAuth] refresh 失败: {exc}")
            if exc.code in ("REFRESH_INVALID", "REFRESH_EXPIRED", "TOKEN_REVOKED"):
                self._clearSession()
            return False
        self._applyLoginResponse(data)
        return True

    def isAccessTokenNearExpiry(self, leadSec: int = 300) -> bool:
        sess = self._api.getSession()
        if not sess.expiresAt:
            return True
        return sess.expiresAt - int(time.time()) < leadSec

    def ensureFreshAccessToken(self) -> None:
        """UI 可在调用云端前主动调,避免 401。"""
        if self.isAccessTokenNearExpiry() and self._api.getSession().refreshToken:
            self._refreshAccessToken()

    # ------------------------------------------------------------------
    # 找回密码 / 修改密码
    # ------------------------------------------------------------------

    def requestPasswordReset(self, email: str) -> dict:
        return self._api.post(
            "/v1/auth/password/reset-request",
            body={"email": email},
            withAuth=False,
        )

    def confirmPasswordReset(self, token: str, newPassword: str) -> dict:
        return self._api.post(
            "/v1/auth/password/reset-confirm",
            body={"token": token, "newPassword": newPassword},
            withAuth=False,
        )

    def changePassword(self, oldPassword: str, newPassword: str) -> dict:
        self.ensureFreshAccessToken()
        return self._api.post(
            "/v1/auth/password/change",
            body={"oldPassword": oldPassword, "newPassword": newPassword},
        )

    # ------------------------------------------------------------------
    # 启动期恢复
    # ------------------------------------------------------------------

    def bootstrap(self) -> bool:
        """程序启动时尝试恢复上次的会话;不抛错。"""
        if self._loadSession() and self._api.getSession().refreshToken:
            # 后台异步 refresh(不阻塞启动)
            try:
                self._refreshAccessToken()
            except Exception:
                logger.exception("[CloudAuth] 启动期 refresh 失败")
            return True
        return False


_singleton: CloudAuth | None = None


def getCloudAuth() -> CloudAuth:
    global _singleton
    if _singleton is None:
        _singleton = CloudAuth()
    return _singleton


__all__ = [
    "CloudAuth",
    "getCloudAuth",
    "CloudApiError",
]
