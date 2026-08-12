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
import threading
import uuid
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Dict, Optional

import requests
from requests.exceptions import ConnectionError, RequestException, Timeout

from app.core.utils import cfg, logger

from .responsive_call import runResponsiveCall

try:
    CLIENT_VERSION = version("prismatica")
except PackageNotFoundError:
    CLIENT_VERSION = "dev"

DEFAULT_CONNECT_TIMEOUT = 3.05
DEFAULT_READ_TIMEOUT = 10.0
DEFAULT_REQUEST_TIMEOUT = (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT)


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


class CloudEventStream:
    """可取消的 SSE 响应句柄；由后台线程迭代事件。"""

    def __init__(self, response) -> None:
        self._response = response
        self._closed = False
        self._closeLock = threading.Lock()
        self._response.encoding = "utf-8"

    def iterEvents(self):
        eventName = "message"
        dataLines: list[str] = []
        try:
            for rawLine in self._response.iter_lines(decode_unicode=True):
                if self._closed:
                    return
                line = str(rawLine or "")
                if not line:
                    if dataLines:
                        rawData = "\n".join(dataLines)
                        try:
                            data = json.loads(rawData)
                        except (ValueError, json.JSONDecodeError) as exc:
                            raise CloudApiError(
                                "BAD_RESPONSE",
                                f"流式响应不是合法 JSON: {exc}",
                            ) from exc
                        yield {
                            "event": eventName,
                            "data": data if isinstance(data, dict) else {},
                        }
                    eventName = "message"
                    dataLines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    eventName = line[6:].strip() or "message"
                elif line.startswith("data:"):
                    dataLines.append(line[5:].lstrip())
            if dataLines and not self._closed:
                try:
                    data = json.loads("\n".join(dataLines))
                except (ValueError, json.JSONDecodeError) as exc:
                    raise CloudApiError(
                        "BAD_RESPONSE",
                        f"流式响应不是合法 JSON: {exc}",
                    ) from exc
                yield {
                    "event": eventName,
                    "data": data if isinstance(data, dict) else {},
                }
        except RequestException as exc:
            if not self._closed:
                raise CloudApiError("NETWORK_ERROR", f"流式连接异常: {exc}") from exc
        finally:
            self.close()

    def close(self) -> None:
        with self._closeLock:
            if self._closed:
                return
            self._closed = True
            self._response.close()


