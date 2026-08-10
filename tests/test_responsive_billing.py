# coding: utf-8
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton

from app.core.services import paid_export as paidExportModule
from app.core.services import paid_metered as paidMeteredModule
from app.core.services.feature_gate import GateResult
from app.core.services.responsive_call import runResponsiveCall


class _FakeButton:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, text: str) -> None:
        self.text = text


class _FakeMessageBox:
    instances: list["_FakeMessageBox"] = []

    def __init__(self, title: str, body: str, parent=None) -> None:
        self.title = title
        self.body = body
        self.parent = parent
        self.hidden = False
        self.deletedLater = False
        self.yesButton = _FakeButton()
        self.cancelButton = _FakeButton()
        self.instances.append(self)

    def exec(self) -> int:
        return 1

    def hide(self) -> None:
        self.hidden = True

    def deleteLater(self) -> None:
        self.deletedLater = True


class _FakeGate:
    def __init__(self, delay: float = 0.06) -> None:
        self.delay = delay
        self.workerThreadId = 0

    def requireFeature(self, featureCode: str, **kwargs) -> GateResult:
        self.workerThreadId = threading.get_ident()
        time.sleep(self.delay)
        return GateResult(
            ok=True,
            reason="ok",
            context={
                "preauth": {"billId": "bill-1", "estimatedCost": 7},
                "featureCode": featureCode,
                "estimatedCost": 7,
                "resourceUsed": int(kwargs.get("resourceUsed", 0)),
                "refund": lambda: {},
            },
        )

    def handleBlockReason(self, result: GateResult, parent=None) -> None:
        raise AssertionError(f"不应阻断计费: {result.reason}")


def testRunResponsiveCallKeepsQtEventLoopAlive(qtbot) -> None:
    mainThreadId = threading.get_ident()
    timerThreadIds: list[int] = []
    workerThreadIds: list[int] = []
    cursorStates = []
    buttonClicks: list[bool] = []
    button = QPushButton("等待期间仍可操作")
    qtbot.addWidget(button)
    button.clicked.connect(lambda: buttonClicks.append(True))

    def interactDuringWait() -> None:
        timerThreadIds.append(threading.get_ident())
        cursorStates.append(QApplication.overrideCursor())
        button.click()

    QTimer.singleShot(10, interactDuringWait)

    def operation() -> str:
        workerThreadIds.append(threading.get_ident())
        time.sleep(0.06)
        return "完成"

    assert runResponsiveCall(operation) == "完成"
    assert timerThreadIds == [mainThreadId]
    assert workerThreadIds and workerThreadIds[0] != mainThreadId
    assert cursorStates == [None]
    assert buttonClicks == [True]


def testPaidMeteredHidesConfirmationBeforePreauth(qtbot, monkeypatch) -> None:
    _FakeMessageBox.instances = []
    gate = _FakeGate()
    visibleDuringWait: list[bool] = []
    monkeypatch.setattr(paidMeteredModule, "MessageBox", _FakeMessageBox)
    monkeypatch.setattr(paidMeteredModule, "_catalogCost", lambda *_args: 7)
    monkeypatch.setattr(paidMeteredModule, "getFeatureGate", lambda: gate)
    QTimer.singleShot(
        10,
        lambda: visibleDuringWait.append(
            any(not box.hidden for box in _FakeMessageBox.instances)
        ),
    )

    transaction = paidMeteredModule.beginPaidMeteredAction(
        None,
        paidMeteredModule.HSK_DOWNLOAD_FEATURE,
        100,
        "下载 100 条 HSK 语料",
    )

    assert transaction is not None
    assert visibleDuringWait == [False]
    assert _FakeMessageBox.instances[0].deletedLater is True
    assert gate.workerThreadId != threading.get_ident()


def testPaidExportHidesConfirmationBeforePreauth(qtbot, monkeypatch) -> None:
    _FakeMessageBox.instances = []
    gate = _FakeGate()
    visibleDuringWait: list[bool] = []

    class _Catalog:
        def fixedCost(self, _featureCode: str) -> int:
            return 7

    monkeypatch.setattr(paidExportModule, "MessageBox", _FakeMessageBox)
    monkeypatch.setattr(paidExportModule, "getPricingCatalog", lambda: _Catalog())
    monkeypatch.setattr(paidExportModule, "getFeatureGate", lambda: gate)
    QTimer.singleShot(
        10,
        lambda: visibleDuringWait.append(
            any(not box.hidden for box in _FakeMessageBox.instances)
        ),
    )

    transaction = paidExportModule.beginPaidAnalysisExport(None, "词频表 CSV")

    assert transaction is not None
    assert visibleDuringWait == [False]
    assert _FakeMessageBox.instances[0].deletedLater is True
    assert gate.workerThreadId != threading.get_ident()
