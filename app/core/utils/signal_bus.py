# coding: utf-8
from PySide6.QtCore import QObject, Signal


class SignalBus(QObject):
    """Signal bus for component communication"""

    # 激活状态变更信号
    activationStatusChanged = Signal(bool)
    # 参数: isActivated (bool) - 是否已激活


signalBus = SignalBus()
