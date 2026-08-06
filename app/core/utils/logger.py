# coding:utf-8
"""
统一的日志配置模块
提供敏感信息过滤、分级日志记录、日志轮转、关键事件审计等功能

================================================================
日志使用规范(2026-08-06 规范化)
================================================================
日志级别与典型用途:

    DEBUG   调试细节,生产默认不输出。用于追踪函数入口/内部状态,
            仅在排查具体问题时启用(DEV 模式自动开启)。
    INFO    关键业务节点摘要,产品级必备追踪。
            例如:登录成功、下载启动、计费成功、任务入队。
    WARNING 可恢复的异常或预期内的失败,需要人关注但不必报警。
            例如:单次网络重试、缓存未命中、文件跳过。
    ERROR   单次操作失败、不可恢复但不影响整体流程。
            例如:某个文件下载失败、单个任务失败。
    EXCEPTION
            与 ERROR 同级,自动附带堆栈。用于 try/except 中需要看调用链的场合。
    AUDIT   关键合规/审计事件,落地到独立 audit_<date>.log(90 天)。
            业务侧必须记录的几类事件:
                - AUTH_xxx    鉴权事件(登录、激活、刷新、注销)
                - BILL_xxx    计费事件(预占、结算、退款、余额变更)
                - DOWNLOAD_xxx
                              语料/资源下载关键节点(开始/结束/失败)
                - STARTUP_xxx 启动流程关键里程碑(各 stage 完成)
                - CONFIG_xxx  用户关键设置变更(路径、并发、模型)

================================================================
输出通道约定
================================================================
- log_<时间戳>.log    普通日志(INFO+),轮转 500MB,保留 10 天
- debug_<日期>.log    调试日志(DEBUG+),轮转 50MB,保留 3 天(仅 DEV 模式)
- error_<日期>.log    错误日志(ERROR+),轮转 10MB,保留 30 天
- audit_<日期>.log    审计日志(自定义过滤),轮转 10MB,保留 90 天
- startup_<时间戳>.log
                      冷启动耗时(独立 logger,见 _StartupProfiler)

================================================================
敏感信息过滤
================================================================
所有 handler 都默认挂 _logFilter,自动遮蔽 API Key / Token /
手机号 / 邮箱 / 身份证 / 银行卡 等敏感字段。无需业务侧手动处理。

================================================================
import 约定
================================================================
统一从 `app.core.utils import logger, audit` 导入,
**不要** `from loguru import logger`(详见 CLAUDE.md)。

audit() 用法:
    from app.core.utils import audit
    audit("AUTH_LOGIN_SUCCESS", f"user={userId}")
"""

import re
import sys
import time
from pathlib import Path
from typing import List, Pattern

from loguru import logger as _logger
from app.core.utils.setting import LOG_FOLDER


