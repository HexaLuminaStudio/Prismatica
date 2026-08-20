# coding: utf-8
"""Prismatica 项目内置聊天视图与消息模型。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.core.utils import qconfig
from app.view.widgets.prismatica_theme import shellPalette


class ChatRole(Enum):
    """聊天消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ChatMessage:
    """文本聊天消息。"""

    id: int
    message: str
    role: str
    avatar: str = ""


@dataclass
class FileChatMessage:
    """附件聊天消息。"""

    id: int
    name: str
    content: str
    role: str
    avatar: str = ""


class SimpleChatTextEdit(QPlainTextEdit):
    """Enter 发送、Shift+Enter 换行的原生聊天输入框。"""

    returnPressed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("AI 聊天输入框")
        self.setTabChangesFocus(True)
        qconfig.themeChanged.connect(self._applyTheme)
        self._applyTheme()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        isReturn = event.key() in {
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        }
        hasShift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if isReturn and not hasShift:
            event.accept()
            self.returnPressed.emit()
            return
        super().keyPressEvent(event)

    def _applyTheme(self, *_args) -> None:
        palette = shellPalette()
        self.setStyleSheet(
            f"""
            QPlainTextEdit {{
                background-color: {palette.surface.name()};
                color: {palette.text.name()};
                border: 1px solid {palette.border.name()};
                border-radius: 10px;
                padding: 10px 12px;
                selection-background-color: {palette.accentSurface.name()};
                selection-color: {palette.text.name()};
            }}
            QPlainTextEdit:focus {{
                border-color: {palette.accentText.name()};
            }}
            """
        )


