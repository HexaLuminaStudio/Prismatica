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
# 内测时间锁校验:首次启动记录 start_date,后续验证签名/截止日/有效期
#
# 行为规则:
#   - IS_BETA=True  → 时间锁全量生效:超出 BETA_HARD_DEADLINE 或 30 天则阻止启动
#   - IS_BETA=False → 不阻止启动(正式版通过激活码授权,时间锁仅做后台追踪)
# =====================================================================
from app.core.utils.license import getLicenseManager
from app.core.utils.setting import IS_BETA

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
else:
    # ============================================================
    # 首次启动引导(2026-07-21 新增)
    # - 读取 cfg.FirstLaunch;为 True 时先弹出引导窗口
    # - 引导完成后 cfg.FirstLaunch 被置为 False,下次启动不再弹出
    # - 若用户在未完成时点关闭按钮,引导窗口拒绝关闭并请求退出主程序
    # ============================================================
    _guideWindow = None
    if qconfig.get(cfg.FirstLaunch):
        try:
            from app.view.widgets.guide_window import GuideWindow

            logger.info("[Main] 检测到首次启动,显示引导窗口")
            _guideWindow = GuideWindow()
            _guideCompleted = _guideWindow.exec()
            if not _guideCompleted:
                # 用户在未完成时尝试关闭引导 -> 退出整个程序
                # cfg.FirstLaunch 保持 True,下次启动仍会引导
                logger.warning("[Main] 引导未完成,退出程序,保留 FirstLaunch=True")
                # 释放引导窗口,避免悬挂引用
                _guideWindow = None
                # 走正常 Qt 退出流程,确保资源回收
                from PySide6.QtWidgets import QApplication

                QApplication.instance().quit()
                # 跳过后续主窗口创建逻辑(用 sys.exit 跳出整个模块底部)
                sys.exit(0)
        except Exception as _guideErr:
            logger.exception(f"[Main] 引导窗口初始化失败,跳过引导: {_guideErr}")

    mainWindow = MainWindow()
    mainWindow.show()

# 应用程序退出处理
result = app.exec()

logger.info("应用程序退出")
# 确保线程被正确停止
sys.exit(result)
