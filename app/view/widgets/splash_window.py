# coding: utf-8
"""
启动等待页面(Splash Window)

设计目标:
    1. 程序启动后**立刻**(在 MainWindow 构建之前)显示一个独立的等待窗口,
       给用户明确的"软件正在启动"反馈,避免出现长时间黑屏 / 无响应。
    2. 主软件(MainWindow)在后台线程中初始化,完成后自动销毁等待页面。
    3. 等待页面关闭支持淡出动画,过渡更自然。

技术选型:
    - 独立 QWidget 而非 QDialog:避免 modality 阻塞主事件循环,
      让等待页面能在主线程中渲染动画 + 处理进度上报,
      而 MainWindow 的构建放在子线程中,真正实现「点击即响应」。
    - 居中显示 + 无边框 + 阴影;logo + 标题 + 进度条 + 动态文案。

进度上报:
    - 提供 setProgress(pct, text) 接口,主程序可分段通知当前阶段
    - 进度条颜色跟随主题色,与主窗口保持一致
"""

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QCloseEvent, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from app.core.utils import logger


# 主题色:与 MainWindow.setThemeColor("#00b09c") 保持一致
THEME_COLOR = QColor("#00b09c")
TEXT_COLOR_LIGHT = QColor("#202020")
TEXT_COLOR_DARK = QColor("#ffffff")
SUBTEXT_COLOR = QColor("#888888")
BACKGROUND_COLOR = QColor("#ffffff")

# 动画参数
FADE_OUT_DURATION_MS = 350
TICK_INTERVAL_MS = 60  # 进度条动画步进(更密更丝滑)
# 自由增长参数:在没有 setProgress 推进时,进度条自动缓慢爬升,
# 给用户「始终在加载」的视觉反馈,避免长时间停在某个数字上。
# 增长上限是「目标值以下 4 个百分点」,保证外部 stage 上报时仍能继续推进。
IDLE_TICK_MS = 220  # 自由增长定时器周期
IDLE_STEP = 1  # 每拍前进的百分点
IDLE_FLOOR_GAP = 4  # 距目标值的最大保留 gap
# 目标追赶参数:外部 setProgress 推进目标后,追赶速度上限
# 越小越绵密,但太密会看不清数字变化。
CHASE_STEP_MAX = 2  # 每帧最大追赶步进
CHASE_STEP_DIVISOR = 8  # 步进 = gap // divisor,越小追赶越慢


