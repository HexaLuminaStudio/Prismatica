# coding: utf-8
"""
语境分析（KWIC）UI 主面板

对应需求文档: test/6D-CorpusClient_需求文档_v3.md §2.4.2

功能覆盖:
    - FR-KWC-001 关键词居中展示（节点词高亮）
    - FR-KWC-002 可配置语境宽度（左/右独立）
    - FR-KWC-003 索引行排序（4 种）
    - FR-KWC-004 二次检索（多层嵌套）
    - FR-KWC-005 随机抽样
    - FR-KWC-006 上下文扩展（详情弹窗）
    - FR-KWC-007 结果统计（顶部摘要栏）
    - FR-KWC-008 结果导出（TXT / CSV）
"""

from __future__ import annotations

import csv
import logging
import os
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
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
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBoxBase,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TransparentPushButton,
    TransparentToggleToolButton,
)

from app.view.widgets.freq_analyzer.concordance_engine import (
    ConcordanceEngine,
    ConcordanceResult,
    KwicHit,
    SortMode,
)
from app.view.widgets.freq_analyzer.result_summary import MetricColor

logger = logging.getLogger(__name__)


# 节点词高亮颜色（柔和黄色背景）
_NODE_HIGHLIGHT_COLOR = QColor("#FFF7B0")


def _makeCleanSwitchButton(text: str, toolTip: str, parent: QWidget) -> "SwitchButton":
    """SwitchButton 工厂：开关文字始终保持不变

    SwitchButton 默认在勾选后切换为内置的 "On"/"Off" 文本，
    这里把 on/off 文本固定为同一 text，避免用户混淆。
    """
    btn = SwitchButton(text, parent)
    btn.setOnText(text)
    btn.setOffText(text)
    btn.setToolTip(toolTip)
    return btn


def _makeDialogHeader(
    dialog: "MessageBoxBase",
    iconPath: str,
    title: str,
    onClose,
) -> QHBoxLayout:
    """构造弹窗标题栏（图标 + 标题 + 弹性 + 关闭按钮），并追加到 viewLayout。"""
    iconLabel = QSvgWidget(iconPath, dialog)
    iconLabel.setFixedSize(20, 20)
    titleLabel = SubtitleLabel(title, dialog)
    titleLabel.setAlignment(Qt.AlignmentFlag.AlignLeft)
    closeBtn = TransparentToggleToolButton(FluentIcon.CLOSE, dialog)
    closeBtn.clicked.connect(onClose)

    headerLayout = QHBoxLayout()
    headerLayout.addWidget(iconLabel, 0, Qt.AlignmentFlag.AlignLeft)
    headerLayout.addWidget(titleLabel, 0, Qt.AlignmentFlag.AlignLeft)
    headerLayout.addStretch()
    headerLayout.addWidget(closeBtn, 0, Qt.AlignmentFlag.AlignRight)
    dialog.viewLayout.addLayout(headerLayout)
    return headerLayout


# ===========================================================================
# 后台检索线程
# ===========================================================================
class ConcordanceWorker(QThread):
    """KWIC 检索后台线程"""

    progress = Signal(str)
    finished = Signal(object)  # ConcordanceResult
    failed = Signal(str)

    def __init__(
        self,
        engine: ConcordanceEngine,
        fileToText: Dict[str, str],
        searchWord: str,
        leftWidth: int,
        rightWidth: int,
        isRegex: bool,
        sortMode: SortMode,
        secondaryWord: Optional[str],
        secondaryRegex: bool,
        secondaryOffset: int,
        sampleLimit: int,
        sampleRandom: bool,
        parent=None,
    ):
        super().__init__(parent)
        self._engine = engine
        self._fileToText = fileToText
        self._searchWord = searchWord
        self._leftWidth = leftWidth
        self._rightWidth = rightWidth
        self._isRegex = isRegex
        self._sortMode = sortMode
        self._secondaryWord = secondaryWord
        self._secondaryRegex = secondaryRegex
        self._secondaryOffset = secondaryOffset
        self._sampleLimit = sampleLimit
        self._sampleRandom = sampleRandom

    def run(self):
        try:
            self.progress.emit("正在检索节点词...")
            result = self._engine.search(
                fileToText=self._fileToText,
                searchWord=self._searchWord,
                leftWidth=self._leftWidth,
                rightWidth=self._rightWidth,
                isRegex=self._isRegex,
                sortMode=self._sortMode,
                secondaryWord=self._secondaryWord,
                secondaryRegex=self._secondaryRegex,
                sampleLimit=self._sampleLimit,
                sampleRandom=self._sampleRandom,
            )
            self.progress.emit(f"完成：共 {result.totalMatches} 条命中")
            self.finished.emit(result)
        except Exception as e:
            logger.exception("[ConcordanceWorker] 检索异常")
            self.failed.emit(str(e))


