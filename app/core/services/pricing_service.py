# coding: utf-8
"""智慧计价引擎

设计目标:
    1. **纯函数**: 前后端共用同一份规则文件,逻辑零 Qt 依赖,易单测
    2. **动作 + 资源双计费**: 基础费(冷启动)+ 阶梯资源费(随用量浮动)
    3. **可热替换**: 启动时从 config/pricing.json 加载,RC+ 可被服务端规则覆盖

扣费公式(对应 PRD §6.1):
    rate       = 命中的阶梯倍率(默认 1.0)
    variable   = ceil(resourceUsed / 1000) * perUnit * rate
    cost       = clamp(baseCost + int(variable), minCost, maxCost)
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from app.core.models.billing_models import (
    ActionType,
    CostPreview,
    PricingRule,
    PricingTier,
)
from app.core.utils.data_paths import DATA_DIR


# ---------------------------------------------------------------------------
# 路径常量:配置/数据均在 <INSTALL_DIR>/datas/ 下
# ---------------------------------------------------------------------------

PRICING_FILE: Path = DATA_DIR / "pricing.json"


# ---------------------------------------------------------------------------
# 默认规则:与 PRD §6.2 表格完全一致
# ---------------------------------------------------------------------------

DEFAULT_RULES: list[dict] = [
    {
        "actionType": "freq_analyze",
        "displayName": "词频分析",
        "baseCost": 2,
        "perUnit": 1,
        "unitName": "千字",
        "tiers": [
            {"upTo": 10000, "rate": 1.0},
            {"upTo": 100000, "rate": 0.8},
            {"upTo": 1000000, "rate": 0.6},
        ],
        "minCost": 2,
        "maxCost": 200,
        "enabled": True,
    },
    {
        "actionType": "kwic_search",
        "displayName": "KWIC 检索",
        "baseCost": 1,
        "perUnit": 1,
        "unitName": "千字",
        "tiers": [{"upTo": 50000, "rate": 1.0}],
        "minCost": 1,
        "maxCost": 50,
        "enabled": True,
    },
    {
        "actionType": "co_occurrence",
        "displayName": "共现网络",
        "baseCost": 3,
        "perUnit": 2,
        "unitName": "千字",
        "tiers": [{"upTo": 100000, "rate": 1.0}],
        "minCost": 3,
        "maxCost": 300,
        "enabled": True,
    },
    {
        "actionType": "dependency_parse",
        "displayName": "句法依存",
        "baseCost": 5,
        "perUnit": 3,
        "unitName": "千字",
        "tiers": [{"upTo": 50000, "rate": 1.0}],
        "minCost": 5,
        "maxCost": 500,
        "enabled": True,
    },
    {
        "actionType": "word_cloud",
        "displayName": "词云生成",
        "baseCost": 1,
        "perUnit": 0,
        "unitName": "次",
        "tiers": [],
        "minCost": 1,
        "maxCost": 10,
        "enabled": True,
    },
    {
        "actionType": "sentiment",
        "displayName": "情感分析",
        "baseCost": 2,
        "perUnit": 1,
        "unitName": "千字",
        "tiers": [{"upTo": 100000, "rate": 1.0}],
        "minCost": 2,
        "maxCost": 100,
        "enabled": True,
    },
    {
        "actionType": "bias_stats",
        "displayName": "偏误统计",
        "baseCost": 2,
        "perUnit": 1,
        "unitName": "千字",
        "tiers": [],
        "minCost": 2,
        "maxCost": 100,
        "enabled": True,
    },
    {
        "actionType": "corpus_import",
        "displayName": "语料导入",
        "baseCost": 0,
        "perUnit": 0,
        "unitName": "次",
        "tiers": [],
        "minCost": 0,
        "maxCost": 0,
        "enabled": True,
    },
    {
        "actionType": "corpus_download",
        "displayName": "语料下载",
        "baseCost": 0,
        "perUnit": 0,
        "unitName": "次",
        "tiers": [],
        "minCost": 0,
        "maxCost": 0,
        "enabled": True,
    },
]


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------


def calcCost(rule: PricingRule, resourceUsed: int) -> int:
    """计算单次动作的扣费(纯函数)。

    Args:
        rule:         单动作计价规则
        resourceUsed: 资源量(字/次),允许负数(视为 0)

    Returns:
        扣费金额(币),已应用 min/max 边界。
    """
    resourceUsed = max(0, int(resourceUsed))
    base = int(rule.baseCost)

    # 0. 阶梯匹配(资源量为 0 也至少按第一档处理)
    rate = 1.0
    matchedTier: Optional[PricingTier] = None
    sortedTiers = sorted(rule.tiers, key=lambda t: t.upTo)
    for tier in sortedTiers:
        if tier.upTo < 0 or resourceUsed <= tier.upTo:
            rate = tier.rate
            matchedTier = tier
            break
    if matchedTier is None and sortedTiers:
        rate = sortedTiers[-1].rate  # 超过最大阶梯:沿用最后一档

    # 1. 资源费
    if rule.perUnit > 0 and resourceUsed > 0:
        units = math.ceil(resourceUsed / 1000.0)
        variable = units * rule.perUnit * rate
    else:
        variable = 0.0

    raw = base + int(round(variable))
    return max(rule.minCost, min(rule.maxCost, raw))


def buildPreview(
    rule: PricingRule,
    resourceUsed: int,
    currentBalance: int,
) -> CostPreview:
    """构造费用预估对象(供 UI 弹窗展示)。"""
    cost = calcCost(rule, resourceUsed)
    affordable = currentBalance >= cost
    return CostPreview(
        actionType=rule.actionType,
        displayName=rule.displayName,
        resourceUsed=max(0, int(resourceUsed)),
        unitName=rule.unitName,
        estimatedCost=cost,
        currentBalance=currentBalance,
        balanceAfter=currentBalance - cost,
        affordable=affordable,
        tierBreakdown=[
            {"upTo": t.upTo, "rate": t.rate} for t in sorted(rule.tiers, key=lambda x: x.upTo)
        ],
    )


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------


def loadRules(path: Optional[Path] = None) -> dict[ActionType, PricingRule]:
    """从 JSON 加载规则。文件不存在时使用 DEFAULT_RULES 并写入磁盘。"""
    target = path or PRICING_FILE
    if not target.exists():
        try:
            saveRules(DEFAULT_RULES, target)
            logger.info(f"[Pricing] 默认规则已写入: {target}")
        except Exception as e:
            logger.warning(f"[Pricing] 写入默认规则失败: {e}")
        raw = DEFAULT_RULES
    else:
        try:
            raw = json.loads(target.read_text(encoding="utf-8")).get("rules", DEFAULT_RULES)
        except Exception as e:
            logger.warning(f"[Pricing] 读取 {target} 失败,使用默认: {e}")
            raw = DEFAULT_RULES

    result: dict[ActionType, PricingRule] = {}
    for item in raw:
        try:
            rule = PricingRule.model_validate(item)
            result[rule.actionType] = rule
        except Exception as e:
            logger.warning(f"[Pricing] 规则解析失败 {item}: {e}")
    # 保证 9 个动作都有规则,缺失则补默认
    for defaultItem in DEFAULT_RULES:
        action = ActionType(defaultItem["actionType"])
        if action not in result:
            result[action] = PricingRule.model_validate(defaultItem)
    return result


def saveRules(rules: list[dict] | dict[ActionType, PricingRule], path: Optional[Path] = None) -> None:
    """保存规则到 JSON(供运营/工具使用)。"""
    target = path or PRICING_FILE
    if isinstance(rules, dict):
        rules = [r.model_dump() for r in rules.values()]
    payload = {
        "version": 1,
        "updatedAt": datetime.utcnow().isoformat(),
        "rules": rules,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 服务类(供上层 service 调用)
# ---------------------------------------------------------------------------


class PricingService:
    """计价服务门面"""

    _instance: Optional["PricingService"] = None

    def __init__(self, rulePath: Optional[Path] = None):
        self._rulePath = rulePath or PRICING_FILE
        self._rules: dict[ActionType, PricingRule] = loadRules(self._rulePath)

    @classmethod
    def instance(cls) -> "PricingService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---------- 公开 API ----------
    def estimate(self, action: ActionType, resourceUsed: int) -> int:
        """预估扣费。"""
        rule = self._getRule(action)
        return calcCost(rule, resourceUsed)

    def preview(
        self,
        action: ActionType,
        resourceUsed: int,
        currentBalance: int,
    ) -> CostPreview:
        """构造预览对象(含余额校验)。"""
        rule = self._getRule(action)
        return buildPreview(rule, resourceUsed, currentBalance)

    def rule(self, action: ActionType) -> PricingRule:
        """获取动作的完整规则(供 UI 展示阶梯明细)。"""
        return self._getRule(action)

    def isEnabled(self, action: ActionType) -> bool:
        """动作是否启用计费(对应 BILLING_ENABLED 开关)。"""
        return self._getRule(action).enabled

    def reload(self) -> None:
        """重新从磁盘加载(供运营工具热替换)。"""
        self._rules = loadRules(self._rulePath)
        logger.info(f"[Pricing] 已重新加载 {len(self._rules)} 条规则")

    # ---------- 内部 ----------
    def _getRule(self, action: ActionType) -> PricingRule:
        rule = self._rules.get(action)
        if rule is None:
            # 兜底:用默认规则,避免抛错
            for item in DEFAULT_RULES:
                if item["actionType"] == action.value:
                    rule = PricingRule.model_validate(item)
                    break
        if rule is None:
            raise ValueError(f"未知动作: {action}")
        return rule


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def getPricingService() -> PricingService:
    """获取全局计价服务单例。"""
    return PricingService.instance()