# 敏感信息模式列表（常量：全大写+下划线）
SENSITIVE_PATTERNS_LIST: List[Pattern] = [
    # API密钥和Token
    re.compile(r"(api[_-]?key|apikey|api[_-]?token|access[_-]?token)", re.IGNORECASE),
    # P0-fix:长度阈值从 16 降到 8,避免短 token(如 8-12 位的内部密钥)漏网
    re.compile(r"(secret[_-]?key|secret[_-]?token|bearer\s+)[\w\-]{8,}", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # OpenAI API Key格式(放低至 20)
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),  # GitHub Personal Access Token(放低)
    re.compile(r"gho_[a-zA-Z0-9]{20,}"),  # GitHub OAuth Token(放低)
    # 密码相关
    # P0-fix:阈值从 6 降到 4,4 位 PIN 也能被遮蔽
    re.compile(r"(密码|pwd|passwd|password)[\::=：]+[^\s]{4,}"),
    re.compile(
        r'(password|passwd|pwd)\s*[:=]\s*["\']?[^\s"\']{4,}["\']?', re.IGNORECASE
    ),
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


def _auditFilter(record):
    """审计日志过滤器(2026-08-06)。

    只放行带 `extra["audit"]=True` 的事件,且对 message 做敏感字段遮蔽。
    业务侧应通过 `logger.bind(audit=True).info(...)` 或模块级 `audit()`
    函数写入,直接调 `logger.info()` 不会进 audit 日志。
    """
    record["message"] = _filterSensitiveInfo(record["message"])
    return record["extra"].get("audit") is True


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

            # 审计日志(2026-08-06 新增):只接收 audit() 写入的事件,
            # 通过 extra["audit"]=True 标记 + 自定义 filter 路由。
            # 业务事件类型约定见模块顶部注释(AUTH_/BILL_/DOWNLOAD_/STARTUP_/CONFIG_)。
            auditHandlerId = _logger.add(
                logPath / "audit_{time:YYYY-MM-DD}.log",
                level="INFO",
                format=self.formatString,
                rotation="10 MB",
                retention="90 days",
                encoding="utf-8",
                enqueue=True,
                filter=_auditFilter,
            )
            self._handlers.append(auditHandlerId)

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

    def audit(self, eventType: str, message: str, *args, **kwargs) -> None:
        """审计事件(2026-08-06 新增)。

        Args:
            eventType: 事件类型标识,建议大写 + 下划线,如
                       "AUTH_LOGIN_SUCCESS" / "BILL_PREAUTH" /
                       "DOWNLOAD_FINISH" 等。
            message:   事件描述,自由文本(已自动过敏感过滤)。
            *args / **kwargs: 透传给 loguru(支持 format 占位)。
        """
        _logger.bind(audit=True).info(f"[{eventType}] {message}", *args, **kwargs)


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


# =====================================================================
# 模块级 audit() 便捷函数(2026-08-06)
#
# 用法:
#     from app.core.utils import audit
#     audit("AUTH_LOGIN_SUCCESS", f"user={userId}")
#
# 与 logger.info() 的区别:
#   - logger.info(...)  → 走 log_<时间戳>.log (普通日志,10 天保留)
#   - audit(...)        → 走 audit_<日期>.log (审计,90 天保留 + 独立通道)
#   - audit 不需要堆栈、不参与日志染色,只关心"发生了什么"
# =====================================================================
def audit(eventType: str, message: str, *args, **kwargs) -> None:
    """模块级 audit() 快捷方法。

    Args:
        eventType: 事件类型,如 "AUTH_LOGIN_SUCCESS" / "BILL_PREAUTH"。
        message:   事件描述,自由文本(已自动过敏感过滤)。
    """
    _logger.bind(audit=True).info(f"[{eventType}] {message}", *args, **kwargs)


# =====================================================================
# 冷启动耗时埋点(2026-07-30 新增)
#
# 设计目的:
#   - 用户反馈"冷启动很慢",需要一个独立文件记录启动各阶段耗时
#   - 落盘到 logs/startup_<时间戳>.log,便于用户反馈后定位瓶颈
#   - 使用 loguru 子 logger("startup"),与主日志隔离
# 用法:
#   profiler = StartupProfiler()
#   with profiler.stage("stage_name", "阶段描述"):
#       ... do work ...
#   profiler.mark("checkpoint_name", "额外说明")
#   profiler.finish()  # 主窗口 ready 后调用,写汇总
# =====================================================================
class _StartupProfiler:
    """冷启动耗时分析器。模块级单例,通过 getStartupProfiler() 获取。"""

    def __init__(self):
        self._bootStart = time.perf_counter()
        self._importStart = self._bootStart  # 将在最早 stage 调用前再校准
        self._records: List[dict] = []  # [(name, label, elapsedMs, sinceStartMs)]
        self._handlerId = None
        self._logFilePath: Path = None
        self._finished = False

    def _attachFileHandler(self) -> None:
        """按需挂载 startup_<时间戳>.log 文件 handler(只挂一次)。"""
        if self._handlerId is not None:
            return
        logDir = Path(LOG_FOLDER)
        logDir.mkdir(parents=True, exist_ok=True)
        # 文件名带启动时刻,便于 grep / 上报时定位当次启动
        self._logFilePath = logDir / f"startup_{time.strftime('%Y%m%d_%H%M%S')}.log"
        fmt = (
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <5}</level> | "
            "<level>{message}</level>"
        )
        self._handlerId = _logger.add(
            str(self._logFilePath),
            level="DEBUG",
            format=fmt,
            filter=lambda record: record["extra"].get("startup") is True,
            encoding="utf-8",
            enqueue=True,
        )
        _logger.bind(startup=True).info(
            "[StartupProfiler] 日志句柄已挂载 -> {}", self._logFilePath.name
        )

    def stage(self, name: str, label: str = ""):
        """返回一个上下文管理器:进入时记录阶段起点,退出时记录耗时。

        :param name:  阶段英文/拼音键名(用于聚合统计)
        :param label: 中文说明(便于用户上报后阅读)
        :return: 上下文管理器
        """
        # 懒加载 file handler,避免污染主流程 import 顺序
        self._attachFileHandler()
        return _StageScope(self, name, label)

    def mark(self, name: str, label: str = "") -> None:
        """记录一个瞬时检查点(不配对 start/end)。"""
        self._attachFileHandler()
        now = time.perf_counter()
        elapsedMs = (now - self._bootStart) * 1000.0
        prevMs = self._records[-1]["sinceStartMs"] if self._records else 0.0
        deltaMs = elapsedMs - prevMs
        self._records.append(
            {
                "name": name,
                "label": label,
                "elapsedMs": elapsedMs,
                "sinceStartMs": elapsedMs,
                "deltaMs": deltaMs,
                "kind": "mark",
            }
        )
        _logger.bind(startup=True).info(
            "[MARK] +{:7.1f}ms (total {:8.1f}ms) | {} | {}",
            deltaMs,
            elapsedMs,
            name,
            label,
        )

    def finish(self) -> None:
        """主窗口 ready 后调用,汇总并落盘。重复调用安全。"""
        if self._finished:
            return
        self._attachFileHandler()
        now = time.perf_counter()
        totalMs = (now - self._bootStart) * 1000.0
        # 汇总:按总耗时倒序,前 12 个最耗时阶段
        stages = [r for r in self._records if r["kind"] == "stage"]
        top = sorted(stages, key=lambda r: r["elapsedMs"], reverse=True)[:12]
        _logger.bind(startup=True).info("=" * 60)
        _logger.bind(startup=True).info(
            "[SUMMARY] 冷启动总耗时 = {:.1f} ms (= {:.2f} s)",
            totalMs,
            totalMs / 1000.0,
        )
        _logger.bind(startup=True).info(
            "[SUMMARY] 累计阶段数 = {}, 检查点数 = {}",
            len(stages),
            sum(1 for r in self._records if r["kind"] == "mark"),
        )
        if top:
            _logger.bind(startup=True).info(
                "[SUMMARY] Top-12 耗时阶段(按结束时间绝对值):"
            )
            for r in top:
                _logger.bind(startup=True).info(
                    "  -> +{:8.1f} ms (total {:8.1f} ms) | {} | {}",
                    r["deltaMs"],
                    r["elapsedMs"],
                    r["name"],
                    r["label"],
                )
        _logger.bind(startup=True).info("[SUMMARY] 详细文件: {}", self._logFilePath)
        _logger.bind(startup=True).info("=" * 60)
        # 同步在主日志里打一行,便于 grep "冷启动总耗时"
        logger.info(
            "[StartupProfiler] 冷启动总耗时 = {:.1f} ms,详情见 {}",
            totalMs,
            self._logFilePath,
        )
        self._finished = True


