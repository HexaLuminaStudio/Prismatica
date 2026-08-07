# coding: utf-8
"""
云端 API 客户端基类(2026-08-07 P0-A 接入)

负责:
    - 注入 baseUrl(从 cfg.cloudBaseUrl)
    - 注入设备 ID(从 utils.device_id)
    - 注入 access token(从本地会话文件,见 cloud_auth)
    - 统一处理 401(过期)→ 自动 refresh
    - 统一处理 402 / 409 → CloudApiError
    - 统一处理 429 → CloudApiError(限速)
    - 网络异常 / 超时 → CloudApiError

所有云端 service(cloud_auth / cloud_account / cloud_billing)都通过
本类发请求,保证:
    - Authorization 头
    - X-Device-Id 头
    - X-Client-Platform 头
    - Idempotency-Key 头(可选,UUID4)
    - X-Request-Id 头(可选,UUID4)
"""
from __future__ import annotations

import json
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import requests
from requests.exceptions import ConnectionError, RequestException, Timeout

from app.core.utils import cfg, logger


@dataclass
class CloudSession:
    """本地维护的会话状态(由 cloud_auth 写入 / 读取)。"""

    accessToken: str = ""
    refreshToken: str = ""
    userId: int = 0
    email: str = ""
    displayName: str = ""
    tier: str = "free"
    issuedAt: int = 0
    expiresAt: int = 0


@dataclass
class CloudApiError(Exception):
    """云端 API 错误统一封装。"""

    code: str
    message: str
    httpStatus: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class CloudApi:
    """云端 API 客户端(单例)。"""

    _instance: Optional["CloudApi"] = None

    def __init__(self) -> None:
        self._session = CloudSession()
        # 用于刷新 access_token 的回调(由 cloud_auth 注入)
        self._refreshCallback = None
        # 用于触发 MAX_DEVICES_REACHED 弹窗的回调
        self._onMaxDevicesReached = None

    @classmethod
    def instance(cls) -> "CloudApi":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------

    def getSession(self) -> CloudSession:
        return self._session

    def setSession(self, session: CloudSession) -> None:
        self._session = session

    def clearSession(self) -> None:
        self._session = CloudSession()

    def isLoggedIn(self) -> bool:
        return bool(self._session.accessToken) and self._session.userId > 0

    def setRefreshCallback(self, callback) -> None:
        self._refreshCallback = callback

    def setOnMaxDevicesReached(self, callback) -> None:
        self._onMaxDevicesReached = callback

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _baseUrl(self) -> str:
        url = (cfg.cloudBaseUrl.value or "").strip().rstrip("/")
        if not url:
            raise CloudApiError("NETWORK_ERROR", "未配置云端 API 地址(cfg.cloudBaseUrl)")
        return url

    def _deviceId(self) -> str:
        # device_id 在项目根目录的 device.bin 中持久化
        try:
            from app.core.utils.device_id import generateOrLoadDeviceId

            return generateOrLoadDeviceId()
        except Exception:
            # 测试/异常环境退化为随机 ID(仍能让请求通过;登录会被后端按设备绑)
            return f"dev-{uuid.uuid4().hex[:16]}"

    def _headers(
        self,
        *,
        withAuth: bool = True,
        idempotencyKey: str | None = None,
        requestId: str | None = None,
    ) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Client-Platform": "prismatica-desktop",
            "X-Request-Id": requestId or str(uuid.uuid4()),
        }
        if idempotencyKey:
            headers["Idempotency-Key"] = idempotencyKey
        if withAuth and self._session.accessToken:
            headers["Authorization"] = f"Bearer {self._session.accessToken}"
            headers["X-Device-Id"] = self._deviceId()
        return headers

    def _unwrapEnvelope(self, payload: Dict[str, Any] | Any) -> Any:
        """统一 envelope:{code, data, requestId, details};失败抛 CloudApiError。"""
        if not isinstance(payload, dict):
            return payload
        if "code" not in payload:
            # 不是 envelope 格式,直接返回(用于 /health 等)
            return payload
        if payload.get("code") == "OK":
            return payload.get("data")
        # 错误
        errCode = str(payload.get("code", "INTERNAL_ERROR"))
        errMsg = str(payload.get("message", errCode))
        errDetails = payload.get("details") or {}
        # 一些特殊码 → 触发业务回调
        if errCode == "MAX_DEVICES_REACHED" and self._onMaxDevicesReached:
            try:
                self._onMaxDevicesReached(errDetails)
            except Exception:
                logger.exception("[CloudApi] onMaxDevicesReached 回调失败")
        # 401 提示 token 失效
        raise CloudApiError(errCode, errMsg, details=errDetails)

    # ------------------------------------------------------------------
    # HTTP 通用
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Dict[str, Any] | None = None,
        withAuth: bool = True,
        idempotencyKey: str | None = None,
        timeout: float = 15.0,
    ) -> Any:
        url = f"{self._baseUrl()}{path}"
        headers = self._headers(
            withAuth=withAuth,
            idempotencyKey=idempotencyKey,
        )
        try:
            resp = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=body,
                timeout=timeout,
            )
        except (ConnectionError, Timeout, socket.gaierror) as exc:
            raise CloudApiError("NETWORK_ERROR", f"网络异常: {exc}") from exc
        except RequestException as exc:
            raise CloudApiError("NETWORK_ERROR", f"请求失败: {exc}") from exc

        # 401 → 尝试 refresh 后重试一次
        if resp.status_code == 401 and withAuth and self._refreshCallback:
            logger.info("[CloudApi] 401,尝试 refresh access_token")
            try:
                if self._refreshCallback():
                    # 用新 token 重试
                    headers["Authorization"] = f"Bearer {self._session.accessToken}"
                    resp = requests.request(
                        method=method,
                        url=url,
                        headers=headers,
                        json=body,
                        timeout=timeout,
                    )
            except Exception as exc:
                logger.warning(f"[CloudApi] refresh 后仍失败: {exc}")

        # 解析 JSON envelope
        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise CloudApiError(
                "BAD_RESPONSE",
                f"响应不是合法 JSON: {exc}",
                httpStatus=resp.status_code,
            ) from exc

        if resp.status_code >= 500:
            raise CloudApiError(
                "INTERNAL_ERROR",
                data.get("message", "服务暂时不可用") if isinstance(data, dict) else "服务暂时不可用",
                httpStatus=resp.status_code,
                details=data if isinstance(data, dict) else {},
            )

        return self._unwrapEnvelope(data)

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    def get(self, path: str, **kw) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Dict[str, Any] | None = None, **kw) -> Any:
        return self.request("POST", path, body=body, **kw)

    def patch(self, path: str, body: Dict[str, Any] | None = None, **kw) -> Any:
        return self.request("PATCH", path, body=body, **kw)

    def delete(self, path: str, **kw) -> Any:
        return self.request("DELETE", path, **kw)


def getCloudApi() -> CloudApi:
    return CloudApi.instance()


__all__ = [
    "CloudSession",
    "CloudApi",
    "CloudApiError",
    "getCloudApi",
]
