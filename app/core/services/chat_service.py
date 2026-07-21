# coding: utf-8
"""
AI 聊天服务

参考 qfluentwidgetspro/chat demo 实现:
- LLMThread: 后台调用 OpenAI 兼容 API (默认 DeepSeek) 进行流式对话
- ChatService: 顶层服务,持有 LLMThread 并对外暴露业务接口

设计要点:
- 遵循项目分层(view → service → api):本模块属于 service 层,
  视图层 (app.view.chat_interface) 仅通过本服务发起对话。
- 流式 token 通过 signal 推回 UI,与 ChatWidget.setStreamMessage 协作。
- 模型 ID 由用户在「设置 → AI 聊天」自由填写,默认 deepseek-chat。
- 历史消息窗口由 cfg.AiMaxHistory 控制,默认保留 10 轮。
- 错误处理:任何 LLM 异常都向 UI emit failed 信号,UI 给出友好提示。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QThread, Signal

from app.core.utils import cfg, logger, qconfig


class LLMThread(QThread):
    """后台调用 OpenAI 兼容 API 的流式对话线程。

    Signals:
        textReceived(str, int): 每个分片文本 + 该分片的 token 数(由 tiktoken 估算)
        chatFinished(): 流式响应正常结束
            注意:不能命名为 finished,QThread 自带同名信号会在 run 退出时
            自动 emit,导致下游信号被触发两次。
        failed(str): 调用过程中出现异常,参数为错误描述
    """

    textReceived = Signal(str, int)
    chatFinished = Signal()
    failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent=parent)

        # ---- 单次 ask 调用参数(由 ask() 重置)----
        self._message: str = ""
        self._fileText: str = ""
        self._prompt: str = ""
        self._model: str = "deepseek-chat"
        self._responseText: str = ""

        # ---- 长寿命状态(随对话进行累积)----
        self._tokenUsage: int = 0
        self._history: List[dict] = []
        self._maxHistory: int = qconfig.get(cfg.AiMaxHistory) or 10

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def ask(
        self,
        message: str,
        prompt: Optional[str] = None,
        fileText: str = "",
    ) -> None:
        """发起一次对话。

        Args:
            message: 用户消息正文
            prompt: 系统提示词;为空则使用 cfg.AiSystemPrompt
            fileText: 附加文件内容(如用户上传的文件内容);为空则忽略
        """
        if self.isRunning() or not message:
            return

        apiKey = qconfig.get(cfg.AiApiKey) or ""
        if not apiKey:
            self.failed.emit("未配置 API Key,请在「设置 → AI 聊天」中填写。")
            return

        self._message = message
        self._fileText = fileText or ""
        self._prompt = (
            prompt
            or self._loadSystemPrompt()
            or "你是一个AI助手,请用中文回答用户的问题。"
        )
        self._responseText = ""

        # 模型选择:始终使用用户在设置页配置的 Chat 模型(支持任意自定义 ID)
        self._model = qconfig.get(cfg.AiModelChat) or "deepseek-chat"

        logger.info(
            f"[LLMThread] 发起对话: model={self._model}, "
            f"history_len={len(self._history)}"
        )
        self.start()

    def clearHistory(self) -> None:
        """清空多轮对话历史"""
        self._history = []
        logger.info("[LLMThread] 已清空对话历史")

    @staticmethod
    def _loadSystemPrompt() -> str:
        """读取用户在「设置 → AI 聊天」中指定的系统提示词文件。

        兼容策略:
            - 字段为空:返回空字符串,由调用方 fallback 到默认提示词
            - 字段是已有文件路径:读取文件内容(utf-8,忽略错误)
            - 字段是早期版本遗留的多行文本(无换行视为单行则当成正文直接使用):
              检测不到路径分隔符或后缀名,且包含 \\n 时,直接返回该文本
        """
        raw = (qconfig.get(cfg.AiSystemPrompt) or "").strip()
        if not raw:
            return ""
        # 早期版本存的是正文(可能含换行),不当作路径
        if "\n" in raw:
            return raw
        path = Path(raw)
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError as e:
                logger.warning(f"[LLMThread] 读取提示词文件失败: {e}")
                return ""
        # 既不是文件也不是多行文本,视为旧版遗留的纯文本提示词
        return raw

    @property
    def responseText(self) -> str:
        return self._responseText

    @property
    def tokenUsage(self) -> int:
        return self._tokenUsage

    # ------------------------------------------------------------------
    # QThread 主体
    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: D401
        try:
            # 延迟导入 openai / tiktoken:避免未配置环境时拖慢启动
            from openai import OpenAI
            import tiktoken

            apiKey = qconfig.get(cfg.AiApiKey) or ""
            baseUrl = qconfig.get(cfg.AiBaseUrl) or "https://api.deepseek.com"

            client = OpenAI(api_key=apiKey, base_url=baseUrl)
            encoder = tiktoken.get_encoding("cl100k_base")

            messages = [
                {"role": "system", "content": self._prompt},
                *self._history,
                {"role": "user", "content": self._message + self._fileText},
            ]

            response = client.chat.completions.create(
                model=self._model,
                messages=messages,
                stream=True,
            )

            for chunk in response:
                if not chunk.choices:
                    continue
                data = chunk.choices[0].delta
                text = data.content
                if not text:
                    continue

                self._responseText += text
                tokenUsage = len(encoder.encode(text))
                self._tokenUsage += tokenUsage
                self.textReceived.emit(text, tokenUsage)

            # 保存历史(剪裁到 maxHistory * 2 条)
            self._history.append({"role": "user", "content": self._message})
            self._history.append({"role": "assistant", "content": self._responseText})
            if len(self._history) > self._maxHistory * 2:
                self._history = self._history[
                    len(self._history) - self._maxHistory * 2 :
                ]

            self.chatFinished.emit()

        except Exception as e:
            import traceback

            errorMsg = str(e) or type(e).__name__
            logger.error(f"[LLMThread] 调用失败: {traceback.format_exc()}")
            # 不覆盖用户已看到的内容,只追加错误标记
            self._responseText += "\n\n[请求发生错误,请稍后再试]"
            self.failed.emit(errorMsg)


class ChatService(QObject):
    """聊天服务的对外门面

    在 view 层只需:
        chatService = ChatService()
        chatService.textReceived.connect(...)
        chatService.ask("你好")
        chatService.clearHistory()
    """

    textReceived = Signal(str, int)
    streamFinished = Signal()
    failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent=parent)
        self._thread = LLMThread(self)
        self._thread.textReceived.connect(self.textReceived)
        self._thread.chatFinished.connect(self._onThreadFinished)
        self._thread.failed.connect(self.failed)

    # 代理 view 关心的几个状态
    @property
    def responseText(self) -> str:
        return self._thread.responseText

    @property
    def tokenUsage(self) -> int:
        return self._thread.tokenUsage

    @property
    def isRunning(self) -> bool:
        return self._thread.isRunning()

    def ask(
        self,
        message: str,
        prompt: Optional[str] = None,
        fileText: str = "",
    ) -> None:
        self._thread.ask(
            message=message,
            prompt=prompt,
            fileText=fileText,
        )

    def clearHistory(self) -> None:
        self._thread.clearHistory()

    def stop(self) -> None:
        """请求中断当前对话(UI 主动调用,如关闭页签)"""
        if self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.wait(2000)
            logger.info("[ChatService] 已请求中断 LLMThread")

    def _onThreadFinished(self) -> None:
        self.streamFinished.emit()
