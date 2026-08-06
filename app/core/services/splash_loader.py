# coding: utf-8
"""
启动加载服务(Splash Loader)

职责:
    - 显示一个 splash 等待窗口,让用户在主窗口构造期间有即时视觉反馈
    - 协调 MainWindow 的构造过程,期间穿插 QApplication.processEvents()
      保证 splash 动画流畅 + 用户不感知卡顿
    - 主窗口构造完成后,通知 splash finish() 自动淡出销毁

设计权衡:
    - 这里**没有**把 MainWindow 的构造放到子线程。
      QWidget 等 GUI 对象虽然可以在子线程创建,但通过 moveToThread
      转回主线程后,字体 / 主题 / 子组件事件绑定等仍可能存在跨线程
      访问风险,实际项目中容易出现奇怪崩溃。
    - 采用更稳妥的方案:在主线程构造 MainWindow,但分阶段处理,
      每个阶段之间 processEvents() 让出主线程,splash 才能渲染动画
      并响应用户操作(关掉 splash)。
    - 同时支持:若外部希望走子线程方案,可将 buildInThread 设为 True。
      当前默认 False(主线程 + processEvents)。

使用方式(参见 main.py):
    loader = SplashLoader(splashWindow)
    loader.start()              # 启动 splash + 异步加载
    # loader.mainWindowReady.connect(...) 在主窗口就绪后展示
"""

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from app.core.utils import log


