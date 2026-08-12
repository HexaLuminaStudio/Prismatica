import pandas as pd
import pytest

from app.view.widgets.freq_analyzer.freq_engine import FrequencyAnalyzer


def test_zipf_fit_includes_rank_one_and_hapax_legomena():
    source = pd.DataFrame(
        {
            "Rank": [1, 2, 3, 4],
            "Word": ["a", "b", "c", "d"],
            "Freq": [8, 4, 2, 1],
        }
    )
    result = FrequencyAnalyzer().computeZipf(source)

    assert int(result["ZipfFitN"].iloc[0]) == 4
    assert result["ZipfFitLogFreq"].notna().all()
    assert result.loc[result["Rank"] == 1, "LogRank"].iloc[0] == 0
    assert result.loc[result["Freq"] == 1, "LogFreq"].iloc[0] == 0
    assert result["ZipfAlpha"].iloc[0] == pytest.approx(1.459, abs=0.001)
