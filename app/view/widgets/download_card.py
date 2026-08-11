# coding: utf-8
"""任务卡片组件。

统一呈现排队、下载、暂停、完成、失败和取消状态。卡片结构与任务管理
设计稿保持一致：类型图标、标题与状态徽标、来源摘要、进度、元信息和操作区。
"""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon,
    IconWidget,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    ProgressBar,
    ToolButton,
    isDarkTheme,
    qconfig,
)

from app.core.services import taskManager
from app.core.utils import logger


_PARAM_LABELS = {
    "keyword": "关键词",
    "keystr": "关键词",
    "title": "题目",
    "essay_title": "题目",
    "hsk_level": "HSK等级",
    "shkgrade": "HSK等级",
    "level": "等级",
    "nationality": "国籍",
    "nation": "国籍",
    "authornationality": "作者国籍",
    "tablename": "语料库",
    "ft": "语料类型",
    "txt": "文本",
    "tag": "标签",
}
_HIDDEN_PARAM_KEYS = {
    "page",
    "per_page",
    "pagesize",
    "token",
    "corp_org_id",
    "isDeptCheck",
    "orderstr",
    "showlenght",
}


def buildTaskCardInfo(task: dict) -> dict:
    """把任务数据库记录转换成卡片输入,避免三个列表重复拼装字段。"""
    info = dict(task.get("info") or {})
    info.update(
        {
            "taskId": task.get("id") or info.get("taskId", ""),
            "type": task.get("type") or info.get("type", "hskDownload"),
            "_status": task.get("status", "pending"),
            "_createdAt": task.get("createdAt"),
            "_startedAt": task.get("startedAt"),
            "_endedAt": task.get("endedAt"),
            "_downloadPath": task.get("downloadPath"),
            "_fileName": task.get("fileName"),
            "_fileSize": task.get("fileSize"),
            "_taskName": task.get("taskName"),
            "_error": task.get("error") or "",
        }
    )
    return info


