# coding: utf-8
"""语料分析固定价导出事务。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QWidget
from qfluentwidgets import MessageBox

from app.core.utils import logger, signalBus

from .cloud_api import CloudApiError, getCloudApi
from .cloud_billing import getCloudBilling
from .feature_gate import GateResult, getFeatureGate
from .pricing_catalog import getPricingCatalog

ANALYSIS_EXPORT_FEATURE = "analysis_export"


class PaidExportTransaction:
    """预占成功后的单次导出事务；必须 commit 或 refund。"""

    def __init__(self, result: GateResult) -> None:
        self._result = result
        self._finished = False

    @property
    def estimatedCost(self) -> int:
        return int(self._result.context.get("estimatedCost", 0) or 0)

    def commit(self) -> bool:
        if self._finished:
            return False
        billId = str((self._result.context.get("preauth") or {}).get("billId", ""))
        for attempt in range(2):
            try:
                settled = getCloudBilling().commitFixed(billId)
                signalBus.balanceChanged.emit(int(settled.get("balanceAfter", 0) or 0))
                self._finished = True
                return True
            except CloudApiError as error:
                logger.warning(f"[PaidExport] 固定价结算失败 attempt={attempt + 1}: {error}")
        self.refund()
        return False

    def refund(self) -> None:
        if self._finished:
            return
        refundFn = self._result.context.get("refund")
        try:
            if refundFn is not None:
                refundFn()
        except Exception:
            logger.exception("[PaidExport] 释放导出预占失败")
        finally:
            self._finished = True


def beginPaidAnalysisExport(
    parent: Optional[QWidget],
    description: str,
) -> PaidExportTransaction | None:
    """确认公开固定价并完成预占；取消、未登录或余额不足时返回 None。"""
    catalog = getPricingCatalog()
    cost = catalog.fixedCost(ANALYSIS_EXPORT_FEATURE)
    if cost is None:
        try:
            catalog.refreshBlocking()
            cost = catalog.fixedCost(ANALYSIS_EXPORT_FEATURE)
        except Exception as error:
            MessageBox("价格加载失败", str(error), parent).exec()
            return None
    if cost is None:
        MessageBox("暂不可导出", "管理员尚未发布语料分析导出价格。", parent).exec()
        return None
    confirm = MessageBox(
        "确认收费导出",
        f"本地分析免费；本次“{description}”导出固定收取 {cost} 点，与分析量无关。\n"
        f"执行过程中将锁定当前价格版本。",
        parent,
    )
    confirm.yesButton.setText(f"确认导出（{cost} 点）")
    confirm.cancelButton.setText("取消")
    if not confirm.exec():
        return None
    gate = getFeatureGate()
    result = gate.requireFeature(
        ANALYSIS_EXPORT_FEATURE,
        resourceUsed=1,
        taskId="analysis-export",
        description=description,
    )
    if not result.ok:
        gate.handleBlockReason(result, parent)
        return None
    return PaidExportTransaction(result)


__all__ = ["ANALYSIS_EXPORT_FEATURE", "PaidExportTransaction", "beginPaidAnalysisExport"]
