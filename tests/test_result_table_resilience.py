# coding: utf-8
"""偏误与 KWIC 的主题、焦点和后台任务生命周期回归。"""
from __future__ import annotations

import pandas as pd
from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, qconfig, setTheme

from app.view import bias_interface as biasModule
from app.view.bias_interface import BiasInterface
from app.view.widgets.freq_analyzer import concordance_widget as kwicModule
from app.view.widgets.freq_analyzer.concordance_engine import KwicHit
from app.view.widgets.freq_analyzer.concordance_widget import ConcordanceWidget
from app.view.widgets.prismatica_theme import shellPalette
from app.view.widgets.result_table_models import KwicResultTableModel


class _SignalHook:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class _FakeWorker:
    instances = []

    def __init__(self, *args, **kwargs) -> None:
        self.progress = _SignalHook()
        self.finished = _SignalHook()
        self.failed = _SignalHook()
        self.running = False
        self.startCount = 0
        self.cancelCount = 0
        self.waitCalls = []
        self.deleteCount = 0
        type(self).instances.append(self)

    def start(self) -> None:
        self.startCount += 1
        self.running = True

    def isRunning(self) -> bool:
        return self.running

    def cancel(self) -> None:
        self.cancelCount += 1

    def wait(self, timeout=None) -> bool:
        self.waitCalls.append(timeout)
        self.running = False
        return True

    def deleteLater(self) -> None:
        self.deleteCount += 1


class _CancellableThread(QThread):
    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        while not self.isInterruptionRequested():
            self.msleep(5)


