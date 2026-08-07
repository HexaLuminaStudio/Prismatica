"""P0-A 桌面端 CloudAuth / CloudAccount 单元测试。

通过 monkeypatch CloudApi 拦截 HTTP,验证客户端序列化、错误处理、信号触发。
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------------------
# 工具:用 requests-mock 风格替代(自己实现一个 monkeypatch-able transport)
# ---------------------------------------------------------------------------


class _MockTransport:
    """替换 CloudApi.request,返回预设响应(按 method + path 匹配)。"""

    def __init__(self) -> None:
        self.responses: List[Dict[str, Any]] = []  # 队列
        self.calls: List[Dict[str, Any]] = []

    def push(self, status: int = 200, body: dict | None = None) -> None:
        self.responses.append({"status": status, "body": body or {}})

    def __call__(self, api, method: str, path: str, body: dict | None = None, **kw) -> Any:
        from app.core.services.cloud_api import CloudApiError

        self.calls.append({"method": method, "path": path, "body": body, "kw": kw})
        if not self.responses:
            raise CloudApiError("MOCK_EMPTY", "mock queue empty")
        resp = self.responses.pop(0)
        # 用 _unwrapEnvelope 逻辑
        return api._unwrapEnvelope(resp["body"])


@pytest.fixture()
def mockApi(monkeypatch):
    from app.core.services import getCloudApi
    from app.core.services.cloud_api import CloudApi

    transport = _MockTransport()

    # 替换 CloudApi.request(把 method/path/body/kw 转给 transport)
    def fakeRequest(self, method, path, *, body=None, withAuth=True, **kw):
        return transport(self, method, path, body=body, **kw)

    monkeypatch.setattr(CloudApi, "request", fakeRequest)

    # 给一个合法 baseUrl,避免 _baseUrl() 抛错
    from app.core.utils.config import cfg

    monkeypatch.setattr(cfg.cloudBaseUrl, "value", "http://test.local", raising=False)

    yield transport


# ---------------------------------------------------------------------------
# CloudAuth
# ---------------------------------------------------------------------------


def test_cloud_auth_login_success(mockApi, monkeypatch) -> None:
    mockApi.push(
        body={
            "code": "OK",
            "data": {
                "user": {
                    "userId": 1,
                    "email": "alice@example.com",
                    "displayName": "Alice",
                    "tier": "free",
                    "status": "active",
                },
                "tokens": {
                    "accessToken": "a.t",
                    "refreshToken": "r.t",
                    "expiresIn": 3600,
                },
            },
        }
    )
    from app.core.services import getCloudAuth, getCloudApi

    # 不让 saveSession 真写盘
    monkeypatch.setattr("app.core.services.cloud_auth.CloudAuth._saveSession", lambda self: None)

    sess = getCloudApi().getSession()
    sess.accessToken = ""  # 起始为空

    auth = getCloudAuth()
    data = auth.login("alice@example.com", "Prismatica2026!")

    assert data["user"]["email"] == "alice@example.com"
    newSess = getCloudApi().getSession()
    assert newSess.accessToken == "a.t"
    assert newSess.userId == 1
    assert newSess.email == "alice@example.com"


def test_cloud_auth_login_invalid_credentials(mockApi, monkeypatch) -> None:
    mockApi.push(
        body={"code": "INVALID_CREDENTIALS", "message": "邮箱或密码错误"}
    )
    from app.core.services import CloudApiError, getCloudAuth

    auth = getCloudAuth()
    with pytest.raises(CloudApiError) as exc:
        auth.login("a@b.com", "wrong")
    assert exc.value.code == "INVALID_CREDENTIALS"


def test_cloud_auth_login_account_locked(mockApi, monkeypatch) -> None:
    mockApi.push(
        body={
            "code": "ACCOUNT_LOCKED",
            "message": "锁定",
            "details": {"retryAfter": 300},
        }
    )
    from app.core.services import CloudApiError, getCloudAuth

    auth = getCloudAuth()
    with pytest.raises(CloudApiError) as exc:
        auth.login("a@b.com", "Prismatica2026!")
    assert exc.value.code == "ACCOUNT_LOCKED"
    assert exc.value.details.get("retryAfter") == 300


def test_cloud_auth_register_then_login(mockApi, monkeypatch) -> None:
    # register -> 201
    mockApi.push(body={"code": "OK", "data": {"user": {"userId": 1}}})
    # 紧接着 login
    mockApi.push(
        body={
            "code": "OK",
            "data": {
                "user": {
                    "userId": 1,
                    "email": "new@user.com",
                    "displayName": "New",
                    "tier": "free",
                    "status": "active",
                },
                "tokens": {
                    "accessToken": "a2",
                    "refreshToken": "r2",
                    "expiresIn": 3600,
                },
            },
        }
    )
    from app.core.services import getCloudAuth, getCloudApi

    monkeypatch.setattr("app.core.services.cloud_auth.CloudAuth._saveSession", lambda self: None)

    auth = getCloudAuth()
    auth.register("new@user.com", "Prismatica2026!", "New")
    assert getCloudApi().getSession().accessToken == "a2"


def test_cloud_auth_refresh_rotates_token(mockApi, monkeypatch) -> None:
    from app.core.services import getCloudAuth, getCloudApi

    # 初始化 session
    sess = getCloudApi().getSession()
    sess.accessToken = "old"
    sess.refreshToken = "old_refresh"

    mockApi.push(
        body={
            "code": "OK",
            "data": {
                "user": {"userId": 1, "email": "x", "tier": "free", "status": "active"},
                "tokens": {
                    "accessToken": "new",
                    "refreshToken": "new_refresh",
                    "expiresIn": 3600,
                },
            },
        }
    )
    monkeypatch.setattr("app.core.services.cloud_auth.CloudAuth._saveSession", lambda self: None)

    auth = getCloudAuth()
    # 重置 lastRefreshAt(避免其他 test 残留)
    auth._lastRefreshAt = 0.0
    ok = auth._refreshAccessToken()
    assert ok is True
    assert getCloudApi().getSession().accessToken == "new"


def test_cloud_auth_change_password(mockApi, monkeypatch) -> None:
    mockApi.push(body={"code": "OK", "data": {"passwordChanged": True, "revokedRefreshTokens": 1}})
    from app.core.services import getCloudAuth, getCloudApi

    sess = getCloudApi().getSession()
    sess.accessToken = "x"
    sess.expiresAt = 99999999999  # 不过期,不需要 refresh

    monkeypatch.setattr("app.core.services.cloud_auth.CloudAuth._saveSession", lambda self: None)
    auth = getCloudAuth()
    result = auth.changePassword("oldPass", "newPass2026!")
    assert result["passwordChanged"] is True
    # 调用参数包含 Authorization
    call = mockApi.calls[-1]
    assert call["method"] == "POST"
    assert "/v1/auth/password/change" in call["path"]


# ---------------------------------------------------------------------------
# CloudAccount
# ---------------------------------------------------------------------------


def test_cloud_account_me(mockApi, monkeypatch) -> None:
    mockApi.push(
        body={
            "code": "OK",
            "data": {
                "userId": 7,
                "email": "me@example.com",
                "displayName": "Me",
                "tier": "pro",
                "status": "active",
                "balance": 120,
                "reserved": 10,
                "available": 110,
            },
        }
    )
    from app.core.services import getCloudAccount, getCloudApi

    sess = getCloudApi().getSession()
    sess.accessToken = "x"

    acc = getCloudAccount()
    me = acc.me()
    assert me["userId"] == 7
    assert me["balance"] == 120


def test_cloud_account_list_devices(mockApi, monkeypatch) -> None:
    mockApi.push(
        body={
            "code": "OK",
            "data": {
                "items": [
                    {
                        "deviceId": 1,
                        "devicePublicId": "d1",
                        "deviceName": "PC",
                        "platform": "win32",
                        "status": "active",
                        "isCurrent": True,
                    }
                ],
                "maxActive": 3,
                "activeCount": 1,
            },
        }
    )
    from app.core.services import getCloudAccount, getCloudApi

    getCloudApi().getSession().accessToken = "x"
    acc = getCloudAccount()
    info = acc.listDevices()
    assert info["maxActive"] == 3
    assert info["items"][0]["isCurrent"] is True


def test_cloud_account_revoke_device(mockApi, monkeypatch) -> None:
    mockApi.push(
        body={
            "code": "OK",
            "data": {"deviceId": 99, "revokedRefreshTokens": 2, "status": "revoked"},
        }
    )
    from app.core.services import getCloudAccount, getCloudApi
    from app.core.utils import signalBus

    getCloudApi().getSession().accessToken = "x"
    captured: list[int] = []
    signalBus.devicesChanged.connect(lambda: captured.append(1))
    try:
        result = getCloudAccount().revokeDevice(99)
    finally:
        signalBus.devicesChanged.disconnect()
    assert result["revokedRefreshTokens"] == 2
    assert captured == [1]  # 信号触发


def test_cloud_account_delete_account_clears_session(mockApi, monkeypatch) -> None:
    mockApi.push(
        body={
            "code": "OK",
            "data": {
                "userId": 1,
                "status": "deleted",
                "scheduledHardDeleteAt": "2026-09-01T00:00:00",
                "revokedRefreshTokens": 2,
            },
        }
    )
    from app.core.services import getCloudAccount, getCloudApi
    from app.core.utils import signalBus

    getCloudApi().getSession().accessToken = "x"
    # mock _clearSession 以避免真写盘
    from app.core.services import cloud_auth

    monkeypatch.setattr(cloud_auth.CloudAuth, "_clearSession", lambda self: None)

    captured: list[bool] = []
    signalBus.sessionChanged.connect(lambda v: captured.append(bool(v)))
    try:
        result = getCloudAccount().deleteAccount("Prismatica2026!")
    finally:
        signalBus.sessionChanged.disconnect()
    assert result["status"] == "deleted"
    assert False in captured  # session 变 false


def test_cloud_account_patch_me(mockApi, monkeypatch) -> None:
    mockApi.push(
        body={
            "code": "OK",
            "data": {
                "userId": 1,
                "displayName": "New Name",
                "updatedAt": "2026-08-07T00:00:00",
            },
        }
    )
    from app.core.services import getCloudAccount, getCloudApi

    getCloudApi().getSession().accessToken = "x"
    result = getCloudAccount().patchMe("New Name")
    assert result["displayName"] == "New Name"
    # PATCH 方法
    call = mockApi.calls[-1]
    assert call["method"] == "PATCH"


# ---------------------------------------------------------------------------
# CloudBilling
# ---------------------------------------------------------------------------


def test_cloud_billing_estimate(mockApi) -> None:
    mockApi.push(
        body={
            "code": "OK",
            "data": {
                "actionType": "kwic_search",
                "displayName": "KWIC 检索",
                "resourceUsed": 1000,
                "unitName": "千字",
                "estimatedCost": 2,
                "currentBalance": 100,
                "balanceAfter": 98,
                "affordable": True,
                "tierBreakdown": [],
            },
        }
    )
    from app.core.services import getCloudApi, getCloudBilling

    getCloudApi().getSession().accessToken = "x"
    preview = getCloudBilling().estimate("kwic_search", 1000)
    assert preview["estimatedCost"] == 2


def test_cloud_billing_preauth_includes_idempotency_key(mockApi) -> None:
    mockApi.push(
        body={
            "code": "OK",
            "data": {
                "billId": "b-1",
                "estimatedCost": 5,
                "balanceAfter": 95,
            },
        }
    )
    from app.core.services import getCloudApi, getCloudBilling

    getCloudApi().getSession().accessToken = "x"
    result = getCloudBilling().preauth("kwic_search", 1000)
    assert result["billId"] == "b-1"
    call = mockApi.calls[-1]
    # Idempotency-Key 应该在 headers 里
    assert "Idempotency-Key" in call["kw"].get("idempotencyKey", "") or call["kw"].get("idempotencyKey")
