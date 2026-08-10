# coding: utf-8
"""启动期 HSK 作文数据库下载进度弹窗。"""

from __future__ import annotations

import threading
from typing import Iterable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
)

from app.core.api.database_download import DatabaseDownloadCancelled
from app.core.services.startup_database_service import (
    DatabaseResource,
    StartupDatabaseService,
)
from app.core.utils import logger


def _formatBytes(byteCount: int) -> str:
    """把字节数格式化为紧凑的中文界面文本。"""
    value = float(max(0, byteCount))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class _StartupDatabaseWorker(QObject):
    progressChanged = Signal(int, int, str, int, int, int)
    statusChanged = Signal(str)
    completed = Signal()
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        service: StartupDatabaseService,
        resources: Iterable[DatabaseResource],
        cancelEvent: threading.Event,
    ) -> None:
        super().__init__()
        self._service = service
        self._resources = list(resources)
        self._cancelEvent = cancelEvent

    @Slot()
    def run(self) -> None:
        try:
            self._service.downloadResources(
                self._resources,
                onProgress=self.progressChanged.emit,
                onStatus=self.statusChanged.emit,
                isCancelled=self._cancelEvent.is_set,
            )
            self.completed.emit()
        except DatabaseDownloadCancelled:
            self.cancelled.emit()
        except Exception as exc:
            logger.warning("[StartupDatabaseDialog] 下载失败: {}", exc)
            self.failed.emit(str(exc))


