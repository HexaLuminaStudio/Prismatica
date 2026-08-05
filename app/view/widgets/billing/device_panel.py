# coding: utf-8
"""设备信息面板 - 展示本机机器码

修复(2026-08-05):
    1. pyperclip 在无 tkinter 的环境会抛 ImportError → 用 QGuiApplication.clipboard()
    2. 机器码用 LineEdit 太长容易看不清 → 用可滚动 + 等宽字体
    3. 增加"用途说明"折叠区域
    4. 修复:ExpandGroupSettingCard 是个 SettingCard 风格的 widget,
       依赖外层窗口宽度做标题/值布局;塞进 320px 窄列时,标题/值/图标严重错位,
       整个卡片显示变形。
       → 改为 SimpleExpandGroup(普通折叠面板)+ 标题/正文 QLabel 组合,
         完全不依赖 SettingCard 的窗口宽度假设。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QPlainTextEdit,
    QFrame,
    QSizePolicy,
    QToolButton,
)
from qfluentwidgets import (
    StrongBodyLabel,
    BodyLabel,
    PushButton,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    IconWidget,
)

from app.core.utils.device_id import generateOrLoadDeviceId


class _ExpandGroup(QWidget):
    """轻量级折叠面板:不依赖 SettingCard 的窗口宽度假设。

    用于替代 ExpandGroupSettingCard,在窄列容器中也能正常显示。
    """

    def __init__(
        self,
        icon: FIF,
        title: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._card = QFrame(self)
        self._card.setObjectName("DeviceHelpCard")
        self._card.setFrameShape(QFrame.NoFrame)
        self._card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        cardLayout = QVBoxLayout(self._card)
        cardLayout.setContentsMargins(12, 10, 12, 10)
        cardLayout.setSpacing(8)

        # 标题行(图标 + 文字 + 折叠箭头按钮)
        headerRow = QHBoxLayout()
        headerRow.setSpacing(8)
        headerRow.setContentsMargins(0, 0, 0, 0)

        self._icon = IconWidget(icon, self._card)
        self._icon.setFixedSize(16, 16)
        headerRow.addWidget(self._icon)

        self._titleLabel = StrongBodyLabel(title, self._card)
        self._titleLabel.setStyleSheet("font-size: 13px;")
        headerRow.addWidget(self._titleLabel, 1)

        self._toggleBtn = QToolButton(self._card)
        self._toggleBtn.setText("▼")
        self._toggleBtn.setFixedSize(20, 20)
        self._toggleBtn.setStyleSheet(
            "QToolButton { border: none; background: transparent; "
            "color: palette(text); font-size: 11px; }"
        )
        self._toggleBtn.setCursor(Qt.PointingHandCursor)
        self._toggleBtn.clicked.connect(self._toggle)
        headerRow.addWidget(self._toggleBtn)

        cardLayout.addLayout(headerRow)

        # 内容容器(可隐藏)
        self._content = QWidget(self._card)
        self._content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        contentLayout = QVBoxLayout(self._content)
        contentLayout.setContentsMargins(24, 0, 0, 0)
        contentLayout.setSpacing(4)
        self._contentLayout = contentLayout
        cardLayout.addWidget(self._content)

        outer.addWidget(self._card)

        self._expanded = False
        self._content.hide()
        self._toggleBtn.setText("▶")
        self._updateStyle()

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        self._toggleBtn.setText("▼" if self._expanded else "▶")
        self._updateStyle()

    def _updateStyle(self) -> None:
        self._card.setStyleSheet(
            "#DeviceHelpCard {"
            " background: rgba(0,0,0,4%);"
            " border: 1px solid rgba(0,0,0,10%);"
            " border-radius: 6px;"
            " }"
        )

    def addWidget(self, w: QWidget) -> None:
        """把子控件加入折叠内容区。"""
        self._contentLayout.addWidget(w)


class DevicePanel(QWidget):
    """本机机器码展示面板"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # 标题 + 操作按钮
        header = QHBoxLayout()
        self.titleLabel = StrongBodyLabel("本机机器码", self)
        header.addWidget(self.titleLabel)
        header.addStretch(1)
        self.copyBtn = PushButton("复制", self, FIF.COPY)
        self.copyBtn.clicked.connect(self._onCopy)
        header.addWidget(self.copyBtn)
        outer.addLayout(header)

        # 机器码显示(等宽字体,只读,自适应高度)
        self.codeView = QPlainTextEdit(self)
        self.codeView.setReadOnly(True)
        self.codeView.setMaximumHeight(96)
        self.codeView.setMinimumHeight(72)
        self.codeView.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(11)
        self.codeView.setFont(mono)
        # 去掉边框让其更内嵌
        self.codeView.setStyleSheet(
            "QPlainTextEdit { background: rgba(0,0,0,4%); "
            "border: 1px solid rgba(0,0,0,10%); border-radius: 6px; "
            "padding: 8px; }"
        )
        outer.addWidget(self.codeView)

        # 说明(可折叠)—— 用轻量级折叠面板,不再用 ExpandGroupSettingCard
        self.helpCard = _ExpandGroup(
            FIF.INFO,
            "机器码用途",
            self,
        )
        self.helpCard.addWidget(
            BodyLabel(
                "• 内测期间辅助识别用户,排查崩溃/反馈时定位日志。",
                self.helpCard,
            )
        )
        self.helpCard.addWidget(
            BodyLabel(
                "• 本期内测**不强制绑定设备**,您可以随时在其他机器使用同账号。",
                self.helpCard,
            )
        )
        self.helpCard.addWidget(
            BodyLabel(
                "• RC+ 正式版将启用设备绑定,以提升账号安全。",
                self.helpCard,
            )
        )
        outer.addWidget(self.helpCard)

        outer.addStretch(1)

        self.refresh()

    def refresh(self) -> None:
        try:
            fp = generateOrLoadDeviceId()
            self.codeView.setPlainText(fp)
        except Exception as e:
            self.codeView.setPlainText(f"(采集失败: {e})")

    def _onCopy(self) -> None:
        text = self.codeView.toPlainText()
        try:
            clipboard = QGuiApplication.clipboard()
            clipboard.setText(text)
            InfoBar.success(
                title="已复制",
                content="机器码已复制到剪贴板",
                parent=self,
                duration=2000,
                position=InfoBarPosition.TOP,
            )
        except Exception as e:
            InfoBar.error(
                title="复制失败",
                content=str(e),
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )
