# coding: utf-8
"""从 freq_analyzer_interface.py 拆分而来

保留全部实现，仅补充必要的 imports。
"""

import json
import logging
import os
import traceback
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

import pandas as pd
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon,
    LineEdit,
    MessageBoxBase,
    PlainTextEdit,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TransparentPushButton,
)

from app.view.widgets.freq_analyzer.dialogs import (
    CleanPreviewDialog,
    PosPreviewDialog,
    SelectColumnDialog,
)
from app.view.widgets.freq_analyzer.freq_engine import (
    CleanRule,
    FrequencyAnalyzer,
    TextCleaner,
    availablePosBackend,
    loadDocxFile,
    loadTextFile,
    posTag,
    posTagCategories,
    posTagsFilter,
)
from app.view.widgets.freq_analyzer.ui_helpers import _makeSwitchButton, _showInfoBar

# PRESETS_DIR / ExcelLoadWorker 在主文件 freq_analyzer_interface.py 中定义，
# 为避免循环 import，采用延迟 import（在 _loadExcel / _scanPresetFiles 中导入）。


class CorpusImportWidget(QWidget):
    """语料导入与清洗设置面板（顶层 SegmentedWidget 第一项）

    职责：
        - 加载 Excel / 文本文件 → 写入 CorpusStore
        - 配置清洗规则 → 写入 CorpusStore
        - 显示当前语料与清洗状态摘要
        - 多语料库切换(顶部 CorpusSwitcherWidget)
    接收：
        corpusStore:    共享语料状态对象（必须）
        corpusManager:  语料库管理器(可选;提供时显示语料库切换卡片)
    """

    def __init__(
        self,
        parent=None,
        corpusStore: Optional["CorpusStore"] = None,
        corpusManager=None,
        cleanCoordinator=None,
    ):
        super().__init__(parent)
        self.setObjectName("CorpusImportWidget")
        self._corpusStore: Optional[CorpusStore] = corpusStore
        self._corpusManager = corpusManager  # 可选:用于多语料库切换 UI
        self._cleanCoordinator = cleanCoordinator  # 可选:异步清洗协调器
        # 本地缓存：rawTexts 与清洗后的文本都从 store 拉取
        self.rawTexts: Dict[str, str] = {}
        self._excelLoader = None  # ExcelLoadWorker 引用，防止被 GC

        self._initUi()

        if self._corpusStore is not None:
            self._bindCorpusStore(self._corpusStore)

        # 监听 store 文本变化 → 同步统计给 manager(用于语料库列表的统计显示)
        if self._corpusStore is not None and self._corpusManager is not None:
            try:
                self._corpusStore.textsChanged.connect(self._publishStats)
            except Exception:
                pass

        # 监听清洗进度(显示「清洗中...」状态)
        if self._cleanCoordinator is not None:
            try:
                self._cleanCoordinator.cleanStarted.connect(self._onCleanStarted)
                self._cleanCoordinator.cleanProgress.connect(self._onCleanProgress)
                self._cleanCoordinator.cleanFinished.connect(self._onCleanFinished)
                self._cleanCoordinator.cleanFailed.connect(self._onCleanFailed)
                self._cleanCoordinator.cleanBusyChanged.connect(
                    self._onCleanBusyChanged
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 语料状态绑定
    # ------------------------------------------------------------------
    def setCorpusStore(self, store) -> None:
        if self._corpusStore is store:
            return
        self._corpusStore = store
        self._bindCorpusStore(store)
        self._onCorpusChanged()

    def setCleanCoordinator(self, coordinator) -> None:
        """切换 CleanCoordinator(语料库切换时由 FreqAnalyzerInterface 调用)"""
        # 解绑旧 coordinator
        if self._cleanCoordinator is not None:
            try:
                self._cleanCoordinator.cleanStarted.disconnect(self._onCleanStarted)
                self._cleanCoordinator.cleanProgress.disconnect(self._onCleanProgress)
                self._cleanCoordinator.cleanFinished.disconnect(self._onCleanFinished)
                self._cleanCoordinator.cleanFailed.disconnect(self._onCleanFailed)
                self._cleanCoordinator.cleanBusyChanged.disconnect(
                    self._onCleanBusyChanged
                )
            except Exception:
                pass
        self._cleanCoordinator = coordinator
        # 绑定新 coordinator
        if coordinator is not None:
            try:
                coordinator.cleanStarted.connect(self._onCleanStarted)
                coordinator.cleanProgress.connect(self._onCleanProgress)
                coordinator.cleanFinished.connect(self._onCleanFinished)
                coordinator.cleanFailed.connect(self._onCleanFailed)
                coordinator.cleanBusyChanged.connect(self._onCleanBusyChanged)
            except Exception as e:
                logger.error(f"[CorpusImportWidget] 绑定 coordinator 失败: {e}")

    def _bindCorpusStore(self, store: "CorpusStore") -> None:
        store.textsChanged.connect(self._onCorpusChanged)
        store.cleanRuleChanged.connect(self._onCorpusChanged)

    def _onCorpusChanged(self) -> None:
        if self._corpusStore is not None:
            self.rawTexts = dict(self._corpusStore.rawTexts)
        self._updateFileCount()
        if hasattr(self, "_refreshCleanSummary"):
            self._refreshCleanSummary()
        # 切换后重新订阅新的 store 的 textsChanged(因为 store 实例变了)
        if self._corpusStore is not None and self._corpusManager is not None:
            try:
                self._corpusStore.textsChanged.connect(self._publishStats)
            except Exception:
                pass
        self._publishStats()

    def _publishStats(self) -> None:
        """将当前 store 的统计信息推送给 CorpusManager,用于 UI 列表显示"""
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

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _initUi(self):
        outerLayout = QVBoxLayout(self)
        outerLayout.setContentsMargins(20, 20, 20, 20)
        outerLayout.setSpacing(12)

        # 标题（L0：SubtitleLabel）
        title = SubtitleLabel("语料导入与清洗", self)
        outerLayout.addWidget(title)

        # 顶部说明（L3：提示文案 12px）
        hint = CaptionLabel(
            "导入一次语料 + 配置一次清洗，「词频分析」与「语境分析」即可共同使用。",
            self,
        )
        hint.setStyleSheet("color: #888; font-size: 12px;")
        hint.setWordWrap(True)
        outerLayout.addWidget(hint)

        # 滚动区
        scrollArea = ScrollArea(self)
        scrollArea.setWidgetResizable(True)
        scrollArea.setStyleSheet("border: none; background: transparent;")
        outerLayout.addWidget(scrollArea, 1)

        scrollContent = QWidget()
        scrollArea.setWidget(scrollContent)
        scrollLayout = QVBoxLayout(scrollContent)
        scrollLayout.setContentsMargins(0, 0, 0, 0)
        scrollLayout.setSpacing(12)

        scrollLayout.addWidget(self._buildFileCard())
        scrollLayout.addWidget(self._buildCleanCard(scrollContent))
        scrollLayout.addWidget(self._buildPosCard(scrollContent))
        scrollLayout.addStretch(1)

        # 语料库切换器:若有 manager 则放在最顶部(在 title 之下)
        if self._corpusManager is not None:
            try:
                from app.view.widgets.freq_analyzer.corpus_switcher_widget import (
                    CorpusSwitcherWidget,
                )

                self._switcher = CorpusSwitcherWidget(
                    manager=self._corpusManager,
                    parent=self,
                )
                # 插入到 scrollLayout 第一个位置
                scrollLayout.insertWidget(0, self._switcher)
            except Exception as e:
                logger.error(f"[CorpusImportWidget] 初始化切换器失败: {e}")
                self._switcher = None

    def _buildFileCard(self) -> CardWidget:
        """语料加载卡片"""
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        title = StrongBodyLabel("1. 加载语料", card)
        layout.addWidget(title)

        # 加载按钮
        loadLayout = QHBoxLayout()
        self.excelBtn = PushButton("加载 Excel", card)
        self.excelBtn.setIcon(FluentIcon.FOLDER)
        self.excelBtn.clicked.connect(self._loadExcel)

        self.textBtn = PushButton("加载文本文件", card)
        self.textBtn.setIcon(FluentIcon.DOCUMENT)
        self.textBtn.clicked.connect(self._loadText)

        self.docxBtn = PushButton("加载 Docx", card)
        self.docxBtn.setIcon(FluentIcon.DOCUMENT)
        self.docxBtn.clicked.connect(self._loadDocx)

        self.clearBtn = TransparentPushButton("清空", card)
        self.clearBtn.clicked.connect(self._clearAll)

        loadLayout.addWidget(self.excelBtn)
        loadLayout.addWidget(self.textBtn)
        loadLayout.addWidget(self.docxBtn)
        loadLayout.addStretch(1)
        loadLayout.addWidget(self.clearBtn)
        layout.addLayout(loadLayout)

        # Excel 列选择
        columnRow = QHBoxLayout()
        columnLabel = BodyLabel("Excel 列名:", card)
        columnLabel.setStyleSheet("min-width: 80px;")
        self.columnEdit = LineEdit(card)
        self.columnEdit.setPlaceholderText("（留空则使用全部文本列）")
        self.columnEdit.setFixedWidth(180)
        self.columnEdit.setReadOnly(True)
        self.pickColumnBtn = PushButton("选择列…", card)
        self.pickColumnBtn.setIcon(FluentIcon.MENU)
        self.pickColumnBtn.clicked.connect(self._pickExcelColumn)
        columnRow.addWidget(columnLabel)
        columnRow.addWidget(self.columnEdit)
        columnRow.addWidget(self.pickColumnBtn)
        columnRow.addStretch(1)
        self.fileCountLabel = CaptionLabel("未加载文件", card)
        self.fileCountLabel.setStyleSheet("color: #666; font-size: 11px;")
        columnRow.addWidget(self.fileCountLabel)
        layout.addLayout(columnRow)

        # 状态（L3：用于显示加载进度）
        self.importStatusLabel = CaptionLabel("", card)
        self.importStatusLabel.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.importStatusLabel)

        return card

    # ------------------------------------------------------------------
    # 加载 / 清空
    # ------------------------------------------------------------------
    def _updateFileCount(self) -> None:
        n = len(self.rawTexts)
        total = sum(len(t) for t in self.rawTexts.values())
        if n == 0:
            self.fileCountLabel.setText("未加载文件")
        else:
            self.fileCountLabel.setText(f"已加载 {n} 个文件，{total:,} 字符")

    def _loadExcel(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 Excel 文件",
            "",
            "Excel Files (*.xlsx *.xls *.XLSX *.XLS *.xIsx *.Xlsx *.Xls);;All Files (*)",
        )
        if not files:
            return

        fileToColumns: Dict[str, List[str]] = {}
        filePreviews: Dict[str, Dict[str, List[str]]] = {}
        for f in files:
            try:
                df = pd.read_excel(f, engine="openpyxl", dtype=str, nrows=5)
                cols = [str(c) for c in df.columns]
                fileToColumns[os.path.basename(f)] = cols
                preview: Dict[str, List[str]] = {}
                for c in cols:
                    vals = df[c].astype(str).fillna("").replace("nan", "").tolist()
                    vals = [v for v in vals if v]
                    preview[c] = vals[:5]
                filePreviews[os.path.basename(f)] = preview
            except Exception as e:
                logger.error(f"[_loadExcel] 读取文件 {os.path.basename(f)} 失败: {e}")
                _showInfoBar(
                    "error",
                    "读取失败",
                    f"{os.path.basename(f)}: {e}",
                    self,
                    duration=3000,
                )
                return

        if not fileToColumns:
            return

        commonCols = set(next(iter(fileToColumns.values())))
        for cols in fileToColumns.values():
            commonCols &= set(cols)

        if not commonCols:
            _showInfoBar(
                "error", "列名不一致", "所选文件没有共同的列名", self, duration=3000
            )
            return

        allCols = list(next(iter(fileToColumns.values())))
        selectedSoFar = self.columnEdit.text().strip()
        dialog = SelectColumnDialog(
            allCols, commonCols, filePreviews, selectedSoFar, self.window()
        )
        if not dialog.exec():
            return
        column = dialog.getSelectedColumn()

        self.columnEdit.setText(column or "")
        self._startExcelLoad(files, column)

    def _startExcelLoad(self, files: List[str], column: Optional[str]) -> None:
        logger.info(
            f"[_startExcelLoad] 开始后台加载 {len(files)} 个文件，列={column!r}"
        )
        self.excelBtn.setEnabled(False)
        self.textBtn.setEnabled(False)
        self.importStatusLabel.setText("正在加载文件...")

        # 延迟 import：避免与主文件 freq_analyzer_interface 循环依赖
        from app.view.freq_analyzer_interface import ExcelLoadWorker

        loader = ExcelLoadWorker(files, column, self)
        self._excelLoader = loader

        loader.progress.connect(
            lambda name: self.importStatusLabel.setText(f"正在加载：{name}")
        )
        loader.failed.connect(self._onExcelLoadFailed)
        loader.finished.connect(self._onExcelLoadFinished)
        loader.start()

    def _onExcelLoadFailed(self, fileName: str, errMsg: str) -> None:
        logger.error(f"[ExcelLoadWorker] 加载失败 {fileName}: {errMsg}")
        _showInfoBar("error", "加载失败", f"{fileName}: {errMsg}", self, duration=3000)

    def _onExcelLoadFinished(self, result: Dict[str, str]) -> None:
        logger.info(f"[_onExcelLoadFinished] 加载完成，共 {len(result)} 个文件")
        if self._corpusStore is not None:
            for name, text in result.items():
                self._corpusStore.addRawText(name, text)
        else:
            self.rawTexts.update(result)
            self._updateFileCount()
        self.excelBtn.setEnabled(True)
        self.textBtn.setEnabled(True)
        self.importStatusLabel.setText("文件加载完成")

    def _pickExcelColumn(self) -> None:
        if not self.rawTexts:
            _showInfoBar("warning", "提示", "请先加载 Excel 文件，再选择列", self)
            return
        self._loadExcel()

    def _loadText(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择文本文件", "", "Text Files (*.txt *.md);;All Files (*)"
        )
        if not files:
            return
        for f in files:
            try:
                text = loadTextFile(f)
                if self._corpusStore is not None:
                    self._corpusStore.addRawText(os.path.basename(f), text)
                else:
                    self.rawTexts[os.path.basename(f)] = text
            except Exception as e:
                logger.error(f"[_loadText] 读取文件 {os.path.basename(f)} 失败: {e}")
                _showInfoBar(
                    "error",
                    "加载失败",
                    f"{os.path.basename(f)}: {e}",
                    self,
                    duration=3000,
                )
        if self._corpusStore is None:
            self._updateFileCount()

    def _loadDocx(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 Word 文档",
            "",
            "Word Documents (*.docx);;All Files (*)",
        )
        if not files:
            return
        ok_count = 0
        for f in files:
            try:
                text = loadDocxFile(f)
                if self._corpusStore is not None:
                    self._corpusStore.addRawText(os.path.basename(f), text)
                else:
                    self.rawTexts[os.path.basename(f)] = text
                ok_count += 1
            except Exception as e:
                logger.error(f"[_loadDocx] 读取文件 {os.path.basename(f)} 失败: {e}")
                _showInfoBar(
                    "error",
                    "加载失败",
                    f"{os.path.basename(f)}: {e}",
                    self,
                    duration=3000,
                )
        if ok_count > 0:
            _showInfoBar(
                "success",
                "加载成功",
                f"成功加载 {ok_count} 个 Docx 文件",
                self,
                duration=2500,
            )
        if self._corpusStore is None:
            self._updateFileCount()

    def _clearAll(self) -> None:
        if self._corpusStore is not None:
            self._corpusStore.clearAll()
            return
        self.rawTexts = {}
        self._updateFileCount()
        self.importStatusLabel.setText("已清空")

    # ------------------------------------------------------------------
    # 清洗规则卡片（独立方法，供 _buildFileCard 之后添加）
    # ------------------------------------------------------------------
    def _buildCleanCard(self, parent: QWidget) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        title = StrongBodyLabel("2. 清洗规则（分词前预处理）", card)
        layout.addWidget(title)

        hint = CaptionLabel(
            "提示：规则按以下顺序应用：替换 → 移除英文/数字/标点/特殊符号 → 自定义字符串/正则 → 合并空白 → 小写化",
            card,
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 第一行：开关
        switchRow = QHBoxLayout()
        switchRow.setSpacing(20)

        self.cleanEnableSwitch = _makeSwitchButton("启用清洗", self)
        self.cleanEnableSwitch.setChecked(False)
        self.cleanEnableSwitch.checkedChanged.connect(self._onCleanEnableChanged)
        switchRow.addWidget(self.cleanEnableSwitch)

        self.cleanEnglishSwitch = _makeSwitchButton("移除英文", self)
        self.cleanEnglishSwitch.setChecked(False)
        switchRow.addWidget(self.cleanEnglishSwitch)

        self.cleanDigitSwitch = _makeSwitchButton("移除数字", self)
        self.cleanDigitSwitch.setChecked(False)
        switchRow.addWidget(self.cleanDigitSwitch)

        self.cleanPunctSwitch = _makeSwitchButton("移除标点", self)
        self.cleanPunctSwitch.setChecked(False)
        switchRow.addWidget(self.cleanPunctSwitch)

        self.cleanSpecialSwitch = _makeSwitchButton("移除特殊符号", self)
        self.cleanSpecialSwitch.setChecked(False)
        switchRow.addWidget(self.cleanSpecialSwitch)

        self.cleanLowerSwitch = _makeSwitchButton("统一小写", self)
        self.cleanLowerSwitch.setChecked(False)
        switchRow.addWidget(self.cleanLowerSwitch)
        switchRow.addStretch(1)
        layout.addLayout(switchRow)

        # 第二行：自定义输入
        inputRow = QHBoxLayout()
        inputRow.setSpacing(12)

        removeWrap = QVBoxLayout()
        removeLabel = BodyLabel("自定义移除字符串（每行一项）", card)
        self.cleanRemoveEdit = PlainTextEdit(card)
        self.cleanRemoveEdit.setPlaceholderText(
            "例如：\n【示例】\n[广告]\nhttp://\nwww."
        )
        self.cleanRemoveEdit.setFixedHeight(110)
        removeWrap.addWidget(removeLabel)
        removeWrap.addWidget(self.cleanRemoveEdit, 1)
        inputRow.addLayout(removeWrap, 1)

        regexWrap = QVBoxLayout()
        regexLabel = BodyLabel("自定义正则表达式（每行一项）", card)
        self.cleanRegexEdit = PlainTextEdit(card)
        self.cleanRegexEdit.setPlaceholderText(
            "例如：\n\\d{4,}  (4 位以上数字)\n\\b[A-Z]+\\b  (全大写单词)"
        )
        self.cleanRegexEdit.setFixedHeight(110)
        regexWrap.addWidget(regexLabel)
        regexWrap.addWidget(self.cleanRegexEdit, 1)
        inputRow.addLayout(regexWrap, 1)

        replaceWrap = QVBoxLayout()
        replaceLabel = BodyLabel("自定义替换（原串=>新串，每行一项）", card)
        self.cleanReplaceEdit = PlainTextEdit(card)
        self.cleanReplaceEdit.setPlaceholderText("例如：\n人工智能=>AI\n机器学习=>ML")
        self.cleanReplaceEdit.setFixedHeight(110)
        replaceWrap.addWidget(replaceLabel)
        replaceWrap.addWidget(self.cleanReplaceEdit, 1)
        inputRow.addLayout(replaceWrap, 1)

        layout.addLayout(inputRow)

        # 第三行：操作按钮
        btnRow = QHBoxLayout()

        presetLabel = BodyLabel("清洗预设:", card)
        btnRow.addWidget(presetLabel)
        self.presetCombo = ComboBox(card)
        self.presetCombo.setMinimumWidth(260)
        self._reloadPresetCombo()
        btnRow.addWidget(self.presetCombo)
        applyPresetBtn = PushButton("应用预设", card)
        applyPresetBtn.setIcon(FluentIcon.DOWNLOAD)
        applyPresetBtn.clicked.connect(self._applyPreset)
        btnRow.addWidget(applyPresetBtn)

        previewBtn = PushButton("预览清洗效果", card)
        previewBtn.setIcon(FluentIcon.VIEW)
        previewBtn.clicked.connect(self._previewCleaning)
        btnRow.addWidget(previewBtn)

        resetBtn = TransparentPushButton("恢复默认", card)
        resetBtn.clicked.connect(self._resetCleanUi)
        btnRow.addWidget(resetBtn)

        btnRow.addStretch(1)
        self.cleanSummaryLabel = CaptionLabel("", card)
        self.cleanSummaryLabel.setStyleSheet("color: #666; font-size: 11px;")
        btnRow.addWidget(self.cleanSummaryLabel)
        layout.addLayout(btnRow)

        # 联动
        self._onCleanEnableChanged(self.cleanEnableSwitch.isChecked())
        for w in (
            self.cleanEnglishSwitch,
            self.cleanDigitSwitch,
            self.cleanPunctSwitch,
            self.cleanSpecialSwitch,
            self.cleanLowerSwitch,
            self.cleanRemoveEdit,
            self.cleanRegexEdit,
            self.cleanReplaceEdit,
        ):
            if isinstance(w, SwitchButton):
                w.checkedChanged.connect(self._refreshCleanSummary)
                # 推送到 store 的同步
                w.checkedChanged.connect(self._pushCleanToStore)
            else:
                w.textChanged.connect(self._refreshCleanSummary)
                w.textChanged.connect(self._pushCleanToStore)
        self.cleanEnableSwitch.checkedChanged.connect(self._pushCleanToStore)
        self._refreshCleanSummary()
        return card

    def _onCleanEnableChanged(self, checked: bool) -> None:
        for w in (
            self.cleanEnglishSwitch,
            self.cleanDigitSwitch,
            self.cleanPunctSwitch,
            self.cleanSpecialSwitch,
            self.cleanLowerSwitch,
            self.cleanRemoveEdit,
            self.cleanRegexEdit,
            self.cleanReplaceEdit,
        ):
            w.setEnabled(checked)

    def _refreshCleanSummary(self) -> None:
        rule = self._collectCleanRule()
        if not rule.isEnabled():
            self.cleanSummaryLabel.setText("当前未启用任何清洗规则")
            return
        bits: List[str] = []
        if rule.removeEnglish:
            bits.append("英文")
        if rule.removeDigits:
            bits.append("数字")
        if rule.removePunct:
            bits.append("标点")
        if rule.removeSpecialSymbols:
            bits.append("特殊符号")
        if rule.customRemoveList:
            bits.append(f"自定义字符串×{len(rule.customRemoveList)}")
        if rule.customRegexList:
            bits.append(f"自定义正则×{len(rule.customRegexList)}")
        if rule.replaceMap:
            bits.append(f"自定义替换×{len(rule.replaceMap)}")
        if rule.lowercase:
            bits.append("统一小写")
        self.cleanSummaryLabel.setText("已启用：" + " / ".join(bits))

    # ------------------------------------------------------------------
    # 词性标记 (POS) 卡片
    # ------------------------------------------------------------------
    def _buildPosCard(self, parent: QWidget) -> CardWidget:
        """词性标记卡片:
        - 顶部后端状态条(基于 jieba.posseg)
        - 多选词性类别(名词/动词/形容词/...) -> 实时预览 + 写入 store
        - 「POS 预览」按钮:对当前已有语料跑一段示例并展示前 N 条标注结果
        """
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        title = StrongBodyLabel("3. 词性标记（POS Tagging）", card)
        layout.addWidget(title)

        # 后端状态
        backend = availablePosBackend()
        backendHint = CaptionLabel(
            f"后端: <b>{backend}</b>  ·  标注结果受所选词性过滤",
            card,
        )
        backendHint.setStyleSheet("color: #666; font-size: 11px;")
        backendHint.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(backendHint)

        # 启用开关 + 类别多选
        enableRow = QHBoxLayout()
        enableRow.setSpacing(20)

        self.posEnableSwitch = _makeSwitchButton("启用词性过滤", self)
        self.posEnableSwitch.setChecked(False)
        enableRow.addWidget(self.posEnableSwitch)

        # 类别多选容器(动态生成)
        self.posCheckBoxes: Dict[str, CheckBox] = {}
        categories = posTagCategories()
        for cat in categories:
            cb = CheckBox(cat["label"], self)
            cb.setToolTip(cat["description"])
            cb.setChecked(False)
            cb.stateChanged.connect(self._onPosSelectionChanged)
            self.posCheckBoxes[cat["key"]] = cb
            enableRow.addWidget(cb)
        enableRow.addStretch(1)
        layout.addLayout(enableRow)

        # 操作按钮行
        btnRow = QHBoxLayout()
        previewBtn = PushButton("POS 预览", self)
        previewBtn.setIcon(FluentIcon.VIEW)
        previewBtn.clicked.connect(self._showPosPreview)
        btnRow.addWidget(previewBtn)

        btnRow.addStretch(1)
        self.posSummaryLabel = CaptionLabel("已选 0 个词性,过滤未启用", card)
        self.posSummaryLabel.setStyleSheet("color: #666; font-size: 11px;")
        btnRow.addWidget(self.posSummaryLabel)
        layout.addLayout(btnRow)

        # 联动:_pushPosToStore 在切换开关/类别时调用,把配置写入 CorpusStore
        self.posEnableSwitch.checkedChanged.connect(self._onPosEnableChanged)
        return card

    def _selectedPosCategories(self) -> List[str]:
        """返回当前已勾选的词性类别 key 列表(顺序与 posTagCategories 一致)。"""
        return [
            key for key in self.posCheckBoxes if self.posCheckBoxes[key].isChecked()
        ]

    def _onPosEnableChanged(self, checked: bool) -> None:
        """启用开关变化时,同步到 CorpusStore。"""
        self._pushPosToStore()

    def _onPosSelectionChanged(self) -> None:
        """类别勾选变化时,自动打开开关 + 推送到 store。"""
        selected = self._selectedPosCategories()
        if selected and not self.posEnableSwitch.isChecked():
            self.posEnableSwitch.setChecked(True)
        self._pushPosToStore()

    def _pushPosToStore(self) -> None:
        """把当前 POS 配置推送到 CorpusStore;同时刷新摘要文案。"""
        enabled = self.posEnableSwitch.isChecked()
        selected = self._selectedPosCategories()
        posTags: Optional[set] = posTagsFilter(selected) if enabled else None

        if self._corpusStore is not None:
            # CorpusStore 新增 posTags 字段(若不存在则跳过)
            if hasattr(self._corpusStore, "setPosTags"):
                self._corpusStore.setPosTags(posTags, enabled=enabled)

        # 摘要
        if not selected:
            summary = "已选 0 个词性,过滤未启用"
        else:
            labels = [self.posCheckBoxes[k].text() for k in selected]
            state = "已启用" if enabled else "未启用(仅预览)"
            summary = f"已选 {len(selected)} 个词性({', '.join(labels)}),{state}"
        self.posSummaryLabel.setText(summary)

    def _showPosPreview(self) -> None:
        """对当前语料的第一段做 POS 标注,在弹窗中展示前 N 条结果。"""
        # 取当前语料:store 优先,其次 rawTexts
        sampleText = ""
        if self._corpusStore is not None and self._corpusStore.rawTexts:
            # 取前 1 个文件
            firstName = next(iter(self._corpusStore.rawTexts.keys()))
            sampleText = self._corpusStore.rawTexts[firstName]
            sampleText = sampleText[:500]  # 截取前 500 字符,避免过慢
            sampleName = firstName
        elif self.rawTexts:
            firstName = next(iter(self.rawTexts.keys()))
            sampleText = self.rawTexts[firstName][:500]
            sampleName = firstName
        else:
            _showInfoBar(
                "warning",
                "无样例",
                "请先加载语料,再进行 POS 预览",
                self,
                duration=2200,
            )
            return

        tagged = posTag(sampleText)
        if not tagged:
            _showInfoBar(
                "warning",
                "标注为空",
                "样例文本过短或全为空白,无法标注",
                self,
                duration=2200,
            )
            return

        # 弹窗:展示标注结果 + 命中过滤的统计
        selected = self._selectedPosCategories()
        posTagsSet = (
            posTagsFilter(selected) if self.posEnableSwitch.isChecked() else None
        )

        # 构造展示文本
        lines = [
            f"# POS 标注预览 · 后端: {availablePosBackend()}",
            f"# 样例来源: {sampleName}  · 字符数: {len(sampleText)}",
            f"# 词性过滤: {('已启用 ' + ', '.join(selected)) if (selected and self.posEnableSwitch.isChecked()) else '未启用'}",
            "",
            "前 80 条标注:",
            "─" * 40,
        ]
        kept = 0
        for i, (word, tag) in enumerate(tagged[:80]):
            mark = ""
            if posTagsSet is not None:
                if tag in posTagsSet:
                    mark = "  ✓"
                    kept += 1
                else:
                    mark = "  ✗"
            lines.append(f"{i+1:>3}. {word:<12} /{tag}{mark}")
        if posTagsSet is not None:
            lines.append("")
            lines.append(
                f"统计: 总 {len(tagged)} 个 token,命中词性过滤 {kept} 个 ({(kept / max(1, len(tagged[:80])) * 100):.1f}%)"
            )

        PosPreviewDialog.showPreview(
            "\n".join(lines),
            title=f"POS 预览 - {sampleName}",
            parent=self.window(),
        )

    def _collectCleanRule(self) -> CleanRule:
        def _splitLines(text: str) -> List[str]:
            return [line.strip() for line in (text or "").splitlines() if line.strip()]

        replaceMap: Dict[str, str] = {}
        for line in _splitLines(self.cleanReplaceEdit.toPlainText()):
            if "=>" in line:
                src, _, dst = line.partition("=>")
                src = src.strip()
                dst = dst.strip()
                if src:
                    replaceMap[src] = dst

        return CleanRule(
            removeEnglish=self.cleanEnglishSwitch.isChecked(),
            removeDigits=self.cleanDigitSwitch.isChecked(),
            removePunct=self.cleanPunctSwitch.isChecked(),
            removeSpecialSymbols=self.cleanSpecialSwitch.isChecked(),
            lowercase=self.cleanLowerSwitch.isChecked(),
            customRemoveList=_splitLines(self.cleanRemoveEdit.toPlainText()),
            customRegexList=_splitLines(self.cleanRegexEdit.toPlainText()),
            replaceMap=replaceMap,
        )

    def _pushCleanToStore(self) -> None:
        """把当前 UI 清洗规则 + 总开关推送到 CorpusStore，供其它面板读取

        默认走异步路径(CleanCoordinator),避免 UI 卡顿:
            - 同步去抖 300ms
            - 后台线程清洗 + 写 cache
            - 完成后原子切换规则 + emit 信号

        若未注入 coordinator,则降级为同步路径(保持向后兼容)。
        """
        if self._corpusStore is None:
            return
        rule = self._collectCleanRule()
        enabled = self.cleanEnableSwitch.isChecked()

        if self._cleanCoordinator is not None:
            # 异步路径(默认)
            self._cleanCoordinator.scheduleClean(rule, enabled)
        else:
            # 降级路径(同步,会阻塞 UI)
            logger.warning(
                "[CorpusImportWidget] CleanCoordinator 未注入,使用同步路径(可能卡顿)"
            )
            self._corpusStore.setCleanRule(rule)
            self._corpusStore.setCleanEnabled(enabled)

    # ------------------------------------------------------------------
    # 清洗进度回调(供 Coordinator → UI 显示状态)
    # ------------------------------------------------------------------
    def _onCleanStarted(self):
        self.importStatusLabel.setText("正在后台清洗...")
        self.importStatusLabel.setStyleSheet("color: #1890ff; font-size: 11px;")

    def _onCleanProgress(self, pct: int, msg: str):
        self.importStatusLabel.setText(f"{msg} ({pct}%)")

    def _onCleanFinished(self, elapsed: float, totalChars: int):
        if totalChars > 0:
            self.importStatusLabel.setText(
                f"清洗完成: {totalChars:,} 字符 / 耗时 {elapsed:.1f}s"
            )
        else:
            self.importStatusLabel.setText("清洗完成")
        self.importStatusLabel.setStyleSheet("color: #52c41a; font-size: 11px;")

    def _onCleanFailed(self, err: str):
        self.importStatusLabel.setText(f"清洗失败: {err[:80]}")
        self.importStatusLabel.setStyleSheet("color: #f5222d; font-size: 11px;")
        logger.error(f"[CorpusImportWidget] 清洗失败: {err}")

    def _onCleanBusyChanged(self, busy: bool):
        # 清洗期间禁用「预览清洗效果」按钮,避免冲突
        if hasattr(self, "previewBtn"):
            self.previewBtn.setEnabled(not busy)

    def _resetCleanUi(self) -> None:
        self.cleanEnableSwitch.setChecked(False)
        self.cleanEnglishSwitch.setChecked(False)
        self.cleanDigitSwitch.setChecked(False)
        self.cleanPunctSwitch.setChecked(False)
        self.cleanSpecialSwitch.setChecked(False)
        self.cleanLowerSwitch.setChecked(False)
        self.cleanRemoveEdit.setPlainText("")
        self.cleanRegexEdit.setPlainText("")
        self.cleanReplaceEdit.setPlainText("")
        self._onCleanEnableChanged(False)
        self._pushCleanToStore()

    # ------------------------------------------------------------------
    # 清洗预设
    # ------------------------------------------------------------------
    def _scanPresetFiles(self) -> List[Tuple[str, str]]:
        # 延迟 import：避免与主文件 freq_analyzer_interface 循环依赖
        from app.view.freq_analyzer_interface import PRESETS_DIR

        try:
            os.makedirs(PRESETS_DIR, exist_ok=True)
            entries: List[Tuple[str, str]] = []
            for name in sorted(os.listdir(PRESETS_DIR)):
                if not name.lower().endswith(".json"):
                    continue
                if name.startswith("_"):
                    continue
                absPath = os.path.join(PRESETS_DIR, name)
                if not os.path.isfile(absPath):
                    continue
                try:
                    with open(absPath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    presetName = str(data.get("name") or os.path.splitext(name)[0])
                    entries.append((presetName, absPath))
                except Exception as e:
                    logger.error(f"[_scanPresetFiles] 解析预设失败 {absPath}: {e}")
            return entries
        except Exception as e:
            logger.error(f"[_scanPresetFiles] 扫描预设目录失败: {e}")
            return []

    def _reloadPresetCombo(self) -> None:
        if not hasattr(self, "presetCombo"):
            return
        self.presetCombo.clear()
        files = self._scanPresetFiles()
        if not files:
            self.presetCombo.addItem("(无可用预设)", userData=None)
            return
        for name, absPath in files:
            self.presetCombo.addItem(name, userData=absPath)

    def _applyPreset(self) -> None:
        path = self.presetCombo.currentData()
        if not path or not isinstance(path, str):
            _showInfoBar("info", "提示", "请先选择预设项", self, duration=2000)
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            ruleDict = payload.get("rule", {})
            rule = self._ruleFromDict(ruleDict)
            label = str(
                payload.get("name") or os.path.splitext(os.path.basename(path))[0]
            )
        except Exception as e:
            logger.error(f"[_applyPreset] 加载预设失败 {path}: {e}")
            _showInfoBar("error", "应用失败", f"预设加载失败：{e}", self, duration=3000)
            return

        self._applyRuleToUi(rule)
        self.cleanEnableSwitch.setChecked(True)
        self._onCleanEnableChanged(True)
        self._refreshCleanSummary()
        self._pushCleanToStore()
        logger.info(f"[_applyPreset] 已应用预设：{label}")
        _showInfoBar("success", "预设已应用", f"已加载：{label}", self, duration=2000)

    @classmethod
    def _ruleFromDict(cls, d: Dict[str, Any]) -> CleanRule:
        return CleanRule(
            removeEnglish=bool(d.get("removeEnglish", False)),
            removeDigits=bool(d.get("removeDigits", False)),
            removePunct=bool(d.get("removePunct", False)),
            removeWhitespace=bool(d.get("removeWhitespace", True)),
            removeSpecialSymbols=bool(d.get("removeSpecialSymbols", False)),
            customRemoveList=list(d.get("customRemoveList", []) or []),
            customRegexList=list(d.get("customRegexList", []) or []),
            replaceMap=dict(d.get("replaceMap", {}) or {}),
            lowercase=bool(d.get("lowercase", False)),
        )

    def _applyRuleToUi(self, rule: CleanRule) -> None:
        self.cleanEnglishSwitch.setChecked(rule.removeEnglish)
        self.cleanDigitSwitch.setChecked(rule.removeDigits)
        self.cleanPunctSwitch.setChecked(rule.removePunct)
        self.cleanSpecialSwitch.setChecked(rule.removeSpecialSymbols)
        self.cleanLowerSwitch.setChecked(rule.lowercase)
        self.cleanRemoveEdit.setPlainText("\n".join(rule.customRemoveList or []))
        self.cleanRegexEdit.setPlainText("\n".join(rule.customRegexList or []))
        replaceLines = [f"{k}=>{v}" for k, v in (rule.replaceMap or {}).items()]
        self.cleanReplaceEdit.setPlainText("\n".join(replaceLines))

    def _previewCleaning(self) -> None:
        if not self.rawTexts:
            _showInfoBar("warning", "提示", "请先加载语料文件", self, duration=2000)
            return
        if not self.cleanEnableSwitch.isChecked():
            _showInfoBar(
                "info", "提示", "请先开启「启用清洗」开关", self, duration=2000
            )
            return
        firstName, firstText = next(iter(self.rawTexts.items()))
        sample = (firstText or "")[:500]
        rule = self._collectCleanRule()
        logger.debug(f"[_previewCleaning] 文件={firstName}, 规则={rule}")

        if TextCleaner is not None:
            cleanedSample = TextCleaner(rule).clean(sample)
        else:
            tmpAnalyzer = FrequencyAnalyzer(cleanRule=rule)
            cleanedSample = tmpAnalyzer.cleaner.clean(sample)

        CleanPreviewDialog(firstName, sample, cleanedSample, self.window()).exec()
