# coding: utf-8
"""让顶层窗口始终适配当前屏幕的可用工作区。"""

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QWidget


DEFAULT_SCREEN_MARGIN = 16


def calculateAdaptiveWindowSizes(
    availableSize: QSize,
    preferredSize: QSize,
    baseMinimumSize: QSize,
    margin: int = DEFAULT_SCREEN_MARGIN,
) -> tuple[QSize, QSize]:
    """根据屏幕可用区域计算初始尺寸与不会越界的最小尺寸。"""
    safeMargin = max(0, int(margin))
    usableWidth = max(1, availableSize.width() - safeMargin * 2)
    usableHeight = max(1, availableSize.height() - safeMargin * 2)

    minimumSize = QSize(
        min(max(1, baseMinimumSize.width()), usableWidth),
        min(max(1, baseMinimumSize.height()), usableHeight),
    )
    targetSize = QSize(
        min(max(minimumSize.width(), preferredSize.width()), usableWidth),
        min(max(minimumSize.height(), preferredSize.height()), usableHeight),
    )
    return targetSize, minimumSize


def availableGeometryForWindow(
    window: QWidget,
    screen: QScreen | None = None,
) -> QRect:
    """优先返回窗口所在屏幕的可用区域，并兼容首次显示前的窗口。"""
    targetScreen = screen
    windowHandle = window.windowHandle()
    if targetScreen is None and windowHandle is not None:
        targetScreen = windowHandle.screen()
    if targetScreen is None:
        targetScreen = QGuiApplication.screenAt(window.frameGeometry().center())
    if targetScreen is None:
        targetScreen = QGuiApplication.primaryScreen()
    return targetScreen.availableGeometry() if targetScreen is not None else QRect()


def fitWindowToAvailableScreen(
    window: QWidget,
    preferredSize: QSize,
    baseMinimumSize: QSize,
    *,
    screen: QScreen | None = None,
    margin: int = DEFAULT_SCREEN_MARGIN,
    keepCurrentSize: bool = False,
    centerWindow: bool = True,
) -> QRect:
    """缩放并放置窗口，确保窗口完整位于当前屏幕工作区内。"""
    availableGeometry = availableGeometryForWindow(window, screen)
    if not availableGeometry.isValid():
        return availableGeometry

    targetSize, minimumSize = calculateAdaptiveWindowSizes(
        availableGeometry.size(),
        preferredSize,
        baseMinimumSize,
        margin,
    )
    window.setMinimumSize(minimumSize)

    if keepCurrentSize:
        currentSize = window.size()
        targetSize = QSize(
            min(max(minimumSize.width(), currentSize.width()), targetSize.width()),
            min(max(minimumSize.height(), currentSize.height()), targetSize.height()),
        )
    window.resize(targetSize)

    if centerWindow:
        targetX = availableGeometry.x() + (availableGeometry.width() - targetSize.width()) // 2
        targetY = availableGeometry.y() + (availableGeometry.height() - targetSize.height()) // 2
    else:
        targetX = min(
            max(window.x(), availableGeometry.left()),
            availableGeometry.right() - targetSize.width() + 1,
        )
        targetY = min(
            max(window.y(), availableGeometry.top()),
            availableGeometry.bottom() - targetSize.height() + 1,
        )
    window.move(targetX, targetY)
    return availableGeometry
