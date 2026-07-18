r"""
Matplotlib 后端兼容性补丁

解决问题:
    VSCode Python 调试扩展(debugpy)在调试会话中激活 IPython-style
    的 matplotlib inputhook,试图用 PyQt4 时代的 API hook 进 QApplication:

        File "...\pydev_ipython\inputhookqt4.py", line 87
            app = QtGui.QApplication([" "])   # ← PyQt4 写法
        AttributeError: module 'PySide6.QtGui' has no attribute 'QApplication'

本模块在 main.py 启动时由 main_window 初始化阶段加载,
作用:
    1. 禁用 IPython matplotlib 自动集成(IPython 默认会 hook 进 PyQt/PySide)
    2. 显式启用 matplotlib inline backend(用于 Qt 嵌入的 FigureCanvasQTAgg)
    3. 防止 pyplot 进入交互模式
"""

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from loguru import logger


def disableIpythonMatplotlibHook() -> None:
    """禁用 IPython 的 matplotlib GUI 集成钩子

    在 debugpy 启动 IPython-style 的 matplotlib 集成时,
    会调用 enable_gui("qt") → enable_qt4 → QtGui.QApplication([" "]),
    在 PySide6 下抛 AttributeError。

    本函数通过 monkey-patch IPython 的 inputhook 让它直接返回 None,
    让 debugpy 跳过 GUI 集成逻辑。
    """
    try:
        # IPython 7+ 提供 inputhook 注册机制
        from IPython import get_ipython

        ip = get_ipython()
        if ip is None:
            return
        # 禁用 matplotlib 集成
        if hasattr(ip, "enable_matplotlib"):
            try:
                ip.enable_matplotlib("inline")  # 使用 inline 后端,不交互
                logger.debug("[matplotlib_backend] IPython matplotlib 切换到 inline")
            except Exception as e:
                logger.debug(f"[matplotlib_backend] IPython 切换 inline 失败: {e}")
    except ImportError:
        # 没有 IPython(独立运行),无需处理
        pass
    except Exception as e:
        logger.debug(f"[matplotlib_backend] 跳过 IPython 集成: {e}")


def configureMatplotlibBackend() -> None:
    """配置 matplotlib 后端为 QtAgg

    调用顺序:
        1. 必须在所有 matplotlib.figure / pyplot import 之前
        2. 必须在创建 QApplication 之前
        3. 如果 QtAgg 不可用,降级到 Agg(纯静态图,无交互)

    Returns:
        bool: True 表示成功切换到 QtAgg,False 表示降级到 Agg
    """
    import os

    # 优先级: 显式传入 > 环境变量 > 默认 QtAgg
    desired = os.environ.get("PRISMATICA_MPL_BACKEND", "QtAgg")

    import matplotlib

    # 先尝试 QtAgg(PySide6 / PyQt5 / PyQt6)
    if desired == "QtAgg":
        try:
            matplotlib.use("QtAgg", force=True)
            logger.info("[matplotlib_backend] 已切换到 QtAgg")
            return True
        except Exception as e:
            logger.warning(f"[matplotlib_backend] QtAgg 切换失败: {e}")

    # 降级到 Agg(无交互,适用于纯导出场景)
    try:
        matplotlib.use("Agg", force=True)
        logger.info("[matplotlib_backend] 已降级到 Agg(无交互)")
    except Exception as e:
        logger.error(f"[matplotlib_backend] Agg 切换也失败: {e}")

    return False


def setupPyplotNonInteractive() -> None:
    """关闭 pyplot 的交互模式,防止它抢 Qt 事件循环"""
    try:
        import matplotlib.pyplot as plt

        plt.ioff()  # interactive(False)
        # 不让 pyplot 在关闭 figure 时立即销毁,允许代码引用
        plt.rcParams["figure.max_open_warning"] = 0
    except Exception as e:
        logger.debug(f"[matplotlib_backend] pyplot.ioff 失败: {e}")


def installAll() -> None:
    """一键应用所有 matplotlib 兼容性补丁

    在 main.py 中调用:
        from app.core.utils.matplotlib_backend import installAll
        installAll()
    """
    configureMatplotlibBackend()
    setupPyplotNonInteractive()
    disableIpythonMatplotlibHook()
