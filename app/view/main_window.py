# coding: utf-8

from PySide6.QtCore import QEasingCurve, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget
from qfluentwidgets import (
    FluentIcon,
    NavigationItemPosition,
    SplashScreen,
    MSFluentWindow,
    setThemeColor,
    MessageBox,
)

from app.core.utils import signalBus, logger
from app.core.utils.config import cfg, qconfig
from app.core.services import taskManager
from .widgets.titlebar_widget import CustomTitleBar
from .widgets.prismatica_navigation import PrismaticaNavigationBar
from .widgets.prismatica_theme import shellPalette
from .hsk_interface import HskInterface
from .hsk_corpus_interface import HskCorpusInterface
from .global_interface import GlobalInterface
from .bias_interface import BiasInterface
from .freq_analyzer_interface import FreqAnalyzerInterface
from .task_interface import TaskInterface
from .chat_interface import ChatInterface
from .setting_interface import SettingInterface
from .project_interface import ProjectInterface
from .widgets.account.login_dialog import LoginInterface


class MainWindow(MSFluentWindow):

    def __init__(self, progressCallback=None, startHidden: bool = False):
        """主窗口构造。

        Args:
            progressCallback: 可选进度回调,签名 (pct: int, text: str) -> None。
                              启动 splash 流程时会传入,用于上报各阶段加载进度;
                              普通调用方无需关心,保持 None 即可。
            startHidden: 是否在构造期间隐藏窗口(不自动 show())。
                        - True: 用于 splash 流程 — 构造期间窗口隐藏,
                          由外部在启动彻底完成后调用 _showAfterStartup() 显示,
                          避免「主窗口闪现未初始化状态」。
                        - False(默认): 兼容旧调用方 — initWindow() 内会照常
                          调用 self.show()。
        """
        self._progressCallback = progressCallback
        self._startHidden = bool(startHidden)
        self._startupShown = False  # 是否已通过 _showAfterStartup() 显示过
        # 项目管理页「锁定态」:AI 报告生成期间锁住页面交互,
        # 此时不允许通过导航栏离开项目管理页,也不允许直接关闭主窗口。
        self._projectBusy: bool = False
        # 主窗口构造区间:30% ~ 95%(由 SplashLoader 协调,前面 0~30% 是
        # 项目预热 / 许可证 / 引导窗口)。拆成多个小步,每个子界面独立上报,
        # 让 splash 进度条在追赶动画下能看到数字持续变化。
        self._reportProgress(30, "初始化主窗口框架")
        super().__init__()
        setThemeColor("#00b09c")
        self._configurePrismaticaShell()
        self._installPrismaticaNavigation()
        self.setTitleBar(CustomTitleBar(self))
        self.initWindow()
        self._reportProgress(35, "构造 HSK 下载界面")
        self.hskInterface = HskInterface(self)

        self._reportProgress(38, "构造 HSK 作文检索界面")
        self.hskCorpusInterface = HskCorpusInterface(self)

        self._reportProgress(40, "构造全球中介下载界面")
        self.globalInterface = GlobalInterface(self)

        self._reportProgress(45, "构造偏误统计界面")
        self.biasInterface = BiasInterface(self)

        self._reportProgress(50, "构造词频分析界面")
        self.freqAnalyzerInterface = FreqAnalyzerInterface(self)

        self._reportProgress(58, "构造任务管理界面")
        self.taskInterface = TaskInterface(self)

        self._reportProgress(65, "构造 AI 聊天界面")
        self.chatInterface = ChatInterface(self)

        self._reportProgress(72, "构建设置界面")
        self.settingInterface = SettingInterface(self)

        # 认证是主窗口内的独立业务页面，不再使用模态登录弹窗。
        self.loginInterface = LoginInterface(self)
        self.loginInterface.loginSucceeded.connect(self._onLoginSucceeded)
        self._accountReturnInterface: QWidget | None = None

        # PRD-002:项目管理子界面(REQ-PROJ-001)
        self._reportProgress(80, "构造项目管理界面")
        self.projectInterface = ProjectInterface(self)
        # 仪表盘的「跳转分析模块」信号 → 切到对应分析页(目前所有资源类型
        # 都映射到 FreqAnalyzerInterface,后续按 moduleKey 分流)
        self.projectInterface.jumpToModule.connect(self._onProjectJumpToModule)

        self._reportProgress(85, "绑定信号与项目切换器")
        self.connectSignalToSlot()

        self._reportProgress(90, "加载导航菜单")
        self.initNavigation()
        self._reportProgress(95, "准备完成")

    def _configurePrismaticaShell(self) -> None:
        """统一主窗口、内容画布与导航条的材质层级。"""
        self.setMicaEffectEnabled(False)
        self.stackedWidget.setObjectName("prismaticaContentSurface")
        self.stackedWidget.view.setObjectName("prismaticaPageStack")
        self._applyShellTheme()
        qconfig.themeChangedFinished.connect(self._applyShellTheme)

    def _applyShellTheme(self) -> None:
        palette = shellPalette()
        self.setCustomBackgroundColor(palette.window, palette.window)
        content = palette.content
        border = palette.border
        self.stackedWidget.setStyleSheet(
            "QFrame#prismaticaContentSurface {"
            f"background-color: rgba({content.red()}, {content.green()}, "
            f"{content.blue()}, 246);"
            f"border: 1px solid {border.name()};"
            "border-right: none; border-bottom: none;"
            "border-top-left-radius: 16px;"
            "}"
        )
        self.stackedWidget.view.setStyleSheet(
            "QStackedWidget#prismaticaPageStack {"
            "background: transparent; border: none;"
            "}"
        )
        self.update()

    def _installPrismaticaNavigation(self) -> None:
        """用品牌侧边栏替换 MSFluentWindow 默认导航条。"""
        oldNavigation = self.navigationInterface
        newNavigation = PrismaticaNavigationBar(self)
        self.hBoxLayout.replaceWidget(oldNavigation, newNavigation)
        oldNavigation.hide()
        oldNavigation.setParent(None)
        oldNavigation.deleteLater()
        self.navigationInterface = newNavigation

    def switchTo(self, interface: QWidget) -> None:
        """使用与侧边栏一致的快速减速节奏切换业务页面。"""
        current = self.stackedWidget.currentWidget()
        if current is interface:
            return
        view = self.stackedWidget.view
        # 认证页包含真实输入控件。位置型页面动画在 Windows 合成器下可能让
        # 控件绘制坐标与命中区域短暂不同步，因此进入/离开认证页时直接切换。
        if interface is self.loginInterface or current is self.loginInterface:
            QStackedWidget.setCurrentWidget(view, interface)
            return
        if QApplication.isEffectEnabled(Qt.UIEffect.UI_General):
            view.setCurrentWidget(
                interface,
                needPopOut=False,
                showNextWidgetDirectly=True,
                duration=220,
                easingCurve=QEasingCurve.Type.OutCubic,
            )
            return
        QStackedWidget.setCurrentWidget(view, interface)

    def _reportProgress(self, pct: int, text: str) -> None:
        """上报当前加载阶段。

        - 仅在传入了 progressCallback 时生效,普通调用完全无副作用
        - 用 try/except 包住,避免回调异常中断主窗口构造
        """
        cb = self._progressCallback
        if cb is None:
            return
        try:
            cb(int(pct), str(text))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[MainWindow] 进度回调异常: {e}")

    def connectSignalToSlot(self):
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

        # 项目管理页 busy 状态(AI 报告生成)→ 锁定导航切换 + 拦截关闭
        try:
            self.projectInterface.busyChanged.connect(self._onProjectBusyChanged)
            # 监听 stackedWidget 切换:若 busy 时被切到非项目管理页,自动拉回
            self.stackedWidget.currentChanged.connect(self._onStackCurrentChanged)
        except Exception as e:
            logger.warning(f"[MainWindow] 连接项目管理 busy 信号失败: {e}")

        # PRD-003:批量下载完成后跳转请求 → 切到对应子界面
        # 子界面通过 signalBus.navigateToSubInterface.emit("TaskInterface") 触发
        try:
            signalBus.navigateToSubInterface.connect(self._onNavigateToSubInterface)
        except Exception as e:
            logger.warning(f"[MainWindow] 连接导航信号失败: {e}")

        # 2026-08-07 P0-A(M11/M13):云端会话 / 余额 / 设备变化 → 通知 accountNav
        try:
            signalBus.sessionChanged.connect(self._onCloudSessionChanged)
            signalBus.balanceChanged.connect(self._onCloudBalanceChanged)
            signalBus.maxDevicesReached.connect(self._onMaxDevicesReached)
        except Exception as e:
            logger.warning(f"[MainWindow] 连接云端信号失败: {e}")

    def _onProjectBusyChanged(self, busy: bool) -> None:
        """项目管理页 AI 报告生成状态变化。"""
        self._projectBusy = bool(busy)
        logger.info(f"[MainWindow] projectBusy={busy}")

    def _onStackCurrentChanged(self, index: int) -> None:
        """stackedWidget 切换时检查:若 _projectBusy 且切到非项目管理页,拉回。

        qfluentwidgets 的导航栏在用户点击其他模块时会调用
        ``stackedWidget.setCurrentWidget(...)``,这里在 currentChanged
        钩子里拦截,保证用户在 AI 报告生成中无法离开项目管理页。
        """
        if not self._projectBusy:
            return
        try:
            currentWidget = self.stackedWidget.widget(index)
            if currentWidget is self.projectInterface:
                return
            # 切到了其他页面 → 拉回项目管理页 + 提示用户
            from qfluentwidgets import InfoBar, InfoBarPosition

            self.stackedWidget.setCurrentWidget(self.projectInterface)
            InfoBar.warning(
                title="AI 报告生成中",
                content="请等待当前报告生成完成,期间无法离开项目管理页。",
                parent=self,
                duration=2500,
                position=InfoBarPosition.TOP,
            )
            logger.info(
                f"[MainWindow] busy 中拦截切页 → 已拉回 projectInterface (was idx={index})"
            )
        except Exception as e:
            logger.warning(f"[MainWindow] busy 拦截切页失败: {e}")

    # ------------------------------------------------------------------
    # 2026-08-07 P0-A(M11/M13):云端账户 / 余额 / 设备上限处理
    # ------------------------------------------------------------------

    def _openAccountPanel(self) -> None:
        """账户入口：已登录打开账户面板，未登录切换到认证页面。"""
        try:
            from app.core.services import getCloudAuth
            from app.view.widgets.account.account_panel import AccountPanel

            if getCloudAuth()._api.isLoggedIn():
                panel = AccountPanel(self)
                panel.exec()
            else:
                current = self.stackedWidget.currentWidget()
                if current is not self.loginInterface:
                    self._accountReturnInterface = current
                self.switchTo(self.loginInterface)
        except Exception as exc:
            logger.exception(f"[MainWindow] 打开账户面板失败: {exc}")

    def _onLoginSucceeded(self) -> None:
        try:
            self._onCloudSessionChanged(True)
            target = self._accountReturnInterface or self.hskInterface
            self.switchTo(target)
            self._accountReturnInterface = None
        except Exception:
            logger.exception("[MainWindow] 登录后恢复业务页面失败")

    def _onCloudSessionChanged(self, loggedIn: bool) -> None:
        if hasattr(self, "accountNav"):
            self.accountNav.setLoggedIn(bool(loggedIn))

    def _onCloudBalanceChanged(self, balance: int) -> None:
        if hasattr(self, "accountNav"):
            self.accountNav.setBalance(int(balance))

    def _onMaxDevicesReached(self, limit: int) -> None:
        from qfluentwidgets import InfoBar, InfoBarPosition

        try:
            InfoBar.warning(
                title="设备数量已达上限",
                content=f"已达 {limit} 台设备上限,请到「账户 → 设备」中撤销旧设备。",
                parent=self,
                duration=4000,
                position=InfoBarPosition.TOP,
            )
        except Exception:
            logger.exception("[MainWindow] maxDevices InfoBar 失败")

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

    def _onNavigateToSubInterface(self, objectName: str) -> None:
        """通用导航请求处理(PRD-003)。

        子界面通过 signalBus.navigateToSubInterface.emit("ObjectName") 触发,
        本方法根据 objectName 找到对应子界面并 switchTo。
        """
        try:
            target = self.findChild(QWidget, objectName)
            if target is None:
                logger.warning(f"[MainWindow] 找不到子界面: {objectName}")
                return
            try:
                self.switchTo(target)
            except Exception:
                try:
                    self.stackedWidget.setCurrentWidget(target)
                except Exception as e:
                    logger.warning(f"[MainWindow] 切换到 {objectName} 失败: {e}")
        except Exception as e:
            logger.warning(f"[MainWindow] _onNavigateToSubInterface 失败: {e}")

    def initNavigation(self):
        self.addSubInterface(
            self.hskInterface,
            FluentIcon.CLOUD_DOWNLOAD,
            "HSK下载",
            position=NavigationItemPosition.TOP,
        )
        # HSK 作文语料检索:与 HSK 下载同级,共用 Dictionary.svg
        self.addSubInterface(
            self.hskCorpusInterface,
            FluentIcon.DICTIONARY,
            "HSK作文检索",
            position=NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.globalInterface,
            FluentIcon.GLOBE,
            "全球中介下载",
            position=NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.biasInterface,
            FluentIcon.FLAG,
            "偏误统计",
            position=NavigationItemPosition.TOP,
        )
        self.addSubInterface(
            self.freqAnalyzerInterface,
            FluentIcon.PIE_SINGLE,
            "语料分析",
            position=NavigationItemPosition.TOP,
        )
        self.navigationInterface.addSectionHeader(
            "研究", NavigationItemPosition.SCROLL
        )
        self.addSubInterface(
            self.chatInterface,
            FluentIcon.CHAT,
            "AI 聊天",
            position=NavigationItemPosition.SCROLL,
        )

        # PRD-002:项目管理(REQ-PROJ-001)— 低频操作,放 SCROLL
        # 注:MVP 阶段复用 Save.svg 作为项目图标(项目 = 已保存的研究单元),
        # 后续若新增 Folder.svg 图标可在此替换。
        self.addSubInterface(
            self.projectInterface,
            FluentIcon.FOLDER,
            "项目管理",
            position=NavigationItemPosition.SCROLL,
        )

        self.navigationInterface.addSectionHeader(
            "系统", NavigationItemPosition.BOTTOM
        )
        self.taskNavButton = self.addSubInterface(
            self.taskInterface,
            FluentIcon.COMPLETED,
            "任务管理",
            position=NavigationItemPosition.BOTTOM,
        )

        self.addSubInterface(
            self.settingInterface,
            FluentIcon.SETTING,
            "设置",
            position=NavigationItemPosition.BOTTOM,
        )

        # 认证页由账户入口驱动，不额外占用一个普通导航按钮。
        self.loginInterface.setProperty("isStackedTransparent", False)
        self.stackedWidget.addWidget(self.loginInterface)

        # 2026-08-07 P0-A(M11):账户入口(M13 信号总线驱动头像状态切换)
        from app.view.widgets.account.account_nav import AccountNavWidget

        self.accountNav = AccountNavWidget(self)
        self.accountNav.setMaximumHeight(60)
        self.navigationInterface.addWidget(
            "accountNav",
            self.accountNav,
            position=NavigationItemPosition.BOTTOM,
        )
        # 把 accountNav 的点击信号转成主窗口页面或已登录账户面板。
        self.accountNav.clicked.connect(self._openAccountPanel)
        self._connectTaskNavigationBadge()

        self.splashScreen.finish()

    def _connectTaskNavigationBadge(self) -> None:
        """让任务角标展示真实的运行中与排队任务数量。"""
        for signal in (
            taskManager.taskCreated,
            taskManager.taskStarted,
            taskManager.taskCompleted,
            taskManager.taskFailed,
            taskManager.taskCancelled,
            taskManager.taskDeleted,
        ):
            signal.connect(self._refreshTaskNavigationBadge)
        self._refreshTaskNavigationBadge()

    def _refreshTaskNavigationBadge(self, *_args) -> None:
        button = getattr(self, "taskNavButton", None)
        if button is None or not hasattr(button, "setBadgeCount"):
            return
        try:
            count = len(taskManager.getRunningTasks()) + len(
                taskManager.getPendingTasks()
            )
            button.setBadgeCount(count)
        except Exception as exc:
            logger.warning(f"[MainWindow] 更新任务导航角标失败: {exc}")

    def initWindow(self):
        self.resize(1250, 850)
        self.setMinimumWidth(900)
        self.setMinimumHeight(700)
        self.setWindowIcon(QIcon(":app/images/logo.png"))
        self.setWindowTitle("棱溯客户端")


        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.raise_()

        desktop = QApplication.primaryScreen().availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)

        if not self._startHidden:
            # 兼容旧调用方:照常 show
            self.show()
            QApplication.processEvents()

    def _showAfterStartup(self) -> None:
        """启动彻底完成后由外部调用:显示主窗口。

        调用时机会在 main.py 中协调 — 通常是 SplashLoader 触发
        startupCompleted 信号后,主窗口已就绪 + splash 已淡出,
        此时再 show() 让用户「一步到位」看到完整主窗口,避免闪现。
        重复调用安全(只生效一次)。

        2026-07-27 调整:即使 SplashLoader 已 fadedOut 之后才调用,
        这里再做一次「主动抢焦点」+「清掉可能的 WindowStaysOnTopHint 残留」,
        进一步保证主窗口稳定显示在最前,避免极端场景下与 splash 残留窗口
        (即便已销毁)或系统弹窗出现层级混乱。
        """
        if self._startupShown:
            logger.debug("[MainWindow] _showAfterStartup 重复调用,忽略")
            return
        self._startupShown = True
        try:
            # 主动清掉最顶标志(主窗口不需要始终置顶,避免压住其他正常窗口)
            try:
                from PySide6.QtCore import Qt

                flags = self.windowFlags()
                if bool(flags & Qt.WindowType.WindowStaysOnTopHint):
                    self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
            except Exception:
                pass
            self.show()
            QApplication.processEvents()
            # raise_() 让窗口激活到前台(Win10 上 show() 不一定抢焦点)
            self.raise_()
            self.activateWindow()
            # 再 processEvents 一次,确保事件循环消化后窗口层级稳定
            QApplication.processEvents()
            # 主窗口首次进入引导遮罩:用半透明黑幕遮住主窗口,
            # 突出关键控件 + 展示说明文字,引导用户认识主界面。
            # - 仅当 cfg.MainTourShown=False 时弹出(默认未展示)
            # - 用户完成/跳过引导后由 MainTourOverlay 自身写 True,后续不再弹出
            # - 用 QTimer.singleShot 延迟到下一轮事件循环,确保主窗口布局完全稳定
            self._maybeStartMainTour()
        except Exception as e:
            logger.exception(f"[MainWindow] _showAfterStartup 显示失败: {e}")

    def _maybeStartMainTour(self) -> None:
        """检查并启动主窗口引导遮罩(若用户尚未完成)。"""
        try:
            shown = bool(qconfig.get(cfg.MainTourShown))
        except Exception as e:
            logger.warning(f"[MainWindow] 读取 MainTourShown 失败: {e}")
            shown = False

        if shown:
            logger.debug("[MainWindow] MainTourShown=True,跳过引导遮罩")
            return

        try:
            from PySide6.QtCore import QTimer

            from app.view.widgets.main_tour_overlay import MainTourOverlay

            def _start():
                try:
                    # 再次校验(防止用户在极小延迟内手动关闭了引导)
                    if bool(qconfig.get(cfg.MainTourShown)):
                        return
                    overlay = MainTourOverlay(self)
                    overlay.start()
                except Exception as e:
                    logger.warning(f"[MainWindow] 启动引导遮罩失败: {e}")

            # 延迟 600ms,让主窗口布局稳定 + 用户视觉过渡
            QTimer.singleShot(600, _start)
        except Exception as e:
            logger.warning(f"[MainWindow] 调度引导遮罩失败: {e}")

    def closeEvent(self, event):
        """窗口关闭事件"""
        # PRD-002:项目管理页 AI 报告生成期间禁止关闭主窗口(防止打断生成)
        if self._projectBusy:
            try:
                # 注意:不要在此局部导入 MessageBox —
                # 那会让 MessageBox 在本函数内成为 local,若 _projectBusy=False
                # 则下方 line ~555 使用 MessageBox 时触发 UnboundLocalError。
                # MessageBox 已在模块顶部导入,直接使用模块级即可。
                from qfluentwidgets import InfoBar, InfoBarPosition

                mb = MessageBox(
                    "AI 报告生成中",
                    "当前正在生成 AI 报告,关闭程序会中断生成过程。\n\n"
                    "确定要强制关闭吗?",
                    self,
                )
                mb.yesButton.setText("强制关闭")
                mb.cancelButton.setText("继续等待")
                if not mb.exec():
                    event.ignore()
                    InfoBar.warning(
                        title="已取消关闭",
                        content="请等待 AI 报告生成完成后再关闭。",
                        parent=self,
                        duration=2000,
                        position=InfoBarPosition.TOP,
                    )
                    logger.info("[MainWindow] busy 中用户取消关闭")
                    return
                logger.warning("[MainWindow] busy 中用户强制关闭,AI 报告将被打断")
            except Exception as e:
                logger.warning(f"[MainWindow] busy 拦截关闭异常(放行): {e}")

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
