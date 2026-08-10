# coding: utf-8
"""下载与 HSK 作文导出的按量计费事务。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtWidgets import QWidget
from qfluentwidgets import MessageBox

from app.core.utils import logger, signalBus

from .cloud_api import CloudApiError
from .cloud_billing import getCloudBilling
from .feature_gate import GateResult, getFeatureGate
from .pricing_catalog import getPricingCatalog
from .responsive_call import runResponsiveCall

HSK_DOWNLOAD_FEATURE = "hsk_download"
GLOBAL_DOWNLOAD_FEATURE = "global_download"
HSK_ESSAY_EXPORT_FEATURE = "hsk_essay_export"


class PaidMeteredTransaction:
    """预占后的按量事务，可交给任务中心或直接结算。"""

    def __init__(self, result: GateResult) -> None:
        self._result = result
        self._finished = False
        self._handedOff = False

    @property
    def estimatedCost(self) -> int:
        return int(self._result.context.get("estimatedCost", 0) or 0)

    @property
    def resourceUsed(self) -> int:
        return int(self._result.context.get("resourceUsed", 0) or 0)

    @property
    def billId(self) -> str:
        return str((self._result.context.get("preauth") or {}).get("billId", ""))

    def attachToTaskInfo(self, taskInfo: Dict[str, Any]) -> None:
        taskInfo["_billing"] = {
            "billId": self.billId,
            "featureCode": str(self._result.context.get("featureCode", "")),
            "resourceUsed": self.resourceUsed,
            "estimatedCost": self.estimatedCost,
            "billingMode": "metered",
        }

    def handOffToTaskManager(self) -> None:
        self._handedOff = True

    def commit(self) -> bool:
        if self._finished or self._handedOff or not self.billId:
            return False
        for attempt in range(2):
            try:
                settled = runResponsiveCall(
                    lambda: getCloudBilling().commitMetered(self.billId)
                )
                signalBus.balanceChanged.emit(int(settled.get("balanceAfter", 0) or 0))
                self._finished = True
                return True
            except CloudApiError as error:
                logger.warning(
                    f"[PaidMetered] 按量结算失败 attempt={attempt + 1}: {error}"
                )
        return False

    def refund(self) -> None:
        if self._finished or self._handedOff:
            return
        refundFn = self._result.context.get("refund")
        try:
            if refundFn is not None:
                runResponsiveCall(refundFn)
        except Exception:
            logger.exception("[PaidMetered] 释放按量计费预占失败")
        finally:
            self._finished = True


def _catalogCost(featureCode: str, resourceUsed: int) -> int | None:
    catalog = getPricingCatalog()
    cost = catalog.meteredCost(featureCode, resourceUsed)
    if cost is not None:
        return cost
    try:
        catalog.refreshResponsive()
    except Exception:
        return None
    return catalog.meteredCost(featureCode, resourceUsed)


def beginPaidMeteredAction(
    parent: Optional[QWidget],
    featureCode: str,
    resourceUsed: int,
    description: str,
    *,
    confirmedCost: int | None = None,
    showConfirmation: bool = True,
) -> PaidMeteredTransaction | None:
    """显示价格、完成服务端报价与预占，并锁定当前价格版本。"""
    resourceUsed = max(0, int(resourceUsed))
    if resourceUsed <= 0:
        MessageBox("无法继续", "没有可计费的下载或导出内容。", parent).exec()
        return None

    catalogCost = _catalogCost(featureCode, resourceUsed)
    if catalogCost is None:
        MessageBox("价格加载失败", "管理员尚未发布该功能价格，请稍后重试。", parent).exec()
        return None
    if showConfirmation:
        confirm = MessageBox(
            "确认按量计费",
            f"本次“{description}”预计收取 {catalogCost} 点。\n"
            "任务开始后锁定当前价格；失败或取消会释放预占。",
            parent,
        )
        confirm.yesButton.setText(f"确认继续（{catalogCost} 点）")
        confirm.cancelButton.setText("取消")
        isConfirmed = bool(confirm.exec())
        confirm.hide()
        confirm.deleteLater()
        if not isConfirmed:
            return None

    gate = getFeatureGate()
    result = runResponsiveCall(
        lambda: gate.requireFeature(
            featureCode,
            resourceUsed=resourceUsed,
            taskId=featureCode,
            description=description,
        )
    )
    if not result.ok:
        gate.handleBlockReason(result, parent)
        return None

    transaction = PaidMeteredTransaction(result)
    acceptedCost = catalogCost if confirmedCost is None else int(confirmedCost)
    if transaction.estimatedCost != acceptedCost:
        changed = MessageBox(
            "价格已更新",
            f"该请求的最新价格为 {transaction.estimatedCost} 点，"
            f"与你刚才看到的 {acceptedCost} 点不同。是否按新价格继续？",
            parent,
        )
        changed.yesButton.setText(f"按 {transaction.estimatedCost} 点继续")
        changed.cancelButton.setText("取消")
        if not changed.exec():
            transaction.refund()
            return None
    return transaction


__all__ = [
    "GLOBAL_DOWNLOAD_FEATURE",
    "HSK_DOWNLOAD_FEATURE",
    "HSK_ESSAY_EXPORT_FEATURE",
    "PaidMeteredTransaction",
    "beginPaidMeteredAction",
]
