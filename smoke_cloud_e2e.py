"""桌面端 P0-A 端到端 smoke runner(2026-08-07 P0-A):

完整流程:
    [1] 启动 app context + mock transport
    [2] 登录(邮箱密码)
    [3] 查询 /me(余额 + tier + 订阅)
    [4] AI 洞察发起:estimate → preauth → settle 结算后余额扣减
    [5] 设备列表查询

通过 monkeypatch 替换 CloudApi.request 用预设响应,不走真实 HTTP,但
覆盖完整业务路径(登录态建立 → 查询 → 计费闭环 → 结算后余额),便于
P0-A 完成验收 + 后续 CI 复用。

用法:
    python smoke_cloud_e2e.py
"""
from __future__ import annotations

import sys
from typing import Any


def _buildMockTransport():
    """创建 monkeypatch-able transport,预填完整 e2e 响应链。"""

    class _Transport:
        def __init__(self) -> None:
            self.responses: list[dict[str, Any]] = []
            self.calls: list[dict[str, Any]] = []

        def push(self, body: dict[str, Any]) -> None:
            self.responses.append(body)

        def __call__(self, api, method: str, path: str, body: dict | None = None, **kw) -> Any:
            from app.core.services.cloud_api import CloudApiError

            self.calls.append({"method": method, "path": path, "body": body})
            if not self.responses:
                raise CloudApiError("MOCK_EMPTY", "mock queue empty")
            resp = self.responses.pop(0)
            return api._unwrapEnvelope(resp)

    return _Transport()


def main() -> int:
    # QApplication 必须在所有 QObject 创建之前存在
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    # ---------------- 装配 mock transport ----------------
    transport = _buildMockTransport()

    # 预填响应队列(顺序对应 [2]~[5] 调用)
    # [2] login
    transport.push(
        {
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
                    "accessToken": "smoke-access",
                    "refreshToken": "smoke-refresh",
                    "expiresIn": 3600,
                },
            },
        }
    )
    # [3] /me (含余额 + 订阅)
    transport.push(
        {
            "code": "OK",
            "data": {
                "userId": 1,
                "email": "alice@example.com",
                "displayName": "Alice",
                "tier": "free",
                "status": "active",
                "balance": 100,
                "reserved": 0,
                "available": 100,
                "subscription": None,
            },
        }
    )
    # [4] estimate
    transport.push(
        {
            "code": "OK",
            "data": {
                "actionType": "ai_insight",
                "estimatedCost": 5,
                "currentBalance": 100,
                "affordable": True,
            },
        }
    )
    # [4] preauth
    transport.push(
        {
            "code": "OK",
            "data": {"billId": 42, "status": "pending", "estimatedCost": 5},
        }
    )
    # [4] settle (真实结算)
    transport.push(
        {
            "code": "OK",
            "data": {"billId": 42, "status": "settled", "realCost": 4},
        }
    )
    # [5] devices
    transport.push(
        {
            "code": "OK",
            "data": {
                "items": [
                    {
                        "deviceId": 1,
                        "devicePublicId": "smoke-device-001",
                        "deviceName": "SmokeDesktop",
                        "platform": "windows",
                        "status": "active",
                        "firstSeenAt": "2026-08-07T00:00:00",
                        "lastSeenAt": "2026-08-07T00:00:00",
                        "revokedAt": None,
                        "isCurrent": True,
                    }
                ],
                "maxActive": 3,
                "activeCount": 1,
            },
        }
    )

    # ---------------- monkeypatch CloudApi ----------------
    from app.core.services import getCloudApi
    from app.core.services.cloud_api import CloudApi
    from app.core.services.cloud_auth import CloudAuth
    from app.core.utils.config import cfg

    # 给合法 baseUrl,避免 _baseUrl 抛错
    cfg.cloudBaseUrl.value = "http://test.local"

    def fakeRequest(self, method, path, *, body=None, withAuth=True, **kw):
        return transport(self, method, path, body=body, **kw)

    original_request = CloudApi.request
    CloudApi.request = fakeRequest  # type: ignore[assignment]

    # 不让 saveSession 真写盘
    original_save = CloudAuth._saveSession
    CloudAuth._saveSession = lambda self: None  # type: ignore[assignment]

    try:
        # ---------------- [1] 启动 context ----------------
        print("[1] app context OK")

        # ---------------- [2] 登录 ----------------
        from app.core.services import getCloudAuth

        auth = getCloudAuth()
        api = getCloudApi()
        api.getSession().accessToken = ""  # 起始为空
        data = auth.login("alice@example.com", "Prismatica2026!")
        sess = api.getSession()
        assert sess.accessToken == "smoke-access", sess.accessToken
        assert data["user"]["email"] == "alice@example.com"
        print(f"[2] login OK userId={data['user']['userId']}")

        # ---------------- [3] 查询 /me ----------------
        from app.core.services import getCloudAccount

        account = getCloudAccount()
        me = account.me()
        assert me["balance"] == 100
        assert me["tier"] == "free"
        print(f"[3] me OK balance={me['balance']} tier={me['tier']}")

        # ---------------- [4] AI 扣费闭环 ----------------
        # 4a) estimate
        from app.core.services import getCloudBilling

        billing = getCloudBilling()
        preview = billing.estimate("ai_insight", 5000)
        assert preview["estimatedCost"] == 5
        # 4b) preauth(uuid4 idempotency_key 自动生成)
        preauth = billing.preauth(
            "ai_insight", 5000, taskId="smoke-task-001", description="smoke e2e"
        )
        assert preauth["billId"] == 42
        assert preauth["status"] == "pending"
        # 4c) settle(模拟 LLM 实际消耗 4 积分)
        settled = billing.settle(42, realCost=4)
        assert settled["status"] == "settled"
        assert settled["realCost"] == 4
        print(
            f"[4] billing OK estimate={preview['estimatedCost']} "
            f"preauth billId={preauth['billId']} settle realCost={settled['realCost']}"
        )

        # ---------------- [5] 设备列表 ----------------
        deviceList = account.listDevices()
        assert deviceList["activeCount"] == 1
        assert deviceList["items"][0]["isCurrent"] is True
        print(
            f"[5] devices OK active={deviceList['activeCount']} max={deviceList['maxActive']}"
        )

        # ---------------- 验证 transport 已消费完 ----------------
        assert len(transport.responses) == 0, (
            f"unconsumed mocks: {len(transport.responses)}"
        )
        # 验证调用顺序
        paths = [c["path"] for c in transport.calls]
        assert paths[0] == "/v1/auth/login", paths
        assert paths[1] == "/v1/account/me", paths
        assert paths[2] == "/v1/billing/estimate", paths
        assert paths[3] == "/v1/billing/preauth", paths
        assert paths[4] == "/v1/billing/settle", paths
        assert paths[5] == "/v1/account/devices", paths

        print("\n=== ALL 5 SMOKE STEPS PASSED ===")
        return 0
    finally:
        # 恢复 monkeypatch
        CloudApi.request = original_request  # type: ignore[assignment]
        CloudAuth._saveSession = original_save  # type: ignore[assignment]


if __name__ == "__main__":
    sys.exit(main())