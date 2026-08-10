# coding: utf-8
"""让同步服务调用在后台执行，同时保持 Qt 主线程持续处理界面事件。"""
from __future__ import annotations

from typing import Callable, Generic, TypeVar, cast

from PySide6.QtCore import QCoreApplication, QEventLoop, QThread

T = TypeVar("T")


class _ResponsiveCallThread(QThread, Generic[T]):
    """执行单个同步调用，并把返回值或异常交还给调用线程。"""

    def __init__(self, operation: Callable[[], T]) -> None:
        super().__init__()
        self._operation = operation
        self.result: T | None = None
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            self.result = self._operation()
        except Exception as error:  # noqa: BLE001 - 必须原样传回服务异常
            self.error = error


def runResponsiveCall(operation: Callable[[], T]) -> T:
    """在 Qt 主线程调用时把阻塞工作移到后台，并同步返回其结果。

    调用方仍可沿用原有顺序式事务代码；等待期间 Qt 会继续处理绘制、
    鼠标、键盘、信号和定时器事件，也不会强制显示系统忙碌光标。
    非 Qt 环境或已经位于工作线程时直接执行，避免嵌套线程。
    """
    application = QCoreApplication.instance()
    if application is None or QThread.currentThread() is not application.thread():
        return operation()

    worker = _ResponsiveCallThread(operation)
    eventLoop = QEventLoop()
    worker.finished.connect(eventLoop.quit)

    worker.start()
    eventLoop.exec(QEventLoop.ProcessEventsFlag.AllEvents)
    worker.wait()

    if worker.error is not None:
        raise worker.error
    return cast(T, worker.result)


__all__ = ["runResponsiveCall"]
