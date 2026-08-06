# coding:utf-8
import os
import sys
import warnings
import time as _startTime

warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    module=r"torch\.cuda",
)


warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module=r"jieba[\\.\/]?.*",
    message=r".*pkg_resources is deprecated.*",
)


warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    message=r".*QMouseEvent\.globalPos.*",
)

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication
from qfluentwidgetspro import setLicense

# =====================================================================
# 冷启动埋点 ① :模块导入阶段已耗时,在这里初始化 profiler(最早)
# =====================================================================
from app.core.utils import cfg, qconfig, autoSetup, logger, getStartupProfiler
from app.core.utils.setting import MODE
from app.view.main_window import (
    MainWindow,
)  # noqa: F401(主窗口构造期会再次用到,先 import 用于计时)
from app.view.resource.resource import *

_startupProfiler = getStartupProfiler()
# 记录从进程起到"刚完成 import"的总耗时(模块级 import 通常是冷启动大头)
importImportSec = _startTime.perf_counter()
_startupProfiler.mark(
    "import_done",
    f"进程启动至所有 import 完成 = {(importImportSec - _startupProfiler._bootStart) * 1000.0:.1f} ms",
)

with _startupProfiler.stage("set_license", "qfluentwidgetspro.setLicense"):
    setLicense(
        "jGEwKHNnQYGLMk+G3DD0REwDKhaSyZ3jj+st63emdDJPlj2M1D2aJ8ediZJVyVG75FyXv56z1BBUk7LFrFBwh2DuEy8f3YuMtezFbY/PSiMRXFdLKM23VSZuEatCBjunKrsOo3Y5D+/0/6B/ulVDxm2YIstlNar6OedvxZSDf4R8tQzIvrrfg0DEMEdqnHvHNcGny39/U2iGzF6HjA+OwKEqZSdP1tG+icDOlfT5AmxWG0oGH1uAzylMnip+NB4OeFQQOG3xGyyVARwPVp35Xg=="
    )

with _startupProfiler.stage("logger_setup", "loguru 初始化(autoSetup)"):
    # 配置日志系统
    autoSetup(MODE)

with _startupProfiler.stage("dpi_scale", "DPI 缩放环境变量设置"):
    # enable dpi scale
    _dpi_scale = cfg.get(cfg.DpiScale)
    if _dpi_scale != "Auto":
        try:
            scale = float(_dpi_scale)
            if scale <= 0:
                raise ValueError(f"Invalid scale factor: {scale}")
            os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
            os.environ["QT_SCALE_FACTOR"] = str(scale)
            logger.info(f"[Main] DPI缩放已设置为 {scale}x")
        except (TypeError, ValueError) as e:
            logger.warning(
                f"[Main] DPI缩放配置无效 ({_dpi_scale!r})，使用系统自动缩放: {e}"
            )

with _startupProfiler.stage("qapplication_init", "QApplication 构造"):
    # create application
    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)

from app.view.widgets.splash_window import SplashWindow
from app.core.services.splash_loader import SplashLoader
from qfluentwidgets import InfoBar, InfoBarPosition


with _startupProfiler.stage("splash_create", "SplashWindow 构造 + 首帧显示"):
    _splashWindow = SplashWindow()
    # 初始目标 5%(SplashWindow 内部已设);自由增长定时器会从 0 自动爬升
    _splashWindow.setProgress(5, "正在初始化…")
    _splashWindow.show()
    # 强制把 splash 顶到最前并抢焦点,确保启动期用户看到的第一窗口就是它
    _splashWindow.raise_()
    _splashWindow.activateWindow()
    QApplication.processEvents()  # 强制首帧绘制,避免被后续阻塞逻辑遮挡

# 2) 创建加载协调器(持有 splash 强引用,避免被 GC)
with _startupProfiler.stage("splash_loader_init", "SplashLoader 构造"):
    _splashLoader = SplashLoader(splashWindow=_splashWindow)

# 3) 持有主窗口的强引用,避免被 GC
_mainWindowRef = None


