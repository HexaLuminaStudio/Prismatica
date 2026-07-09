# coding: utf-8
"""
任务管理界面
显示进行中和已完成的任务
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

from qfluentwidgets import Pivot
from qfluentwidgetspro import SlideAniStackedWidget

from .widgets.task_downloading import DownloadingScrollArea
from .widgets.task_downloaded import DownloadedScrollArea
from .widgets.download_card import DownloadCard

class TaskInterface(QWidget):
    """任务管理主界面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("TaskInterface")
        self._init_ui()

    def _init_ui(self):
        """初始化UI"""
        # 标签页切换器
        self.pivot = Pivot(self)

        # 页面容器
        self.stackedWidget = SlideAniStackedWidget(self)

        # 进行中页面
        self.downloadingScrollArea = DownloadingScrollArea(self.stackedWidget)

        # 已完成页面
        self.downloadedScrollArea = DownloadedScrollArea(self.stackedWidget)

        # 添加标签页
        self.pivot.addItem(
            "inProgress",
            "进行中",
            onClick=lambda: self.stackedWidget.setCurrentWidget(
                self.downloadingScrollArea
            ),
        )
        self.pivot.addItem(
            "completed",
            "已完成",
            onClick=lambda: self.stackedWidget.setCurrentWidget(
                self.downloadedScrollArea
            ),
        )
        self.pivot.setCurrentItem("inProgress")

        # 添加页面到堆叠窗口
        self.stackedWidget.addWidget(self.downloadingScrollArea)
        self.stackedWidget.addWidget(self.downloadedScrollArea)
        self.stackedWidget.setCurrentWidget(self.downloadingScrollArea)

        # 布局
        self.__init_layout()
    
    def __init_layout(self):
        """设置布局"""
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(15, 15, 15, 5)
        self.vBoxLayout.addWidget(
            self.pivot, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.vBoxLayout.addWidget(self.stackedWidget)
