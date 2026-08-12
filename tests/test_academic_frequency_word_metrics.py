import math

import pytest

from app.view.widgets.freq_analyzer.freq_engine import FrequencyAnalyzer
from app.view.widgets.freq_analyzer.word_analysis_engine import WordAnalysisEngine


def test_frequency_normalization_uses_full_analysis_token_total() -> None:
    analyzer = FrequencyAnalyzer(useStopwords=False)
    analyzer.segmenter.tokenize = lambda text, useJieba=True: text.split()

    unfiltered = analyzer.analyzeTexts(["alpha alpha beta"], minFreq=1)
    thresholded = analyzer.analyzeTexts(["alpha alpha beta"], minFreq=2)

    unfilteredAlpha = unfiltered.loc[unfiltered["Word"] == "alpha"].iloc[0]
    thresholdedAlpha = thresholded.loc[thresholded["Word"] == "alpha"].iloc[0]

    assert list(thresholded["Word"]) == ["alpha"]
    assert thresholdedAlpha["Pct"] == pytest.approx(200 / 3)
    assert thresholdedAlpha["Pmw"] == pytest.approx(2_000_000 / 3)
    assert thresholdedAlpha["Pct"] == pytest.approx(unfilteredAlpha["Pct"])
    assert thresholdedAlpha["Pmw"] == pytest.approx(unfilteredAlpha["Pmw"])


def test_mattr_retains_a_type_when_one_duplicate_leaves_the_window() -> None:
    engine = WordAnalysisEngine()

    result = engine._mattr(["a", "b", "a", "c"], windowSize=3)

    expected = ((2 / 3) + 1.0) / 2
    assert result == pytest.approx(expected)


def test_type_token_curve_counts_each_new_type_once_per_segment() -> None:
    engine = WordAnalysisEngine()

    curve = engine._typeTokenCurve(["a", "a", "b", "b", "c"], step=2)

    assert [(point.tokenCount, point.typeCount) for point in curve] == [
        (0, 0),
        (2, 1),
        (4, 2),
        (5, 3),
    ]
    assert [(point.newTypes, point.growthRate) for point in curve[1:]] == [
        (1, 0.5),
        (1, 0.5),
        (1, 1.0),
    ]


def test_analyze_populates_declared_guiraud_metric() -> None:
    engine = WordAnalysisEngine()

    metrics = engine.analyze(["a", "b", "a", "c"], mattrWindow=3, curveStep=2)

    assert metrics.guiraud == pytest.approx(3 / math.sqrt(4))
    assert "guirauD" not in vars(metrics)
