# coding: utf-8
"""HSK 作文检索首次使用流程回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

import app.view.main_window as mainWindowModule
import app.view.widgets.hsk_corpus.hsk_corpus_browser as hskCorpusModule
import app.view.widgets.resource_verification_dialog as resourceDialogModule
from app.core.services.startup_database_service import (
    DatabaseResource,
    DatabaseVerificationResult,
)
from app.view.main_window import MainWindow
from app.view.widgets.account.login_dialog import LoginInterface
from app.view.widgets.hsk_corpus.hsk_corpus_browser import HskCorpusBrowser
from app.view.widgets.resource_verification_dialog import ResourceVerificationDialog


@pytest.fixture(autouse=True)
def _useOnlineResourceMode(monkeypatch):
    """本文件验证正式版在线资源修复链，显式关闭内测本地裁剪。"""
    monkeypatch.setattr(mainWindowModule, "INTERNAL_TEST_MODE", False)
    monkeypatch.setattr(hskCorpusModule, "INTERNAL_TEST_MODE", False)
    monkeypatch.setattr(resourceDialogModule, "INTERNAL_TEST_MODE", False)


def testUnavailableCorpusShowsOneInPagePreparationAction(qtbot, tmp_path) -> None:
    browser = HskCorpusBrowser()
    qtbot.addWidget(browser)
    browser._dbPath = tmp_path / "missing.db"

    browser.refreshResourceState()

    assert browser.searchBtn.isEnabled() is False
    assert browser._resourceActionButton.isHidden() is False
    assert browser._resourceActionButton.text() == "准备作文资源"
    assert "不必前往设置页" in browser._emptyStateCaption.text()
    with qtbot.waitSignal(browser.resourcePreparationRequested, timeout=1000):
        qtbot.mouseClick(browser._resourceActionButton, Qt.MouseButton.LeftButton)


def testPreparationDialogAutomaticallyRepairsAndSignalsReady(qtbot, tmp_path) -> None:
    resource = DatabaseResource(
        key="hskCorpus",
        displayName="HSK 作文数据表",
        targetPath=Path(tmp_path / "hsk_corpus.db"),
        url="https://example.invalid/hsk_corpus.db",
        tableName="hsk_corpus",
    )

    class FakeResourceService:
        def __init__(self) -> None:
            self.resources = [resource]
            self.repairCalls = 0

        def verifyResources(self):
            return [
                DatabaseVerificationResult(
                    resource,
                    self.repairCalls > 0,
                    "完整性正常" if self.repairCalls else "文件不存在",
                    rowCount=11337 if self.repairCalls else 0,
                    fileSize=1024 if self.repairCalls else 0,
                )
            ]

        def downloadResources(
            self,
            resources,
            onProgress=None,
            onStatus=None,
            isCancelled=None,
        ) -> None:
            assert list(resources) == [resource]
            assert not isCancelled()
            self.repairCalls += 1
            if onStatus is not None:
                onStatus("正在下载 HSK 作文数据表…")
            if onProgress is not None:
                onProgress(1, 1, resource.displayName, 1024, 1024, 100)

    service = FakeResourceService()
    parent = QWidget()
    parent.resize(1000, 720)
    qtbot.addWidget(parent)
    dialog = ResourceVerificationDialog(
        service=service,
        parent=parent,
        autoRepair=True,
    )
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(dialog.resourcesReady, timeout=3000):
        dialog.show()

    qtbot.waitUntil(lambda: dialog._verificationThread is None, timeout=2000)
    assert service.repairCalls == 1
    assert dialog.allResourcesValid is True
    assert dialog._state == "completed"


def testLoginReturnsToCorpusAndContinuesPreparation(monkeypatch) -> None:
    class FakeCloudApi:
        def isLoggedIn(self) -> bool:
            return False

    class FakeLoginInterface:
        def __init__(self) -> None:
            self.contextMessage = ""

        def prepareForLogin(self, contextMessage: str = "") -> None:
            self.contextMessage = contextMessage

    class FakeCorpusInterface:
        def __init__(self) -> None:
            self.preparationStarts = 0

        def startResourcePreparation(self) -> None:
            self.preparationStarts += 1

    class FakeAccountInterface:
        def __init__(self) -> None:
            self.refreshCalls = 0

        def refresh(self) -> None:
            self.refreshCalls += 1

    class FakeWindow:
        def __init__(self) -> None:
            self.loginInterface = FakeLoginInterface()
            self.hskCorpusInterface = FakeCorpusInterface()
            self.accountInterface = FakeAccountInterface()
            self._postLoginInterface = None
            self._resumeHskResourcePreparation = False
            self.switchedInterfaces = []
            self.sessionChanges = []

        def switchTo(self, interface) -> None:
            self.switchedInterfaces.append(interface)

        def _onCloudSessionChanged(self, loggedIn: bool) -> None:
            self.sessionChanges.append(loggedIn)

    monkeypatch.setattr(mainWindowModule, "getCloudApi", lambda: FakeCloudApi())
    window = FakeWindow()

    MainWindow._onHskCorpusResourcePreparationRequested(window)

    assert window.switchedInterfaces == [window.loginInterface]
    assert "自动返回 HSK 作文检索" in window.loginInterface.contextMessage
    assert window._postLoginInterface is window.hskCorpusInterface

    MainWindow._onLoginSucceeded(window)

    assert window.sessionChanges == [True]
    assert window.switchedInterfaces[-1] is window.hskCorpusInterface
    assert window.hskCorpusInterface.preparationStarts == 1
    assert window.accountInterface.refreshCalls == 0
    assert window._postLoginInterface is None
    assert window._resumeHskResourcePreparation is False


def testLoggedInUserStartsPreparationWithoutLeavingPage(monkeypatch) -> None:
    class FakeCloudApi:
        def isLoggedIn(self) -> bool:
            return True

    class FakeCorpusInterface:
        def __init__(self) -> None:
            self.preparationStarts = 0

        def startResourcePreparation(self) -> None:
            self.preparationStarts += 1

    class FakeWindow:
        def __init__(self) -> None:
            self.hskCorpusInterface = FakeCorpusInterface()

    monkeypatch.setattr(mainWindowModule, "getCloudApi", lambda: FakeCloudApi())
    window = FakeWindow()

    MainWindow._onHskCorpusResourcePreparationRequested(window)

    assert window.hskCorpusInterface.preparationStarts == 1


def testLoginPageExplainsAutomaticReturn(qtbot, monkeypatch) -> None:
    interface = LoginInterface()
    qtbot.addWidget(interface)
    interface._switchTab(1, animate=False)

    interface.prepareForLogin("登录后将自动返回 HSK 作文检索")

    assert interface._stack.currentIndex() == 0
    assert interface._loginSubtitleLabel.text() == "登录后将自动返回 HSK 作文检索"
