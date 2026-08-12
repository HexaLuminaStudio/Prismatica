"""平台 AI 客户端超时与错误提示回归测试。"""
from __future__ import annotations

from app.core.services import chat_service
from app.core.services.cloud_api import CloudApiError


class _SuccessfulCloudApi:

    def __init__(self) -> None:
        self.timeout = None
        self.stream = _EventStream(
            [
                {
                    "event": "progress",
                    "data": {
                        "stage": "generating",
                        "percent": 20,
                        "message": "AI 正在生成解读",
                    },
                },
                {"event": "delta", "data": {"text": "完"}},
                {"event": "delta", "data": {"text": "成"}},
                {
                    "event": "completed",
                    "data": {
                        "message": "完成",
                        "model": "test-model",
                        "usage": {"totalTokens": 2},
                        "billing": {"balanceAfter": 10},
                    },
                },
            ]
        )

    def openEventStream(self, _path, *, body, idempotencyKey, timeout):
        del body, idempotencyKey
        self.timeout = timeout
        return self.stream


class _EventStream:

    def __init__(self, events) -> None:
        self.events = events
        self.closed = False

    def iterEvents(self):
        yield from self.events

    def close(self) -> None:
        self.closed = True


class _TimeoutCloudApi:

    def openEventStream(self, _path, *, body, idempotencyKey, timeout):
        del body, idempotencyKey, timeout
        raise CloudApiError(
            "NETWORK_ERROR",
            "网络异常: Read timed out. (read timeout=630.0)",
        )


def _worker() -> chat_service.LLMThread:
    worker = chat_service.LLMThread()
    worker._message = "生成报告"
    worker._prompt = "请用中文回答"
    worker._featureCode = "ai_report"
    return worker


def testLlmThread_WaitsLongerThanServerMaximum(monkeypatch) -> None:
    cloudApi = _SuccessfulCloudApi()
    monkeypatch.setattr(chat_service, "getCloudApi", lambda: cloudApi)
    worker = _worker()
    deltas = []
    progress = []
    worker.textReceived.connect(lambda text, _tokens: deltas.append(text))
    worker.progressChanged.connect(
        lambda stage, percent, message: progress.append((stage, percent, message))
    )

    worker.run()

    assert cloudApi.timeout == chat_service.AI_CHAT_REQUEST_TIMEOUT
    assert cloudApi.timeout[1] > 600
    assert deltas == ["完", "成"]
    assert progress == [("generating", 20, "AI 正在生成解读")]
    assert worker.responseText == "完成"
    assert worker.tokenUsage == 2
    assert cloudApi.stream.closed is True


def testLlmThread_EmitsFriendlyMessageForReadTimeout(monkeypatch) -> None:
    monkeypatch.setattr(chat_service, "getCloudApi", lambda: _TimeoutCloudApi())
    worker = _worker()
    failures = []
    worker.failed.connect(failures.append)

    worker.run()

    assert failures == ["AI 生成等待超时，请稍后重试。"]
    assert "Read timed out" not in worker.responseText
