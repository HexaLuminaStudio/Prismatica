# coding: utf-8
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

# 内测本地模式：发布不接入 Prismatica 自有账号、鉴权、计费、价格与 AI 云端。
# HSK / Global 语料站点等第三方数据源不受此开关影响。
INTERNAL_TEST_MODE = False

# 暂时隐藏 AI 聊天入口(2026-08-17)：仅从主窗口导航中移除展示，
# ChatInterface / ChatService 等功能代码完整保留，改回 False 即可恢复。
HIDE_AI_CHAT = True


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

MODE: Literal["DEV", "TEST", "RES"] = "DEV" if DEBUG else "RES"
