# coding: utf-8
"""P0-A 桌面端 兑换码对话框。

桌面端 redeem 路径:把明文 INV/TRY/RCH 码通过 /v1/auth/redeem 上交给云端,
云端按 kind 分别处理(升级订阅 / 充值 / 试用)。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, MessageBox, PrimaryPushButton, PushButton, TitleLabel

from app.core.services import CloudApiError, getCloudApi
from app.core.utils import logger, signalBus


class RedeemDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("兑换码")
        self.setMinimumWidth(440)
        self._buildUi()

    def _buildUi(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        layout.addWidget(TitleLabel("兑换码 / 邀请码"))
        self._hint = CaptionLabel("支持 INV- / TRY- / RCH- 开头(后端自动识别)")
        layout.addWidget(self._hint)

        from PySide6.QtWidgets import QLineEdit

        self._codeEdit = QLineEdit()
        self._codeEdit.setPlaceholderText("INV-XXXX-XXXX-XXXX-XXXX")
        layout.addWidget(self._codeEdit)

        self._statusLabel = CaptionLabel(" ")
        self._statusLabel.setStyleSheet("color: #c42b1c;")
        layout.addWidget(self._statusLabel)

        btnRow = QHBoxLayout()
        btnRow.addStretch(1)
        cancelBtn = PushButton("取消")
        cancelBtn.clicked.connect(self.reject)
        btnRow.addWidget(cancelBtn)
        self._okBtn = PrimaryPushButton("兑换")
        self._okBtn.clicked.connect(self._onSubmit)
        btnRow.addWidget(self._okBtn)
        layout.addLayout(btnRow)

    def _onSubmit(self) -> None:
        from PySide6.QtCore import QThread

        code = self._codeEdit.text().strip()
        if not code:
            self._statusLabel.setText("请输入兑换码")
            return
        self._okBtn.setEnabled(False)
        self._statusLabel.setText("兑换中…")

        try:
            data = getCloudApi().post(
                "/v1/auth/redeem",
                body={
                    "code": code,
                    "deviceId": "",
                    "deviceName": "prismatica-desktop",
                    "platform": "win32",
                    "displayName": "",
                },
                withAuth=False,  # 兑换码 + 设备绑定,无需登录
            )
        except CloudApiError as exc:
            logger.warning(f"[RedeemDialog] 兑换失败: {exc}")
            if exc.code == "INVALID_CODE":
                self._statusLabel.setText("码无效或已损坏")
            elif exc.code == "EXPIRED":
                self._statusLabel.setText("该码已过期")
            elif exc.code == "ALREADY_USED":
                self._statusLabel.setText("该码已被使用")
            elif exc.code == "NEED_ACTIVATION":
                self._statusLabel.setText("请先激活(注册)后再使用兑换码")
            elif exc.code == "NETWORK_ERROR":
                self._statusLabel.setText("网络异常,请检查连接")
            else:
                self._statusLabel.setText(f"兑换失败:{exc.message}")
            self._okBtn.setEnabled(True)
            return
        except Exception as exc:
            logger.exception("[RedeemDialog] 异常")
            self._statusLabel.setText(f"兑换失败:{exc}")
            self._okBtn.setEnabled(True)
            return

        # 成功 — 触发余额 / 订阅刷新
        try:
            signalBus.balanceChanged.emit(0)  # 让主窗口重新拉 me()
        except Exception:
            pass
        try:
            from app.core.services import getCloudAuth
            # 兑换后用户已激活,后台尝试 bootstrap
            getCloudAuth().bootstrap()
        except Exception:
            pass

        balance = (data or {}).get("balance", {})
        if isinstance(balance, dict):
            balText = f"{balance.get('balance', 0)} 积分"
        else:
            balText = "—"

        MessageBox(
            "兑换成功",
            f"已成功兑换。当前余额:{balText}",
            self,
        ).exec()
        self.accept()


__all__ = ["RedeemDialog"]
