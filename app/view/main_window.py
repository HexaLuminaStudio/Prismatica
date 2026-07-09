# coding: utf-8

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qfluentwidgets import NavigationItemPosition, SplashScreen, MSFluentWindow

from app.core.utils import signalBus, logger
from .widgets.titlebar_widget import CustomTitleBar
from .hsk_interface import HskInterface
from .setting_interface import SettingInterface


class MainWindow(MSFluentWindow):

    def __init__(self):
        logger.info("开始初始化主窗口")
        super().__init__()
        self.setTitleBar(CustomTitleBar(self))
        self.initWindow()

        self.hskInterface = HskInterface(self)
        self.settingInterface = SettingInterface(self)

        self.connectSignalToSlot()

        self.initNavigation()
        logger.info("主窗口初始化完成")

    def connectSignalToSlot(self):
        logger.debug("连接信号和槽")

    def initNavigation(self):
        logger.info("开始初始化导航界面")
        self.addSubInterface(
            self.hskInterface,
            QIcon(":app/icons/Hsk.svg"),
            "HSK下载",
            position=NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.settingInterface,
            QIcon(":app/icons/Setting.svg"),
            "设置",
            position=NavigationItemPosition.BOTTOM,
        )
        self.splashScreen.finish()
        logger.info("导航界面初始化完成")

    def initWindow(self):
        logger.info("开始初始化窗口设置")
        self.resize(1050, 800)
        self.setMinimumWidth(760)
        self.setMinimumHeight(800)
        self.setWindowIcon(QIcon(":app/images/logo.png"))
        self.setWindowTitle("六维语宙客户端")

        logger.debug("已设置窗口基本属性")

        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.raise_()
        logger.debug("已创建并设置启动屏幕")

        desktop = QApplication.primaryScreen().availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)
        logger.debug("已移动窗口到屏幕中心")

        self.show()
        QApplication.processEvents()
        logger.info("窗口初始化完成并显示")
