"""构式分析学术统计回归测试。"""

from __future__ import annotations

from app.core.services.insight_prompts import (
    buildConstructionPrompt,
    summarizeConstructionData,
)
from app.view.widgets.freq_analyzer.construction_engine import ConstructionEngine


def _constructionResult():
    return ConstructionEngine().analyze(
        tokens=["run", "mid", "run", "end", "see", "mid", "see", "end"],
        posTags=["v", "d", "v", "x", "v", "d", "v", "x"],
        patternStr="<V> mid <V> end",
        minFreq=1,
        slotMiThreshold=1.0,
    )


def testConstructionDoesNotInventOverallG2WithoutBaseline() -> None:
    result = _constructionResult()

    assert result.matchCount == 2
    assert result.overallInferenceAvailable is False
    assert "不计算" in result.overallInferenceNote
    assert not hasattr(result, "logLikelihood")
    assert not hasattr(result, "isSignificant")


def testSlotMiThresholdIsLabeledAsAssociationStrength() -> None:
    result = _constructionResult()

    assert result.slotEntries
    assert all(entry.meetsMiThreshold for entry in result.slotEntries)
    assert not hasattr(result.slotEntries[0], "isSignificant")

    summary = summarizeConstructionData(result)
    prompt = buildConstructionPrompt(summary, {"corpusName": "demo"})["user"]
    assert "不报告构式整体 G²/p 值" in prompt
    assert "MI 是效应强度,不是 p 值" in prompt
    assert "Log-Likelihood=" not in prompt