# ===========================================================================
# 上下文扩展弹窗（FR-KWC-006）
# ===========================================================================
class KwicExpandDialog(MessageBoxBase):
    """点击索引行后展开更宽上下文"""

    def __init__(
        self,
        hit: KwicHit,
        expandedLeft: List[str],
        expandedRight: List[str],
        parent=None,
    ):
        super().__init__(parent)
        self._hit = hit

        # 标题栏
        iconLabel = _makeSvgIcon(":app/icons/Setting.svg", self)
        titleLabel = SubtitleLabel("扩展上下文", self)
        titleLabel.setAlignment(Qt.AlignmentFlag.AlignLeft)
        closeBtn = TransparentToggleToolButton(FluentIcon.CLOSE, self)
        closeBtn.clicked.connect(self.accept)
        headerLayout = QHBoxLayout()
        headerLayout.addWidget(iconLabel, 0, Qt.AlignmentFlag.AlignLeft)
        headerLayout.addWidget(titleLabel, 0, Qt.AlignmentFlag.AlignLeft)
        headerLayout.addStretch()
        headerLayout.addWidget(closeBtn, 0, Qt.AlignmentFlag.AlignRight)
        self.viewLayout.addLayout(headerLayout)

        # 元信息
        metaLabel = CaptionLabel(
            f"来源文件：{hit.sourceFile}    节点位置：token #{hit.tokenIndex}",
            self,
        )
        metaLabel.setStyleSheet("color: #666; font-size: 12px;")
        self.viewLayout.addWidget(metaLabel)

        # 扩展上下文（彩色拼接：左=灰、节点=黄高亮、右=蓝灰）
        view = PlainTextEdit(self)
        view.setReadOnly(True)
        view.setStyleSheet(
            "QPlainTextEdit {"
            " background-color: #fafafa;"
            " border: 1px solid #e0e0e0;"
            " border-radius: 4px;"
            " padding: 8px;"
            " font-family: 'Consolas', 'Microsoft YaHei', monospace;"
            " font-size: 13px;"
            "}"
        )
        view.setPlainText(self._formatExpanded(expandedLeft, hit, expandedRight))
        self.viewLayout.addWidget(view, 1)

        # 底部关闭按钮
        closeBottom = PushButton("关闭", self)
        closeBottom.clicked.connect(self.accept)
        self.buttonLayout.addWidget(closeBottom)
        self.buttonGroup.hide()
        self.widget.setFixedWidth(720)
        self.widget.setFixedHeight(420)

    @staticmethod
    def _formatExpanded(left: List[str], hit: KwicHit, right: List[str]) -> str:
        leftText = " ".join(left) if left else ""
        nodeText = " ".join(hit.node) if hit.node else ""
        rightText = " ".join(right) if right else ""
        return f"{leftText}  《{nodeText}》  {rightText}".strip()


