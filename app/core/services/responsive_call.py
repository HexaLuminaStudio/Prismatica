# coding: utf-8
"""让同步服务调用在后台执行，同时保持 Qt 主线程持续处理界面事件。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QThread, Signal

from app.core.utils.application_lifecycle import isApplicationShuttingDown

T = TypeVar("T")
_RESPONSIVE_EXECUTOR = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="responsive-call",
)


class _FutureNotifier(QObject):
    """把 concurrent future 的完成事件安全转发到 Qt 主线程。"""

    completed = Signal()


def runResponsiveCall(operation: Callable[[], T]) -> T:
    """在 Qt 主线程调用时把阻塞工作移到后台，并同步返回其结果。

    调用方仍可沿用原有顺序式事务代码；等待期间 Qt 会继续处理绘制、
    鼠标、键盘、信号和定时器事件，也不会强制显示系统忙碌光标。
    非 Qt 环境或已经位于工作线程时直接执行，避免嵌套线程。
    """
    application = QCoreApplication.instance()
    if application is None or QThread.currentThread() is not application.thread():
        return operation()

    future = _RESPONSIVE_EXECUTOR.submit(operation)
    notifier = _FutureNotifier()
    eventLoop = QEventLoop()
    notifier.completed.connect(eventLoop.quit)

    def _notifyCompleted(_future) -> None:
        if isApplicationShuttingDown():
            return
        try:
            notifier.completed.emit()
        except RuntimeError:
            pass

    future.add_done_callback(_notifyCompleted)

    if not future.done():
        eventLoop.exec(QEventLoop.ProcessEventsFlag.AllEvents)

    return future.result()


__all__ = ["runResponsiveCall"]