def _relativeLuminance(color: QColor) -> float:
    channels = []
    for channel in (color.redF(), color.greenF(), color.blueF()):
        channels.append(
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrastRatio(first: QColor, second: QColor) -> float:
    light = max(_relativeLuminance(first), _relativeLuminance(second))
    dark = min(_relativeLuminance(first), _relativeLuminance(second))
    return (light + 0.05) / (dark + 0.05)


def testDarkThemeRefreshesKwicRolesAndBiasCards(qtbot) -> None:
    previousTheme = qconfig.theme
    setTheme(Theme.LIGHT)
    model = KwicResultTableModel()
    model.setHits(
        [
            KwicHit(
                leftContext=["左侧"],
                node=["节点"],
                rightContext=["右侧"],
                sourceFile="sample.txt",
            )
        ]
    )
    changedSpy = QSignalSpy(model.dataChanged)
    bias = BiasInterface()
    qtbot.addWidget(bias)
    assert "#FFFFFF" in bias.conditionCard.styleSheet()

    try:
        setTheme(Theme.DARK)
        QTest.qWait(20)
        nodeIndex = model.index(0, 2)
        foreground = model.data(nodeIndex, Qt.ItemDataRole.ForegroundRole)
        background = model.data(nodeIndex, Qt.ItemDataRole.BackgroundRole)
        assert changedSpy.count() >= 1
        assert foreground == QColor("#FFD86A")
        assert background == QColor("#5D5016")
        assert _contrastRatio(foreground, background) >= 4.5

        assert "#2B2B2B" in bias.conditionCard.styleSheet()
        assert "#454545" in bias.resultCard.styleSheet()
        assert "#B8B8B8" in bias.statusLabel.styleSheet()
        darkText = shellPalette().text.name()
        assert _contrastRatio(QColor(darkText), QColor("#2B2B2B")) >= 4.5
        assert darkText in bias.filterTitle.styleSheet()
        for filterWidget in (bias.charFilter, bias.wordFilter, bias.sentFilter):
            for checkbox in filterWidget.checkboxes.values():
                assert darkText in checkbox.styleSheet()
    finally:
        setTheme(previousTheme)
        QTest.qWait(20)


def testExplicitFocusChainsSupportForwardAndReverseTab(qtbot) -> None:
    kwic = ConcordanceWidget()
    qtbot.addWidget(kwic)
    kwic.resize(1100, 760)
    kwic.show()
    kwic.activateWindow()
    kwic.raise_()
    QTest.qWait(30)
    kwic.searchEdit.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(kwic.searchEdit, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is kwic.regexCheck
    QTest.keyClick(kwic.regexCheck, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is kwic.caseCheck
    QTest.keyClick(
        kwic.caseCheck,
        Qt.Key.Key_Tab,
        Qt.KeyboardModifier.ShiftModifier,
    )
    assert QApplication.focusWidget() is kwic.regexCheck

    kwic.exportCsvBtn.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(kwic.exportCsvBtn, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is kwic._viewPivot.items["table"]

    bias = BiasInterface()
    qtbot.addWidget(bias)
    bias.resize(1180, 760)
    bias.columnCombobox.addItem("作文")
    bias.columnCombobox.setEnabled(True)
    bias.columnConfigBtn.setEnabled(True)
    bias.show()
    bias.activateWindow()
    bias.raise_()
    QTest.qWait(30)
    bias.chooseFileBtn.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(bias.chooseFileBtn, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is bias.columnCombobox
    QTest.keyClick(bias.columnCombobox, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is bias.columnConfigBtn
    QTest.keyClick(bias.columnConfigBtn, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is bias.filterSegment.items["character"]


def testRepeatedActionsStartOnlyOneWorker(qtbot, monkeypatch) -> None:
    _FakeWorker.instances = []
    monkeypatch.setattr(kwicModule, "ConcordanceWorker", _FakeWorker)
    kwic = ConcordanceWidget()
    qtbot.addWidget(kwic)
    kwic.searchEdit.setText("学习")
    kwic.fileToText = {"sample.txt": "学习语料"}
    monkeypatch.setattr(kwic, "_reloadEffectiveTexts", lambda: True)
    kwic.show()

    for _ in range(10):
        QTest.mouseClick(kwic.searchBtn, Qt.MouseButton.LeftButton)
    kwic._runSearch()

    assert len(_FakeWorker.instances) == 1
    assert _FakeWorker.instances[0].startCount == 1
    assert kwic.searchBtn.isEnabled() is False

    _FakeWorker.instances = []
    monkeypatch.setattr(biasModule, "MatchingWorker", _FakeWorker)
    bias = BiasInterface()
    qtbot.addWidget(bias)
    bias.dfs = {
        "sample.xlsx": pd.DataFrame({"text": ["错字[C]"]}),
    }
    bias.selectedColumn = "text"
    monkeypatch.setattr(bias.charFilter, "selectedTexts", lambda: ["错字 [C]"])
    monkeypatch.setattr(bias.wordFilter, "selectedTexts", lambda: [])
    monkeypatch.setattr(bias.sentFilter, "selectedTexts", lambda: [])
    bias._refreshAnalyzeState()
    bias.show()

    for _ in range(10):
        QTest.mouseClick(bias.analyzeBtn, Qt.MouseButton.LeftButton)
    bias._runMatching()

    assert len(_FakeWorker.instances) == 1
    assert _FakeWorker.instances[0].startCount == 1
    assert bias._isAnalysisRunning is True
    assert bias.analyzeBtn.isEnabled() is False


def testClosingPagesCancelsAndJoinsRunningTasks(qtbot) -> None:
    bias = BiasInterface()
    qtbot.addWidget(bias)
    kwic = ConcordanceWidget()
    qtbot.addWidget(kwic)

    loadThread = _CancellableThread()
    matchingThread = _CancellableThread()
    searchThread = _CancellableThread()
    loadThread.start()
    matchingThread.start()
    searchThread.start()
    qtbot.waitUntil(
        lambda: all(
            worker.isRunning()
            for worker in (loadThread, matchingThread, searchThread)
        ),
        timeout=1000,
    )
    bias.loadThread = loadThread
    bias._matchingWorker = matchingThread
    bias._isAnalysisRunning = True
    kwic._worker = searchThread

    bias.show()
    kwic.show()
    bias.close()
    kwic.close()

    assert loadThread.isRunning() is False
    assert matchingThread.isRunning() is False
    assert searchThread.isRunning() is False
    assert bias.loadThread is None
    assert bias._matchingWorker is None
    assert bias._isAnalysisRunning is False
    assert kwic._worker is None
