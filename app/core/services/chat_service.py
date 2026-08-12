# coding: utf-8
"""
平台 AI 聊天服务

参考 qfluentwidgetspro/chat demo 实现:
- LLMThread: 后台调用 PrismaticaAPI，由服务端持有供应商 API Key 并按真实 Token 结算
- ChatService: 顶层服务,持有 LLMThread 并对外暴露业务接口

设计要点:
- 遵循项目分层(view → service → api):本模块属于 service 层,
  视图层 (app.view.chat_interface) 仅通过本服务发起对话。
- 服务端完成请求后通过 signal 推回文本与供应商真实 Token 用量。
- 模型 ID 与供应商 API Key 只由 PrismaticaAPI 的环境变量配置。
- 历史消息窗口由 cfg.AiMaxHistory 控制,默认保留 10 轮。
- 错误处理:任何 LLM 异常都向 UI emit failed 信号,UI 给出友好提示。
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, List, Optional

from PySide6.QtCore import QObject, QThread, Signal

from app.core.utils import cfg, logger, qconfig, signalBus

from .cloud_api import CloudApiError, CloudEventStream, getCloudApi


# 后端 AI_READ_TIMEOUT_SEC 允许的最大值是 600 秒。客户端必须比后端多留
# 一段响应封装与网络传输余量，避免慢模型仍在生成时由客户端先行断开。
AI_CHAT_CONNECT_TIMEOUT_SECONDS = 10.0
AI_CHAT_READ_TIMEOUT_SECONDS = 630.0
AI_CHAT_REQUEST_TIMEOUT = (
    AI_CHAT_CONNECT_TIMEOUT_SECONDS,
    AI_CHAT_READ_TIMEOUT_SECONDS,
)


class LLMThread(QThread):
    """后台调用 PrismaticaAPI 平台 AI 端点的对话线程。

    Signals:
        textReceived(str, int): 完整响应文本 + 供应商返回的真实 Token 总数
        chatFinished(): 响应正常结束
            注意:不能命名为 finished,QThread 自带同名信号会在 run 退出时
            自动 emit,导致下游信号被触发两次。
        failed(str): 调用过程中出现异常,参数为错误描述
    """

    textReceived = Signal(str, int)
    progressChanged = Signal(str, int, str)
    chatFinished = Signal()
    failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent=parent)

        # ---- 单次 ask 调用参数(由 ask() 重置)----
        self._message: str = ""
        self._fileText: str = ""
        self._prompt: str = ""
        self._model: str = "平台模型"
        self._featureCode: str = "ai_chat"
        self._responseText: str = ""

        # ---- 长寿命状态(随对话进行累积)----
        self._tokenUsage: int = 0
        self._history: List[dict] = []
        self._maxHistory: int = qconfig.get(cfg.AiMaxHistory) or 10
        self._activeStream: CloudEventStream | None = None
        self._streamLock = threading.Lock()

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def ask(
        self,
        message: str,
        prompt: Optional[str] = None,
        fileText: str = "",
        featureCode: str = "ai_chat",
    ) -> None:
        """发起一次对话。

        Args:
            message: 用户消息正文
            prompt: 系统提示词;为空则使用 cfg.AiSystemPrompt
            fileText: 附加文件内容(如用户上传的文件内容);为空则忽略
        """
        if self.isRunning():
            logger.warning("[LLMThread] 已有对话进行中,忽略新请求")
            return
        if not message:
            logger.debug("[LLMThread] 消息为空,忽略请求")
            return

        if not getCloudApi().isLoggedIn():
            self.failed.emit("请先登录 Prismatica 账号后再使用平台 AI。")
            return

        self._message = message
        self._fileText = fileText or ""
        self._prompt = (
            prompt
            or self._loadSystemPrompt()
            or "你是一个AI助手,请用中文回答用户的问题。"
        )
        self._responseText = ""
        self._featureCode = featureCode

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
            messages = [
                {"role": "system", "content": self._prompt},
                *self._history,
                {"role": "user", "content": self._message + self._fileText},
            ]
            requestBody = {
                "featureCode": self._featureCode,
                "messages": messages,
                "temperature": 0.3,
            }
            idempotencyKey = str(uuid.uuid4())
            try:
                eventStream = getCloudApi().openEventStream(
                    "/v1/ai/chat/stream",
                    body=requestBody,
                    idempotencyKey=idempotencyKey,
                    timeout=AI_CHAT_REQUEST_TIMEOUT,
                )
            except CloudApiError as error:
                if error.httpStatus not in (404, 405):
                    raise
                self.progressChanged.emit(
                    "compatibility",
                    15,
                    "服务器暂不支持流式返回，正在使用兼容模式",
                )
                response = getCloudApi().post(
                    "/v1/ai/chat",
                    body=requestBody,
                    idempotencyKey=idempotencyKey,
                    timeout=AI_CHAT_REQUEST_TIMEOUT,
                )
                self._completeResponse(response, emitText=True)
                return

            self._setActiveStream(eventStream)
            isCompleted = False
            try:
                for item in eventStream.iterEvents():
                    if self.isInterruptionRequested():
                        return
                    eventName = str(item.get("event", ""))
                    data = item.get("data") or {}
                    if eventName == "progress":
                        self.progressChanged.emit(
                            str(data.get("stage", "")),
                            int(data.get("percent", 0) or 0),
                            str(data.get("message", "")),
                        )
                    elif eventName == "delta":
                        text = str(data.get("text", ""))
                        if text:
                            self._responseText += text
                            self.textReceived.emit(text, 0)
                    elif eventName == "completed":
                        self._completeResponse(data, emitText=False)
                        isCompleted = True
                        break
                    elif eventName == "error":
                        raise CloudApiError(
                            str(data.get("code", "INTERNAL_ERROR")),
                            str(data.get("message", "AI 服务暂时不可用")),
                            details=data.get("details") or {},
                        )
            finally:
                eventStream.close()
            if not isCompleted and not self.isInterruptionRequested():
                raise CloudApiError("BAD_RESPONSE", "AI 流式响应提前结束")

        except CloudApiError as e:
            if e.code == "NETWORK_ERROR" and "Read timed out" in e.message:
                errorMsg = "AI 生成等待超时，请稍后重试。"
            else:
                errorMsg = e.message or str(e)
            logger.warning(
                f"[LLMThread] 云端调用失败, model={self._model}, "
                f"historyLen={len(self._history)}, code={e.code}: {e.message}"
            )
            self._responseText += f"\n\n[请求发生错误：{errorMsg}]"
            self.failed.emit(errorMsg)
        except Exception as e:
            errorMsg = str(e) or type(e).__name__
            logger.exception(
                f"[LLMThread] 调用失败, model={self._model}, "
                f"historyLen={len(self._history)}: {e}"
            )
            # 不覆盖用户已看到的内容,只追加错误标记
            self._responseText += "\n\n[请求发生错误,请稍后再试]"
            self.failed.emit(errorMsg)
        finally:
            self._setActiveStream(None)

    def _completeResponse(self, response: Any, *, emitText: bool) -> None:
        text = str((response or {}).get("message", ""))
        if not text:
            raise CloudApiError("BAD_RESPONSE", "平台 AI 未返回有效文本")
        usage = (response or {}).get("usage") or {}
        billing = (response or {}).get("billing") or {}
        self._model = str((response or {}).get("model", "平台模型"))
        if emitText:
            self._responseText = text
            self.textReceived.emit(text, int(usage.get("totalTokens", 0) or 0))
        elif not self._responseText:
            self._responseText = text
            self.textReceived.emit(text, 0)
        self._tokenUsage = int(usage.get("totalTokens", 0) or 0)
        try:
            signalBus.balanceChanged.emit(int(billing.get("balanceAfter", 0) or 0))
        except Exception:
            pass
        self._history.append({"role": "user", "content": self._message})
        self._history.append({"role": "assistant", "content": self._responseText})
        if len(self._history) > self._maxHistory * 2:
            self._history = self._history[len(self._history) - self._maxHistory * 2 :]
        logger.info(
            f"[LLMThread] 对话完成, model={self._model}, "
            f"responseChars={len(self._responseText)}, tokens={self._tokenUsage}"
        )
        self.chatFinished.emit()

    def _setActiveStream(self, eventStream: CloudEventStream | None) -> None:
        with self._streamLock:
            self._activeStream = eventStream

    def cancelActiveRequest(self) -> None:
        with self._streamLock:
            eventStream = self._activeStream
        if eventStream is not None:
            eventStream.close()


class ChatService(QObject):
    """聊天服务的对外门面

    在 view 层只需:
        chatService = ChatService()
        chatService.textReceived.connect(...)
        chatService.ask("你好")
        chatService.clearHistory()
    """

    textReceived = Signal(str, int)
    progressChanged = Signal(str, int, str)
    streamFinished = Signal()
    failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent=parent)
        self._thread = LLMThread(self)
        self._thread.textReceived.connect(self.textReceived)
        self._thread.progressChanged.connect(self.progressChanged)
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
        featureCode: str = "ai_chat",
    ) -> None:
        self._thread.ask(
            message=message,
            prompt=prompt,
            fileText=fileText,
            featureCode=featureCode,
        )

    def clearHistory(self) -> None:
        self._thread.clearHistory()

    def stop(self) -> None:
        """请求中断当前对话(UI 主动调用,如关闭页签)

        流程:
        1. requestInterruption(): 触发 LLMThread.run() 内的循环检查点
        2. wait(3000): 等待线程退出(流式 chunk 之间的间隔通常 < 1s,3s 足够)
        3. 仍不退则 terminate() 强制结束(罕见情况:网络阻塞/SDK 卡死)
        """
        if self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.cancelActiveRequest()
            if not self._thread.wait(3000):
                logger.warning("[ChatService] LLMThread 在 3s 内未退出,强制 terminate")
                self._thread.terminate()
                self._thread.wait(1000)
            logger.info("[ChatService] 已请求中断 LLMThread")

    def _onThreadFinished(self) -> None:
        self.streamFinished.emit()


__all__ = [
    "AI_CHAT_CONNECT_TIMEOUT_SECONDS",
    "AI_CHAT_READ_TIMEOUT_SECONDS",
    "AI_CHAT_REQUEST_TIMEOUT",
    "ChatService",
    "CloudEventStream",
    "LLMThread",
]
