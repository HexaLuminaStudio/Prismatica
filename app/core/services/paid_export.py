# coding: utf-8
"""语料分析固定价导出事务。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QWidget
from qfluentwidgets import MessageBox

from app.core.utils import logger, signalBus
from app.core.utils.setting import INTERNAL_TEST_MODE

from .cloud_api import CloudApiError, getCloudApi
from .cloud_billing import getCloudBilling
from .feature_gate import GateResult, getFeatureGate
from .paid_action_guard import (
    PaidActionLease,
    notifyPaidActionBusy,
    paidActionRegistry,
)
from .pricing_catalog import getPricingCatalog
from .responsive_call import runResponsiveCall

ANALYSIS_EXPORT_FEATURE = "analysis_export"


class PaidExportTransaction:
    """预占成功后的单次导出事务；必须 commit 或 refund。"""

    def __init__(self, result: GateResult, actionLease: PaidActionLease) -> None:
        self._result = result
        self._actionLease = actionLease
        self._finished = False

    @property
    def estimatedCost(self) -> int:
        return int(self._result.context.get("estimatedCost", 0) or 0)

    def commit(self) -> bool:
        if self._finished:
            return False
        if self._result.context.get("localMode"):
            self._finished = True
            self._actionLease.release()
            return True
        billId = str((self._result.context.get("preauth") or {}).get("billId", ""))
        for attempt in range(2):
            try:
                settled = runResponsiveCall(
                    lambda: getCloudBilling().commitFixed(billId)
                )
                signalBus.balanceChanged.emit(int(settled.get("balanceAfter", 0) or 0))
                self._finished = True
                self._actionLease.release()
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
                runResponsiveCall(refundFn)
        except Exception:
            logger.exception("[PaidExport] 释放导出预占失败")
        finally:
            self._finished = True
            self._actionLease.release()


def beginPaidAnalysisExport(
    parent: Optional[QWidget],
    description: str,
) -> PaidExportTransaction | None:
    """确认公开固定价并完成预占；取消、未登录或余额不足时返回 None。"""
    actionLease = paidActionRegistry.tryAcquire(
        ANALYSIS_EXPORT_FEATURE,
        1,
        description,
    )
    if actionLease is None:
        notifyPaidActionBusy(parent, description)
        return None

    transaction: PaidExportTransaction | None = None
    try:
        if INTERNAL_TEST_MODE:
            result = GateResult(
                ok=True,
                reason="local_mode",
                message="内测本地模式不计费",
                context={
                    "featureCode": ANALYSIS_EXPORT_FEATURE,
                    "estimatedCost": 0,
                    "resourceUsed": 1,
                    "localMode": True,
                },
            )
            transaction = PaidExportTransaction(result, actionLease)
            return transaction
        catalog = getPricingCatalog()
        cost = catalog.fixedCost(ANALYSIS_EXPORT_FEATURE)
        if cost is None:
            try:
                catalog.refreshResponsive()
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
        isConfirmed = bool(confirm.exec())
        confirm.hide()
        confirm.deleteLater()
        if not isConfirmed:
            return None
        gate = getFeatureGate()
        result = runResponsiveCall(
            lambda: gate.requireFeature(
                ANALYSIS_EXPORT_FEATURE,
                resourceUsed=1,
                taskId=f"{ANALYSIS_EXPORT_FEATURE}:{actionLease.operationId}",
                description=description,
                idempotencyKey=actionLease.operationId,
            )
        )
        if not result.ok:
            gate.handleBlockReason(result, parent)
            return None
        transaction = PaidExportTransaction(result, actionLease)
        return transaction
    except Exception:
        if transaction is not None:
            transaction.refund()
        raise
    finally:
        if transaction is None:
            actionLease.release()


__all__ = ["ANALYSIS_EXPORT_FEATURE", "PaidExportTransaction", "beginPaidAnalysisExport"]
