# coding: utf-8
"""
已完成任务滚动区域
显示已完成的任务记录
"""

from app.core.utils import logger
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

from qfluentwidgets import SmoothScrollArea, FluentIcon

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

        # 空状态提示(Fluent 风,沿用 Prismatica 主题色 #00b09c)
        self._initEmptyState()

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

    def _initEmptyState(self):
        """初始化 Fluent 风格空状态卡片(已完成页,简化版)。

        设计稿:88×88 圆形 check 图标 + 标题 + 短描述。无 CTA,纯信息表达。
        """
        from PySide6.QtWidgets import QFrame, QLabel, QHBoxLayout, QGraphicsDropShadowEffect
        from PySide6.QtGui import QColor
        from qfluentwidgets import IconWidget

        self.emptyCard = QFrame(self.scrollWidget)
        self.emptyCard.setObjectName("taskEmptyCard")
        self.emptyCard.setMinimumHeight(320)
        self.emptyCard.setStyleSheet(
            """
            #taskEmptyCard {
                background: #ffffff;
                border: 1px solid #e5e5e5;
                border-radius: 8px;
            }
            #taskEmptyCard #emptyIconWrap {
                background: rgba(0, 176, 156, 0.08);
                border-radius: 9999px;
            }
            #taskEmptyCard QLabel {
                background: transparent;
            }
            #taskEmptyCard #emptyTitle {
                font-size: 17px;
                font-weight: 600;
                color: #1f1f1f;
            }
            #taskEmptyCard #emptyDesc {
                font-size: 13px;
                color: #616161;
                line-height: 1.6;
            }
            """
        )

        shadowEffect = QGraphicsDropShadowEffect(self.emptyCard)
        shadowEffect.setBlurRadius(16)
        shadowEffect.setOffset(0, 2)
        shadowEffect.setColor(QColor(0, 0, 0, 10))
        self.emptyCard.setGraphicsEffect(shadowEffect)

        emptyLayout = QVBoxLayout(self.emptyCard)
        emptyLayout.setContentsMargins(32, 40, 32, 40)
        emptyLayout.setSpacing(10)
        emptyLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        iconWrap = QFrame(self.emptyCard)
        iconWrap.setObjectName("emptyIconWrap")
        iconWrap.setFixedSize(88, 88)
        iconLayout = QHBoxLayout(iconWrap)
        iconLayout.setContentsMargins(0, 0, 0, 0)
        iconLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.emptyIcon = IconWidget(FluentIcon.MESSAGE, self.emptyCard)
        try:
            self.emptyIcon.setIcon(FluentIcon.ACCEPT)
        except Exception:
            pass
        self.emptyIcon.setFixedSize(40, 40)
        self.emptyIcon.setStyleSheet("background: transparent; color: #00b09c;")
        iconLayout.addWidget(self.emptyIcon)
        emptyLayout.addWidget(iconWrap, 0, Qt.AlignmentFlag.AlignHCenter)

        self.emptyTitleLabel = QLabel("暂无已完成的任务", self.emptyCard)
        self.emptyTitleLabel.setObjectName("emptyTitle")
        self.emptyTitleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emptyLayout.addWidget(self.emptyTitleLabel, 0, Qt.AlignmentFlag.AlignHCenter)

        self.emptyDescLabel = QLabel(
            "完成的任务会自动归档在这里。",
            self.emptyCard,
        )
        self.emptyDescLabel.setObjectName("emptyDesc")
        self.emptyDescLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.emptyDescLabel.setWordWrap(True)
        self.emptyDescLabel.setMaximumWidth(380)
        emptyLayout.addWidget(self.emptyDescLabel, 0, Qt.AlignmentFlag.AlignHCenter)

        self.vBoxLayout.addWidget(
            self.emptyCard, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )

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
            if hasattr(self, "emptyCard") and self.emptyCard is not None:
                self.emptyCard.hide()
        else:
            if hasattr(self, "emptyCard") and self.emptyCard is not None:
                self.emptyCard.show()
