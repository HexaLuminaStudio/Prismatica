# coding: utf-8
"""Prismatica 项目内置聊天组件回归测试。"""
from pathlib import Path

from PySide6.QtCore import Qt

from app.view.widgets.prismatica_chat import (
    ChatMessage,
    ChatRole,
    FileChatMessage,
    PrismaticaChatWidget,
    SimpleChatTextEdit,
)


def testChatWidgetAddsStreamsAndFinalizesMessages(qtbot) -> None:
    widget = PrismaticaChatWidget()
    qtbot.addWidget(widget)
    userMessage = ChatMessage(0, "你好", ChatRole.USER.value)
    streamMessage = ChatMessage(1, "正在", ChatRole.ASSISTANT.value)

    widget.addMessage(userMessage)
    widget.setLoading(True)
    widget.setStreamMessage(streamMessage)
    widget.setStreamMessage(
        ChatMessage(1, "正在回答", ChatRole.ASSISTANT.value)
    )
    widget.addMessage(ChatMessage(1, "回答完成", ChatRole.ASSISTANT.value))

    assert sorted(widget._messages) == [0, 1]
    assert widget._loadingRow is None
    assert widget._streamMessageId is None
    assert widget._messages[1].message.message == "回答完成"


def testChatWidgetCopiesDeletesAndClearsMessages(qtbot) -> None:
    widget = PrismaticaChatWidget()
    qtbot.addWidget(widget)
    copied = []
    deleteRequests = []
    widget.messageCopied.connect(lambda: copied.append(True))
    widget.messageDeleteRequest.connect(deleteRequests.append)
    widget.addMessage(
        FileChatMessage(3, "notes.txt", "2 KB", ChatRole.USER.value)
    )

    widget.copyMessage(3)
    widget._messages[3]._deleteButton.click()

    assert copied == [True]
    assert deleteRequests == [3]

    widget.deleteMessage(3)
    assert widget._messages == {}
    widget.clearHistory()
    assert widget._rows == {}


def testSimpleChatTextEditUsesEnterToSendAndShiftEnterToWrap(qtbot) -> None:
    edit = SimpleChatTextEdit()
    qtbot.addWidget(edit)
    sends = []
    edit.returnPressed.connect(lambda: sends.append(True))

    qtbot.keyPress(edit, Qt.Key.Key_Return)
    qtbot.keyPress(
        edit,
        Qt.Key.Key_Return,
        modifier=Qt.KeyboardModifier.ShiftModifier,
    )

    assert sends == [True]
    assert edit.toPlainText() == "\n"


def testProChatImportsHaveBeenRemoved() -> None:
    projectRoot = Path(__file__).resolve().parents[1]
    sourceFiles = [
        projectRoot / "app/view/chat_interface.py",
        projectRoot / "app/core/services/chat_service.py",
    ]

    for sourceFile in sourceFiles:
        source = sourceFile.read_text(encoding="utf-8")
        assert "qfluentwidgetspro" not in source
