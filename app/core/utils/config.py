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
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3ODQ4MTU0NzEsInN1YiI6IjEzMzYwMCJ9.HGBma8WDDaOpdD2B8FNi2F_ROOWTXpdS1xVx1EKxTT0",
    )
    GlobalLoginToken = ConfigItem(
        "CorpusDownload", "GlobalLoginToken", "e92aa8cdd80826ef8991151690edf688"
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
