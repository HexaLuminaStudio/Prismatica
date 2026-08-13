# coding: utf-8
"""Fluent 安装器进度协议回归测试。"""

from installer.frontend.core import (
    decodeProgressData,
    parseProgressState,
    readProgressState,
)


def testDecodeProgressDataSupportsLegacyGBK() -> None:
    rawData = "42|正在安装 Prismatica".encode("gbk")

    assert decodeProgressData(rawData) == "42|正在安装 Prismatica"


def testParseProgressStateTranslatesAsciiStatus() -> None:
    assert parseProgressState("37|installing") == (37, "正在安装 Prismatica")


def testReadProgressStateReadsCompleteSnapshot(tmp_path) -> None:
    progressPath = tmp_path / "install.progress"
    progressPath.write_bytes(b"73|finishing")

    assert readProgressState(progressPath) == (73, "正在完成系统配置")


def testReadProgressStateIgnoresPartialSnapshot(tmp_path) -> None:
    progressPath = tmp_path / "install.progress"
    progressPath.write_bytes(b"73|")

    assert readProgressState(progressPath) is None


def testReadProgressStateSupportsRealLegacyFormat(tmp_path) -> None:
    progressPath = tmp_path / "install.progress"
    progressPath.write_bytes("100|安装完成".encode("gbk"))

    assert readProgressState(progressPath) == (100, "安装完成")
