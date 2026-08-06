# coding: utf-8
"""
下载卡片组件
简约现代风格的横向下载任务卡片
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFrame

from app.core.utils import logger

from app.core.services import taskManager

from qfluentwidgets import (
    CardWidget,
    BodyLabel,
    ProgressBar,
    IconWidget,
    PrimaryPushButton,
    ToolButton,
    MessageBox,
)

from qfluentwidgets import FluentIcon


class DownloadCard(CardWidget):
    """简约现代风格的下载任务卡片"""

    def __init__(self, info_dict: dict, parent=None):
        super().__init__(parent=parent)
        self.infoDict = info_dict
        self.taskType = info_dict.get("type", "hskDownload")
        self.taskId = info_dict.get("taskId", "")
        self.isPaused = False

        self._initUi()
        self._setupStyle()

    def _initUi(self):
        """初始化UI"""
        self.setFixedHeight(88)
        self.setMinimumWidth(400)

        mainLayout = QHBoxLayout(self)
        mainLayout.setContentsMargins(16, 12, 12, 12)
        mainLayout.setSpacing(16)

        # 左侧：类型图标
        self._initIconArea(mainLayout)

        # 中间：信息区域
        self._initInfoArea(mainLayout)

        # 右侧：操作按钮
        self._initActionArea(mainLayout)

    def _initIconArea(self, parentLayout):
        """初始化图标区域"""
        iconPath = (
            ":app/icons/Hsk.svg"
            if self.taskType == "hskDownload"
            else ":app/icons/Global.svg"
        )
        self.iconWidget = IconWidget(iconPath, self)
        self.iconWidget.setFixedSize(48, 48)
        parentLayout.addWidget(
            self.iconWidget,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

    def _initInfoArea(self, parentLayout):
        """初始化信息区域"""
        infoWidget = QFrame(self)
        infoWidget.setObjectName("infoWidget")

        infoLayout = QVBoxLayout(infoWidget)
        infoLayout.setContentsMargins(0, 0, 0, 0)
        infoLayout.setSpacing(6)

        # 标题行
        titleLayout = QHBoxLayout()
        titleLayout.setSpacing(8)

        # 类型标签
        typeText = "HSK" if self.taskType == "hskDownload" else "Global"
        self.typeLabel = BodyLabel(typeText, self)
        self.typeLabel.setStyleSheet("font-weight: 600; color: #0078D4;")

        # 参数摘要
        payload = self.infoDict.get("payload", {})
        if isinstance(payload, dict):
            params = ", ".join(str(v) for v in payload.values() if v)
        else:
            params = ""
        self.paramsLabel = BodyLabel(
            params[:30] + ("..." if len(params) > 30 else ""), self
        )
        self.paramsLabel.setStyleSheet("color: #666;")

        titleLayout.addWidget(self.typeLabel)
        titleLayout.addWidget(self.paramsLabel, 1)
        infoLayout.addLayout(titleLayout)

        # 进度条
        self.progressBar = ProgressBar(self)
        self.progressBar.setFixedHeight(4)
        self.progressBar.setContentsMargins(0, 0, 0, 0)
        infoLayout.addWidget(self.progressBar)

        # 状态行
        statusLayout = QHBoxLayout()
        statusLayout.setSpacing(16)

        # 文件数
        self.fileLabel = BodyLabel("等待中...", self)
        self.fileLabel.setStyleSheet("color: #888; font-size: 12px;")

        # 速度
        self.speedLabel = BodyLabel("", self)
        self.speedLabel.setStyleSheet("color: #888; font-size: 12px;")

        # 剩余时间
        self.timeLabel = BodyLabel("", self)
        self.timeLabel.setStyleSheet("color: #888; font-size: 12px;")

        statusLayout.addWidget(self.fileLabel)
        statusLayout.addWidget(self.speedLabel)
        statusLayout.addWidget(self.timeLabel)
        statusLayout.addStretch()

        infoLayout.addLayout(statusLayout)

        parentLayout.addWidget(infoWidget, 1)

    def _initActionArea(self, parentLayout):
        """初始化操作按钮区域"""
        buttonWidget = QFrame(self)
        buttonLayout = QHBoxLayout(buttonWidget)
        buttonLayout.setContentsMargins(0, 0, 0, 0)
        buttonLayout.setSpacing(8)

        # 暂停/继续按钮
        self.pauseButton = ToolButton(FluentIcon.PAUSE, self)
        self.pauseButton.setFixedSize(32, 32)
        self.pauseButton.clicked.connect(self._onPauseClicked)

        # 取消/删除按钮
        self.cancelButton = ToolButton(FluentIcon.CLOSE, self)
        self.cancelButton.setFixedSize(32, 32)
        self.cancelButton.clicked.connect(self._onCancelClicked)

        buttonLayout.addWidget(self.pauseButton)
        buttonLayout.addWidget(self.cancelButton)

        parentLayout.addWidget(
            buttonWidget,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

    def _addDeleteButton(self):
        """添加删除按钮到卡片"""
        from qfluentwidgets import ToolButton
        from qfluentwidgets import FluentIcon

        self.deleteButton = ToolButton(FluentIcon.DELETE, self)
        self.deleteButton.setFixedSize(32, 32)
        self.deleteButton.clicked.connect(self._onDeleteClicked)

        # 获取按钮布局并添加删除按钮
        buttonWidget = self.cancelButton.parent()
        if buttonWidget:
            layout = buttonWidget.layout()
            if layout:
                layout.addWidget(self.deleteButton)

    def _addRedownloadButton(self):
        """添加重新下载按钮到卡片"""
        from qfluentwidgets import ToolButton
        from qfluentwidgets import FluentIcon

        self.redownloadButton = ToolButton(FluentIcon.SYNC, self)
        self.redownloadButton.setFixedSize(32, 32)
        self.redownloadButton.clicked.connect(self._onRedownloadClicked)

        # 获取按钮布局并添加重新下载按钮
        buttonWidget = self.cancelButton.parent()
        if buttonWidget:
            layout = buttonWidget.layout()
            if layout:
                # 如果删除按钮已存在，在其之前插入；否则添加到末尾
                if hasattr(self, "deleteButton"):
                    deleteIndex = layout.indexOf(self.deleteButton)
                    if deleteIndex >= 0:
                        layout.insertWidget(deleteIndex, self.redownloadButton)
                    else:
                        layout.addWidget(self.redownloadButton)
                else:
                    layout.addWidget(self.redownloadButton)

    def _onRedownloadClicked(self):
        """重新下载按钮点击"""
        from app.core.services import taskManager
        from qfluentwidgets import InfoBar, InfoBarIcon, InfoBarPosition

        # 从 infoDict 获取下载参数
        infoDict = self.infoDict.copy()
        taskType = infoDict.get("type", "hskDownload")

        # 移除 taskId（新建任务会生成新的）
        if "taskId" in infoDict:
            del infoDict["taskId"]

        try:
            # 创建新任务
            newTaskId = taskManager.createTask(taskType, infoDict)
            logger.info(f"[DownloadCard] 重新创建任务: {newTaskId}")

            # 显示成功提示
            InfoBar.success(
                "任务已创建",
                f"重新下载任务已加入队列",
                Qt.Orientation.Horizontal,
                True,
                3000,
                InfoBarPosition.TOP_RIGHT,
                self.window(),
            )
        except Exception as e:
            logger.error(f"[DownloadCard] 重新创建任务失败: {e}")
            InfoBar.error(
                "创建失败",
                f"重新下载任务失败: {str(e)}",
                Qt.Orientation.Horizontal,
                True,
                3000,
                InfoBarPosition.TOP_RIGHT,
                self.window(),
            )

    def _setupStyle(self):
        """设置样式"""
        self.setStyleSheet(
            """
            DownloadCard {
                background: transparent;
            }
            #infoWidget {
                background: rgba(255, 255, 255, 0.6);
                border-radius: 8px;
                padding: 8px;
            }
            QFrame#infoWidget {
                border: 1px solid rgba(0, 0, 0, 0.06);
            }
        """
        )

    def updateProgress(
        self,
        progress: int,
        fileCount: str = None,
        speed: str = None,
        remainingTime: str = None,
    ):
        """更新进度信息"""
        # 如果任务已暂停，不更新进度
        if self.isPaused:
            return

        self.progressBar.setValue(progress)

        if fileCount is not None:
            self.fileLabel.setText(fileCount)
        if speed is not None:
            self.speedLabel.setText(speed)
        if remainingTime is not None:
            self.timeLabel.setText(remainingTime)

    def setCompleted(self):
        """设置为完成状态"""
        self.progressBar.setValue(100)
        self.fileLabel.setText("已完成")
        self.speedLabel.setText("")
        self.timeLabel.setText("")
        self.pauseButton.setEnabled(False)
        # 取消按钮改为打开文件夹按钮
        self.cancelButton.setIcon(FluentIcon.FOLDER)
        self.cancelButton.clicked.disconnect()
        self.cancelButton.clicked.connect(self._onOpenFolderClicked)
        # 添加重新下载按钮
        self._addRedownloadButton()
        # 添加删除按钮
        self._addDeleteButton()
        self._setCompletedStyle()

    def setFailed(self, error: str = None):
        """设置为失败状态"""
        self.progressBar.setCustomBarColor("#E74856", "#FF6B6B")
        if error:
            self.fileLabel.setText(f"失败: {error[:20]}")
        else:
            self.fileLabel.setText("下载失败")
        self.pauseButton.setEnabled(False)
        self._setFailedStyle()

    def _setCompletedStyle(self):
        """设置完成样式"""
        self.typeLabel.setStyleSheet("font-weight: 600; color: #107C10;")

    def _setFailedStyle(self):
        """设置失败样式"""
        self.typeLabel.setStyleSheet("font-weight: 600; color: #E74856;")

    def _onPauseClicked(self):
        """暂停/继续按钮点击"""
        from app.core.services import taskManager

        if not self.isPaused:
            # 暂停任务
            self.isPaused = True
            self.pauseButton.setIcon(FluentIcon.PLAY)
            self.fileLabel.setText("已暂停")
            taskManager.pauseTask(self.taskId)
        else:
            # 恢复任务
            self.isPaused = False
            self.pauseButton.setIcon(FluentIcon.PAUSE)
            taskManager.resumeTask(self.taskId)

    def _onCancelClicked(self):
        """取消按钮点击"""
        confirmDialog = MessageBox(
            "确认取消", "确定要取消这个下载任务吗？", self.window()
        )
        confirmDialog.yesButton.setText("确定")
        confirmDialog.cancelButton.setText("取消")

        if confirmDialog.exec():
            from app.core.services import taskManager

            taskManager.stopTask(self.taskId)

            try:
                self.pauseButton.clicked.disconnect()
                self.cancelButton.clicked.disconnect()
            except Exception:
                pass

            self.deleteLater()

    def _onDeleteClicked(self):
        """删除按钮点击

        P0-fix:统一走 TaskManager.removeTask,它会:
        1. 删除数据库中的任务记录
        2. emit taskDeleted 信号
        DownloadedScrollArea 监听 taskDeleted 即可同步移除卡片
        (不再依赖卡片自己的 deleteLater + 字典清理)。
        """
        from qfluentwidgets import MessageBox
        from app.core.services import taskManager

        confirmDialog = MessageBox(
            "确认删除", "确定要删除这条下载记录吗？", self.window()
        )
        confirmDialog.yesButton.setText("确定")
        confirmDialog.cancelButton.setText("取消")

        if confirmDialog.exec():
            # 通过 TaskManager 统一删除 + 通知 UI
            # P0-A1 fix 2026-07-18:统一走 taskManager.removeTaskWithFallback()
            # 不再自己 try/except + 手动调 taskControl
            taskManager.removeTaskWithFallback(self.taskId)

            try:
                if hasattr(self, "pauseButton"):
                    self.pauseButton.clicked.disconnect()
                if hasattr(self, "cancelButton"):
                    self.cancelButton.clicked.disconnect()
                if hasattr(self, "deleteButton"):
                    self.deleteButton.clicked.disconnect()
            except Exception:
                pass

            self.deleteLater()

    def _onOpenFolderClicked(self):
        """打开文件夹并选中文件"""
        import os
        import platform
        import subprocess

        # 从数据库获取下载路径
        # P0-A1 fix 2026-07-18:走 taskManager.getDownloadPath() 高阶接口
        filePath = taskManager.getDownloadPath(self.taskId)
        if not filePath:
            logger.warning(f"[DownloadCard] 无法获取下载路径: {self.taskId}")
            from qfluentwidgets import MessageBox

            msgBox = MessageBox("提示", "无法获取下载文件路径", self.window())
            msgBox.yesButton.setText("确定")
            msgBox.exec()
            return

        # 规范化路径
        filePath = os.path.normpath(filePath)

        # Windows 路径格式化
        if platform.system() == "Windows":
            filePath = filePath.replace("/", "\\")

        # 检查文件是否存在
        if not os.path.exists(filePath):
            logger.warning(f"[DownloadCard] 文件不存在: {filePath}")
            from qfluentwidgets import MessageBox

            msgBox = MessageBox(
                "文件不存在",
                f"下载文件已被删除或移动到其他位置。\n\n是否删除这条下载记录？",
                self.window(),
            )
            msgBox.yesButton.setText("删除")
            msgBox.cancelButton.setText("保留")

            if msgBox.exec():
                # 用户选择删除记录
                # P0-A1 fix 2026-07-18:统一走 taskManager.removeTaskWithFallback()
                taskManager.removeTaskWithFallback(self.taskId)

                try:
                    if hasattr(self, "pauseButton"):
                        self.pauseButton.clicked.disconnect()
                    if hasattr(self, "cancelButton"):
                        self.cancelButton.clicked.disconnect()
                    if hasattr(self, "deleteButton"):
                        self.deleteButton.clicked.disconnect()
                except Exception:
                    pass

                self.deleteLater()
            return

        logger.info(f"[DownloadCard] 打开文件夹: {filePath}")

        # 根据系统选择打开方式
        if platform.system() == "Windows":
            subprocess.Popen(f'explorer /select,"{filePath}"', shell=True)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", filePath])
        else:
            folderPath = os.path.dirname(filePath)
            subprocess.Popen(["xdg-open", folderPath])