class SplashWindow(QWidget):
    """启动等待窗口。

    - 无边框、置顶、居中显示
    - 进度条 + 动态文案 + 旋转 logo(用 pixmap 简单脉动代替)
    - 提供 setProgress / setStage 给加载线程回调
    - finish() 触发淡出 + 关闭
    """

    # 进度变化信号(0~100),便于跨线程安全地更新 UI
    progressChanged = Signal(int, str)
    finished = Signal()
    # 真正淡出销毁后(在 _onFadeFinished 末尾)发出。
    # main.py / SplashLoader 监听此信号,确保主窗口 show() 在 splash 完全退场之后,
    # 避免「splash 淡出动画残留」与主窗口同时显示造成重叠冲突。
    fadedOut = Signal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        # 无边框 + 置顶 + 工具窗口(不在任务栏显示,避免抢主窗口任务栏入口)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.SplashScreen
            | Qt.WindowType.Tool
        )
        # 半透明背景,便于阴影透出
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 禁用右键菜单 / 系统菜单,等待窗口不接收用户交互
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._targetProgress = 0
        self._currentProgress = 0
        self._stageText = "正在准备…"
        self._isFinished = False
        self._isHeld = (
            False  # 修复(2026-08-05):hold/release 状态守卫,避免重复 hide/show
        )
        self._lastExternalProgressAt = 0.0  # 上次外部推进时间戳(秒)
        # 初始把目标设到 5,自由增长定时器会从 0 自动爬升,
        # 用户一打开 splash 就看到进度条在动,而不是停在 0 等几秒。
        self._targetProgress = 5

        self._buildUi()
        self._applyShadow()
        self._wireProgressAnimation()

        # 进度信号由外部跨线程触发,槽函数内部已做线程安全处理
        self.progressChanged.connect(self._onProgressChanged)


    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _buildUi(self) -> None:
        """构建内部布局:卡片容器 + logo + 标题 + 进度条 + 文案"""
        # 外层透明容器(用于显示阴影 + 整体居中)
        outerLayout = QVBoxLayout(self)
        outerLayout.setContentsMargins(20, 20, 20, 20)
        outerLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 卡片容器(实际显示背景 + 内容)
        self._card = QWidget(self)
        self._card.setObjectName("splashCard")
        self._card.setStyleSheet(
            f"#splashCard {{"
            f"background-color: rgb{BACKGROUND_COLOR.red(), BACKGROUND_COLOR.green(), BACKGROUND_COLOR.blue()};"
            f"border-radius: 14px;"
            f"border: 1px solid #e6e6e6;"
            f"}}"
        )
        self._card.setFixedSize(440, 260)

        cardLayout = QVBoxLayout(self._card)
        cardLayout.setContentsMargins(36, 28, 36, 24)
        cardLayout.setSpacing(12)
        cardLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ---- Logo ----
        self._logoLabel = QLabel(self._card)
        logoPixmap = QPixmap(":app/images/logo.png")
        if not logoPixmap.isNull():
            self._logoLabel.setPixmap(
                logoPixmap.scaled(
                    72,
                    72,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            # fallback:用纯色圆 + 文字,避免 logo 缺失时空白
            self._logoLabel.setText("P")
            self._logoLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            font = QFont()
            font.setPointSize(36)
            font.setBold(True)
            self._logoLabel.setFont(font)
            self._logoLabel.setStyleSheet(
                f"color: rgb{THEME_COLOR.red(), THEME_COLOR.green(), THEME_COLOR.blue()};"
            )
        self._logoLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cardLayout.addWidget(self._logoLabel, 0, Qt.AlignmentFlag.AlignHCenter)

        # ---- 标题 ----
        self._titleLabel = QLabel("Prismatica 棱溯", self._card)
        titleFont = QFont()
        titleFont.setPointSize(16)
        titleFont.setBold(True)
        self._titleLabel.setFont(titleFont)
        self._titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._titleLabel.setStyleSheet(
            f"color: rgb{TEXT_COLOR_LIGHT.red(), TEXT_COLOR_LIGHT.green(), TEXT_COLOR_LIGHT.blue()};"
        )
        cardLayout.addWidget(self._titleLabel, 0, Qt.AlignmentFlag.AlignHCenter)

        # ---- 副标题 / 动态文案 ----
        self._stageLabel = QLabel(self._stageText, self._card)
        stageFont = QFont()
        stageFont.setPointSize(10)
        self._stageLabel.setFont(stageFont)
        self._stageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stageLabel.setStyleSheet(
            f"color: rgb{SUBTEXT_COLOR.red(), SUBTEXT_COLOR.green(), SUBTEXT_COLOR.blue()};"
        )
        self._stageLabel.setWordWrap(True)
        cardLayout.addWidget(self._stageLabel)

        # ---- 进度条 ----
        self._progressBar = QProgressBar(self._card)
        self._progressBar.setRange(0, 100)
        self._progressBar.setValue(0)
        self._progressBar.setTextVisible(False)
        self._progressBar.setFixedHeight(6)
        self._progressBar.setStyleSheet(
            "QProgressBar {"
            "    background-color: #eef0f2;"
            "    border: none;"
            "    border-radius: 3px;"
            "}"
            "QProgressBar::chunk {"
            f"    background-color: rgb{THEME_COLOR.red(), THEME_COLOR.green(), THEME_COLOR.blue()};"
            "    border-radius: 3px;"
            "}"
        )
        cardLayout.addWidget(self._progressBar)

        outerLayout.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)

    def _applyShadow(self) -> None:
        """为卡片添加柔和阴影(在 translucent 窗口上才能显示)"""
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 60))
        self._card.setGraphicsEffect(shadow)

    # ------------------------------------------------------------------
    # 进度上报(线程安全)
    # ------------------------------------------------------------------
    @Slot(int, str)
    def _onProgressChanged(self, pct: int, text: str) -> None:
        """槽函数:更新目标进度 + 阶段文案。

        使用 progressChanged 信号从子线程跨线程调用,Qt 自动选队列连接,
        保证 UI 更新发生在主线程。
        """
        import time

        self._targetProgress = max(0, min(100, int(pct)))
        self._lastExternalProgressAt = time.monotonic()
        if text:
            self._stageText = text
            self._stageLabel.setText(text)

    def setProgress(self, pct: int, text: str = "") -> None:
        """外部调用接口(主线程直接调用)。

        子线程请通过 emit progressChanged.emit(pct, text) 触发。
        """
        self.progressChanged.emit(pct, text)

    def setStage(self, text: str) -> None:
        """仅更新阶段文案,不改变进度。"""
        self._stageText = text
        self._stageLabel.setText(text)

    # ------------------------------------------------------------------
    # 平滑进度动画(双轨制,让进度条始终在缓慢移动)
    # ------------------------------------------------------------------
    def _wireProgressAnimation(self) -> None:
        """启动两个定时器:
        - _tickTimer:高频(60ms)目标追赶定时器,负责追上 setProgress 推送的新目标。
        - _idleTimer:低频(220ms)自由增长定时器,在外部没推进时让进度条自动缓慢爬升,
          给用户「始终在加载」的视觉反馈(只要还没到目标)。
        """
        # 目标追赶
        self._tickTimer = QTimer(self)
        self._tickTimer.setInterval(TICK_INTERVAL_MS)
        self._tickTimer.timeout.connect(self._advanceProgress)
        self._tickTimer.start()

        # 自由增长(在外部 setProgress 静默期间也保持进度条在动)
        self._idleTimer = QTimer(self)
        self._idleTimer.setInterval(IDLE_TICK_MS)
        self._idleTimer.timeout.connect(self._idleAdvanceProgress)
        self._idleTimer.start()

    def _advanceProgress(self) -> None:
        """高频追赶:每次最多前进 CHASE_STEP_MAX,让数字变化可观察。"""
        if self._currentProgress < self._targetProgress:
            gap = self._targetProgress - self._currentProgress
            step = max(1, gap // CHASE_STEP_DIVISOR)
            step = min(step, CHASE_STEP_MAX)
            self._currentProgress = min(
                self._targetProgress, self._currentProgress + step
            )
            self._progressBar.setValue(self._currentProgress)
        elif self._currentProgress > self._targetProgress:
            # 不应出现倒退;若发生,防御性吸附到目标
            self._currentProgress = self._targetProgress
            self._progressBar.setValue(self._currentProgress)

    def _idleAdvanceProgress(self) -> None:
        """低频自由增长:仅在 current < target - IDLE_FLOOR_GAP 时推进,
        保证外部 setProgress 推进 target 后仍能继续往上爬到新 target。"""
        if self._isFinished:
            return
        ceiling = self._targetProgress - IDLE_FLOOR_GAP
        if self._currentProgress < ceiling:
            self._currentProgress = min(ceiling, self._currentProgress + IDLE_STEP)
            self._progressBar.setValue(self._currentProgress)

    # ------------------------------------------------------------------
    # 居中显示
    # ------------------------------------------------------------------
    def centerOnScreen(self) -> None:
        """将 splash 居中到主屏幕"""
        from PySide6.QtWidgets import QApplication

        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        # self.width() / height() 包含阴影边距(由外层 layout margin 决定)
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + (geo.height() - self.height()) // 2
        self.move(x, y)

    def showEvent(self, event):
        super().showEvent(event)
        # 首次显示后立即居中(此时 self.width()/height() 才稳定)
        self.centerOnScreen()

    # ------------------------------------------------------------------
    # 关闭:屏蔽用户主动关闭(避免加载中途被误关),对外提供 finish() 主动收尾
    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent):
        """屏蔽外部强制关闭。等待窗口必须由 finish() 主动收尾,
        否则可能与 MainWindow 关闭流程冲突。
        """
        if self._isFinished:
            event.accept()
            return
        logger.debug("[Splash] 屏蔽外部 close 事件,等待 finish() 触发")
        event.ignore()

    def finish(self) -> None:
        """主程序加载完成后调用:推进到 100%,淡出后销毁。"""
        if self._isFinished:
            return
        self._isFinished = True

        # 推进到 100% + 显示完成文案
        self.progressChanged.emit(100, "启动完成")
        # 立即跳到 100%(不等动画)
        self._targetProgress = 100
        self._currentProgress = 100
        self._progressBar.setValue(100)

        # 延迟一小段时间让用户看到 100%,再淡出
        QTimer.singleShot(220, self._startFadeOut)

    def _startFadeOut(self) -> None:
        """淡出动画:透明度从 1.0 → 0.0,然后销毁。"""
        self._fadeAnim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fadeAnim.setDuration(FADE_OUT_DURATION_MS)
        self._fadeAnim.setStartValue(1.0)
        self._fadeAnim.setEndValue(0.0)
        self._fadeAnim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fadeAnim.finished.connect(self._onFadeFinished)
        self._fadeAnim.start()

    def _onFadeFinished(self) -> None:
        """淡出结束:停止定时器,销毁窗口,通知外部。"""
        for timerAttr in ("_tickTimer", "_idleTimer"):
            try:
                timer = getattr(self, timerAttr, None)
                if timer is not None:
                    timer.stop()
            except Exception:
                pass
        self.hide()
        try:
            self.finished.emit()
        except (RuntimeError, AttributeError):
            pass
        # 真正销毁前发出 fadedOut — SplashLoader 监听后会触发
        # startupCompleted,主窗口再 show()。这样保证主窗口出现时
        # splash 已彻底消失,不会出现「两个窗口叠加」或「splash
        # 淡出残留与主窗口并排」的情况。
        try:
            self.fadedOut.emit()
        except (RuntimeError, AttributeError):
            pass
        self.deleteLater()

    # ------------------------------------------------------------------
    # 引导窗口临时退场
    # ------------------------------------------------------------------
    def hold(self) -> None:
        """临时隐藏 splash(不销毁),用于「首次启动引导」等需要让用户
        与一个真正的模态窗口交互的场景。

        - 引导窗口显示期间 splash 不可见,避免与引导窗口重叠
        - 引导窗口关闭后,调用 release() 让 splash 重新显示

        实现要点:
            - 不修改 _isFinished,finish() 仍可后续调用
            - 不停止定时器,再次 show 时进度条会自动重新开始追赶
            - _isHeld 状态守卫,避免未持有时重复 release 时 no-op
        """
        if self._isFinished:
            logger.debug("[Splash] hold() 调用但 splash 已 finish,忽略")
            return
        if self._isHeld:
            # 修复(2026-08-05):同一窗口多次 hold() 是无意义的,避免日志噪音
            logger.debug("[Splash] hold() 调用但已处于 held 状态,忽略")
            return
        try:
            self.hide()
            self._isHeld = True
        except Exception:
            logger.debug("[Splash] hold() hide 失败")

    def release(self, progress: int = None, text: str = "") -> None:
        """hold() 的反向操作:让 splash 重新可见(引导结束后)。

        Args:
            progress: 可选,重新显示时把目标进度推到指定值(默认沿用 hold 前进度)
            text: 可选,阶段文案
        """
        if self._isFinished:
            return
        if not self._isHeld:
            # 修复(2026-08-05):未持有时 release 是无意义的,
            # 不再触发 show/raise/activate,避免空 splash 闪现。
            return
        if progress is not None:
            try:
                self.progressChanged.emit(int(progress), str(text))
            except Exception:
                pass
        try:
            self.show()
            self.raise_()
            self.activateWindow()
            self._isHeld = False
            # 强制首帧立即绘制,避免被后续阻塞逻辑遮挡
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()
        except Exception:
            # 释放失败时也要清掉守卫,避免后续 release() 永远 no-op,
            # 导致 splash 永久不可见。
            self._isHeld = False
            logger.debug("[Splash] release() show 失败")
