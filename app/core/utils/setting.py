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


def _getLicenseSecret() -> str:
    """获取激活码 HMAC 签名密钥(优先环境变量,其次常量默认值)。

    安全提示:
        - 生产部署必须通过环境变量 LICENSE_SECRET 注入,不要在源码中硬编码
        - 默认值为开发占位,正式发布前请轮换为强随机字符串(>=32 字节)
        - 客户端无法仅凭本字段伪造激活码,但泄露本字段会大幅降低伪造门槛
    """
    import os as _os

    envSecret = _os.environ.get("LICENSE_SECRET")
    if envSecret:
        return envSecret
    # 开发期默认值:正式发布前必须替换为环境变量或强随机串
    return "DEV-LICENSE-HMAC-SECRET-PLEASE-OVERRIDE-IN-PROD"


# 激活码签名 HMAC 密钥(项目内统一引用)
LICENSE_SECRET = _getLicenseSecret()


def getInstallDir() -> Path:
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


INSTALL_DIR = getInstallDir()
CONFIG_FOLDER = INSTALL_DIR / "config"
CONFIG_FILE = CONFIG_FOLDER / "config.json"
DOWNLOAD_FOLDER = INSTALL_DIR / "download"
LOG_FOLDER = INSTALL_DIR / "logs"
DATA_FOLDER = INSTALL_DIR / "datas"  # 统一数据根目录(语料库/注册表/导出)

# 确保目录存在
CONFIG_FOLDER.mkdir(parents=True, exist_ok=True)
DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
LOG_FOLDER.mkdir(parents=True, exist_ok=True)
DATA_FOLDER.mkdir(parents=True, exist_ok=True)

MODE: Literal["DEV", "TEST", "RES"] = "DEV"
