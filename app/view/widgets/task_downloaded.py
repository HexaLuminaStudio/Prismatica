# coding: utf-8
"""
已完成任务滚动区域
显示已完成的任务记录
"""

from app.core.utils import logger
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

from qfluentwidgets import SmoothScrollArea, BodyLabel

from app.core.services import taskManager

from .download_card import DownloadCard


class DownloadedScrollArea(SmoothScrollArea):
    """已完成任务滚动区域"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.scrollWidget = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.scrollWidget)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setSpacing(8)

        # 空状态提示
        self.emptyLabel = BodyLabel("暂无已完成的任务", self)
        self.emptyLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.emptyLabel.setStyleSheet("color: #999; font-size: 14px;")
        self.vBoxLayout.addWidget(self.emptyLabel)

        # 存储已完成卡片
        self.completedCards = {}

        # 连接信号
        taskManager.taskCompleted.connect(self._onTaskCompleted)
        taskManager.taskFailed.connect(self._onTaskFailed)
        # P0-fix:监听任务删除信号,完成卡片被删除时同步从字典移除,
        # 避免 completedCards 里残留悬空引用。
        taskManager.taskDeleted.connect(self.removeCard)

        self._initWidget()

    def _initWidget(self):
        """初始化组件"""
        self.setObjectName("DownloadedScrollArea")
        self.setViewportMargins(0, 15, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.scrollWidget.setStyleSheet("background:transparent")
        self.setStyleSheet("background:transparent;border:none;")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 恢复已完成的任务
        self._restoreCompletedTasks()

    def _restoreCompletedTasks(self):
        """恢复已完成的任务"""
        # P0-A1 fix 2026-07-18:走 TaskManager.getDoneTasks() 高阶接口
        try:
            completedTasks = taskManager.getDoneTasks()
            for task in completedTasks:
                taskId = task.get("id")
                if taskId and taskId not in self.completedCards:
                    info = task.get("info", {})
                    info["taskId"] = taskId
                    info["type"] = task.get("type")

                    card = DownloadCard(info, self.scrollWidget)

                    # 设置完成状态
                    status = task.get("status")
                    if status == "completed":
                        card.setCompleted()
                    else:
                        errorMsg = task.get("error", "")
                        card.setFailed(errorMsg[:20] if errorMsg else None)

                    self.completedCards[taskId] = card
                    self.vBoxLayout.insertWidget(0, card, 0, Qt.AlignmentFlag.AlignTop)

            self._updateEmptyState()
            logger.info(f"[DownloadedArea] 恢复 {len(completedTasks)} 个已完成任务")
        except Exception as e:
            logger.error(f"[DownloadedArea] 恢复已完成任务失败: {e}")

    def _onTaskCompleted(self, taskId: str):
        """任务完成时添加卡片"""
        logger.info(f"[DownloadedArea] 任务完成: {taskId}")

        try:
            # P0-A1 fix 2026-07-18:走 TaskManager.getTask() 高阶接口
            taskInfo = taskManager.getTask(taskId)
            if not taskInfo:
                return

            info = taskInfo.get("info", {})
            info["taskId"] = taskId
            info["type"] = taskInfo.get("type")

            card = DownloadCard(info, self.scrollWidget)
            card.setCompleted()

            self.completedCards[taskId] = card
            self.vBoxLayout.insertWidget(0, card, 0, Qt.AlignmentFlag.AlignTop)
            self._updateEmptyState()

        except Exception as e:
            logger.error(f"[DownloadedArea] 添加完成卡片失败: {e}")

    def _onTaskFailed(self, taskId: str, error: str):
        """任务失败时添加卡片"""
        logger.info(f"[DownloadedArea] 任务失败: {taskId}")

        try:
            # P0-A1 fix 2026-07-18:走 TaskManager.getTask() 高阶接口
            taskInfo = taskManager.getTask(taskId)
            if not taskInfo:
                return

            info = taskInfo.get("info", {})
            info["taskId"] = taskId
            info["type"] = taskInfo.get("type")

            card = DownloadCard(info, self.scrollWidget)
            card.setFailed(error[:20] if error else None)

            self.completedCards[taskId] = card
            self.vBoxLayout.insertWidget(0, card, 0, Qt.AlignmentFlag.AlignTop)
            self._updateEmptyState()

        except Exception as e:
            logger.error(f"[DownloadedArea] 添加失败卡片失败: {e}")

    def removeCard(self, taskId: str):
        """移除指定卡片"""
        if taskId in self.completedCards:
            card = self.completedCards.pop(taskId)
            try:
                self.vBoxLayout.removeWidget(card)
                card.deleteLater()
            except RuntimeError:
                pass  # 卡片已被删除
            self._updateEmptyState()

    def clearAll(self):
        """清除所有已完成卡片"""
        for card in self.completedCards.values():
            self.vBoxLayout.removeWidget(card)
            card.deleteLater()
        self.completedCards.clear()
        self._updateEmptyState()

    def _updateEmptyState(self):
        """更新空状态显示"""
        if self.completedCards:
            self.emptyLabel.hide()
        else:
            self.emptyLabel.show()
