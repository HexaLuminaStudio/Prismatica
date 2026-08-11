# coding: utf-8
"""桌面端价格目录缓存；约每 30 秒检查服务端版本。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.utils import logger

from .cloud_api import getCloudApi
from .responsive_call import runResponsiveCall

PRICE_REFRESH_MS = 30_000
PRICE_REFRESH_MAX_MS = 300_000


class PricingCatalog(QObject):
    catalogChanged = Signal(object)
    refreshFailed = Signal(str)
    _loadedFromWorker = Signal(object)
    _failedFromWorker = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._catalog: Dict[str, Any] = {}
        self._refreshing = False
        self._consecutiveFailures = 0
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pricing-catalog")
        self._loadedFromWorker.connect(self._applyCatalog)
        self._failedFromWorker.connect(self._onFailed)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.refreshAsync)
        self.refreshAsync()

    @property
    def version(self) -> str:
        return str(self._catalog.get("version", ""))

    def rule(self, featureCode: str) -> Dict[str, Any]:
        for item in self._catalog.get("rules", []) or []:
            if item.get("featureCode") == featureCode:
                return dict(item)
        return {}

    def fixedCost(self, featureCode: str) -> int | None:
        rule = self.rule(featureCode)
        if rule.get("billingMode") != "fixed" or not rule.get("enabled", True):
            return None
        return int(rule.get("fixedCost", 0) or 0)

    def meteredCost(self, featureCode: str, resourceUsed: int) -> int | None:
        rule = self.rule(featureCode)
        if rule.get("billingMode") != "metered" or not rule.get("enabled", True):
            return None
        unitSize = max(1, int(rule.get("unitSize", 1) or 1))
        units = max(0, (max(0, int(resourceUsed)) + unitSize - 1) // unitSize)
        cost = int(rule.get("baseCost", 0) or 0) + units * int(
            rule.get("perUnitCost", 0) or 0
        )
        minimum = int(rule.get("minCost", 0) or 0)
        maximum = int(rule.get("maxCost", 1_000_000) or 1_000_000)
        return max(minimum, min(maximum, cost))

    def refreshBlocking(self) -> Dict[str, Any]:
        data = getCloudApi().get("/v1/pricing/catalog", withAuth=False, timeout=5.0) or {}
        if isinstance(data, dict):
            self._applyCatalog(data)
        return dict(self._catalog)

    def refreshResponsive(self) -> Dict[str, Any]:
        """后台获取价格目录，并在调用线程安全地应用新快照。"""
        data = runResponsiveCall(
            lambda: getCloudApi().get(
                "/v1/pricing/catalog",
                withAuth=False,
                timeout=5.0,
            )
            or {}
        )
        if isinstance(data, dict):
            self._applyCatalog(data)
        return dict(self._catalog)

    def refreshAsync(self) -> None:
        if self._refreshing:
            return
        self._timer.stop()
        self._refreshing = True
        future = self._executor.submit(
            getCloudApi().get,
            "/v1/pricing/catalog",
            withAuth=False,
            timeout=5.0,
        )

        def _done(completed) -> None:
            try:
                data = completed.result() or {}
                self._loadedFromWorker.emit(data if isinstance(data, dict) else {})
            except Exception as error:
                self._failedFromWorker.emit(str(error))

        future.add_done_callback(_done)

    def _applyCatalog(self, data: Dict[str, Any]) -> None:
        self._refreshing = False
        wasRecovering = self._consecutiveFailures > 0
        self._consecutiveFailures = 0
        previousVersion = self.version
        self._catalog = dict(data)
        self._timer.start(PRICE_REFRESH_MS)
        if wasRecovering:
            logger.info("[PricingCatalog] 云端价格目录连接已恢复")
        if self.version != previousVersion or not previousVersion:
            self.catalogChanged.emit(dict(self._catalog))

    def _onFailed(self, message: str) -> None:
        self._refreshing = False
        self._consecutiveFailures += 1
        backoffLevel = min(self._consecutiveFailures - 1, 4)
        retryDelayMs = min(
            PRICE_REFRESH_MS * (2**backoffLevel),
            PRICE_REFRESH_MAX_MS,
        )
        self._timer.start(retryDelayMs)
        if self._consecutiveFailures == 1:
            logger.warning(
                f"[PricingCatalog] 刷新失败，将在 {retryDelayMs // 1000} 秒后重试: "
                f"{message}"
            )
        else:
            logger.debug(
                f"[PricingCatalog] 连续刷新失败 {self._consecutiveFailures} 次，"
                f"下次重试等待 {retryDelayMs // 1000} 秒: {message}"
            )
        self.refreshFailed.emit(message)


_singleton: PricingCatalog | None = None


def getPricingCatalog() -> PricingCatalog:
    global _singleton
    if _singleton is None:
        _singleton = PricingCatalog()
    return _singleton


__all__ = [
    "PRICE_REFRESH_MAX_MS",
    "PRICE_REFRESH_MS",
    "PricingCatalog",
    "getPricingCatalog",
]
