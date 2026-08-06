# coding: utf-8
"""云端 HTTP 客户端(2026-08-05 T1 重构)

封装 FastAPI/Flask 后端 11 个端点(对齐后端 PRD §5):
    - 认证:/v1/auth/{redeem, refresh, logout}
    - 账户:/v1/account/{me, bills}
    - 计费:/v1/billing/{estimate, preauth, settle, refund}
    - 健康:/healthz(可选)

关键能力:
    - httpx 同步客户端(无需 async 改造现有 PySide6 服务)
    - 自动 JWT 管理(从 license.enc 读 / 自动 refresh)
    - 错误码映射(后端 envelope → GatewayError,含 machine-readable code)
    - 网络异常 → 抛 NETWORK_ERROR
    - X-Device-Id / X-Client-Version 标准头
    - refresh 接口本身失败 → 统一 emit signalBus.sessionExpired
    - 401 重试不再裸发 httpx,而是递归调用 _request(retry_on_401=False)

兼容:CloudApiError / CloudApi / getCloudApi 名称保留,所有现有调用方
不需要改 import。
"""

from __future__ import annotations

import platform as _platform
from typing import Any, Optional

import httpx
from loguru import logger

from app.core.services.cloud_config import getCloudConfig
from app.core.services.cloud_device import getOrCreateDeviceId
from app.core.utils.error_messages_cn import pickMessage
from app.core.utils.setting import APP_NAME, VERSION


# ---------------------------------------------------------------------------
# 错误码(对齐后端 envelope,机器可读)
# 保留旧命名 CODE_* 以兼容现有调用方
# ---------------------------------------------------------------------------

CODE_INVALID_CODE = "INVALID_CODE"
CODE_EXPIRED = "EXPIRED"
CODE_ALREADY_USED = "ALREADY_USED"
CODE_ALREADY_AUTHENTICATED = "ALREADY_AUTHENTICATED"
CODE_NEED_ACTIVATION = "NEED_ACTIVATION"
CODE_INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
CODE_BILL_NOT_FOUND = "BILL_NOT_FOUND"
CODE_BILL_ALREADY_SETTLED = "BILL_ALREADY_SETTLED"
CODE_BILL_NOT_PENDING = "BILL_NOT_PENDING"
CODE_RATE_LIMITED = "RATE_LIMITED"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"
CODE_NETWORK_ERROR = "NETWORK_ERROR"
CODE_REFRESH_INVALID = "REFRESH_INVALID"
CODE_REFRESH_EXPIRED = "REFRESH_EXPIRED"
CODE_UNAUTHORIZED = "UNAUTHORIZED"


class CloudApiError(Exception):
    """云端 API 业务错误。

    Attributes:
        code:       机器可读错误码(对齐后端 PRD §6)
        message:    给 UI 用的中文文案(后端返回优先,本地兜底其次)
        httpStatus: HTTP 状态码(0 = 网络异常)
        details:    后端返回的 details 字段(可选)
    """

    def __init__(
        self,
        code: str,
        message: str,
        httpStatus: int = 0,
        details: Optional[dict] = None,
    ):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.httpStatus = httpStatus
        self.details = details or {}

    # 兼容旧属性名(GatewayError / 抽象统一阶段也保留一段时间)
    @property
    def statusCode(self) -> int:
        return self.httpStatus


# 兼容别名(后续改名阶段使用,先简单 alias)
GatewayError = CloudApiError


# ---------------------------------------------------------------------------
# Token 存储(委托给 AuthService,避免循环 import)
# ---------------------------------------------------------------------------


class TokenStore:
    """JWT 存储抽象(默认实现绑定到 AuthService 的 license.enc)。"""

    def getAccessToken(self) -> Optional[str]:
        return None

    def getRefreshToken(self) -> Optional[str]:
        return None

    def setTokens(self, accessToken: str, refreshToken: str, expiresIn: int) -> None:
        pass

    def clearTokens(self) -> None:
        pass


def _defaultTokenStore() -> TokenStore:
    """默认 TokenStore:绑定到 AuthService(避免循环 import)。"""
    from app.core.services.auth_service import getAuthService

    auth = getAuthService()

    class _AuthTokenStore(TokenStore):
        def getAccessToken(self) -> Optional[str]:
            return auth.getAccessToken()

        def getRefreshToken(self) -> Optional[str]:
            return auth.getRefreshToken()

        def setTokens(
            self,
            accessToken: str,
            refreshToken: str,
            expiresIn: int,
        ) -> None:
            auth.setTokens(accessToken, refreshToken, expiresIn)

        def clearTokens(self) -> None:
            auth.clearTokens()

    return _AuthTokenStore()


