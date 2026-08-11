# coding: utf-8
"""采集设置页所需的本机系统信息。"""

import platform
import sys

import psutil

from app.core.utils import logger
from app.core.utils.setting import INSTALL_DIR


class SystemInfoService:
    """以可降级的方式提供系统、处理器、内存和磁盘摘要。"""

    def getItems(self) -> list[tuple[str, str]]:
        """返回设置页可直接展示的系统信息条目。"""
        infoItems = [("系统", self._getOperatingSystemLabel())]

        try:
            logicalCount = psutil.cpu_count(logical=True)
            physicalCount = psutil.cpu_count(logical=False) or logicalCount
            if physicalCount is None or logicalCount is None:
                raise RuntimeError("无法读取处理器核心数")
            infoItems.append(("CPU", f"{physicalCount} 核 / {logicalCount} 线程"))
        except Exception as error:
            logger.warning(f"[SystemInfo] 处理器信息读取失败: {error}")
            infoItems.append(("CPU", platform.machine() or "未知"))

        try:
            memory = psutil.virtual_memory()
            totalGb = memory.total / (1024**3)
            usedGb = memory.used / (1024**3)
            infoItems.append(
                ("内存", f"{usedGb:.1f} GB / {totalGb:.1f} GB ({memory.percent:.1f}%)")
            )
        except Exception as error:
            logger.warning(f"[SystemInfo] 内存信息读取失败: {error}")
            infoItems.append(("内存", "未知"))

        try:
            disk = psutil.disk_usage(str(INSTALL_DIR))
            totalGb = disk.total / (1024**3)
            usedGb = disk.used / (1024**3)
            infoItems.append(
                ("磁盘", f"{usedGb:.0f} GB / {totalGb:.0f} GB ({disk.percent:.1f}%)")
            )
        except Exception as error:
            logger.warning(f"[SystemInfo] 磁盘信息读取失败: {error}")
            infoItems.append(("磁盘", "未知"))

        return infoItems

    @staticmethod
    def _getOperatingSystemLabel() -> str:
        """返回用户可识别的系统名称，并修正 Windows 11 兼容版本号。"""
        systemName = platform.system() or "未知系统"
        release = platform.release()

        if systemName == "Windows":
            try:
                windowsVersion = sys.getwindowsversion()
                if windowsVersion.major == 10 and windowsVersion.build >= 22000:
                    release = "11"
            except (AttributeError, OSError):
                pass
        elif systemName == "Darwin":
            systemName = "macOS"
            release = platform.mac_ver()[0] or release

        return " ".join(part for part in (systemName, release) if part).strip()


systemInfoService = SystemInfoService()
