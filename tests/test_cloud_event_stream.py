"""桌面端 SSE 解析与主动关闭测试。"""
from __future__ import annotations

from app.core.services.cloud_api import CloudEventStream


class _Response:

    def __init__(self, lines) -> None:
        self.lines = lines
        self.encoding = ""
        self.closed = False

    def iter_lines(self, decode_unicode: bool = False):
        assert decode_unicode is True
        yield from self.lines

    def close(self) -> None:
        self.closed = True


def testCloudEventStream_ParsesNamedUtf8EventsAndKeepAlive() -> None:
    response = _Response(
        [
            ": keep-alive",
            "event: progress",
            'data: {"stage":"generating","percent":20,"message":"正在生成"}',
            "",
            "event: delta",
            'data: {"text":"解读"}',
            "",
        ]
    )
    stream = CloudEventStream(response)

    assert list(stream.iterEvents()) == [
        {
            "event": "progress",
            "data": {
                "stage": "generating",
                "percent": 20,
                "message": "正在生成",
            },
        },
        {"event": "delta", "data": {"text": "解读"}},
    ]
    assert response.encoding == "utf-8"
    assert response.closed is True
