# coding: utf-8
"""计价引擎单元测试"""

from __future__ import annotations

import pytest

from app.core.models.billing_models import ActionType, PricingRule, PricingTier
from app.core.services.pricing_service import (
    DEFAULT_RULES,
    PricingService,
    buildPreview,
    calcCost,
    loadRules,
)


def test_calcCost_basic_tier():
    rule = PricingRule(
        actionType=ActionType.FREQ_ANALYZE,
        displayName="词频分析",
        baseCost=2,
        perUnit=1,
        unitName="千字",
        tiers=[PricingTier(upTo=10000, rate=1.0)],
    )
    # 0 字 → base 2
    assert calcCost(rule, 0) == 2
    # 5000 字 → ceil(5)*1*1.0 + 2 = 7
    assert calcCost(rule, 5000) == 7
    # 10 字 → ceil(1)*1*1.0 + 2 = 3(任何用量向上取整 1 千字)
    assert calcCost(rule, 10) == 3
    # 1000 字 → ceil(1)*1 + 2 = 3
    assert calcCost(rule, 1000) == 3


def test_calcCost_higher_tier_larger_discount():
    rule = PricingRule(
        actionType=ActionType.FREQ_ANALYZE,
        displayName="词频分析",
        baseCost=2,
        perUnit=1,
        unitName="千字",
        tiers=[
            PricingTier(upTo=10000, rate=1.0),
            PricingTier(upTo=100000, rate=0.8),
            PricingTier(upTo=1000000, rate=0.6),
        ],
    )
    # 50 千字 → 命中 100000 这一档 rate=0.8 → ceil(50)*1*0.8 = 40 + 2 = 42
    cost = calcCost(rule, 50_000)
    assert cost == 42


def test_calcCost_min_max_boundary():
    rule = PricingRule(
        actionType=ActionType.WORD_CLOUD,
        displayName="词云",
        baseCost=5,
        perUnit=0,
        unitName="次",
        tiers=[],
        minCost=1,
        maxCost=10,
    )
    # base 5, clamp 到 [1,10] = 5
    assert calcCost(rule, 0) == 5
    # 设 base 太大
    rule2 = PricingRule(
        actionType=ActionType.WORD_CLOUD,
        displayName="词云",
        baseCost=20,
        perUnit=0,
        unitName="次",
        tiers=[],
        minCost=1,
        maxCost=10,
    )
    assert calcCost(rule2, 0) == 10  # 截到 maxCost


def test_calcCost_negative_resource():
    rule = PricingRule(
        actionType=ActionType.WORD_CLOUD,
        displayName="词云",
        baseCost=1,
        perUnit=0,
        unitName="次",
        tiers=[],
    )
    assert calcCost(rule, -100) == 1


def test_default_rules_have_all_actions():
    actions = {item["actionType"] for item in DEFAULT_RULES}
    expected = {a.value for a in ActionType}
    assert actions == expected


def test_loadRules_writes_default_when_missing(tmp_path):
    target = tmp_path / "pricing.json"
    rules = loadRules(target)
    assert target.exists()
    assert len(rules) == len(ActionType)


def test_pricing_service_estimate_and_preview():
    svc = PricingService.instance()
    cost = svc.estimate(ActionType.FREQ_ANALYZE, 5000)
    assert cost >= 2

    preview = svc.preview(ActionType.FREQ_ANALYZE, 5000, currentBalance=100)
    assert preview.estimatedCost == cost
    assert preview.currentBalance == 100
    assert preview.balanceAfter == 100 - cost
    assert preview.affordable is True


def test_buildPreview_insufficient_balance():
    rule = PricingRule(
        actionType=ActionType.DEPENDENCY_PARSE,
        displayName="句法依存",
        baseCost=5,
        perUnit=3,
        unitName="千字",
        tiers=[PricingTier(upTo=50, rate=1.0)],
    )
    preview = buildPreview(rule, resourceUsed=20_000, currentBalance=10)
    assert preview.affordable is False
    assert preview.balanceAfter < 0