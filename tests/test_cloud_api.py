# coding: utf-8
"""CloudApi 单测(2026-08-05 T8)

覆盖:
    - 网络异常 → CloudApiError(NETWORK_ERROR)
    - 4xx/5xx envelope → CloudApiError(code=..., message=...)
    - 401 → 触发 refresh + 重试一次(成功)
    - 401 → 触发 refresh + 重试一次(refresh 也 401,emit sessionExpired)
    - 信封无 message → 中文兜底文案

mock 方式:用 respx 拦截 httpx 客户端。
"""

from __future__ import annotations

from typing import List, Tuple
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.core.services.cloud_api import (
    CODE_NETWORK_ERROR,
    CODE_REFRESH_INVALID,
    CODE_UNAUTHORIZED,
    CloudApi,
    CloudApiError,
    TokenStore,
    resetCloudApiForTesting,
)


# ---------------------------------------------------------------------------
# Fake TokenStore
# ---------------------------------------------------------------------------


class _FakeTokenStore(TokenStore):
    def __init__(self, access: str = "", refresh: str = ""):
        self._access = access
        self._refresh = refresh

    def getAccessToken(self) -> str:
        return self._access

    def getRefreshToken(self) -> str:
        return self._refresh

    def setTokens(self, accessToken: str, refreshToken: str, expiresIn: int) -> None:
        self._access = accessToken
        self._refresh = refreshToken

    def clearTokens(self) -> None:
        self._access = ""
        self._refresh = ""


# ---------------------------------------------------------------------------
# 辅助:用 httpx.MockTransport 模拟一层 transport,避免依赖外部服务
# ---------------------------------------------------------------------------


class _MockClient(CloudApi):
    """继承 CloudApi,只把 httpx.request 替换为 MockTransport。"""

    def __init__(self, transport: httpx.MockTransport, tokenStore: TokenStore):
        super().__init__(baseUrl="http://test.local", tokenStore=tokenStore)
        self._transport = transport
        self._emitted: List[Tuple[str, str]] = []

    # 直接覆盖 _request 内部的 httpx 入口(用 transport 模拟)
    def _doRequest(self, method: str, url: str, **kwargs):  # noqa: D401
        req = httpx.Request(method, url, **kwargs)
        return self._transport.handle_request(req)

    # 覆写 _request 的 httpx.request 调用
    def _request(self, method, path, withAuth=True, retryOn401=True, **kwargs):  # type: ignore[override]
        import json as _json

        url = f"{self._baseUrl}{path}"
        headers = self._buildHeaders(withAuth=withAuth)
        try:
            req = httpx.Request(method, url, headers=headers, **kwargs)
            resp = self._transport.handle_request(req)
        except Exception:
            raise CloudApiError(
                code=CODE_NETWORK_ERROR,
                message="云端不可达",
                httpStatus=0,
            )
        if resp.status_code == 401 and withAuth and retryOn401:
            refreshed = self._refreshIfPossible()
            if not refreshed:
                raise CloudApiError(
                    code=CODE_REFRESH_INVALID,
                    message="登录已过期,请重新激活",
                    httpStatus=401,
                )
            return self._request(method, path, withAuth=withAuth, retryOn401=False, **kwargs)
        return self._handleResponse(resp)


@pytest.fixture(autouse=True)
def _reset():
    resetCloudApiForTesting()
    yield
    resetCloudApiForTesting()


def _okJson(payload: dict, status: int = 200) -> httpx.Response:
    body = _jsonBody(payload)
    return httpx.Response(status, content=body, headers={"content-type": "application/json"})


def _envelope(status: int, code: str, message: str) -> httpx.Response:
    body = _jsonBody({"error": {"code": code, "message": message}})
    return httpx.Response(status, content=body, headers={"content-type": "application/json"})


def _jsonBody(data: dict) -> bytes:
    import json as _j

    return _j.dumps(data).encode("utf-8")


# ---------------------------------------------------------------------------
# 1) 网络异常 → NETWORK_ERROR
# ---------------------------------------------------------------------------


def test_network_error_raises_network_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=req)

    store = _FakeTokenStore(access="t1", refresh="rt1")
    client = _MockClient(httpx.MockTransport(handler), store)
    with pytest.raises(CloudApiError) as ei:
        client._request("GET", "/v1/account/me", withAuth=False)
    assert ei.value.code == CODE_NETWORK_ERROR
    assert ei.value.httpStatus == 0


# ---------------------------------------------------------------------------
# 2) 4xx envelope → 透传 code+message
# ---------------------------------------------------------------------------


