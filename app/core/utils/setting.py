# coding: utf-8
import os
import sys
from pathlib import Path
from typing import Literal

# change DEBUG to False if you want to compile the code to exe
DEBUG = "__compiled__" not in globals()


YEAR = 2026
AUTHOR = "猫叁零"
VERSION = "v1.0.0"
INNER_VERSION = "100"
APP_NAME = "Prismatica"


def get_install_dir() -> Path:
    """
    获取应用程序安装目录。

    - 打包成 exe 后：返回 exe 所在目录（sys.executable 的父目录）
    - 开发模式下：返回项目根目录（app/ 的父目录）
    """
    if getattr(sys, "frozen", False):
        # PyInstaller / Nuitka 打包后的 exe 环境
        return Path(sys.executable).parent
    else:
        # 开发环境：项目根目录 = app/ 的父目录
        return Path(__file__).resolve().parent.parent.parent.parent

INSTALL_DIR = get_install_dir()
CONFIG_FOLDER = INSTALL_DIR / "config"
CONFIG_FILE = CONFIG_FOLDER / "config.json"
DOWNLOAD_FOLDER = INSTALL_DIR / "download"
LOG_FOLDER = INSTALL_DIR / "logs"

# 确保目录存在
CONFIG_FOLDER.mkdir(parents=True, exist_ok=True)
DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
LOG_FOLDER.mkdir(parents=True, exist_ok=True)

MODE: Literal["DEV", "TEST", "RES"] = "DEV"
