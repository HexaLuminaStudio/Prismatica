# coding: utf-8
"""错误文案兜底映射(中文,2026-08-05 T1 引入)

使用场景:云端 API 在 4xx/5xx 时返回的 envelope 未带 message
(或者 message 为空字符串),前端按 HTTP status 兜底成中文文案,
保证 InfoBar 永远有 friendly message 给用户。

优先顺序:
    1. 后端 envelope.message(由 errors._ERROR_MESSAGE_CN 提供,已在大部分路径生效)
    2. 本表(statusCode → 中文)
    3. 顶层的「服务暂时不可用,请稍后再试」(500/502/503/504 共用)
"""

from __future__ import annotations

from typing import Optional


# HTTP 状态码 → 中文兜底文案
_STATUS_MESSAGE_CN: dict[int, str] = {
    400: "请求参数有误,请检查后重试",
    401: "未登录或登录已过期,请重新激活",
    402: "余额不足",
    403: "权限不足",
    404: "服务接口不存在或资源不可达",
    405: "请求方式不被支持",
    408: "请求超时,请重试",
    409: "请求冲突,该操作可能已完成",
    410: "资源已失效",
    422: "请求格式有误",
    429: "请求过于频繁,请稍后再试",
    500: "服务暂时不可用,请稍后再试",
    502: "网关异常,请稍后再试",
    503: "服务暂时不可用,请稍后再试",
    504: "网关超时,请稍后再试",
}


def fallbackMessage(statusCode: int) -> str:
    """返回 HTTP 状态码对应的中文兜底文案。

    未列出的状态码 → 走「服务暂时不可用」通用兜底。
    """
    return _STATUS_MESSAGE_CN.get(statusCode, _STATUS_MESSAGE_CN[500])


def pickMessage(serverMessage: Optional[str], statusCode: int) -> str:
    """统一文案选择:返回后端 message 或本地兜底。

    Args:
        serverMessage: 后端 envelope 中解析出的 message(可能为 None 或空字符串)。
        statusCode:    HTTP 状态码。

    Returns:
        最终给 UI 显示的中文文案。
    """
    if serverMessage and serverMessage.strip():
        return serverMessage.strip()
    return fallbackMessage(statusCode)


__all__ = ["fallbackMessage", "pickMessage"]
