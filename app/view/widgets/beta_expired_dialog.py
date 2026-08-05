# coding: utf-8
"""
内测过期提示对话框

启动时若 ensureBetaTimelock() 返回 expired_* 状态,
则弹出此对话框**提示用户**(不再强行阻止启动),并提供激活码入口。

设计变更(P0-fix 2026-07-18):
    - 由「全屏遮罩 + sys.exit 强行退出」改为「非模态弹窗 + 提示」
    - 用户可选择:
        1. 输入有效激活码 → 解锁正式版(若 HMAC 校验通过)
        2. 继续使用 → 仅关闭弹窗,主程序正常运行(带过期提示)
        3. 退出程序 → 通过 QApplication.quit() 走正常 Qt 退出流程
    - 默认行为:弹窗出现一次;关闭后下次启动仍会出现(提醒持久化)
    - 不再阻断主窗口的初始化与显示
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
    """内测期结束提示对话框(非模态,可关闭继续使用)

    行为:
        - 默认非模态,关闭不影响主程序运行
        - 提供三种选择:激活 / 继续使用 / 退出
    """

    def __init__(self, status: dict, parent=None, modal: bool = False):
        """构造内测过期提示对话框

        Args:
            status: ensureBetaTimelock() 返回的状态字典
            parent: 父窗口。可为 None,内部回退到 activeWindow()。
            modal:  是否为模态阻塞模式。
                    True  = 仅有「使用激活码解锁 / 退出程序」两个按钮,
                            通过 dialog.exec() 阻塞主循环,
                            适合「不显示主窗口,只显示弹窗」的场景。
                    False = 额外提供「继续使用」按钮,非模态,
                            适合「主窗口已显示,弹窗作为提醒」的场景。
        """
        # P0-fix 2026-07-18:MessageBoxBase.__init__ 会访问 parent.width/height
        # 用于居中布局,parent=None 会抛 AttributeError: 'NoneType' object
        # has no attribute 'width'。调用方传 None 时,我们回退到当前活动窗口。
        if parent is None:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                parent = app.activeWindow()
            if parent is None:
                # 极端情况:活动窗口也未创建,创建一个临时顶层 widget。
                from PySide6.QtWidgets import QWidget

                parent = QWidget()
                parent.resize(800, 600)
                # 把这个临时 widget 存到模块级全局,避免 GC。
                # 注意:它本身就是 parent,只要 dialog 活着,parent 就活着,
                # 这里只是为了双重保险。
                import sys as _sys

                _sys.modules.setdefault("__beta_expired_fallback_parent__", parent)
        super().__init__(parent=parent)
        self._status = status
        self._modal = modal
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
            f"• 当前状态:内测期已结束(可继续使用,但部分功能可能受限)"
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
            "修改系统时间或复制激活文件均无法绕过校验。"
            "您可继续使用本程序,也可输入激活码解锁正式版。",
            self,
        )
        securityNote.setStyleSheet("color: #888; font-size: 11px; padding: 4px 0;")
        securityNote.setWordWrap(True)
        self.viewLayout.addWidget(securityNote)

        # ---- 激活码输入区 ----
        self._codeEdit = LineEdit(self)
        self._codeEdit.setPlaceholderText("请输入激活码(如有)")
        self.viewLayout.addWidget(self._codeEdit)

    def _wireEvents(self):
        """绑定按钮事件

        P0-fix:新增「继续使用」按钮,允许用户在不过期后仍能使用程序。
        取消按钮改为「退出程序」,yes 按钮改为「使用激活码解锁」。

        模态模式(self._modal=True)下不显示「继续使用」按钮,
        因为主窗口不显示,「继续」没有意义 — 只有「激活」或「退出」两条路。
        """
        # MessageBoxBase 自带 yes/no 按钮,这里重新设置文案
        self.yesButton.setText("使用激活码解锁")
        self.yesButton.setIcon(FIF.ACCEPT.icon())
        self.cancelButton.setText("退出程序")
        self.cancelButton.setIcon(FIF.CLOSE.icon())

        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._onActivate)
        self.cancelButton.clicked.disconnect()
        self.cancelButton.clicked.connect(self._onExit)

        # 「继续使用」按钮:仅在非模态模式下显示
        # (模态模式下没有主窗口可继续使用,按钮无意义)
        if not self._modal:
            self._continueButton = PushButton("继续使用", self)
            self._continueButton.setIcon(FIF.PLAY.icon())
            self._continueButton.clicked.connect(self._onContinue)

            buttonLayout = self.buttonGroup.layout()
            if buttonLayout is not None:
                # 插入到 yes 按钮之后、cancel 按钮之前
                buttonLayout.insertWidget(1, self._continueButton)

    def _onActivate(self):
        """用户点击「使用激活码解锁」(2026-08-05 改走云端兑换)"""
        from app.core.services.auth_service import getAuthService

        code = self._codeEdit.text().strip()
        if not code:
            self._reasonLabel.setText("请输入有效的激活码")
            self._reasonLabel.setStyleSheet(
                "color: #b00; font-size: 14px; padding: 4px 0;"
            )
            return

        result = getAuthService().redeemCode(code)
        if not result.success:
            self._reasonLabel.setText(f"激活失败:{result.message}")
            self._reasonLabel.setStyleSheet(
                "color: #b00; font-size: 14px; padding: 4px 0;"
            )
            return

        self._reasonLabel.setText("激活成功!下次启动将自动识别正式版。")
        self._reasonLabel.setStyleSheet(
            "color: #2c8a4a; font-size: 14px; padding: 4px 0;"
        )
        self.yesButton.setEnabled(False)
        self.cancelButton.setText("关闭")

        # 模态模式:把 cancelButton 从「退出程序」重新接到 _onContinue
        # (只关闭弹窗),这样点击「关闭」不会触发 QApplication.quit()。
        # 非模态模式:保持原 handler 不变,行为一致。
        if self._modal:
            self.cancelButton.clicked.disconnect()
            self.cancelButton.clicked.connect(self._onContinue)
        # 3 秒后自动关闭(无论模态与否)
        from PySide6.QtCore import QTimer

        QTimer.singleShot(3000, self._onContinue)

    def _onContinue(self):
        """用户点击「继续使用」:仅关闭弹窗,主程序正常运行

        P0-fix 2026-07-18:旧版本这里会 sys.exit(0) 强行退出,新版本
        改为仅关闭弹窗,主窗口可正常显示并使用。
        """
        self.accept()
        # 注意:不调用 QApplication.quit(),主程序继续运行

    def _onExit(self):
        """用户点击「退出程序」"""
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


def showBetaExpiredWarning(
    status: dict, parent=None, modal: bool = False
) -> "BetaExpiredDialog":
    """显示内测过期提示对话框(主程序在过期时调用)

    Args:
        status: ensureBetaTimelock() 返回的状态字典
        parent: 父窗口(通常是 MainWindow)。可为 None,
                此时回退到 QApplication.activeWindow()(BetaExpiredDialog
                内部已处理 None parent)。
        modal:  True = 模态阻塞:不显示「继续使用」按钮,用 dialog.exec()
                  阻塞主循环,适合「不显示主窗口,只显示弹窗」的场景。
                False = 非模态:用 dialog.show(),主程序可继续运行,
                  适合「主窗口已显示,弹窗作为提醒」的场景。

    Returns:
        已显示/执行的 BetaExpiredDialog 实例,调用方可保留引用
        防止被 Python 引用计数回收导致弹窗被销毁。
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    dialog = BetaExpiredDialog(status, parent=parent, modal=modal)
    dialog.setWindowTitle("Prismatica - 内测期结束")
    dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)

    if modal:
        # 模态阻塞模式:用 exec() 进入局部事件循环。
        # 用户必须做出选择(激活成功 / 退出程序)才会返回。
        dialog.exec()
    else:
        # 非模态提醒模式:仅显示弹窗,不阻塞主程序。
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
    return dialog


def showBetaExpiredDialog(status: dict) -> None:
    """兼容旧 API 的别名(已废弃)。

    旧实现使用 dialog.exec() 阻塞 + 退出程序,已替换为非模态弹窗。
    新代码应直接调用 showBetaExpiredWarning()。
    此函数保留仅为不破坏既有 import,内部调用新版本。
    """
    import warnings

    warnings.warn(
        "showBetaExpiredDialog 已废弃,请改用 showBetaExpiredWarning",
        DeprecationWarning,
        stacklevel=2,
    )
    showBetaExpiredWarning(status)
