# coding:utf-8
import sys

from qfluentwidgets import (
    BoolValidator,
    ConfigItem,
    ConfigSerializer,
    FolderValidator,
    OptionsConfigItem,
    OptionsValidator,
    qconfig,
    QConfig,
    Theme,
)

from .setting import CONFIG_FILE, DOWNLOAD_FOLDER


def isWin11():
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000


class Config(QConfig):
    """Config of application"""

    # 语料下载设置
    DownloadSavePath = ConfigItem(
        "CorpusDownload", "DownloadSavePath", str(DOWNLOAD_FOLDER), FolderValidator()
    )
    NumberPerDownloads = OptionsConfigItem(
        "CorpusDownload", "NumberPerDownloads", 100, OptionsValidator([10, 20, 50, 100])
    )
    ThreadPerDownloads = OptionsConfigItem(
        "CorpusDownload",
        "ThreadPerDownloads",
        3,
        OptionsValidator([1, 2, 3, 4, 5, 6]),
    )
    MaximumAttempts = OptionsConfigItem(
        "CorpusDownload",
        "MaximumAttempts",
        3,
        OptionsValidator([i for i in range(1, 11)]),
    )
    HSKLoginToken = ConfigItem(
        "CorpusDownload",
        "HSKLoginToken",
        "",  # 请通过环境变量 HSK_USERNAME 和 HSK_PASSWORD 配置
    )
    GlobalLoginToken = ConfigItem(
        "CorpusDownload", "GlobalLoginToken", ""  # 请通过环境变量配置
    )

    MicaEnabled = ConfigItem("MainWindow", "MicaEnabled", isWin11(), BoolValidator())
    DpiScale = OptionsConfigItem(
        "MainWindow",
        "DpiScale",
        "Auto",
        OptionsValidator([1.0, 1.25, 1.5, 1.75, 2.0, "Auto"]),
        restart=True,
    )


cfg = Config()
cfg.themeMode.value = Theme.AUTO
qconfig.load(str(CONFIG_FILE.absolute()), cfg)