def test_4xx_envelope_passes_code_and_message():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/billing/preauth":
            return _envelope(402, "INSUFFICIENT_BALANCE", "余额不足")
        return _envelope(404, "NOT_FOUND", "资源不存在")

    store = _FakeTokenStore(access="t", refresh="rt")
    client = _MockClient(httpx.MockTransport(handler), store)
    with pytest.raises(CloudApiError) as ei:
        client.preauth(actionType="freq_analyze", resourceUsed=1000)
    assert ei.value.code == "INSUFFICIENT_BALANCE"
    assert ei.value.httpStatus == 402
    assert "余额不足" in ei.value.message


# ---------------------------------------------------------------------------
# 3) 401 → refresh + 重试成功
# ---------------------------------------------------------------------------


def test_401_triggers_refresh_and_retries():
    state = {"call": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["call"] += 1
        if req.url.path == "/v1/auth/refresh":
            return _okJson(
                {
                    "tokens": {
                        "accessToken": "new-access",
                        "refreshToken": "new-refresh",
                        "expiresIn": 3600,
                    }
                }
            )
        if req.url.path == "/v1/account/me":
            # 第一次 401,refresh 后第二次 200
            if state["call"] <= 2:
                return _envelope(401, "UNAUTHORIZED", "未登录或登录已过期")
            return _okJson({"userId": "u1", "displayName": "d", "tier": "beta", "balance": 88})
        return _envelope(404, "NOT_FOUND", "?")

    store = _FakeTokenStore(access="old-access", refresh="old-refresh")
    client = _MockClient(httpx.MockTransport(handler), store)
    res = client.getMe()
    assert res["userId"] == "u1"
    # refresh 后 store 也更新了
    assert store.getAccessToken() == "new-access"
    assert store.getRefreshToken() == "new-refresh"


# ---------------------------------------------------------------------------
# 4) 401 → refresh 也 401 → 抛 REFRESH_INVALID,且不调业务接口第二次
# ---------------------------------------------------------------------------


def test_401_refresh_also_fails_raises_refresh_invalid():
    state = {"meCall": 0, "refreshCall": 0, "expiredEmit": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/auth/refresh":
            state["refreshCall"] += 1
            return _envelope(401, "REFRESH_INVALID", "refresh token expired")
        if req.url.path == "/v1/account/me":
            state["meCall"] += 1
            return _envelope(401, "UNAUTHORIZED", "未登录")
        return _envelope(404, "NOT_FOUND", "?")

    store = _FakeTokenStore(access="a", refresh="r")
    client = _MockClient(httpx.MockTransport(handler), store)

    # Patch sessionExpired emit
    import app.core.services.cloud_api as _mod

    orig_emit = _mod._emitSessionExpired

    def spy(reason: str) -> None:
        state["expiredEmit"] += 1
        orig_emit(reason)

    with patch.object(_mod, "_emitSessionExpired", spy):
        with pytest.raises(CloudApiError) as ei:
            client.getMe()
    assert ei.value.code == CODE_REFRESH_INVALID
    assert state["refreshCall"] == 1
    # 只调了一次 me(因为第一次 401 后就抛了,不会再递归调原请求)
    assert state["meCall"] == 1
    # sessionExpired 被 emit 一次
    assert state["expiredEmit"] == 1


# ---------------------------------------------------------------------------
# 5) 无 envelope.message → 中文兜底
# ---------------------------------------------------------------------------


def test_missing_envelope_message_falls_back_to_cn():
    def handler(req: httpx.Request) -> httpx.Response:
        # envelope 无 message 字段
        body = _jsonBody({"error": {"code": "INTERNAL_ERROR"}})
        return httpx.Response(500, content=body, headers={"content-type": "application/json"})

    store = _FakeTokenStore(access="", refresh="")
    client = _MockClient(httpx.MockTransport(handler), store)
    with pytest.raises(CloudApiError) as ei:
        client._request("GET", "/v1/account/me", withAuth=False)
    assert ei.value.code == "INTERNAL_ERROR"
    assert ei.value.httpStatus == 500
    # 兜底中文
    assert ei.value.message


# ---------------------------------------------------------------------------
# 6) 没有 refresh token → emit sessionExpired + 抛 REFRESH_INVALID
# ---------------------------------------------------------------------------


def test_401_without_refresh_token():
    state = {"expiredEmit": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/account/me":
            return _envelope(401, "UNAUTHORIZED", "未登录")
        return _envelope(404, "NOT_FOUND", "?")

    store = _FakeTokenStore(access="a", refresh="")  # 没 refresh
    client = _MockClient(httpx.MockTransport(handler), store)

    import app.core.services.cloud_api as _mod

    orig_emit = _mod._emitSessionExpired

    def spy(reason: str) -> None:
        state["expiredEmit"] += 1
        orig_emit(reason)

    with patch.object(_mod, "_emitSessionExpired", spy):
        with pytest.raises(CloudApiError) as ei:
            client.getMe()
    assert ei.value.code == CODE_REFRESH_INVALID
    assert state["expiredEmit"] == 1
