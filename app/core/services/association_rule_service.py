# coding: utf-8
"""偏误共现关联的统计计算服务。"""

from itertools import combinations
from math import inf

import pandas as pd
from scipy.stats import fisher_exact


RULE_COLUMNS = [
    "antecedents",
    "consequents",
    "antecedent support",
    "consequent support",
    "support",
    "confidence",
    "lift",
    "leverage",
    "conviction",
    "odds ratio",
    "raw p-value",
    "adjusted p-value",
    "joint count",
    "antecedent count",
    "consequent count",
    "transaction count",
    "directional p-value",
    "directional adjusted p-value",
]


def adjustPValuesHolm(pValues: list[float]) -> list[float]:
    """使用 Holm 步降法校正一组 P 值，控制族错误率。"""
    if not pValues:
        return []

    orderedIndices = sorted(range(len(pValues)), key=pValues.__getitem__)
    adjustedValues = [1.0] * len(pValues)
    runningMaximum = 0.0
    hypothesisCount = len(pValues)

    for rank, originalIndex in enumerate(orderedIndices):
        candidate = min(1.0, (hypothesisCount - rank) * float(pValues[originalIndex]))
        runningMaximum = max(runningMaximum, candidate)
        adjustedValues[originalIndex] = runningMaximum

    return adjustedValues


def _emptyRuleFrame(
    transactionCount: int,
    testedPairCount: int,
    familyWiseAlpha: float,
    minJointCount: int,
) -> pd.DataFrame:
    frame = pd.DataFrame(columns=RULE_COLUMNS)
    frame.attrs.update(
        {
            "transactionCount": transactionCount,
            "testedPairCount": testedPairCount,
            "testedHypothesisCount": testedPairCount * 2,
            "familyWiseAlpha": familyWiseAlpha,
            "minJointCount": minJointCount,
        }
    )
    return frame


def mineAssociationRules(
    transactions: list[list[str]],
    minSupport: float = 0.01,
    minConfidence: float = 0.5,
    minJointCount: int = 3,
    familyWiseAlpha: float = 0.05,
) -> pd.DataFrame:
    """挖掘句子级两两正关联，并用 Fisher 精确检验与 Holm 法筛选。"""
    normalizedTransactions = [
        {str(item).strip() for item in transaction if str(item).strip()}
        for transaction in transactions
    ]
    transactionCount = len(normalizedTransactions)
    allItems = sorted(set().union(*normalizedTransactions)) if transactions else []
    testedPairCount = len(allItems) * (len(allItems) - 1) // 2

    if transactionCount == 0 or testedPairCount == 0:
        return _emptyRuleFrame(
            transactionCount,
            testedPairCount,
            familyWiseAlpha,
            minJointCount,
        )

    itemCounts = {
        item: sum(item in transaction for transaction in normalizedTransactions)
        for item in allItems
    }
    directionalStatistics = []

    for leftItem, rightItem in combinations(allItems, 2):
        leftCount = itemCounts[leftItem]
        rightCount = itemCounts[rightItem]
        jointCount = sum(
            leftItem in transaction and rightItem in transaction
            for transaction in normalizedTransactions
        )
        leftOnlyCount = leftCount - jointCount
        rightOnlyCount = rightCount - jointCount
        neitherCount = transactionCount - leftCount - rightCount + jointCount
        for antecedent, consequent, antecedentCount, consequentCount in (
            (leftItem, rightItem, leftCount, rightCount),
            (rightItem, leftItem, rightCount, leftCount),
        ):
            # Fisher 检验的是无向 2×2 关联;方向规则的置信度阈值另行应用。
            fisherResult = fisher_exact(
                [
                    [jointCount, antecedentCount - jointCount],
                    [consequentCount - jointCount, neitherCount],
                ],
                alternative="greater",
            )
            directionalStatistics.append(
                {
                    "antecedent": antecedent,
                    "consequent": consequent,
                    "antecedentCount": antecedentCount,
                    "consequentCount": consequentCount,
                    "jointCount": jointCount,
                    "oddsRatio": float(fisherResult.statistic),
                    "rawPValue": float(fisherResult.pvalue),
                }
            )

    adjustedPValues = adjustPValuesHolm(
        [rule["rawPValue"] for rule in directionalStatistics]
    )
    rules = []

    for direction, adjustedPValue in zip(directionalStatistics, adjustedPValues):
        antecedentCount = direction["antecedentCount"]
        consequentCount = direction["consequentCount"]
        jointCount = direction["jointCount"]
        support = jointCount / transactionCount
        antecedentSupport = antecedentCount / transactionCount
        consequentSupport = consequentCount / transactionCount
        leverage = support - antecedentSupport * consequentSupport
        lift = (
            support / (antecedentSupport * consequentSupport)
            if antecedentSupport and consequentSupport
            else 0.0
        )

        if (
            jointCount < minJointCount
            or support < minSupport
            or lift <= 1.0
            or adjustedPValue > familyWiseAlpha
        ):
            continue

        confidence = jointCount / antecedentCount if antecedentCount else 0.0
        if confidence < minConfidence:
            continue

        conviction = (
            (1.0 - consequentSupport) / (1.0 - confidence)
            if confidence < 1.0
            else inf
        )
        rules.append(
                {
                    "antecedents": frozenset({direction["antecedent"]}),
                    "consequents": frozenset({direction["consequent"]}),
                    "antecedent support": antecedentSupport,
                    "consequent support": consequentSupport,
                    "support": support,
                    "confidence": confidence,
                    "lift": lift,
                    "leverage": leverage,
                    "conviction": conviction,
                    "odds ratio": direction["oddsRatio"],
                    "raw p-value": direction["rawPValue"],
                    "adjusted p-value": adjustedPValue,
                    "joint count": jointCount,
                    "antecedent count": antecedentCount,
                    "consequent count": consequentCount,
                    "transaction count": transactionCount,
                    "directional p-value": direction["rawPValue"],
                    "directional adjusted p-value": adjustedPValue,
                }
            )

    if not rules:
        return _emptyRuleFrame(
            transactionCount,
            testedPairCount,
            familyWiseAlpha,
            minJointCount,
        )

    result = pd.DataFrame(rules, columns=RULE_COLUMNS)
    result = result.sort_values(
        ["adjusted p-value", "lift", "confidence", "joint count"],
        ascending=[True, False, False, False],
    ).reset_index(drop=True)
    result.attrs.update(
        {
            "transactionCount": transactionCount,
            "testedPairCount": testedPairCount,
            "testedHypothesisCount": testedPairCount * 2,
            "familyWiseAlpha": familyWiseAlpha,
            "minJointCount": minJointCount,
        }
    )
    return result
