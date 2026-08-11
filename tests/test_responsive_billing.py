# coding: utf-8
from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton

from app.core.services import cloud_billing as cloudBillingModule
from app.core.services import paid_export as paidExportModule
from app.core.services import paid_metered as paidMeteredModule
from app.core.services.feature_gate import GateResult
from app.core.services.paid_action_guard import PaidActionRegistry
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
        self.calls: list[tuple[str, dict]] = []

    def requireFeature(self, featureCode: str, **kwargs) -> GateResult:
        self.workerThreadId = threading.get_ident()
        self.calls.append((featureCode, dict(kwargs)))
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
    transaction.refund()


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
    transaction.refund()


def testPaidMeteredBlocksReentrantDuplicateAndReusesOperationId(
    qtbot,
    monkeypatch,
) -> None:
    gate = _FakeGate()
    nestedResults = []
    description = "下载 321 条防重入测试语料"
    monkeypatch.setattr(paidMeteredModule, "_catalogCost", lambda *_args: 7)
    monkeypatch.setattr(paidMeteredModule, "getFeatureGate", lambda: gate)

    QTimer.singleShot(
        10,
        lambda: nestedResults.append(
            paidMeteredModule.beginPaidMeteredAction(
                None,
                paidMeteredModule.HSK_DOWNLOAD_FEATURE,
                321,
                description,
                showConfirmation=False,
            )
        ),
    )
    transaction = paidMeteredModule.beginPaidMeteredAction(
        None,
        paidMeteredModule.HSK_DOWNLOAD_FEATURE,
        321,
        description,
        showConfirmation=False,
    )

    assert transaction is not None
    try:
        assert nestedResults == [None]
        assert len(gate.calls) == 1
        _, kwargs = gate.calls[0]
        operationId = kwargs["idempotencyKey"]
        assert operationId
        assert kwargs["taskId"] == (
            f"{paidMeteredModule.HSK_DOWNLOAD_FEATURE}:{operationId}"
        )
    finally:
        transaction.refund()

    nextTransaction = paidMeteredModule.beginPaidMeteredAction(
        None,
        paidMeteredModule.HSK_DOWNLOAD_FEATURE,
        321,
        description,
        showConfirmation=False,
    )
    assert nextTransaction is not None
    nextTransaction.refund()


def testPaidExportBlocksReentrantDuplicate(qtbot, monkeypatch) -> None:
    gate = _FakeGate()
    nestedResults = []
    description = "防重入测试词频表 CSV"

    class _Catalog:
        def fixedCost(self, _featureCode: str) -> int:
            return 7

    monkeypatch.setattr(paidExportModule, "MessageBox", _FakeMessageBox)
    monkeypatch.setattr(paidExportModule, "getPricingCatalog", lambda: _Catalog())
    monkeypatch.setattr(paidExportModule, "getFeatureGate", lambda: gate)
    QTimer.singleShot(
        10,
        lambda: nestedResults.append(
            paidExportModule.beginPaidAnalysisExport(None, description)
        ),
    )

    transaction = paidExportModule.beginPaidAnalysisExport(None, description)

    assert transaction is not None
    try:
        assert nestedResults == [None]
        assert len(gate.calls) == 1
    finally:
        transaction.refund()


def testPaidMeteredReleasesGuardWhenPreauthRaises(qtbot, monkeypatch) -> None:
    description = "预占异常后的释放测试"

    class _RaisingGate:
        def requireFeature(self, _featureCode: str, **_kwargs) -> GateResult:
            raise RuntimeError("模拟预占异常")

    monkeypatch.setattr(paidMeteredModule, "_catalogCost", lambda *_args: 7)
    monkeypatch.setattr(paidMeteredModule, "getFeatureGate", lambda: _RaisingGate())
    with pytest.raises(RuntimeError, match="模拟预占异常"):
        paidMeteredModule.beginPaidMeteredAction(
            None,
            paidMeteredModule.HSK_DOWNLOAD_FEATURE,
            654,
            description,
            showConfirmation=False,
        )

    gate = _FakeGate(delay=0)
    monkeypatch.setattr(paidMeteredModule, "getFeatureGate", lambda: gate)
    transaction = paidMeteredModule.beginPaidMeteredAction(
        None,
        paidMeteredModule.HSK_DOWNLOAD_FEATURE,
        654,
        description,
        showConfirmation=False,
    )
    assert transaction is not None
    transaction.refund()


def testPaidMeteredReleasesGuardWhenCommitFails(monkeypatch) -> None:
    registry = PaidActionRegistry()
    actionLease = registry.tryAcquire("feature", 1, "结算失败释放测试")
    refunds = []

    class _Billing:
        def commitMetered(self, _billId: str):
            raise paidMeteredModule.CloudApiError("NETWORK_ERROR", "模拟结算失败")

    assert actionLease is not None
    result = GateResult(
        ok=True,
        reason="ok",
        context={
            "preauth": {"billId": "bill-failure"},
            "refund": lambda: refunds.append(True) or {},
        },
    )
    transaction = paidMeteredModule.PaidMeteredTransaction(result, actionLease)
    monkeypatch.setattr(paidMeteredModule, "getCloudBilling", lambda: _Billing())

    assert transaction.commit() is False
    assert refunds == [True]
    assert actionLease.isReleased is True


def testPaidActionRegistryOnlyBlocksSameIntent() -> None:
    registry = PaidActionRegistry()
    firstLease = registry.tryAcquire("feature", 1, "导出 A")
    secondLease = registry.tryAcquire("feature", 1, "导出 B")
    duplicateLease = registry.tryAcquire("feature", 1, "导出 A")

    assert firstLease is not None
    assert secondLease is not None
    assert duplicateLease is None
    firstLease.release()
    secondLease.release()


def testCloudBillingForwardsExplicitIdempotencyKey(monkeypatch) -> None:
    calls = []

    class _Api:
        def post(self, path: str, *, body=None, **kwargs):
            calls.append((path, body, kwargs))
            return {"billId": "bill-1"}

    monkeypatch.setattr(cloudBillingModule, "getCloudApi", lambda: _Api())
    billing = cloudBillingModule.CloudBilling()
    billing.preauth(
        "analysis_export",
        1,
        taskId="analysis_export:operation-123",
        description="词频表 CSV",
        idempotencyKey="operation-123",
    )

    assert calls == [
        (
            "/v1/billing/preauth",
            {
                "actionType": "analysis_export",
                "resourceUsed": 1,
                "taskId": "analysis_export:operation-123",
                "description": "词频表 CSV",
            },
            {"idempotencyKey": "operation-123"},
        )
    ]
