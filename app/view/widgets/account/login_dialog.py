# coding: utf-8
"""P0-A 桌面端 登录 / 注册对话框。

- Tab 切换「登录」「注册」,共享同一份样式
- 邮箱 + 密码输入框 + 错误提示(INVALID_CREDENTIALS / ACCOUNT_LOCKED / NETWORK_ERROR)
- 「忘记密码」入口 → 弹 reset 子对话框
- 离线时:登录按钮置灰 + tooltip
"""
from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    LargeTitleLabel,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    TitleLabel,
)

from app.core.services import CloudApiError, getCloudAuth
from app.core.utils import logger, signalBus


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 10


def _validateEmail(text: str) -> bool:
    return bool(text) and bool(EMAIL_RE.match(text.strip()))


def _validatePassword(text: str) -> bool:
    return bool(text) and len(text) >= MIN_PASSWORD_LEN and any(c.isalpha() for c in text) and any(c.isdigit() for c in text)


class _PasswordResetDialog(QDialog):
    """找回密码:输入邮箱 + 输入 token + 设置新密码 三步合并为单对话框。"""

    passwordReset = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("找回密码")
        self.setMinimumWidth(420)
        self._buildUi()

    def _buildUi(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        layout.addWidget(TitleLabel("通过邮箱验证码重置密码"))

        self._emailEdit = QLineEdit()
        self._emailEdit.setPlaceholderText("注册时的邮箱")
        layout.addWidget(self._emailEdit)

        self._sendCodeBtn = PrimaryPushButton("发送重置链接")
        self._sendCodeBtn.clicked.connect(self._onSendCode)
        layout.addWidget(self._sendCodeBtn)

        # 分割
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

        self._tokenEdit = QLineEdit()
        self._tokenEdit.setPlaceholderText("邮件中的 token")
        layout.addWidget(self._tokenEdit)

        self._newPasswordEdit = PasswordLineEdit()
        self._newPasswordEdit.setPlaceholderText("新密码(至少 10 位,含字母和数字)")
        layout.addWidget(self._newPasswordEdit)

        self._confirmEdit = PasswordLineEdit()
        self._confirmEdit.setPlaceholderText("再输入一次")
        layout.addWidget(self._confirmEdit)

        self._statusLabel = CaptionLabel("")
        self._statusLabel.setStyleSheet("color: #c42b1c;")
        layout.addWidget(self._statusLabel)

        btnRow = QHBoxLayout()
        btnRow.addStretch(1)
        cancelBtn = PushButton("取消")
        cancelBtn.clicked.connect(self.reject)
        btnRow.addWidget(cancelBtn)
        self._submitBtn = PrimaryPushButton("提交重置")
        self._submitBtn.clicked.connect(self._onSubmit)
        btnRow.addWidget(self._submitBtn)
        layout.addLayout(btnRow)

    def _onSendCode(self) -> None:
        email = self._emailEdit.text().strip()
        if not _validateEmail(email):
            self._statusLabel.setText("邮箱格式不正确")
            return
        try:
            getCloudAuth().requestPasswordReset(email)
        except CloudApiError as exc:
            self._statusLabel.setText(f"发送失败:{exc.message}")
            return
        # 后端不论邮箱是否存在都返回 200(防枚举)
        QMessageBox.information(
            self,
            "已发送",
            "如果该邮箱已注册,重置链接会很快发送(开发环境请看日志)。",
        )
        self._statusLabel.setText("重置链接已发送(开发环境请在日志中查找 token)")

    def _onSubmit(self) -> None:
        token = self._tokenEdit.text().strip()
        newPw = self._newPasswordEdit.text()
        if not token:
            self._statusLabel.setText("请输入 token")
            return
        if newPw != self._confirmEdit.text():
            self._statusLabel.setText("两次输入的密码不一致")
            return
        if not _validatePassword(newPw):
            self._statusLabel.setText("新密码不符合强度要求(至少 10 位 + 字母 + 数字)")
            return
        try:
            getCloudAuth().confirmPasswordReset(token, newPw)
        except CloudApiError as exc:
            self._statusLabel.setText(f"重置失败:{exc.message}")
            return
        QMessageBox.information(self, "完成", "密码已重置,请用新密码登录。")
        self.passwordReset.emit()
        self.accept()


class LoginDialog(QDialog):
    """登录 / 注册对话框(同一窗口内 Tab 切换)。"""

    loginSucceeded = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("登录 Prismatica 账号")
        self.setMinimumWidth(440)
        self.setMinimumHeight(480)
        self._buildUi()
        # 监听 online 状态:断开时按钮置灰
        try:
            from app.core.utils import cfg

            self._baseUrl = (cfg.cloudBaseUrl.value or "").strip()
        except Exception:
            self._baseUrl = ""
        self._refreshOfflineState()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _buildUi(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        # Header
        headerRow = QHBoxLayout()
        headerRow.addWidget(LargeTitleLabel("Prismatica 账号"))
        headerRow.addStretch(1)
        headerRow.addWidget(CaptionLabel("v1.0 · 云端 P0-A"))
        root.addLayout(headerRow)

        # Tab
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._loginPage = self._buildLoginPage()
        self._registerPage = self._buildRegisterPage()
        self._stack.addWidget(self._loginPage)
        self._stack.addWidget(self._registerPage)

        # Tab 切换按钮组(用 PushButton 当作分段控件)
        tabRow = QHBoxLayout()
        tabRow.setSpacing(8)
        self._tabLoginBtn = PushButton("登录")
        self._tabRegisterBtn = PushButton("注册新账号")
        self._tabLoginBtn.setCheckable(True)
        self._tabRegisterBtn.setCheckable(True)
        self._tabLoginBtn.setChecked(True)
        self._tabLoginBtn.clicked.connect(lambda: self._switchTab(0))
        self._tabRegisterBtn.clicked.connect(lambda: self._switchTab(1))
        tabRow.addWidget(self._tabLoginBtn)
        tabRow.addWidget(self._tabRegisterBtn)
        tabRow.addStretch(1)
        root.addLayout(tabRow)

        # 状态条
        self._statusLabel = CaptionLabel(" ")
        self._statusLabel.setStyleSheet("color: #c42b1c;")
        root.addWidget(self._statusLabel)

    def _buildLoginPage(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(12)

        self._loginEmailEdit = QLineEdit()
        self._loginEmailEdit.setPlaceholderText("邮箱")
        layout.addWidget(self._loginEmailEdit)

        self._loginPasswordEdit = PasswordLineEdit()
        self._loginPasswordEdit.setPlaceholderText("密码")
        layout.addWidget(self._loginPasswordEdit)

        forgotRow = QHBoxLayout()
        forgotRow.addStretch(1)
        self._forgotBtn = CaptionLabel("忘记密码?")
        self._forgotBtn.setStyleSheet("color: #0078d4;")
        self._forgotBtn.setCursor(Qt.PointingHandCursor)
        self._forgotBtn.mousePressEvent = self._onForgotClicked  # type: ignore[assignment]
        forgotRow.addWidget(self._forgotBtn)
        layout.addLayout(forgotRow)

        self._loginBtn = PrimaryPushButton("登录")
        self._loginBtn.clicked.connect(self._onLogin)
        layout.addWidget(self._loginBtn)

        layout.addStretch(1)
        return page

    def _buildRegisterPage(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(12)

        self._regEmailEdit = QLineEdit()
        self._regEmailEdit.setPlaceholderText("邮箱")
        layout.addWidget(self._regEmailEdit)

        self._regPasswordEdit = PasswordLineEdit()
        self._regPasswordEdit.setPlaceholderText("密码(至少 10 位,含字母+数字)")
        layout.addWidget(self._regPasswordEdit)

        self._regDisplayEdit = QLineEdit()
        self._regDisplayEdit.setPlaceholderText("昵称(可选)")
        layout.addWidget(self._regDisplayEdit)

        self._registerBtn = PrimaryPushButton("创建账号并登录")
        self._registerBtn.clicked.connect(self._onRegister)
        layout.addWidget(self._registerBtn)

        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------
    # 行为
    # ------------------------------------------------------------------

    def _switchTab(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._tabLoginBtn.setChecked(index == 0)
        self._tabRegisterBtn.setChecked(index == 1)
        self._statusLabel.setText(" ")

    def _refreshOfflineState(self) -> None:
        offline = not self._baseUrl
        if offline:
            self._statusLabel.setText("未配置云端 API 地址,登录功能不可用。")
        for btn in (self._loginBtn, self._registerBtn):
            btn.setEnabled(not offline)
            if offline:
                btn.setToolTip("请先在「设置 → 云端」配置 API 地址")
            else:
                btn.setToolTip("")

    def _onForgotClicked(self, *args) -> None:
        dlg = _PasswordResetDialog(self)
        dlg.exec()

    def _onLogin(self) -> None:
        email = self._loginEmailEdit.text().strip()
        password = self._loginPasswordEdit.text()
        if not _validateEmail(email):
            self._statusLabel.setText("邮箱格式不正确")
            return
        if not password:
            self._statusLabel.setText("请输入密码")
            return
        self._loginBtn.setEnabled(False)
        self._statusLabel.setText("登录中…")
        try:
            getCloudAuth().login(email, password)
        except CloudApiError as exc:
            logger.warning(f"[LoginDialog] 登录失败: {exc}")
            if exc.code == "INVALID_CREDENTIALS":
                self._statusLabel.setText("邮箱或密码错误")
            elif exc.code == "ACCOUNT_LOCKED":
                retry = (exc.details or {}).get("retryAfter")
                if retry:
                    self._statusLabel.setText(f"账号已锁定,{retry} 秒后可重试")
                else:
                    self._statusLabel.setText("账号已锁定,请稍后再试")
            elif exc.code == "NETWORK_ERROR":
                self._statusLabel.setText("网络异常,请检查连接")
            elif exc.code == "RATE_LIMITED":
                self._statusLabel.setText("请求过于频繁,请稍后再试")
            else:
                self._statusLabel.setText(f"登录失败:{exc.message}")
            self._loginBtn.setEnabled(True)
            return
        except Exception as exc:
            logger.exception("[LoginDialog] 登录异常")
            self._statusLabel.setText(f"登录失败:{exc}")
            self._loginBtn.setEnabled(True)
            return
        self._statusLabel.setText("登录成功")
        self.loginSucceeded.emit()
        self.accept()

    def _onRegister(self) -> None:
        email = self._regEmailEdit.text().strip()
        password = self._regPasswordEdit.text()
        display = self._regDisplayEdit.text().strip()
        if not _validateEmail(email):
            self._statusLabel.setText("邮箱格式不正确")
            return
        if not _validatePassword(password):
            self._statusLabel.setText("密码不符合强度要求(至少 10 位 + 字母 + 数字)")
            return
        self._registerBtn.setEnabled(False)
        self._statusLabel.setText("注册中…")
        try:
            getCloudAuth().register(email, password, display)
        except CloudApiError as exc:
            logger.warning(f"[LoginDialog] 注册失败: {exc}")
            if exc.code == "EMAIL_ALREADY_USED":
                self._statusLabel.setText("该邮箱已注册,直接登录即可")
            elif exc.code == "WEAK_PASSWORD":
                self._statusLabel.setText("密码强度不足")
            elif exc.code == "NETWORK_ERROR":
                self._statusLabel.setText("网络异常,请检查连接")
            else:
                self._statusLabel.setText(f"注册失败:{exc.message}")
            self._registerBtn.setEnabled(True)
            return
        except Exception as exc:
            logger.exception("[LoginDialog] 注册异常")
            self._statusLabel.setText(f"注册失败:{exc}")
            self._registerBtn.setEnabled(True)
            return
        self._statusLabel.setText("注册并登录成功")
        self.loginSucceeded.emit()
        self.accept()


__all__ = ["LoginDialog"]