class CloudApi:
    """云端 API 客户端(单例)。"""

    _instance: Optional["CloudApi"] = None

    def __init__(self) -> None:
        self._session = CloudSession()
        self._httpClients = threading.local()
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

    def _httpClient(self) -> requests.Session:
        """返回当前线程独享的 HTTP 会话，并应用明确的代理策略。"""
        client = getattr(self._httpClients, "client", None)
        if client is None:
            client = requests.Session()
            self._httpClients.client = client
        client.trust_env = bool(cfg.cloudUseSystemProxy.value)
        return client

    def _discardHttpClient(self) -> None:
        """丢弃当前线程的连接池，避免 VPN/网络切换后复用失效连接。"""
        client = getattr(self._httpClients, "client", None)
        if client is None:
            return
        try:
            client.close()
        finally:
            del self._httpClients.client

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
            "X-Client-Version": CLIENT_VERSION,
            "X-Device-Id": self._deviceId(),
            "X-Request-Id": requestId or str(uuid.uuid4()),
        }
        if idempotencyKey:
            headers["Idempotency-Key"] = idempotencyKey
        if withAuth and self._session.accessToken:
            headers["Authorization"] = f"Bearer {self._session.accessToken}"
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
        timeout: float | tuple[float, float] = DEFAULT_REQUEST_TIMEOUT,
    ) -> Any:
        """发送云端请求；从 Qt 主线程调用时自动转入后台线程。"""
        return runResponsiveCall(
            lambda: self._requestBlocking(
                method,
                path,
                body=body,
                withAuth=withAuth,
                idempotencyKey=idempotencyKey,
                timeout=timeout,
            )
        )

    def _requestOnce(
        self,
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        body: Dict[str, Any] | None,
        timeout: float | tuple[float, float],
        stream: bool = False,
    ):
        """执行一次 HTTP 请求，并在网络异常时清理当前线程连接池。"""
        try:
            return self._httpClient().request(
                method=method,
                url=url,
                headers=headers,
                json=body,
                timeout=timeout,
                stream=stream,
            )
        except (ConnectionError, Timeout, socket.gaierror) as exc:
            self._discardHttpClient()
            raise CloudApiError("NETWORK_ERROR", f"网络异常: {exc}") from exc
        except RequestException as exc:
            self._discardHttpClient()
            raise CloudApiError("NETWORK_ERROR", f"请求失败: {exc}") from exc

    def _requestBlocking(
        self,
        method: str,
        path: str,
        *,
        body: Dict[str, Any] | None,
        withAuth: bool,
        idempotencyKey: str | None,
        timeout: float | tuple[float, float],
    ) -> Any:
        url = f"{self._baseUrl()}{path}"
        headers = self._headers(
            withAuth=withAuth,
            idempotencyKey=idempotencyKey,
        )
        resp = self._requestOnce(
            method,
            url,
            headers=headers,
            body=body,
            timeout=timeout,
        )

        # 401 → 尝试 refresh 后重试一次
        if resp.status_code == 401 and withAuth and self._refreshCallback:
            logger.info("[CloudApi] 401,尝试 refresh access_token")
            try:
                failedAccessToken = headers.get("Authorization", "").removeprefix("Bearer ")
                if self._refreshCallback(failedAccessToken):
                    # 用新 token 重试
                    headers["Authorization"] = f"Bearer {self._session.accessToken}"
                    resp = self._requestOnce(
                        method,
                        url,
                        headers=headers,
                        body=body,
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

    def openEventStream(
        self,
        path: str,
        *,
        body: Dict[str, Any] | None = None,
        withAuth: bool = True,
        idempotencyKey: str | None = None,
        timeout: float | tuple[float, float] = DEFAULT_REQUEST_TIMEOUT,
    ) -> CloudEventStream:
        """阻塞打开 SSE；调用方必须在后台线程迭代并在取消时关闭。"""
        url = f"{self._baseUrl()}{path}"
        headers = self._headers(
            withAuth=withAuth,
            idempotencyKey=idempotencyKey,
        )
        headers["Accept"] = "text/event-stream"
        response = self._requestOnce(
            "POST",
            url,
            headers=headers,
            body=body,
            timeout=timeout,
            stream=True,
        )
        if response.status_code == 401 and withAuth and self._refreshCallback:
            failedAccessToken = headers.get("Authorization", "").removeprefix("Bearer ")
            if self._refreshCallback(failedAccessToken):
                response.close()
                headers["Authorization"] = f"Bearer {self._session.accessToken}"
                response = self._requestOnce(
                    "POST",
                    url,
                    headers=headers,
                    body=body,
                    timeout=timeout,
                    stream=True,
                )
        if response.status_code >= 400:
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                response.close()
                raise CloudApiError(
                    "BAD_RESPONSE",
                    f"流式接口返回无效响应: {exc}",
                    httpStatus=response.status_code,
                ) from exc
            response.close()
            try:
                self._unwrapEnvelope(payload)
            except CloudApiError as error:
                error.httpStatus = response.status_code
                raise
            raise CloudApiError(
                "BAD_RESPONSE",
                "流式接口返回错误",
                httpStatus=response.status_code,
            )
        contentType = response.headers.get("Content-Type", "").lower()
        if "text/event-stream" not in contentType:
            response.close()
            raise CloudApiError("BAD_RESPONSE", "云端未返回事件流")
        return CloudEventStream(response)

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
    "CloudEventStream",
    "CloudSession",
    "CloudApi",
    "CloudApiError",
    "getCloudApi",
]
