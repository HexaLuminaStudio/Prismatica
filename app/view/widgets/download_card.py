# coding: utf-8
"""
下载卡片组件
简约现代风格的横向下载任务卡片
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFrame

from qfluentwidgets import (
    CardWidget,
    BodyLabel,
    ProgressBar,
    IconWidget,
    PrimaryPushButton,
    ToolButton,
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

        self._init_ui()
        self._setup_style()

    def _init_ui(self):
        """初始化UI"""
        self.setFixedHeight(88)
        self.setMinimumWidth(400)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(16, 12, 12, 12)
        main_layout.setSpacing(16)

        # 左侧：类型图标
        self._init_icon_area(main_layout)

        # 中间：信息区域
        self._init_info_area(main_layout)

        # 右侧：操作按钮
        self._init_action_area(main_layout)

    def _init_icon_area(self, parent_layout):
        """初始化图标区域"""
        icon_path = (
            ":app/icons/Hsk.svg"
            if self.taskType == "hskDownload"
            else ":app/icons/Global.svg"
        )
        self.iconWidget = IconWidget(icon_path, self)
        self.iconWidget.setFixedSize(48, 48)
        parent_layout.addWidget(
            self.iconWidget,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

    def _init_info_area(self, parent_layout):
        """初始化信息区域"""
        info_widget = QFrame(self)
        info_widget.setObjectName("infoWidget")

        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(6)

        # 标题行
        title_layout = QHBoxLayout()
        title_layout.setSpacing(8)

        # 类型标签
        type_text = "HSK" if self.taskType == "hskDownload" else "Global"
        self.typeLabel = BodyLabel(type_text, self)
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

        title_layout.addWidget(self.typeLabel)
        title_layout.addWidget(self.paramsLabel, 1)
        info_layout.addLayout(title_layout)

        # 进度条
        self.progressBar = ProgressBar(self)
        self.progressBar.setFixedHeight(4)
        self.progressBar.setContentsMargins(0, 0, 0, 0)
        info_layout.addWidget(self.progressBar)

        # 状态行
        status_layout = QHBoxLayout()
        status_layout.setSpacing(16)

        # 文件数
        self.fileLabel = BodyLabel("等待中...", self)
        self.fileLabel.setStyleSheet("color: #888; font-size: 12px;")

        # 速度
        self.speedLabel = BodyLabel("", self)
        self.speedLabel.setStyleSheet("color: #888; font-size: 12px;")

        # 剩余时间
        self.timeLabel = BodyLabel("", self)
        self.timeLabel.setStyleSheet("color: #888; font-size: 12px;")

        status_layout.addWidget(self.fileLabel)
        status_layout.addWidget(self.speedLabel)
        status_layout.addWidget(self.timeLabel)
        status_layout.addStretch()

        info_layout.addLayout(status_layout)

        parent_layout.addWidget(info_widget, 1)

    def _init_action_area(self, parent_layout):
        """初始化操作按钮区域"""
        button_widget = QFrame(self)
        button_layout = QHBoxLayout(button_widget)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)

        # 暂停/继续按钮
        self.pauseButton = ToolButton(FluentIcon.PAUSE, self)
        self.pauseButton.setFixedSize(32, 32)
        self.pauseButton.clicked.connect(self._on_pause_clicked)

        # 取消按钮
        self.cancelButton = ToolButton(FluentIcon.CLOSE, self)
        self.cancelButton.setFixedSize(32, 32)
        self.cancelButton.clicked.connect(self._on_cancel_clicked)

        button_layout.addWidget(self.pauseButton)
        button_layout.addWidget(self.cancelButton)

        parent_layout.addWidget(
            button_widget,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

    def _setup_style(self):
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

    def update_progress(
        self,
        progress: int,
        file_count: str = None,
        speed: str = None,
        remaining_time: str = None,
    ):
        """更新进度信息"""
        self.progressBar.setValue(progress)

        if file_count is not None:
            self.fileLabel.setText(file_count)
        if speed is not None:
            self.speedLabel.setText(speed)
        if remaining_time is not None:
            self.timeLabel.setText(remaining_time)

    def set_completed(self):
        """设置为完成状态"""
        self.progressBar.setValue(100)
        self.fileLabel.setText("已完成")
        self.speedLabel.setText("")
        self.timeLabel.setText("")
        self.pauseButton.setEnabled(False)
        self.cancelButton.setEnabled(False)
        self._set_completed_style()

    def set_failed(self, error: str = None):
        """设置为失败状态"""
        self.progressBar.setCustomBarColor("#E74856", "#FF6B6B")
        if error:
            self.fileLabel.setText(f"失败: {error[:20]}")
        else:
            self.fileLabel.setText("下载失败")
        self.pauseButton.setEnabled(False)
        self._set_failed_style()

    def _set_completed_style(self):
        """设置完成样式"""
        self.typeLabel.setStyleSheet("font-weight: 600; color: #107C10;")

    def _set_failed_style(self):
        """设置失败样式"""
        self.typeLabel.setStyleSheet("font-weight: 600; color: #E74856;")

    def _on_pause_clicked(self):
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

    def _on_cancel_clicked(self):
        """取消按钮点击"""
        from app.core.services import taskManager

        # 停止任务
        taskManager.stopTask(self.taskId)

        # 断开所有信号连接
        try:
            self.pauseButton.clicked.disconnect()
            self.cancelButton.clicked.disconnect()
        except Exception:
            pass

        self.deleteLater()