class SplashLoader(QObject):
    """Splash 加载协调器。

    信号:
        mainWindowReady(object)  - MainWindow 已构造好,可在主线程 show()
        loadFailed(object)       - 构造失败,携带异常对象
        progressChanged(int, str) - 进度更新(已自动转发到 splash)
    """

    mainWindowReady = Signal(object)
    loadFailed = Signal(object)
    progressChanged = Signal(int, str)
    # 启动彻底完成:外部告知"可以显示主窗口了"后,
    # loader 会先让 splash 淡出,再触发 startupCompleted。
    # main.py 收到该信号后才真正调用 window.show(),
    # 避免 splash 淡出与主窗口 show 之间出现未初始化完成的窗口闪现。
    startupCompleted = Signal()

    def __init__(self, splashWindow=None, buildInThread: bool = False):
        """初始化加载器。

        Args:
            splashWindow: 已创建好的 SplashWindow 实例。
            buildInThread: 是否在子线程构造 MainWindow。默认 False(主线程 + processEvents,
                          更稳定);若为 True,则在子线程构造并 moveToThread 转回主线程。
        """
        super().__init__()
        self._splash = splashWindow
        self._buildInThread = buildInThread
        self._started = False
        # 订阅 splash 的 fadedOut 信号,等 splash 真正销毁后才放行
        # startupCompleted。这样外部在 main.py 中收到信号再 window.show()
        # 时,splash 已不存在,不会出现「主窗口与 splash 淡出残留」重叠。
        if splashWindow is not None:
            try:
                splashWindow.fadedOut.connect(self._onSplashFadedOut)
            except Exception:
                log.debug("[SplashLoader] 订阅 splash.fadedOut 失败(可能已销毁)")
        self._pendingStartupCompleted = False

    # ------------------------------------------------------------------
    # 进度上报
    # ------------------------------------------------------------------
    def _reportProgress(self, pct: int, text: str) -> None:
        """转发到 splash + 外部监听者。
        由 MainWindow 内部调用,必须线程安全。
        """
        if self._splash is not None:
            try:
                self._splash.setProgress(int(pct), str(text))
            except Exception:
                log.debug("[SplashLoader] splash.setProgress 失败(可能已销毁)")
        try:
            self.progressChanged.emit(int(pct), str(text))
        except (RuntimeError, AttributeError):
            pass

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动加载流程。立即返回,不阻塞调用者。"""
        if self._started:
            log.warning("[SplashLoader] 已启动,忽略重复 start()")
            return
        self._started = True

        if self._buildInThread:
            self._startInThread()
        else:
            self._startInMainThread()

    def _startInMainThread(self) -> None:
        """主线程构造方案:分阶段构造 + 阶段间 processEvents。

        注意:此方案下 'start()' 立即返回,实际构造由一个 QTimer.singleShot(0, ...)
        在下一轮事件循环中触发。这样保证调用方在 start() 之后还能继续初始化
        splash 的事件监听。
        """
        from PySide6.QtCore import QTimer

        # 用 0 延迟触发,确保 start() 立即返回;splash 才会立刻拿到事件循环
        QTimer.singleShot(0, self._buildMainWindowSafely)

    def _buildMainWindowSafely(self) -> None:
        """主线程中安全构造 MainWindow。

        - 全程 try/except,失败通过 loadFailed 信号通知外部
        - 每个阶段后 processEvents() 让 splash 能渲染动画
        - 构造完成后**不**立即关闭 splash,而是保持 splash 显示,
          等待外部调用 notifyStartupCompleted() 通知「可以显示了」。
          收到通知后,loader 会先让 splash 淡出,再触发 startupCompleted。
        """
        try:
            from app.view.main_window import MainWindow

            # 主窗口构造从 30% 开始(由 MainWindow 内部 _reportProgress 推进到 95%)。
            # 在此之前先推进到 28%,留 2% 的 gap 让动画从外部衔接过来。
            self._reportProgress(28, "开始构造主窗口…")
            self._processEventsBriefly()
            mainWindow = MainWindow(
                progressCallback=self._reportProgress,
                startHidden=True,  # 关键:构造期间隐藏主窗口,避免闪现
            )

            # 阶段 2:构造完毕,主动再 processEvents 一次,让 splash 看到 100%
            self._processEventsBriefly()

            # 注意:此处**不**调 splash.finish(),仅通知外部主窗口已就绪,
            # 等 main.py 在合适时机调用 loader.notifyStartupCompleted() 后
            # 才会真正淡出 splash 并触发 startupCompleted 信号。
            self.mainWindowReady.emit(mainWindow)
        except Exception as e:
            log.exception(f"[SplashLoader] MainWindow 构造失败: {e}")
            self._handleBuildError(e)

    def notifyStartupCompleted(self) -> None:
        """外部(main.py)在确认所有初始化已彻底完成后调用。

        流程:
            1) splash 显示「启动完成」并开始淡出(内部 fade 动画)
            2) 标记 _pendingStartupCompleted=True
            3) 等待 splash 的 fadedOut 信号(淡出+销毁后触发)
            4) 真正发出 startupCompleted,此时 main.py 再 window.show()

        这样保证:
            - 主窗口出现时 splash 已彻底销毁,两个窗口不会同时可见
            - 不会出现「splash 淡出残留与主窗口并排」或「splash 盖在
              主窗口上 220ms 残留」的情况
        """
        self._pendingStartupCompleted = True
        if self._splash is not None:
            try:
                self._splash.finish()
            except Exception:
                log.debug("[SplashLoader] splash.finish 调用失败(可能已销毁)")
                # splash 已无法 fadeOut(可能已销毁),直接放行 startupCompleted,
                # 避免主流程被卡死。
                self._pendingStartupCompleted = False
                try:
                    self.startupCompleted.emit()
                except (RuntimeError, AttributeError):
                    pass
        else:
            # 没有 splash,直接放行
            self._pendingStartupCompleted = False
            try:
                self.startupCompleted.emit()
            except (RuntimeError, AttributeError):
                pass

    def _onSplashFadedOut(self) -> None:
        """splash 真正淡出+销毁后由 SplashWindow.fadedOut 信号触发。

        仅当之前调用过 notifyStartupCompleted(标记了 _pendingStartupCompleted)
        才放行 startupCompleted,避免误触发。
        """
        if not self._pendingStartupCompleted:
            return
        self._pendingStartupCompleted = False
        try:
            self.startupCompleted.emit()
        except (RuntimeError, AttributeError):
            pass

    # ------------------------------------------------------------------
    # 子线程方案(可选,默认不启用)
    # ------------------------------------------------------------------
    def _startInThread(self) -> None:
        """子线程构造方案。需要 MainWindow 在子线程中安全创建,
        通常需要额外的封装;此处保留入口以备未来扩展。
        """
        from PySide6.QtCore import QThread

        class _Builder(QObject):
            def __init__(outerSelf, loader: "SplashLoader"):
                super().__init__()
                outerSelf._loader = loader

            def run(outerSelf) -> None:
                try:
                    from app.view.main_window import MainWindow

                    outerSelf._loader._reportProgress(15, "初始化主窗口框架")
                    QApplication.processEvents()

                    window = MainWindow(
                        progressCallback=outerSelf._loader._reportProgress,
                        startHidden=True,  # 构造期间隐藏,等 startupCompleted 后再 show
                    )
                    # 转回主线程,后续 show()/GUI 操作必须在主线程执行
                    mainThread = QApplication.instance().thread()
                    if mainThread is not None:
                        window.moveToThread(mainThread)

                    # 注意:此处不主动关闭 splash,等待外部 notifyStartupCompleted
                    outerSelf._loader.mainWindowReady.emit(window)
                except Exception as e:
                    log.exception(f"[SplashLoader](子线程) MainWindow 构造失败: {e}")
                    outerSelf._loader._handleBuildError(e)

        thread = QThread()
        builder = _Builder(self)
        builder.moveToThread(thread)
        thread.started.connect(builder.run)
        builder.destroyed.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _processEventsBriefly(self) -> None:
        """短暂处理一次事件,让 splash 能渲染动画 + 接收鼠标事件。

        不使用 sleep 之类阻塞,避免影响 UI 响应性。
        """
        try:
            QApplication.processEvents()
        except Exception:
            pass

    def _handleBuildError(self, exc: Exception) -> None:
        """构造失败:推进 splash 到 100%(显示失败文案)+ 通知外部。

        兜底逻辑:
            - 先尝试 setProgress(100, ...) 显示失败文案
            - 即使 setProgress 抛异常,也尽量执行 finish(),确保 splash 不再占用屏幕
            - 任意失败都被吞掉(loadFailed 信号已足够承载错误信息)
        """
        if self._splash is not None:
            try:
                self._splash.setProgress(100, f"启动失败: {exc}")
            except Exception:
                pass
            try:
                self._splash.finish()
            except Exception:
                pass
        try:
            self.loadFailed.emit(exc)
        except (RuntimeError, AttributeError):
            pass
