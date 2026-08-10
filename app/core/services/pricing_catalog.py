# coding: utf-8
"""桌面端价格目录缓存；约每 30 秒检查服务端版本。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.utils import logger

from .cloud_api import getCloudApi

PRICE_REFRESH_MS = 30_000


class PricingCatalog(QObject):
    catalogChanged = Signal(object)
    refreshFailed = Signal(str)
    _loadedFromWorker = Signal(object)
    _failedFromWorker = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._catalog: Dict[str, Any] = {}
        self._refreshing = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pricing-catalog")
        self._loadedFromWorker.connect(self._applyCatalog)
        self._failedFromWorker.connect(self._onFailed)
        self._timer = QTimer(self)
        self._timer.setInterval(PRICE_REFRESH_MS)
        self._timer.timeout.connect(self.refreshAsync)
        self._timer.start()
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

    def refreshAsync(self) -> None:
        if self._refreshing:
            return
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
        previousVersion = self.version
        self._catalog = dict(data)
        if self.version != previousVersion or not previousVersion:
            self.catalogChanged.emit(dict(self._catalog))

    def _onFailed(self, message: str) -> None:
        self._refreshing = False
        logger.warning(f"[PricingCatalog] 刷新失败: {message}")
        self.refreshFailed.emit(message)


_singleton: PricingCatalog | None = None


def getPricingCatalog() -> PricingCatalog:
    global _singleton
    if _singleton is None:
        _singleton = PricingCatalog()
    return _singleton


__all__ = ["PRICE_REFRESH_MS", "PricingCatalog", "getPricingCatalog"]
