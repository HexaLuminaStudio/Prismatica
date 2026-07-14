# coding: utf-8
"""
词频分析插件（对标 AntConc 词频统计）

主入口：定义 Plugin 类与主界面 FreqAnalyzerWidget

组件拆分：
    - UI 工具 (helpers)  : app.view.widgets.freq_analyzer.ui_helpers
    - 弹窗 (dialogs)     : app.view.widgets.freq_analyzer.dialogs
    - 语境分析 (KWIC)    : app.view.widgets.freq_analyzer.concordance_widget
    - 语料导入与清洗     : app.view.widgets.freq_analyzer.corpus_import_widget
    - 词频分析主面板     : app.view.widgets.freq_analyzer.freq_analyzer_widget

本文件保留:
    - 业务核心: CorpusStore、ExcelLoadWorker、FreqWorkerThread
    - 顶层路由: FreqAnalyzerInterface (在 3 个面板之间切换)
"""

import json
import logging
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

import pandas as pd

# 内置模块: presets 目录位于 app/view/widgets/freq_analyzer/presets
_moduleDir = os.path.dirname(os.path.abspath(__file__))
_widgetsDir = os.path.join(_moduleDir, "widgets", "freq_analyzer")
PRESETS_DIR = os.path.join(_widgetsDir, "presets")

from app.view.widgets.freq_analyzer.freq_analyzer_widget import (
    JIEBA_AVAILABLE,  # noqa: F401  兼容性 re-export
)

try:
    import jieba  # type: ignore

    _JIEBA_AVAILABLE = True
except ImportError:
    _JIEBA_AVAILABLE = False
    jieba = None

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
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
    SegmentedWidget,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    TransparentPushButton,
    TransparentToggleToolButton,
)
from qfluentwidgetspro import RoundTableWidget as ProRoundTableWidget
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib import pyplot as plt

# 内置模块: 不再依赖插件 PluginBase

# 从 app.view.widgets.freq_analyzer 包内导入核心
from app.view.widgets.freq_analyzer.freq_engine import (
    FrequencyAnalyzer,
    loadExcelColumn,
    loadTextFile,
    DEFAULT_STOPWORDS_ZH,
    DEFAULT_STOPWORDS_EN,
    CleanRule,
    TextCleaner,
)


# 从 app.view.widgets.freq_analyzer 包内导入 KWIC 模块
from app.view.widgets.freq_analyzer.concordance_engine import (  # type: ignore  # noqa: F401
    ConcordanceEngine,
    ConcordanceResult,
    KwicHit,
    SortMode,
)
from app.view.widgets.freq_analyzer.concordance_widget import (
    ConcordanceWidget,
    CorpusStatusCard,
)


# 设置 matplotlib 中文字体
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False


