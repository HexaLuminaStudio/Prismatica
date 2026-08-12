# coding: utf-8
"""语料持久化恢复与分析面板首次同步回归。"""
from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QTableWidget

from app.view.freq_analyzer_interface import FreqAnalyzerInterface
from app.view.widgets.freq_analyzer import freq_analyzer_widget as freqWidgetModule
from app.view.widgets.freq_analyzer.corpus_store import CorpusStore
from app.view.widgets.freq_analyzer.freq_analyzer_widget import FreqAnalyzerWidget
from app.view.widgets.freq_analyzer.freq_engine import CleanRule


def test_corpus_store_restores_cleaned_texts_after_restart(tmp_path) -> None:
    dbPath = tmp_path / "restart.db"
    store = CorpusStore(dbPath=str(dbPath))
    store.addRawTexts(
        {
            "first.txt": "Hello   世界",
            "second.txt": "再次   启动",
        }
    )
    store.commitCleanState(CleanRule(lowercase=True), enabled=True)
    expectedTexts = store.effectiveTexts()
    store.close()

    restoredStore = CorpusStore(dbPath=str(dbPath))
    try:
        assert restoredStore.cleanEnabled is True
        assert restoredStore.cacheCoverage() == {
            "total": 2,
            "cached": 2,
            "coverage": 1.0,
        }
        assert restoredStore.effectiveTextsFromCacheOnly() == expectedTexts
    finally:
        restoredStore.close()


def test_initial_panel_sync_replays_restored_store() -> None:
    restoredStore = object()

    class FakePanel:
        def __init__(self) -> None:
            self.receivedStore = None

        def setCorpusStore(self, store) -> None:
            self.receivedStore = store

    panels = {"freqAnalyzer": FakePanel(), "sentiment": FakePanel()}
    desktop = FakePanel()
    interface = SimpleNamespace(
        corpusStore=restoredStore,
        _panels=panels,
        desktop=desktop,
    )

    FreqAnalyzerInterface._synchronizePanelsWithCorpusStore(interface)

    assert all(panel.receivedStore is restoredStore for panel in panels.values())
    assert desktop.receivedStore is restoredStore


def test_restored_frequency_panel_is_populated_by_initial_sync(
    qtbot,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(freqWidgetModule, "ProRoundTableWidget", QTableWidget)
    dbPath = tmp_path / "panel-restart.db"
    store = CorpusStore(dbPath=str(dbPath))
    store.addRawText("restored.txt", "重启后仍可分析")
    store.close()

    restoredStore = CorpusStore(dbPath=str(dbPath))
    panel = FreqAnalyzerWidget(corpusStore=restoredStore)
    qtbot.addWidget(panel)
    desktop = SimpleNamespace(setCorpusStore=lambda store: None)
    interface = SimpleNamespace(
        corpusStore=restoredStore,
        _panels={"freqAnalyzer": panel},
        desktop=desktop,
    )
    try:
        assert panel.rawTexts == {}

        FreqAnalyzerInterface._synchronizePanelsWithCorpusStore(interface)

        assert panel.rawTexts == {"restored.txt": "重启后仍可分析"}
    finally:
        restoredStore.close()


def test_frequency_analysis_uses_live_store_texts_when_local_cache_is_empty(
    monkeypatch,
) -> None:
    warnings = []
    monkeypatch.setattr(
        freqWidgetModule,
        "_showInfoBar",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    class RunningWorker:
        def isRunning(self) -> bool:
            return True

    harness = SimpleNamespace(
        rawTexts={},
        effectiveTexts={"restored.txt": "已恢复的清洗语料"},
        _worker=RunningWorker(),
    )

    FreqAnalyzerWidget._runAnalysis(harness)

    assert warnings == []
