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

    # 余额变更信号:参数为 (userId: str, balance: int)
    balanceChanged = Signal(str, int)

    # 账单新增信号:参数为 userId
    billCreated = Signal(str)

    # 凭证损坏信号:参数为损坏原因字符串(str)
    licenseCorrupted = Signal(str)

    # 云端鉴权失效信号(2026-08-05 F5):refresh token 失效,
    # 调用方应引导用户重新激活(redeemCode)。参数为失败原因(str)。
    sessionExpired = Signal(str)

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

    # PRD-003 HSK 批量下载
    # 跳转请求信号:参数为子界面 objectName (str),由 main_window 订阅后 switchTo
    navigateToSubInterface = Signal(str)


signalBus = SignalBus()