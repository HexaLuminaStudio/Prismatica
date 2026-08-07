# coding:utf-8
import os
import sys
import warnings

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

from app.core.utils import cfg, configureLogging, log, qconfig
from app.core.utils.setting import MODE

# 日志只允许在应用入口初始化一次，业务模块导入不会创建日志文件。
configureLogging(MODE)

from app.view.main_window import (
    MainWindow,
)  # noqa: F401(主窗口构造期会再次使用)
from app.view.resource.resource import *

setLicense(
    "jGEwKHNnQYGLMk+G3DD0REwDKhaSyZ3jj+st63emdDJPlj2M1D2aJ8ediZJVyVG75FyXv56z1BBUk7LFrFBwh2DuEy8f3YuMtezFbY/PSiMRXFdLKM23VSZuEatCBjunKrsOo3Y5D+/0/6B/ulVDxm2YIstlNar6OedvxZSDf4R8tQzIvrrfg0DEMEdqnHvHNcGny39/U2iGzF6HjA+OwKEqZSdP1tG+icDOlfT5AmxWG0oGH1uAzylMnip+NB4OeFQQOG3xGyyVARwPVp35Xg=="
)

# enable dpi scale
_dpi_scale = cfg.get(cfg.DpiScale)
if _dpi_scale != "Auto":
    try:
        scale = float(_dpi_scale)
        if scale <= 0:
            raise ValueError(f"Invalid scale factor: {scale}")
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_SCALE_FACTOR"] = str(scale)
    except (TypeError, ValueError) as e:
        log.warning(
            f"[Main] DPI缩放配置无效 ({_dpi_scale!r})，使用系统自动缩放: {e}"
        )

# create application
app = QApplication(sys.argv)
app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)

from app.view.widgets.splash_window import SplashWindow
from app.core.services.splash_loader import SplashLoader
from qfluentwidgets import InfoBar, InfoBarPosition


_splashWindow = SplashWindow()
# 初始目标 5%(SplashWindow 内部已设);自由增长定时器会从 0 自动爬升
_splashWindow.setProgress(5, "正在初始化…")
_splashWindow.show()
# 强制把 splash 顶到最前并抢焦点,确保启动期用户看到的第一窗口就是它
_splashWindow.raise_()
_splashWindow.activateWindow()
QApplication.processEvents()  # 强制首帧绘制,避免被后续阻塞逻辑遮挡

# 2) 创建加载协调器(持有 splash 强引用,避免被 GC)
_splashLoader = SplashLoader(splashWindow=_splashWindow)

# 3) 持有主窗口的强引用,避免被 GC
_mainWindowRef = None


# 4) 主窗口构造完成回调 — 此时**不**显示主窗口,只把窗口存起来,
#    触发 splash 淡出前的预热完成通知。
def _onMainWindowReady(window) -> None:
    global _mainWindowRef
    _mainWindowRef = window
    # 把"启动彻底完成"通知延迟到下一轮事件循环,确保子线程/异步信号链
    # 完全处理完毕后,主窗口才进入展示流程
    from PySide6.QtCore import QTimer

    QTimer.singleShot(0, _splashLoader.notifyStartupCompleted)


# 5) 启动彻底完成(splash 已淡出后)回调 — 此时才真正显示主窗口
def _onStartupCompleted() -> None:
    try:
        if _mainWindowRef is None:
            log.warning("[Main] startupCompleted 触发但 _mainWindowRef 为空")
            return
        _mainWindowRef._showAfterStartup()
        log.info("应用程序启动完成")
    except Exception as e:
        log.exception(f"[Main] 主窗口展示失败: {e}")
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
    log.error(f"[Main] 主窗口构造失败,程序退出: {exc}")
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
    from app.core.services.project_manager import ProjectManager

    _splashWindow.setProgress(10, "正在加载项目数据…")
    _splashWindow.raise_()
    QApplication.processEvents()
    ProjectManager.instance()
    _splashWindow.setProgress(15, "项目数据加载完成")
    QApplication.processEvents()
except Exception as _pmWarmupErr:
    log.warning(f"[Main] 预热 ProjectManager 失败(非致命,继续): {_pmWarmupErr}")

# =====================================================================
# 云端会话恢复(2026-08-07)
# - 同步加载 cloud_session.enc,若有效则复用上次的 access/refresh token
# - bootstrap() 内部会后台异步调 /v1/auth/refresh,不阻塞启动期
# - 必须先于 MainWindow 构造,否则 AccountNavWidget 初次渲染拿不到会话
# =====================================================================
try:
    from app.core.services import getCloudAuth

    _splashWindow.setProgress(16, "恢复云端会话…")
    QApplication.processEvents()
    ok = getCloudAuth().bootstrap()
    log.info(f"[Main] 云端会话恢复结果: {'已恢复' if ok else '无历史会话'}")
except Exception as _bootErr:
    log.warning(f"[Main] 云端会话恢复失败(非致命,继续): {_bootErr}")

# ============================================================
# 首次启动引导(2026-07-21 新增)
# - 读取 cfg.FirstLaunch;为 True 时先弹出引导窗口
# - 引导完成后 cfg.FirstLaunch 被置为 False,下次启动不再弹出
# - 若用户在未完成时点关闭按钮,引导窗口拒绝关闭并请求退出主程序
# ============================================================
_guideWindow = None
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
        _guideWindow = GuideWindow()
        try:
            _splashWindow.hold()
        except Exception as _holdErr:
            log.warning(f"[Main] 暂存 splash 失败(非致命): {_holdErr}")
        QApplication.processEvents()

        # 修复(2026-08-05):hold/release 用 try/finally 配对,
        # 哪怕 exec 抛异常也要恢复 splash,避免「软件无反应」。
        try:
            _guideCompleted = _guideWindow.exec()
        finally:
            # 引导结束后让 splash 重新接管(若进度已推进过,这里继续推一格)
            try:
                _splashWindow.release(progress=25, text="引导完成,准备启动主窗口…")
            except Exception as _relErr:
                log.warning(f"[Main] 恢复 splash 失败(非致命): {_relErr}")

        if not _guideCompleted:
            # 用户在未完成时尝试关闭引导 -> 退出整个程序
            # cfg.FirstLaunch 保持 True,下次启动仍会引导
            log.info("[Main] 用户未完成首次引导,程序退出")
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
        log.exception(f"[Main] 引导窗口初始化失败,跳过引导: {_guideErr}")

# ============================================================
# 启动主窗口异步加载
# - SplashLoader 在下一轮事件循环触发 MainWindow 构造
# - 完成后通过 mainWindowReady 信号在主线程 show()
# - splash 在主窗口 ready 后自动 finish() 并淡出销毁
# ============================================================
# 引导完成(或跳过引导) → 推进到 26%,留 4% 给 SplashLoader 衔接 30%
_splashWindow.setProgress(26, "准备启动主窗口…")
QApplication.processEvents()
_splashLoader.start()  # 立即返回,异步构造

# 应用程序退出处理
result = app.exec()

log.info("应用程序退出")
# 确保线程被正确停止
sys.exit(result)
