"""搭配分析学术统计回归测试。"""

from __future__ import annotations

import math

import pytest

from app.view.widgets.freq_analyzer.collocation_engine import (
    CollocationEngine,
    ContingencyTable,
)


def _entryByWord(result, word: str):
    return next(entry for entry in result.collocates if entry.collocate == word)


def testSentenceBoundariesStopBothScanDirections() -> None:
    engine = CollocationEngine()
    result = engine.analyze(
        tokens=["far", "。", "near", "node", "inside", "！", "after"],
        nodeWord="node",
        leftSpan=3,
        rightSpan=3,
        minFreq=1,
        topN=0,
        sentenceBoundaryIndices=[2, 6],
    )

    words = {entry.collocate for entry in result.collocates}
    assert words == {"near", "inside"}
    assert "far" not in result.positionDistribution.get(-3, {})
    assert "after" not in result.positionDistribution.get(3, {})


def testOverlappingWindowsUseOneValidOpportunityPopulation() -> None:
    engine = CollocationEngine()
    result = engine.analyze(
        tokens=["node", "x", "node"],
        nodeWord="node",
        leftSpan=1,
        rightSpan=1,
        minFreq=1,
        topN=0,
    )

    entry = _entryByWord(result, "x")
    assert result.contextOpportunityCount == 4
    assert result.nodeOpportunityCount == 2
    assert entry.freq == 2
    assert entry.collocateFreq == 2
    assert entry.corpusFreq == 1
    assert entry.expectedFreq == pytest.approx(1.0)
    assert entry.mi == pytest.approx(1.0)
    assert entry.logLikelihood == pytest.approx(8.0 * math.log(2.0), abs=1e-4)

    table = ContingencyTable(
        O=entry.freq,
        R=result.nodeOpportunityCount,
        C=entry.collocateFreq,
        N=result.contextOpportunityCount,
    )
    assert (table.O11, table.O12, table.O21, table.O22) == (2, 0, 0, 2)


def testInvalidContingencyMarginsAreRejected() -> None:
    with pytest.raises(ValueError, match="O"):
        ContingencyTable(O=2, R=1, C=1, N=3)


def testDocumentBoundaryAlwaysStopsCollocation() -> None:
    engine = CollocationEngine()
    result = engine.analyze(
        tokens=["node", "x"],
        nodeWord="node",
        leftSpan=1,
        rightSpan=1,
        minFreq=1,
        topN=0,
        crossSentenceBoundary=True,
        documentBoundaryIndices=[1],
    )

    assert result.contextOpportunityCount == 0
    assert result.nodeOpportunityCount == 0
    assert result.collocates == []


def testWorkerMarksEveryFileBoundaryAsHardBoundary(qtbot) -> None:
    from app.view.widgets.freq_analyzer.collocation_widget import CollocationWorker

    class _CorpusStore:
        def effectiveTexts(self):
            return {"first.txt": "node", "second.txt": "x"}

    class _Segmenter:
        def cutJieba(self, text: str):
            return text.split()

    worker = CollocationWorker(
        corpusStore=_CorpusStore(),
        segmenter=_Segmenter(),
        nodeWord="node",
        leftSpan=1,
        rightSpan=1,
        minFreq=1,
        topN=10,
        caseSensitive=False,
        crossSentenceBoundary=True,
    )
    results = []
    errors = []
    worker.finished.connect(results.append)
    worker.failed.connect(errors.append)

    worker.run()

    assert errors == []
    assert len(results) == 1
    assert results[0].collocates == []


def testContinuityCorrectionCompatibilityFlagDoesNotAlterG2() -> None:
    engine = CollocationEngine()
    kwargs = {
        "tokens": ["node", "x", "node", "y"],
        "nodeWord": "node",
        "leftSpan": 0,
        "rightSpan": 1,
        "minFreq": 1,
        "topN": 0,
    }

    plainResult = engine.analyze(**kwargs, continuityCorrection=False)
    requestedResult = engine.analyze(**kwargs, continuityCorrection=True)
    plainValues = {
        entry.collocate: entry.logLikelihood for entry in plainResult.collocates
    }
    requestedValues = {
        entry.collocate: entry.logLikelihood for entry in requestedResult.collocates
    }

    assert requestedValues == plainValues
    assert requestedResult.continuityCorrection is False


def testMiThresholdIsAssociationStrengthNotSignificance() -> None:
    result = CollocationEngine().analyze(
        tokens=["node", "x", "node", "y"],
        nodeWord="node",
        leftSpan=0,
        rightSpan=1,
        minFreq=1,
        topN=0,
        miThreshold=0.0,
    )

    assert result.strongAssociationCount == len(result.collocates)
    assert result.miThreshold == 0.0
    assert all(entry.meetsMiThreshold for entry in result.collocates)
    assert not hasattr(result, "significantCount")
    assert not hasattr(result.collocates[0], "isSignificant")
