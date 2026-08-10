# coding: utf-8
"""P0-A 桌面端 CloudBilling:estimate / preauth / settle / refund。

feature_gate 委托本类完成真正的 HTTP 调用;UI 通常不直接调。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from app.core.utils import logger

from .cloud_api import getCloudApi


class CloudBilling:
    """云端计费门面。"""

    def __init__(self) -> None:
        self._api = getCloudApi()

    def estimate(self, actionType: str, resourceUsed: int) -> Dict[str, Any]:
        return (
            self._api.post(
                "/v1/billing/estimate",
                body={"actionType": actionType, "resourceUsed": int(resourceUsed)},
            )
            or {}
        )

    def preauth(
        self,
        actionType: str,
        resourceUsed: int,
        *,
        taskId: str = "",
        description: str = "",
    ) -> Dict[str, Any]:
        # 自动生成 idempotency key(同一资源/动作/参数 → 同一 bill)
        idemKey = str(uuid.uuid4())
        return (
            self._api.post(
                "/v1/billing/preauth",
                body={
                    "actionType": actionType,
                    "resourceUsed": int(resourceUsed),
                    "taskId": taskId,
                    "description": description,
                },
                idempotencyKey=idemKey,
            )
            or {}
        )

    def settle(self, billId: str, realCost: int, resourceUsed: int = 0) -> Dict[str, Any]:
        return (
            self._api.post(
                "/v1/billing/settle",
                body={
                    "billId": billId,
                    "realCost": int(realCost),
                    "resourceUsed": int(resourceUsed),
                },
            )
            or {}
        )

    def commitFixed(self, billId: str) -> Dict[str, Any]:
        return self._api.post("/v1/billing/commit-fixed", body={"billId": billId}) or {}

    def commitMetered(self, billId: str) -> Dict[str, Any]:
        return self._api.post("/v1/billing/commit-metered", body={"billId": billId}) or {}

    def refund(self, billId: str) -> Dict[str, Any]:
        return self._api.post("/v1/billing/refund", body={"billId": billId}) or {}


_singleton: CloudBilling | None = None


def getCloudBilling() -> CloudBilling:
    global _singleton
    if _singleton is None:
        _singleton = CloudBilling()
    return _singleton


__all__ = ["CloudBilling", "getCloudBilling"]
