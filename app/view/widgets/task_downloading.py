# coding: utf-8
"""
进行中任务滚动区域
显示正在下载的任务卡片
"""

from app.core.utils import logger
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

from qfluentwidgets import SmoothScrollArea, FluentIcon

from app.core.services import taskManager

from .download_card import DownloadCard


class DownloadingScrollArea(SmoothScrollArea):
    """进行中任务滚动区域"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.scrollWidget = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.scrollWidget)
        self.vBoxLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setSpacing(8)

        # 空状态提示(Fluent 风,沿用 Prismatica 主题色 #00b09c)
        self._initEmptyState()

        # 存储下载卡片
        self.downloadCards = {}

        # 连接信号
        taskManager.taskStarted.connect(self._onTaskStarted)
        taskManager.taskProgress.connect(self._onTaskProgress)
        taskManager.taskCompleted.connect(self._onTaskCompleted)
        taskManager.taskFailed.connect(self._onTaskFailed)
        taskManager.taskCancelled.connect(self._onTaskCancelled)

        self._initWidget()

    def _initWidget(self):
        """初始化组件"""
        self.setObjectName("DownloadingScrollArea")
        self.setViewportMargins(0, 15, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.scrollWidget.setStyleSheet("background:transparent")
        self.setStyleSheet("background:transparent;border:none;")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 恢复进行中的任务
        self._restoreRunningTasks()

    def _initEmptyState(self):
        """初始化 Fluent 风格空状态卡片(简化版)。

        设计稿:88×88 圆形 inbox 图标(主题色淡背景)+ 标题 + 短描述 + 单一主 CTA。
        风格克制,只保留一个动作,避免空状态界面杂乱。
        """
        from PySide6.QtWidgets import QFrame, QLabel, QHBoxLayout
        from qfluentwidgets import IconWidget, PrimaryPushButton

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

        # 卡片阴影(Fluent 风格,轻量)
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        from PySide6.QtGui import QColor

        shadowEffect = QGraphicsDropShadowEffect(self.emptyCard)
        shadowEffect.setBlurRadius(16)
        shadowEffect.setOffset(0, 2)
        shadowEffect.setColor(QColor(0, 0, 0, 10))
        self.emptyCard.setGraphicsEffect(shadowEffect)

        emptyLayout = QVBoxLayout(self.emptyCard)
        emptyLayout.setContentsMargins(32, 40, 32, 40)
        emptyLayout.setSpacing(10)
        emptyLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 图标圈
        iconWrap = QFrame(self.emptyCard)
        iconWrap.setObjectName("emptyIconWrap")
        iconWrap.setFixedSize(88, 88)
        iconLayout = QHBoxLayout(iconWrap)
        iconLayout.setContentsMargins(0, 0, 0, 0)
        iconLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.emptyIcon = IconWidget(FluentIcon.MESSAGE, self.emptyCard)
        try:
            self.emptyIcon.setIcon(FluentIcon.INBOX)
        except Exception:
            try:
                self.emptyIcon.setIcon(FluentIcon.DOWNLOAD)
            except Exception:
                pass
        self.emptyIcon.setFixedSize(40, 40)
        # 主题色描边
        self.emptyIcon.setStyleSheet(
            "background: transparent; color: #00b09c;"
        )
        iconLayout.addWidget(self.emptyIcon)
        emptyLayout.addWidget(iconWrap, 0, Qt.AlignmentFlag.AlignHCenter)

        # 标题
        self.emptyTitleLabel = QLabel("暂无进行中的任务", self.emptyCard)
        self.emptyTitleLabel.setObjectName("emptyTitle")
        self.emptyTitleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emptyLayout.addWidget(self.emptyTitleLabel, 0, Qt.AlignmentFlag.AlignHCenter)

        # 描述(一短句即可)
        self.emptyDescLabel = QLabel(
            "在 HSK 下载或全球中介页面发起任务后,会显示在这里。",
            self.emptyCard,
        )
        self.emptyDescLabel.setObjectName("emptyDesc")
        self.emptyDescLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.emptyDescLabel.setWordWrap(True)
        self.emptyDescLabel.setMaximumWidth(380)
        emptyLayout.addWidget(self.emptyDescLabel, 0, Qt.AlignmentFlag.AlignHCenter)

        # 单一主 CTA(主题色)
        self.goHskButton = PrimaryPushButton("前往 HSK 下载", self.emptyCard)
        self.goHskButton.setFixedHeight(32)
        self.goHskButton.clicked.connect(self._onGoHskClicked)
        emptyLayout.addSpacing(8)
        emptyLayout.addWidget(self.goHskButton, 0, Qt.AlignmentFlag.AlignHCenter)

        self.vBoxLayout.addWidget(
            self.emptyCard, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )

    def _onGoHskClicked(self):
        """点击「前往 HSK 下载」:切到 HSK 下载子界面。"""
        try:
            mainWindow = self.window()
            for attrName in ("hskInterface", "hskCorpusInterface"):
                target = getattr(mainWindow, attrName, None)
                if target is not None and hasattr(mainWindow, "switchTo"):
                    mainWindow.switchTo(target)
                    return
        except Exception as e:
            logger.warning(f"[DownloadingArea] 跳转 HSK 失败: {e}")

    def _restoreRunningTasks(self):
        """恢复进行中的任务"""
        # 获取pending和in_progress状态的任务
        # P0-A1 fix 2026-07-18:走 TaskManager 高阶接口,不再直接调 taskControl
        try:
            pendingTasks = taskManager.getPendingTasksFromDb()
            inProgressTasks = taskManager.getInProgressTasks()

            for task in inProgressTasks + pendingTasks:
                taskId = task.get("id")
                if taskId and taskId not in self.downloadCards:
                    info = task.get("info", {})
                    info["taskId"] = taskId
                    info["type"] = task.get("type")
                    self._createCard(info)
        except Exception as e:
            logger.error(f"[DownloadingArea] 恢复任务失败: {e}")

    def _createCard(self, info: dict):
        """创建下载卡片"""
        taskId = info.get("taskId")
        if not taskId or taskId in self.downloadCards:
            return

        card = DownloadCard(info, self.scrollWidget)
        self.downloadCards[taskId] = card
        self.vBoxLayout.insertWidget(0, card, 0, Qt.AlignmentFlag.AlignTop)
        self._updateEmptyState()

        logger.info(f"[DownloadingArea] 创建下载卡片: {taskId}")

    def _onTaskStarted(self, taskId: str):
        """任务启动时创建卡片"""
        logger.info(f"[DownloadingArea] 任务启动: {taskId}")

        # P0-A1 fix 2026-07-18:走 TaskManager.getTask() 高阶接口
        taskInfo = taskManager.getTask(taskId)
        if not taskInfo:
            logger.warning(f"[DownloadingArea] 任务信息不存在: {taskId}")
            return

        info = taskInfo.get("info", {})
        info["taskId"] = taskId
        info["type"] = taskInfo.get("type")

        self._createCard(info)

    def _onTaskProgress(self, taskId: str, progressInfo: dict):
        """任务进度更新"""
        if taskId not in self.downloadCards:
            return

        card = self.downloadCards[taskId]
        # 检查卡片是否已被删除
        if not card or not card.progressBar:
            self.downloadCards.pop(taskId, None)
            return

        try:
            card.updateProgress(
                progress=progressInfo.get("progress", 0),
                fileCount=progressInfo.get("page", ""),
                speed=progressInfo.get("speed", ""),
                remainingTime=progressInfo.get("time", ""),
            )
        except RuntimeError:
            # 卡片已被删除，移除引用
            self.downloadCards.pop(taskId, None)

    def _onTaskCompleted(self, taskId: str, filePath: str = ""):
        """任务完成时移除卡片"""
        logger.info(f"[DownloadingArea] 任务完成: {taskId}, filePath={filePath}")

        if taskId not in self.downloadCards:
            return

        card = self.downloadCards.pop(taskId, None)
        if card:
            try:
                self.vBoxLayout.removeWidget(card)
                card.setCompleted()
                card.deleteLater()
            except RuntimeError:
                pass  # 卡片已被删除

        self._updateEmptyState()

    def _onTaskFailed(self, taskId: str, error: str):
        """任务失败时移除卡片"""
        logger.error(f"[DownloadingArea] 任务失败: {taskId}, error={error}")

        if taskId not in self.downloadCards:
            return

        card = self.downloadCards.pop(taskId, None)
        if card:
            try:
                self.vBoxLayout.removeWidget(card)
                card.setFailed(error[:20] if error else None)
                card.deleteLater()
            except RuntimeError:
                pass  # 卡片已被删除

        self._updateEmptyState()

    def _onTaskCancelled(self, taskId: str):
        """任务取消时移除卡片"""
        logger.info(f"[DownloadingArea] 任务取消: {taskId}")

        if taskId not in self.downloadCards:
            return

        card = self.downloadCards.pop(taskId, None)
        if card:
            try:
                self.vBoxLayout.removeWidget(card)
                card.deleteLater()
            except RuntimeError:
                pass  # 卡片已被删除

        self._updateEmptyState()

    def _updateEmptyState(self):
        """更新空状态显示"""
        if self.downloadCards:
            if hasattr(self, "emptyCard") and self.emptyCard is not None:
                self.emptyCard.hide()
        else:
            if hasattr(self, "emptyCard") and self.emptyCard is not None:
                self.emptyCard.show()

    def getCard(self, taskId: str) -> DownloadCard:
        """获取指定卡片"""
        return self.downloadCards.get(taskId)
