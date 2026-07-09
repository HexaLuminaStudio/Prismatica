# coding:utf-8
import sys

from qfluentwidgets import (BoolValidator, ConfigItem, ConfigSerializer, FolderValidator, OptionsConfigItem,
                            OptionsValidator, qconfig, QConfig, Theme)

from .setting import CONFIG_FILE, DOWNLOAD_FOLDER


def isWin11():
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000



class Config(QConfig):
    """Config of application"""

    micaEnabled = ConfigItem("MainWindow", "MicaEnabled", isWin11(), BoolValidator())
    dpiScale = OptionsConfigItem(
        "MainWindow",
        "DpiScale",
        "Auto",
        OptionsValidator([1.0, 1.25, 1.5, 1.75, 2.0, "Auto"]),
        restart=True,
    )


cfg = Config()
cfg.themeMode.value = Theme.AUTO
qconfig.load(str(CONFIG_FILE.absolute()), cfg)