class _MessageText(QTextBrowser):
    """随内容高度增长、由外层统一滚动的消息正文。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._syncingHeight = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setOpenExternalLinks(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._syncHeight()
        )

    def setMessage(self, text: str) -> None:
        try:
            self.setMarkdown(text)
        except AttributeError:
            self.setPlainText(text)
        QTimer.singleShot(0, self._syncHeight)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        QTimer.singleShot(0, self._syncHeight)

    def _syncHeight(self) -> None:
        if self._syncingHeight:
            return
        self._syncingHeight = True
        viewportWidth = max(1, self.viewport().width())
        try:
            if abs(self.document().textWidth() - viewportWidth) > 1:
                self.document().setTextWidth(viewportWidth)
            documentHeight = self.document().documentLayout().documentSize().height()
            self.setFixedHeight(max(36, math.ceil(documentHeight) + 8))
        finally:
            self._syncingHeight = False


class _MessageBubble(QFrame):
    """单条可复制、可请求删除的消息气泡。"""

    copyRequested = Signal(str)
    deleteRequested = Signal(object)

    def __init__(
        self,
        message: ChatMessage | FileChatMessage,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._message = message
        self.setObjectName("chatMessageBubble")
        self.setProperty("chatRole", message.role)
        self.setMaximumWidth(760)
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self._roleLabel = QLabel(self)
        self._roleLabel.setObjectName("chatRoleLabel")
        self._copyButton = QToolButton(self)
        self._copyButton.setIcon(QIcon(":/app/icons/Copy.svg"))
        self._copyButton.setToolTip("复制消息")
        self._copyButton.setAccessibleName("复制这条消息")
        self._deleteButton = QToolButton(self)
        self._deleteButton.setIcon(QIcon(":/app/icons/Erase.svg"))
        self._deleteButton.setToolTip("删除消息")
        self._deleteButton.setAccessibleName("删除这条消息")
        for button in (self._copyButton, self._deleteButton):
            button.setAutoRaise(True)
            button.setFixedSize(28, 28)

        headerLayout = QHBoxLayout()
        headerLayout.setContentsMargins(0, 0, 0, 0)
        headerLayout.setSpacing(4)
        headerLayout.addWidget(self._roleLabel)
        headerLayout.addStretch(1)
        headerLayout.addWidget(self._copyButton)
        headerLayout.addWidget(self._deleteButton)

        self._body = _MessageText(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(6)
        layout.addLayout(headerLayout)
        layout.addWidget(self._body)

        self._copyButton.clicked.connect(self._copy)
        self._deleteButton.clicked.connect(
            lambda: self.deleteRequested.emit(self._message.id)
        )
        self.setMessage(message)

    @property
    def message(self) -> ChatMessage | FileChatMessage:
        return self._message

    def setMessage(self, message: ChatMessage | FileChatMessage) -> None:
        self._message = message
        self.setProperty("chatRole", message.role)
        self._roleLabel.setText(
            "你" if message.role == ChatRole.USER.value else "Prismatica AI"
        )
        if isinstance(message, FileChatMessage):
            bodyText = f"附件：{message.name}\n{message.content}"
        else:
            bodyText = message.message
        self._body.setMessage(bodyText)
        self.style().unpolish(self)
        self.style().polish(self)

    def applyTheme(self) -> None:
        palette = shellPalette()
        isUser = self._message.role == ChatRole.USER.value
        background = palette.accentSurface if isUser else palette.surface
        border = palette.accentText if isUser else palette.border
        self.setStyleSheet(
            f"""
            QFrame#chatMessageBubble {{
                background-color: {background.name()};
                border: 1px solid {border.name()};
                border-radius: 14px;
            }}
            QLabel#chatRoleLabel {{
                color: {palette.mutedText.name()};
                font-size: 12px;
                font-weight: 600;
                border: none;
                background: transparent;
            }}
            QTextBrowser {{
                color: {palette.text.name()};
                background: transparent;
                border: none;
            }}
            QToolButton {{
                background: transparent;
                border: none;
                border-radius: 6px;
                padding: 4px;
            }}
            QToolButton:hover {{
                background-color: {palette.surfaceAlt.name()};
            }}
            """
        )

    def _copy(self) -> None:
        if isinstance(self._message, FileChatMessage):
            text = f"{self._message.name}\n{self._message.content}"
        else:
            text = self._message.message
        QApplication.clipboard().setText(text)
        self.copyRequested.emit(text)


class PrismaticaChatWidget(QScrollArea):
    """使用原生 Qt 控件渲染消息的聊天记录视图。"""

    messageCopied = Signal()
    messageDeleteRequest = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._messages: dict[int, _MessageBubble] = {}
        self._rows: dict[int, QWidget] = {}
        self._streamMessageId: int | None = None
        self._loadingRow: QWidget | None = None
        self._backgroundColor = QColor()

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._content = QWidget(self)
        self._content.setObjectName("chatHistoryContent")
        self._contentLayout = QVBoxLayout(self._content)
        self._contentLayout.setContentsMargins(18, 18, 18, 18)
        self._contentLayout.setSpacing(12)
        self._contentLayout.addStretch(1)
        self.setWidget(self._content)

        qconfig.themeChanged.connect(self._applyTheme)
        self._applyTheme()

    def addMessage(self, message: ChatMessage | FileChatMessage) -> None:
        """添加消息；若同 ID 为流式占位，则转为最终消息。"""
        self.setLoading(False)
        existing = self._messages.get(message.id)
        if existing is not None:
            existing.setMessage(message)
            existing.applyTheme()
        else:
            self._addBubble(message)
        if self._streamMessageId == message.id:
            self._streamMessageId = None
        self._scrollToBottom()

    def updateMessage(self, message: ChatMessage | FileChatMessage) -> None:
        self.addMessage(message)

    def deleteMessage(self, messageId) -> None:
        """删除指定消息。"""
        try:
            normalizedId = int(messageId)
        except (TypeError, ValueError):
            return
        bubble = self._messages.pop(normalizedId, None)
        row = self._rows.pop(normalizedId, None)
        if bubble is None or row is None:
            return
        self._contentLayout.removeWidget(row)
        row.deleteLater()
        if self._streamMessageId == normalizedId:
            self._streamMessageId = None

    def setStreamMessage(self, message: ChatMessage) -> None:
        """新增或更新当前流式助手消息。"""
        self.setLoading(False)
        existing = self._messages.get(message.id)
        if existing is None:
            self._addBubble(message)
        else:
            existing.setMessage(message)
            existing.applyTheme()
        self._streamMessageId = message.id
        self._scrollToBottom()

    def setLoading(self, isLoad: bool, avatar: str = "") -> None:
        """显示或隐藏助手生成占位。"""
        del avatar
        if isLoad and self._loadingRow is None:
            loadingMessage = ChatMessage(
                id=-1,
                message="正在生成回答…",
                role=ChatRole.ASSISTANT.value,
            )
            row, bubble = self._createRow(loadingMessage)
            bubble._copyButton.hide()
            bubble._deleteButton.hide()
            self._contentLayout.insertWidget(self._contentLayout.count() - 1, row)
            self._loadingRow = row
            self._scrollToBottom()
        elif not isLoad and self._loadingRow is not None:
            self._contentLayout.removeWidget(self._loadingRow)
            self._loadingRow.deleteLater()
            self._loadingRow = None

    def stopStreamMessage(self) -> None:
        self._streamMessageId = None
        self.setLoading(False)

    def setHistory(self, history: list[ChatMessage | FileChatMessage]) -> None:
        self.clearHistory()
        for message in history:
            self.addMessage(message)

    def clearHistory(self) -> None:
        """清空全部消息和流式状态。"""
        self.setLoading(False)
        for row in list(self._rows.values()):
            self._contentLayout.removeWidget(row)
            row.deleteLater()
        self._messages.clear()
        self._rows.clear()
        self._streamMessageId = None

    def copyMessage(self, messageId) -> None:
        bubble = self._messages.get(int(messageId))
        if bubble is not None:
            bubble._copy()

    def setAvatarRadius(self, radius: int) -> None:
        """保留旧接口；项目内置视图不显示远程头像。"""
        del radius

    def page(self) -> "PrismaticaChatWidget":
        """兼容旧聊天页背景设置调用。"""
        return self

    def setBackgroundColor(self, color) -> None:
        self._backgroundColor = QColor(color)
        self._applyTheme()

    def _addBubble(self, message: ChatMessage | FileChatMessage) -> None:
        row, bubble = self._createRow(message)
        self._messages[message.id] = bubble
        self._rows[message.id] = row
        self._contentLayout.insertWidget(self._contentLayout.count() - 1, row)

    def _createRow(
        self,
        message: ChatMessage | FileChatMessage,
    ) -> tuple[QWidget, _MessageBubble]:
        row = QWidget(self._content)
        rowLayout = QHBoxLayout(row)
        rowLayout.setContentsMargins(0, 0, 0, 0)
        rowLayout.setSpacing(0)
        bubble = _MessageBubble(message, row)
        bubble.copyRequested.connect(lambda _text: self.messageCopied.emit())
        bubble.deleteRequested.connect(self.messageDeleteRequest.emit)
        bubble.applyTheme()
        if message.role == ChatRole.USER.value:
            rowLayout.addStretch(1)
            rowLayout.addWidget(bubble)
        else:
            rowLayout.addWidget(bubble)
            rowLayout.addStretch(1)
        return row, bubble

    def _scrollToBottom(self) -> None:
        QTimer.singleShot(
            0,
            lambda: self.verticalScrollBar().setValue(
                self.verticalScrollBar().maximum()
            ),
        )

    def _applyTheme(self, *_args) -> None:
        palette = shellPalette()
        background = (
            self._backgroundColor
            if self._backgroundColor.isValid()
            else palette.content
        )
        self.setStyleSheet(
            f"""
            QScrollArea {{
                background-color: {background.name()};
                border: none;
            }}
            QWidget#chatHistoryContent {{
                background-color: {background.name()};
            }}
            """
        )
        for bubble in self._messages.values():
            bubble.applyTheme()


__all__ = [
    "ChatMessage",
    "ChatRole",
    "FileChatMessage",
    "PrismaticaChatWidget",
    "SimpleChatTextEdit",
]
