# coding: utf-8
"""P0-A 桌面端 CloudAccount:me / patch / devices / revoke / delete / 订阅。"""
from __future__ import annotations

from typing import Any, Dict, List

from app.core.utils import logger, signalBus

from .cloud_api import CloudApi, getCloudApi


class CloudAccount:
    """云端账号门面。"""

    def __init__(self) -> None:
        self._api = getCloudApi()

    # ------------------------------------------------------------------
    # /me
    # ------------------------------------------------------------------

    def me(self) -> Dict[str, Any]:
        """GET /v1/account/me:用户态 + 余额 + 订阅。"""
        data = self._api.get("/v1/account/me")
        if isinstance(data, dict):
            # 通知余额变化(供 UI 头像红点 / 抽屉数字)
            try:
                balance = int(data.get("balance", 0) or 0)
                signalBus.balanceChanged.emit(balance)
            except Exception:
                pass
        return data or {}

    def patchMe(self, displayName: str) -> Dict[str, Any]:
        return self._api.patch(
            "/v1/account/me",
            body={"displayName": displayName},
        ) or {}

    # ------------------------------------------------------------------
    # 设备
    # ------------------------------------------------------------------

    def listDevices(self) -> Dict[str, Any]:
        return self._api.get("/v1/account/devices") or {
            "items": [],
            "maxActive": 3,
            "activeCount": 0,
        }

    def revokeDevice(self, deviceRecordId: int) -> Dict[str, Any]:
        result = self._api.delete(f"/v1/account/devices/{int(deviceRecordId)}") or {}
        try:
            signalBus.devicesChanged.emit()
        except Exception:
            pass
        return result

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------

    def listSubscriptions(self, *, limit: int = 50, cursor: str | None = None) -> Dict[str, Any]:
        params = f"?limit={int(limit)}"
        if cursor:
            params += f"&cursor={cursor}"
        return self._api.get(f"/v1/account/subscriptions{params}") or {
            "items": [],
            "nextCursor": None,
        }

    # ------------------------------------------------------------------
    # 账单
    # ------------------------------------------------------------------

    def listBills(self, *, limit: int = 50, cursor: str | None = None) -> Dict[str, Any]:
        params = f"?limit={int(limit)}"
        if cursor:
            params += f"&cursor={cursor}"
        return self._api.get(f"/v1/account/bills{params}") or {
            "items": [],
            "nextCursor": None,
        }

    # ------------------------------------------------------------------
    # 注销
    # ------------------------------------------------------------------

    def deleteAccount(self, password: str) -> Dict[str, Any]:
        result = self._api.post(
            "/v1/account/delete",
            body={"password": password, "confirm": True},
        ) or {}
        # 注销后清空本地会话
        try:
            from .cloud_auth import getCloudAuth

            getCloudAuth()._clearSession()
            signalBus.sessionChanged.emit(False)
        except Exception:
            pass
        return result


_singleton: CloudAccount | None = None


def getCloudAccount() -> CloudAccount:
    global _singleton
    if _singleton is None:
        _singleton = CloudAccount()
    return _singleton


__all__ = ["CloudAccount", "getCloudAccount"]
