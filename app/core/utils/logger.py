# coding: utf-8
"""Prismatica 前端统一日志入口。

日志模块本身不创建目录、不创建文件。应用入口必须显式调用
``configureLogging()``，所有业务模块统一从 ``app.core.utils`` 导入
``log``。``logger`` 仅作为旧代码迁移期的兼容别名。
"""

from __future__ import annotations

import logging
import re
import sys
import threading
from pathlib import Path
from typing import List, Pattern

from loguru import logger as _logger


SENSITIVE_PATTERNS_LIST: List[Pattern[str]] = [
    re.compile(
        r"(api[_-]?key|api[_-]?token|access[_-]?token|secret[_-]?key|"
        r"secret[_-]?token|authorization|bearer|jwt)"
        r"\s*[:=：]?\s*[\"']?[^\s,;\"']{4,}[\"']?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(密码|password|passwd|pwd)\s*[:=：]\s*[\"']?[^\s,;\"']{4,}[\"']?",
        re.IGNORECASE,
    ),
    re.compile(r"sk-[a-zA-Z0-9_-]{20,}"),
    re.compile(r"gh[op]_[a-zA-Z0-9]{20,}"),
    re.compile(r"Basic\s+[A-Za-z0-9+/]+=*", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(
        r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
        r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"
    ),
    re.compile(r"\b(?:\d{4}[\s-]?){3}\d{4}\b"),
    re.compile(
        r"(?:mongodb|mysql|postgresql|redis)://[^\s]+",
        re.IGNORECASE,
    ),
    re.compile(r"-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH)?\s*PRIVATE\s+KEY-----"),
]


_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)
_LEVELS = {"DEV": "DEBUG", "TEST": "WARNING", "RES": "INFO"}
_configureLock = threading.RLock()
_configured = False
_handlerIds: list[int] = []


def _maskSensitiveValue(value: str) -> str:
    """将匹配到的敏感内容整体遮蔽，避免保留可恢复片段。"""
    if ":" in value or "=" in value or "：" in value:
        parts = re.split(r"[:=：]", value, maxsplit=1)
        return f"{parts[0]}=***"
    if value.lower().startswith("bearer "):
        return "Bearer ***"
    if value.lower().startswith("basic "):
        return "Basic ***"
    return "***"


def _filterSensitiveInfo(message: str) -> str:
    filteredMessage = str(message)
    for pattern in SENSITIVE_PATTERNS_LIST:
        filteredMessage = pattern.sub(
            lambda match: _maskSensitiveValue(match.group()), filteredMessage
        )
    return filteredMessage


def _logFilter(record) -> bool:
    """所有输出通道共用的敏感信息过滤器。"""
    record["message"] = _filterSensitiveInfo(record["message"])
    return True


class _InterceptHandler(logging.Handler):
    """把标准库 logging 转发到统一 Loguru sink。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame = logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        _logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def _installStandardLoggingBridge() -> None:
    """只接管第三方库错误，避免其调试与重试信息淹没业务日志。"""
    rootLogger = logging.getLogger()
    rootLogger.handlers = [_InterceptHandler()]
    rootLogger.setLevel(logging.ERROR)


def _installExceptionHooks() -> None:
    def _handleException(excType, excValue, excTraceback) -> None:
        if issubclass(excType, KeyboardInterrupt):
            sys.__excepthook__(excType, excValue, excTraceback)
            return
        _logger.opt(exception=(excType, excValue, excTraceback)).critical(
            "未捕获异常"
        )

    sys.excepthook = _handleException

    if hasattr(threading, "excepthook"):
        def _handleThreadException(args) -> None:
            if args.exc_type is SystemExit:
                return
            _logger.opt(
                exception=(args.exc_type, args.exc_value, args.exc_traceback)
            ).critical("线程发生未捕获异常: {}", args.thread.name)

        threading.excepthook = _handleThreadException


def configureLogging(
    environment: str = "DEV",
    *,
    logDir: str | Path | None = None,
    consoleOutput: bool | None = None,
    fileOutput: bool = True,
) -> None:
    """一次性配置前端日志。

    重复调用不会重新注册 handler，也不会创建新的日志文件。生产和开发
    环境始终只有一个活动文件 ``prismatica.log``；DEV 环境默认额外输出
    到控制台。
    """
    global _configured

    with _configureLock:
        if _configured:
            return

        normalizedEnvironment = str(environment).upper()
        level = _LEVELS.get(normalizedEnvironment, "INFO")
        if consoleOutput is None:
            consoleOutput = normalizedEnvironment == "DEV"

        _logger.remove()
        _handlerIds.clear()

        if consoleOutput:
            _handlerIds.append(
                _logger.add(
                    sys.stderr,
                    level=level,
                    format=_FORMAT,
                    colorize=True,
                    filter=_logFilter,
                    backtrace=False,
                    diagnose=False,
                )
            )

        if fileOutput:
            if logDir is None:
                from app.core.utils.setting import LOG_FOLDER

                resolvedLogDir = Path(LOG_FOLDER)
            else:
                resolvedLogDir = Path(logDir)
            resolvedLogDir.mkdir(parents=True, exist_ok=True)

            _handlerIds.append(
                _logger.add(
                    resolvedLogDir / "prismatica.log",
                    level=level,
                    format=_FORMAT,
                    rotation="20 MB",
                    retention=5,
                    compression="zip",
                    encoding="utf-8",
                    filter=_logFilter,
                    backtrace=False,
                    diagnose=False,
                )
            )

        _installStandardLoggingBridge()
        _installExceptionHooks()
        _configured = True


def isLoggingConfigured() -> bool:
    """返回日志系统是否已经完成初始化。"""
    return _configured


# Loguru 自带默认 stderr handler。模块导入时先移除它，确保初始化前不产生
# 文件或控制台输出；真正的输出通道只由 configureLogging() 创建。
_logger.remove()

# 新代码使用 log。logger 是旧代码迁移期的兼容别名，二者指向同一实例。
log = _logger
logger = log


__all__ = ["configureLogging", "isLoggingConfigured", "log", "logger"]
