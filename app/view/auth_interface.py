# coding: utf-8
"""AuthGate - 启动门(模态对话框)

main.py 在 SplashWindow 之后调用:
    if not authService.isAuthenticated():
        gate = AuthGate(parent=None)
        gate.exec()
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication
from qfluentwidgets import (
    FluentWindow,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
)

from app.core.services.auth_service import getAuthService
from app.view.widgets.auth.login_dialog import LoginDialog


class AuthGate:
    """启动门包装器(非 QWidget,持有 LoginDialog 的引用以防 GC)"""

    # 防御性超时(2026-08-05):防止 LoginDialog 因平台/主题 bug 不可见
    # 时 exec() 永久阻塞,30s 后强制 reject 结束阻塞。
    TIMEOUT_MS = 30_000

    def __init__(self, parent=None):
        self._auth = getAuthService()
        # 修复(2026-08-05):LoginDialog 现已重构为独立 QWidget,
        # 不再依赖 parent 几何;parent 仅用于顶级 owner 关系,可保持 None。
        self._parent = parent
        self._dialog: Optional[LoginDialog] = None
        self._success = False
        self._timeoutTimer: Optional[QTimer] = None

    def exec(self) -> bool:
        """阻塞弹出,直到激活成功 / 用户退出。"""
        if self._auth.isAuthenticated():
            return True
        # 不传 parent —— 新版 LoginDialog 是独立 QWidget,parent 不影响几何
        self._dialog = LoginDialog(parent=self._parent)
        # 启动超时守卫(防止 dialog 不可见导致永久卡死)
        self._timeoutTimer = QTimer()
        self._timeoutTimer.setSingleShot(True)
        self._timeoutTimer.timeout.connect(self._onTimeout)
        self._timeoutTimer.start(self.TIMEOUT_MS)
        try:
            self._dialog.exec()
        finally:
            try:
                if self._timeoutTimer is not None:
                    self._timeoutTimer.stop()
            except Exception:
                pass
        self._success = self._dialog.isSuccess()
        return self._success

    def _onTimeout(self) -> None:
        """启动门响应超时:强制 reject 退出。"""
        from loguru import logger

        logger.warning("[AuthGate] 启动门响应超时(30s),强制结束")
        try:
            if self._dialog is not None:
                self._dialog.reject()
        except Exception:
            pass
        self._success = False

    def isSuccess(self) -> bool:
        return self._success


def showAuthGate(parent=None) -> bool:
    """便捷函数:启动门。

    修复(2026-08-05):新 LoginDialog 不再需要 parent 几何,
    旧 fallback 到 activeWindow() 的逻辑可移除。
    """
    return AuthGate(parent).exec()
