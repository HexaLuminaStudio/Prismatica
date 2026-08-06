# coding: utf-8
"""失败与取消任务列表。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from qfluentwidgets import FluentIcon, SmoothScrollArea

from app.core.services import taskManager
from app.core.utils import logger

from .download_card import DownloadCard, buildTaskCardInfo
from .task_empty_state import TaskEmptyState


class FailedScrollArea(SmoothScrollArea):
    """展示失败和用户取消的终态任务。"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.failedCards: dict[str, DownloadCard] = {}

        self.scrollWidget = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.scrollWidget)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setSpacing(12)

        self.emptyCard = TaskEmptyState(
            "暂无失败任务",
            "执行异常、重试耗尽或手动取消的任务会显示在这里。",
            icon=FluentIcon.CLOSE,
            parent=self.scrollWidget,
        )
        self.vBoxLayout.addWidget(self.emptyCard)
        self._initWidget()

        taskManager.taskFailed.connect(self._onTaskFailed)
        taskManager.taskCancelled.connect(self._onTaskCancelled)
        taskManager.taskDeleted.connect(self.removeCard)
        self.reloadTasks()

    def _initWidget(self) -> None:
        self.setObjectName("FailedScrollArea")
        self.setViewportMargins(0, 12, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.scrollWidget.setStyleSheet("background: transparent;")
        self.setStyleSheet("background: transparent; border: none;")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def reloadTasks(self) -> None:
        self._clearCards()
        try:
            tasks = taskManager.getFailedTasks(includeCancelled=True)
            for task in reversed(tasks):
                self._addTask(task)
            logger.info(f"[FailedArea] 已刷新失败任务, count={len(tasks)}")
        except Exception as exc:
            logger.exception(f"[FailedArea] 恢复失败任务失败: {exc}")
        self._updateEmptyState()

    def _clearCards(self) -> None:
        for card in self.failedCards.values():
            self.vBoxLayout.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.failedCards.clear()

    def _addTask(self, task: dict) -> None:
        taskId = task.get("id")
        if not taskId or taskId in self.failedCards:
            return
        card = DownloadCard(buildTaskCardInfo(task), self.scrollWidget)
        if task.get("status") == "cancelled":
            card.setCancelled()
        else:
            card.setFailed(task.get("error") or "任务执行失败,未返回详细原因")
        self.failedCards[taskId] = card
        self.vBoxLayout.insertWidget(0, card)
        self._updateEmptyState()

    def _onTaskFailed(self, taskId: str, _error: str) -> None:
        task = taskManager.getTask(taskId)
        if task:
            self._addTask(task)

    def _onTaskCancelled(self, taskId: str) -> None:
        task = taskManager.getTask(taskId)
        if task:
            self._addTask(task)

    def removeCard(self, taskId: str) -> None:
        card = self.failedCards.pop(taskId, None)
        if card is not None:
            try:
                self.vBoxLayout.removeWidget(card)
                card.hide()
                card.deleteLater()
            except RuntimeError:
                pass
        self._updateEmptyState()

    def _updateEmptyState(self) -> None:
        self.emptyCard.setVisible(not self.failedCards)
