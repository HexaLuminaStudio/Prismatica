# coding: utf-8
"""停用词设置单一来源与分析页面去重回归。"""
from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication, QBoxLayout, QTableWidget, QWidget
from qfluentwidgets import PushButton, Theme, qconfig, setTheme

from app.core.services import stopwordService
from app.core.services.stopword_service import defaultStopwords
from app.core.utils import cfg
from app.view import freq_analyzer_interface as interfaceModule
from app.view.setting_interface import AnalysisSettingWidget
from app.view.widgets.freq_analyzer import keyword_list_widget as keywordModule
from app.view.widgets.freq_analyzer.dialogs import StopwordsDialog
from app.view.widgets.freq_analyzer import freq_analyzer_widget as freqWidgetModule
from app.view.widgets.freq_analyzer.freq_analyzer_widget import FreqAnalyzerWidget
from app.view.widgets.freq_analyzer.keyword_list_widget import KeywordListWidget
from app.view.widgets.freq_analyzer.network_widget import NetworkWidget
from app.view.widgets.prismatica_theme import shellPalette


class _SignalHook:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback, *args) -> None:
        self.callbacks.append(callback)


@pytest.fixture
def isolatedStopwordConfig():
    previousEnabled = qconfig.get(cfg.analysisStopwordsEnabled)
    previousWords = qconfig.get(cfg.analysisStopwordsJson)
    qconfig.set(cfg.analysisStopwordsEnabled, False, save=False)
    qconfig.set(cfg.analysisStopwordsJson, "", save=False)
    yield
    qconfig.set(cfg.analysisStopwordsEnabled, previousEnabled, save=False)
    qconfig.set(cfg.analysisStopwordsJson, previousWords, save=False)


def testStopwordServicePersistsOneNormalizedSource(isolatedStopwordConfig) -> None:
    assert stopwordService.words() == defaultStopwords()

    savedWords = stopwordService.saveWords(
        ["所以", "and", "所以", "", "# 注释"],
        save=False,
    )
    stopwordService.setEnabled(True, save=False)

    assert savedWords == ["所以", "and"]
    assert stopwordService.words() == ["所以", "and"]
    assert stopwordService.isEnabled() is True
    assert json.loads(qconfig.get(cfg.analysisStopwordsJson)) == ["所以", "and"]


def testAnalysisSettingCardOwnsManagementAndCompactLayout(
    qtbot,
    monkeypatch,
    isolatedStopwordConfig,
) -> None:
    originalSaveWords = stopwordService.saveWords
    monkeypatch.setattr(
        stopwordService,
        "setEnabled",
        lambda isEnabled: qconfig.set(
            cfg.analysisStopwordsEnabled,
            bool(isEnabled),
            save=False,
        ),
    )
    monkeypatch.setattr(
        stopwordService,
        "saveWords",
        lambda words: originalSaveWords(words, save=False),
    )
    monkeypatch.setattr(
        StopwordsDialog,
        "edit",
        staticmethod(lambda currentWords, parent: ["我们", "their"]),
    )

    card = AnalysisSettingWidget()
    qtbot.addWidget(card)
    card.stopwordSwitch.setChecked(True)
    card._openStopwordManager()
    card.setCompactLayout(True)

    assert stopwordService.isEnabled() is True
    assert stopwordService.words() == ["我们", "their"]
    assert card.stopwordCountLabel.text() == "2 个"
    assert all(
        group.hBoxLayout.direction() == QBoxLayout.Direction.TopToBottom
        for group in card.groupWidgets
    )
def testAnalysisPagesHaveNoLocalStopwordControls(
    qtbot,
    monkeypatch,
    isolatedStopwordConfig,
) -> None:
    monkeypatch.setattr(freqWidgetModule, "PrismaticaTableWidget", QTableWidget)
    widgets = [FreqAnalyzerWidget(), KeywordListWidget(), NetworkWidget()]
    for widget in widgets:
        qtbot.addWidget(widget)
        buttonTexts = [button.text() for button in widget.findChildren(PushButton)]
        assert all("停用词" not in text for text in buttonTexts)
        assert not hasattr(widget, "stopSwitch")
        assert not hasattr(widget, "stopwordsViewBtn")
        assert not hasattr(widget, "stopwordsBtn")


