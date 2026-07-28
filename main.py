# coding:utf-8
import os
import sys
import warnings

# 注意:已移除 app.core.utils.matplotlib_backend.installAll() 调用。
# 原因:
#   1. 它会无条件切换 matplotlib 后端到 QtAgg 并触发 PySide6 后端加载链,
#      在 Nuitka 打包的 Win10 环境中可能找不到 shiboken6 路径,
#      报 "D:shiboken6\libshiboken does not exist"。
#   2. 它会拖慢启动时间(QtAgg 后端加载本身较重)。
# 各 view(bias_interface / freq_analyzer_interface / network_widget 等)
# 在内部已按需调用 matplotlib.use("QtAgg", force=True),且执行时机在
# QApplication 创建之后,shiboken6 路径已就绪,不受上述问题影响。
#
# 若需要在调试环境下让 IPython 跳过 GUI 集成,
# 可在外部环境变量中显式设置 MPLBACKEND=Agg(此处不再强制)。

# 静默 torch.cuda 内部的 pynvml deprecation 警告(由 PyTorch 触发,非本项目问题)
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    module=r"torch\.cuda",
)

# 静默 jieba 内部 pkg_resources 弃用警告(由 setuptools>=81 触发,非本项目问题)
# 警告来源:jieba/_compat.py:18 的 `import pkg_resources`
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module=r"jieba[\\.\/]?.*",
    message=r".*pkg_resources is deprecated.*",
)

# 静默 PySide6 内部 QMouseEvent.globalPos() 弃用警告
# 警告来源:qfluentwidgetspro.Drawer / 其他组件在事件处理中调用了已被弃用的
# globalPos() API（PySide6 6.5+ 推荐用 globalPosition().toPoint() 替代）。
# 第三方库升级滞后,本项目无法直接修复,统一静默。
# 注意:此警告由 PySide6 在 C++ 层触发,无法用 module 匹配;
# 只用 message 匹配,避免误伤其他弃用警告。
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    message=r".*QMouseEvent\.globalPos.*",
)

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication
from qfluentwidgetspro import setLicense

from app.core.utils import cfg, qconfig, autoSetup, logger
from app.core.utils.setting import MODE
from app.view.main_window import MainWindow
from app.view.resource.resource import *

setLicense(
    "jGEwKHNnQYGLMk+G3DD0REwDKhaSyZ3jj+st63emdDJPlj2M1D2aJ8ediZJVyVG75FyXv56z1BBUk7LFrFBwh2DuEy8f3YuMtezFbY/PSiMRXFdLKM23VSZuEatCBjunKrsOo3Y5D+/0/6B/ulVDxm2YIstlNar6OedvxZSDf4R8tQzIvrrfg0DEMEdqnHvHNcGny39/U2iGzF6HjA+OwKEqZSdP1tG+icDOlfT5AmxWG0oGH1uAzylMnip+NB4OeFQQOG3xGyyVARwPVp35Xg=="
)

# 配置日志系统
autoSetup(MODE)

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

# create application
app = QApplication(sys.argv)
app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)

# =====================================================================
# 立即启动 Splash 等待窗口(2026-07-27 改造,2026-07-27 再次调整)
# 设计目标:
#   - QApplication 创建后**第一件事**就是显示 splash,做到「一运行就启动」
#   - 后续所有重操作(项目数据预热 / beta 校验 / 引导窗口 / 主窗口构造)
#     都在 splash 显示**之后**进行,用户立即看到视觉反馈,启动期不再黑屏
#   - splash 始终保持最前(WindowStaysOnTopHint + raise_ + activateWindow)
#     并强制处理一次事件循环,让首帧马上绘制出来
# =====================================================================
from app.view.widgets.splash_window import SplashWindow
from app.core.services.splash_loader import SplashLoader
from qfluentwidgets import InfoBar, InfoBarPosition

# 1) 创建并立即显示 splash(< 100ms 内可见,真正「一运行就启动」)
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
# 内测时间锁校验:首次启动记录 start_date,后续验证签名/截止日/有效期
#
# 行为规则:
#   - IS_BETA=True  → 时间锁全量生效:超出 BETA_HARD_DEADLINE 或 30 天则阻止启动
#   - IS_BETA=False → 不阻止启动(正式版通过激活码授权,时间锁仅做后台追踪)
# 注意:此校验在 splash 之后进行,期间 splash 持续显示并展示阶段文案
# =====================================================================
from app.core.utils.license import getLicenseManager
from app.core.utils.setting import IS_BETA

_splashWindow.setProgress(18, "正在校验许可证…")
_splashWindow.raise_()
QApplication.processEvents()

_betaStatus = getLicenseManager().ensureBetaTimelock()
# P0-fix:用于在过期时持有非模态弹窗的强引用,防止 Python GC 回收。
# 模块级全局变量,生命周期等同进程。
_betaExpiredDialog = None
logger.info(
    f"[BetaTimelock] IS_BETA={IS_BETA}, "
    f"status={_betaStatus.get('status')}, "
    f"daysRemaining={_betaStatus.get('daysRemaining')}, "
    f"deadline={_betaStatus.get('deadline')}"
)

if IS_BETA and _betaStatus.get("status") in ("expired_hard", "expired_30d"):
    # P0-fix 2026-07-18:内测已过期时**仅显示提示弹窗**,不显示主窗口。
    # - 弹窗用 modal 模式阻塞主事件循环(直到用户激活成功 / 退出程序)
    # - 不创建 MainWindow,避免用户绕过弹窗进入主程序
    # - 用户必须做出选择(激活码 / 退出)
    from app.view.widgets.beta_expired_dialog import showBetaExpiredWarning

    logger.warning(
        f"[BetaTimelock] 内测已过期,显示提示弹窗: {_betaStatus.get('reason')}"
    )
    # 暂存到模块级变量,避免弹窗被 GC
    _betaExpiredDialog = showBetaExpiredWarning(_betaStatus, parent=None, modal=True)
    # 过期分支:splash 立即关闭(主窗口永远不会构造)
    try:
        _splashWindow.finish()
    except Exception:
        pass
else:
    # ============================================================
    # 首次启动引导(2026-07-21 新增)
    # - 读取 cfg.FirstLaunch;为 True 时先弹出引导窗口
    # - 引导完成后 cfg.FirstLaunch 被置为 False,下次启动不再弹出
    # - 若用户在未完成时点关闭按钮,引导窗口拒绝关闭并请求退出主程序
    # ============================================================
    _guideWindow = None
    # 许可证校验通过 → 推进到 22%(给引导窗口留 6%)
    _splashWindow.setProgress(22, "许可证校验通过")
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

            _guideCompleted = _guideWindow.exec()

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
    _splashLoader.start()  # 立即返回,异步构造

# 应用程序退出处理
result = app.exec()

logger.info("应用程序退出")
# 确保线程被正确停止
sys.exit(result)
