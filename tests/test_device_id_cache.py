# coding: utf-8
"""设备标识进程内缓存与并发加载回归测试。"""
from __future__ import annotations

import threading
import time

import app.core.utils.device_id as deviceIdModule


def test_generateOrLoadDeviceIdUsesProcessCache(tmp_path, monkeypatch) -> None:
    device = deviceIdModule.DeviceIdentifier(str(tmp_path / "device.bin"))
    loadCount = 0

    def load() -> bool:
        nonlocal loadCount
        loadCount += 1
        device.deviceId = "cached-device-id"
        device.deviceFeatures = {"mac": "a", "disk": "b", "hostname": "c"}
        return True

    monkeypatch.setattr(device, "load", load)
    monkeypatch.setattr(deviceIdModule, "_deviceIdentifier", device)

    results = [deviceIdModule.generateOrLoadDeviceId() for _ in range(10)]

    assert results == ["cached-device-id"] * 10
    assert loadCount == 1


def test_generateOrLoadDeviceIdLoadsOnceAcrossThreads(tmp_path, monkeypatch) -> None:
    device = deviceIdModule.DeviceIdentifier(str(tmp_path / "device.bin"))
    loadCount = 0

    def load() -> bool:
        nonlocal loadCount
        loadCount += 1
        time.sleep(0.03)
        device.deviceId = "thread-safe-device-id"
        device.deviceFeatures = {"mac": "a", "disk": "b", "hostname": "c"}
        return True

    monkeypatch.setattr(device, "load", load)
    monkeypatch.setattr(deviceIdModule, "_deviceIdentifier", device)
    results = []
    threads = [
        threading.Thread(
            target=lambda: results.append(deviceIdModule.generateOrLoadDeviceId())
        )
        for _ in range(8)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == ["thread-safe-device-id"] * 8
    assert loadCount == 1


def test_resetInvalidatesProcessCache(tmp_path, monkeypatch) -> None:
    storagePath = tmp_path / "device.bin"
    storagePath.write_bytes(b"placeholder")
    device = deviceIdModule.DeviceIdentifier(str(storagePath))
    device.deviceId = "old-device-id"
    device.deviceFeatures = {"mac": "a", "disk": "b", "hostname": "c"}
    monkeypatch.setattr(deviceIdModule, "_deviceIdentifier", device)

    assert device.reset() is True
    assert device.deviceId is None
    assert device.deviceFeatures == {}
    assert storagePath.exists() is False
