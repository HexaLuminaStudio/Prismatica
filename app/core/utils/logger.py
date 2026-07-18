# coding:utf-8
"""
统一的日志配置模块
提供敏感信息过滤、分级日志记录、日志轮转等功能
"""

import re
import sys
from pathlib import Path
from typing import List, Pattern

from loguru import logger as _logger
from app.core.utils.setting import LOG_FOLDER


# 敏感信息模式列表（常量：全大写+下划线）
SENSITIVE_PATTERNS_LIST: List[Pattern] = [
    # API密钥和Token
    re.compile(r"(api[_-]?key|apikey|api[_-]?token|access[_-]?token)", re.IGNORECASE),
    # P0-fix:长度阈值从 16 降到 8,避免短 token(如 8-12 位的内部密钥)漏网
    re.compile(
        r"(secret[_-]?key|secret[_-]?token|bearer\s+)[\w\-]{8,}", re.IGNORECASE
    ),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # OpenAI API Key格式(放低至 20)
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),  # GitHub Personal Access Token(放低)
    re.compile(r"gho_[a-zA-Z0-9]{20,}"),  # GitHub OAuth Token(放低)
    # 密码相关
    # P0-fix:阈值从 6 降到 4,4 位 PIN 也能被遮蔽
    re.compile(r"(密码|pwd|passwd|password)[\::=：]+[^\s]{4,}"),
    re.compile(r'(password|passwd|pwd)\s*[:=]\s*["\']?[^\s"\']{4,}["\']?', re.IGNORECASE),
    # 认证信息
    re.compile(
        r'(authorization|bearer|token|jwt)[\s:=]+["\']?[A-Za-z0-9\-_.~+/]+=*["\']?',
        re.IGNORECASE,
    ),
    re.compile(r"Basic\s+[A-Za-z0-9+/]+=*", re.IGNORECASE),
    # 邮箱和电话
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    re.compile(r"1[3-9]\d{9}"),  # 中国手机号格式
    # 身份证号
    re.compile(
        r"\b[1-9]\d{5}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"
    ),
    # 信用卡相关
    re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    re.compile(r"\b\d{16}\b"),
    # IP地址和端口（可能包含敏感信息）
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5}\b"),
    # 数据库连接字符串
    re.compile(
        r"(mongodb|mysql|postgresql|redis):\/\/[^\s]+:[^\s]+@[^\s]+", re.IGNORECASE
    ),
    re.compile(
        r"(host|server|database|db)[_-]?(name|user)[\s:=]+[^\s,;]+", re.IGNORECASE
    ),
    # SSH密钥和私钥
    re.compile(r"-----BEGIN\s+(RSA|DSA|EC|OPENSSH)?\s*PRIVATE\s+KEY-----"),
    # 财务数据
    re.compile(
        r"(account[_-]?no|bank[_-]?card|credit[_-]?card|银行卡)[:s:=：]+\d+",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{15,19}\b"),  # 银行卡号（15-19位）
]


def _filterSensitiveInfo(message: str) -> str:
    """
    过滤日志消息中的敏感信息

    :param message: 原始日志消息
    :return: 过滤后的安全消息
    """
    filteredMessage = message

    for pattern in SENSITIVE_PATTERNS_LIST:
        filteredMessage = pattern.sub(
            lambda m: _maskSensitiveValue(m.group()), filteredMessage
        )

    return filteredMessage


def _maskSensitiveValue(value: str) -> str:
    """
    遮蔽敏感值，保留前两位和后四位

    :param value: 敏感值
    :return: 遮蔽后的值
    """
    if len(value) <= 8:
        return "*" * len(value)

    # 检测是否有键名
    if ":" in value or "=" in value or " " in value:
        parts = re.split(r"[:=\s]+", value, 1)
        if len(parts) == 2:
            key, secret = parts
            if len(secret) > 6:
                return f"{key}:{secret[:2]}{'*' * (len(secret) - 6)}{secret[-4:]}"

    # 简单遮蔽
    if len(value) > 6:
        return f"{value[:2]}{'*' * (len(value) - 6)}{value[-4:]}"

    return "*" * len(value)


