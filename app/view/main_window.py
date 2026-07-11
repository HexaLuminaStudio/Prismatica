# coding: utf-8

import os
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
from app.core.plugin import getPluginManager, PluginMetadata
from .widgets.titlebar_widget import CustomTitleBar
from .hsk_interface import HskInterface
from .global_interface import GlobalInterface
from .bias_interface import BiasInterface
from .plugin_interface import PluginInterface
from .task_interface import TaskInterface
from .setting_interface import SettingInterface


class MainWindow(MSFluentWindow):

    def __init__(self):
        logger.info("开始初始化主窗口")
        super().__init__()
        setThemeColor("#00b09c")
        self.setTitleBar(CustomTitleBar(self))
        self.initWindow()

        self.hskInterface = HskInterface(self)
        self.globalInterface = GlobalInterface(self)
        self.biasInterface = BiasInterface(self)
        self.pluginInterface = PluginInterface(self)
        self.taskInterface = TaskInterface(self)
        self.settingInterface = SettingInterface(self)

        # 插件界面管理
        self._pluginInterfaces = {}  # pluginId -> widget
        self._pluginInsertIndex = None  # 插件插入位置索引

        self.connectSignalToSlot()

        self.initNavigation()
        self._initPluginManager()
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
            self.pluginInterface,
            QIcon(":app/icons/Plugin.svg"),
            "插件管理",
            position=NavigationItemPosition.BOTTOM,
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
        logger.info("导航界面初始化完成")

    def initWindow(self):
        logger.info("开始初始化窗口设置")
        self.resize(1000, 750)
        self.setMinimumWidth(700)
        self.setMinimumHeight(700)
        self.setWindowIcon(QIcon(":app/images/logo.png"))
        self.setWindowTitle("棱溯客户端")

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

    def _initPluginManager(self):
        """初始化插件管理器，注册回调"""
        manager = getPluginManager()
        manager.registerCallback("enable", self._onPluginEnabled)
        manager.registerCallback("disable", self._onPluginDisabled)

        # 加载已启用的插件界面
        self._loadEnabledPluginInterfaces()

    def _loadEnabledPluginInterfaces(self):
        """加载所有已启用的插件界面"""
        # 记录插件应该插入的位置（在 Global下载 之后）
        if self._pluginInsertIndex is None:
            self._pluginInsertIndex = (
                len(self.navigationInterface.items) - 1
            )  # 底部导航项之前
            logger.debug(f"[MainWindow] 插件插入位置: {self._pluginInsertIndex}")

        manager = getPluginManager()
        for metadata in manager.getEnabledPlugins():
            self._addPluginInterface(metadata)

    def _getPluginIcon(self, metadata: PluginMetadata) -> QIcon:
        """获取插件图标"""
        iconPath = metadata.iconPath

        if iconPath and os.path.exists(iconPath):
            return QIcon(iconPath)

        return QIcon(":app/icons/Plugin.svg")

    def _addPluginInterface(self, metadata: PluginMetadata):
        """添加插件界面到主窗口"""
        pluginId = metadata.pluginId

        if pluginId in self._pluginInterfaces:
            logger.debug(f"[MainWindow] 插件界面已存在: {pluginId}")
            return

        # 获取插件界面
        pluginInstance = metadata.instance
        if not pluginInstance:
            logger.warning(f"[MainWindow] 插件实例不存在: {pluginId}")
            return

        interface = pluginInstance.getInterface()
        if interface is None:
            logger.debug(f"[MainWindow] 插件无界面: {pluginId}")
            return

        # 设置对象名
        interface.setObjectName(f"Plugin_{pluginId}")

        # 获取图标
        icon = self._getPluginIcon(metadata)

        # 动态获取插入位置（插件插入在 Global下载 之后，底部导航之前）
        if self._pluginInsertIndex is None:
            self._pluginInsertIndex = self.navigationInterface.count() - 1

        # 在插件列表末尾插入
        self.addSubInterface(
            interface,
            icon,
            metadata.name,
            position=NavigationItemPosition.TOP,
        )

        self._pluginInterfaces[pluginId] = interface
        logger.info(f"[MainWindow] 添加插件界面: {metadata.name}")

    def _removePluginInterface(self, pluginId: str):
        """从主窗口移除插件界面"""
        if pluginId not in self._pluginInterfaces:
            return

        interface = self._pluginInterfaces.pop(pluginId)
        objectName = interface.objectName()

        # 从 stackedWidget 移除
        for i in range(self.stackedWidget.count()):
            if self.stackedWidget.widget(i) == interface:
                self.stackedWidget.removeWidget(interface)
                break

        # 从 navigationInterface 移除
        navInterface = self.navigationInterface

        # 通过 objectName 查找并移除导航项
        items_to_remove = []
        for item_key, item_info in navInterface.items.items():
            if item_key == objectName:
                items_to_remove.append(item_key)

        for item_key in items_to_remove:
            navInterface.removeWidget(item_key)

        logger.info(f"[MainWindow] 移除插件界面: {pluginId}")

    def _onPluginEnabled(self, metadata: PluginMetadata):
        """插件启用回调"""
        self._addPluginInterface(metadata)

    def _onPluginDisabled(self, metadata: PluginMetadata):
        """插件禁用回调"""
        self._removePluginInterface(metadata.pluginId)

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
                event.accept()
            else:
                # 用户取消退出
                event.ignore()
                logger.info("[MainWindow] 用户取消退出")
        else:
            event.accept()
