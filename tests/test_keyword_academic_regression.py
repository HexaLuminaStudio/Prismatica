import numpy as np
import pytest

from app.view.widgets.freq_analyzer.keyword_list_engine import (
    LL_THRESHOLD_P001,
    LL_THRESHOLD_P005,
    LL_THRESHOLD_P001_LARGE,
    adjustPValuesHolm,
    analyzeKeywordList,
    significanceAlphaFromLlThreshold,
)


def test_chi_square_thresholds_map_to_stated_alpha_levels():
    assert significanceAlphaFromLlThreshold(LL_THRESHOLD_P005) == pytest.approx(0.05)
    assert significanceAlphaFromLlThreshold(LL_THRESHOLD_P001) == pytest.approx(0.01)
    assert significanceAlphaFromLlThreshold(LL_THRESHOLD_P001_LARGE) == pytest.approx(
        0.001
    )


def test_holm_adjustment_uses_the_complete_family():
    adjusted = adjustPValuesHolm(np.array([0.01, 0.02, 0.50]))
    assert adjusted == pytest.approx([0.03, 0.04, 0.50])


def test_keyword_result_reports_family_and_adjusted_p_values():
    result = analyzeKeywordList(
        ["a"] * 20 + ["b"] * 3,
        ["a"] * 2 + ["b"] * 20 + ["c"] * 3,
        minFreq=2,
        topN=2,
    )
    assert result is not None
    assert result.testedHypotheses == 3
    assert result.familyWiseAlpha == pytest.approx(0.01)
    assert {"RawP", "AdjustedP", "Direction", "IsKey"} <= set(result.df.columns)
    assert len(result.df) == 2
