# coding: utf-8
"""
AI 聊天子界面

参照 test/demo.py 实现 qfluentwidgetspro.ChatWidget 的集成,
适配 Prismatica 项目规范:
    - 视图层通过 ChatService 调用 LLM,不分直接依赖 openai
    - 顶部为 HeaderWidget(机器人图标 + 标题 + 副标题 + 保存按钮)
    - 中间为 ChatWidget(渲染用户/助手气泡)
    - 底部为工具栏 + 输入框 + 发送按钮 + 状态条
    - 状态条显示当前 token 用量与生成提示
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    AvatarWidget,
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    HorizontalSeparator,
    ImageLabel,
    IndeterminateProgressRing,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PrimaryPushButton,
    StrongBodyLabel,
    ToolTipFilter,
    TransparentToolButton,
    VerticalSeparator,
    themeColor,
)
from qfluentwidgetspro.chat import (
    ChatMessage,
    ChatRole,
    ChatWidget,
    FileChatMessage,
    SimpleChatTextEdit,
)

from app.core.services import ChatService
from app.core.utils import logger, qconfig
from app.view.widgets.prismatica_theme import pageBackgroundColor


# 角色头像资源(气泡用 URL 字符串,不是 QIcon)
_ASSISTANT_AVATAR_URL = "https://avatars.githubusercontent.com/u/101397164?v=4"
_USER_AVATAR_URL = "https://avatars.githubusercontent.com/u/101397164?v=4"


class _ChatHeader(QWidget):
    """聊天页顶部:机器人头像 + 标题 + 副标题"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.avatar = AvatarWidget(":app/icons/Robot.svg")
        self.avatar.setRadius(16)
        self.titleLabel = BodyLabel("棱溯-Prismatica 语料软件AI助手")
        self.contentLabel = CaptionLabel(self._modelName())
        self.contentLabel.setTextColor(QColor(114, 114, 114), QColor(196, 196, 196))

        self.hBoxLayout = QHBoxLayout(self)
        self.vBoxLayout = QVBoxLayout()
        self.hBoxLayout.addWidget(self.avatar)
        self.hBoxLayout.addLayout(self.vBoxLayout)
        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.setContentsMargins(20, 10, 15, 5)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.contentLabel)
        self.vBoxLayout.setSpacing(0)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)

    def refreshModel(self) -> None:
        self.contentLabel.setText(self._modelName())

    @staticmethod
    def _modelName() -> str:
        return "Prismatica 平台模型 · 按真实 Token 计费"