# 4) 主窗口构造完成回调 — 此时**不**显示主窗口,只把窗口存起来,
#    触发 splash 淡出前的预热完成通知。
def _onMainWindowReady(window) -> None:
    global _mainWindowRef
    _mainWindowRef = window
    # 冷启动埋点:主窗口已构造完毕(此后只剩 splash 淡出 + show)
    try:
        _startupProfiler.mark(
            "mainwindow_ready",
            "MainWindow 已构造完成,等待 splash 淡出后再展示",
        )
    except Exception as _profErr:
        logger.warning(f"[StartupProfiler] mark 失败(非致命): {_profErr}")
    logger.info("[Main] MainWindow 已构造完成,等待 splash 淡出后再展示")
    # 把"启动彻底完成"通知延迟到下一轮事件循环,确保子线程/异步信号链
    # 完全处理完毕后,主窗口才进入展示流程
    from PySide6.QtCore import QTimer

    QTimer.singleShot(0, _splashLoader.notifyStartupCompleted)


# 5) 启动彻底完成(splash 已淡出后)回调 — 此时才真正显示主窗口
def _onStartupCompleted() -> None:
    try:
        if _mainWindowRef is None:
            logger.warning("[Main] startupCompleted 触发但 _mainWindowRef 为空")
            return
        _mainWindowRef._showAfterStartup()
        logger.info("[Main] 主窗口已展示(启动彻底完成)")
        # 启动审计(2026-08-06):主窗口展示完成落 audit,
        # 用于事后追溯「启动耗时 / 异常中止」等关键里程碑。
        try:
            from app.core.utils import audit
            totalMs = (time.perf_counter() - _startupProfiler._bootStart) * 1000.0
            audit("STARTUP_MAIN_WINDOW_READY", f"totalMs={totalMs:.1f}")
        except Exception as _auditErr:
            logger.debug(f"[Main] audit 写入失败(非致命): {_auditErr}")
        # 冷启动埋点:此时用户已看到主窗口,落盘冷启动耗时汇总
        try:
            _startupProfiler.finish()
        except Exception as _pfErr:
            logger.warning(f"[StartupProfiler] finish 失败(非致命): {_pfErr}")
    except Exception as e:
        logger.exception(f"[Main] 主窗口展示失败: {e}")
        try:
            InfoBar.error(
                title="启动失败",
                content=f"主窗口初始化异常: {e}",
                parent=_splashWindow,
                duration=5000,
                position=InfoBarPosition.TOP,
            )
        except Exception:
            pass


# 4) 加载失败:展示错误信息并退出
def _onLoadFailed(exc) -> None:
    logger.error(f"[Main] 主窗口构造失败,程序退出: {exc}")
    try:
        InfoBar.error(
            title="启动失败",
            content=f"主窗口初始化异常: {exc}",
            parent=_splashWindow,
            duration=5000,
            position=InfoBarPosition.TOP,
        )
    except Exception:
        pass
    QApplication.instance().quit()
    sys.exit(1)


_splashLoader.mainWindowReady.connect(_onMainWindowReady)
_splashLoader.loadFailed.connect(_onLoadFailed)
_splashLoader.startupCompleted.connect(_onStartupCompleted)

# =====================================================================
# 项目数据预热(splash 显示之后执行,2026-07-27 调整)
# 目标:让 SQLite DB I/O 等耗时同步操作在用户看到 splash 之后执行,
#       通过 splash 文案反馈加载状态,避免启动期出现黑屏。
# 涉及的模块:
#   - ProjectManager:__init__ 中包含 _initDb + _loadAllProjectsFromDb
#     + _restoreActiveProject 三步同步 DB 操作。
#     这里只是「提前触发」,不破坏 ProjectManager 单例语义 —
#     instance() 仍只创建一次,后续模块再 import projectManager
#     时拿到的是同一个缓存实例,无副作用。
# 注意:这一段必须在 splash 出现**之后**执行,确保 splash 是
#       用户看到的第一个窗口。
# =====================================================================
try:
    with _startupProfiler.stage(
        "project_manager_warmup", "ProjectManager.instance() 预热"
    ):
        from app.core.services.project_manager import ProjectManager

        _splashWindow.setProgress(10, "正在加载项目数据…")
        _splashWindow.raise_()
        QApplication.processEvents()
        logger.info("[Main] 预热:提前加载项目元数据(splash 之后)")
        ProjectManager.instance()
        _splashWindow.setProgress(15, "项目数据加载完成")
        QApplication.processEvents()
