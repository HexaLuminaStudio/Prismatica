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


# =====================================================================
# 内测版 / 正式版 开关(P0-fix 2026-07-18)
#
# 控制方式（优先级从高到低）：
#   1. 环境变量 PRISMATICA_BETA_MODE=1      → 强制打开内测模式
#   2. 环境变量 PRISMATICA_BETA_MODE=0      → 强制关闭内测模式
#   3.    未设置环境变量                        → 默认 False（正式版）
#
# 示例：
#   PowerShell: $env:PRISMATICA_BETA_MODE=1; python main.py
#   CMD:        set PRISMATICA_BETA_MODE=1 && python main.py
#   PyInstaller: PRISMATICA_BETA_MODE=1 Prismatica.exe
# =====================================================================


IS_BETA: bool = True


def _getLicenseSecret() -> str:
    """获取激活码 HMAC 签名密钥(2026-08-06 对齐服务端 .env.example)。

    优先级:
        1. 环境变量 LICENSE_SECRET(推荐,便于运营切换密钥)
        2. 与服务端 PrismaticaAPI/.env.example 默认值一致

    安全提示:
        - 生产部署必须通过环境变量 LICENSE_SECRET 注入,不要在源码中硬编码
        - 默认值为与本地联调后端(雨云公网 MySQL)对齐的密钥,正式发布前请轮换
        - 客户端无法仅凭本字段伪造激活码,但泄露本字段会大幅降低伪造门槛
    """
    import os as _os

    envSecret = _os.environ.get("LICENSE_SECRET")
    if envSecret:
        return envSecret
    # 默认值与 PrismaticaAPI/.env.example 的 LICENSE_SECRET 完全一致
    return "ec41f548431eb9ab3502b00dafd5bb3c192c81c38091b38cddfdbfc1a0b9ca65"


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
