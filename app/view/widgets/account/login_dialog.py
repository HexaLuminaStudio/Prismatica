# coding: utf-8
"""Prismatica 桌面端登录与注册界面。"""
from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import QEasingCurve, Qt, Signal, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import FluentIcon, PrimaryPushButton, PushButton, CheckBox
from qfluentwidgetspro import IndeterminateProgressPushButton

from app.core.services import CloudApiError, getCloudAuth
from app.core.services.cloud_auth import CloudLoginWorker
from app.core.utils import logger
from app.view.resource import resource as _resource
from app.view.widgets.prismatica_theme import ACCENT, shellPalette


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 10
AUTH_CARD_SIZE = (560, 660)


def _validateEmail(text: str) -> bool:
    return bool(text) and bool(EMAIL_RE.match(text.strip()))


def _validatePassword(text: str) -> bool:
    return (
        bool(text)
        and len(text) >= MIN_PASSWORD_LEN
        and any(char.isalpha() for char in text)
        and any(char.isdigit() for char in text)
    )


class _AuthBrandMark(QSvgWidget):
    """顶部用户图标标识，与资源中的 User.svg 保持一致。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(":/app/icons/User.svg", parent)
        self.setFixedSize(56, 56)
        self.setAttribute(Qt.WA_TranslucentBackground)


class _TransitionOverlay(QWidget):
    """不参与命中测试的轻量转场蒙层，避免 QGraphicsEffect 合成异常。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor("#FFFFFF")
        self._opacity = 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.hide()

    def setColor(self, color: QColor) -> None:  # noqa: N802
        self._color = QColor(color)

    def setOpacity(self, opacity: float) -> None:  # noqa: N802
        self._opacity = max(0.0, min(1.0, float(opacity)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        color = QColor(self._color)
        color.setAlphaF(self._opacity)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(self.rect(), 12, 12)


class _AuthLineEdit(QLineEdit):
    """带 Fluent 前缀图标的统一输入框。"""

    def __init__(
        self,
        placeholder: str,
        icon: FluentIcon,
        *,
        password: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(not password)
        self.addAction(icon.icon(), QLineEdit.LeadingPosition)
        if password:
            self.setEchoMode(QLineEdit.Password)
            self._revealAction = self.addAction(FluentIcon.VIEW.icon(), QLineEdit.TrailingPosition)
            self._revealAction.setToolTip("显示密码")
            self._revealAction.triggered.connect(self._togglePassword)
        self._applyTheme()

    def _togglePassword(self) -> None:
        reveal = self.echoMode() == QLineEdit.Password
        self.setEchoMode(QLineEdit.Normal if reveal else QLineEdit.Password)
        self._revealAction.setIcon((FluentIcon.HIDE if reveal else FluentIcon.VIEW).icon())
        self._revealAction.setToolTip("隐藏密码" if reveal else "显示密码")

    def _applyTheme(self) -> None:
        palette = shellPalette()
        background = "#FFFFFF" if palette.content.lightness() > 128 else "#24292D"
        borderHover = "#5F6B73" if palette.content.lightness() <= 128 else "#AAB6BC"
        self.setStyleSheet(
            f"""
            QLineEdit {{
                padding: 0 34px 0 34px;
                color: {palette.text.name()};
                background-color: {background};
                border: 1px solid {palette.border.name()};
                border-radius: 6px;
                selection-background-color: {ACCENT.name()};
            }}
            QLineEdit:hover {{ border-color: {borderHover}; }}
            QLineEdit:focus {{ border: 1px solid {ACCENT.name()}; }}
            """
        )


class _StatusBanner(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("authStatusBanner")
        self.setStyleSheet(
            """
            QFrame#authStatusBanner {
                background-color: #FFF2F0;
                border: 1px solid #FFD1CC;
                border-radius: 6px;
            }
            QLabel { color: #B42318; background: transparent; }
            """
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        icon = QLabel("!")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(18, 18)
        icon.setStyleSheet(
            "color: white; background-color: #D92D20; border-radius: 9px; font-weight: 700;"
        )
        self._label = QLabel()
        self._label.setWordWrap(True)
        row.addWidget(icon)
        row.addWidget(self._label, 1)
        self.hide()

    def setText(self, text: str) -> None:  # noqa: N802
        message = text.strip()
        self._label.setText(message)
        self.setVisible(bool(message))

    def text(self) -> str:
        return self._label.text()


class _StatusProxy:
    """兼容旧代码的 `_statusLabel`，并将信息路由到当前页面。"""

    def __init__(self, page: "LoginInterface") -> None:
        self._page = page

    def setText(self, text: str) -> None:  # noqa: N802
        self._page._currentStatus().setText(text)

    def text(self) -> str:
        return self._page._currentStatus().text()


class _Divider(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        for _ in range(2):
            line = QFrame()
            line.setObjectName("authDividerLine")
            line.setFrameShape(QFrame.HLine)
            if row.count() == 0:
                row.addWidget(line, 1)
                badge = QLabel("或")
                badge.setObjectName("authDividerBadge")
                badge.setAlignment(Qt.AlignCenter)
                badge.setFixedSize(28, 28)
                row.addWidget(badge)
            else:
                row.addWidget(line, 1)


class LoginInterface(QWidget):
    """嵌入主窗口内容区的登录/注册页面。"""

    loginSucceeded = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("accountAuthInterface")
        self._loginWorker: CloudLoginWorker | None = None
        authFont = QFont("Microsoft YaHei UI", 10)
        authFont.setFamilies(
            ["Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"]
        )
        self.setFont(authFont)
        self._buildUi()
        try:
            from app.core.utils import cfg

            self._baseUrl = (cfg.cloudBaseUrl.value or "").strip()
            cfg.cloudBaseUrl.valueChanged.connect(self._onBaseUrlChanged)
        except Exception:
            self._baseUrl = ""
        self._refreshOfflineState()

    def _onBaseUrlChanged(self, _value: str) -> None:
        try:
            from app.core.utils import cfg

            self._baseUrl = (cfg.cloudBaseUrl.value or "").strip()
        except Exception:
            self._baseUrl = ""
        self._refreshOfflineState()

    def _buildUi(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(0)
        root.addStretch(1)

        self._shell = QFrame()
        self._shell.setObjectName("authCard")
        self._shell.setFixedSize(*AUTH_CARD_SIZE)
        root.addWidget(self._shell, 0, Qt.AlignCenter)
        root.addStretch(1)

        shellLayout = QVBoxLayout(self._shell)
        shellLayout.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        shellLayout.addWidget(self._stack)
        self._loginPage = self._buildLoginPage()
        self._registerPage = self._buildRegisterPage()
        self._stack.addWidget(self._loginPage)
        self._stack.addWidget(self._registerPage)

        self._transitionOverlay = _TransitionOverlay(self._shell)
        self._transitionAnimation = QVariantAnimation(self)
        self._transitionAnimation.setDuration(160)
        self._transitionAnimation.setEasingCurve(QEasingCurve.OutCubic)
        self._transitionAnimation.valueChanged.connect(
            self._transitionOverlay.setOpacity
        )
        self._transitionAnimation.finished.connect(self._finishTransition)

        self._statusLabel = _StatusProxy(self)
        self._applyTheme()
        self._switchTab(0, animate=False)

    def _buildLoginPage(self) -> QWidget:
        page = QWidget()
        page.setObjectName("authPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(90, 30, 90, 24)
        layout.setSpacing(0)
        self._addBrandHeader(
            layout,
            "登录 Prismatica",
            "使用邮箱登录，继续你的语料研究",
        )
        layout.addSpacing(28)
        self._addFieldLabel(layout, "邮箱")
        self._loginEmailEdit = _AuthLineEdit("name@example.com", FluentIcon.MAIL)
        layout.addWidget(self._loginEmailEdit)
        layout.addSpacing(16)
        self._addFieldLabel(layout, "密码")
        self._loginPasswordEdit = _AuthLineEdit("请输入密码", FluentIcon.FINGERPRINT, password=True)
        self._loginPasswordEdit.returnPressed.connect(self._onLogin)
        layout.addWidget(self._loginPasswordEdit)
        layout.addSpacing(12)

        options = QHBoxLayout()
        options.setContentsMargins(0, 0, 0, 0)
        self._rememberCheck = CheckBox("记住我")
        options.addWidget(self._rememberCheck)
        options.addStretch(1)
        layout.addLayout(options)
        layout.addSpacing(20)

        self._loginBtn = IndeterminateProgressPushButton("登录")
        self._loginBtn.setFixedHeight(44)
        self._loginBtn.clicked.connect(self._onLogin)
        layout.addWidget(self._loginBtn)
        self._loginStatus = _StatusBanner()
        layout.addSpacing(10)
        layout.addWidget(self._loginStatus)
        layout.addStretch(1)
        layout.addWidget(_Divider())
        layout.addSpacing(14)
        self._toRegisterBtn = PushButton("还没有账号？  注册")
        self._toRegisterBtn.setFixedHeight(40)
        self._toRegisterBtn.clicked.connect(lambda: self._switchTab(1))
        layout.addWidget(self._toRegisterBtn)
        layout.addSpacing(16)
        self._addFooter(layout, includeTerms=True)
        return page

    def _buildRegisterPage(self) -> QWidget:
        page = QWidget()
        page.setObjectName("authPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(50, 28, 50, 22)
        layout.setSpacing(0)
        self._addBrandHeader(
            layout,
            "创建 Prismatica 账号",
            "注册后同步你的研究进度与任务",
        )
        layout.addSpacing(18)
        self._addFieldLabel(layout, "邮箱")
        self._regEmailEdit = _AuthLineEdit("name@example.com", FluentIcon.MAIL)
        layout.addWidget(self._regEmailEdit)
        layout.addSpacing(12)
        self._addFieldLabel(layout, "密码")
        self._regPasswordEdit = _AuthLineEdit("至少 10 位，包含字母和数字", FluentIcon.FINGERPRINT, password=True)
        self._regPasswordEdit.textChanged.connect(self._updatePasswordStrength)
        layout.addWidget(self._regPasswordEdit)
        layout.addSpacing(7)
        strengthRow = QHBoxLayout()
        strengthRow.setSpacing(10)
        self._passwordStrength = QProgressBar()
        self._passwordStrength.setRange(0, 4)
        self._passwordStrength.setValue(0)
        self._passwordStrength.setTextVisible(False)
        self._passwordStrength.setFixedHeight(5)
        self._strengthLabel = QLabel("请输入密码")
        self._strengthLabel.setObjectName("authHint")
        strengthRow.addWidget(self._passwordStrength, 1)
        strengthRow.addWidget(self._strengthLabel)
        layout.addLayout(strengthRow)
        layout.addSpacing(12)

        self._addFieldLabel(layout, "确认密码")
        self._regConfirmEdit = _AuthLineEdit("再次输入密码", FluentIcon.FINGERPRINT, password=True)
        self._regConfirmEdit.textChanged.connect(self._updateConfirmHint)
        layout.addWidget(self._regConfirmEdit)
        self._confirmHint = QLabel("")
        self._confirmHint.setObjectName("authErrorHint")
        self._confirmHint.setFixedHeight(17)
        layout.addWidget(self._confirmHint)
        layout.addSpacing(8)
        self._agreementCheck = CheckBox("我已阅读并同意《服务条款》与《隐私政策》")
        layout.addWidget(self._agreementCheck)
        layout.addSpacing(14)

        self._registerBtn = PrimaryPushButton("创建账号并登录")
        self._registerBtn.setFixedHeight(44)
        self._registerBtn.clicked.connect(self._onRegister)
        layout.addWidget(self._registerBtn)
        self._registerStatus = _StatusBanner()
        layout.addSpacing(8)
        layout.addWidget(self._registerStatus)
        layout.addStretch(1)
        layout.addWidget(_Divider())
        layout.addSpacing(12)
        self._backLoginBtn = PushButton("返回登录")
        self._backLoginBtn.setFixedHeight(40)
        self._backLoginBtn.clicked.connect(lambda: self._switchTab(0))
        layout.addWidget(self._backLoginBtn)
        layout.addSpacing(12)
        self._addFooter(layout)
        return page

    def _addBrandHeader(self, layout: QVBoxLayout, title: str, subtitle: str) -> None:
        brandRow = QHBoxLayout()
        brandRow.addStretch(1)
        brandRow.addWidget(_AuthBrandMark())
        brandRow.addStretch(1)
        layout.addLayout(brandRow)
        layout.addSpacing(12)
        titleLabel = QLabel(title)
        titleLabel.setObjectName("authTitle")
        titleLabel.setAlignment(Qt.AlignCenter)
        subtitleLabel = QLabel(subtitle)
        subtitleLabel.setObjectName("authSubtitle")
        subtitleLabel.setAlignment(Qt.AlignCenter)
        layout.addWidget(titleLabel)
        layout.addSpacing(6)
        layout.addWidget(subtitleLabel)

    def _addFieldLabel(self, layout: QVBoxLayout, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("authFieldLabel")
        layout.addWidget(label)
        layout.addSpacing(6)

    def _addFooter(self, layout: QVBoxLayout, *, includeTerms: bool = False) -> None:
        footer = QLabel("Prismatica · 专注于语料研究")
        footer.setObjectName("authFooter")
        footer.setAlignment(Qt.AlignCenter)
        layout.addWidget(footer)
        if includeTerms:
            terms = QLabel("登录即代表同意《服务条款》与《隐私政策》")
            terms.setObjectName("authFooter")
            terms.setAlignment(Qt.AlignCenter)
            layout.addSpacing(4)
            layout.addWidget(terms)

    def _applyTheme(self) -> None:
        palette = shellPalette()
        card = "#FFFFFF" if palette.content.lightness() > 128 else "#202428"
        self._transitionOverlay.setColor(QColor(card))
        page = "transparent"
        self.setStyleSheet(
            f"""
            QWidget#accountAuthInterface {{ background: transparent; }}
            QFrame#authCard {{
                background-color: {card};
                border: 1px solid {palette.border.name()};
                border-radius: 12px;
            }}
            QWidget#authPage {{ background: {page}; border: none; }}
            QLabel {{ color: {palette.text.name()}; background: transparent; border: none; }}
            QLabel#authTitle {{ font-size: 23px; font-weight: 600; }}
            QLabel#authSubtitle {{ color: {palette.mutedText.name()}; font-size: 13px; }}
            QLabel#authFieldLabel {{ font-size: 13px; font-weight: 500; }}
            QLabel#authHint, QLabel#authFooter {{ color: {palette.mutedText.name()}; font-size: 11px; }}
            QLabel#authErrorHint {{ color: #D92D20; font-size: 11px; }}
            QLabel#authDividerBadge {{ color: {palette.mutedText.name()}; border: 1px solid {palette.border.name()}; border-radius: 14px; }}
            QFrame#authDividerLine {{ color: {palette.border.name()}; }}
            QCheckBox {{ color: {palette.text.name()}; spacing: 7px; background: transparent; }}
            QCheckBox:focus {{ outline: 2px solid {ACCENT.name()}; outline-offset: 2px; }}
            QProgressBar {{ background-color: {palette.border.name()}; border: none; border-radius: 2px; }}
            QProgressBar::chunk {{ background-color: {ACCENT.name()}; border-radius: 2px; }}
            """
        )

    def _switchTab(self, index: int, *, animate: bool = True) -> None:
        index = 1 if index else 0
        if index == 1 and self._loginEmailEdit.text() and not self._regEmailEdit.text():
            self._regEmailEdit.setText(self._loginEmailEdit.text())
        self._stack.setCurrentIndex(index)
        self._loginStatus.setText("")
        self._registerStatus.setText("")
        self._transitionAnimation.stop()
        if (
            animate
            and self.isVisible()
            and QApplication.isEffectEnabled(Qt.UIEffect.UI_General)
        ):
            self._transitionOverlay.setGeometry(self._shell.rect())
            self._transitionOverlay.setOpacity(0.16)
            self._transitionOverlay.show()
            self._transitionOverlay.raise_()
            self._transitionAnimation.setStartValue(0.16)
            self._transitionAnimation.setEndValue(0.0)
            self._transitionAnimation.start()
        else:
            self._finishTransition()

    def _finishTransition(self) -> None:
        self._transitionOverlay.setOpacity(0.0)
        self._transitionOverlay.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._setLoginBusy(
            self._loginWorker is not None and self._loginWorker.isRunning()
        )
        self._restoreActionButton(self._registerBtn, "创建账号并登录")

    def _currentStatus(self) -> _StatusBanner:
        return self._registerStatus if self._stack.currentIndex() else self._loginStatus

    def _updatePasswordStrength(self, password: str) -> None:
        score = 0
        score += int(len(password) >= MIN_PASSWORD_LEN)
        score += int(any(char.isalpha() for char in password) and any(char.isdigit() for char in password))
        score += int(any(char.islower() for char in password) and any(char.isupper() for char in password))
        score += int(any(not char.isalnum() for char in password))
        labels = ("请输入密码", "较弱", "可用", "良好", "强")
        colors = ("#B8C1C6", "#D92D20", "#E39400", "#00A28F", ACCENT.name())
        self._passwordStrength.setValue(score)
        self._strengthLabel.setText(labels[score])
        self._passwordStrength.setStyleSheet(
            f"QProgressBar {{ background: #E5EAED; border: none; border-radius: 2px; }} "
            f"QProgressBar::chunk {{ background: {colors[score]}; border-radius: 2px; }}"
        )
        self._updateConfirmHint(self._regConfirmEdit.text())

    def _updateConfirmHint(self, confirmation: str) -> None:
        mismatch = bool(confirmation) and confirmation != self._regPasswordEdit.text()
        self._confirmHint.setText("两次输入的密码不一致" if mismatch else "")

    def _refreshOfflineState(self) -> None:
        offline = not self._baseUrl
        loginBusy = self._loginWorker is not None and self._loginWorker.isRunning()
        if offline:
            self._loginStatus.setText("未配置云端 API 地址，登录功能暂不可用")
        self._loginBtn.setEnabled(not offline and not loginBusy)
        self._registerBtn.setEnabled(not offline)
        for button in (self._loginBtn, self._registerBtn):
            button.setToolTip("请先在设置中配置云端 API 地址" if offline else "")

    def _onLogin(self) -> None:
        if self._loginWorker is not None and self._loginWorker.isRunning():
            return
        email = self._loginEmailEdit.text().strip()
        password = self._loginPasswordEdit.text()
        if not _validateEmail(email):
            self._loginStatus.setText("请输入有效的邮箱地址")
            return
        if not password:
            self._loginStatus.setText("请输入密码")
            return
        worker = CloudLoginWorker(
            email,
            password,
            self._rememberCheck.isChecked(),
            parent=self,
        )
        self._loginWorker = worker
        self._loginStatus.setText("")
        self._setLoginBusy(True)
        worker.succeeded.connect(self._onLoginSucceeded)
        worker.failed.connect(self._onLoginFailed)
        worker.finished.connect(self._onLoginFinished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _onLoginSucceeded(self, _result: object) -> None:
        self.loginSucceeded.emit()

    def _onLoginFailed(self, error: object) -> None:
        if isinstance(error, CloudApiError):
            logger.warning(f"[LoginInterface] 登录失败: {error}")
            messages = {
                "INVALID_CREDENTIALS": "邮箱或密码错误",
                "NETWORK_ERROR": "无法连接云端服务，请检查网络后重试",
                "RATE_LIMITED": "请求过于频繁，请稍后再试",
            }
            if error.code == "ACCOUNT_LOCKED":
                retry = (error.details or {}).get("retryAfter")
                message = f"账号已锁定，{retry} 秒后可重试" if retry else "账号已锁定，请稍后再试"
            else:
                message = messages.get(error.code, f"登录失败：{error.message}")
            self._loginStatus.setText(message)
            return
        logger.error(f"[LoginInterface] 登录异常: {error}")
        self._loginStatus.setText("登录遇到异常，请稍后重试")

    def _onLoginFinished(self) -> None:
        self._loginWorker = None
        self._setLoginBusy(False)

    def _setLoginBusy(self, busy: bool) -> None:
        if busy:
            self._loginBtn.load()
            self._loginBtn.setText("正在连接云端…")
        else:
            self._loginBtn.normal()
            self._loginBtn.setText("登录")
        self._loginBtn.setEnabled(bool(self._baseUrl) and not busy)
        for widget in (
            self._loginEmailEdit,
            self._loginPasswordEdit,
            self._rememberCheck,
            self._toRegisterBtn,
        ):
            widget.setEnabled(not busy)

    def _onRegister(self) -> None:
        email = self._regEmailEdit.text().strip()
        password = self._regPasswordEdit.text()
        confirmation = self._regConfirmEdit.text()
        if not _validateEmail(email):
            self._registerStatus.setText("请输入有效的邮箱地址")
            return
        if not _validatePassword(password):
            self._registerStatus.setText("密码至少 10 位，且需要同时包含字母和数字")
            return
        if password != confirmation:
            self._registerStatus.setText("两次输入的密码不一致")
            return
        if not self._agreementCheck.isChecked():
            self._registerStatus.setText("请先阅读并同意服务条款与隐私政策")
            return
        self._registerBtn.setEnabled(False)
        self._registerStatus.setText("")
        self._registerBtn.setText("创建中…")
        try:
            getCloudAuth().register(email, password, "", rememberMe=self._rememberCheck.isChecked())
        except CloudApiError as exc:
            logger.warning(f"[LoginInterface] 注册失败: {exc}")
            messages = {
                "EMAIL_ALREADY_USED": "该邮箱已注册，可以直接返回登录",
                "WEAK_PASSWORD": "密码强度不足",
                "NETWORK_ERROR": "网络异常，请检查连接",
            }
            self._registerStatus.setText(messages.get(exc.code, f"注册失败：{exc.message}"))
            self._restoreActionButton(self._registerBtn, "创建账号并登录")
            return
        except Exception as exc:
            logger.exception("[LoginInterface] 注册异常")
            self._registerStatus.setText(f"注册失败：{exc}")
            self._restoreActionButton(self._registerBtn, "创建账号并登录")
            return
        self.loginSucceeded.emit()
        self._restoreActionButton(self._registerBtn, "创建账号并登录")

    def _restoreActionButton(self, button: QPushButton, text: str) -> None:
        button.setText(text)
        button.setEnabled(bool(self._baseUrl))

__all__ = ["LoginInterface"]