except Exception as _pmWarmupErr:
    logger.warning(f"[Main] 预热 ProjectManager 失败(非致命,继续): {_pmWarmupErr}")

# =====================================================================
# 启动门(2026-08-06 简化:删除本地内测时间锁)
#
# 历史背景:
#   - 旧版本:本地维护 BETA_HARD_DEADLINE + 30 天有效期,过期强制弹窗。
#   - 新版本:全部授权与有效期由云端 PrismaticaAPI 接管,本地不再做日期限制。
# 行为规则:
#   - IS_BETA=True  → 仍保留启动门(强制先激活才能进主界面)
#   - IS_BETA=False → 不弹启动门,允许进入主界面,付费功能按需激活
# =====================================================================
from app.core.utils.setting import IS_BETA
from app.core.services.auth_service import getAuthService  # 启动门

_splashWindow.setProgress(18, "正在准备启动…")
_splashWindow.raise_()
QApplication.processEvents()

logger.info(f"[Main] IS_BETA={IS_BETA},本地时间锁已下线(2026-08-06)")

# ============================================================
# 启动门(REQ-BETA-002,2026-08-06 调整):
# - 内测版(IS_BETA=True):未激活用户必须先激活才能进入主窗口
# - 正式版(IS_BETA=False):不再弹启动门,允许进入主界面,付费功能按需再激活
# ============================================================
_authService = getAuthService()
if IS_BETA and not _authService.isAuthenticated():
    # 云端恢复:本地凭证完好但 token 过期 / expireAt 陈旧时,先尝试
    # 用 refresh token 从云端恢复会话(失败不阻断,进入登录门)
    try:
        _authService.restoreSession()
    except Exception as _restoreErr:  # noqa: BLE001
        logger.warning(f"[Main] 云端会话恢复失败(忽略): {_restoreErr}")
    if not _authService.isAuthenticated():
        try:
            with _startupProfiler.stage("auth_gate", "AuthGate 启动门"):
                from app.view.auth_interface import showAuthGate

                _splashWindow.setProgress(20, "正在校验激活凭证…")
                _splashWindow.raise_()
                QApplication.processEvents()
                # 修复(2026-08-05 启动门卡死):
                #   1) 不再 hold splash —— splash 已 hide 时其子 dialog 也
                #      不可见不响应,会导致 exec() 永久阻塞。
                #   2) showAuthGate(parent=None) → 内部 fallback 到 activeWindow,
                #      让 LoginDialog 作为独立顶层弹窗显示。
                #   3) splash 仍作为 WindowStaysOnTopHint 在最下层,登录完成
                #      后主窗口上来时 splash 才 finish。
                activated = showAuthGate(parent=None)
                if not activated:
                    logger.warning("[Main] 用户未激活,退出程序")
                    try:
                        _splashWindow.finish()
                    except Exception:
                        pass
                    QApplication.instance().quit()
                    sys.exit(0)
        except Exception as _authErr:
            logger.exception(f"[Main] AuthGate 异常(非致命,继续): {_authErr}")
elif not IS_BETA:
    # 正式版:仅尝试云端恢复(若本地有未过期凭证),不做强制拦截。
    try:
        _authService.restoreSession()
    except Exception as _restoreErr:  # noqa: BLE001
        logger.info(f"[Main] 正式版跳过启动门,云端会话恢复失败(忽略): {_restoreErr}")