class StartupDatabaseDialog(QDialog):
    """在主窗口构造前下载必需数据库的应用级模态弹窗。"""

    def __init__(
        self,
        service: StartupDatabaseService,
        resources: Iterable[DatabaseResource],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._resources = list(resources)
        self._thread: Optional[QThread] = None
        self._worker: Optional[_StartupDatabaseWorker] = None
        self._cancelEvent = threading.Event()
        self._workerState = "idle"
        self._exitRequested = False
        self._started = False

        self.setWindowTitle("准备 HSK 作文数据库")
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(520)
        self.setMaximumWidth(620)
        self.setAccessibleName("HSK 作文数据库下载进度")
        self._buildUi()
        QTimer.singleShot(0, self._startDownload)

    def _buildUi(self) -> None:
        rootLayout = QVBoxLayout(self)
        rootLayout.setContentsMargins(28, 24, 28, 22)
        rootLayout.setSpacing(12)

        titleLabel = SubtitleLabel("正在准备 HSK 作文数据", self)
        rootLayout.addWidget(titleLabel)

        descriptionLabel = BodyLabel(
            "检测到本地数据库缺失或不可用。下载完成并通过校验后，软件会自动继续启动。",
            self,
        )
        descriptionLabel.setWordWrap(True)
        rootLayout.addWidget(descriptionLabel)
        rootLayout.addSpacing(8)

        self.statusLabel = StrongBodyLabel("正在检查下载配置…", self)
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setAccessibleName("当前下载状态")
        rootLayout.addWidget(self.statusLabel)

        self.progressBar = QProgressBar(self)
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(True)
        self.progressBar.setFormat("准备中")
        self.progressBar.setFixedHeight(22)
        self.progressBar.setAccessibleName("数据库下载进度")
        rootLayout.addWidget(self.progressBar)

        detailRow = QHBoxLayout()
        self.resourceLabel = CaptionLabel(
            f"共需下载 {len(self._resources)} 个数据库文件", self
        )
        self.sizeLabel = CaptionLabel("", self)
        self.sizeLabel.setAlignment(Qt.AlignmentFlag.AlignRight)
        detailRow.addWidget(self.resourceLabel, 1)
        detailRow.addWidget(self.sizeLabel)
        rootLayout.addLayout(detailRow)

        self.errorLabel = QLabel("", self)
        self.errorLabel.setWordWrap(True)
        self.errorLabel.setStyleSheet("color: #c42b1c; font-size: 12px;")
        self.errorLabel.setAccessibleName("数据库下载错误")
        self.errorLabel.hide()
        rootLayout.addWidget(self.errorLabel)

        rootLayout.addSpacing(6)
        buttonRow = QHBoxLayout()
        buttonRow.addStretch(1)
        self.exitButton = PushButton("退出软件", self)
        self.exitButton.clicked.connect(self._requestExit)
        buttonRow.addWidget(self.exitButton)
        self.retryButton = PrimaryPushButton("重试下载", self)
        self.retryButton.clicked.connect(self._startDownload)
        self.retryButton.hide()
        buttonRow.addWidget(self.retryButton)
        rootLayout.addLayout(buttonRow)

    def _startDownload(self) -> None:
        if self._thread is not None:
            return
        resources = self._service.missingResources()
        if not resources:
            self.accept()
            return

        self._resources = resources
        self._cancelEvent = threading.Event()
        self._exitRequested = False
        self._workerState = "running"
        self.retryButton.hide()
        self.exitButton.setEnabled(True)
        self.errorLabel.hide()
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setFormat("正在连接…")
        self.statusLabel.setText("正在连接下载服务器…")
        self.sizeLabel.setText("")

        self._thread = QThread(self)
        self._worker = _StartupDatabaseWorker(
            self._service,
            resources,
            self._cancelEvent,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progressChanged.connect(self._onProgressChanged)
        self._worker.statusChanged.connect(self.statusLabel.setText)
        self._worker.completed.connect(self._onCompleted)
        self._worker.failed.connect(self._onFailed)
        self._worker.cancelled.connect(self._onCancelled)
        self._worker.completed.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._worker.cancelled.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._onThreadFinished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        self._started = True

    @Slot(int, int, str, int, int, int)
    def _onProgressChanged(
        self,
        resourceIndex: int,
        resourceCount: int,
        displayName: str,
        downloadedBytes: int,
        totalBytes: int,
        resourcePercent: int,
    ) -> None:
        self.resourceLabel.setText(
            f"文件 {resourceIndex}/{resourceCount} · {displayName}"
        )
        if resourcePercent < 0:
            self.progressBar.setRange(0, 0)
            self.progressBar.setFormat("")
            self.sizeLabel.setText(f"已下载 {_formatBytes(downloadedBytes)}")
            return

        overallPercent = int(
            ((resourceIndex - 1) + resourcePercent / 100) * 100 / resourceCount
        )
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(overallPercent)
        self.progressBar.setFormat(f"总进度 {overallPercent}%")
        self.sizeLabel.setText(
            f"{_formatBytes(downloadedBytes)} / {_formatBytes(totalBytes)}"
        )

    @Slot()
    def _onCompleted(self) -> None:
        self._workerState = "completed"
        self.statusLabel.setText("数据库已下载并校验完成")
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(100)
        self.progressBar.setFormat("完成")
        if self._thread is not None:
            self._thread.quit()

    @Slot(str)
    def _onFailed(self, message: str) -> None:
        self._workerState = "failed"
        self._lastError = message or "数据库下载失败，请稍后重试。"
        if self._thread is not None:
            self._thread.quit()

    @Slot()
    def _onCancelled(self) -> None:
        self._workerState = "cancelled"
        if self._thread is not None:
            self._thread.quit()

    @Slot()
    def _onThreadFinished(self) -> None:
        state = self._workerState
        self._thread = None
        self._worker = None
        if state == "completed":
            QTimer.singleShot(180, self.accept)
            return
        if self._exitRequested or state == "cancelled":
            QDialog.reject(self)
            return

        self.statusLabel.setText("数据库下载未完成")
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setFormat("失败")
        self.errorLabel.setText(getattr(self, "_lastError", "数据库下载失败。"))
        self.errorLabel.show()
        self.retryButton.show()
        self.exitButton.setEnabled(True)

    @Slot()
    def _requestExit(self) -> None:
        self._exitRequested = True
        if self._thread is None:
            QDialog.reject(self)
            return
        self.statusLabel.setText("正在停止下载并清理临时文件…")
        self.exitButton.setEnabled(False)
        self.retryButton.setEnabled(False)
        self._cancelEvent.set()

    def reject(self) -> None:
        self._requestExit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None:
            self._requestExit()
            event.ignore()
            return
        event.accept()
