# coding:utf-8
import os
import sys
import warnings

# =====================================================================
# 必须在所有 matplotlib 相关 import 之前设置后端
# ---------------------------------------------------------------------
# 修复 VSCode Python 调试扩展(debugpy)的 IPython matplotlib 集成
# 在 PySide6 环境下的崩溃问题:
#
#     AttributeError: module 'PySide6.QtGui' has no attribute 'QApplication'
#
# 原因:
#     debugpy 在调试会话中会激活 IPython-style 的 matplotlib inputhook,
#     但其内部实现仍使用 PyQt4 时代的 QtGui.QApplication(应位于 QtWidgets),
#     而我们的环境是 PySide6,因此触发 AttributeError。
#
# 解决:
#     1. 设置 MPLBACKEND = "Agg"(非交互后端),让 IPython 跳过 GUI 集成
#     2. 设置 PYDEVD_IPYTHON_COMPATIBLE_SOMETHING = 0 (旧版兼容标志)
#     3. 后续在 freq_analyzer_interface.py 中会强制切换到 QtAgg
#
# 这样做的好处:
#     - 即使在 VSCode 调试器下,程序也能正常启动
#     - 不影响运行时(matplotlib 实际使用的是 freq_analyzer_interface
#       里设置的 QtAgg)
# =====================================================================
os.environ.setdefault("MPLBACKEND", "Agg")

# 应用 matplotlib 兼容性补丁(必须在任何 matplotlib / pyplot import 之前)
try:
    from app.core.utils.matplotlib_backend import installAll

    installAll()
except Exception as _mplErr:
    # 即使补丁失败也不应阻止程序启动
    print(f"[warn] matplotlib backend 初始化失败: {_mplErr}", file=sys.stderr)

# 静默 torch.cuda 内部的 pynvml deprecation 警告(由 PyTorch 触发,非本项目问题)
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    module=r"torch\.cuda",
)

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication
from qfluentwidgetspro import setLicense

from app.core.utils import cfg, autoSetup, logger
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
# =====================================================================
from app.core.utils.license import getLicenseManager

_betaStatus = getLicenseManager().ensureBetaTimelock()
logger.info(
    f"[BetaTimelock] status={_betaStatus.get('status')}, "
    f"daysRemaining={_betaStatus.get('daysRemaining')}, "
    f"deadline={_betaStatus.get('deadline')}"
)

if _betaStatus.get("status") in ("expired_hard", "expired_30d"):
    # 内测已过期:显示遮罩界面阻止使用
    from app.view.widgets.beta_expired_dialog import showBetaExpiredDialog

    logger.warning(f"[BetaTimelock] 已阻止启动: {_betaStatus.get('reason')}")
    showBetaExpiredDialog(_betaStatus)
    sys.exit(0)

mainWindow = MainWindow()
mainWindow.show()

# 应用程序退出处理
result = app.exec()

logger.info("应用程序退出")
# 确保线程被正确停止
sys.exit(result)