# ============================================================
# 首次启动引导(2026-07-21 新增)
# - 读取 cfg.FirstLaunch;为 True 时先弹出引导窗口
# - 引导完成后 cfg.FirstLaunch 被置为 False,下次启动不再弹出
# - 若用户在未完成时点关闭按钮,引导窗口拒绝关闭并请求退出主程序
# ============================================================
_guideWindow = None
# 启动门通过 → 推进到 22%(给引导窗口留 6%)
_splashWindow.setProgress(22, "启动门已通过")
QApplication.processEvents()
if qconfig.get(cfg.FirstLaunch):
    try:
        from app.view.widgets.guide_window import GuideWindow

        _splashWindow.setProgress(24, "正在显示首次启动引导…")
        QApplication.processEvents()

        # ---- 2026-07-27 改造:Splash ↔ 引导窗口融合 ----
        # 引导窗口是真正的交互窗口,需要用户点击"下一步 / 完成"。
        # 若此时 splash 仍在最前(WindowStaysOnTopHint),会出现
        #   - splash + 引导窗口重叠闪现(后者被 splash 遮住一半)
        #   - 用户点不到引导窗口的"下一步"按钮(splash 抢了事件)
        # 解决方案:
        #   - exec() 前调用 splash.hold():临时隐藏 splash(不销毁,
        #     保留状态),此时引导窗口独占屏幕
        #   - exec() 返回后调用 splash.release():让 splash 重新显示
        #   - 配合 SplashLoader 的 fadedOut → startupCompleted 推迟逻辑,
        #     整个启动序列不会出现"两个窗口叠加"
        logger.info("[Main] 检测到首次启动,显示引导窗口(暂存 splash)")
        _guideWindow = GuideWindow()
        try:
            _splashWindow.hold()
        except Exception as _holdErr:
            logger.warning(f"[Main] 暂存 splash 失败(非致命): {_holdErr}")
        QApplication.processEvents()

        with _startupProfiler.stage(
            "guide_window_exec", "首次启动 GuideWindow.exec()"
        ):
            # 修复(2026-08-05):hold/release 用 try/finally 配对,
            # 哪怕 exec 抛异常也要恢复 splash,避免「软件无反应」。
            try:
                _guideCompleted = _guideWindow.exec()
            finally:
                # 引导结束后让 splash 重新接管(若进度已推进过,这里继续推一格)
                try:
                    _splashWindow.release(
                        progress=25, text="引导完成,准备启动主窗口…"
                    )
                except Exception as _relErr:
                    logger.warning(f"[Main] 恢复 splash 失败(非致命): {_relErr}")

        if not _guideCompleted:
            # 用户在未完成时尝试关闭引导 -> 退出整个程序
            # cfg.FirstLaunch 保持 True,下次启动仍会引导
            logger.warning("[Main] 引导未完成,退出程序,保留 FirstLaunch=True")
            # 释放引导窗口,避免悬挂引用
            _guideWindow = None
            # 关闭 splash,再退出
            try:
                _splashWindow.finish()
            except Exception:
                pass
            QApplication.instance().quit()
            # 跳过后续主窗口创建逻辑(用 sys.exit 跳出整个模块底部)
            sys.exit(0)
    except Exception as _guideErr:
        logger.exception(f"[Main] 引导窗口初始化失败,跳过引导: {_guideErr}")

# ============================================================
# 启动主窗口异步加载
# - SplashLoader 在下一轮事件循环触发 MainWindow 构造
# - 完成后通过 mainWindowReady 信号在主线程 show()
# - splash 在主窗口 ready 后自动 finish() 并淡出销毁
# ============================================================
# 引导完成(或跳过引导) → 推进到 26%,留 4% 给 SplashLoader 衔接 30%
_splashWindow.setProgress(26, "准备启动主窗口…")
QApplication.processEvents()
with _startupProfiler.stage(
    "splash_loader_start", "SplashLoader.start() 触发异步加载"
):
    _splashLoader.start()  # 立即返回,异步构造

# 应用程序退出处理
result = app.exec()

logger.info("应用程序退出")
# 确保线程被正确停止
sys.exit(result)