class ChatInterface(QWidget):
    """AI 聊天子页面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("chatInterface")

        # ---- 状态 ----
        self._messageId = 0
        self._fileText: str = ""

        # ---- 服务层 ----
        self.chatService = ChatService(self)
        self.chatService.textReceived.connect(self._onTextReceived)
        self.chatService.streamFinished.connect(self._onStreamFinished)
        self.chatService.failed.connect(self._onFailed)

        # ---- UI ----
        self.headerWidget = _ChatHeader(self)
        self.chatPage = ChatWidget(self)
        self.messageTextEdit = SimpleChatTextEdit(self)

        self.progressRing = IndeterminateProgressRing()
        self.waitLabel = CaptionLabel("生成中...")
        self.tokenLabel = CaptionLabel("Tokens")
        self.tokenValueLabel = CaptionLabel("0")
        self.shortcutLabel1 = CaptionLabel("Shift + Enter  换行")
        self.shortcutLabel2 = CaptionLabel("Enter  发送")
        self.sendButton = PrimaryPushButton(FluentIcon.SEND, "发送", self)

        self.fileButton = TransparentToolButton(
            QIcon(":/app/icons/Paperclip.svg"), self
        )
        self.eraseButton = TransparentToolButton(QIcon(":/app/icons/Erase.svg"), self)

        self._initWidget()
        self._initLayout()
        self._connectSignals()

    # ------------------------------------------------------------------
    # UI 构造
    # ------------------------------------------------------------------
    def _initWidget(self) -> None:
        self.setStyleSheet("#chatInterface{background:transparent;}")
        self.messageTextEdit.setFocus()
        self.messageTextEdit.setPlaceholderText("问点问题吧~")

        # 主题感知背景色
        self.updateBackgroundColor()
        qconfig.themeChanged.connect(self.updateBackgroundColor)

        # 占位 / 配色
        self.tokenValueLabel.setTextColor(themeColor(), themeColor())

        # 工具按钮 tooltip
        for btn in (self.eraseButton, self.fileButton):
            btn.installEventFilter(ToolTipFilter(btn))
        self.eraseButton.setToolTip("清空聊天记录")
        self.fileButton.setToolTip("添加附件(将文件内容作为上下文)")

    def _initLayout(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(15, 0, 15, 5)
        outer.setSpacing(0)

        # 顶部 header
        outer.addWidget(self.headerWidget)
        outer.addWidget(HorizontalSeparator())

        # 中间聊天页(占主要区域)
        outer.addWidget(self.chatPage, 1)
        outer.addWidget(HorizontalSeparator())

        # 工具条
        toolbarLayout = QHBoxLayout()
        toolbarLayout.setContentsMargins(15, 5, 15, 5)
        toolbarLayout.addWidget(self.fileButton, 0, Qt.AlignmentFlag.AlignLeft)
        toolbarLayout.addStretch(1)
        toolbarLayout.addWidget(self.eraseButton, 0, Qt.AlignmentFlag.AlignRight)
        outer.addLayout(toolbarLayout)

        # 输入区
        self.messageTextEdit.setFixedHeight(100)
        outer.addWidget(self.messageTextEdit)
        outer.addWidget(HorizontalSeparator())

        # 底部状态 / 发送
        bottomLayout = QHBoxLayout()
        bottomLayout.setSpacing(10)
        bottomLayout.setContentsMargins(15, 12, 15, 4)

        # 主题感知文本色
        waitLight, waitDark = QColor(96, 96, 96), QColor(255, 255, 255, 216)
        shortcutLight, shortcutDark = QColor(0, 0, 0, 96), QColor(255, 255, 255, 216)
        self.waitLabel.setTextColor(waitLight, waitDark)
        self.shortcutLabel1.setTextColor(shortcutLight, shortcutDark)
        self.shortcutLabel2.setTextColor(shortcutLight, shortcutDark)

        self.progressRing.setFixedSize(20, 20)
        self.progressRing.setStrokeWidth(3)

        loadingLayout = QHBoxLayout()
        loadingLayout.setSpacing(5)
        loadingLayout.addWidget(self.progressRing)
        loadingLayout.addWidget(self.waitLabel)
        bottomLayout.addLayout(loadingLayout)

        tokenLayout = QHBoxLayout()
        tokenLayout.setSpacing(2)
        tokenLayout.addWidget(self.tokenLabel)
        tokenLayout.addWidget(self.tokenValueLabel)
        bottomLayout.addLayout(tokenLayout)
        bottomLayout.addStretch(1)

        shortcutLayout = QHBoxLayout()
        shortcutLayout.setSpacing(8)
        separator = VerticalSeparator()
        separator.setFixedHeight(12)
        shortcutLayout.addWidget(self.shortcutLabel1)
        shortcutLayout.addWidget(separator)
        shortcutLayout.addWidget(self.shortcutLabel2)
        bottomLayout.addLayout(shortcutLayout)

        self.sendButton.setFixedWidth(120)
        bottomLayout.addWidget(self.sendButton)
        outer.addLayout(bottomLayout)

        self._setLoading(False)

    def _connectSignals(self) -> None:
        self.messageTextEdit.returnPressed.connect(self._sendMessage)
        self.sendButton.clicked.connect(self._sendMessage)
        self.eraseButton.clicked.connect(self._newChat)
        self.fileButton.clicked.connect(self._addFile)
        self.chatPage.messageCopied.connect(self._onMessageCopied)
        self.chatPage.messageDeleteRequest.connect(self._deleteMessage)

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------
    def _sendMessage(self) -> None:
        text = self.messageTextEdit.toPlainText().strip()
        if not text or self.chatService.isRunning:
            return

        # 渲染用户气泡
        msg = ChatMessage(
            id=self._messageId,
            message=text,
            role=ChatRole.USER.value,
            avatar=_USER_AVATAR_URL,
        )
        self._messageId += 1
        self.messageTextEdit.clear()
        self.chatPage.addMessage(msg)
        self._setLoading(True)

        # 流式占位
        self.chatPage.setLoading(True, avatar=_ASSISTANT_AVATAR_URL)
        self._setTokenUsage(0)

        # 真正发起调用
        self.chatService.ask(
            message=text,
            fileText=self._fileText,
        )
        self._fileText = ""

    def _onTextReceived(self, text: str, tokenUsage: int) -> None:
        # 增量更新当前助手气泡
        if self._messageId == 0:
            return
        msg = ChatMessage(
            id=self._messageId,
            message=self.chatService.responseText,
            role=ChatRole.ASSISTANT.value,
            avatar=_ASSISTANT_AVATAR_URL,
        )
        self.chatPage.setStreamMessage(msg)
        # 增量累计 token
        current = int(self.tokenValueLabel.text() or "0")
        self._setTokenUsage(current + tokenUsage)

    def _onStreamFinished(self) -> None:

        self.chatPage.setLoading(False)
        self._setLoading(False)
        finalMsg = ChatMessage(
            id=self._messageId,
            message=self.chatService.responseText,
            role=ChatRole.ASSISTANT.value,
            avatar=_ASSISTANT_AVATAR_URL,
        )
        self._messageId += 1
        self.chatPage.addMessage(finalMsg)

    def _onFailed(self, errMsg: str) -> None:
        logger.error(f"[ChatInterface] LLM 调用失败: {errMsg}")
        # 关闭 loading 占位,不要调用 stopStreamMessage(),否则会与 addMessage 重复
        self.chatPage.setLoading(False)
        self._setLoading(False)
        InfoBar.error(
            title="请求失败",
            content=errMsg[:120],
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            duration=4000,
            position=InfoBarPosition.TOP,
            parent=self.window(),
        )

    def _newChat(self) -> None:
        # 二次确认,避免误操作
        w = MessageBox("清空聊天", "确定清空当前对话历史吗?", self.window())
        if not w.exec():
            return
        self.chatPage.clearHistory()
        self.chatService.clearHistory()
        self._setTokenUsage(0)
        InfoBar.success(
            title="",
            content="已清空对话记录",
            position=InfoBarPosition.TOP,
            parent=self.window(),
        )

    def _addFile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择附件",
            "",
            "文本文件 (*.txt *.md *.json *.csv *.log);;所有文件 (*)",
        )
        if not path:
            return
        file = Path(path)
        try:
            self._fileText = file.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.error(f"[ChatInterface] 读取附件失败: {e}")
            InfoBar.error(
                title="读取失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=3000,
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return
        # 渲染文件气泡,让用户能在聊天区看到已选择的附件
        sizeKb = max(1, file.stat().st_size // 1024)
        fileMsg = FileChatMessage(
            id=self._messageId,
            role=ChatRole.USER.value,
            name=file.name,
            content=f"{sizeKb} KB",
            avatar=_USER_AVATAR_URL,
        )
        self._messageId += 1
        self.chatPage.addMessage(fileMsg)
        InfoBar.success(
            title="",
            content=f"已附加:{file.name}",
            position=InfoBarPosition.TOP,
            parent=self.window(),
        )

    def _onMessageCopied(self) -> None:
        InfoBar.success(
            title="",
            content="已复制到剪贴板",
            position=InfoBarPosition.TOP,
            parent=self.window(),
        )

    def _deleteMessage(self, messageId) -> None:
        """删除指定气泡(用户/助手气泡均可),二次确认避免误操作"""
        w = MessageBox(
            "删除聊天消息",
            "确定删除此消息吗?删除后该消息将从界面中移除。",
            self.window(),
        )
        if w.exec():
            self.chatPage.deleteMessage(messageId)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _setLoading(self, isLoad: bool) -> None:
        self.progressRing.setVisible(isLoad)
        self.waitLabel.setVisible(isLoad)
        self.tokenLabel.setHidden(isLoad)
        self.tokenValueLabel.setHidden(isLoad)

    def _setTokenUsage(self, value: int) -> None:
        self.tokenValueLabel.setText(str(value))

    def updateBackgroundColor(self) -> None:
        """主题切换时刷新聊天页背景色"""
        try:
            self.chatPage.page().setBackgroundColor(pageBackgroundColor())
        except Exception as e:
            logger.warning(f"[ChatInterface] 设置背景色失败: {e}")

    def showEvent(self, event) -> None:
        """每次进入页面时刷新模型名(API Key 可能刚改过)"""
        super().showEvent(event)
        try:
            self.headerWidget.refreshModel()
        except Exception:
            pass
