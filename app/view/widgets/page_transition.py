"""主窗口业务页面切换策略。"""

from PySide6.QtWidgets import QStackedWidget, QWidget


def switchPageInstantly(view: QStackedWidget, interface: QWidget) -> None:
    """直接提交目标页，避免整页动画重复绘制复杂控件。"""
    QStackedWidget.setCurrentWidget(view, interface)


__all__ = ["switchPageInstantly"]
