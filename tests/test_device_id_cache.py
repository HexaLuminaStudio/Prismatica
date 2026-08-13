# coding: utf-8
"""设备标识进程内缓存与并发加载回归测试。"""
from __future__ import annotations

import json
import threading
import time

import pytest

import app.core.utils.device_id as deviceIdModule
from app.core.utils.encryption import AESCipherGCM


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


def test_deviceIdIgnoresNetworkAndSystemVersionChanges(tmp_path, monkeypatch) -> None:
    storagePath = tmp_path / "device.bin"
    first = deviceIdModule.DeviceIdentifier(str(storagePath))
    first.deviceFeatures = {
        "machineId": "stable-machine-guid",
        "mac": "00:11:22:33:44:55",
        "hostname": "OLD-NAME",
        "platformVersion": "10.0.1",
    }

    firstDeviceId = first.generateDeviceId()
    assert first.save() is True

    second = deviceIdModule.DeviceIdentifier(str(storagePath))
    changedFeatures = {
        "machineId": "stable-machine-guid",
        "mac": "AA:BB:CC:DD:EE:FF",
        "hostname": "NEW-NAME",
        "platformVersion": "10.0.2",
    }

    def collectChangedFeatures():
        second.deviceFeatures = changedFeatures
        return changedFeatures

    monkeypatch.setattr(second, "collectDeviceFeatures", collectChangedFeatures)

    assert second.load() is True
    assert second.deviceId == firstDeviceId


def test_legacyDeviceFileMigratesWithoutChangingKnownDeviceId(
    tmp_path,
    monkeypatch,
) -> None:
    storagePath = tmp_path / "device.bin"
    legacyFeatures = {
        "mac": "00:11:22:33:44:55",
        "motherboard": "board-1",
        "disk": "disk-1",
        "hostname": "DESKTOP",
        "platform": "Windows",
        "platformVersion": "10.0.1",
        "processor": "x64",
    }
    legacy = deviceIdModule.DeviceIdentifier(str(storagePath))
    legacy.deviceFeatures = legacyFeatures
    legacyDeviceId = legacy._hashFeatureItems(legacy._legacyFeatureItems())
    legacyPayload = json.dumps(
        {"deviceId": legacyDeviceId, "features": legacyFeatures},
        ensure_ascii=False,
    )
    storagePath.write_text(
        AESCipherGCM(legacy.deriveLegacyEncryptionKey()).encrypt(legacyPayload),
        encoding="utf-8",
    )

    upgraded = deviceIdModule.DeviceIdentifier(str(storagePath))
    currentFeatures = {"machineId": "stable-machine-guid", **legacyFeatures}

    def collectCurrentFeatures():
        upgraded.deviceFeatures = currentFeatures
        return currentFeatures

    monkeypatch.setattr(upgraded, "collectDeviceFeatures", collectCurrentFeatures)

    assert upgraded.load() is True
    assert upgraded.deviceId == legacyDeviceId

    afterNetworkChange = deviceIdModule.DeviceIdentifier(str(storagePath))
    changedFeatures = {**currentFeatures, "mac": "AA:BB:CC:DD:EE:FF"}

    def collectChangedFeatures():
        afterNetworkChange.deviceFeatures = changedFeatures
        return changedFeatures

    monkeypatch.setattr(afterNetworkChange, "collectDeviceFeatures", collectChangedFeatures)
    assert afterNetworkChange.load() is True
    assert afterNetworkChange.deviceId == legacyDeviceId


def test_generateOrLoadDeviceIdRejectsUnpersistedIdentity(tmp_path, monkeypatch) -> None:
    device = deviceIdModule.DeviceIdentifier(str(tmp_path / "device.bin"))
    device.deviceFeatures = {"machineId": "stable-machine-guid"}
    monkeypatch.setattr(device, "save", lambda: False)
    monkeypatch.setattr(deviceIdModule, "_deviceIdentifier", device)

    with pytest.raises(RuntimeError, match="持久化"):
        deviceIdModule.generateOrLoadDeviceId()
