# coding: utf-8
"""
内测过期对话框

启动时若 ensureBetaTimelock() 返回 expired_* 状态,
则弹出此对话框阻断使用,并提供激活码入口。

设计目标:
    - 全屏遮罩,用户无法绕开
    - 显示过期原因 + 截止日期 + 剩余 0 天的视觉提醒
    - 提供「输入激活码」按钮
    - 提供「退出」按钮强制退出
"""

import sys
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout

from qfluentwidgets import (
    MessageBoxBase,
    SubtitleLabel,
    StrongBodyLabel,
    BodyLabel,
    CaptionLabel,
    PushButton,
    PrimaryPushButton,
    LineEdit,
    FluentIcon as FIF,
)


class BetaExpiredDialog(MessageBoxBase):
    """内测期结束对话框(全屏遮罩,无法绕过)"""

    def __init__(self, status: dict, parent=None):
        super().__init__(parent=parent)
        self._status = status
        self._buildUi()
        self._wireEvents()

    def _buildUi(self):
        # ---- 标题(图标 + 文字) ----
        titleLayout = QHBoxLayout()
        titleLayout.setContentsMargins(0, 0, 0, 0)
        titleLayout.setSpacing(8)
        icon = FIF.CANCEL_MEDIUM.icon()
        # 用 CaptionLabel 替代标题(因 MessageBoxBase 自带 titleBar 较窄)
        self._titleLabel = SubtitleLabel("内测期已结束", self)
        titleLayout.addWidget(self._titleLabel)
        titleLayout.addStretch(1)
        self.viewLayout.addLayout(titleLayout)

        # ---- 主体说明 ----
        reason = self._status.get("reason") or "内测已结束"
        deadline = self._status.get("deadline") or ""
        startDate = self._status.get("startDate") or "(未记录)"

        self._reasonLabel = BodyLabel(reason, self)
        self._reasonLabel.setStyleSheet(
            "color: #c97a00; font-size: 14px; padding: 4px 0;"
        )
        self._reasonLabel.setWordWrap(True)
        self.viewLayout.addWidget(self._reasonLabel)

        # ---- 详细信息块 ----
        infoText = (
            f"• 首次启动日期:{startDate}\n"
            f"• 内测截止日期:{deadline}\n"
            f"• 当前状态:已过期(无法使用)"
        )
        self._infoLabel = BodyLabel(infoText, self)
        self._infoLabel.setStyleSheet(
            "color: #444; font-size: 13px; padding: 8px 12px; "
            "background: #fafafa; border-radius: 4px;"
        )
        self.viewLayout.addWidget(self._infoLabel)

        # ---- 解释为什么改时间没用 ----
        securityNote = CaptionLabel(
            "提示:本授权使用 HMAC 签名 + 设备指纹加密存储,"
            "修改系统时间或复制激活文件均无法绕过校验。",
            self,
        )
        securityNote.setStyleSheet(
            "color: #888; font-size: 11px; padding: 4px 0;"
        )
        securityNote.setWordWrap(True)
        self.viewLayout.addWidget(securityNote)

        # ---- 激活码输入区 ----
        self._codeEdit = LineEdit(self)
        self._codeEdit.setPlaceholderText("请输入激活码(如有)")
        self.viewLayout.addWidget(self._codeEdit)

    def _wireEvents(self):
        """绑定按钮事件"""
        # MessageBoxBase 自带 yes/no 按钮,这里重新设置文案
        self.yesButton.setText("使用激活码解锁")
        self.yesButton.setIcon(FIF.ACCEPT.icon())
        self.cancelButton.setText("退出")
        self.cancelButton.setIcon(FIF.CLOSE.icon())

        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._onActivate)
        self.cancelButton.clicked.disconnect()
        self.cancelButton.clicked.connect(self._onExit)

    def _onActivate(self):
        """用户点击「使用激活码解锁」"""
        from app.core.utils.license import getLicenseManager
        from app.core.utils.device_id import generateOrLoadDeviceId

        code = self._codeEdit.text().strip()
        if not code:
            self._reasonLabel.setText("请输入有效的激活码")
            self._reasonLabel.setStyleSheet(
                "color: #b00; font-size: 14px; padding: 4px 0;"
            )
            return

        deviceCode = generateOrLoadDeviceId()
        mgr = getLicenseManager()
        result = mgr.verifyActivationCode(code, deviceCode)

        if not result.get("success"):
            self._reasonLabel.setText(
                f"激活失败:{result.get('message', '未知错误')}"
            )
            self._reasonLabel.setStyleSheet(
                "color: #b00; font-size: 14px; padding: 4px 0;"
            )
            return

        # 激活成功:保存并重启
        mgr.activate(code, deviceCode)
        self._reasonLabel.setText("激活成功!请重新启动程序。")
        self._reasonLabel.setStyleSheet(
            "color: #2c8a4a; font-size: 14px; padding: 4px 0;"
        )
        self.yesButton.setEnabled(False)
        self.cancelButton.setText("退出")

    def _onExit(self):
        """用户点击「退出」"""
        self.accept()
        # P0-fix:不要直接 sys.exit(0),会绕过 Qt 的析构流程,导致 worker
        # 线程、SQLite 连接、网络会话未关闭就硬退出。
        # 改用 QApplication.quit() 走正常 Qt 退出流程,事件循环收到 quit
        # 信号后会自动触发各对象的析构与 atexit 钩子。
        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:
            sys.exit(0)


def showBetaExpiredDialog(status: dict) -> None:
    """显示内测过期对话框(主程序在过期时调用)

    Args:
        status: ensureBetaTimelock() 返回的状态字典

    Notes:
        该函数会进入 Qt 事件循环,直到用户关闭对话框后才会 return。
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    dialog = BetaExpiredDialog(status, parent=None)
    dialog.setWindowTitle("Prismatica - 内测期结束")
    dialog.setWindowFlags(
        Qt.WindowType.Dialog
        | Qt.WindowType.WindowStaysOnTopHint
    )

    # 使用模态 + exec() 阻塞
    dialog.exec()