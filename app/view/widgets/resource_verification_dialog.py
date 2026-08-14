# coding: utf-8
"""设置页 HSK 作文资源校验与修复弹窗。"""

from __future__ import annotations

import threading
from typing import Dict, Iterable, Optional

from PySide6.QtCore import QDate, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    IconWidget,
    MessageBoxBase,
    StrongBodyLabel,
    SubtitleLabel,
    qconfig,
)

from app.core.services.startup_database_service import (
    DatabaseDownloadCancelled,
    DatabaseResource,
    DatabaseVerificationResult,
    DatabaseVerificationThread,
    StartupDatabaseService,
)
from app.core.services.cloud_api import CloudApiError
from app.core.utils import logger
from app.view.widgets.prismatica_theme import shellPalette


def _formatBytes(byteCount: int) -> str:
    value = float(max(0, byteCount))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class _ResourceStatusRow(QFrame):
    """展示单个数据库的名称、当日日期与当前校验状态。"""

    def __init__(self, resource: DatabaseResource, parent=None) -> None:
        super().__init__(parent)
        self.resource = resource
        self.setObjectName("resourceStatusRow")
        self.setMinimumHeight(78)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        self.iconContainer = QWidget(self)
        self.iconContainer.setFixedSize(36, 36)
        iconLayout = QHBoxLayout(self.iconContainer)
        iconLayout.setContentsMargins(8, 8, 8, 8)
        iconWidget = IconWidget(
            FluentIcon.DOCUMENT.icon(color=QColor("#00b09c")),
            self.iconContainer,
        )
        iconWidget.setFixedSize(20, 20)
        iconLayout.addWidget(iconWidget)
        layout.addWidget(self.iconContainer)

        textLayout = QVBoxLayout()
        textLayout.setSpacing(3)
        titleLabel = StrongBodyLabel(resource.displayName, self)
        dateLabel = CaptionLabel(QDate.currentDate().toString("yyyy年M月d日"), self)
        textLayout.addWidget(titleLabel)
        textLayout.addWidget(dateLabel)
        layout.addLayout(textLayout, 1)

        stateLayout = QVBoxLayout()
        stateLayout.setSpacing(4)
        stateLayout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.stateLabel = QLabel("待校验", self)
        self.stateLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stateLabel.setMinimumWidth(72)
        self.stateLabel.setFixedHeight(24)
        self.detailLabel = CaptionLabel("等待开始", self)
        self.detailLabel.setAlignment(Qt.AlignmentFlag.AlignRight)
        stateLayout.addWidget(self.stateLabel, 0, Qt.AlignmentFlag.AlignRight)
        stateLayout.addWidget(self.detailLabel, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(stateLayout)
        self.setState("pending", "待校验", "等待开始")
        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)

    def _applyTheme(self, *_args) -> None:
        palette = shellPalette()
        self.setStyleSheet(
            f"QFrame#resourceStatusRow {{ background: {palette.surfaceAlt.name()}; "
            f"border: 1px solid {palette.border.name()}; border-radius: 9px; }}"
        )
        self.iconContainer.setStyleSheet(
            f"background: {palette.accentSurface.name()}; border-radius: 8px;"
        )
        self.setState(self._state, self.stateLabel.text(), self.detailLabel.text())

    def setState(self, state: str, label: str, detail: str) -> None:
        self._state = state
        palette = shellPalette()
        palettes = {
            "pending": (
                palette.mutedText.name(),
                palette.surface.name(),
                palette.border.name(),
            ),
            "running": (
                palette.accentText.name(),
                palette.accentSurface.name(),
                palette.accentText.name(),
            ),
            "success": (
                palette.successText.name(),
                palette.successSurface.name(),
                palette.successText.name(),
            ),
            "error": (
                palette.dangerText.name(),
                palette.dangerSurface.name(),
                palette.dangerText.name(),
            ),
        }
        foreground, background, border = palettes.get(state, palettes["pending"])
        self.stateLabel.setText(label)
        self.stateLabel.setStyleSheet(
            f"color: {foreground}; background: {background}; "
            f"border: 1px solid {border}; border-radius: 12px; padding: 0 9px;"
        )
        self.detailLabel.setText(detail)
        self.detailLabel.setToolTip(detail)

    def showResult(self, result: DatabaseVerificationResult) -> None:
        if result.isValid:
            self.setState(
                "success",
                "完整",
                f"{result.rowCount:,} 条 · {_formatBytes(result.fileSize)}",
            )
            return
        self.setState("error", "需修复", result.message)


