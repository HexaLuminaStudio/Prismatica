# coding: utf-8
"""云端连接策略、价格退避和后台登录回归测试。"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal

from app.core.services import cloud_api as cloudApiModule
from app.core.services.cloud_auth import CloudAuth, CloudLoginWorker
from app.core.services import pricing_catalog as pricingCatalogModule
from app.view import account_interface as accountInterfaceModule
from app.view.widgets.account import login_dialog as loginDialogModule
from app.view.widgets.freq_analyzer import clean_coordinator as cleanCoordinatorModule


@pytest.fixture(autouse=True)
def _useOnlineCloudMode(monkeypatch):
    """本文件验证正式版云端韧性，显式关闭内测本地硬阻断。"""
    monkeypatch.setattr(cloudApiModule, "INTERNAL_TEST_MODE", False)
    monkeypatch.setattr(pricingCatalogModule, "INTERNAL_TEST_MODE", False)


class _Response:
    status_code = 200

    def json(self):
        return {"code": "OK", "data": {"reachable": True}}


class _HttpClient:
    def __init__(self) -> None:
        self.trust_env = True
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return _Response()


def testCloudApiNeverFallsBackToRandomDeviceId(monkeypatch) -> None:
    from app.core.utils import device_id as deviceIdModule

    monkeypatch.setattr(
        deviceIdModule,
        "generateOrLoadDeviceId",
        lambda: (_ for _ in ()).throw(RuntimeError("无法持久化")),
    )

    with pytest.raises(cloudApiModule.CloudApiError) as captured:
        cloudApiModule.CloudApi()._deviceId()

    assert captured.value.code == "DEVICE_ID_UNAVAILABLE"


def testCloudApiDefaultsToDirectThreadLocalSession(monkeypatch) -> None:
    clients = []

    def createClient():
        client = _HttpClient()
        clients.append(client)
        return client

    fakeCfg = SimpleNamespace(
        cloudBaseUrl=SimpleNamespace(value="http://cloud.test"),
        cloudUseSystemProxy=SimpleNamespace(value=False),
    )
    monkeypatch.setattr(cloudApiModule, "cfg", fakeCfg)
    monkeypatch.setattr(cloudApiModule.requests, "Session", createClient)
    api = cloudApiModule.CloudApi()
    monkeypatch.setattr(api, "_headers", lambda **_kwargs: {})

    assert api.get("/healthz", withAuth=False) == {"reachable": True}
    assert api.get("/v1/pricing/catalog", withAuth=False) == {"reachable": True}
    assert len(clients) == 1
    assert clients[0].trust_env is False
    assert [call["url"] for call in clients[0].calls] == [
        "http://cloud.test/healthz",
        "http://cloud.test/v1/pricing/catalog",
    ]


def testCloudApiAllowsExplicitSystemProxyOptIn(monkeypatch) -> None:
    client = _HttpClient()
    fakeCfg = SimpleNamespace(
        cloudBaseUrl=SimpleNamespace(value="http://cloud.test"),
        cloudUseSystemProxy=SimpleNamespace(value=True),
    )
    monkeypatch.setattr(cloudApiModule, "cfg", fakeCfg)
    monkeypatch.setattr(cloudApiModule.requests, "Session", lambda: client)

    assert cloudApiModule.CloudApi()._httpClient().trust_env is True


def testCloudApiMovesMainThreadRequestToWorker(qtbot, monkeypatch) -> None:
    mainThreadId = threading.get_ident()
    requestThreadIds = []
    requestTimeouts = []

    class _ThreadRecordingClient(_HttpClient):
        def request(self, **kwargs):
            requestThreadIds.append(threading.get_ident())
            requestTimeouts.append(kwargs["timeout"])
            return super().request(**kwargs)

    fakeCfg = SimpleNamespace(
        cloudBaseUrl=SimpleNamespace(value="http://cloud.test"),
        cloudUseSystemProxy=SimpleNamespace(value=False),
    )
    monkeypatch.setattr(cloudApiModule, "cfg", fakeCfg)
    monkeypatch.setattr(cloudApiModule.requests, "Session", _ThreadRecordingClient)
    api = cloudApiModule.CloudApi()
    monkeypatch.setattr(api, "_headers", lambda **_kwargs: {})

    assert api.get("/healthz", withAuth=False) == {"reachable": True}
    assert requestThreadIds and requestThreadIds[0] != mainThreadId
    assert requestTimeouts == [cloudApiModule.DEFAULT_REQUEST_TIMEOUT]


def testCloudApiDiscardsConnectionPoolAfterTimeout(qtbot, monkeypatch) -> None:
    clients = []

    class _TimeoutClient(_HttpClient):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        def request(self, **kwargs):
            raise cloudApiModule.Timeout("模拟 VPN 切换超时")

        def close(self) -> None:
            self.closed = True

    def createClient():
        client = _TimeoutClient()
        clients.append(client)
        return client

    fakeCfg = SimpleNamespace(
        cloudBaseUrl=SimpleNamespace(value="http://cloud.test"),
        cloudUseSystemProxy=SimpleNamespace(value=False),
    )
    monkeypatch.setattr(cloudApiModule, "cfg", fakeCfg)
    monkeypatch.setattr(cloudApiModule.requests, "Session", createClient)
    api = cloudApiModule.CloudApi()
    monkeypatch.setattr(api, "_headers", lambda **_kwargs: {})

    with pytest.raises(cloudApiModule.CloudApiError, match="NETWORK_ERROR"):
        api.get("/healthz", withAuth=False)
    assert clients and clients[0].closed is True


def testRefreshUsesSingleFlightForConcurrent401(monkeypatch) -> None:
    auth = CloudAuth()
    oldSession = cloudApiModule.CloudSession(
        accessToken="old-access",
        refreshToken="refresh-token",
        userId=1,
    )

    class _RefreshApi:
        def __init__(self) -> None:
            self.session = oldSession
            self.calls = 0

        def getSession(self):
            return self.session

        def post(self, *_args, **_kwargs):
            self.calls += 1
            time.sleep(0.05)
            return {"tokens": {}, "user": {}}

    api = _RefreshApi()
    auth._api = api

    def applyResponse(_data) -> None:
        api.session = cloudApiModule.CloudSession(
            accessToken="new-access",
            refreshToken="new-refresh",
            userId=1,
        )

    monkeypatch.setattr(auth, "_applyLoginResponse", applyResponse)
    results = []
    threads = [
        threading.Thread(
            target=lambda: results.append(auth._refreshAccessToken("old-access"))
        )
        for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [True, True, True]
    assert api.calls == 1


def testRefreshNetworkFailureUsesBackoff(monkeypatch) -> None:
    auth = CloudAuth()

    class _FailingApi:
        def __init__(self) -> None:
            self.calls = 0
            self.session = cloudApiModule.CloudSession(
                accessToken="access",
                refreshToken="refresh",
                userId=1,
            )

        def getSession(self):
            return self.session

        def post(self, *_args, **_kwargs):
            self.calls += 1
            raise cloudApiModule.CloudApiError("NETWORK_ERROR", "模拟超时")

    api = _FailingApi()
    auth._api = api

    assert auth._refreshAccessToken("access") is False
    assert auth._refreshAccessToken("access") is False
    assert api.calls == 1
    assert auth._refreshFailureCount == 1


def testBootstrapRefreshesExpiredSessionInBackground(monkeypatch) -> None:
    auth = CloudAuth()
    auth._api.setSession(
        cloudApiModule.CloudSession(
            accessToken="expired",
            refreshToken="refresh",
            userId=1,
            expiresAt=int(time.time()) - 1,
        )
    )
    started = threading.Event()
    release = threading.Event()

    monkeypatch.setattr(auth, "_loadSession", lambda: True)

    def refresh(_failedAccessToken: str = "") -> bool:
        started.set()
        release.wait(1.0)
        return True

    monkeypatch.setattr(auth, "_refreshAccessToken", refresh)

    assert auth.bootstrap() is True
    assert started.wait(0.5)
    assert auth._bootstrapRefreshThread is not None
    assert auth._bootstrapRefreshThread.is_alive()
    release.set()
    auth._bootstrapRefreshThread.join(1.0)


def testBootstrapKeepsStillValidAccessTokenWithoutNetwork(monkeypatch) -> None:
    auth = CloudAuth()
    auth._api.setSession(
        cloudApiModule.CloudSession(
            accessToken="valid",
            refreshToken="refresh",
            userId=1,
            expiresAt=int(time.time()) + 1800,
        )
    )
    monkeypatch.setattr(auth, "_loadSession", lambda: True)
    monkeypatch.setattr(
        auth,
        "_refreshAccessToken",
        lambda *_args: pytest.fail("有效 access token 不应在启动时刷新"),
    )

    assert auth.bootstrap() is True
    assert auth._bootstrapRefreshThread is None


def testAccountTaskDropsResultWhenShutdownBegins(monkeypatch) -> None:
    shuttingDown = False
    delivered = []

    monkeypatch.setattr(
        accountInterfaceModule,
        "isApplicationShuttingDown",
        lambda: shuttingDown,
    )

    def operation():
        nonlocal shuttingDown
        shuttingDown = True
        return {"done": True}

    task = accountInterfaceModule._AccountTask(operation)
    task.signals.succeeded.connect(delivered.append)
    task.run()

    assert delivered == []


def testCleanWorkerDoesNotStartDuringShutdown(monkeypatch) -> None:
    started = []
    monkeypatch.setattr(
        cleanCoordinatorModule,
        "isApplicationShuttingDown",
        lambda: True,
    )
    worker = cleanCoordinatorModule.CleanWorker(None, None, True, "rule-hash")
    worker.signals.started.connect(lambda: started.append(True))

    worker.run()

    assert started == []


def testPricingCatalogUsesBoundedExponentialBackoff(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        pricingCatalogModule.PricingCatalog,
        "refreshAsync",
        lambda _self: None,
    )
    catalog = pricingCatalogModule.PricingCatalog()

    try:
        intervals = []
        for _ in range(6):
            catalog._onFailed("模拟网络超时")
            intervals.append(catalog._timer.interval())

        assert intervals == [30_000, 60_000, 120_000, 240_000, 300_000, 300_000]
        catalog._applyCatalog({"version": "v1", "rules": []})
        assert catalog._consecutiveFailures == 0
        assert catalog._timer.interval() == 30_000
    finally:
        catalog.shutdown()


def testPricingCatalogKeepsSnapshotAndSignalsSameVersionRefresh(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        pricingCatalogModule.PricingCatalog,
        "refreshAsync",
        lambda _self: None,
    )
    catalog = pricingCatalogModule.PricingCatalog()
    updates = []
    catalog.catalogChanged.connect(updates.append)

    try:
        snapshot = {"version": "v1", "state": "active", "rules": []}
        catalog._applyCatalog(snapshot)
        catalog._applyCatalog(snapshot)
        catalog._applyCatalog({})

        assert len(updates) == 2
        assert catalog.snapshot()["version"] == "v1"
        assert catalog.lastSyncedAt is not None
        assert catalog._consecutiveFailures == 1
    finally:
        catalog.shutdown()


def testCloudLoginWorkerRunsServiceOffMainThread(qtbot) -> None:
    mainThreadId = threading.get_ident()

    class _AuthService:
        def __init__(self) -> None:
            self.workerThreadId = 0
            self.calls = []

        def login(self, email: str, password: str, rememberMe: bool = True):
            self.workerThreadId = threading.get_ident()
            self.calls.append((email, password, rememberMe))
            return {"user": {"userId": 1}}

    authService = _AuthService()
    worker = CloudLoginWorker(
        "user@example.com",
        "password-123",
        True,
        authService=authService,
    )

    with qtbot.waitSignal(worker.succeeded, timeout=2000) as blocker:
        worker.start()
    assert worker.wait(2000)
    assert blocker.args == [{"user": {"userId": 1}}]
    assert authService.workerThreadId != mainThreadId
    assert authService.calls == [("user@example.com", "password-123", True)]
    assert worker._password == ""


class _LoginWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()
    instances = []

    def __init__(self, email: str, password: str, rememberMe: bool, parent=None) -> None:
        super().__init__(parent)
        self.email = email
        self.password = password
        self.rememberMe = rememberMe
        self._running = False
        self.instances.append(self)

    def isRunning(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True

    def complete(self) -> None:
        self.succeeded.emit({"user": {"userId": 1}})
        self._running = False
        self.finished.emit()

    def fail(self, error: object) -> None:
        self.failed.emit(error)
        self._running = False
        self.finished.emit()


def testLoginInterfaceShowsProgressAndBlocksDuplicateSubmission(
    qtbot,
    monkeypatch,
) -> None:
    _LoginWorker.instances = []
    monkeypatch.setattr(loginDialogModule, "CloudLoginWorker", _LoginWorker)
    interface = loginDialogModule.LoginInterface()
    qtbot.addWidget(interface)
    interface._loginEmailEdit.setText("user@example.com")
    interface._loginPasswordEdit.setText("password-123")

    interface._onLogin()
    interface._onLogin()

    assert len(_LoginWorker.instances) == 1
    assert interface._loginBtn.loading is True
    assert interface._loginBtn.text() == "正在连接云端…"
    assert interface._loginBtn.isEnabled() is False
    assert interface._loginEmailEdit.isEnabled() is False

    with qtbot.waitSignal(interface.loginSucceeded, timeout=1000):
        _LoginWorker.instances[0].complete()
    assert interface._loginBtn.loading is False
    assert interface._loginBtn.text() == "登录"
    assert interface._loginBtn.isEnabled() is True
    assert interface._loginEmailEdit.isEnabled() is True

    interface._onLogin()
    assert len(_LoginWorker.instances) == 2
    _LoginWorker.instances[1].fail(
        cloudApiModule.CloudApiError("NETWORK_ERROR", "模拟网络异常")
    )
    assert interface._loginStatus.text() == "无法连接云端服务，请检查网络后重试"
    assert interface._loginBtn.loading is False
    assert interface._loginBtn.isEnabled() is True
