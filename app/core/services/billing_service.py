# coding: utf-8
"""计费服务(BillingService)- 2026-08-05 T3 重构

变化摘要:
    - 删掉所有对 account_db 的写(bills 是云端权威)
    - 云端方法(preauth/settle/refund/listBills/rechargeByCode/refreshUserFromCloud)
      改为委托给 BillingGateway
    - 保留 preview/estimate/ensureAccount/getBalance/getAccount 等只读/本地方法,
      供 @charged 装饰器和其他业务侧使用

业务侧调用约定:
    - 业务侧(billing_service / decorators / account_interface / bill_table 等)
      调本服务的方法即可,内部走 BillingGateway → CloudApi → 云端。
    - 写操作(preauth/settle/refund/recharge)100% 走云端,不再有本地 SQLite 落库。
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from app.core.models.billing_models import (
    Account,
    ActionType,
    BillItem,
    CostPreview,
)
from app.core.services.billing_gateway import (
    BillingGateway,
    PreauthResult as _GatewayPreauthResult,
    RechargeResult as _GatewayRechargeResult,
    getBillingGateway,
)
from app.core.services.pricing_service import PricingService, getPricingService
from app.core.utils.signal_bus import signalBus


_billingInstance: Optional["BillingService"] = None


# 兼容旧 import(外部如果还 `from billing_service import RechargeResult` 仍能拿到)
RechargeResult = _GatewayRechargeResult
PreauthResult = _GatewayPreauthResult


__all__ = [
    "BillingService",
    "getBillingService",
    "InsufficientBalanceError",
    "RechargeResult",
    "PreauthResult",
]


class InsufficientBalanceError(Exception):
    """余额不足异常(供 @charged 装饰器捕获)。"""

    def __init__(self, currentBalance: int, required: int):
        super().__init__(f"余额不足: 当前 {currentBalance}, 需要 {required}")
        self.currentBalance = currentBalance
        self.required = required


class BillingService:
    """计费服务门面(2026-08-05 改为委托 BillingGateway)。"""

    def __init__(
        self,
        pricing: Optional[PricingService] = None,
        gateway: Optional[BillingGateway] = None,
    ):
        self._pricing = pricing or getPricingService()
        self._gateway = gateway or getBillingGateway()
        # 云端账户缓存(让 UI 不重复打云端)
        self._cachedUser: Optional[Account] = None
        self._cachedUserAt: Optional[float] = None

    @classmethod
    def instance(cls) -> "BillingService":
        global _billingInstance
        if _billingInstance is None:
            _billingInstance = cls()
        return _billingInstance

    # ---------- 账户(走云端) ----------
    def ensureAccount(self, userId: str, displayName: str, tier: str = "beta") -> Account:
        """ensure 本地有缓存账户;云端账户已 redeem 后由 RefreshUser 拉回缓存。

        强云端决策:不再创建本地 SQLite 账户,直接返回缓存或 0 余额占位。
        """
        cached = self._cachedUser
        if cached is not None and cached.userId == userId:
            return cached
        return Account(
            userId=userId,
            displayName=displayName,
            tier=tier,
            balance=0,
        )

    def getAccount(self, userId: str) -> Optional[Account]:
        cached = self._cachedUser
        if cached is not None and cached.userId == userId:
            return cached
        return None

    def getBalance(self, userId: str) -> int:
        cached = self._cachedUser
        if cached is not None and cached.userId == userId:
            return cached.balance
        return 0

    def refreshUserFromCloud(self, userId: str) -> Optional[Account]:
        """主动从云端拉取账户信息,刷新本地缓存。

        失败 → 网络异常 fallback:
            1. 尝试从 cloud_cache.readUser() 拿历史快照
            2. 都拿不到 → 返回 None,UI 显示「---」
        """
        account = self._gateway.me()
        if account is None:
            try:
                from app.core.services import cloud_cache

                cached = cloud_cache.readUser()
                if cached and cached.get("userId") == userId:
                    return Account(
                        userId=str(cached["userId"]),
                        displayName=str(cached.get("displayName") or "内测用户"),
                        tier=str(cached.get("tier") or "beta"),
                        balance=int(cached.get("balance", 0) or 0),
                        frozenBalance=int(cached.get("frozenBalance", 0) or 0),
                        totalSpent=int(cached.get("totalSpent", 0) or 0),
                        totalRecharged=int(cached.get("totalRecharged", 0) or 0),
                    )
            except Exception:  # noqa: BLE001
                pass
            return None
        # 写本地缓存 + 触发信号
        import time as _t

        self._cachedUser = account
        self._cachedUserAt = _t.time()
        try:
            from app.core.services import cloud_cache

            cloud_cache.writeUser(account.model_dump())
        except Exception:  # noqa: BLE001
            pass
        try:
            signalBus.balanceChanged.emit(userId, account.balance)
        except Exception:  # noqa: BLE001
            pass
        return account

    # ---------- 充值 ----------
    def rechargeByCode(
        self,
        userId: str,
        code: str,
        expectedAmount: int,
        note: str = "",
    ) -> RechargeResult:
        """充值(走 BillingGateway,失败转 RechargeResult)。"""
        try:
            result = self._gateway.recharge(code=code)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[Billing] recharge 异常: {e}")
            return RechargeResult(
                success=False,
                message=f"云端不可达: {e}",
                amount=0,
            )
        if not result.success:
            return result
        try:
            signalBus.balanceChanged.emit(userId, result.balanceAfter)
        except Exception:  # noqa: BLE001
            pass
        # 失效缓存(下次 getBalance 重新拉)
        self._cachedUser = None
        return result

    def manualGrant(self, userId: str, amount: int, note: str = "") -> RechargeResult:
        """后台手动赠送:本期不暴露给前端,由 admin 端调用。

        保留方法签名(供旧代码 import 不出错),实际抛 NotImplementedError。
        """
        raise NotImplementedError(
            "manualGrant 已迁移到 admin 端,请使用 POST /v1/admin/grant"
        )

    # ---------- 预估(本地) ----------
    def preview(
        self,
        action: ActionType,
        resourceUsed: int,
        userId: str,
    ) -> CostPreview:
        balance = self.getBalance(userId)
        return self._pricing.preview(action, resourceUsed, balance)

    def estimate(self, action: ActionType, resourceUsed: int) -> int:
        return self._pricing.estimate(action, resourceUsed)

    # ---------- 预占(走云端) ----------
    def frozenPreauth(
        self,
        userId: str,
        action: ActionType,
        resourceUsed: int,
        taskId: str = "",
        description: str = "",
    ) -> PreauthResult:
        """预占扣费(走云端 BillingGateway.preauth)。

        失败语义:
            - 余额不足(INSUFFICIENT_BALANCE)→ 抛 InsufficientBalanceError
            - 其他网络/业务错误 → PreauthResult(success=False)
        """
        try:
            result = self._gateway.preauth(
                action=action,
                resourceUsed=resourceUsed,
                taskId=taskId,
                description=description,
            )
        except Exception as e:  # noqa: BLE001
            # CloudApiError 已细化为 PreauthResult(success=False),这里的兜底是网络层异常
            from app.core.services.cloud_api import CODE_NETWORK_ERROR, CloudApiError

            if isinstance(e, CloudApiError) and e.code == CODE_NETWORK_ERROR:
                return PreauthResult(
                    success=False,
                    message=e.message or "云端不可达",
                )
            logger.exception(f"[Billing] preauth 异常: {e}")
            return PreauthResult(success=False, message=f"云端不可达: {e}")

        if not result.success:
            return result

        try:
            signalBus.balanceChanged.emit(userId, result.balanceAfter)
        except Exception:  # noqa: BLE001
            pass
        # 失效缓存
        self._cachedUser = None
        logger.info(
            f"[Billing] 云端预占 user={userId} action={action.value} "
            f"cost={result.estimatedCost} billId={result.billId}"
        )
        return result

    # ---------- 结算(走云端) ----------
    def settle(self, billId: str, realResourceUsed: int) -> BillItem:
        """根据实际资源量结算(走云端,失败 fallback 到 refund)。"""
        # 计算 realCost(本地走 PricingService)
        try:
            bill_before = self.getBillOrNone(billId)
            if bill_before is not None:
                realCost = self._pricing.estimate(bill_before.actionType, realResourceUsed)
                realCost = min(realCost, bill_before.estimatedCost)
            else:
                realCost = 0
        except Exception:  # noqa: BLE001
            realCost = 0

        billItem = self._settleViaGateway(billId=billId, realCost=realCost, realResourceUsed=realResourceUsed)
        if billItem is not None:
            return billItem
        # 降级退款
        logger.warning(
            f"[Billing] settle 失败转 refund billId={billId}"
        )
        return self.refund(billId, reason="settle_failed")

    def _settleViaGateway(
        self, billId: str, realCost: int, realResourceUsed: int
    ) -> Optional[BillItem]:
        """直接走云端 settle;返回 BillItem 或 None(表示失败或需要 refund)。"""
        try:
            # 改造点:使用 realCost 调云端 settle;realCost=0 时让云端按 estimatedCost 兜底
            api = self._gateway._api
            data = api.settle(
                billId=billId,
                realCost=realCost,
                resourceUsed=realResourceUsed,
            )
            return self._gateway.settle(billId, realResourceUsed) or _billItemFromSettleData(data, billId, realResourceUsed, realCost)
        except Exception:  # noqa: BLE001
            return None

    # ---------- 退款(走云端) ----------
    def refund(self, billId: str, reason: str = "") -> BillItem:
        """退款(走云端 BillingGateway.refund,失败抛 CloudApiError)。"""
        billItem = self._gateway.refund(billId)
        if billItem is None:
            # 云端失败:让上层 caller 自己处理(原语义)
            from app.core.services.cloud_api import CloudApiError

            raise CloudApiError(
                code="BILL_NOT_PENDING",
                message=f"账单 {billId} 退款失败",
                httpStatus=409,
            )
        # 失效缓存 + 信号
        self._cachedUser = None
        userId = self._cachedUser.userId if self._cachedUser else ""
        try:
            signalBus.balanceChanged.emit(userId, billItem.balanceAfter)
        except Exception:  # noqa: BLE001
            pass
        logger.info(
            f"[Billing] 退款 billId={billId} reason={reason} balanceAfter={billItem.balanceAfter}"
        )
        return billItem

    # ---------- 查询(走云端) ----------
    def listBills(self, userId: str, limit: int = 100) -> list[BillItem]:
        """拉取账单列表(走云端 BillingGateway.listBills)。"""
        items = self._gateway.listBills(limit=limit)
        # 顺手写 cache
        try:
            from app.core.services import cloud_cache

            cloud_cache.writeBills([i.model_dump() for i in items])
        except Exception:  # noqa: BLE001
            pass
        return items

    def listRecharges(self, userId: str, limit: int = 50):
        """充值记录列表:本期云端未单独提供,沿用本地 cache 兜底(若无则空)。"""
        try:
            from app.core.services import cloud_cache

            cached = cloud_cache.readBills()  # 仅作为历史快照兜底
            return cached or []
        except Exception:  # noqa: BLE001
            return []

    # ---------- 辅助 ----------
    def getBillOrNone(self, billId: str) -> Optional[BillItem]:
        """从本地 listBills 中查一条(bills 是云端权威,本方法仅在已缓存时返回)。"""
        cached = self._gateway.listBills(limit=200)
        for b in cached:
            if b.billId == billId:
                return b
        return None


def _billItemFromSettleData(
    data: dict,
    billId: str,
    realResourceUsed: int,
    realCost: int,
) -> BillItem:
    """云端 settle 响应 → BillItem。"""
    from app.core.models.billing_models import ActionType, BillStatus

    realCostOut = int(data.get("realCost", realCost) or realCost)
    balanceAfter = int(data.get("balanceAfter", 0) or 0)
    return BillItem(
        billId=str(data.get("billId") or billId),
        userId="",
        actionType=ActionType.FREQ_ANALYZE,
        estimatedCost=realCostOut,
        realCost=realCostOut,
        resourceUsed=int(realResourceUsed),
        balanceAfter=balanceAfter,
        balanceBefore=0,
        status=BillStatus.SETTLED,
        createdAt=__import__("datetime").datetime.utcnow(),
        settledAt=__import__("datetime").datetime.utcnow(),
    )


def getBillingService() -> BillingService:
    return BillingService.instance()