def testAllAnalysisConsumersReadGlobalStopwordConfig(
    qtbot,
    monkeypatch,
    isolatedStopwordConfig,
) -> None:
    stopwordService.saveWords(["我们", "their"], save=False)
    stopwordService.setEnabled(True, save=False)
    monkeypatch.setattr(freqWidgetModule, "PrismaticaTableWidget", QTableWidget)

    class FakeFreqWorker:
        captured = {}

        def __init__(self, texts, **kwargs):
            self.progress = _SignalHook()
            self.finished = _SignalHook()
            self.failed = _SignalHook()
            self.captured = {"texts": texts, **kwargs}
            FakeFreqWorker.captured = self.captured

        def isRunning(self) -> bool:
            return False

        def start(self) -> None:
            return None

    monkeypatch.setattr(interfaceModule, "FreqWorkerThread", FakeFreqWorker)
    freqWidget = FreqAnalyzerWidget()
    qtbot.addWidget(freqWidget)
    freqWidget.rawTexts = {"sample.txt": "我们研究 their corpus"}
    freqWidget._runAnalysis()

    assert FakeFreqWorker.captured["useStopwords"] is True
    assert FakeFreqWorker.captured["stopwords"] == {"我们", "their"}

    class FakeStore:
        def fileCount(self) -> int:
            return 1

    class FakeKeywordWorker:
        captured = {}

        def __init__(self, **kwargs):
            self.progress = _SignalHook()
            self.partialStats = _SignalHook()
            self.tableRowsReady = _SignalHook()
            self.chartDataReady = _SignalHook()
            FakeKeywordWorker.captured = kwargs

        def isRunning(self) -> bool:
            return False

    monkeypatch.setattr(keywordModule, "KeywordListWorker", FakeKeywordWorker)
    keywordWidget = KeywordListWidget()
    qtbot.addWidget(keywordWidget)
    keywordWidget._observedStore = FakeStore()
    keywordWidget._referenceStore = FakeStore()
    monkeypatch.setattr(
        keywordWidget,
        "startWorker",
        lambda worker, **kwargs: True,
    )
    keywordWidget._onRunClicked()

    assert FakeKeywordWorker.captured["useStopwords"] is True
    assert FakeKeywordWorker.captured["stopwords"] == ["我们", "their"]

    networkWidget = NetworkWidget()
    qtbot.addWidget(networkWidget)
    networkParams = networkWidget._collectParams()
    assert networkParams.stopwords == {"我们", "their"}


def testStopwordDialogAdaptsAndRefreshesTheme(
    qtbot,
    isolatedStopwordConfig,
) -> None:
    previousTheme = qconfig.theme
    parent = QWidget()
    parent.resize(468, 620)
    qtbot.addWidget(parent)
    try:
        setTheme(Theme.DARK, save=False)
        QApplication.processEvents()
        dialog = StopwordsDialog(currentWords=[], parent=parent)
        qtbot.addWidget(dialog)
        parent.show()
        dialog.show()
        QApplication.processEvents()
        darkPalette = shellPalette(True)
        assert dialog.editor.toPlainText() == ""
        assert dialog.widget.width() <= parent.width()
        assert not dialog.buttonGroup.isHidden()
        assert not dialog.yesButton.isHidden()
        assert not dialog.cancelButton.isHidden()
        assert darkPalette.text.name() in dialog.editor.styleSheet()
        assert darkPalette.border.name() in dialog.editor.styleSheet()

        setTheme(Theme.LIGHT, save=False)
        qtbot.wait(30)
        QApplication.processEvents()
        lightPalette = shellPalette(False)
        assert lightPalette.text.name() in dialog.editor.styleSheet()
        assert lightPalette.border.name() in dialog.editor.styleSheet()
    finally:
        setTheme(previousTheme, save=False)
        QApplication.processEvents()
