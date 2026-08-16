# coding: utf-8
"""内测本地模式的云端隔离与本地旁路回归测试。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QLabel, QPushButton

from app.core.services import cloud_api as cloudApiModule
from app.core.services import global_service as globalServiceModule
from app.core.services import hsk_service as hskServiceModule
from app.core.services import paid_export as paidExportModule
from app.core.services import paid_metered as paidMeteredModule
from app.core.utils.setting import INTERNAL_TEST_MODE
from app.view.widgets.freq_analyzer import ai_insight_mixin as aiMixinModule
from app.view.setting_interface import AgreementLabelWidget, SettingInterface
from app.view.main_window import MainWindow
from app.view.widgets.project_manager_widget import _EmptyProjectState
from app.view.widgets.splash_window import SplashWindow


def _failCloudPath(*_args, **_kwargs):
    raise AssertionError("内测本地模式不应进入云端链路")


def testInternalTestModeEnabledForBetaBuild() -> None:
    assert INTERNAL_TEST_MODE is True


def testCloudApiRejectsBeforeCreatingHttpRequest(monkeypatch) -> None:
    api = cloudApiModule.CloudApi()
    monkeypatch.setattr(api, "_requestOnce", _failCloudPath)

    with pytest.raises(cloudApiModule.CloudApiError) as errorInfo:
        api.get("/healthz", withAuth=False)

    assert errorInfo.value.code == "CLOUD_DISABLED"
    assert api.isLoggedIn() is False


def testInternalOfficialTokenExceptionUsesOnlyFixedUnauthenticatedRoute(
    monkeypatch,
) -> None:
    api = cloudApiModule.CloudApi()
    calls = []
    monkeypatch.setattr(
        cloudApiModule.cfg.cloudBaseUrl,
        "value",
        "https://internal-guide.example.test",
    )

    assert api._baseUrl(allowInternalTestOfficialCorpus=True) == (
        "https://internal-guide.example.test"
    )

    def fakeRequestBlocking(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"provider": "hsk", "token": "guide-token"}

    monkeypatch.setattr(api, "_requestBlocking", fakeRequestBlocking)

    payload = api.requestOfficialCorpusToken(
        "hsk",
        allowInternalTestGuideRequest=True,
    )

    assert payload == {"provider": "hsk", "token": "guide-token"}
    assert calls == [
        (
            "POST",
            "/v1/resources/official-token",
            {
                "body": {"provider": "hsk"},
                "withAuth": False,
                "idempotencyKey": None,
                "timeout": 35.0,
                "allowInternalTestOfficialCorpus": True,
            },
        )
    ]

    with pytest.raises(ValueError):
        api.requestOfficialCorpusToken(
            "billing",
            allowInternalTestGuideRequest=True,
        )


def testLocalPaidActionsSkipCatalogGateAndBilling(monkeypatch) -> None:
    monkeypatch.setattr(paidExportModule, "INTERNAL_TEST_MODE", True)
    monkeypatch.setattr(paidMeteredModule, "INTERNAL_TEST_MODE", True)
    monkeypatch.setattr(paidExportModule, "getPricingCatalog", _failCloudPath)
    monkeypatch.setattr(paidExportModule, "getFeatureGate", _failCloudPath)
    monkeypatch.setattr(paidMeteredModule, "getPricingCatalog", _failCloudPath)
    monkeypatch.setattr(paidMeteredModule, "getFeatureGate", _failCloudPath)

    exportTransaction = paidExportModule.beginPaidAnalysisExport(None, "本地 CSV")
    downloadTransaction = paidMeteredModule.beginPaidMeteredAction(
        None,
        paidMeteredModule.HSK_DOWNLOAD_FEATURE,
        123,
        "本地 HSK 下载",
    )

    assert exportTransaction is not None
    assert exportTransaction.estimatedCost == 0
    assert exportTransaction.commit() is True
    assert downloadTransaction is not None
    assert downloadTransaction.estimatedCost == 0
    taskInfo = {"url": "https://hsk.blcu.edu.cn"}
    downloadTransaction.attachToTaskInfo(taskInfo)
    assert "_billing" not in taskInfo
    assert downloadTransaction.commit() is True


def testOfficialCorpusGatewayForcedOffButCustomSourceRemains(monkeypatch) -> None:
    monkeypatch.setattr(hskServiceModule, "INTERNAL_TEST_MODE", True)
    monkeypatch.setattr(globalServiceModule, "INTERNAL_TEST_MODE", True)

    hskService = hskServiceModule.HskTokenRefreshThread(useOfficial=True)
    globalService = globalServiceModule.GlobalTokenRefreshThread(useOfficial=True)

    assert hskService.useOfficial is False
    assert globalService.useOfficial is False

    hskGuideService = hskServiceModule.HskTokenRefreshThread(
        useOfficial=True,
        allowInternalTestGuideRequest=True,
    )
    globalGuideService = globalServiceModule.GlobalTokenRefreshThread(
        useOfficial=True,
        allowInternalTestGuideRequest=True,
    )

    assert hskGuideService.useOfficial is True
    assert globalGuideService.useOfficial is True

    calls = []
    monkeypatch.setattr(
        hskServiceModule,
        "requestOfficialCorpusToken",
        lambda provider, **kwargs: calls.append((provider, kwargs)) or "hsk-token",
    )
    monkeypatch.setattr(
        globalServiceModule,
        "requestOfficialCorpusToken",
        lambda provider, **kwargs: calls.append((provider, kwargs)) or "global-token",
    )

    hskGuideService.run()
    globalGuideService.run()

    assert calls == [
        ("hsk", {"allowInternalTestGuideRequest": True}),
        ("global", {"allowInternalTestGuideRequest": True}),
    ]


def testAiInsightButtonNotExposedInLocalMode(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(aiMixinModule, "INTERNAL_TEST_MODE", True)

    class LocalMixin(aiMixinModule.AiInsightMixin):
        pass

    button = QPushButton("AI 解读")
    qtbot.addWidget(button)
    LocalMixin().setupAiInsightButton(button)

    assert button.isHidden() is True
    assert button.isEnabled() is False


def testCloudSettingsAndPricingEntryAreNotExposed(qtbot) -> None:
    settingInterface = SettingInterface()
    footer = AgreementLabelWidget()
    qtbot.addWidget(settingInterface)
    qtbot.addWidget(footer)

    assert settingInterface.aiChatSettingWidget is None
    assert settingInterface.aiInsightSettingWidget is None
    assert footer.layout().indexOf(footer.pricingStatusLabel) == -1
    assert footer.pricingStatusLabel.isHidden() is True
    assert footer.separator.isHidden() is True
    assert footer.layout().indexOf(footer.userAgreementLabel) >= 0


def testSplashUsesFinalSizeBeforeFirstShow(qtbot) -> None:
    splash = SplashWindow()
    qtbot.addWidget(splash)

    assert splash.size() == QSize(520, 400)
    assert splash.minimumSize() == QSize(520, 400)
    assert splash.maximumSize() == QSize(520, 400)

    splash.show()
    qtbot.waitExposed(splash)
    assert splash.size() == QSize(520, 400)


def testProjectEmptyStateContainsNoCloudOrAiCopy(qtbot) -> None:
    emptyState = _EmptyProjectState()
    qtbot.addWidget(emptyState)
    visibleCopy = "\n".join(
        label.text() for label in emptyState.findChildren(QLabel) if not label.isHidden()
    )

    assert "AI" not in visibleCopy
    assert "登录" not in visibleCopy
    assert "跨设备" not in visibleCopy
    assert "仅保存在本机" in visibleCopy


def testMainNavigationDoesNotRequireCloudPages() -> None:
    class FakeNavigation:
        def addSectionHeader(self, *_args, **_kwargs) -> None:
            pass

    class FakeSplash:
        def finish(self) -> None:
            pass

    class FakeWindow:
        def __init__(self) -> None:
            self.hskInterface = object()
            self.hskCorpusInterface = object()
            self.globalInterface = object()
            self.biasInterface = object()
            self.freqAnalyzerInterface = object()
            self.projectInterface = object()
            self.taskInterface = object()
            self.settingInterface = object()
            self.navigationInterface = FakeNavigation()
            self.splashScreen = FakeSplash()
            self.labels = []

        def addSubInterface(self, _interface, _icon, label, **_kwargs):
            self.labels.append(label)
            return object()

        def _connectTaskNavigationBadge(self) -> None:
            pass

    window = FakeWindow()
    MainWindow.initNavigation(window)

    assert "AI 聊天" not in window.labels
    assert "HSK下载" in window.labels
    assert "全球中介下载" in window.labels
    assert not hasattr(window, "accountNav")