class _StageScope:
    """_StartupProfiler.stage() 返回的上下文管理器。"""

    def __init__(self, owner: "_StartupProfiler", name: str, label: str):
        self._owner = owner
        self._name = name
        self._label = label
        self._start = 0.0
        self._prevMs = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        # 当前累计耗时(进入此阶段时的"已过时间")
        self._prevMs = (self._start - self._owner._bootStart) * 1000.0
        _logger.bind(startup=True).info(
            "[STAGE-START] t={:.1f} ms | {} | {}",
            self._prevMs,
            self._name,
            self._label,
        )
        return self

    def __exit__(self, excType, excVal, excTb):
        end = time.perf_counter()
        stageMs = (end - self._start) * 1000.0
        deltaMs = stageMs  # 阶段自身耗时
        self._owner._records.append(
            {
                "name": self._name,
                "label": self._label,
                "elapsedMs": (end - self._owner._bootStart) * 1000.0,
                "sinceStartMs": (end - self._owner._bootStart) * 1000.0,
                "deltaMs": stageMs,
                "kind": "stage",
            }
        )
        if excType is None:
            _logger.bind(startup=True).info(
                "[STAGE-END  ] +{:7.1f} ms (total {:8.1f} ms) | {} | {}",
                deltaMs,
                (end - self._owner._bootStart) * 1000.0,
                self._name,
                self._label,
            )
        else:
            _logger.bind(startup=True).error(
                "[STAGE-ERR  ] {:7.1f} ms (total {:8.1f} ms) | {} | {} | exc={}",
                stageMs,
                (end - self._owner._bootStart) * 1000.0,
                self._name,
                self._label,
                excVal,
            )
        # 不吞异常,让上层知道
        return False


# 单例代理
_startupProfilerInstance: _StartupProfiler = None


def getStartupProfiler() -> _StartupProfiler:
    """获取冷启动耗时分析器单例。模块导入后随时可调用。"""
    global _startupProfilerInstance
    if _startupProfilerInstance is None:
        _startupProfilerInstance = _StartupProfiler()
    return _startupProfilerInstance
