"""P0-A signalBus 多设备上限 / 会话变化 / 余额变化 测试。

覆盖:
    - sessionChanged / balanceChanged / devicesChanged / maxDevicesReached
    - CloudApi 收到 MAX_DEVICES_REACHED envelope 时触发 _onMaxDevicesReached
"""
from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)


def test_signal_bus_exposes_p0a_signals() -> None:
    from app.core.utils import signalBus

    assert hasattr(signalBus, "sessionChanged")
    assert hasattr(signalBus, "balanceChanged")
    assert hasattr(signalBus, "devicesChanged")
    assert hasattr(signalBus, "maxDevicesReached")
    assert hasattr(signalBus, "featureBlocked")


def test_session_changed_emits_bool() -> None:
    from app.core.utils import signalBus

    captured: list[bool] = []
    signalBus.sessionChanged.connect(lambda v: captured.append(bool(v)))
    try:
        signalBus.sessionChanged.emit(True)
        signalBus.sessionChanged.emit(False)
    finally:
        signalBus.sessionChanged.disconnect()
    assert captured == [True, False]


def test_balance_changed_emits_int() -> None:
    from app.core.utils import signalBus

    captured: list[int] = []
    signalBus.balanceChanged.connect(lambda v: captured.append(int(v)))
    try:
        signalBus.balanceChanged.emit(0)
        signalBus.balanceChanged.emit(150)
        signalBus.balanceChanged.emit(15)
    finally:
        signalBus.balanceChanged.disconnect()
    assert captured == [0, 150, 15]


def test_max_devices_reached_emits_int() -> None:
    from app.core.utils import signalBus

    captured: list[int] = []
    signalBus.maxDevicesReached.connect(lambda v: captured.append(int(v)))
    try:
        signalBus.maxDevicesReached.emit(3)
    finally:
        signalBus.maxDevicesReached.disconnect()
    assert captured == [3]


def test_cloud_api_unwrap_max_devices_calls_callback() -> None:
    """CloudApi._unwrapEnvelope 收到 MAX_DEVICES_REACHED 应触发 _onMaxDevicesReached。"""
    from app.core.services import getCloudApi
    from app.core.services.cloud_api import CloudApiError

    api = getCloudApi()
    captured: list[dict] = []

    def cb(details: dict) -> None:
        captured.append(details)

    api.setOnMaxDevicesReached(cb)
    payload = {
        "code": "MAX_DEVICES_REACHED",
        "message": "已达到上限",
        "details": {"limit": 3},
    }
    with pytest.raises(CloudApiError) as exc:
        api._unwrapEnvelope(payload)
    assert exc.value.code == "MAX_DEVICES_REACHED"
    assert captured == [{"limit": 3}]


def test_ensure_below_max_devices_triggers_signal_on_limit(monkeypatch) -> None:
    """cloud_user.ensureBelowMaxDevices 在 activeCount >= maxActive 时发信号。"""
    from app.core.services import cloud_user
    from app.core.utils import signalBus

    captured: list[int] = []
    signalBus.maxDevicesReached.connect(lambda v: captured.append(int(v)))

    # monkeypatch CloudAccount.listDevices
    monkeypatch.setattr(
        cloud_user,
        "getCloudAccount",
        lambda: _FakeAccount({"items": [], "maxActive": 3, "activeCount": 3}),
    )

    try:
        ok = cloud_user.ensureBelowMaxDevices()
    finally:
        signalBus.maxDevicesReached.disconnect()
    assert ok is False
    assert captured == [3]


def test_ensure_below_max_devices_returns_true_when_under_limit(monkeypatch) -> None:
    from app.core.services import cloud_user
    from app.core.utils import signalBus

    captured: list[int] = []
    signalBus.maxDevicesReached.connect(lambda v: captured.append(int(v)))

    monkeypatch.setattr(
        cloud_user,
        "getCloudAccount",
        lambda: _FakeAccount({"items": [], "maxActive": 3, "activeCount": 1}),
    )
    try:
        ok = cloud_user.ensureBelowMaxDevices()
    finally:
        signalBus.maxDevicesReached.disconnect()
    assert ok is True
    assert captured == []


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


class _FakeAccount:
    def __init__(self, data: dict) -> None:
        self._data = data

    def listDevices(self) -> dict:
        return self._data