def _emitSessionExpired(reason: str) -> None:
    """集中发送「会话失效」信号(供 _request 401 重试路径调用)。"""
    try:
        from app.core.utils.signal_bus import signalBus

        signalBus.sessionExpired.emit(reason)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[CloudApi] sessionExpired 信号发送失败(忽略): {e}")


# ---------------------------------------------------------------------------
# CloudApi 客户端
# ---------------------------------------------------------------------------


class CloudApi:
    """云端 HTTP 客户端。

    单例入口由 getCloudApi() 提供。
    """

    def __init__(
        self,
        baseUrl: Optional[str] = None,
        tokenStore: Optional[TokenStore] = None,
        clientVersion: str = VERSION,
    ):
        cfg = getCloudConfig()
        self._baseUrl = (baseUrl or cfg.baseUrl).rstrip("/")
        self._timeout = cfg.timeoutSec
        self._retryTimes = max(0, int(getattr(cfg, "retryTimes", 1) or 1))
        self._retryBackoffSec = float(getattr(cfg, "retryBackoffSec", 1.0) or 1.0)
        self._clientVersion = clientVersion
        self._tokenStore = tokenStore or _defaultTokenStore()
        self._deviceId = getOrCreateDeviceId()

    # ============================================================
    # 公开 API
    # ============================================================

    def redeem(
        self,
        code: str,
        deviceId: Optional[str] = None,
        displayName: str = "内测用户",
        deviceName: str = "",
    ) -> dict:
        """兑换凭证(INV/TRY/RCH)。"""
        body = {
            "code": code,
            "deviceId": deviceId if deviceId is not None else self._deviceId,
            "displayName": displayName,
            "deviceName": deviceName or self._detectDeviceName(),
        }
        resp = self._request("POST", "/v1/auth/redeem", withAuth=False, json=body)
        self._storeTokensFromResponse(resp)
        return resp

    def refresh(self, refreshToken: str) -> dict:
        """刷新 accessToken(由 _request 在 401 时内部调用,或外部主动调)。"""
        resp = self._request(
            "POST",
            "/v1/auth/refresh",
            withAuth=False,
            retryOn401=False,  # refresh 接口本身不能再 refresh
            json={"refreshToken": refreshToken},
        )
        self._storeTokensFromResponse(resp)
        return resp

    def logout(self) -> None:
        """best-effort 注销(失败也不抛错)。"""
        try:
            self._request("POST", "/v1/auth/logout", withAuth=True, json={})
        except CloudApiError:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                self._tokenStore.clearTokens()
            except Exception:  # noqa: BLE001
                pass

    # ---------- 账户 ----------

    def getMe(self) -> dict:
        return self._request("GET", "/v1/account/me")

    def listBills(self, cursor: str = "", limit: int = 50) -> dict:
        params: dict[str, Any] = {"limit": int(limit)}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/v1/account/bills", params=params)

    # ---------- 计费 ----------

    def estimate(self, actionType: str, resourceUsed: int) -> dict:
        return self._request(
            "POST",
            "/v1/billing/estimate",
            json={"actionType": actionType, "resourceUsed": int(resourceUsed)},
        )

    def preauth(
        self,
        actionType: str,
        resourceUsed: int,
        taskId: str = "",
        description: str = "",
    ) -> dict:
        return self._request(
            "POST",
            "/v1/billing/preauth",
            json={
                "actionType": actionType,
                "resourceUsed": int(resourceUsed),
                "taskId": taskId,
                "description": description,
            },
        )

    def settle(self, billId: str, realCost: int, resourceUsed: int) -> dict:
        return self._request(
            "POST",
            "/v1/billing/settle",
            json={
                "billId": billId,
                "realCost": int(realCost),
                "resourceUsed": int(resourceUsed),
            },
        )

    def refund(self, billId: str) -> dict:
        return self._request("POST", "/v1/billing/refund", json={"billId": billId})

    # ---------- 健康检查 ----------

    def health(self) -> dict:
        return self._request("GET", "/healthz", withAuth=False)

    # ============================================================
    # 内部:HTTP 请求主入口
    # ============================================================

    def _request(
        self,
        method: str,
        path: str,
        withAuth: bool = True,
        retryOn401: bool = True,
        **kwargs,
    ) -> dict:
        """HTTP 请求核心逻辑(2026-08-05 T1 重构)。

        - 自动加 Bearer JWT(withAuth=True)
        - 401 + retryOn401=True → 自动 refresh + 递归重试一次
        - refresh 接口内部用 retryOn401=False 防止递归
        - refresh 失败 → emit sessionExpired,抛 CloudApiError(REFRESH_INVALID)
        - 4xx/5xx → 抛 CloudApiError(从 envelope 解析 code/message)
        - 网络异常 → 抛 CloudApiError(NETWORK_ERROR, httpStatus=0)
        """
        url = f"{self._baseUrl}{path}"
        headers = self._buildHeaders(withAuth=withAuth)
        try:
            resp = httpx.request(
                method,
                url,
                headers=headers,
                timeout=self._timeout,
                **kwargs,
            )
        except httpx.RequestError as e:
            logger.warning(f"[CloudApi] 网络异常 {method} {path}: {e}")
            raise CloudApiError(
                code=CODE_NETWORK_ERROR,
                message=pickMessage(None, 0),
                httpStatus=0,
            )

        # 401 + retryOn401=True → 尝试 refresh + 递归一次
        if resp.status_code == 401 and withAuth and retryOn401:
            refreshed = self._refreshIfPossible()
            if not refreshed:
                raise CloudApiError(
                    code=CODE_REFRESH_INVALID,
                    message=pickMessage("登录已过期,请重新激活", 401),
                    httpStatus=401,
                )
            # 用新 token 递归一次(refresh 接口本身 retryOn401=False,不会再来一轮)
            return self._request(method, path, withAuth=withAuth, retryOn401=False, **kwargs)

        # 解析响应 envelope
        return self._handleResponse(resp)

    def _refreshIfPossible(self) -> bool:
        """尝试 refresh 一次。返回 True 表示拿到了新 token,False 表示失败。

        失败原因:
            - 没有 refresh token
            - refresh 接口 401(refresh token 过期/无效)
            - 网络异常

        任何失败:统一 _emitSessionExpired + 返回 False,让 _request 抛出 REFRESH_INVALID。
        """
        rt = self._tokenStore.getRefreshToken()
        if not rt:
            _emitSessionExpired("登录已过期,请重新激活")
            return False
        try:
            self.refresh(rt)  # 内部 retryOn401=False,失败会抛 CloudApiError
            return True
        except CloudApiError as e:
            logger.warning(f"[CloudApi] refresh 失败: {e.code} {e.message}")
            _emitSessionExpired(
                "登录已过期,请重新激活"
                if e.code == CODE_REFRESH_INVALID
                else f"刷新登录态失败:{e.message}"
            )
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[CloudApi] refresh 异常: {e}")
            _emitSessionExpired(f"刷新登录态异常:{e}")
            return False

    def _storeTokensFromResponse(self, resp: dict) -> None:
        """从 redeem/refresh 响应里提取 tokens,写入 TokenStore。"""
        tokens = (resp or {}).get("tokens") or {}
        if tokens.get("accessToken"):
            try:
                self._tokenStore.setTokens(
                    accessToken=tokens["accessToken"],
                    refreshToken=tokens.get("refreshToken", ""),
                    expiresIn=int(tokens.get("expiresIn", 3600) or 3600),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[CloudApi] 持久化 token 失败(忽略): {e}")

    def _buildHeaders(self, withAuth: bool) -> dict[str, str]:
        headers: dict[str, str] = {
            "X-Device-Id": self._deviceId,
            "X-Client-Version": f"{APP_NAME}-{self._clientVersion}",
            "Content-Type": "application/json",
        }
        if withAuth:
            access = self._tokenStore.getAccessToken()
            if access:
                headers["Authorization"] = f"Bearer {access}"
        return headers

    def _handleResponse(self, resp: httpx.Response) -> dict:
        """统一处理 HTTP 响应(2026-08-06 适配后端 envelope):

        - 2xx:返回 `data` 字段(后端 envelope = {code, data, requestId})
              若 body 不是 envelope 形态(老接口/健康检查),回退到原 dict 兜底
        - 4xx/5xx:从顶层 code/message 解出 CloudApiError(后端已统一为顶层 envelope)
        """
        if resp.status_code >= 200 and resp.status_code < 300:
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                return {}

            # 兼容老接口:body 没有 code/data 字段就当成裸数据返回
            if not isinstance(body, dict) or "code" not in body or "data" not in body:
                return body if isinstance(body, dict) else {}
            return body.get("data") or {}

        # 尝试解析 error envelope(2026-08-06 起为顶层 code/message,兼容旧 error.* 结构)
        try:
            data = resp.json()
            if isinstance(data, dict) and "code" in data and "message" in data:
                code = data.get("code") or CODE_INTERNAL_ERROR
                msg = data.get("message") or resp.text or ""
                details = data.get("details") or {}
            else:
                err = (data or {}).get("error") or {}
                code = err.get("code") or CODE_INTERNAL_ERROR
                msg = err.get("message") or resp.text or ""
                details = err.get("details") or {}
        except Exception:  # noqa: BLE001
            code = _statusToCode(resp.status_code)
            msg = resp.text or ""
            details = {}

        # HTTP → 业务码映射(给 UI 一致的语义)
        if resp.status_code == 401:
            code = CODE_UNAUTHORIZED
        elif resp.status_code == 402:
            code = CODE_INSUFFICIENT_BALANCE
        elif resp.status_code == 429:
            code = CODE_RATE_LIMITED

        finalMessage = pickMessage(msg, resp.status_code)

        try:
            method = resp.request.method
            path = resp.request.url.path
        except Exception:  # noqa: BLE001
            method = "?"
            path = "?"

        logger.warning(
            f"[CloudApi] {method} {path} "
            f"→ {resp.status_code} [{code}] {finalMessage}"
        )
        raise CloudApiError(
            code=code,
            message=finalMessage,
            httpStatus=resp.status_code,
            details=details,
        )

    def _detectDeviceName(self) -> str:
        try:
            return f"{_platform.system()}-{_platform.node()}"[:64]
        except Exception:  # noqa: BLE001
            return ""


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _statusToCode(status: int) -> str:
    return {
        400: CODE_INVALID_CODE,
        401: CODE_UNAUTHORIZED,
        402: CODE_INSUFFICIENT_BALANCE,
        404: "NOT_FOUND",
        409: "CONFLICT",
        429: CODE_RATE_LIMITED,
        500: CODE_INTERNAL_ERROR,
        502: CODE_INTERNAL_ERROR,
        503: CODE_INTERNAL_ERROR,
        504: CODE_INTERNAL_ERROR,
    }.get(status, CODE_INTERNAL_ERROR)


# ---------------------------------------------------------------------------
# 单例 / 测试钩子
# ---------------------------------------------------------------------------


_cloudApiInstance: Optional[CloudApi] = None


def getCloudApi() -> CloudApi:
    """获取全局 CloudApi 单例。"""
    global _cloudApiInstance
    if _cloudApiInstance is None:
        _cloudApiInstance = CloudApi()
    return _cloudApiInstance


def resetCloudApiForTesting() -> None:
    """仅供测试:重置单例。"""
    global _cloudApiInstance
    _cloudApiInstance = None


def makeCloudApi(baseUrl: str, tokenStore: Optional[TokenStore] = None) -> CloudApi:
    """仅供测试/脚本:创建指定 baseUrl 的独立实例。"""
    return CloudApi(baseUrl=baseUrl, tokenStore=tokenStore)


__all__ = [
    "CloudApi",
    "CloudApiError",
    "GatewayError",
    "TokenStore",
    "getCloudApi",
    "resetCloudApiForTesting",
    "makeCloudApi",
    # 错误码常量
    "CODE_INVALID_CODE",
    "CODE_EXPIRED",
    "CODE_ALREADY_USED",
    "CODE_ALREADY_AUTHENTICATED",
    "CODE_NEED_ACTIVATION",
    "CODE_INSUFFICIENT_BALANCE",
    "CODE_BILL_NOT_FOUND",
    "CODE_BILL_ALREADY_SETTLED",
    "CODE_BILL_NOT_PENDING",
    "CODE_RATE_LIMITED",
    "CODE_INTERNAL_ERROR",
    "CODE_NETWORK_ERROR",
    "CODE_REFRESH_INVALID",
    "CODE_REFRESH_EXPIRED",
    "CODE_UNAUTHORIZED",
]