class CorpusStore(QObject):
    """词频分析与 KWIC 共享的语料状态。

    设计目的：
        用户只需"导入一次语料 + 配置一次清洗"，
        即可在「词频分析」与「语境分析 (KWIC)」两个面板同时使用。
    字段：
        rawTexts:   {filename: raw text}        — 原始文本（只读视图）
        cleanRule:  CleanRule                   — 清洗规则
        cleanEnabled: bool                      — 是否启用清洗
    派生：
        effectiveTexts: 依据 cleanEnabled 与 cleanRule 派生每文件的"最终文本"
                        词频分析与 KWIC 共享该 dict
    信号：
        textsChanged:        任何文本/导入/清空变更都触发
        cleanRuleChanged:    清洗规则或启用状态变更时触发
    """

    textsChanged = Signal()
    cleanRuleChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rawTexts: Dict[str, str] = {}
        self.cleanRule: CleanRule = CleanRule()
        self.cleanEnabled: bool = False
        # 持有 TextCleaner 实例（避免每次清洗重建）
        self._cleaner: Optional[TextCleaner] = None

    # ---------------- 文本变更 ----------------
    def addRawText(self, fileName: str, text: str) -> None:
        self.rawTexts[fileName] = text
        self.textsChanged.emit()

    def removeRawText(self, fileName: str) -> None:
        if fileName in self.rawTexts:
            del self.rawTexts[fileName]
            self.textsChanged.emit()

    def clearAll(self) -> None:
        if self.rawTexts:
            self.rawTexts.clear()
            self.textsChanged.emit()

    # ---------------- 清洗规则 ----------------
    def setCleanEnabled(self, enabled: bool) -> None:
        if enabled == self.cleanEnabled:
            return
        self.cleanEnabled = enabled
        self.cleanRuleChanged.emit()

    def setCleanRule(self, rule: CleanRule) -> None:
        self.cleanRule = rule
        self.cleanRuleChanged.emit()

    # ---------------- 派生数据 ----------------
    def _cleanerInstance(self) -> TextCleaner:
        if self._cleaner is None:
            self._cleaner = TextCleaner()
        return self._cleaner

    def effectiveTexts(self) -> Dict[str, str]:
        """根据当前 cleanEnabled/cleanRule 计算每个文件的最终文本。

        - 未启用 → 直接返回原文
        - 启用   → 走 TextCleaner.clean()
        """
        if not self.cleanEnabled or not self.rawTexts:
            return dict(self.rawTexts)
        cleaner = self._cleanerInstance()
        # TextCleaner 在构造时绑定 rule；切换 rule 后需调用 setRule 重新编译正则
        if cleaner.rule is not self.cleanRule:
            cleaner.setRule(self.cleanRule)
        return {name: cleaner.clean(raw) for name, raw in self.rawTexts.items()}

    def totalChars(self) -> int:
        return sum(len(t) for t in self.rawTexts.values())

    def fileCount(self) -> int:
        return len(self.rawTexts)


class ExcelLoadWorker(QThread):
    """后台加载 Excel 列文本，避免大文件阻塞 UI 线程"""

    progress = Signal(str)  # 当前正在加载的文件名
    finished = Signal(dict)  # {baseName: text}
    failed = Signal(str, str)  # (fileName, errorMsg)

    def __init__(self, files: List[str], column: Optional[str], parent=None):
        super().__init__(parent)
        self._files = files
        self._column = column

    def run(self):
        result: Dict[str, str] = {}
        for f in self._files:
            baseName = os.path.basename(f)
            self.progress.emit(baseName)
            try:
                text = loadExcelColumn(f, column=self._column)
                result[baseName] = text
            except Exception as e:
                self.failed.emit(baseName, str(e))
        self.finished.emit(result)


class FreqWorkerThread(QThread):
    """词频分析后台线程"""

    progress = Signal(int, str)
    finished = Signal(object, object)  # (unigramDf, ngramDf)
    failed = Signal(str)

    def __init__(
        self,
        fileToText: Dict[str, str],
        minLength: int,
        maxLength: int,
        caseSensitive: bool,
        excludeNumbers: bool,
        useStopwords: bool,
        useJieba: bool,
        ngramN: int = 2,
        ngramMinFreq: int = 2,
        cleanRule: Optional[CleanRule] = None,
    ):
        super().__init__()
        self.fileToText = fileToText
        self.minLength = minLength
        self.maxLength = maxLength
        self.caseSensitive = caseSensitive
        self.excludeNumbers = excludeNumbers
        self.useStopwords = useStopwords
        self.useJieba = useJieba
        self.ngramN = max(2, int(ngramN))  # N-gram 阶数，至少为 2
        self.ngramMinFreq = max(1, int(ngramMinFreq))  # 过滤最低频次
        self.cleanRule = cleanRule or CleanRule()
        self._isCanceled = False

    def cancel(self):
        self._isCanceled = True

    def run(self):
        try:
            self.progress.emit(10, "正在初始化分析器...")

            if self._isCanceled:
                return

            analyzer = FrequencyAnalyzer(
                minLength=self.minLength,
                maxLength=self.maxLength,
                caseSensitive=self.caseSensitive,
                excludeNumbers=self.excludeNumbers,
                useStopwords=self.useStopwords,
                useJieba=self.useJieba and JIEBA_AVAILABLE,
                cleanRule=self.cleanRule,
            )

            self.progress.emit(30, "正在分词与统计...")
            if self._isCanceled:
                return

            unigramDf = analyzer.analyzeCorpus(self.fileToText)

            if self._isCanceled:
                return

            label = "Bigram" if self.ngramN == 2 else f"{self.ngramN}-gram"
            self.progress.emit(70, f"正在生成 {label}...")
            ngramDf = analyzer.analyzeNgrams(self.fileToText, n=self.ngramN)
            if not ngramDf.empty:
                ngramDf = ngramDf[ngramDf["Freq"] >= self.ngramMinFreq]

            self.progress.emit(100, "分析完成")
            self.finished.emit(unigramDf, ngramDf)

        except Exception as e:
            logger.error(f"[FreqWorkerThread] 分析异常: {traceback.format_exc()}")
            self.failed.emit(str(e))


