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

    # PRD-002 研究项目(REQ-PROJ-001)
    # 激活项目变更信号:参数为项目 id (str),空串表示无激活项目
    activeProjectChanged = Signal(str)
    # 项目列表变更信号(新建/删除/重命名) — 无参数
    projectListChanged = Signal()


signalBus = SignalBus()
