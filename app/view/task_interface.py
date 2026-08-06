# coding: utf-8
"""任务管理界面。"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    FluentIcon,
    Pivot,
    PrimaryPushButton,
    PushButton,
    isDarkTheme,
)
from app.core.services import taskManager
from app.core.utils import logger

from .widgets.task_downloaded import DownloadedScrollArea
from .widgets.task_downloading import DownloadingScrollArea
from .widgets.task_failed import FailedScrollArea


class TaskInterface(QWidget):
    """任务管理主界面,提供进行中、已完成和失败三个状态视图。"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("TaskInterface")
        self._statsRefreshPending = False
        self._initUi()
        self._connectSignals()
        self.refreshStats()

    def _initUi(self) -> None:
        # 设计稿字体栈为 Segoe UI + 微软雅黑。显式设置中文回退,
        # 避免部分精简版 Windows / 离屏渲染环境出现方框字形。
        taskFont = QFont("Microsoft YaHei UI", 9)
        taskFont.setFamilies(
            ["Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"]
        )
        self.setFont(taskFont)

        self.pivot = Pivot(self)
        self.pivot.setObjectName("taskPivot")
        self.pivot.setItemFontSize(13)

        # 任务页只需要稳定的状态切换,使用 Qt 原生堆叠容器可避免为简单
        # 切页引入额外授权依赖,也让离线发布环境更可靠。
        self.stackedWidget = QStackedWidget(self)
        self.downloadingScrollArea = DownloadingScrollArea(self.stackedWidget)
        self.downloadedScrollArea = DownloadedScrollArea(self.stackedWidget)
        self.failedScrollArea = FailedScrollArea(self.stackedWidget)

        pages = (
            (
                "inProgress",
                "进行中",
                FluentIcon.SYNC,
                self.downloadingScrollArea,
            ),
            ("completed", "已完成", FluentIcon.ACCEPT, self.downloadedScrollArea),
            ("failed", "失败", FluentIcon.CLOSE, self.failedScrollArea),
        )
        for routeKey, text, icon, page in pages:
            self.pivot.addItem(
                routeKey,
                text,
                icon=icon,
                onClick=lambda checked=False, p=page: self.stackedWidget.setCurrentWidget(
                    p
                ),
            )
            self.stackedWidget.addWidget(page)

        self.pivot.setCurrentItem("inProgress")
        self.stackedWidget.setCurrentWidget(self.downloadingScrollArea)

        self.refreshButton = PushButton(FluentIcon.SYNC, "刷新", self)
        self.refreshButton.setFixedHeight(34)
        self.refreshButton.clicked.connect(self.refreshAll)

        self.newTaskButton = PrimaryPushButton(FluentIcon.ADD, "新建任务", self)
        self.newTaskButton.setFixedHeight(34)
        self.newTaskButton.clicked.connect(self._navigateToNewTask)

        toolbar = QFrame(self)
        toolbar.setObjectName("taskToolbar")
        toolbarLayout = QHBoxLayout(toolbar)
        toolbarLayout.setContentsMargins(4, 3, 4, 3)
        toolbarLayout.setSpacing(8)
        toolbarLayout.addWidget(self.pivot, 0, Qt.AlignmentFlag.AlignLeft)
        toolbarLayout.addStretch(1)
        toolbarLayout.addWidget(self.refreshButton)
        toolbarLayout.addWidget(self.newTaskButton)

        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(20, 14, 20, 12)
        self.vBoxLayout.setSpacing(4)
        self.vBoxLayout.addWidget(toolbar)
        self.vBoxLayout.addWidget(self.stackedWidget, 1)

        self.newTaskShortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self.newTaskShortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.newTaskShortcut.activated.connect(self._navigateToNewTask)
        self._applyStyle()

    def _connectSignals(self) -> None:
        signals = [
            taskManager.taskStarted,
            taskManager.taskCompleted,
            taskManager.taskFailed,
            taskManager.taskCancelled,
            taskManager.taskDeleted,
        ]
        if hasattr(taskManager, "taskCreated"):
            signals.append(taskManager.taskCreated)
        for signal in signals:
            signal.connect(self._scheduleStatsRefresh)

    def _scheduleStatsRefresh(self, *_args) -> None:
        if self._statsRefreshPending:
            return
        self._statsRefreshPending = True
        QTimer.singleShot(0, self._flushStatsRefresh)

    def _flushStatsRefresh(self) -> None:
        self._statsRefreshPending = False
        self.refreshStats()

    def refreshStats(self) -> None:
        stats = taskManager.getTaskStats()
        running = stats.get("pending", 0) + stats.get("inProgress", 0)
        completed = stats.get("completed", 0)
        failed = stats.get("failed", 0) + stats.get("cancelled", 0)
        self.pivot.setItemText("inProgress", f"进行中  {running}")
        self.pivot.setItemText("completed", f"已完成  {completed}")
        self.pivot.setItemText("failed", f"失败  {failed}")

    def refreshAll(self) -> None:
        """手动刷新三个列表和数量徽标。"""
        self.refreshButton.setEnabled(False)
        try:
            self.downloadingScrollArea.reloadTasks()
            self.downloadedScrollArea.reloadTasks()
            self.failedScrollArea.reloadTasks()
            self.refreshStats()
            logger.info("[TaskInterface] 用户刷新任务页面")
        finally:
            self.refreshButton.setEnabled(True)

    def _navigateToNewTask(self) -> None:
        try:
            mainWindow = self.window()
            for attrName in ("hskInterface", "hskCorpusInterface"):
                target = getattr(mainWindow, attrName, None)
                if target is not None and hasattr(mainWindow, "switchTo"):
                    mainWindow.switchTo(target)
                    return
            logger.warning("[TaskInterface] 新建任务跳转失败: 找不到 HSK 页面")
        except Exception as exc:
            logger.warning(f"[TaskInterface] 新建任务跳转异常: {exc}")

    def _applyStyle(self) -> None:
        dark = isDarkTheme()
        border = "#3b3b3b" if dark else "#e5e5e5"
        muted = "#303030" if dark else "#f5f5f5"
        self.setStyleSheet(
            f"""
            QWidget#TaskInterface {{ background: transparent; }}
            QFrame#taskToolbar {{
                background: {muted};
                border: 1px solid {border};
                border-radius: 7px;
            }}
            """
        )

    def showEvent(self, event) -> None:
        self.refreshStats()
        super().showEvent(event)