class _DatabaseDownloadWorker(QObject):
    progressChanged = Signal(int, int, str, int, int, int)
    statusChanged = Signal(str)
    completed = Signal()
    failed = Signal(str, str)
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
        except CloudApiError as exc:
            logger.warning(
                "[ResourceVerificationDialog] 资源授权失败: code={} message={}",
                exc.code,
                exc.message,
            )
            self.failed.emit(exc.code, exc.message)
        except Exception as exc:
            logger.warning("[ResourceVerificationDialog] 资源修复失败: {}", exc)
            self.failed.emit("", str(exc))


class ResourceVerificationDialog(MessageBoxBase):
    """使用 Fluent MessageBoxBase 的资源校验、结果与修复流程。"""

    resourcesReady = Signal()

    def __init__(
        self,
        service: Optional[StartupDatabaseService] = None,
        parent=None,
        autoRepair: bool = False,
    ) -> None:
        super().__init__(parent)
        self._service = service or StartupDatabaseService()
        self._autoRepair = bool(autoRepair)
        self._readyEmitted = False
        self._verificationThread: Optional[DatabaseVerificationThread] = None
        self._downloadThread: Optional[QThread] = None
        self._downloadWorker: Optional[_DatabaseDownloadWorker] = None
        self._cancelEvent = threading.Event()
        self._state = "pending"
        self._closeRequested = False
        self._invalidResources = []
        self._results = []
        self.hasVerified = False
        self.allResourcesValid = False
        self._rows: Dict[str, _ResourceStatusRow] = {}

        self.widget.setFixedWidth(620)
        self.yesButton.setText("开始校验")
        self.cancelButton.setText("关闭")
        self._buildUi()
        QTimer.singleShot(0, self._startVerification)

    def _buildUi(self) -> None:
        self.viewLayout.setContentsMargins(24, 18, 24, 8)
        self.viewLayout.setSpacing(12)

        titleLabel = SubtitleLabel(
            "正在准备 HSK 作文资源" if self._autoRepair else "HSK 作文资源校验",
            self,
        )
        self.viewLayout.addWidget(titleLabel)
        descriptionLabel = BodyLabel(
            (
                "首次使用会自动检查并下载所需数据，完成后即可直接检索。"
                if self._autoRepair
                else "检查作文数据表与正文库的 SQLite 完整性。发现异常时可在此直接重新下载修复。"
            ),
            self,
        )
        descriptionLabel.setWordWrap(True)
        self.viewLayout.addWidget(descriptionLabel)

        self.overviewLabel = StrongBodyLabel("正在准备校验…", self)
        self.overviewLabel.setWordWrap(True)
        self.viewLayout.addWidget(self.overviewLabel)

        for resource in self._service.resources:
            row = _ResourceStatusRow(resource, self)
            self._rows[resource.key] = row
            self.viewLayout.addWidget(row)

        self.progressBar = QProgressBar(self)
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(True)
        self.progressBar.setFixedHeight(22)
        self.progressBar.hide()
        self.viewLayout.addWidget(self.progressBar)

        self.progressDetailLabel = CaptionLabel("", self)
        self.progressDetailLabel.setWordWrap(True)
        self.progressDetailLabel.hide()
        self.viewLayout.addWidget(self.progressDetailLabel)

    def _setRowsRunning(self, text: str = "正在检查") -> None:
        for row in self._rows.values():
            row.setState("running", "校验中", text)

    def _startVerification(self) -> None:
        if self._verificationThread is not None or self._downloadThread is not None:
            return
        self._state = "verifying"
        self._closeRequested = False
        self.yesButton.setEnabled(False)
        self.yesButton.setText("校验中…")
        self.cancelButton.show()
        self.cancelButton.setEnabled(True)
        self.overviewLabel.setText("正在执行 SQLite 完整性检查与数据统计…")
        self.progressBar.hide()
        self.progressDetailLabel.hide()
        self._setRowsRunning()

        self._verificationThread = DatabaseVerificationThread(self._service, self)
        self._verificationThread.verificationFinished.connect(
            self._onVerificationFinished
        )
        self._verificationThread.verificationFailed.connect(
            self._onVerificationFailed
        )
        self._verificationThread.finished.connect(self._onVerificationThreadFinished)
        self._verificationThread.start()

    @Slot(object)
    def _onVerificationFinished(self, results) -> None:
        self._results = list(results)
        self.hasVerified = True
        for result in self._results:
            row = self._rows.get(result.resource.key)
            if row is not None:
                row.showResult(result)

        invalidResults = [result for result in self._results if not result.isValid]
        self._invalidResources = [result.resource for result in invalidResults]
        self.allResourcesValid = not invalidResults
        self.yesButton.setEnabled(True)
        if invalidResults:
            self._state = "needsRepair"
            self.overviewLabel.setText(
                (
                    f"发现 {len(invalidResults)} 个资源需要准备，正在自动下载…"
                    if self._autoRepair
                    else f"发现 {len(invalidResults)} 个异常资源，可重新下载并自动复检。"
                )
            )
            self.yesButton.setText("正在准备…" if self._autoRepair else "修复资源")
            self.cancelButton.setText("关闭")
            self.cancelButton.show()
            if self._autoRepair:
                self.yesButton.setEnabled(False)
                QTimer.singleShot(0, self._startRepair)
            return

        self._state = "completed"
        totalRows = sum(result.rowCount for result in self._results)
        totalSize = sum(result.fileSize for result in self._results)
        self.overviewLabel.setText(
            f"全部资源完整 · 共 {totalRows:,} 条数据 · {_formatBytes(totalSize)}"
        )
        self.yesButton.setText("完成")
        self.cancelButton.hide()
        self._emitResourcesReady()
        if self._autoRepair:
            QTimer.singleShot(700, self.accept)

    def _emitResourcesReady(self) -> None:
        """资源首次进入完整状态时通知调用页面。"""
        if self._readyEmitted:
            return
        self._readyEmitted = True
        self.resourcesReady.emit()

    @Slot(str)
    def _onVerificationFailed(self, message: str) -> None:
        self._state = "verificationFailed"
        self.hasVerified = False
        self.allResourcesValid = False
        self.overviewLabel.setText(message or "资源校验失败，请稍后重试。")
        self.yesButton.setEnabled(True)
        self.yesButton.setText("重新校验")
        self.cancelButton.setText("关闭")
        for row in self._rows.values():
            row.setState("error", "校验失败", "无法读取资源")

    @Slot()
    def _onVerificationThreadFinished(self) -> None:
        thread = self._verificationThread
        self._verificationThread = None
        if thread is not None:
            thread.deleteLater()
        if self._closeRequested:
            MessageBoxBase.reject(self)

    def _startRepair(self) -> None:
        if not self._invalidResources or self._downloadThread is not None:
            return
        self._state = "repairing"
        self._closeRequested = False
        self._cancelEvent = threading.Event()
        self.yesButton.setEnabled(False)
        self.yesButton.setText("修复中…")
        self.cancelButton.setText("关闭")
        self.cancelButton.show()
        self.overviewLabel.setText("正在重新下载异常资源…")
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setFormat("准备下载")
        self.progressBar.show()
        self.progressDetailLabel.setText("正在连接资源服务器…")
        self.progressDetailLabel.show()
        for resource in self._invalidResources:
            row = self._rows.get(resource.key)
            if row is not None:
                row.setState("running", "修复中", "等待下载")

        self._downloadThread = QThread(self)
        self._downloadWorker = _DatabaseDownloadWorker(
            self._service,
            self._invalidResources,
            self._cancelEvent,
        )
        self._downloadWorker.moveToThread(self._downloadThread)
        self._downloadThread.started.connect(self._downloadWorker.run)
        self._downloadWorker.progressChanged.connect(self._onDownloadProgress)
        self._downloadWorker.statusChanged.connect(self.overviewLabel.setText)
        self._downloadWorker.completed.connect(self._onDownloadCompleted)
        self._downloadWorker.failed.connect(self._onDownloadFailed)
        self._downloadWorker.cancelled.connect(self._onDownloadCancelled)
        self._downloadWorker.completed.connect(self._downloadWorker.deleteLater)
        self._downloadWorker.failed.connect(self._downloadWorker.deleteLater)
        self._downloadWorker.cancelled.connect(self._downloadWorker.deleteLater)
        self._downloadThread.finished.connect(self._onDownloadThreadFinished)
        self._downloadThread.start()

    @Slot(int, int, str, int, int, int)
    def _onDownloadProgress(
        self,
        resourceIndex: int,
        resourceCount: int,
        displayName: str,
        downloadedBytes: int,
        totalBytes: int,
        resourcePercent: int,
    ) -> None:
        normalizedPercent = max(0, resourcePercent)
        overallPercent = int(
            ((resourceIndex - 1) + normalizedPercent / 100)
            * 100
            / max(1, resourceCount)
        )
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(overallPercent)
        self.progressBar.setFormat(f"总进度 {overallPercent}%")
        if totalBytes > 0:
            sizeText = f"{_formatBytes(downloadedBytes)} / {_formatBytes(totalBytes)}"
        else:
            sizeText = f"已下载 {_formatBytes(downloadedBytes)}"
        self.progressDetailLabel.setText(
            f"文件 {resourceIndex}/{resourceCount} · {displayName} · {sizeText}"
        )
        for resource in self._invalidResources:
            if resource.displayName != displayName:
                continue
            row = self._rows.get(resource.key)
            if row is not None:
                row.setState("running", "下载中", sizeText)
            break

    @Slot()
    def _onDownloadCompleted(self) -> None:
        self._state = "repairCompleted"
        self.progressBar.setValue(100)
        self.progressBar.setFormat("下载完成")
        self.progressDetailLabel.setText("正在重新校验下载后的资源…")
        if self._downloadThread is not None:
            self._downloadThread.quit()

    @Slot(str, str)
    def _onDownloadFailed(self, _code: str, message: str) -> None:
        self._state = "repairFailed"
        self._lastRepairError = message or "资源下载失败，请稍后重试。"
        if self._downloadThread is not None:
            self._downloadThread.quit()

    @Slot()
    def _onDownloadCancelled(self) -> None:
        self._state = "repairCancelled"
        if self._downloadThread is not None:
            self._downloadThread.quit()

    @Slot()
    def _onDownloadThreadFinished(self) -> None:
        state = self._state
        thread = self._downloadThread
        self._downloadThread = None
        self._downloadWorker = None
        if thread is not None:
            thread.deleteLater()
        if self._closeRequested or state == "repairCancelled":
            MessageBoxBase.reject(self)
            return
        if state == "repairCompleted":
            QTimer.singleShot(0, self._startVerification)
            return

        self._state = "needsRepair"
        self.overviewLabel.setText(
            getattr(self, "_lastRepairError", "资源修复失败。")
        )
        self.progressBar.setValue(0)
        self.progressBar.setFormat("修复失败")
        self.yesButton.setEnabled(True)
        self.yesButton.setText("重试修复")
        self.cancelButton.setText("关闭")
        for resource in self._invalidResources:
            row = self._rows.get(resource.key)
            if row is not None:
                row.setState("error", "修复失败", "请检查网络后重试")

    def validate(self) -> bool:
        """拦截主按钮，将校验与修复留在同一个 MessageBoxBase 内。"""
        if self._state == "completed":
            return True
        if self._state in {"pending", "verificationFailed"}:
            self._startVerification()
            return False
        if self._state in {"needsRepair", "repairFailed"}:
            self._startRepair()
            return False
        return False

    def reject(self) -> None:
        """关闭弹窗只结束当前校验/下载，不退出软件。"""
        if self._verificationThread is not None:
            self._closeRequested = True
            self.cancelButton.setEnabled(False)
            self.overviewLabel.setText("正在结束校验，请稍候…")
            return
        if self._downloadThread is not None:
            self._closeRequested = True
            self.cancelButton.setEnabled(False)
            self.overviewLabel.setText("正在停止下载并清理临时文件…")
            self._cancelEvent.set()
            return
        MessageBoxBase.reject(self)
