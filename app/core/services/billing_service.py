# coding: utf-8
"""计费服务(BillingService)

负责扣费三段式:
    1. estimate   — 预估费用
    2. frozenPreauth — 预占余额
    3. settle     — 结算(差额返还)
    4. refund     — 失败/取消时全额返还

与 AuthService 解耦:仅依赖 account_db + pricing_service。
UI 通过 @charged 装饰器间接调用本服务,业务方法零侵入。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from loguru import logger

from app.core.models.billing_models import (
    Account,
    ActionType,
    BillItem,
    BillStatus,
    CostPreview,
)
from app.core.services import account_db
from app.core.services.pricing_service import PricingService, getPricingService
from app.core.utils.signal_bus import signalBus


_billingInstance: Optional["BillingService"] = None


@dataclass
class RechargeResult:
    success: bool
    message: str
    amount: int = 0
    balanceAfter: int = 0


@dataclass
class PreauthResult:
    success: bool
    message: str
    billId: Optional[str] = None
    estimatedCost: int = 0
    balanceAfter: int = 0


class InsufficientBalanceError(Exception):
    """余额不足异常(供 @charged 装饰器捕获)"""

    def __init__(self, currentBalance: int, required: int):
        super().__init__(f"余额不足: 当前 {currentBalance}, 需要 {required}")
        self.currentBalance = currentBalance
        self.required = required


class BillingService:
    """计费服务门面"""

    def __init__(self, pricing: Optional[PricingService] = None):
        self._pricing = pricing or getPricingService()

    @classmethod
    def instance(cls) -> "BillingService":
        global _billingInstance
        if _billingInstance is None:
            _billingInstance = cls()
        return _billingInstance

    # ---------- 账户 ----------
    def ensureAccount(self, userId: str, displayName: str, tier: str = "beta") -> Account:
        """确保账户存在(不存在则创建,余额为 0)。"""
        existing = account_db.getAccount(userId)
        if existing is not None:
            return existing
        account = Account(
            userId=userId,
            displayName=displayName,
            tier=tier,
            balance=0,
        )
        account_db.upsertAccount(account)
        return account

    def getAccount(self, userId: str) -> Optional[Account]:
        return account_db.getAccount(userId)

    def getBalance(self, userId: str) -> int:
        acc = account_db.getAccount(userId)
        return acc.balance if acc else 0

    # ---------- 充值 ----------
    def rechargeByCode(
        self,
        userId: str,
        code: str,
        expectedAmount: int,
        note: str = "",
    ) -> RechargeResult:
        """通过充值码加余额(原子操作,失败时不影响账户)。"""
        try:
            amount, _ = account_db.consumeRechargeCode(code, userId)
        except LookupError:
            return RechargeResult(success=False, message="充值码无效或不存在", amount=0)
        except ValueError as e:
            return RechargeResult(success=False, message=str(e), amount=0)

        if amount != expectedAmount:
            logger.warning(
                f"[Billing] 充值码面额不一致: code={code}, "
                f"expected={expectedAmount}, actual={amount}"
            )

        try:
            record = account_db.addBalance(
                userId=userId,
                delta=amount,
                source="recharge_code",
                code=code,
            )
        except LookupError:
            return RechargeResult(success=False, message="账户不存在,请先激活", amount=0)

        signalBus.balanceChanged.emit(userId, record.balanceAfter)
        logger.info(
            f"[Billing] 充值成功 user={userId} amount={amount} "
            f"balance={record.balanceBefore}→{record.balanceAfter}"
        )
        return RechargeResult(
            success=True,
            message=f"充值成功 +{amount} 币",
            amount=amount,
            balanceAfter=record.balanceAfter,
        )

    def manualGrant(
        self,
        userId: str,
        amount: int,
        note: str = "",
    ) -> RechargeResult:
        """后台手动赠送(运营后台调用)。"""
        try:
            record = account_db.addBalance(
                userId=userId, delta=amount, source="manual_gift", code=""
            )
        except LookupError as e:
            return RechargeResult(success=False, message=str(e))
        signalBus.balanceChanged.emit(userId, record.balanceAfter)
        return RechargeResult(
            success=True, message=f"赠送成功 +{amount}", amount=amount,
            balanceAfter=record.balanceAfter,
        )

    # ---------- 预估 ----------
    def preview(
        self,
        action: ActionType,
        resourceUsed: int,
        userId: str,
    ) -> CostPreview:
        balance = self.getBalance(userId)
        return self._pricing.preview(action, resourceUsed, balance)

    def estimate(
        self,
        action: ActionType,
        resourceUsed: int,
    ) -> int:
        return self._pricing.estimate(action, resourceUsed)

    # ---------- 预占 ----------
    def frozenPreauth(
        self,
        userId: str,
        action: ActionType,
        resourceUsed: int,
        taskId: str = "",
        description: str = "",
    ) -> PreauthResult:
        """预占扣费。失败抛 InsufficientBalanceError 或返回 success=False。"""
        rule = self._pricing.rule(action)
        estimated = self._pricing.estimate(action, resourceUsed)

        acc = account_db.getAccount(userId)
        if acc is None:
            return PreauthResult(success=False, message="账户不存在,请先激活")
        if acc.balance < estimated:
            return PreauthResult(
                success=False,
                message=f"余额不足: 当前 {acc.balance} 币, 需要 {estimated} 币",
            )

        try:
            balanceAfter = account_db.freezePreauth(userId, estimated)
        except Exception as e:
            logger.exception(f"[Billing] 预占失败: {e}")
            return PreauthResult(success=False, message=f"预占失败: {e}")

        bill = account_db.createBill(
            userId=userId,
            actionType=action,
            estimatedCost=estimated,
            resourceUsed=resourceUsed,
            balanceBefore=balanceAfter + estimated,
            taskId=taskId,
            description=description,
            displayName=rule.displayName,
        )

        signalBus.balanceChanged.emit(userId, balanceAfter)
        logger.info(
            f"[Billing] 预占 user={userId} action={action.value} "
            f"cost={estimated} billId={bill.billId}"
        )
        return PreauthResult(
            success=True,
            message="ok",
            billId=bill.billId,
            estimatedCost=estimated,
            balanceAfter=balanceAfter,
        )

    # ---------- 结算 ----------
    def settle(
        self,
        billId: str,
        realResourceUsed: int,
    ) -> BillItem:
        """根据实际资源量结算(差额自动返还)。"""
        bill = account_db.getBill(billId)
        if bill is None:
            raise LookupError(f"账单不存在: {billId}")
        if bill.status != BillStatus.PENDING:
            logger.warning(f"[Billing] 账单已结算/退款,跳过: {billId} status={bill.status}")
            return bill

        rule = self._pricing.rule(bill.actionType)
        realCost = self._pricing.estimate(bill.actionType, realResourceUsed)
        realCost = min(realCost, bill.estimatedCost)  # 永远不超出预占

        newBalance = account_db.settleFrozen(bill.userId, bill.estimatedCost, realCost)
        account_db.updateBill(
            billId=billId,
            realCost=realCost,
            resourceUsed=realResourceUsed,
            balanceAfter=newBalance,
            status=BillStatus.SETTLED,
        )
        bill.realCost = realCost
        bill.resourceUsed = realResourceUsed
        bill.balanceAfter = newBalance
        bill.status = BillStatus.SETTLED

        signalBus.balanceChanged.emit(bill.userId, newBalance)
        logger.info(
            f"[Billing] 结算 billId={billId} realCost={realCost} "
            f"resource={realResourceUsed} balanceAfter={newBalance}"
        )
        return bill

    # ---------- 退款 ----------
    def refund(self, billId: str, reason: str = "") -> BillItem:
        bill = account_db.getBill(billId)
        if bill is None:
            raise LookupError(f"账单不存在: {billId}")
        if bill.status != BillStatus.PENDING:
            logger.warning(f"[Billing] 账单已结算/退款,跳过退款: {billId}")
            return bill

        newBalance = account_db.refundFrozen(bill.userId, bill.estimatedCost)
        account_db.updateBill(
            billId=billId,
            realCost=0,
            resourceUsed=bill.resourceUsed,
            balanceAfter=newBalance,
            status=BillStatus.REFUNDED,
        )
        bill.status = BillStatus.REFUNDED
        bill.realCost = 0
        bill.balanceAfter = newBalance

        signalBus.balanceChanged.emit(bill.userId, newBalance)
        logger.info(
            f"[Billing] 退款 billId={billId} amount={bill.estimatedCost} "
            f"reason={reason} balanceAfter={newBalance}"
        )
        return bill

    # ---------- 查询 ----------
    def listBills(self, userId: str, limit: int = 100) -> list[BillItem]:
        return account_db.listBills(userId, limit=limit)

    def listRecharges(self, userId: str, limit: int = 50):
        return account_db.listRecharges(userId, limit=limit)


def getBillingService() -> BillingService:
    return BillingService.instance()