def _logFilter(record):
    """
    日志过滤器 - 自动过滤敏感信息
    """
    # 在记录前过滤敏感信息
    record["message"] = _filterSensitiveInfo(record["message"])
    return True


class Logger:
    """日志管理器类"""

    def __init__(
        self,
        logDir: str = "logs",
        rotation: str = "500 MB",
        retention: str = "10 days",
        level: str = "INFO",
        consoleOutput: bool = True,
        fileOutput: bool = True,
        formatString: str = None,
    ):
        """
        初始化日志管理器

        :param logDir: 日志文件存储目录
        :param rotation: 日志轮转大小限制
        :param retention: 日志保留时间
        :param level: 日志级别
        :param consoleOutput: 是否输出到控制台
        :param fileOutput: 是否输出到文件
        :param formatString: 自定义日志格式
        """
        self.logDir = logDir
        self.rotation = rotation
        self.retention = retention
        self.level = level
        self.consoleOutput = consoleOutput
        self.fileOutput = fileOutput
        self.formatString = formatString or (
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        )

        # 存储handler ID以便后续管理（受保护属性）
        self._handlers = []

    def setup(self):
        """配置日志系统"""
        # 移除默认处理器
        _logger.remove()

        # 添加控制台输出
        if self.consoleOutput:
            handlerId = _logger.add(
                sys.stderr,
                level=self.level,
                format=self.formatString,
                colorize=True,
                filter=_logFilter,
            )
            self._handlers.append(handlerId)

        # 添加文件输出
        if self.fileOutput:
            logPath = Path(self.logDir)
            logPath.mkdir(parents=True, exist_ok=True)

            # 普通日志文件
            handlerId = _logger.add(
                logPath / "log_{time:YYYY-MM-DD_HH-mm-ss}.log",
                level=self.level,
                format=self.formatString,
                rotation=self.rotation,
                retention=self.retention,
                encoding="utf-8",
                enqueue=True,  # 多线程安全
                filter=_logFilter,
            )
            self._handlers.append(handlerId)

            # 错误日志单独记录
            handlerId = _logger.add(
                logPath / "error_{time:YYYY-MM-DD}.log",
                level="ERROR",
                format=self.formatString,
                rotation="10 MB",
                retention="30 days",
                encoding="utf-8",
                enqueue=True,
                filter=_logFilter,
                compression="zip",
            )
            self._handlers.append(handlerId)

            # 调试日志（仅开发环境）
            handlerId = _logger.add(
                logPath / "debug_{time:YYYY-MM-DD}.log",
                level="DEBUG",
                format=self.formatString,
                rotation="50 MB",
                retention="3 days",
                encoding="utf-8",
                enqueue=True,
                filter=_logFilter,
            )
            self._handlers.append(handlerId)

        return self

    def debug(self, message: str, *args, **kwargs):
        """记录DEBUG级别日志"""
        _logger.debug(message, *args, **kwargs)

    def info(self, message: str, *args, **kwargs):
        """记录INFO级别日志"""
        _logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args, **kwargs):
        """记录WARNING级别日志"""
        _logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args, **kwargs):
        """记录ERROR级别日志"""
        _logger.error(message, *args, **kwargs)

    def critical(self, message: str, *args, **kwargs):
        """记录CRITICAL级别日志"""
        _logger.critical(message, *args, **kwargs)

    def exception(self, message: str, *args, **kwargs):
        """记录异常信息（自动包含堆栈跟踪）"""
        _logger.exception(message, *args, **kwargs)


def _autoSetup(environment: str = "DEV"):
    """
    根据环境自动配置日志级别

    :param environment: 运行环境标识 ("DEV", "TEST", "RES")
    """
    levelMap = {"DEV": "DEBUG", "TEST": "INFO", "RES": "WARNING"}

    level = levelMap.get(environment, "INFO")

    loggerInstance = Logger(logDir=LOG_FOLDER, level=level)
    loggerInstance.setup()

    return loggerInstance


# 创建默认的日志实例（小写变量名）
log = _autoSetup()


def getLogger():
    """获取logger实例"""
    return _logger


def getLog():
    """获取Logger类实例"""
    return log


# 为了向后兼容，导出_logger实例（小写变量名）
logger = _logger
