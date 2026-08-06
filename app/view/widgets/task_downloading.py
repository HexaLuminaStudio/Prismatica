# coding: utf-8
"""进行中任务列表。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from qfluentwidgets import SmoothScrollArea

from app.core.services import taskManager
from app.core.utils import logger

from .download_card import DownloadCard, buildTaskCardInfo
from .task_empty_state import TaskEmptyState


class DownloadingScrollArea(SmoothScrollArea):
    """展示 pending / in_progress 任务并实时接收进度。"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.downloadCards: dict[str, DownloadCard] = {}

        self.scrollWidget = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.scrollWidget)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setSpacing(12)

        self._initEmptyState()
        self._initWidget()
        self._connectSignals()
        self.reloadTasks()

    def _initWidget(self) -> None:
        self.setObjectName("DownloadingScrollArea")
        self.setViewportMargins(0, 12, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.scrollWidget.setStyleSheet("background: transparent;")
        self.setStyleSheet("background: transparent; border: none;")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _connectSignals(self) -> None:
        if hasattr(taskManager, "taskCreated"):
            taskManager.taskCreated.connect(self._onTaskCreated)
        taskManager.taskStarted.connect(self._onTaskStarted)
        taskManager.taskProgress.connect(self._onTaskProgress)
        taskManager.taskCompleted.connect(self._onTaskCompleted)
        taskManager.taskFailed.connect(self._onTaskFailed)
        taskManager.taskCancelled.connect(self._onTaskCancelled)
        taskManager.taskDeleted.connect(self.removeCard)

    def _initEmptyState(self) -> None:
        self.emptyCard = TaskEmptyState(
            "暂无任务",
            "在 HSK 下载或全球中介页面发起任务后会显示在这里。\n"
            "任务面板会统一跟踪语料下载、队列等待和执行状态。",
            primaryText="前往 HSK 下载",
            primaryAction=self._onGoHskClicked,
            secondaryText="全球中介市场",
            secondaryAction=self._onGoGlobalClicked,
            shortcutText="ⓘ 支持快捷键 Ctrl + N",
            parent=self.scrollWidget,
        )
        self.vBoxLayout.addWidget(self.emptyCard)

    def _switchToMainWindowTarget(self, *attrNames: str) -> None:
        try:
            mainWindow = self.window()
            for attrName in attrNames:
                target = getattr(mainWindow, attrName, None)
                if target is not None and hasattr(mainWindow, "switchTo"):
                    mainWindow.switchTo(target)
                    return
            logger.warning(
                f"[DownloadingArea] 页面跳转目标不存在: targets={attrNames}"
            )
        except Exception as exc:
            logger.warning(f"[DownloadingArea] 页面跳转失败: {exc}")

    def _onGoHskClicked(self) -> None:
        self._switchToMainWindowTarget("hskInterface", "hskCorpusInterface")

    def _onGoGlobalClicked(self) -> None:
        self._switchToMainWindowTarget("globalInterface")

    def reloadTasks(self) -> None:
        """从数据库重新加载进行中任务。"""
        self._clearCards()
        try:
            inProgressTasks = taskManager.getInProgressTasks()
            pendingTasks = taskManager.getPendingTasksFromDb()
            for task in reversed(inProgressTasks + pendingTasks):
                self._createCard(buildTaskCardInfo(task))
            logger.info(
                f"[DownloadingArea] 已刷新任务列表, "
                f"running={len(inProgressTasks)}, pending={len(pendingTasks)}"
            )
        except Exception as exc:
            logger.exception(f"[DownloadingArea] 恢复任务失败: {exc}")
        self._updateEmptyState()

    def _clearCards(self) -> None:
        for card in self.downloadCards.values():
            self.vBoxLayout.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.downloadCards.clear()

    def _createCard(self, info: dict) -> DownloadCard | None:
        taskId = info.get("taskId")
        if not taskId:
            logger.warning("[DownloadingArea] 忽略缺少 taskId 的任务记录")
            return None
        existing = self.downloadCards.get(taskId)
        if existing is not None:
            return existing
        card = DownloadCard(info, self.scrollWidget)
        self.downloadCards[taskId] = card
        self.vBoxLayout.insertWidget(0, card)
        self._updateEmptyState()
        return card

    def _loadTaskCard(self, taskId: str) -> DownloadCard | None:
        task = taskManager.getTask(taskId)
        if not task:
            logger.warning(f"[DownloadingArea] 任务信息不存在: {taskId}")
            return None
        return self._createCard(buildTaskCardInfo(task))

    def _onTaskCreated(self, taskId: str) -> None:
        card = self._loadTaskCard(taskId)
        if card is not None:
            card.setQueued()

    def _onTaskStarted(self, taskId: str) -> None:
        card = self.downloadCards.get(taskId) or self._loadTaskCard(taskId)
        if card is not None:
            card.setRunning()

    def _onTaskProgress(self, taskId: str, progressInfo: dict) -> None:
        card = self.downloadCards.get(taskId)
        if card is None:
            return
        try:
            card.updateProgress(
                progress=progressInfo.get("progress", 0),
                fileCount=progressInfo.get("page", ""),
                speed=progressInfo.get("speed", ""),
                remainingTime=progressInfo.get("time", ""),
            )
        except RuntimeError:
            self.downloadCards.pop(taskId, None)

    def _onTaskCompleted(self, taskId: str, _filePath: str = "") -> None:
        self.removeCard(taskId)

    def _onTaskFailed(self, taskId: str, _error: str) -> None:
        self.removeCard(taskId)

    def _onTaskCancelled(self, taskId: str) -> None:
        self.removeCard(taskId)

    def removeCard(self, taskId: str) -> None:
        card = self.downloadCards.pop(taskId, None)
        if card is not None:
            try:
                self.vBoxLayout.removeWidget(card)
                card.hide()
                card.deleteLater()
            except RuntimeError:
                pass
        self._updateEmptyState()

    def _updateEmptyState(self) -> None:
        self.emptyCard.setVisible(not self.downloadCards)

    def getCard(self, taskId: str) -> DownloadCard | None:
        return self.downloadCards.get(taskId)
