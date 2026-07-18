# coding: utf-8

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qfluentwidgets import (
    NavigationItemPosition,
    SplashScreen,
    MSFluentWindow,
    setThemeColor,
    MessageBox,
)

from app.core.utils import signalBus, logger
from app.core.api import taskControl
from .widgets.titlebar_widget import CustomTitleBar
from .hsk_interface import HskInterface
from .global_interface import GlobalInterface
from .bias_interface import BiasInterface
from .freq_analyzer_interface import FreqAnalyzerInterface
from .task_interface import TaskInterface
from .setting_interface import SettingInterface


class MainWindow(MSFluentWindow):

    def __init__(self):
        logger.info("[MainWindow] 开始初始化主窗口")
        super().__init__()
        setThemeColor("#00b09c")
        self.setTitleBar(CustomTitleBar(self))
        self.initWindow()

        self.hskInterface = HskInterface(self)
        self.globalInterface = GlobalInterface(self)
        self.biasInterface = BiasInterface(self)
        self.freqAnalyzerInterface = FreqAnalyzerInterface(self)
        self.taskInterface = TaskInterface(self)
        self.settingInterface = SettingInterface(self)

        self.connectSignalToSlot()

        self.initNavigation()
        logger.info("[MainWindow] 主窗口初始化完成")

    def connectSignalToSlot(self):
        logger.debug("[MainWindow] 连接信号和槽")

    def initNavigation(self):
        logger.info("[MainWindow] 开始初始化导航界面")
        self.addSubInterface(
            self.hskInterface,
            QIcon(":app/icons/Hsk.svg"),
            "HSK下载",
            position=NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.globalInterface,
            QIcon(":app/icons/Global.svg"),
            "全球中介下载",
            position=NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.biasInterface,
            QIcon(":app/icons/Bias.svg"),
            "偏误统计",
            position=NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.freqAnalyzerInterface,
            QIcon(":app/icons/Analysis.svg"),
            "语料分析",
            position=NavigationItemPosition.TOP,
        )

        self.addSubInterface(
            self.taskInterface,
            QIcon(":app/icons/Task.svg"),
            "任务管理",
            position=NavigationItemPosition.BOTTOM,
        )
        self.addSubInterface(
            self.settingInterface,
            QIcon(":app/icons/Setting.svg"),
            "设置",
            position=NavigationItemPosition.BOTTOM,
        )
        self.splashScreen.finish()
        logger.info("[MainWindow] 导航界面初始化完成")

    def initWindow(self):
        logger.info("[MainWindow] 开始初始化窗口设置")
        self.resize(1250, 750)
        self.setMinimumWidth(900)
        self.setMinimumHeight(700)
        self.setWindowIcon(QIcon(":app/images/logo.png"))
        self.setWindowTitle("棱溯客户端")

        logger.debug("[MainWindow] 已设置窗口基本属性")

        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.raise_()
        logger.debug("[MainWindow] 已创建并设置启动屏幕")

        desktop = QApplication.primaryScreen().availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)
        logger.debug("[MainWindow] 已移动窗口到屏幕中心")

        self.show()
        QApplication.processEvents()
        logger.info("[MainWindow] 窗口初始化完成并显示")

    def closeEvent(self, event):
        """窗口关闭事件"""
        # 检查是否有进行中或等待中的任务
        pendingTasks = taskControl.getTasksByStatus("pending")
        inProgressTasks = taskControl.getTasksByStatus("in_progress")

        totalTasks = len(pendingTasks) + len(inProgressTasks)

        if totalTasks > 0:
            # 构建任务详情
            taskDetails = []
            if pendingTasks:
                taskDetails.append(f"等待中: {len(pendingTasks)} 个")
            if inProgressTasks:
                taskDetails.append(f"进行中: {len(inProgressTasks)} 个")
            detailText = "，".join(taskDetails)

            msgBox = MessageBox(
                "确认退出",
                f"有 {totalTasks} 个下载任务尚未完成\n\n{detailText}\n\n退出将取消这些任务，确定退出吗？",
                self,
            )
            msgBox.yesButton.setText("退出")
            msgBox.cancelButton.setText("取消")

            if msgBox.exec():
                # 用户确认退出，停止所有任务
                from app.core.services import taskManager

                taskManager.stopAllTasks()
                logger.info("[MainWindow] 用户确认退出，停止所有任务")
                # 刷新分词缓存到磁盘
                self._flushTokenCacheOnExit()
                event.accept()
            else:
                # 用户取消退出
                event.ignore()
                logger.info("[MainWindow] 用户取消退出")
        else:
            # 刷新分词缓存到磁盘
            self._flushTokenCacheOnExit()
            event.accept()

    def _flushTokenCacheOnExit(self):
        """退出前刷新分词缓存到磁盘,避免丢失未写入的 token 缓存"""
        try:
            if (
                hasattr(self, "freqAnalyzerInterface")
                and self.freqAnalyzerInterface is not None
            ):
                store = getattr(self.freqAnalyzerInterface, "corpusStore", None)
                if store is not None and hasattr(store, "flushTokenCache"):
                    store.flushTokenCache(maxWait=1.0)
        except Exception as e:
            logger.warning(f"[MainWindow] 刷新 token cache 失败: {e}")
