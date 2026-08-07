# coding: utf-8
"""
信号总线
用于组件间通信的全局信号
"""

from PySide6.QtCore import QObject, Signal


class SignalBus(QObject):
    """Signal bus for component communication"""

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

    # 2026-08-07 P0-A(M13):
    #   会话状态变化(bool 是否登录),头像 / 抽屉 / 登录窗 监听
    sessionChanged = Signal(bool)
    # 余额变化(int 可用余额),头像红点 / 抽屉数字
    balanceChanged = Signal(int)
    # 设备列表变化(无参),「我的账户 → 设备」子页签
    devicesChanged = Signal()
    # 触发多设备上限弹窗(int 上限值),登录 / 抽屉订阅
    maxDevicesReached = Signal(int)
    # AI 洞察等高级功能被阻断(reason, message),UI 弹通用对话框
    featureBlocked = Signal(str, str)


signalBus = SignalBus()