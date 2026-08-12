# coding: utf-8
"""应用单实例守卫。"""
from __future__ import annotations

import atexit
import ctypes
import os
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO

DEFAULT_INSTANCE_NAME = "Hexalumina.Prismatica.Desktop"
_ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    """持有操作系统级进程锁，防止同一用户会话重复启动应用。"""

    def __init__(self, instanceName: str = DEFAULT_INSTANCE_NAME) -> None:
        self._instanceName = instanceName
        self._mutexHandle: int | None = None
        self._lockFile: BinaryIO | None = None
        self._acquired = False

    def acquire(self) -> bool:
        """尝试取得单实例锁；已有实例时返回 False。"""
        if self._acquired:
            return True
        acquired = self._acquireWindowsMutex() if sys.platform == "win32" else self._acquireFileLock()
        if acquired:
            self._acquired = True
            atexit.register(self.release)
        return acquired

    def _acquireWindowsMutex(self) -> bool:
        """使用当前登录会话内的 Windows 命名互斥体。"""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        createMutex = kernel32.CreateMutexW
        createMutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        createMutex.restype = ctypes.c_void_p
        closeHandle = kernel32.CloseHandle
        closeHandle.argtypes = [ctypes.c_void_p]
        closeHandle.restype = ctypes.c_bool

        ctypes.set_last_error(0)
        handle = createMutex(None, True, f"Local\\{self._instanceName}")
        if not handle:
            raise OSError(ctypes.get_last_error(), "无法创建应用单实例互斥体")
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            closeHandle(handle)
            return False
        self._mutexHandle = int(handle)
        return True

    def _acquireFileLock(self) -> bool:
        """非 Windows 开发环境使用无残留风险的文件描述符锁。"""
        import fcntl

        safeName = "".join(char if char.isalnum() else "_" for char in self._instanceName)
        lockPath = Path(tempfile.gettempdir()) / f"{safeName}-{os.getuid()}.lock"
        lockFile = lockPath.open("a+b")
        try:
            fcntl.flock(lockFile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lockFile.close()
            return False
        self._lockFile = lockFile
        return True

    def notifyAlreadyRunning(self) -> None:
        """提示用户已有实例正在运行；不依赖 QApplication。"""
        title = "棱溯（Prismatica）"
        message = "棱溯已在运行，无需重复启动。\n\n请切换到已打开的窗口继续使用。"
        if sys.platform == "win32":
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            messageBox = user32.MessageBoxW
            messageBox.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.c_uint,
            ]
            messageBox.restype = ctypes.c_int
            # MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND
            messageBox(None, message, title, 0x00000000 | 0x00000040 | 0x00010000)
            return
        print(message, file=sys.stderr)

    def release(self) -> None:
        """释放单实例锁；可安全重复调用。"""
        if not self._acquired:
            return
        self._acquired = False
        if self._mutexHandle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
            kernel32.ReleaseMutex.restype = ctypes.c_bool
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_bool
            handle = ctypes.c_void_p(self._mutexHandle)
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
            self._mutexHandle = None
        if self._lockFile is not None:
            try:
                import fcntl

                fcntl.flock(self._lockFile.fileno(), fcntl.LOCK_UN)
            finally:
                self._lockFile.close()
                self._lockFile = None


__all__ = ["DEFAULT_INSTANCE_NAME", "SingleInstanceGuard"]
