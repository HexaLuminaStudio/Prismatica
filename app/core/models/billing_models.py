# coding: utf-8
"""计费与账单数据模型

即充即用(智慧计价)的核心数据结构:
    - ActionType         : 所有可计费的动作枚举
    - PricingRule        : 单动作的"基础费 + 阶梯资源费"规则
    - RechargeRecord     : 充值/赠送流水
    - BillItem           : 单次功能扣费流水
    - Account            : 本地账户(余额 + 冻结 + 累计)
    - CostPreview        : 扣费前费用预估
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """所有可计费的动作(与 views/widgets/freq_analyzer/ 中的分析模块一一对应)"""

    FREQ_ANALYZE = "freq_analyze"
    KWIC_SEARCH = "kwic_search"
    CO_OCCURRENCE = "co_occurrence"
    DEPENDENCY_PARSE = "dependency_parse"
    WORD_CLOUD = "word_cloud"
    SENTIMENT = "sentiment"
    BIAS_STATS = "bias_stats"
    CORPUS_IMPORT = "corpus_import"
    CORPUS_DOWNLOAD = "corpus_download"


class BillStatus(str, Enum):
    """账单状态"""

    PENDING = "pending"      # 已预占,业务执行中
    SETTLED = "settled"      # 已结算
    REFUNDED = "refunded"    # 已退款
    FAILED = "failed"        # 失败


class PricingTier(BaseModel):
    """资源阶梯(资源量越大,单价越低)"""

    upTo: int = Field(..., description="该阶梯上限(资源量);-1 表示无穷大")
    rate: float = Field(..., description="该阶梯单价倍率(相对 perUnit)")


class PricingRule(BaseModel):
    """单动作的智慧计价规则

    扣费公式:
        cost = clamp(baseCost + ceil(resourceUsed / 1000) * perUnit * tierRate,
                     minCost, maxCost)
    其中 tierRate 由 resourceUsed 命中的阶梯决定。
    """

    actionType: ActionType
    displayName: str = Field(..., description="中文显示名,如「词频分析」")
    baseCost: int = Field(..., ge=0, description="基础费(币)")
    perUnit: int = Field(..., ge=0, description="单价(币 / 千字或币 / 次)")
    unitName: str = Field(..., description="单位显示名,如「千字」「次」")
    tiers: list[PricingTier] = Field(default_factory=list)
    minCost: int = Field(default=0, description="最低扣费(币)")
    maxCost: int = Field(default=10000, description="最高扣费(币)")
    enabled: bool = Field(default=True, description="是否启用")


class CostPreview(BaseModel):
    """扣费前预估(供 UI 弹窗展示)"""

    actionType: ActionType
    displayName: str
    resourceUsed: int = Field(..., description="预估资源量(字/次)")
    unitName: str
    estimatedCost: int = Field(..., description="预估扣费(币)")
    currentBalance: int = Field(..., description="当前余额(币)")
    balanceAfter: int = Field(..., description="扣费后余额(币)")
    affordable: bool = Field(..., description="余额是否足够")
    tierBreakdown: list[dict] = Field(
        default_factory=list,
        description="阶梯明细 [{upTo, rate}]",
    )


class RechargeRecord(BaseModel):
    """充值/赠送流水"""

    recordId: str
    userId: str
    amount: int = Field(..., gt=0)
    source: str = Field(..., description="manual_gift / recharge_code / activation_grant")
    code: str = Field(default="", description="若为充值码则记录码明文(已脱敏仅留尾 4 位)")
    operatorNote: str = ""
    balanceBefore: int = 0
    balanceAfter: int = 0
    createdAt: datetime = Field(default_factory=datetime.utcnow)
    expireAt: Optional[datetime] = None


class BillItem(BaseModel):
    """单次功能扣费流水"""

    billId: str
    userId: str
    actionType: ActionType
    actionDisplayName: str = ""
    estimatedCost: int = 0
    realCost: int = 0
    resourceUsed: int = 0
    balanceBefore: int = 0
    balanceAfter: int = 0
    status: BillStatus = BillStatus.PENDING
    taskId: str = ""
    description: str = ""
    createdAt: datetime = Field(default_factory=datetime.utcnow)
    settledAt: Optional[datetime] = None


class Account(BaseModel):
    """本地账户(单用户模型,内测期一机一账户)"""

    userId: str
    displayName: str
    tier: str = "beta"
    balance: int = Field(default=0, ge=0)
    frozenBalance: int = Field(default=0, ge=0)
    totalSpent: int = Field(default=0, ge=0)
    totalRecharged: int = Field(default=0, ge=0)
    expireAt: Optional[datetime] = Field(default=None, description="云端真实到期时间(naive UTC)")
    createdAt: datetime = Field(default_factory=datetime.utcnow)
    updatedAt: datetime = Field(default_factory=datetime.utcnow)


__all__ = [
    "ActionType",
    "BillStatus",
    "PricingTier",
    "PricingRule",
    "CostPreview",
    "RechargeRecord",
    "BillItem",
    "Account",
]