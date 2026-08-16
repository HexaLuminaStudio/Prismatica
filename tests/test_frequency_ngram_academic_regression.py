"""N-gram 原始邻接关系的学术回归测试。"""

from __future__ import annotations

from app.view.widgets.freq_analyzer import freq_engine as freqEngineModule
from app.view.widgets.freq_analyzer.freq_engine import FrequencyAnalyzer


def testNgramsDoNotCrossPunctuationOrSentenceBoundaries() -> None:
    analyzer = FrequencyAnalyzer(useJieba=False)

    result = analyzer.analyzeNgrams({"demo.txt": "A. B C\nD E"}, n=2)

    assert set(result["Ngram"]) == {"b c", "d e"}
    assert "a b" not in set(result["Ngram"])
    assert "c d" not in set(result["Ngram"])


def testPosFilteringDoesNotJoinTokensAcrossRemovedToken(monkeypatch) -> None:
    analyzer = FrequencyAnalyzer(
        useJieba=False,
        posTags={"n"},
        posEnabled=True,
    )
    monkeypatch.setattr(
        freqEngineModule,
        "posTagBatch",
        lambda texts: [[("alpha", "n"), ("skip", "x"), ("beta", "n")]],
    )

    result = analyzer.analyzeNgrams({"demo.txt": "alpha skip beta"}, n=2)

    assert result.empty


def testStopwordFilteringDoesNotJoinTokensAcrossRemovedToken() -> None:
    analyzer = FrequencyAnalyzer(
        useJieba=False,
        useStopwords=True,
        stopwords={"skip"},
    )

    result = analyzer.analyzeNgrams({"demo.txt": "alpha skip beta"}, n=2)

    assert result.empty