# ---------------------------------------------------------------------------
# 弹窗与 UI 工具函数（已迁移到 widgets/freq_analyzer/ui_helpers 与 dialogs）
# ---------------------------------------------------------------------------
from app.view.widgets.freq_analyzer.ui_helpers import (  # noqa: F401, F403
    _showInfoBar,
    _makeDialogHeader,
    _makeScrollArea,
    _setupDialogClose,
    _makeAlignedItem,
    _makeSwitchButton,
)
from app.view.widgets.freq_analyzer.dialogs import (  # noqa: F401
    ZipfDialog,
    NgramDialog,
    SelectColumnDialog,
    CleanPreviewDialog,
)
from app.view.widgets.freq_analyzer.corpus_import_widget import CorpusImportWidget
from app.view.widgets.freq_analyzer.freq_analyzer_widget import FreqAnalyzerWidget
from app.view.widgets.freq_analyzer.concordance_widget import ConcordanceWidget


class FreqAnalyzerInterface(QWidget):
    """词频分析 / KWIC 内置界面（对标 AntConc）

    集成:
        - 词频分析（FreqAnalyzerWidget）
        - 语境分析 KWIC（ConcordanceWidget）

    通过 SegmentedWidget 在两个面板之间切换。
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("freqAnalyzerInterface")
        # 词频分析与 KWIC 共享同一语料与清洗状态
        self.corpusStore = CorpusStore(self)
        self._buildUi()

    def _buildUi(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        # 分段控件（中心对齐）
        self.segmented = SegmentedWidget(self)
        outer.addWidget(self.segmented, 0, Qt.AlignmentFlag.AlignCenter)

        # 面板容器
        panelContainer = QWidget(self)
        self._panelLayout = QVBoxLayout(panelContainer)
        self._panelLayout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panelContainer, 1)

        # 实例化三个子面板，全部注入共享 CorpusStore
        self._panels = {
            "corpusImport": CorpusImportWidget(
                panelContainer, corpusStore=self.corpusStore
            ),
            "freqAnalyzer": FreqAnalyzerWidget(
                panelContainer, corpusStore=self.corpusStore
            ),
            "concordance": ConcordanceWidget(
                panelContainer, corpusStore=self.corpusStore
            ),
        }
        for key, widget in self._panels.items():
            widget.setObjectName(key)
            self._panelLayout.addWidget(widget)
            widget.hide()

        self.segmented.addItem("corpusImport", "语料导入与清洗")
        self.segmented.addItem("freqAnalyzer", "词频分析")
        self.segmented.addItem("concordance", "语境分析")
        self.segmented.setCurrentItem("corpusImport")
        self._panels["corpusImport"].show()

        self.segmented.currentItemChanged.connect(self._onItemChanged)

    def _onItemChanged(self, routeKey: str) -> None:
        for key, panel in self._panels.items():
            if key == routeKey:
                panel.show()
            else:
                panel.hide()