# ===========================================================================
# 共享：语料状态只读卡
# ===========================================================================
class CorpusStatusCard(CardWidget):
    """只读的语料状态卡（供 FreqAnalyzerWidget 与 ConcordanceWidget 共用）

    行为：
        - 仅展示当前 CorpusStore 中的文件数 / 总字符数 / 提示语
        - 不提供任何"加载 / 清空"按钮；所有写入操作由顶层 CorpusImportWidget 负责
        - 通过 setStore() 绑定 store；store 变化时自动刷新
    """

    def __init__(self, parent=None, corpusStore: Optional["QObject"] = None):
        super().__init__(parent)
        self._corpusStore: Optional[QObject] = corpusStore

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        self._titleLabel = StrongBodyLabel("语料来源", self)
        layout.addWidget(self._titleLabel)

        self._countLabel = CaptionLabel("未加载文件", self)
        self._countLabel.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._countLabel)

        self._hintLabel = CaptionLabel(
            "请到第一个标签「语料导入与清洗」加载文件；此处只读取已清洗后的语料进行分析。",
            self,
        )
        self._hintLabel.setStyleSheet("color: #888; font-size: 11px;")
        self._hintLabel.setWordWrap(True)
        layout.addWidget(self._hintLabel)

        if self._corpusStore is not None:
            self._bindStore(self._corpusStore)

        self._refresh()

    # ------------------------------------------------------------------
    # store 绑定
    # ------------------------------------------------------------------
    def setStore(self, store: "QObject") -> None:
        """运行时注入 / 切换 CorpusStore"""
        if self._corpusStore is store:
            self._refresh()
            return
        self._corpusStore = store
        self._bindStore(store)
        self._refresh()

    def _bindStore(self, store: "QObject") -> None:
        try:
            store.textsChanged.connect(self._refresh)
            store.cleanRuleChanged.connect(self._refresh)
        except AttributeError:
            pass

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        if self._corpusStore is None:
            self._countLabel.setText("未加载文件")
            return
        try:
            n = self._corpusStore.fileCount()
            total = self._corpusStore.totalChars()
        except AttributeError:
            self._countLabel.setText("未加载文件")
            return
        if n == 0:
            self._countLabel.setText("未加载文件")
        else:
            self._countLabel.setText(f"已加载 {n} 个文件，{total:,} 字符")


