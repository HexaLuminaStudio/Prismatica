# coding: utf-8
"""充值码兑换弹窗

修复(2026-08-05):
    1. codeEdit.text() 没 trim → 用户粘贴带换行的码会失败
    2. 空值时仅打 InfoBar 但没聚焦 → 用户看不到焦点在哪
    3. self.view 重新赋值 → 与 MessageBoxBase 内部 view 冲突
    4. 按钮文案与图标不一致
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget
from qfluentwidgets import (
    MessageBoxBase,
    SubtitleLabel,
    BodyLabel,
    CaptionLabel,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
)

from app.core.services.auth_service import getAuthService


def _resolveBodyWidget(dlg: MessageBoxBase) -> QWidget:
    return getattr(dlg, "widget", None) or getattr(dlg, "view", None)


class RechargeDialog(MessageBoxBase):
    """输入充值码弹窗"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self._success = False

        # 标题与说明
        self.titleLabel = SubtitleLabel("输入充值码", self)
        self.hintLabel = CaptionLabel(
            "请输入 RCH-XXXX-XXXX-XXXX 格式的充值码", self
        )

        # 输入框
        self.codeEdit = LineEdit(self)
        self.codeEdit.setPlaceholderText("RCH-XXXX-XXXX-XXXX")
        self.codeEdit.setClearButtonEnabled(True)
        # 粘贴时自动 trim 空白
        self.codeEdit.textChanged.connect(self._onCodeChanged)

        # 余额提示(从剪贴板自动识别体验码/邀请码)
        self._extraLabel = CaptionLabel("", self)
        self._extraLabel.setStyleSheet("color: #888;")

        layout = getattr(self, "viewLayout", None) or _resolveBodyWidget(self).layout()
        layout.setContentsMargins(24, 24, 24, 16)
        layout.setSpacing(8)
        layout.addWidget(self.titleLabel)
        layout.addWidget(self.hintLabel)
        layout.addSpacing(4)
        layout.addWidget(self.codeEdit)
        layout.addWidget(self._extraLabel)
        layout.addStretch(1)

        # 按钮
        self.yesButton.setText("兑换")
        self.cancelButton.setText("取消")
        # yesButton 默认连接 accept;重定向到 _onConfirm
        try:
            self.yesButton.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.yesButton.clicked.connect(self._onConfirm)
        self.buttonGroup.setMinimumWidth(220)
        _resolveBodyWidget(self).setMinimumWidth(360)
        _resolveBodyWidget(self).setMinimumHeight(180)

    # ---------- 状态 ----------
    def isSuccess(self) -> bool:
        return self._success

    # ---------- 槽 ----------
    def _onCodeChanged(self, text: str) -> None:
        """根据前缀自动提示,避免用户把邀请码误填到充值码框。"""
        t = text.strip()
        if not t:
            self._extraLabel.setText("")
            return
        prefix = t.split("-", 1)[0] if "-" in t else t[:3]
        if prefix == "RCH":
            self._extraLabel.setText("✓ 格式正确")
            self._extraLabel.setStyleSheet("color: #2bb673;")
        elif prefix in ("INV", "TRY"):
            self._extraLabel.setText(
                f"⚠ 这是{('邀请码' if prefix == 'INV' else '体验码')},请前往启动门输入"
            )
            self._extraLabel.setStyleSheet("color: #d83b3b;")
        else:
            self._extraLabel.setText("格式: RCH-XXXX-XXXX-XXXX")
            self._extraLabel.setStyleSheet("color: #888;")

    def _onConfirm(self) -> None:
        # 关键:trim 空白 + 聚焦提示
        code = self.codeEdit.text().strip()
        if not code:
            self.codeEdit.setError(True)
            self.codeEdit.setFocus()
            InfoBar.warning(
                title="请输入充值码",
                content="充值码不能为空",
                parent=self,
                duration=2500,
                position=InfoBarPosition.TOP,
            )
            return
        self.codeEdit.setError(False)

        auth = getAuthService()
        if not auth.isAuthenticated():
            InfoBar.error(
                title="未激活",
                content="请先使用邀请码/体验码激活后再充值",
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )
            return

        # 走 AuthService.redeemCode(自动识别 RCH 类型)
        from app.core.utils.signed_code import tryParseAnyCode

        try:
            kind, model = tryParseAnyCode(code)
        except Exception as e:
            InfoBar.error(
                title="充值码无效",
                content=str(e),
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )
            self.codeEdit.setError(True)
            return

        if kind != "recharge":
            InfoBar.error(
                title="类型错误",
                content="请输入 RCH- 开头的充值码",
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )
            self.codeEdit.setError(True)
            return

        result = auth.redeemCode(code)
        if result.success:
            self._success = True
            InfoBar.success(
                title="充值成功",
                content=result.message,
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )
            self.accept()
        else:
            # 修复 UI-6(2026-08-05):根据 result.code 给用户更明确的引导
            title, hint = self._formatError(result.code, result.message)
            InfoBar.error(
                title=title,
                content=f"{result.message}\n{hint}".strip(),
                parent=self,
                duration=3500,
                position=InfoBarPosition.TOP,
            )
            self.codeEdit.setError(True)

    @staticmethod
    def _formatError(code: str, message: str) -> tuple[str, str]:
        """根据机器可读错误码返回 (标题, 引导文案)。"""
        if code == "ALREADY_USED":
            return "充值失败", "💡 每个充值码仅可使用一次,请联系运营获取新码"
        if code == "EXPIRED":
            return "充值失败", "💡 该充值码已过期,请联系运营补发"
        if code == "NEED_ACTIVATION":
            return "未激活", "💡 请先到「账户」页用邀请码或体验码激活"
        if code == "INVALID":
            return "充值码无效", "💡 请检查码格式或联系运营确认"
        # 兜底
        return "充值失败", ""