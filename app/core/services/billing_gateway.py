# coding: utf-8
"""BillingGateway(2026-08-05 T3 新增)

把 BillingService 中云端相关的逻辑拆分出来,只负责:
    - 调 CloudApi 完成 me / listBills / estimate / preauth / settle / refund / recharge
    - 把云端响应解析成 Account / BillItem / CostPreview / PreauthResult 等强类型
    - 失败统一抛 CloudApiError,由调用方决定 UI 文案

不负责:
    - 持久化本地 SQLite(2026-08-05 决策:bills 是云端权威,本地不再写)
    - estimate 的本地计算(留 BillingService.preview 走 PricingService)

调用方约定:
    - 业务侧(billing_service / decorators / account_interface / bill_table 等)
      改调本 Gateway 而不是直接 import cloud_api。

向后兼容:
    - BillingService 仍存在,作为「业务门面」被 @charged 装饰器调用,内部委托本 Gateway。
    - 老的 BillingService.{preauth,settle,refund,listBills,rechargeByCode} 方法
      临时保留并委托到本类,二期再删;这一版不强行破坏。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.core.utils import audit, logger

from app.core.models.billing_models import (
    Account,
    ActionType,
    BillItem,
    BillStatus,
    CostPreview,
)
from app.core.services.cloud_api import (
    CloudApi,
    CloudApiError,
    getCloudApi,
)


class BillingGateway:
    """计费云端门面(强云端,不再写本地 SQLite)。

    单例入口由 getBillingGateway() 提供。
    """

    def __init__(self, api: Optional[CloudApi] = None):
        self._api = api or getCloudApi()

    # ============================================================
    # 公开 API
    # ============================================================

    def me(self) -> Optional[Account]:
        """拉取 /v1/account/me,转换为 Account。失败返回 None。"""
        try:
            data = self._api.getMe()
        except CloudApiError as e:
            logger.warning(f"[BillingGateway] me 失败: {e.code} {e.message}")
            return None
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[BillingGateway] me 异常: {e}")
            return None
        return _accountFromCloud(data)

    def listBills(self, cursor: str = "", limit: int = 50) -> list[BillItem]:
        """拉取账单列表。失败返回 []。"""
        try:
            data = self._api.listBills(cursor=cursor, limit=limit)
        except CloudApiError as e:
            logger.warning(f"[BillingGateway] listBills 失败: {e.code} {e.message}")
            return []
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[BillingGateway] listBills 异常: {e}")
            return []
        items = data.get("items") or []
        return [_billItemFromCloud(it) for it in items]

    def estimate(self, action: ActionType, resourceUsed: int) -> CostPreview:
        """调 /v1/billing/estimate 转 CostPreview。失败抛 CloudApiError。"""
        data = self._api.estimate(
            actionType=action.value,
            resourceUsed=int(resourceUsed),
        )
        return _costPreviewFromCloud(data, fallbackAction=action)

    def preauth(
        self,
        action: ActionType,
        resourceUsed: int,
        taskId: str = "",
        description: str = "",
    ) -> "PreauthResult":
        """调 /v1/billing/preauth,返回 PreauthResult。"""
        data = self._api.preauth(
            actionType=action.value,
            resourceUsed=int(resourceUsed),
            taskId=taskId,
            description=description,
        )
        result = PreauthResult(
            success=True,
            message="ok",
            billId=str(data.get("billId") or ""),
            estimatedCost=int(data.get("estimatedCost", 0) or 0),
            balanceAfter=int(data.get("balanceAfter", 0) or 0),
        )
        # 计费审计(2026-08-06):预占成功落 audit_<date>.log(90 天)
        audit(
            "BILL_PREAUTH",
            f"action={action.value} billId={result.billId} "
            f"estimated={result.estimatedCost} balanceAfter={result.balanceAfter}",
        )
        return result

    def settle(self, billId: str, realResourceUsed: int) -> Optional[BillItem]:
        """调 /v1/billing/settle,转 BillItem。失败返回 None。"""
        # 简化:realCost 在 Gateway 里算(不动 PricingService)
        # 真实业务侧用 PricingService.estimate(action, real) 也行,这里仅做 IO 透传。
        try:
            data = self._api.settle(
                billId=billId,
                realCost=0,  # 由 BillingService 外部算好后传入更精准;此处给 0 让云端按 estimatedCost 兜底
                resourceUsed=int(realResourceUsed),
            )
        except CloudApiError as e:
            logger.warning(f"[BillingGateway] settle 失败: {e.code} {e.message}")
            audit("BILL_SETTLE_FAIL", f"billId={billId} code={e.code}")
            return None
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[BillingGateway] settle 异常: {e}")
            audit("BILL_SETTLE_FAIL", f"billId={billId} exc={type(e).__name__}")
            return None
        billIdOut = str(data.get("billId") or billId)
        realCost = int(data.get("realCost", 0) or 0)
        balanceAfter = int(data.get("balanceAfter", 0) or 0)
        audit(
            "BILL_SETTLE",
            f"billId={billIdOut} realCost={realCost} balanceAfter={balanceAfter}",
        )
        return BillItem(
            billId=billIdOut,
            userId="",  # 云端 settle 响应里没有 userId,业务方若有需要再拉详情
            actionType=ActionType.FREQ_ANALYZE,
            estimatedCost=realCost,
            realCost=realCost,
            resourceUsed=int(realResourceUsed),
            balanceBefore=0,
            balanceAfter=balanceAfter,
            status=BillStatus.SETTLED,
            createdAt=datetime.utcnow(),
            settledAt=datetime.utcnow(),
        )

    def refund(self, billId: str) -> Optional[BillItem]:
        """调 /v1/billing/refund。失败返回 None。"""
        try:
            data = self._api.refund(billId=billId)
        except CloudApiError as e:
            logger.warning(f"[BillingGateway] refund 失败: {e.code} {e.message}")
            audit("BILL_REFUND_FAIL", f"billId={billId} code={e.code}")
            return None
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[BillingGateway] refund 异常: {e}")
            audit("BILL_REFUND_FAIL", f"billId={billId} exc={type(e).__name__}")
            return None
        refunded = int(data.get("refundedAmount", 0) or 0)
        balanceAfter = int(data.get("balanceAfter", 0) or 0)
        audit(
            "BILL_REFUND",
            f"billId={billId} refunded={refunded} balanceAfter={balanceAfter}",
        )
        return BillItem(
            billId=str(data.get("billId") or billId),
            userId="",
            actionType=ActionType.FREQ_ANALYZE,
            estimatedCost=int(data.get("refundedAmount", 0) or 0),
            realCost=0,
            resourceUsed=0,
            balanceBefore=0,
            balanceAfter=int(data.get("balanceAfter", 0) or 0),
            status=BillStatus.REFUNDED,
            createdAt=datetime.utcnow(),
            settledAt=datetime.utcnow(),
        )

    def recharge(self, code: str) -> "RechargeResult":
        """充值:走 redeem(mode=recharge)。

        云端模式下 redeem 就是充值;返回 RechargeResult(success=False) 表示失败。
        """
        try:
            data = self._api.redeem(code=code, displayName="")
        except CloudApiError as e:
            logger.warning(f"[BillingGateway] recharge 失败: {e.code} {e.message}")
            return RechargeResult(success=False, message=e.message or "充值失败", amount=0)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[BillingGateway] recharge 异常: {e}")
            return RechargeResult(success=False, message=f"云端不可达: {e}", amount=0)
        balanceDict = data.get("balance") or {}
        balanceAfter = int(balanceDict.get("balance", 0) or 0)
        return RechargeResult(
            success=True,
            message=f"充值成功 +{balanceAfter} 币",
            amount=balanceAfter,
            balanceAfter=balanceAfter,
        )


# ---------------------------------------------------------------------------
# 强类型结果(放这里避免外部依赖数据库 dataclass)
# ---------------------------------------------------------------------------


class PreauthResult:
    """预占结果。"""

    def __init__(
        self,
        success: bool,
        message: str,
        billId: Optional[str] = None,
        estimatedCost: int = 0,
        balanceAfter: int = 0,
    ):
        self.success = success
        self.message = message
        self.billId = billId
        self.estimatedCost = estimatedCost
        self.balanceAfter = balanceAfter


class RechargeResult:
    """充值结果。"""

    def __init__(
        self,
        success: bool,
        message: str,
        amount: int = 0,
        balanceAfter: int = 0,
    ):
        self.success = success
        self.message = message
        self.amount = amount
        self.balanceAfter = balanceAfter


# ---------------------------------------------------------------------------
# 内部:云端响应 → 强类型
# ---------------------------------------------------------------------------


def _parseDt(raw: Any) -> Optional[datetime]:
    """解析云端 ISO 时间字符串 → naive UTC datetime;解析失败返回 None。"""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:  # noqa: BLE001
        return None


def _accountFromCloud(data: dict) -> Account:
    """云端 /v1/account/me(对齐后端 UserAccountOut)→ 本地 Account。"""
    if not isinstance(data, dict):
        data = {}
    return Account(
        userId=str(data.get("userId") or ""),
        displayName=str(data.get("displayName") or "内测用户"),
        tier=str(data.get("tier") or "beta"),
        balance=int(data.get("balance", 0) or 0),
        frozenBalance=int(data.get("frozenBalance", 0) or 0),
        totalSpent=int(data.get("totalSpent", 0) or 0),
        totalRecharged=int(data.get("totalRecharged", 0) or 0),
        expireAt=_parseDt(data.get("expireAt")),
    )


def _billItemFromCloud(it: dict) -> BillItem:
    """云端单条账单(对齐后端 BillOut)→ BillItem。"""
    if not isinstance(it, dict):
        it = {}
    try:
        created = it.get("createdAt")
        createdAt = _parseDt(created) or datetime.utcnow()
    except Exception:  # noqa: BLE001
        createdAt = datetime.utcnow()
    try:
        settledAt = _parseDt(it.get("settledAt"))
    except Exception:  # noqa: BLE001
        settledAt = None

    actionStr = str(it.get("actionType") or "freq_analyze")
    try:
        actionType = ActionType(actionStr)
    except Exception:  # noqa: BLE001
        actionType = ActionType.FREQ_ANALYZE

    statusStr = str(it.get("status") or "settled")
    try:
        status = BillStatus(statusStr)
    except Exception:  # noqa: BLE001
        status = BillStatus.SETTLED

    return BillItem(
        billId=str(it.get("billId") or ""),
        userId=str(it.get("userId") or ""),
        actionType=actionType,
        actionDisplayName=str(it.get("actionDisplayName") or ""),
        estimatedCost=int(it.get("estimatedCost", 0) or 0),
        realCost=int(it.get("realCost", 0) or 0),
        resourceUsed=int(it.get("resourceUsed", 0) or 0),
        balanceBefore=int(it.get("balanceBefore", 0) or 0),
        balanceAfter=int(it.get("balanceAfter", 0) or 0),
        status=status,
        taskId=str(it.get("taskId") or ""),
        description=str(it.get("description") or ""),
        createdAt=createdAt,
        settledAt=settledAt,
    )


def _costPreviewFromCloud(data: dict, fallbackAction: ActionType) -> CostPreview:
    """云端 CostPreview(对齐后端 CostPreview)→ 本地 CostPreview。"""
    if not isinstance(data, dict):
        data = {}
    actionStr = str(data.get("actionType") or fallbackAction.value)
    try:
        actionType = ActionType(actionStr)
    except Exception:  # noqa: BLE001
        actionType = fallbackAction
    return CostPreview(
        actionType=actionType,
        displayName=str(data.get("displayName") or fallbackAction.value),
        resourceUsed=int(data.get("resourceUsed", 0) or 0),
        unitName=str(data.get("unitName") or "次"),
        estimatedCost=int(data.get("estimatedCost", 0) or 0),
        currentBalance=int(data.get("currentBalance", 0) or 0),
        balanceAfter=int(data.get("balanceAfter", 0) or 0),
        affordable=bool(data.get("affordable", True)),
        tierBreakdown=list(data.get("tierBreakdown") or []),
    )


def _parseDt(raw: Any) -> Optional[datetime]:
    """ISO 8601 字符串 → datetime(naive UTC)。"""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------


_gatewayInstance: Optional[BillingGateway] = None


def getBillingGateway() -> BillingGateway:
    """获取 BillingGateway 全局单例。"""
    global _gatewayInstance
    if _gatewayInstance is None:
        _gatewayInstance = BillingGateway()
    return _gatewayInstance


def resetBillingGatewayForTesting() -> None:
    """测试钩子:重置单例。"""
    global _gatewayInstance
    _gatewayInstance = None


__all__ = [
    "BillingGateway",
    "PreauthResult",
    "RechargeResult",
    "getBillingGateway",
    "resetBillingGatewayForTesting",
]