# ===========================================================================
# 主面板
# ===========================================================================
class ConcordanceWidget(QWidget):
    """语境分析（KWIC）主面板"""

    def __init__(self, parent=None, corpusStore=None):
        super().__init__(parent)
        # 与 FreqAnalyzerWidget 共享：CorpusStore 为权威，本地仅做缓存
        from app.view.freq_analyzer_interface import CorpusStore  # 局部避免循环

        self._corpusStore: Optional[CorpusStore] = corpusStore
        self.fileToText: Dict[str, str] = {}  # 本地缓存（来自 store.effectiveTexts）
        self._worker: Optional[ConcordanceWorker] = None
        self._currentResult: Optional[ConcordanceResult] = None
        self._secondaryStack: List[Dict] = []  # 嵌套二次检索历史
        # 注入 token cache(加速重复分词)
        tokenCache = (
            self._corpusStore.tokenCache() if self._corpusStore is not None else None
        )
        self._engine = ConcordanceEngine(
            useJieba=True, caseSensitive=False, tokenCache=tokenCache
        )

        self._initUi()

        if self._corpusStore is not None:
            self._bindCorpusStore(self._corpusStore)

    # ------------------------------------------------------------------
    # 语料状态绑定（与 FreqAnalyzerWidget 共享 CorpusStore）
    # ------------------------------------------------------------------
    def setCorpusStore(self, store) -> None:
        if self._corpusStore is store:
            return
        self._corpusStore = store
        self._bindCorpusStore(store)
        # 把 store 同步给语料状态卡
        if hasattr(self, "_corpusStatusCard") and self._corpusStatusCard is not None:
            self._corpusStatusCard.setStore(store)
        self._onCorpusChanged()

    def _bindCorpusStore(self, store) -> None:
        store.textsChanged.connect(self._onCorpusChanged)
        store.cleanRuleChanged.connect(self._onCorpusChanged)

    def _onCorpusChanged(self) -> None:
        # 重新从 store 拉取最新清洗后文本
        if self._corpusStore is not None:
            self.fileToText = self._corpusStore.effectiveTexts()
        # 语料/规则变更 → 清空当前 KWIC 结果与二次检索历史
        self._currentResult = None
        if hasattr(self, "resultTable"):
            self.resultTable.setRowCount(0)
        if hasattr(self, "_resultSummary"):
            self._resultSummary.setPlaceholder("语料已变更，请重新检索")
        if hasattr(self, "statusLabel"):
            self.statusLabel.setText("就绪")
        self._secondaryStack = []
        # _updateFileCount 已废弃：原用于更新页面底部 fileCountLabel，
        # 该 UI 元素已被顶部 CorpusStatusCard 替代。

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _initUi(self):
        outerLayout = QVBoxLayout(self)
        outerLayout.setContentsMargins(20, 20, 20, 20)
        outerLayout.setSpacing(12)

        # 标题
        title = SubtitleLabel("语境分析", self)
        outerLayout.addWidget(title)

        # 滚动容器
        scrollArea = ScrollArea(self)
        scrollArea.setWidgetResizable(True)
        scrollArea.setStyleSheet("border: none; background: transparent;")
        outerLayout.addWidget(scrollArea, 1)

        scrollContent = QWidget()
        scrollArea.setWidget(scrollContent)
        scrollLayout = QVBoxLayout(scrollContent)
        scrollLayout.setContentsMargins(0, 0, 0, 0)
        scrollLayout.setSpacing(12)

        scrollLayout.addWidget(self._buildCorpusCard())
        scrollLayout.addWidget(self._buildSearchCard())
        scrollLayout.addWidget(self._buildResultCard())
        scrollLayout.addStretch(1)

    def _buildCorpusCard(self) -> "CorpusStatusCard":
        """语料状态卡（只读）

        使用共享的 CorpusStatusCard，统一展示风格与「词频分析」页面的语料来源卡一致。
        """
        card = CorpusStatusCard(self, corpusStore=self._corpusStore)
        self._corpusStatusCard = card  # 保留引用，setCorpusStore 时调用 setStore
        return card

    def _buildSearchCard(self) -> CardWidget:
        """检索参数卡片"""
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(StrongBodyLabel("2. 检索参数", card))

        # 节点词
        row1 = QHBoxLayout()
        row1.addWidget(BodyLabel("节点词:", card))
        self.searchEdit = LineEdit(card)
        self.searchEdit.setPlaceholderText("例如：学习 / 学习|研究 / 学习.*")
        self.searchEdit.setMinimumWidth(200)
        row1.addWidget(self.searchEdit, 1)

        self.regexCheck = CheckBox("正则", card)
        row1.addWidget(self.regexCheck)
        self.caseCheck = CheckBox("区分大小写", card)
        row1.addWidget(self.caseCheck)
        layout.addLayout(row1)

        # 宽度
        row2 = QHBoxLayout()
        row2.setAlignment(Qt.AlignmentFlag.AlignLeft)
        row2.addWidget(BodyLabel("语境宽度:", card))
        row2.addWidget(BodyLabel("左", card))
        self.leftSpin = SpinBox(card)
        self.leftSpin.setRange(0, 50)
        self.leftSpin.setValue(10)
        row2.addWidget(self.leftSpin)
        row2.addWidget(BodyLabel("词", card))
        row2.addSpacing(8)
        row2.addWidget(BodyLabel("右", card))
        self.rightSpin = SpinBox(card)
        self.rightSpin.setRange(0, 50)
        self.rightSpin.setValue(10)
        row2.addWidget(self.rightSpin)
        row2.addWidget(BodyLabel("词", card))
        row2.addSpacing(16)
        layout.addLayout(row2)

        # 排序 + 抽样
        row3 = QHBoxLayout()
        row3.setAlignment(Qt.AlignmentFlag.AlignLeft)
        row3.addWidget(BodyLabel("排序:", card))
        self.sortCombo = ComboBox(card)
        for mode in [
            ("原始语序", SortMode.ORIGINAL),
            ("左 1 词", SortMode.LEFT_FIRST),
            ("右 1 词", SortMode.RIGHT_FIRST),
            ("节点搭配词", SortMode.NODE_COLLOCATE),
        ]:
            self.sortCombo.addItem(mode[0], userData=mode[1])
        row3.addWidget(self.sortCombo)

        row3.addSpacing(16)
        row3.addWidget(BodyLabel("抽样上限:", card))
        self.sampleLimitSpin = SpinBox(card)
        self.sampleLimitSpin.setRange(0, 100000)
        self.sampleLimitSpin.setValue(100)
        row3.addWidget(self.sampleLimitSpin)
        self.sampleRandomCheck = CheckBox("随机抽样", card)
        self.sampleRandomCheck.setChecked(True)
        row3.addWidget(self.sampleRandomCheck)
        layout.addLayout(row3)

        # 二次检索区
        row4 = QHBoxLayout()
        row4.addWidget(BodyLabel("二次检索:", card))
        self.secondaryEdit = LineEdit(card)
        self.secondaryEdit.setPlaceholderText("可选：再次输入检索词进行子集筛选")
        row4.addWidget(self.secondaryEdit, 1)
        row4.addWidget(BodyLabel("位置:", card))
        self.secondaryOffsetSpin = SpinBox(card)
        self.secondaryOffsetSpin.setRange(-10, 10)
        self.secondaryOffsetSpin.setValue(0)
        self.secondaryOffsetSpin.setToolTip(
            "0=节点词本身; 正数=节点词右侧第N词; 负数=左侧第N词"
        )
        row4.addWidget(self.secondaryOffsetSpin)
        self.secondaryRegexCheck = CheckBox("正则", card)
        row4.addWidget(self.secondaryRegexCheck)
        addBtn = PushButton("追加筛选", card)
        addBtn.clicked.connect(self._addSecondary)
        row4.addWidget(addBtn)
        layout.addLayout(row4)

        # 已应用的二次检索栈
        self.secondaryHistoryLabel = CaptionLabel("", card)
        self.secondaryHistoryLabel.setStyleSheet("color: #1a7f37; font-size: 11px;")
        self.secondaryHistoryLabel.setVisible(False)
        layout.addWidget(self.secondaryHistoryLabel)

        # 操作按钮（执行 + 导出 两组，按钮用 Stretch 隔开避免视觉错位）
        btnRow = QHBoxLayout()
        btnRow.setSpacing(8)

        self.searchBtn = PrimaryPushButton("开始检索", card)
        self.searchBtn.setIcon(FluentIcon.SEARCH)
        self.searchBtn.clicked.connect(self._runSearch)
        btnRow.addWidget(self.searchBtn)

        self.resetSecondaryBtn = PushButton("清除二次筛选", card)
        self.resetSecondaryBtn.setIcon(FluentIcon.CANCEL)
        self.resetSecondaryBtn.clicked.connect(self._resetSecondary)
        btnRow.addWidget(self.resetSecondaryBtn)

        btnRow.addStretch(1)

        self.exportTxtBtn = PushButton("导出 TXT", card)
        self.exportTxtBtn.setIcon(FluentIcon.SAVE)
        self.exportTxtBtn.clicked.connect(lambda: self._export("txt"))
        btnRow.addWidget(self.exportTxtBtn)

        self.exportCsvBtn = PushButton("导出 CSV", card)
        self.exportCsvBtn.setIcon(FluentIcon.SAVE)
        self.exportCsvBtn.clicked.connect(lambda: self._export("csv"))
        btnRow.addWidget(self.exportCsvBtn)

        layout.addLayout(btnRow)

        return card

    def _buildResultCard(self) -> CardWidget:
        """结果列表卡片"""
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(StrongBodyLabel("3. 检索结果", card))

        # 统计栏(FR-KWC-007,使用统一 ResultSummary 大指标卡)
        from app.view.widgets.freq_analyzer.result_summary import (
            MetricColor,
            ResultSummary,
        )

        self._resultSummary = ResultSummary(self)
        self._resultSummary.setTitle("检索摘要")
        self._resultSummary.setPlaceholder("请输入检索词并点击「开始检索」")
        layout.addWidget(self._resultSummary)
        # 兼容旧代码:summaryLabel 仍指向 detailLabel,避免外部代码报错
        self.summaryLabel = self._resultSummary._detailLabel

        # 双击行 → 展开详情
        self.resultTable = ProRoundTableWidget(card)
        self.resultTable.setColumnCount(4)
        self.resultTable.setHorizontalHeaderLabels(
            ["来源文件", "左侧语境", "节点词", "右侧语境"]
        )
        self.resultTable.verticalHeader().setVisible(False)
        self.resultTable.setEditTriggers(self.resultTable.EditTrigger.NoEditTriggers)
        self.resultTable.setSelectionBehavior(
            self.resultTable.SelectionBehavior.SelectRows
        )
        self.resultTable.setShowGrid(False)
        self.resultTable.setAlternatingRowColors(True)
        header = self.resultTable.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.resultTable.setColumnWidth(0, 140)
        self.resultTable.setColumnWidth(2, 100)
        self.resultTable.cellDoubleClicked.connect(self._onRowDoubleClicked)
        # CardWidget 内 stretch 无效，给表格一个合理的最小高度避免被压缩到一行
        self.resultTable.setMinimumHeight(360)
        layout.addWidget(self.resultTable)

        # 状态栏
        self.statusLabel = CaptionLabel("", card)
        self.statusLabel.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.statusLabel)
        return card

    # ------------------------------------------------------------------
    # 语料加载 / 清空
    # ------------------------------------------------------------------
    def _loadTextFiles(self):
        # 若绑定 CorpusStore，语料由顶层统一管理，本面板不应重复加载
        if self._corpusStore is not None:
            _showInfoBar(
                "warning",
                "提示",
                "语料已绑定到顶层共享，请前往「词频分析」页面导入",
                self,
                duration=2500,
            )
            return
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择文本文件",
            "",
            "Text Files (*.txt *.md);;All Files (*)",
        )
        if not files:
            return
        for f in files:
            try:
                text = _readTextFile(f)
                self.fileToText[os.path.basename(f)] = text
            except Exception as e:
                logger.error(f"[_loadTextFiles] 读取 {f} 失败: {e}")
                _showInfoBar(
                    "error",
                    "加载失败",
                    f"{os.path.basename(f)}: {e}",
                    self,
                    duration=3000,
                )

    def _clearAll(self):
        if self._corpusStore is not None:
            self._corpusStore.clearAll()
            return
        self.fileToText = {}
        self._currentResult = None
        self.resultTable.setRowCount(0)
        if hasattr(self, "_resultSummary"):
            self._resultSummary.setPlaceholder("已清空 — 请重新检索")
        self.statusLabel.setText("已清空")
        # 顶部 CorpusStatusCard 会通过 corpusStore 信号自动刷新；
        # 旧版本此处调用 _updateFileCount() 更新 fileCountLabel，UI 已移除。
        self._resetSecondary(silent=True)

    def closeEvent(self, event) -> None:
        """关闭前取消后台任务，避免线程悬挂"""
        for w in (self._worker,):
            if w is not None and w.isRunning():
                if hasattr(w, "cancel"):
                    w.cancel()
                w.wait(2000)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # 二次检索管理
    # ------------------------------------------------------------------
    def _addSecondary(self):
        if not self._currentResult:
            _showInfoBar(
                "warning",
                "提示",
                "请先执行主检索",
                self,
                duration=2000,
            )
            return
        word = self.secondaryEdit.text().strip()
        if not word:
            _showInfoBar(
                "warning",
                "提示",
                "请输入二次检索词",
                self,
                duration=2000,
            )
            return
        self._secondaryStack.append(
            {
                "word": word,
                "regex": self.secondaryRegexCheck.isChecked(),
                "offset": self.secondaryOffsetSpin.value(),
            }
        )
        self._refreshSecondaryHistory()
        # 直接基于当前栈再次过滤
        self._applySecondaryStack()

    def _resetSecondary(self, silent: bool = False):
        self._secondaryStack.clear()
        self.secondaryEdit.clear()
        self.secondaryOffsetSpin.setValue(0)
        self.secondaryRegexCheck.setChecked(False)
        self._refreshSecondaryHistory()
        if not silent and self._currentResult:
            self._refreshTableFromResult(self._currentResult)

    def _refreshSecondaryHistory(self):
        if not self._secondaryStack:
            self.secondaryHistoryLabel.setVisible(False)
            self.secondaryHistoryLabel.setText("")
            return
        text = "已应用二次筛选: " + " → ".join(
            f"{item['word']}({'正' if item['regex'] else '字'}·off={item['offset']:+d})"
            for item in self._secondaryStack
        )
        self.secondaryHistoryLabel.setVisible(True)
        self.secondaryHistoryLabel.setText(text)

    def _applySecondaryStack(self):
        if not self._currentResult:
            return
        hits = list(self._currentResult.hits)
        for item in self._secondaryStack:
            hits = self._engine._filterSecondary(  # noqa: SLF001
                hits=hits,
                secondaryWord=item["word"],
                isRegex=item["regex"],
                offset=item["offset"],
            )
        self._currentResult.hits = hits
        self._refreshTableFromResult(self._currentResult)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def _runSearch(self):
        word = self.searchEdit.text().strip()
        if not word:
            _showInfoBar("warning", "提示", "请输入节点词", self, duration=2000)
            return
        if not self.fileToText:
            _showInfoBar(
                "warning",
                "提示",
                "请先加载语料文件",
                self,
                duration=2000,
            )
            return
        if self._worker and self._worker.isRunning():
            return

        # 二次检索栈每次重新跑前都清空，避免叠加
        self._resetSecondary(silent=True)

        self._engine.caseSensitive = self.caseCheck.isChecked()
        self.searchBtn.setEnabled(False)
        self.statusLabel.setText("检索中...")

        self._worker = ConcordanceWorker(
            engine=self._engine,
            fileToText=self.fileToText,
            searchWord=word,
            leftWidth=self.leftSpin.value(),
            rightWidth=self.rightSpin.value(),
            isRegex=self.regexCheck.isChecked(),
            sortMode=self.sortCombo.currentData() or SortMode.ORIGINAL,
            secondaryWord=None,
            secondaryRegex=False,
            secondaryOffset=0,
            sampleLimit=self.sampleLimitSpin.value(),
            sampleRandom=self.sampleRandomCheck.isChecked(),
            parent=self,
        )
        self._worker.progress.connect(self._onProgress)
        self._worker.finished.connect(self._onSearchFinished)
        self._worker.failed.connect(self._onSearchFailed)
        self._worker.start()

    def _onProgress(self, msg: str):
        self.statusLabel.setText(msg)

    def _onSearchFailed(self, err: str):
        self.searchBtn.setEnabled(True)
        self.statusLabel.setText(f"检索失败: {err}")
        logger.error(f"[_onSearchFailed] {err}")
        _showInfoBar("error", "检索失败", err, self, duration=3000)

    def _onSearchFinished(self, result: ConcordanceResult):
        self.searchBtn.setEnabled(True)
        self._currentResult = result
        self._refreshTableFromResult(result)
        logger.info(
            f"[_onSearchFinished] 节点词={result.searchWord!r} "
            f"命中={result.totalMatches} 展示={len(result.hits)}"
        )

    def _refreshTableFromResult(self, result: ConcordanceResult):
        # 摘要(FR-KWC-007,使用统一大指标卡)
        coverage = 0.0
        try:
            totalChars = self._corpusStore.totalChars() if self._corpusStore else 0
            coverage = min(100.0, result.totalMatches * 50 / max(1, totalChars) * 100)
        except Exception:
            pass
        leftW = self.leftSpin.value()
        rightW = self.rightSpin.value()

        self._resultSummary.clear()
        self._resultSummary.setMetrics(
            [
                ("检索词", result.searchWord, MetricColor.PRIMARY),
                ("命中数", f"{result.totalMatches:,}", MetricColor.SUCCESS),
                ("当前展示", f"{len(result.hits):,}", MetricColor.ACCENT),
                ("覆盖度", f"{coverage:.1f}%", MetricColor.NEUTRAL),
            ]
        )
        self._resultSummary.setDetail(
            f"🔍 语料库 <b>{result.corpusName}</b> &nbsp;|&nbsp; "
            f"语境宽度 <b>L{leftW}/R{rightW}</b> &nbsp;|&nbsp; "
            f"双击行可展开 <b>±100 词</b> 详情"
        )
        self.statusLabel.setText(f"双击索引行可查看上下文扩展（前后各 100 词）")

        self.resultTable.setRowCount(len(result.hits))
        for i, hit in enumerate(result.hits):
            fileItem = QTableWidgetItem(hit.sourceFile)
            fileItem.setToolTip(hit.sourceFile)
            self.resultTable.setItem(i, 0, fileItem)

            leftItem = QTableWidgetItem(hit.leftText)
            leftItem.setForeground(QColor("#666666"))
            self.resultTable.setItem(i, 1, leftItem)

            # 节点词高亮（FR-KWC-001）
            nodeItem = QTableWidgetItem(hit.nodeText)
            nodeItem.setBackground(_NODE_HIGHLIGHT_COLOR)
            nodeItem.setForeground(QColor("#c2410c"))
            font = nodeItem.font()
            font.setBold(True)
            nodeItem.setFont(font)
            self.resultTable.setItem(i, 2, nodeItem)

            rightItem = QTableWidgetItem(hit.rightText)
            rightItem.setForeground(QColor("#666666"))
            self.resultTable.setItem(i, 3, rightItem)

    # ------------------------------------------------------------------
    # 双击行 → 上下文扩展（FR-KWC-006）
    # ------------------------------------------------------------------
    def _onRowDoubleClicked(self, row: int, _col: int):
        if not self._currentResult:
            return
        if row < 0 or row >= len(self._currentResult.hits):
            return
        hit = self._currentResult.hits[row]

        # 重建该文件的完整分词流（带行号信息，扩展时按行号裁剪避免跨行）
        text = self.fileToText.get(hit.sourceFile, "")
        try:
            fullTokens, lineMap = self._engine.buildContextMap(text)
            expandedLeft, expandedRight = self._engine.expandContext(
                hit=hit,
                fullTokensByFile={hit.sourceFile: (fullTokens, lineMap)},
                expandWidth=100,
            )
        except Exception as e:
            logger.error(f"[_onRowDoubleClicked] 扩展失败: {e}")
            _showInfoBar(
                "error",
                "扩展失败",
                str(e),
                self,
                duration=3000,
            )
            return
        dlg = KwicExpandDialog(hit, expandedLeft, expandedRight, self.window())
        dlg.exec()

    # ------------------------------------------------------------------
    # 导出（FR-KWC-008）
    # ------------------------------------------------------------------
    def _export(self, fmt: str):
        if not self._currentResult or not self._currentResult.hits:
            _showInfoBar("warning", "提示", "暂无可导出结果", self, duration=2000)
            return
        if fmt == "txt":
            defaultName = "kwic_results.txt"
            filt = "TXT Files (*.txt)"
            path, _ = QFileDialog.getSaveFileName(
                self, "导出 KWIC TXT", defaultName, filt
            )
            if not path:
                return
            if not path.lower().endswith(".txt"):
                path += ".txt"
            try:
                with open(path, "w", encoding="utf-8") as f:
                    secondaryTxt = (
                        " -> ".join(
                            f"{i['word']}(off={i['offset']:+d})"
                            for i in self._secondaryStack
                        )
                        or "(无二次筛选)"
                    )
                    f.write(
                        f"# 检索词: {self._currentResult.searchWord}\n"
                        f"# 命中: {self._currentResult.totalMatches}\n"
                        f"# 语料库: {self._currentResult.corpusName}\n"
                        f"# 语境宽度: L{self.leftSpin.value()}/R{self.rightSpin.value()}\n"
                        f"# 二次筛选: {secondaryTxt}\n\n"
                    )
                    for hit in self._currentResult.hits:
                        f.write(
                            f"[{hit.sourceFile}] "
                            f"{hit.leftText}  《{hit.nodeText}》  {hit.rightText}\n"
                        )
                _showInfoBar("success", "导出成功", f"已保存：{path}", self)
            except Exception as e:
                logger.error(f"[_export] TXT 导出失败: {e}")
                _showInfoBar("error", "导出失败", str(e), self, duration=3000)
        else:
            defaultName = "kwic_results.csv"
            path, _ = QFileDialog.getSaveFileName(
                self, "导出 KWIC CSV", defaultName, "CSV Files (*.csv)"
            )
            if not path:
                return
            if not path.lower().endswith(".csv"):
                path += ".csv"
            try:
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["来源文件", "左侧语境", "节点词", "右侧语境"])
                    for hit in self._currentResult.hits:
                        writer.writerow(
                            [
                                hit.sourceFile,
                                hit.leftText,
                                hit.nodeText,
                                hit.rightText,
                            ]
                        )
                _showInfoBar("success", "导出成功", f"已保存：{path}", self)
            except Exception as e:
                logger.error(f"[_export] CSV 导出失败: {e}")
                _showInfoBar("error", "导出失败", str(e), self, duration=3000)


# ===========================================================================
# 内部工具：PySide6 + qfluentwidgets 复用
# ===========================================================================
# qfluentwidgetspro 才是提供 ProRoundTableWidget 的实际包
try:
    from qfluentwidgetspro import RoundTableWidget as ProRoundTableWidget  # noqa: E402
except ImportError:
    # 兜底：若环境仅有 qfluentwidgets，则使用 QTableWidget
    from qfluentwidgets.components.widgets.table_view import TableWidget as ProRoundTableWidget  # type: ignore  # noqa: E402


def _makeSvgIcon(path: str, parent: QWidget):
    icon = QSvgWidget(path, parent)
    icon.setFixedSize(20, 20)
    return icon


def _readTextFile(filePath: str) -> str:
    encodings = ["utf-8", "gbk", "utf-16", "latin-1"]
    for enc in encodings:
        try:
            with open(filePath, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(filePath, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _showInfoBar(
    kind: str,
    title: str,
    content: str,
    parent: QWidget,
    duration: int = 2500,
) -> None:
    getattr(InfoBar, kind)(
        title,
        content,
        Qt.Orientation.Horizontal,
        True,
        duration,
        InfoBarPosition.TOP_RIGHT,
        parent,
    )
