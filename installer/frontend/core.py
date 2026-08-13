# coding: utf-8
"""安装器前端的路径、参数与提权进程服务。"""

from __future__ import annotations

import ctypes
import locale
import os
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal


APP_NAME = "Prismatica"
APP_VERSION = "1.0.0"
APP_EXE_NAME = "6DCorpusClient.exe"
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_SHOWNORMAL = 1
WAIT_OBJECT_0 = 0
INFINITE = 0xFFFFFFFF
ERROR_CANCELLED = 1223
PROGRESS_STATUS_TEXT = {
    "preparing": "正在准备安装",
    "preparing-files": "正在准备程序文件",
    "installing": "正在安装 Prismatica",
    "finishing": "正在完成系统配置",
    "completed": "安装完成",
}


@dataclass(frozen=True)
class InstallOptions:
    """用户在 Fluent 前端确认的安装选项。"""

    installDir: Path
    createDesktopIcon: bool
    associateProjectFiles: bool


def defaultInstallDir() -> Path:
    """返回与 Inno `{autopf}` 一致的默认安装目录。"""
    programFiles = os.environ.get("ProgramFiles") or r"C:\Program Files"
    return Path(programFiles) / APP_NAME


def bundledPath(relativePath: str | Path) -> Path:
    """同时兼容源码运行与 Nuitka onefile 解包目录。"""
    relativePath = Path(relativePath)
    moduleDir = Path(__file__).resolve().parent
    candidates = [moduleDir, *list(moduleDir.parents)[:4], Path(sys.executable).resolve().parent]
    for baseDir in candidates:
        candidatePath = baseDir / relativePath
        if candidatePath.exists():
            return candidatePath
    return moduleDir.parent / relativePath


def buildInstallerArguments(
    options: InstallOptions,
    progressPath: Path,
    logPath: Path,
) -> list[str]:
    """构建交给 Inno 核心的静默安装参数。"""
    taskValues = [
        "desktopicon" if options.createDesktopIcon else "!desktopicon",
        "fileassoc" if options.associateProjectFiles else "!fileassoc",
    ]
    return [
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        f'/DIR={options.installDir}',
        f'/MERGETASKS={",".join(taskValues)}',
        f'/PROGRESSFILE={progressPath}',
        f'/LOG={logPath}',
    ]


def parseProgressState(rawState: str) -> tuple[int, str]:
    """解析 Inno 写出的 `百分比|状态` 文本。"""
    normalizedState = rawState.strip()
    if not normalizedState:
        return 0, "正在准备安装"
    percentText, separator, statusText = normalizedState.partition("|")
    try:
        percent = max(0, min(100, int(percentText)))
    except ValueError:
        percent = 0
    if not separator or not statusText.strip():
        statusText = "正在写入程序文件"
    normalizedStatus = statusText.strip()
    return percent, PROGRESS_STATUS_TEXT.get(normalizedStatus, normalizedStatus)


def decodeProgressData(rawData: bytes) -> str:
    """解码 Inno 进度文件，并兼容旧版本使用的 Windows ANSI 编码。"""
    if not rawData:
        return ""
    if rawData.startswith((b"\xff\xfe", b"\xfe\xff")):
        return rawData.decode("utf-16")

    encodings = ["utf-8-sig"]
    if os.name == "nt":
        encodings.append("mbcs")
    encodings.extend((locale.getpreferredencoding(False), "gb18030"))
    for encoding in dict.fromkeys(encodings):
        try:
            return rawData.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return rawData.decode("utf-8", errors="replace")


def readProgressState(progressPath: Path) -> tuple[int, str] | None:
    """读取一份完整进度快照；文件并发写入时留待下一次轮询。"""
    try:
        rawState = decodeProgressData(progressPath.read_bytes()).strip()
    except (OSError, UnicodeError):
        return None
    if not rawState:
        return None

    percentText, separator, statusText = rawState.partition("|")
    if not separator or not percentText.isdecimal() or not statusText.strip():
        return None
    return parseProgressState(rawState)


class ShellExecuteInfoW(ctypes.Structure):
    """Win32 ShellExecuteExW 参数结构。"""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIdList", wintypes.LPVOID),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def runElevatedAndWait(executablePath: Path, arguments: list[str]) -> int:
    """通过 UAC 启动 Inno 核心并等待退出码。"""
    if os.name != "nt":
        raise RuntimeError("Prismatica 安装器仅支持 Windows")
    if not executablePath.is_file():
        raise FileNotFoundError(f"未找到安装核心：{executablePath}")

    parameterText = subprocess.list2cmdline(arguments)
    shellInfo = ShellExecuteInfoW()
    shellInfo.cbSize = ctypes.sizeof(shellInfo)
    shellInfo.fMask = SEE_MASK_NOCLOSEPROCESS
    shellInfo.lpVerb = "runas"
    shellInfo.lpFile = str(executablePath)
    shellInfo.lpParameters = parameterText
    shellInfo.lpDirectory = str(executablePath.parent)
    shellInfo.nShow = SW_SHOWNORMAL

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfoW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    if not shell32.ShellExecuteExW(ctypes.byref(shellInfo)):
        errorCode = ctypes.get_last_error()
        if errorCode == ERROR_CANCELLED:
            raise PermissionError("你取消了 Windows 管理员授权，尚未开始安装")
        raise OSError(errorCode, ctypes.FormatError(errorCode))

    try:
        waitResult = kernel32.WaitForSingleObject(shellInfo.hProcess, INFINITE)
        if waitResult != WAIT_OBJECT_0:
            raise OSError(f"等待安装核心结束失败：{waitResult}")
        exitCode = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(shellInfo.hProcess, ctypes.byref(exitCode)):
            errorCode = ctypes.get_last_error()
            raise OSError(errorCode, ctypes.FormatError(errorCode))
        return int(exitCode.value)
    finally:
        kernel32.CloseHandle(shellInfo.hProcess)


class InstallWorker(QThread):
    """在工作线程中运行需要管理员权限的 Inno 安装核心。"""

    processStarted = Signal()
    processFinished = Signal(int)
    processFailed = Signal(str)

    def __init__(
        self,
        backendPath: Path,
        arguments: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._backendPath = backendPath
        self._arguments = list(arguments)

    def run(self) -> None:
        try:
            self.processStarted.emit()
            exitCode = runElevatedAndWait(self._backendPath, self._arguments)
        except Exception as error:
            self.processFailed.emit(str(error))
            return
        self.processFinished.emit(exitCode)


__all__ = [
    "APP_EXE_NAME",
    "APP_NAME",
    "APP_VERSION",
    "InstallOptions",
    "InstallWorker",
    "buildInstallerArguments",
    "bundledPath",
    "decodeProgressData",
    "defaultInstallDir",
    "parseProgressState",
    "readProgressState",
    "runElevatedAndWait",
]
