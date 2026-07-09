# coding: utf-8
"""
信号总线
用于组件间通信的全局信号
"""

from PySide6.QtCore import QObject, Signal


class SignalBus(QObject):
    """Signal bus for component communication"""

    # 激活状态变更信号
    activationStatusChanged = Signal(bool)

    # HSK Token刷新信号
    hskTokenRefreshSignal = Signal(str)
    # 参数: token (str) - 新的Token值

    # Global Token刷新信号
    globalTokenRefreshSignal = Signal(str)
    # 参数: token (str) - 新的Token值


signalBus = SignalBus()
