"""偏误关联统计服务回归测试。"""

import pytest

from app.core.services.association_rule_service import (
    adjustPValuesHolm,
    mineAssociationRules,
)


def test_holm_adjustment_is_monotonic_in_p_value_order():
    adjustedValues = adjustPValuesHolm([0.01, 0.04, 0.03])

    assert adjustedValues == pytest.approx([0.03, 0.06, 0.06])


def test_sentence_transactions_preserve_directional_and_symmetric_metrics():
    transactions = (
        [["A", "B"]] * 25
        + [["A"]] * 15
        + [["B"]] * 5
        + [[]] * 55
    )

    rules = mineAssociationRules(
        transactions,
        minSupport=0.01,
        minConfidence=0.5,
        minJointCount=3,
        familyWiseAlpha=0.05,
    )

    assert len(rules) == 2
    ruleByAntecedent = {
        next(iter(row["antecedents"])): row
        for _, row in rules.iterrows()
    }
    assert ruleByAntecedent["A"]["support"] == pytest.approx(0.25)
    assert ruleByAntecedent["A"]["confidence"] == pytest.approx(0.625)
    assert ruleByAntecedent["B"]["confidence"] == pytest.approx(5 / 6)
    assert ruleByAntecedent["A"]["lift"] == pytest.approx(25 / 12)
    assert ruleByAntecedent["B"]["lift"] == pytest.approx(25 / 12)
    assert ruleByAntecedent["A"]["leverage"] == pytest.approx(0.13)
    assert ruleByAntecedent["A"]["joint count"] == 25
    assert ruleByAntecedent["A"]["adjusted p-value"] <= 0.05
    assert rules.attrs["transactionCount"] == 100
    assert rules.attrs["testedPairCount"] == 1


def test_low_evidence_cooccurrence_is_not_reported():
    transactions = [["A", "B"], ["A"], ["B"]] + [[]] * 17

    rules = mineAssociationRules(
        transactions,
        minSupport=0.01,
        minConfidence=0.1,
        minJointCount=3,
        familyWiseAlpha=0.05,
    )

    assert rules.empty
    assert rules.attrs["transactionCount"] == 20
    assert rules.attrs["testedPairCount"] == 1
