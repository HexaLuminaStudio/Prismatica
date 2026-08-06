from __future__ import annotations

import logging
from pathlib import Path


def test_logging_uses_one_file_and_is_idempotent():
    from app.core.utils.logger import (
        configureLogging,
        isLoggingConfigured,
        log,
        logger,
    )

    primaryLogDir = Path(__file__).parent
    logPath = primaryLogDir / "prismatica.log"
    ignoredLogDir = primaryLogDir / "ignored-logger-output"
    if logPath.exists():
        logPath.unlink()

    configureLogging(
        "DEV",
        logDir=primaryLogDir,
        consoleOutput=False,
    )
    configureLogging(
        "DEV",
        logDir=ignoredLogDir,
        consoleOutput=False,
    )

    assert isLoggingConfigured()
    assert logger is log

    log.info("统一日志 password=Secret123")
    log.error("单次错误")
    logging.getLogger("third_party").warning("应被忽略的第三方警告")
    logging.getLogger("third_party").error("标准库日志")
    log.complete()

    try:
        assert logPath.is_file()
        assert not ignoredLogDir.exists()

        content = logPath.read_text(encoding="utf-8")
        assert "统一日志" in content
        assert "password=***" in content
        assert "Secret123" not in content
        assert content.count("单次错误") == 1
        assert content.count("标准库日志") == 1
        assert "应被忽略的第三方警告" not in content
    finally:
        log.remove()
        if logPath.exists():
            logPath.unlink()
