# coding: utf-8
"""跨线程共享的应用退出状态。"""
from __future__ import annotations

import threading

_shutdownEvent = threading.Event()


def beginApplicationShutdown() -> None:
    """标记应用进入退出阶段，后台任务应停止投递 Qt 回调。"""
    _shutdownEvent.set()


def isApplicationShuttingDown() -> bool:
    """返回应用是否已经进入退出阶段。"""
    return _shutdownEvent.is_set()


__all__ = ["beginApplicationShutdown", "isApplicationShuttingDown"]
