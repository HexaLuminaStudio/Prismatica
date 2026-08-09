"""语料导入与基础清洗工作台。

页面只暴露高频且可理解的基础能力：文件导入、单文件移除和五项字符清洗。
正则、自定义替换、清洗预设与词性标注不再出现在该工作流中。
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pandas as pd
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    IconWidget,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    SwitchButton,
    TransparentPushButton,
    isDarkTheme,
    qconfig,
)

from app.core.utils import logger
from app.view.widgets.freq_analyzer.dialogs import (
    CleanPreviewDialog,
    SelectColumnDialog,
)
from app.view.widgets.freq_analyzer.freq_engine import (
    CleanRule,
    FrequencyAnalyzer,
    TextCleaner,
)
from app.view.widgets.freq_analyzer.ui_helpers import _makeSwitchButton, _showInfoBar

if TYPE_CHECKING:
    from app.view.widgets.freq_analyzer.corpus_store import CorpusStore

_SUPPORTED_EXTENSIONS = {".xlsx", ".txt", ".md", ".docx"}


class _CorpusDropZone(QFrame):
    """可点击、可键盘操作、可接收本地文件拖放的导入区域。"""

    browseRequested = Signal()
    filesDropped = Signal(list)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("corpusDropZone")
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("选择语料文件")
        self.setAccessibleDescription("支持拖入或选择 Excel、文本和 Docx 文件")
        self.setMinimumHeight(218)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 22)
        layout.setSpacing(10)
        layout.addStretch(1)

        self.iconHost = QFrame(self)
        self.iconHost.setObjectName("corpusDropIconHost")
        self.iconHost.setFixedSize(54, 54)
        iconLayout = QVBoxLayout(self.iconHost)
        iconLayout.setContentsMargins(14, 14, 14, 14)
        self.iconWidget = IconWidget(FluentIcon.UP, self.iconHost)
        self.iconWidget.setFixedSize(26, 26)
        iconLayout.addWidget(self.iconWidget)
        layout.addWidget(self.iconHost, 0, Qt.AlignmentFlag.AlignHCenter)

        self.titleLabel = StrongBodyLabel("将语料文件拖到这里", self)
        self.titleLabel.setObjectName("corpusDropTitle")
        self.titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.titleLabel)

        hint = CaptionLabel("或点击选择本地文件", self)
        hint.setObjectName("corpusMutedHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        formats = QHBoxLayout()
        formats.setSpacing(6)
        formats.addStretch(1)
        for value in (".xlsx", ".txt", ".md", ".docx"):
            chip = QLabel(value, self)
            chip.setObjectName("corpusFormatChip")
            formats.addWidget(chip)
        formats.addStretch(1)
        layout.addLayout(formats)
        layout.addStretch(1)

    def _acceptedPaths(self, event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        return [
            path
            for path in paths
            if os.path.splitext(path)[1].lower() in _SUPPORTED_EXTENSIONS
        ]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.isEnabled() and self._acceptedPaths(event):
            event.acceptProposedAction()
            self.setProperty("dragActive", True)
            self.style().unpolish(self)
            self.style().polish(self)
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        paths = self._acceptedPaths(event)
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        if paths:
            event.acceptProposedAction()
            self.filesDropped.emit(paths)
        else:
            event.ignore()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.browseRequested.emit()

    def keyPressEvent(self, event) -> None:
        if self.isEnabled() and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        ):
            self.browseRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class CorpusImportWidget(QWidget):
    """以最短路径完成语料导入和基础清洗。"""

    finishedRequested = Signal()

    def __init__(
        self,
        parent=None,
        corpusStore: CorpusStore | None = None,
        corpusManager=None,
        cleanCoordinator=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CorpusImportWidget")
        self._corpusStore: CorpusStore | None = corpusStore
        self._corpusManager = corpusManager
        self._cleanCoordinator = cleanCoordinator
        self._boundStore = None
        self._syncingUi = False
        self._cleanBusy = False
        self.rawTexts: dict[str, str] = {}
        self._excelLoader = None
        self._textLoader = None

        self._initUi()
        if corpusStore is not None:
            self._bindCorpusStore(corpusStore)
            self._onCorpusChanged()
        self.setCleanCoordinator(cleanCoordinator)

    # ------------------------------------------------------------------
    # Store / coordinator binding
    # ------------------------------------------------------------------
    def setCorpusStore(self, store) -> None:
        if self._corpusStore is store and self._boundStore is store:
            self._onCorpusChanged()
            return
        self._corpusStore = store
        self._bindCorpusStore(store)
        self._onCorpusChanged()

    def setCleanCoordinator(self, coordinator) -> None:
        if self._cleanCoordinator is coordinator and getattr(
            self, "_boundCoordinator", None
        ) is coordinator:
            return
        old = getattr(self, "_boundCoordinator", None)
        if old is not None:
            for signal, slot in (
                (old.cleanStarted, self._onCleanStarted),
                (old.cleanProgress, self._onCleanProgress),
                (old.cleanFinished, self._onCleanFinished),
                (old.cleanFailed, self._onCleanFailed),
                (old.cleanBusyChanged, self._onCleanBusyChanged),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._cleanCoordinator = coordinator
        self._boundCoordinator = coordinator
        if coordinator is not None:
            coordinator.cleanStarted.connect(self._onCleanStarted)
            coordinator.cleanProgress.connect(self._onCleanProgress)
            coordinator.cleanFinished.connect(self._onCleanFinished)
            coordinator.cleanFailed.connect(self._onCleanFailed)
            coordinator.cleanBusyChanged.connect(self._onCleanBusyChanged)

    def _bindCorpusStore(self, store: CorpusStore) -> None:
        if self._boundStore is store:
            return
        old = self._boundStore
        if old is not None:
            for signal, slot in (
                (old.textsChanged, self._onTextsChanged),
                (old.cleanRuleChanged, self._onCleanRuleChanged),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        store.textsChanged.connect(self._onTextsChanged)
        store.cleanRuleChanged.connect(self._onCleanRuleChanged)
        self._boundStore = store

    def _onCorpusChanged(self) -> None:
        self._onTextsChanged()
        self._onCleanRuleChanged()

    def _onTextsChanged(self) -> None:
        if self._corpusStore is not None:
            self.rawTexts = dict(self._corpusStore.rawTexts)
        self._updateFileCount()
        self._refreshCleanSummary()
        self._publishStats()

    def _onCleanRuleChanged(self) -> None:
        self._syncCleanUiFromStore()
        self._refreshCleanSummary()

    def _publishStats(self) -> None:
        if self._corpusManager is None or self._corpusStore is None:
            return
        active = self._corpusManager.activeCorpus()
        if active is None:
            return
        self._corpusManager.updateStats(
            active.id,
            self._corpusStore.fileCount(),
            self._corpusStore.totalChars(),
        )

    def _syncCleanUiFromStore(self) -> None:
        if self._syncingUi or self._corpusStore is None:
            return
        store = self._corpusStore
        rule = getattr(store, "cleanRule", CleanRule())
        enabled = bool(getattr(store, "cleanEnabled", False))
        self._syncingUi = True
        try:
            mappings = (
                (self.cleanEnableSwitch, enabled),
                (self.cleanEnglishSwitch, bool(rule.removeEnglish)),
                (self.cleanDigitSwitch, bool(rule.removeDigits)),
                (self.cleanPunctSwitch, bool(rule.removePunct)),
                (self.cleanSpecialSwitch, bool(rule.removeSpecialSymbols)),
                (self.cleanLowerSwitch, bool(rule.lowercase)),
            )
            for switch, checked in mappings:
                switch.blockSignals(True)
                switch.setChecked(checked)
                switch.blockSignals(False)
            self._onCleanEnableChanged(enabled)
        finally:
            self._syncingUi = False

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _initUi(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 0, 4, 4)
        outer.setSpacing(10)

        commandBar = QFrame(self)
        commandBar.setObjectName("corpusCommandBar")
        commandLayout = QHBoxLayout(commandBar)
        commandLayout.setContentsMargins(12, 8, 12, 8)
        commandLayout.setSpacing(8)
        self.importStepButton = PushButton("1  导入", commandBar)
        self.cleanStepButton = PushButton("2  清洗", commandBar)
        self.importStepButton.setProperty("active", True)
        for button in (self.importStepButton, self.cleanStepButton):
            button.setMinimumHeight(34)
            commandLayout.addWidget(button)
        commandLayout.addStretch(1)
        self.workflowStatusLabel = CaptionLabel("等待导入语料", commandBar)
        self.workflowStatusLabel.setObjectName("corpusWorkflowStatus")
        commandLayout.addWidget(self.workflowStatusLabel)
        outer.addWidget(commandBar)

        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        outer.addWidget(self.scrollArea, 1)

        scrollContent = QWidget(self.scrollArea)
        scrollContent.setObjectName("corpusImportCanvas")
        self.scrollArea.setWidget(scrollContent)
        contentLayout = QVBoxLayout(scrollContent)
        contentLayout.setContentsMargins(4, 4, 4, 12)
        contentLayout.setSpacing(14)

        if self._corpusManager is not None:
            try:
                from app.view.widgets.freq_analyzer.corpus_switcher_widget import (
                    CorpusSwitcherWidget,
                )

                self._switcher = CorpusSwitcherWidget(
                    manager=self._corpusManager,
                    parent=scrollContent,
                )
                contentLayout.addWidget(self._switcher)
            except Exception as exc:  # noqa: BLE001 - optional widget initialization
                logger.error(f"[CorpusImportWidget] 初始化语料库切换器失败: {exc}")
                self._switcher = None

        self.workspace = QWidget(scrollContent)
        self.workspace.setObjectName("corpusWorkspace")
        self.workspaceLayout = QGridLayout(self.workspace)
        self.workspaceLayout.setContentsMargins(0, 0, 0, 0)
        self.workspaceLayout.setHorizontalSpacing(14)
        self.workspaceLayout.setVerticalSpacing(14)
        self.fileCard = self._buildFileCard()
        self.cleanCard = self._buildCleanCard()
        self.workspaceLayout.addWidget(self.fileCard, 0, 0)
        self.workspaceLayout.addWidget(self.cleanCard, 0, 1)
        self.workspaceLayout.setColumnStretch(0, 2)
        self.workspaceLayout.setColumnStretch(1, 1)
        contentLayout.addWidget(self.workspace)
        contentLayout.addStretch(1)

        actionBar = QFrame(self)
        actionBar.setObjectName("corpusActionBar")
        actionLayout = QHBoxLayout(actionBar)
        actionLayout.setContentsMargins(14, 9, 14, 9)
        actionLayout.setSpacing(10)
        self.importStatusLabel = CaptionLabel("选择文件后将自动解析", actionBar)
        self.importStatusLabel.setObjectName("corpusImportStatus")
        self.importStatusLabel.setWordWrap(True)
        actionLayout.addWidget(self.importStatusLabel, 1)
        self.resetButton = TransparentPushButton("恢复默认", actionBar)
        self.resetButton.clicked.connect(self._resetCleanUi)
        actionLayout.addWidget(self.resetButton)
        self.previewBtn = PushButton("预览清洗效果", actionBar)
        self.previewBtn.setIcon(FluentIcon.VIEW)
        self.previewBtn.clicked.connect(self._previewCleaning)
        actionLayout.addWidget(self.previewBtn)
        self.finishButton = PrimaryPushButton("完成并返回", actionBar)
        self.finishButton.setIcon(FluentIcon.ACCEPT)
        self.finishButton.clicked.connect(self.finishedRequested.emit)
        actionLayout.addWidget(self.finishButton)
        outer.addWidget(actionBar)

        self.importStepButton.clicked.connect(
            lambda: self._activateStep("import")
        )
        self.cleanStepButton.clicked.connect(
            lambda: self._activateStep("clean")
        )
        qconfig.themeChangedFinished.connect(self._applyTheme)
        self._applyTheme()
        QTimer.singleShot(0, self._reflowWorkspace)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflowWorkspace()

    def _reflowWorkspace(self) -> None:
        if not hasattr(self, "workspaceLayout"):
            return
        wide = self.width() >= 1120
        self.workspaceLayout.removeWidget(self.fileCard)
        self.workspaceLayout.removeWidget(self.cleanCard)
        self.workspaceLayout.addWidget(self.fileCard, 0, 0)
        if wide:
            self.workspaceLayout.addWidget(self.cleanCard, 0, 1)
            self.workspaceLayout.setColumnStretch(0, 2)
            self.workspaceLayout.setColumnStretch(1, 1)
        else:
            self.workspaceLayout.addWidget(self.cleanCard, 1, 0)
            self.workspaceLayout.setColumnStretch(0, 1)
            self.workspaceLayout.setColumnStretch(1, 0)

    def _applyTheme(self) -> None:
        dark = isDarkTheme()
        canvas = "#202428" if dark else "#F5F7FA"
        surface = "#2B3035" if dark else "#FFFFFF"
        surfaceAlt = "#343B40" if dark else "#F3F7F7"
        border = "#465058" if dark else "#DCE4E8"
        text = "#F3F6F7" if dark else "#1E252B"
        muted = "#B8C2C8" if dark else "#596873"
        accent = "#56D6C5" if dark else "#007C70"
        success = "#72D572" if dark else "#107C10"
        warning = "#F4D35E" if dark else "#8A6100"
        error = "#FF99A0" if dark else "#C42B1C"
        self.dropZone.iconWidget.setIcon(
            FluentIcon.UP.icon(color=Qt.GlobalColor.white)
        )
        activeStepStyle = (
            f"QPushButton {{ color: {accent}; background: rgba(0, 176, 156, 0.12); "
            "border: 1px solid transparent; font-weight: 600; border-radius: 6px; }"
        )
        inactiveStepStyle = (
            f"QPushButton {{ color: {muted}; background: transparent; "
            f"border: 1px solid {border}; border-radius: 6px; }}"
        )
        active = getattr(self, "_activeStep", "import")
        self.importStepButton.setStyleSheet(
            activeStepStyle if active == "import" else inactiveStepStyle
        )
        self.cleanStepButton.setStyleSheet(
            activeStepStyle if active == "clean" else inactiveStepStyle
        )
        self.setStyleSheet(
            f"""
            QWidget#CorpusImportWidget, QWidget#corpusImportCanvas,
            QWidget#corpusWorkspace {{ background: {canvas}; }}
            QScrollArea {{ border: none; background: {canvas}; }}
            QFrame#corpusCommandBar, QFrame#corpusActionBar {{
                background: {surface}; border: 1px solid {border}; border-radius: 10px;
            }}
            QFrame#corpusFileCard, QFrame#corpusCleanCard {{
                background: {surface}; border: 1px solid {border}; border-radius: 12px;
            }}
            QLabel {{ color: {text}; }}
            QLabel#corpusMutedHint, QLabel#corpusImportStatus,
            QLabel#corpusFileCount, QLabel#corpusCleanHint {{ color: {muted}; }}
            QLabel#corpusWorkflowStatus {{
                color: {accent}; background: {surfaceAlt}; padding: 5px 9px;
                border-radius: 5px; font-weight: 600;
            }}
            QPushButton[active="true"] {{
                color: {accent}; background: rgba(0, 176, 156, 0.12);
                border-color: transparent; font-weight: 600;
            }}
            QFrame#corpusDropZone {{
                background: {surfaceAlt}; border: 1px dashed {border}; border-radius: 12px;
            }}
            QFrame#corpusDropZone:hover, QFrame#corpusDropZone:focus,
            QFrame#corpusDropZone[dragActive="true"] {{ border: 2px solid {accent}; }}
            QFrame#corpusDropIconHost {{
                background: {accent}; border: none; border-radius: 27px;
            }}
            QLabel#corpusDropTitle {{ font-size: 16px; font-weight: 700; }}
            QLabel#corpusFormatChip {{
                color: {muted}; background: {surface}; border: 1px solid {border};
                padding: 3px 7px; border-radius: 5px;
            }}
            QListWidget#corpusFileList {{
                background: {surface}; color: {text}; border: 1px solid {border};
                border-radius: 8px; padding: 4px; outline: none;
            }}
            QListWidget#corpusFileList::item {{ padding: 8px; border-radius: 5px; }}
            QListWidget#corpusFileList::item:selected {{
                background: rgba(0, 176, 156, 0.16); color: {text};
            }}
            QFrame#corpusCleanOption {{
                background: transparent; border: none; border-bottom: 1px solid {border};
                border-radius: 0;
            }}
            QLabel#corpusCleanSection {{ color: {accent}; font-weight: 700; }}
            QLabel#corpusCleanCount {{ color: {muted}; }}
            QLabel#corpusImportStatus[status="busy"] {{ color: {accent}; }}
            QLabel#corpusImportStatus[status="success"] {{ color: {success}; }}
            QLabel#corpusImportStatus[status="warning"] {{ color: {warning}; }}
            QLabel#corpusImportStatus[status="error"] {{ color: {error}; }}
            """
        )

    def _activateStep(self, step: str) -> None:
        self._activeStep = step
        target = self.fileCard if step == "import" else self.cleanCard
        self.scrollArea.ensureWidgetVisible(target, 0, 12)
        self._applyTheme()

    # ------------------------------------------------------------------
    # File import
    # ------------------------------------------------------------------
    def _buildFileCard(self) -> CardWidget:
        card = CardWidget(self.workspace)
        card.setObjectName("corpusFileCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = StrongBodyLabel("语料文件", card)
        header.addWidget(title)
        header.addStretch(1)
        self.fileCountLabel = CaptionLabel("未加载文件", card)
        self.fileCountLabel.setObjectName("corpusFileCount")
        header.addWidget(self.fileCountLabel)
        layout.addLayout(header)

        self.dropZone = _CorpusDropZone(card)
        self.dropZone.browseRequested.connect(self._browseCorpusFiles)
        self.dropZone.filesDropped.connect(self._handleCorpusFiles)
        layout.addWidget(self.dropZone)

        listHeader = QHBoxLayout()
        listTitle = StrongBodyLabel("已选择文件", card)
        listHeader.addWidget(listTitle)
        listHeader.addStretch(1)
        self.clearBtn = TransparentPushButton("清空", card)
        self.clearBtn.clicked.connect(self._clearAll)
        listHeader.addWidget(self.clearBtn)
        layout.addLayout(listHeader)

        self.fileList = QListWidget(card)
        self.fileList.setObjectName("corpusFileList")
        self.fileList.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.fileList.setMinimumHeight(180)
        self.fileList.setMaximumHeight(280)
        self.fileList.setAlternatingRowColors(False)
        self.fileList.setToolTip("选择一个文件后可从当前语料库移除")
        layout.addWidget(self.fileList)
        removeRow = QHBoxLayout()
        removeRow.addStretch(1)
        self.removeFileButton = TransparentPushButton("移除所选文件", card)
        self.removeFileButton.setIcon(FluentIcon.DELETE)
        self.removeFileButton.setEnabled(False)
        self.removeFileButton.clicked.connect(self._removeSelectedFile)
        self.fileList.itemSelectionChanged.connect(
            lambda: self.removeFileButton.setEnabled(
                self.fileList.currentItem() is not None
            )
        )
        removeRow.addWidget(self.removeFileButton)
        layout.addLayout(removeRow)
        return card

    def _updateFileCount(self) -> None:
        count = len(self.rawTexts)
        total = sum(len(text) for text in self.rawTexts.values())
        if count:
            self.fileCountLabel.setText(f"{count} 个文件 · {total:,} 字符")
            self.workflowStatusLabel.setText(f"{count} 个文件已就绪")
        else:
            self.fileCountLabel.setText("未加载文件")
            self.workflowStatusLabel.setText("等待导入语料")

        selected = self.fileList.currentItem()
        selectedName = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self.fileList.clear()
        selectedRow = -1
        for index, (name, text) in enumerate(sorted(self.rawTexts.items())):
            item = QListWidgetItem(f"{name}    ·    {len(text):,} 字符")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(name)
            self.fileList.addItem(item)
            if name == selectedName:
                selectedRow = index
        if selectedRow >= 0:
            self.fileList.setCurrentRow(selectedRow)
        self.removeFileButton.setEnabled(self.fileList.currentItem() is not None)
        self.clearBtn.setEnabled(count > 0)
        if hasattr(self, "previewBtn"):
            self.previewBtn.setEnabled(
                not self._cleanBusy
                and self.cleanEnableSwitch.isChecked()
                and count > 0
            )
        if not self._isImportBusy() and not self._cleanBusy:
            if count:
                self._setImportStatus(
                    f"{count} 个文件已解析，清洗设置自动保存", "success"
                )
            else:
                self._setImportStatus("选择文件后将自动解析", "")

    def _removeSelectedFile(self) -> None:
        item = self.fileList.currentItem()
        if item is None:
            return
        fileName = item.data(Qt.ItemDataRole.UserRole)
        if not fileName:
            return
        if self._corpusStore is not None:
            self._corpusStore.removeRawText(fileName)
        else:
            self.rawTexts.pop(fileName, None)
            self._updateFileCount()
        self._setImportStatus(f"已移除：{fileName}", "warning")

    def _browseCorpusFiles(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择语料文件",
            "",
            "支持的语料 (*.xlsx *.txt *.md *.docx);;"
            "Excel (*.xlsx);;文本 (*.txt *.md);;Word (*.docx)",
        )
        self._handleCorpusFiles(files)

    def _handleCorpusFiles(self, files: list[str]) -> None:
        if not files:
            return
        accepted = [
            path
            for path in files
            if os.path.splitext(path)[1].lower() in _SUPPORTED_EXTENSIONS
        ]
        rejected = len(files) - len(accepted)
        if rejected:
            _showInfoBar(
                "warning",
                "部分文件未导入",
                f"已忽略 {rejected} 个不支持的文件",
                self,
                duration=2500,
            )
        excelFiles = [
            path
            for path in accepted
            if os.path.splitext(path)[1].lower() == ".xlsx"
        ]
        textFiles = [path for path in accepted if path not in excelFiles]
        if excelFiles:
            self._prepareExcelFiles(excelFiles)
        if textFiles:
            self._startTextLoad(textFiles, label="语料")

    def _prepareExcelFiles(self, files: list[str]) -> None:
        fileToColumns: dict[str, list[str]] = {}
        filePreviews: dict[str, dict[str, list[str]]] = {}
        for filePath in files:
            try:
                frame = pd.read_excel(filePath, engine="openpyxl", dtype=str, nrows=5)
                columns = [str(column) for column in frame.columns]
                fileToColumns[os.path.basename(filePath)] = columns
                filePreviews[os.path.basename(filePath)] = {
                    column: [
                        value
                        for value in (
                            frame[column]
                            .astype(str)
                            .fillna("")
                            .replace("nan", "")
                            .tolist()
                        )
                        if value
                    ][:5]
                    for column in columns
                }
            except Exception as exc:  # noqa: BLE001 - parser errors are user-facing
                logger.error(
                    f"[CorpusImportWidget] 读取 {os.path.basename(filePath)} 失败: {exc}"
                )
                _showInfoBar(
                    "error",
                    "读取失败",
                    f"{os.path.basename(filePath)}: {exc}",
                    self,
                    duration=3000,
                )
                return
        if not fileToColumns:
            return
        commonColumns = set(next(iter(fileToColumns.values())))
        for columns in fileToColumns.values():
            commonColumns &= set(columns)
        if not commonColumns:
            _showInfoBar(
                "error", "列名不一致", "所选 Excel 没有共同列名", self, duration=3000
            )
            return
        allColumns = list(next(iter(fileToColumns.values())))
        dialog = SelectColumnDialog(
            allColumns,
            commonColumns,
            filePreviews,
            "",
            self.window(),
        )
        if dialog.exec():
            self._startExcelLoad(files, dialog.getSelectedColumn())

    def _startExcelLoad(self, files: list[str], column: str | None) -> None:
        from app.view.freq_analyzer_interface import ExcelLoadWorker

        loader = ExcelLoadWorker(files, column, self)
        self._excelLoader = loader
        loader.progress.connect(
            lambda name: self._setImportStatus(f"正在加载：{name}", "busy")
        )
        loader.failed.connect(self._onExcelLoadFailed)
        loader.finished.connect(self._onExcelLoadFinished)
        loader.start()
        self._setImportStatus(f"正在加载 {len(files)} 个 Excel 文件", "busy")
        self._updateImportBusy()

    def _onExcelLoadFailed(self, fileName: str, errMsg: str) -> None:
        logger.error(f"[ExcelLoadWorker] 加载失败 {fileName}: {errMsg}")
        _showInfoBar("error", "加载失败", f"{fileName}: {errMsg}", self, duration=3000)

    def _onExcelLoadFinished(self, result: dict[str, str]) -> None:
        if self._corpusStore is not None:
            self._corpusStore.addRawTexts(result)
        else:
            self.rawTexts.update(result)
        self._excelLoader = None
        self._setImportStatus(f"Excel 加载完成：{len(result)} 个文件", "success")
        if self._corpusStore is None:
            self._updateFileCount()
        self._updateImportBusy()
        self._scheduleCleaningForImportedTexts()

    def _startTextLoad(self, files: list[str], label: str = "语料") -> None:
        if self._textLoader is not None and self._textLoader.isRunning():
            _showInfoBar("warning", "加载中", "请等待当前文件加载完成", self)
            return
        from app.view.freq_analyzer_interface import TextLoadWorker

        loader = TextLoadWorker(files, parent=self)
        self._textLoader = loader
        loader.progress.connect(
            lambda index, name: self._setImportStatus(
                f"正在加载（{index}/{len(files)}）：{name}", "busy"
            )
        )
        loader.failed.connect(
            lambda name, error: _showInfoBar(
                "error", "加载失败", f"{name}: {error}", self, duration=3000
            )
        )

        def onFinished(result: dict[str, str]) -> None:
            successCount = 0
            try:
                if self._corpusStore is not None:
                    self._corpusStore.addRawTexts(result)
                else:
                    self.rawTexts.update(result)
                successCount = len(result)
            except Exception as exc:  # noqa: BLE001 - store adapters may vary
                logger.error(f"[CorpusImportWidget] 批量写入语料失败: {exc}")
            self._textLoader = None
            self._setImportStatus(
                f"{label}加载完成：{successCount}/{len(files)} 个文件", "success"
            )
            if self._corpusStore is None:
                self._updateFileCount()
            self._updateImportBusy()
            self._scheduleCleaningForImportedTexts()

        loader.finished.connect(onFinished)
        loader.start()
        self._setImportStatus(f"正在加载 {len(files)} 个{label}文件", "busy")
        self._updateImportBusy()

    def _updateImportBusy(self) -> None:
        busy = self._isImportBusy()
        self.dropZone.setEnabled(not busy)
        if hasattr(self, "finishButton"):
            self.finishButton.setEnabled(not busy and not self._cleanBusy)

    def _isImportBusy(self) -> bool:
        return bool(
            (self._excelLoader is not None and self._excelLoader.isRunning())
            or (self._textLoader is not None and self._textLoader.isRunning())
        )

    def _clearAll(self) -> None:
        if self._corpusStore is not None:
            self._corpusStore.clearAll()
        else:
            self.rawTexts = {}
            self._updateFileCount()
        self._setImportStatus("已清空当前语料", "warning")

    # ------------------------------------------------------------------
    # Basic cleaning
    # ------------------------------------------------------------------
    def _buildCleanCard(self) -> CardWidget:
        card = CardWidget(self.workspace)
        card.setObjectName("corpusCleanCard")
        card.setMinimumWidth(330)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = StrongBodyLabel("清洗选项", card)
        header.addWidget(title)
        header.addStretch(1)
        self.cleanSummaryLabel = CaptionLabel("未启用", card)
        self.cleanSummaryLabel.setObjectName("corpusCleanCount")
        header.addWidget(self.cleanSummaryLabel)
        layout.addLayout(header)

        self.cleanEnableSwitch = _makeSwitchButton("启用清洗", card)
        self.cleanEnableSwitch.checkedChanged.connect(self._onCleanEnableChanged)
        self._addCleanOption(
            layout,
            "基础清洗",
            "启用后，分析模块统一使用清洗后的文本。",
            self.cleanEnableSwitch,
            emphasized=True,
        )

        section = QLabel("预处理", card)
        section.setObjectName("corpusCleanSection")
        layout.addWidget(section)
        whitespaceRow = QFrame(card)
        whitespaceRow.setObjectName("corpusCleanOption")
        whitespaceLayout = QHBoxLayout(whitespaceRow)
        whitespaceLayout.setContentsMargins(0, 8, 0, 10)
        whitespaceText = QVBoxLayout()
        whitespaceText.setSpacing(2)
        whitespaceText.addWidget(BodyLabel("合并连续空白", whitespaceRow))
        whitespaceHint = CaptionLabel("自动整理多余空格与换行", whitespaceRow)
        whitespaceHint.setObjectName("corpusCleanHint")
        whitespaceText.addWidget(whitespaceHint)
        whitespaceLayout.addLayout(whitespaceText, 1)
        fixed = CaptionLabel("默认开启", whitespaceRow)
        fixed.setObjectName("corpusWorkflowStatus")
        whitespaceLayout.addWidget(fixed)
        layout.addWidget(whitespaceRow)

        self.cleanLowerSwitch = _makeSwitchButton("", card)
        self._addCleanOption(
            layout, "统一小写", "将英文统一转换为小写", self.cleanLowerSwitch
        )

        section = QLabel("过滤", card)
        section.setObjectName("corpusCleanSection")
        layout.addWidget(section)
        self.cleanEnglishSwitch = _makeSwitchButton("", card)
        self.cleanDigitSwitch = _makeSwitchButton("", card)
        self.cleanPunctSwitch = _makeSwitchButton("", card)
        self.cleanSpecialSwitch = _makeSwitchButton("", card)
        for titleText, hintText, switch in (
            ("移除英文", "删除 A-Z 英文字母", self.cleanEnglishSwitch),
            ("移除数字", "删除 0-9 数字", self.cleanDigitSwitch),
            ("移除标点", "删除中英文标点", self.cleanPunctSwitch),
            ("移除特殊符号", "删除表情、货币和数学符号", self.cleanSpecialSwitch),
        ):
            self._addCleanOption(layout, titleText, hintText, switch)
        layout.addStretch(1)

        self._cleanOptionSwitches = (
            self.cleanEnglishSwitch,
            self.cleanDigitSwitch,
            self.cleanPunctSwitch,
            self.cleanSpecialSwitch,
            self.cleanLowerSwitch,
        )
        for switch in self._cleanOptionSwitches:
            switch.checkedChanged.connect(self._refreshCleanSummary)
            switch.checkedChanged.connect(self._pushCleanToStore)
        self.cleanEnableSwitch.checkedChanged.connect(self._pushCleanToStore)
        self._onCleanEnableChanged(False)
        self._refreshCleanSummary()
        return card

    def _addCleanOption(
        self,
        layout: QVBoxLayout,
        title: str,
        hint: str,
        switch: SwitchButton,
        *,
        emphasized: bool = False,
    ) -> None:
        row = QFrame(self.cleanCard if hasattr(self, "cleanCard") else self.workspace)
        row.setObjectName("corpusCleanOption")
        rowLayout = QHBoxLayout(row)
        rowLayout.setContentsMargins(0, 9 if emphasized else 7, 0, 11)
        textLayout = QVBoxLayout()
        textLayout.setSpacing(2)
        titleLabel = StrongBodyLabel(title, row) if emphasized else BodyLabel(title, row)
        textLayout.addWidget(titleLabel)
        hintLabel = CaptionLabel(hint, row)
        hintLabel.setObjectName("corpusCleanHint")
        hintLabel.setWordWrap(True)
        textLayout.addWidget(hintLabel)
        rowLayout.addLayout(textLayout, 1)
        switch.setAccessibleName(title)
        switch.setAccessibleDescription(hint)
        switch.setParent(row)
        rowLayout.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(row)

    def _onCleanEnableChanged(self, checked: bool) -> None:
        if not hasattr(self, "_cleanOptionSwitches"):
            switches = tuple(
                switch
                for name in (
                    "cleanEnglishSwitch",
                    "cleanDigitSwitch",
                    "cleanPunctSwitch",
                    "cleanSpecialSwitch",
                    "cleanLowerSwitch",
                )
                if (switch := getattr(self, name, None)) is not None
            )
        else:
            switches = self._cleanOptionSwitches
        for switch in switches:
            switch.setEnabled(checked)
        if hasattr(self, "previewBtn"):
            self.previewBtn.setEnabled(
                not self._cleanBusy and checked and bool(self.rawTexts)
            )
        self._refreshCleanSummary()

    def _refreshCleanSummary(self) -> None:
        if not hasattr(self, "cleanSummaryLabel"):
            return
        if not self.cleanEnableSwitch.isChecked():
            self.cleanSummaryLabel.setText("未启用")
            return
        active = sum(switch.isChecked() for switch in self._cleanOptionSwitches)
        self.cleanSummaryLabel.setText(f"已启用 {active + 1} 项")

    def _collectCleanRule(self) -> CleanRule:
        baseRule = (
            self._corpusStore.cleanRule
            if self._corpusStore is not None
            else CleanRule()
        )
        return CleanRule(
            removeEnglish=self.cleanEnglishSwitch.isChecked(),
            removeDigits=self.cleanDigitSwitch.isChecked(),
            removePunct=self.cleanPunctSwitch.isChecked(),
            removeWhitespace=True,
            removeSpecialSymbols=self.cleanSpecialSwitch.isChecked(),
            lowercase=self.cleanLowerSwitch.isChecked(),
            customRemoveList=list(getattr(baseRule, "customRemoveList", []) or []),
            customRegexList=list(getattr(baseRule, "customRegexList", []) or []),
            replaceMap=dict(getattr(baseRule, "replaceMap", {}) or {}),
            posOnClean=bool(getattr(baseRule, "posOnClean", False)),
        )

    def _pushCleanToStore(self) -> None:
        if self._syncingUi or self._corpusStore is None:
            return
        rule = self._collectCleanRule()
        enabled = self.cleanEnableSwitch.isChecked()
        if self._cleanCoordinator is not None:
            scheduled = self._cleanCoordinator.scheduleClean(rule, enabled)
            if scheduled:
                self.finishButton.setEnabled(False)
                self._setImportStatus("清洗设置已更新，正在准备…", "busy")
        else:
            try:
                self._corpusStore.setCleanRule(rule)
                self._corpusStore.setCleanEnabled(enabled)
            except Exception as exc:  # noqa: BLE001 - compatibility store adapters
                logger.warning(f"[CorpusImportWidget] 同步清洗设置失败: {exc}")

    def _scheduleCleaningForImportedTexts(self) -> None:
        if self.cleanEnableSwitch.isChecked():
            self._pushCleanToStore()

    def _onCleanStarted(self) -> None:
        self._setImportStatus("正在后台清洗…", "busy")

    def _onCleanProgress(self, percent: int, message: str) -> None:
        self._setImportStatus(f"{message}（{percent}%）", "busy")

    def _onCleanFinished(self, elapsed: float, totalChars: int) -> None:
        if self._corpusStore is not None and not self._corpusStore.cleanEnabled:
            message = "已关闭清洗，分析模块将使用原始文本"
        elif totalChars > 0:
            message = f"清洗完成：{totalChars:,} 字符 · {elapsed:.1f} 秒"
        else:
            message = "清洗完成"
        self._setImportStatus(message, "success")

    def _onCleanFailed(self, error: str) -> None:
        self._setImportStatus(f"清洗失败：{error[:80]}", "error")
        logger.error(f"[CorpusImportWidget] 清洗失败: {error}")

    def _onCleanBusyChanged(self, busy: bool) -> None:
        self._cleanBusy = busy
        self.previewBtn.setEnabled(
            not busy and self.cleanEnableSwitch.isChecked() and bool(self.rawTexts)
        )
        self.finishButton.setEnabled(not busy and not self._isImportBusy())

    def _resetCleanUi(self) -> None:
        self.cleanEnableSwitch.setChecked(False)
        for switch in self._cleanOptionSwitches:
            switch.setChecked(False)
        self._onCleanEnableChanged(False)
        self._pushCleanToStore()
        self._setImportStatus("清洗选项已恢复默认", "success")

    def _previewCleaning(self) -> None:
        if not self.rawTexts:
            _showInfoBar("warning", "暂无语料", "请先导入语料文件", self)
            return
        if not self.cleanEnableSwitch.isChecked():
            _showInfoBar("info", "尚未启用清洗", "请先开启清洗", self)
            return
        firstName, firstText = next(iter(self.rawTexts.items()))
        sample = (firstText or "")[:500]
        rule = self._collectCleanRule()
        cleanedSample = (
            TextCleaner(rule).clean(sample)
            if TextCleaner is not None
            else FrequencyAnalyzer(cleanRule=rule).cleaner.clean(sample)
        )
        CleanPreviewDialog(firstName, sample, cleanedSample, self.window()).exec()

    def _setImportStatus(self, text: str, status: str = "") -> None:
        self.importStatusLabel.setText(text)
        self.importStatusLabel.setProperty("status", status)
        self.importStatusLabel.style().unpolish(self.importStatusLabel)
        self.importStatusLabel.style().polish(self.importStatusLabel)


__all__ = ["CorpusImportWidget"]
