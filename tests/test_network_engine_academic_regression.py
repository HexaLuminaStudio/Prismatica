# coding: utf-8
"""共现网络统计口径的学术回归测试。"""
from __future__ import annotations

import math

import pytest

from app.view.widgets.freq_analyzer.network_engine import (
    CooccurrenceEngine,
    EdgeWeight,
)


def testUndirectedPositionPairIsCountedOnce() -> None:
    engine = CooccurrenceEngine(useJieba=False)
    coMatrix = {}

    engine._scanCooccurrence(
        tokens=["甲", "乙"],
        candidates={"甲": 1, "乙": 1},
        windowSize=1,
        coMatrix=coMatrix,
    )

    assert coMatrix == {("乙", "甲"): 1}


def testWindowDistanceUsesOriginalTokenPositions() -> None:
    engine = CooccurrenceEngine(useJieba=False)
    coMatrix = {}

    engine._scanCooccurrence(
        tokens=["甲", "非候选词", "乙"],
        candidates={"甲": 1, "乙": 1},
        windowSize=1,
        coMatrix=coMatrix,
    )

    assert coMatrix == {}


def testPmiUsesSingleTotalTokenFactor() -> None:
    engine = CooccurrenceEngine(useJieba=False)

    weights = engine._normalizeEdgeWeights(
        coMatrix={("a", "b"): 2, ("a", "c"): 1, ("b", "c"): 1},
        candidates={"a": 4, "b": 4, "c": 2},
        method=EdgeWeight.PMI,
    )

    expectedPmi = math.log2((2 * 4) / (3 * 3))
    assert weights[("a", "b")] == pytest.approx(expectedPmi)


@pytest.mark.parametrize(
    "method",
    [EdgeWeight.NPMI, EdgeWeight.DICE, EdgeWeight.JACCARD],
)
def testWideWindowWeightsStayInProbabilityBounds(method: EdgeWeight) -> None:
    engine = CooccurrenceEngine(useJieba=False)
    coMatrix = {}
    engine._scanCooccurrence(
        tokens=["a", "b", "a", "b", "c"],
        candidates={"a": 2, "b": 2, "c": 1},
        windowSize=4,
        coMatrix=coMatrix,
    )

    weights = engine._normalizeEdgeWeights(
        coMatrix=coMatrix,
        candidates={"a": 2, "b": 2, "c": 1},
        method=method,
    )

    assert weights
    assert all(-1.0 <= value <= 1.0 for value in weights.values())
