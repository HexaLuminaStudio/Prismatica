"""首次启动引导官方账号入口测试。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Qt, Signal

from app.core.services.cloud_api import CloudApiError
from app.core.services.official_corpus import requestOfficialCorpusToken
from app.core.utils import cfg
from app.view.widgets import guide_window as guideModule


@pytest.fixture(autouse=True)
def _useOnlineGuideMode(monkeypatch):
    """本文件验证正式版官方账号入口，显式关闭内测本地裁剪。"""
    monkeypatch.setattr(guideModule, "INTERNAL_TEST_MODE", False)


class FakeConfig:
    def __init__(self) -> None:
        self.values = {}

    def get(self, item):
        return self.values.get(item, "")

    def set(self, item, value) -> None:
        self.values[item] = value


class FakeRefreshThread(QObject):
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, token: str = "issued-token", error: str = "") -> None:
        super().__init__()
        self.token = token
        self.errorMessage = error

    def start(self) -> None:
        if self.errorMessage:
            self.error.emit(self.errorMessage)
        else:
            self.finished.emit(self.token)


class FakeCloudApi:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls = []

    def post(self, path: str, **kwargs):
        self.calls.append((path, kwargs))
        return self.payload


def testOfficialCorpusClientUsesUnauthenticatedCloudGateway() -> None:
    api = FakeCloudApi({"provider": "hsk", "token": "cloud-token"})

    token = requestOfficialCorpusToken("hsk", api=api)

    assert token == "cloud-token"
    assert api.calls == [
        (
            "/v1/resources/official-token",
            {
                "body": {"provider": "hsk"},
                "withAuth": False,
                "timeout": 35.0,
            },
        )
    ]


def testOfficialCorpusClientRejectsMismatchedProvider() -> None:
    api = FakeCloudApi({"provider": "global", "token": "wrong-token"})

    try:
        requestOfficialCorpusToken("hsk", api=api)
    except CloudApiError as error:
        assert error.code == "BAD_RESPONSE"
    else:
        raise AssertionError("provider 不一致时应拒绝响应")


def testHskGuideOfficialAccountSavesOnlyTokenAndMode(qtbot, monkeypatch) -> None:
    fakeConfig = FakeConfig()
    monkeypatch.setattr(guideModule, "qconfig", fakeConfig)
    page = guideModule.HskTokenGuideInterface()
    qtbot.addWidget(page)
    page.tokenUsernameEdit.setText("user-owned-account")
    page.tokenPasswordEdit.setText("user-owned-password")
    monkeypatch.setattr(
        page,
        "_createOfficialRefreshThread",
        lambda: FakeRefreshThread("hsk-official-token"),
    )

    qtbot.mouseClick(page.officialAccountButton, Qt.MouseButton.LeftButton)

    assert fakeConfig.values[cfg.HSKLoginToken] == "hsk-official-token"
    assert fakeConfig.values[cfg.HSKUseOfficialAccount] is True
    assert cfg.HSKLoginUsername not in fakeConfig.values
    assert cfg.HSKLoginPassword not in fakeConfig.values
    assert page.isValidated() is True
    assert "官方账号" in page.statusLabel.text()


def testGlobalGuideCustomAccountDisablesOfficialMode(qtbot, monkeypatch) -> None:
    fakeConfig = FakeConfig()
    monkeypatch.setattr(guideModule, "qconfig", fakeConfig)
    page = guideModule.GlobalTokenGuideInterface()
    qtbot.addWidget(page)
    page.tokenUsernameEdit.setText("custom-global-user")
    page.tokenPasswordEdit.setText("custom-global-password")
    monkeypatch.setattr(
        page,
        "_createRefreshThread",
        lambda _username, _password: FakeRefreshThread("global-custom-token"),
    )

    qtbot.mouseClick(page.refreshButton, Qt.MouseButton.LeftButton)

    assert fakeConfig.values[cfg.GlobalLoginToken] == "global-custom-token"
    assert fakeConfig.values[cfg.GlobalUseOfficialAccount] is False
    assert fakeConfig.values[cfg.GlobalLoginUsername] == "custom-global-user"
    assert fakeConfig.values[cfg.GlobalLoginPassword] == "custom-global-password"
    assert "自己的账号" in page.statusLabel.text()


def testOfficialAccountErrorRestoresBothActions(qtbot, monkeypatch) -> None:
    fakeConfig = FakeConfig()
    monkeypatch.setattr(guideModule, "qconfig", fakeConfig)
    page = guideModule.HskTokenGuideInterface()
    qtbot.addWidget(page)
    monkeypatch.setattr(
        page,
        "_createOfficialRefreshThread",
        lambda: FakeRefreshThread(error="官方账号暂时不可用"),
    )

    qtbot.mouseClick(page.officialAccountButton, Qt.MouseButton.LeftButton)

    assert page.officialAccountButton.isEnabled() is True
    assert page.refreshButton.isEnabled() is True
    assert page.tokenUsernameEdit.isEnabled() is True
    assert page.tokenPasswordEdit.isEnabled() is True
    assert page.isValidated() is False
    assert "官方账号暂时不可用" in page.statusLabel.text()
