# coding: utf-8
"""P0-A 桌面端 CloudUser:多设备上限 / 跨设备恢复(整合在 cloud_auth)."""
from __future__ import annotations

from typing import Any, Dict

from app.core.utils import logger, signalBus

from .cloud_account import getCloudAccount
from .cloud_api import getCloudApi


def ensureBelowMaxDevices(*, maxActive: int = 3) -> bool:
    """检查当前激活设备数,接近上限时发信号让 UI 提示。"""
    try:
        info = getCloudAccount().listDevices()
    except Exception as exc:
        logger.debug(f"[CloudUser] listDevices 失败: {exc}")
        return True  # 网络异常时不阻塞

    active = int(info.get("activeCount", 0) or 0)
    if active >= maxActive:
        try:
            signalBus.maxDevicesReached.emit(int(info.get("maxActive", maxActive)))
        except Exception:
            pass
        return False
    return True


__all__ = ["ensureBelowMaxDevices"]
