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
from app.core.services import taskManager
from .widgets.titlebar_widget import CustomTitleBar
from .hsk_interface import HskInterface
from .global_interface import GlobalInterface
from .bias_interface import BiasInterface
from .freq_analyzer_interface import FreqAnalyzerInterface
from .task_interface import TaskInterface
from .chat_interface import ChatInterface
from .setting_interface import SettingInterface
from .project_interface import ProjectInterface


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
        self.chatInterface = ChatInterface(self)
        self.settingInterface = SettingInterface(self)
        # PRD-002:项目管理子界面(REQ-PROJ-001)
        self.projectInterface = ProjectInterface(self)
        # 仪表盘的「跳转分析模块」信号 → 切到对应分析页(目前所有资源类型
        # 都映射到 FreqAnalyzerInterface,后续按 moduleKey 分流)
        self.projectInterface.jumpToModule.connect(self._onProjectJumpToModule)

        self.connectSignalToSlot()

        self.initNavigation()
        logger.info("[MainWindow] 主窗口初始化完成")

    def connectSignalToSlot(self):
        logger.debug("[MainWindow] 连接信号和槽")
        # PRD-002:顶栏项目切换器的「项目管理」入口 → 切到项目管理页
        # 「新建项目」入口 → 弹出 NewProjectDialog
        try:
            from app.view.widgets.project_manager_dialogs import NewProjectDialog
            from qfluentwidgets import InfoBar, InfoBarPosition

            switcher = getattr(self.titleBar, "projectSwitcher", None)
            if switcher is not None:
                switcher.manageRequested.connect(self._onProjectManageRequested)
                switcher.newRequested.connect(self._onProjectNewRequested)
        except Exception as e:
            logger.warning(f"[MainWindow] 连接项目切换器信号失败: {e}")

    def _onProjectManageRequested(self) -> None:
        """用户从顶栏下拉选「项目管理」入口 — 切到项目管理页"""
        try:
            self.switchTo(self.projectInterface)
        except Exception:
            # 旧版本 qfluentwidgets 可能没有 switchTo,改用 stackedWidget 路由
            try:
                self.stackedWidget.setCurrentWidget(self.projectInterface)
            except Exception as e:
                logger.warning(f"[MainWindow] 切换到项目管理页失败: {e}")

    def _onProjectNewRequested(self) -> None:
        """用户从顶栏下拉选「新建项目」入口 — 弹窗 + 创建"""
        try:
            from app.view.widgets.project_manager_dialogs import NewProjectDialog
            from app.core.services import projectManager
            from qfluentwidgets import InfoBar, InfoBarPosition

            dialog = NewProjectDialog(self)
            if not dialog.exec():
                return
            result = dialog.getResult()
            name = result["name"]
            template = result["template"]
            description = result["description"]
            # 进入「创建中」状态
            self._setProjectCreatingState(True, name)
            # 异步创建(磁盘 I/O 在子线程,不阻塞 UI)
            projectManager.createProjectAsync(
                name=name,
                template=template,
                description=description,
                onSuccess=self._onMainProjectCreated,
                onError=self._onMainProjectCreateFailed,
            )
        except Exception as e:
            logger.warning(f"[MainWindow] 新建项目入口失败: {e}")
            self._setProjectCreatingState(False)

    def _setProjectCreatingState(self, creating: bool, name: str = "") -> None:
        """切换顶栏入口的「创建中」状态(避免重复触发)。"""
        try:
            switcher = getattr(self.titleBar, "projectSwitcher", None)
            if switcher is not None and hasattr(switcher, "setBusy"):
                switcher.setBusy(creating)
            if creating:
                from qfluentwidgets import InfoBar, InfoBarPosition

                self._creatingInfoBar = InfoBar.info(
                    title="正在创建",
                    content=f"正在创建项目「{name}」,请稍候…",
                    parent=self,
                    duration=-1,
                    position=InfoBarPosition.TOP,
                )
            else:
                bar = getattr(self, "_creatingInfoBar", None)
                if bar is not None:
                    try:
                        bar.close()
                    except Exception:
                        pass
                    self._creatingInfoBar = None
        except Exception as e:
            logger.warning(f"[MainWindow] _setProjectCreatingState 异常: {e}")

    def _onMainProjectCreated(self, project) -> None:
        """异步创建成功回调(主线程)。"""
        from qfluentwidgets import InfoBar, InfoBarPosition

        self._setProjectCreatingState(False)
        InfoBar.success(
            title="已创建",
            content=f"项目「{project.name}」已创建并自动激活",
            parent=self,
            duration=2500,
            position=InfoBarPosition.TOP,
        )
        # 创建后自动切到项目管理页查看
        self._onProjectManageRequested()

    def _onMainProjectCreateFailed(self, errMsg: str) -> None:
        """异步创建失败回调(主线程)。"""
        from qfluentwidgets import InfoBar, InfoBarPosition

        self._setProjectCreatingState(False)
        logger.warning(f"[MainWindow] 新建项目失败: {errMsg}")
        InfoBar.error(
            title="创建失败",
            content=errMsg,
            parent=self,
            duration=3000,
            position=InfoBarPosition.TOP,
        )

    def _onProjectJumpToModule(self, moduleKey: str) -> None:
        """仪表盘点击「跳转分析模块」 → 路由到对应分析子界面。

        MVP 阶段所有分析资源都映射到 FreqAnalyzerInterface;
        后续按 moduleKey 分流(如 "bias" → BiasInterface 等)。
        """
        try:
            if moduleKey == "freq_analyzer" or not moduleKey:
                target = self.freqAnalyzerInterface
            elif moduleKey == "bias":
                target = self.biasInterface
            elif moduleKey == "hsk":
                target = self.hskInterface
            elif moduleKey == "global":
                target = self.globalInterface
            else:
                # 未知 moduleKey — fallback 到 freq_analyzer
                target = self.freqAnalyzerInterface
            try:
                self.switchTo(target)
            except Exception:
                try:
                    self.stackedWidget.setCurrentWidget(target)
                except Exception as e:
                    logger.warning(f"[MainWindow] 跳转到 {moduleKey} 失败: {e}")
        except Exception as e:
            logger.warning(f"[MainWindow] _onProjectJumpToModule 失败: {e}")

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
            self.chatInterface,
            QIcon(":app/icons/Chat.svg"),
            "AI 聊天",
            position=NavigationItemPosition.SCROLL,
        )

        # PRD-002:项目管理(REQ-PROJ-001)— 低频操作,放 SCROLL
        # 注:MVP 阶段复用 Save.svg 作为项目图标(项目 = 已保存的研究单元),
        # 后续若新增 Folder.svg 图标可在此替换。
        self.addSubInterface(
            self.projectInterface,
            QIcon(":app/icons/Save.svg"),
            "项目管理",
            position=NavigationItemPosition.SCROLL,
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
        self.resize(1250, 850)
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
        # P0-A1 fix 2026-07-18:不再直接调 taskControl,改走 TaskManager 高阶接口
        pendingTasks = taskManager.getPendingTasksFromDb()
        inProgressTasks = taskManager.getInProgressTasks()

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
                # 用户确认退出,停止所有任务
                # P0-fix 2026-07-18:不要再写 `from app.core.services import taskManager`,
                # 那会让 taskManager 在整个 closeEvent 内变成 local,
                # 导致 line 119-120 的 taskManager.getPendingTasksFromDb() 报 UnboundLocalError。
                # 模块顶部已经导入过,直接用即可。
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
