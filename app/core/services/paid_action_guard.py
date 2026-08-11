# coding: utf-8
"""收费动作防重入租约与稳定操作标识。"""
from __future__ import annotations

import threading
import uuid
from typing import Optional

from PySide6.QtWidgets import QWidget

from app.core.utils import logger


def buildPaidActionKey(
    featureCode: str,
    resourceUsed: int,
    description: str,
) -> str:
    """把一次收费意图规范化为进程内防重入键。"""
    normalizedDescription = " ".join(str(description or "").split())
    return f"{str(featureCode).strip()}|{max(0, int(resourceUsed))}|{normalizedDescription}"


class PaidActionLease:
    """覆盖预占、业务执行和结算/退款全过程的收费动作租约。"""

    def __init__(
        self,
        registry: "PaidActionRegistry",
        actionKey: str,
        operationId: str,
    ) -> None:
        self._registry = registry
        self.actionKey = actionKey
        self.operationId = operationId
        self._released = False
        self._stateLock = threading.Lock()

    @property
    def isReleased(self) -> bool:
        with self._stateLock:
            return self._released

    def release(self) -> None:
        """幂等释放租约；只有持有同一 operationId 的调用可以释放。"""
        with self._stateLock:
            if self._released:
                return
            self._released = True
        self._registry._release(self.actionKey, self.operationId)


class PaidActionRegistry:
    """维护当前进程内仍未完成的收费意图。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._activeOperations: dict[str, str] = {}

    def tryAcquire(
        self,
        featureCode: str,
        resourceUsed: int,
        description: str,
    ) -> Optional[PaidActionLease]:
        """尝试获取租约；同一收费意图仍活跃时返回 ``None``。"""
        actionKey = buildPaidActionKey(featureCode, resourceUsed, description)
        with self._lock:
            if actionKey in self._activeOperations:
                return None
            operationId = str(uuid.uuid4())
            self._activeOperations[actionKey] = operationId
        return PaidActionLease(self, actionKey, operationId)

    def isActive(
        self,
        featureCode: str,
        resourceUsed: int,
        description: str,
    ) -> bool:
        """返回指定收费意图当前是否已持有租约。"""
        actionKey = buildPaidActionKey(featureCode, resourceUsed, description)
        with self._lock:
            return actionKey in self._activeOperations

    def _release(self, actionKey: str, operationId: str) -> None:
        with self._lock:
            if self._activeOperations.get(actionKey) != operationId:
                return
            self._activeOperations.pop(actionKey, None)


def notifyPaidActionBusy(
    parent: Optional[QWidget],
    description: str,
) -> None:
    """以非模态提示告知用户已有相同收费动作正在处理。"""
    logger.info(f"[PaidActionGuard] 阻止重复收费动作: {description}")
    if parent is None:
        return
    try:
        from qfluentwidgets import InfoBar, InfoBarPosition

        InfoBar.warning(
            title="操作正在处理中",
            content=f"“{description}”正在预占、执行或结算，请勿重复提交。",
            parent=parent,
            duration=2500,
            position=InfoBarPosition.TOP,
        )
    except Exception:
        logger.exception("[PaidActionGuard] 显示重复操作提示失败")


paidActionRegistry = PaidActionRegistry()


__all__ = [
    "PaidActionLease",
    "PaidActionRegistry",
    "buildPaidActionKey",
    "notifyPaidActionBusy",
    "paidActionRegistry",
]