class DownloadCard(CardWidget):
    """任务管理页的统一状态卡片。"""

    def __init__(self, info_dict: dict, parent=None):
        super().__init__(parent=parent)
        self.infoDict = dict(info_dict or {})
        self.taskType = self.infoDict.get("type", "hskDownload")
        self.taskId = self.infoDict.get("taskId", "")
        self.isPaused = False
        self._status = self.infoDict.get("_status", "pending")
        self._errorText = self.infoDict.get("_error", "") or ""
        self._alwaysShowActions = False
        self._styleState = "muted"
        self._connectedButtons: set[ToolButton] = set()

        self._initUi()
        self._applyInitialState()
        qconfig.themeChangedFinished.connect(self._refreshTheme)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _initUi(self) -> None:
        self.setObjectName("downloadCard")
        self.setMinimumWidth(460)
        self.setFixedHeight(120)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(14)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 16))
        self.setGraphicsEffect(shadow)

        mainLayout = QHBoxLayout(self)
        mainLayout.setContentsMargins(16, 14, 12, 14)
        mainLayout.setSpacing(14)

        self.iconWrap = QFrame(self)
        self.iconWrap.setObjectName("taskIconWrap")
        self.iconWrap.setFixedSize(42, 42)
        iconLayout = QHBoxLayout(self.iconWrap)
        iconLayout.setContentsMargins(0, 0, 0, 0)
        iconLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._typeIcon = (
            FluentIcon.DOWNLOAD
            if self.taskType == "hskDownload"
            else FluentIcon.GLOBE
        )
        self.iconWidget = IconWidget(self._typeIcon, self.iconWrap)
        self.iconWidget.setFixedSize(24, 24)
        iconLayout.addWidget(self.iconWidget)
        mainLayout.addWidget(self.iconWrap, 0, Qt.AlignmentFlag.AlignTop)

        content = QFrame(self)
        content.setObjectName("taskContent")
        contentLayout = QVBoxLayout(content)
        contentLayout.setContentsMargins(0, 0, 0, 0)
        contentLayout.setSpacing(6)

        titleRow = QHBoxLayout()
        titleRow.setSpacing(8)
        self.titleLabel = BodyLabel(self._buildTitle(), content)
        self.titleLabel.setObjectName("taskTitle")
        self.titleLabel.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.titleLabel.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.titleLabel.setToolTip(self.titleLabel.text())
        titleRow.addWidget(self.titleLabel, 0)

        self.statusLabel = QLabel("排队中", content)
        self.statusLabel.setObjectName("statusChip")
        self.statusLabel.setFixedHeight(24)
        titleRow.addWidget(self.statusLabel, 0)
        titleRow.addStretch(1)
        contentLayout.addLayout(titleRow)

        self.subtitleLabel = BodyLabel(self._buildSubtitle(), content)
        self.subtitleLabel.setObjectName("taskSubtitle")
        contentLayout.addWidget(self.subtitleLabel)

        self.errorFrame = QFrame(content)
        self.errorFrame.setObjectName("errorDetail")
        errorLayout = QVBoxLayout(self.errorFrame)
        errorLayout.setContentsMargins(10, 7, 10, 7)
        errorLayout.setSpacing(2)
        errorTitle = QLabel("错误详情", self.errorFrame)
        errorTitle.setObjectName("errorTitle")
        self.errorLabel = QLabel("", self.errorFrame)
        self.errorLabel.setObjectName("errorText")
        self.errorLabel.setWordWrap(True)
        self.errorLabel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        errorLayout.addWidget(errorTitle)
        errorLayout.addWidget(self.errorLabel)
        self.errorFrame.hide()
        contentLayout.addWidget(self.errorFrame)

        self.progressBar = ProgressBar(content)
        self.progressBar.setFixedHeight(6)
        self.progressBar.setValue(0)
        contentLayout.addWidget(self.progressBar)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        self.fileLabel = BodyLabel("等待调度", content)
        self.speedLabel = BodyLabel("", content)
        self.timeLabel = BodyLabel("", content)
        for label in (self.fileLabel, self.speedLabel, self.timeLabel):
            label.setObjectName("taskMeta")
            footer.addWidget(label)
        footer.addStretch(1)
        self.percentLabel = QLabel("0%", content)
        self.percentLabel.setObjectName("percentChip")
        self.percentLabel.setFixedHeight(23)
        footer.addWidget(self.percentLabel)
        contentLayout.addLayout(footer)
        mainLayout.addWidget(content, 1)

        self.actionWidget = QFrame(self)
        self.actionWidget.setObjectName("taskActions")
        actionLayout = QHBoxLayout(self.actionWidget)
        actionLayout.setContentsMargins(0, 0, 0, 0)
        actionLayout.setSpacing(4)
        self.viewButton = ToolButton(FluentIcon.INFO, self.actionWidget)
        self.pauseButton = ToolButton(FluentIcon.PAUSE, self.actionWidget)
        self.cancelButton = ToolButton(FluentIcon.CLOSE, self.actionWidget)
        for button in (self.viewButton, self.pauseButton, self.cancelButton):
            button.setFixedSize(32, 32)
            actionLayout.addWidget(button)
        mainLayout.addWidget(self.actionWidget, 0, Qt.AlignmentFlag.AlignTop)
        self.actionWidget.hide()

    def _applyInitialState(self) -> None:
        if self._status == "in_progress":
            self.setRunning()
        elif self._status == "completed":
            self.setCompleted()
        elif self._status == "failed":
            self.setFailed(self._errorText)
        elif self._status == "cancelled":
            self.setCancelled()
        else:
            self.setQueued()

    # ------------------------------------------------------------------
    # Content formatting
    # ------------------------------------------------------------------
    def _buildTitle(self) -> str:
        explicit = self.infoDict.get("_taskName") or self.infoDict.get("taskName")
        if explicit:
            return str(explicit)
        source = "HSK 语料下载" if self.taskType == "hskDownload" else "Global 语料下载"
        summary = self._payloadSummary()
        return f"{source} · {summary}" if summary else source

    def _payloadSummary(self) -> str:
        payload = self.infoDict.get("payload", {})
        if not isinstance(payload, dict):
            return ""
        parts = []
        for key, value in payload.items():
            if key in _HIDDEN_PARAM_KEYS or value in (None, "", [], {}):
                continue
            if isinstance(value, bool):
                continue
            if isinstance(value, (list, tuple)):
                value = "、".join(str(item) for item in value[:3])
            text = str(value).strip()
            if not text:
                continue
            label = _PARAM_LABELS.get(key, key)
            parts.append(f"{label}：{text}")
            if len(parts) == 2:
                break
        summary = " · ".join(parts)
        return summary[:46] + ("…" if len(summary) > 46 else "")

    def _buildSubtitle(self) -> str:
        url = str(self.infoDict.get("url") or "")
        host = urlparse(url).hostname or (
            "hsk.blcu.edu.cn"
            if self.taskType == "hskDownload"
            else "qqk.blcu.edu.cn"
        )
        shortId = self.taskId[:8] if self.taskId else "待分配"
        return f"{host} · 任务 ID {shortId}"

    @staticmethod
    def _formatTime(value) -> str:
        if not value:
            return ""
        try:
            return datetime.fromisoformat(str(value)).strftime("%H:%M")
        except (TypeError, ValueError):
            return str(value)[11:16] if len(str(value)) >= 16 else str(value)

    @staticmethod
    def _formatBytes(value) -> str:
        try:
            size = float(value or 0)
        except (TypeError, ValueError):
            return ""
        if size <= 0:
            return ""
        units = ("B", "KB", "MB", "GB")
        unit = units[0]
        for unit in units:
            if size < 1024 or unit == units[-1]:
                break
            size /= 1024
        return f"{size:.0f} {unit}" if size >= 10 else f"{size:.1f} {unit}"

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def setQueued(self) -> None:
        self._status = "pending"
        self.isPaused = False
        self._alwaysShowActions = False
        self.iconWidget.setIcon(FluentIcon.HISTORY)
        self.statusLabel.setText("排队中")
        self.fileLabel.setText("等待调度")
        self.speedLabel.setText("")
        self.timeLabel.setText("")
        self.pauseButton.setEnabled(False)
        self.errorFrame.hide()
        self.setFixedHeight(120)
        self.progressBar.setCustomBarColor("#d6a514", "#f0c244")
        self._configureRunningActions(canPause=False)
        self._applyStyle("warning")

    def setRunning(self) -> None:
        self._status = "in_progress"
        self.isPaused = False
        self._alwaysShowActions = False
        self.iconWidget.setIcon(self._typeIcon)
        self.statusLabel.setText("下载中")
        self.pauseButton.setEnabled(True)
        self.pauseButton.setIcon(FluentIcon.PAUSE)
        self.pauseButton.setToolTip("暂停任务")
        self.errorFrame.hide()
        self.setFixedHeight(120)
        self.progressBar.setCustomBarColor("#00b09c", "#1ec5ae")
        self._configureRunningActions(canPause=True)
        self._applyStyle("info")

    def setPaused(self) -> None:
        self.isPaused = True
        self.statusLabel.setText("已暂停")
        self.pauseButton.setIcon(FluentIcon.PLAY)
        self.pauseButton.setToolTip("继续任务")
        self.fileLabel.setText("任务已暂停")
        self._applyStyle("warning")

    def updateProgress(
        self,
        progress: int,
        fileCount: str | None = None,
        speed: str | None = None,
        remainingTime: str | None = None,
    ) -> None:
        if self.isPaused:
            return
        if self._status != "in_progress":
            self.setRunning()
        progress = max(0, min(100, int(progress or 0)))
        self.progressBar.setValue(progress)
        self.percentLabel.setText(f"{progress}%")
        if fileCount:
            self.fileLabel.setText(str(fileCount))
        if speed:
            self.speedLabel.setText(str(speed))
        if remainingTime:
            self.timeLabel.setText(str(remainingTime))

    def setCompleted(self) -> None:
        self._status = "completed"
        self.isPaused = False
        self._alwaysShowActions = False
        self.iconWidget.setIcon(FluentIcon.ACCEPT)
        self.statusLabel.setText("成功")
        self.progressBar.setValue(100)
        self.percentLabel.setText("100%")
        self.progressBar.setCustomBarColor("#107c10", "#35a935")
        endedAt = self._formatTime(self.infoDict.get("_endedAt"))
        fileSize = self._formatBytes(self.infoDict.get("_fileSize"))
        path = str(self.infoDict.get("_downloadPath") or "")
        self.fileLabel.setText(f"完成于 {endedAt}" if endedAt else "已完成")
        self.speedLabel.setText(fileSize)
        self.timeLabel.setText(self._shortPath(path))
        self.errorFrame.hide()
        self.setFixedHeight(120)
        self._configureCompletedActions()
        self._applyStyle("success")

    def setFailed(self, error: str | None = None) -> None:
        self._status = "failed"
        self.isPaused = False
        self._alwaysShowActions = True
        self.iconWidget.setIcon(FluentIcon.CLOSE)
        self._errorText = str(error or self._errorText or "任务执行失败,未返回详细原因")
        self.statusLabel.setText("失败")
        self.progressBar.setValue(100)
        self.percentLabel.setText("终端错误")
        self.progressBar.setCustomBarColor("#d13438", "#e57b7d")
        endedAt = self._formatTime(self.infoDict.get("_endedAt"))
        self.fileLabel.setText(f"失败于 {endedAt}" if endedAt else "执行失败")
        self.speedLabel.setText(f"任务 ID {self.taskId[:8]}" if self.taskId else "")
        self.timeLabel.setText("")
        self.errorLabel.setText(self._errorText)
        self.errorLabel.setToolTip(self._errorText)
        self.errorFrame.show()
        self.setFixedHeight(178)
        self._configureFailedActions()
        self.actionWidget.show()
        self._applyStyle("danger")

    def setCancelled(self) -> None:
        self._status = "cancelled"
        self.isPaused = False
        self._alwaysShowActions = True
        self.iconWidget.setIcon(FluentIcon.CANCEL)
        self.statusLabel.setText("已取消")
        endedAt = self._formatTime(self.infoDict.get("_endedAt"))
        self.fileLabel.setText(f"取消于 {endedAt}" if endedAt else "任务已取消")
        self.speedLabel.setText("")
        self.timeLabel.setText("")
        self.errorFrame.hide()
        self.setFixedHeight(120)
        self._configureCancelledActions()
        self.actionWidget.show()
        self._applyStyle("muted")

    @staticmethod
    def _shortPath(path: str) -> str:
        if not path:
            return ""
        parent = str(Path(path).parent)
        return "…" + parent[-30:] if len(parent) > 30 else parent

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _disconnect(self, button: ToolButton) -> None:
        if button not in self._connectedButtons:
            return
        try:
            button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._connectedButtons.discard(button)

    def _connectAction(self, button, icon, tooltip: str, callback) -> None:
        self._disconnect(button)
        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setEnabled(True)
        button.show()
        button.clicked.connect(callback)
        self._connectedButtons.add(button)

    def _configureRunningActions(self, *, canPause: bool) -> None:
        self._connectAction(
            self.viewButton, FluentIcon.INFO, "查看任务参数", self._onShowDetailsClicked
        )
        self._connectAction(
            self.pauseButton,
            FluentIcon.PAUSE if not self.isPaused else FluentIcon.PLAY,
            "暂停任务" if not self.isPaused else "继续任务",
            self._onPauseClicked,
        )
        self.pauseButton.setEnabled(canPause)
        self._connectAction(
            self.cancelButton, FluentIcon.CLOSE, "取消任务", self._onCancelClicked
        )

    def _configureCompletedActions(self) -> None:
        self._connectAction(
            self.viewButton, FluentIcon.FOLDER, "打开文件位置", self._onOpenFolderClicked
        )
        self._connectAction(
            self.pauseButton, FluentIcon.SYNC, "重新下载", self._onRedownloadClicked
        )
        self._connectAction(
            self.cancelButton, FluentIcon.DELETE, "删除记录", self._onDeleteClicked
        )

    def _configureFailedActions(self) -> None:
        self._connectAction(
            self.viewButton, FluentIcon.SYNC, "立即重试", self._onRedownloadClicked
        )
        copyIcon = getattr(FluentIcon, "COPY", FluentIcon.DOCUMENT)
        self._connectAction(
            self.pauseButton, copyIcon, "复制错误详情", self._onCopyErrorClicked
        )
        self._connectAction(
            self.cancelButton, FluentIcon.DELETE, "删除记录", self._onDeleteClicked
        )

    def _configureCancelledActions(self) -> None:
        self._connectAction(
            self.viewButton, FluentIcon.SYNC, "重新下载", self._onRedownloadClicked
        )
        self.pauseButton.hide()
        self._connectAction(
            self.cancelButton, FluentIcon.DELETE, "删除记录", self._onDeleteClicked
        )

    def _onShowDetailsClicked(self) -> None:
        payload = self.infoDict.get("payload", {})
        rows = [f"任务 ID：{self.taskId or '未分配'}"]
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in _HIDDEN_PARAM_KEYS or value in (None, "", [], {}):
                    continue
                rows.append(f"{_PARAM_LABELS.get(key, key)}：{value}")
        dialog = MessageBox("任务详情", "\n".join(rows), self.window())
        dialog.yesButton.setText("关闭")
        dialog.cancelButton.hide()
        dialog.exec()

    def _onCopyErrorClicked(self) -> None:
        QApplication.clipboard().setText(self._errorText)
        InfoBar.success(
            title="已复制",
            content="错误详情已复制到剪贴板",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            duration=2000,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self.window(),
        )

    def _onRedownloadClicked(self) -> None:
        infoDict = {
            key: value
            for key, value in self.infoDict.items()
            if key != "taskId" and not str(key).startswith("_")
        }
        try:
            newTaskId = taskManager.createTask(self.taskType, infoDict)
            logger.info(f"[DownloadCard] 重新创建任务: {newTaskId}")
            InfoBar.success(
                title="任务已创建",
                content="重新下载任务已加入队列",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=2600,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
        except Exception as exc:
            logger.exception(f"[DownloadCard] 重新创建任务失败: {exc}")
            InfoBar.error(
                title="创建失败",
                content=f"重新下载任务失败: {str(exc)[:100]}",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )

    def _onPauseClicked(self) -> None:
        if not self.isPaused:
            if taskManager.pauseTask(self.taskId):
                self.setPaused()
        else:
            if taskManager.resumeTask(self.taskId):
                self.setRunning()

    def _onCancelClicked(self) -> None:
        dialog = MessageBox("确认取消", "确定要取消这个下载任务吗？", self.window())
        dialog.yesButton.setText("取消任务")
        dialog.cancelButton.setText("返回")
        if dialog.exec():
            taskManager.stopTask(self.taskId)

    def _onDeleteClicked(self) -> None:
        dialog = MessageBox("确认删除", "确定要删除这条任务记录吗？", self.window())
        dialog.yesButton.setText("删除")
        dialog.cancelButton.setText("保留")
        if dialog.exec():
            taskManager.removeTaskWithFallback(self.taskId)

    def _onOpenFolderClicked(self) -> None:
        filePath = taskManager.getDownloadPath(self.taskId)
        if not filePath:
            self._showMissingFile("无法获取下载文件路径")
            return
        filePath = os.path.normpath(filePath)
        if not os.path.exists(filePath):
            self._showMissingFile("下载文件已被删除或移动")
            return
        logger.info(f"[DownloadCard] 打开文件位置: {filePath}")
        if platform.system() == "Windows":
            subprocess.Popen(["explorer", f"/select,{filePath}"])
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", filePath])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(filePath)])

    def _showMissingFile(self, message: str) -> None:
        logger.warning(f"[DownloadCard] {message}, taskId={self.taskId}")
        dialog = MessageBox("文件不可用", message, self.window())
        dialog.yesButton.setText("知道了")
        dialog.cancelButton.hide()
        dialog.exec()

    # ------------------------------------------------------------------
    # Visual state
    # ------------------------------------------------------------------
    def _applyStyle(self, state: str) -> None:
        self._styleState = state
        dark = isDarkTheme()
        card = "#2b2b2b" if dark else "#ffffff"
        foreground = "#f5f5f5" if dark else "#1f1f1f"
        muted = "#b3b3b3" if dark else "#616161"
        borderMap = {
            "success": "rgba(16, 124, 16, 0.45)",
            "danger": "rgba(209, 52, 56, 0.62)",
            "warning": "rgba(193, 156, 0, 0.38)",
            "info": "rgba(0, 176, 156, 0.28)",
            "muted": "#454545" if dark else "#d8d8d8",
        }
        chipMap = {
            "success": ("rgba(16,124,16,.12)", "#107c10"),
            "danger": ("rgba(209,52,56,.12)", "#d13438"),
            "warning": ("rgba(193,156,0,.14)", "#a67c00"),
            "info": ("rgba(0,120,212,.11)", "#0078d4"),
            "muted": ("rgba(97,97,97,.12)", muted),
        }
        iconMap = {
            "success": ("rgba(16,124,16,.10)", "#107c10"),
            "danger": ("rgba(209,52,56,.10)", "#d13438"),
            "warning": ("rgba(193,156,0,.12)", "#a67c00"),
            "info": ("rgba(0,176,156,.09)", "#00a894"),
            "muted": ("rgba(97,97,97,.10)", muted),
        }
        chipBackground, chipColor = chipMap[state]
        iconBackground, iconColor = iconMap[state]
        self.setStyleSheet(
            f"""
            CardWidget#downloadCard {{
                background: {card};
                border: 1px solid {borderMap[state]};
                border-radius: 9px;
            }}
            CardWidget#downloadCard:hover {{
                border: 1px solid rgba(0, 176, 156, 0.58);
            }}
            QFrame#taskContent, QFrame#taskActions {{
                background: transparent;
                border: none;
            }}
            QFrame#taskIconWrap {{
                background: {iconBackground};
                color: {iconColor};
                border: none;
                border-radius: 8px;
            }}
            BodyLabel#taskTitle {{
                color: {foreground};
                font-size: 14px;
                font-weight: 600;
            }}
            BodyLabel#taskSubtitle, BodyLabel#taskMeta {{
                color: {muted};
                font-size: 12px;
            }}
            QLabel#statusChip {{
                color: {chipColor};
                background: {chipBackground};
                border: none;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 12px;
            }}
            QLabel#percentChip {{
                color: {chipColor};
                background: {chipBackground};
                border: none;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 12px;
            }}
            QFrame#errorDetail {{
                background: rgba(209, 52, 56, 0.055);
                border: 1px solid rgba(209, 52, 56, 0.24);
                border-radius: 7px;
            }}
            QLabel#errorTitle {{
                color: #d13438;
                background: transparent;
                font-size: 11px;
                font-weight: 600;
            }}
            QLabel#errorText {{
                color: {foreground};
                background: transparent;
                font-size: 12px;
            }}
            """
        )

    def _refreshTheme(self, *_args) -> None:
        self._applyStyle(self._styleState)

    def enterEvent(self, event) -> None:
        self.actionWidget.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._alwaysShowActions:
            self.actionWidget.hide()
        super().leaveEvent(event)
