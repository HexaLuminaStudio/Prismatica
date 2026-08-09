"""全局页面画布与主导航切换回归测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

from app.view.widgets.page_transition import switchPageInstantly
from app.view.widgets.prismatica_theme import pageBackgroundColor, shellPalette


def _getApp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _RecordingStack(QStackedWidget):
    """记录是否误走带扩展参数的动画切页入口。"""

    def __init__(self) -> None:
        super().__init__()
        self.animatedCallCount = 0

    def setCurrentWidget(self, widget, *args, **kwargs) -> None:
        if args or kwargs:
            self.animatedCallCount += 1
        super().setCurrentWidget(widget)


def test_light_pages_share_soft_gray_background_token():
    assert pageBackgroundColor(False).name().lower() == "#f6f8fa"
    assert shellPalette(False).content == pageBackgroundColor(False)


def test_main_navigation_switches_without_page_animation():
    _getApp()
    view = _RecordingStack()
    firstPage = QWidget()
    secondPage = QWidget()
    view.addWidget(firstPage)
    view.addWidget(secondPage)
    switchPageInstantly(view, secondPage)

    assert view.currentWidget() is secondPage
    assert view.animatedCallCount == 0
