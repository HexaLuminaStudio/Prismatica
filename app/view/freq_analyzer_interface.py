# coding: utf-8
"""
词频分析模块（对标 AntConc 词频统计）

主入口：FreqAnalyzerInterface 与内置面板 FreqAnalyzerWidget

组件拆分：
    - UI 工具 (helpers)  : app.view.widgets.freq_analyzer.ui_helpers
    - 弹窗 (dialogs)     : app.view.widgets.freq_analyzer.dialogs
    - 语境分析 (KWIC)    : app.view.widgets.freq_analyzer.concordance_widget
    - 语料导入与清洗     : app.view.widgets.freq_analyzer.corpus_import_widget
    - 词频分析主面板     : app.view.widgets.freq_analyzer.freq_analyzer_widget
    - 词语分析面板       : app.view.widgets.freq_analyzer.word_analysis_widget
                           (含词汇指标 / 高频词 / 词汇分布 / 词汇增长曲线)
    - 搭配分析面板       : app.view.widgets.freq_analyzer.collocation_widget
                           (MI / MI3 / T / LogDice / Z / Delta-P, FR-CLB-001~011)
    - 词语云图面板       : app.view.widgets.freq_analyzer.word_cloud_widget
                           (纯 matplotlib, FR-WDC-001~005)
    - 句法依存图面板     : app.view.widgets.freq_analyzer.dependency_widget
                           (依存分析, FR-DEP-001~005)

本文件保留:
    - 业务核心: CorpusStore、ExcelLoadWorker、FreqWorkerThread
    - 顶层路由: FreqAnalyzerInterface (在 9 个面板之间切换)
"""

import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Tuple

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from loguru import logger

import pandas as pd

# 内置模块: presets 目录位于 app/view/widgets/freq_analyzer/presets
# —————————————————————————————————————————————————————————————————————
# 预设目录采用"双目录"策略:
#   1. BUILTIN_PRESETS_DIR  (只读,内置): 项目源码随包发布的预设模板
#   2. USER_PRESETS_DIR     (可写,用户): config/clean_presets/
#        - 用户通过"导入预设"按钮添加的预设文件存放在这里
#        - 与语料库、清洗缓存同级,均为用户私有数据
#        - 即使更新软件版本,用户预设也不会被覆盖
# UI 下拉框会同时扫描两个目录,并以"(内置)" / "(自定义)" 前缀区分
# —————————————————————————————————————————————————————————————————————
from app.core.utils.setting import CONFIG_FOLDER  # noqa: E402

_moduleDir = os.path.dirname(os.path.abspath(__file__))
_widgetsDir = os.path.join(_moduleDir, "widgets", "freq_analyzer")

# 内置预设目录(随包发布,只读)
BUILTIN_PRESETS_DIR = os.path.join(_widgetsDir, "presets")

# 用户预设目录(config/clean_presets,可写)
USER_PRESETS_DIR = str(CONFIG_FOLDER / "clean_presets")

# 向后兼容(保留 PRESETS_DIR 旧名,指向内置目录)
PRESETS_DIR = BUILTIN_PRESETS_DIR


def getAllPresetDirs() -> List[Tuple[str, bool]]:
    """返回所有预设目录

    Returns:
        List[Tuple[str, bool]]: (目录路径, 是否内置) 元组列表
    """
    return [
        (BUILTIN_PRESETS_DIR, True),  # 内置(只读)
        (USER_PRESETS_DIR, False),  # 用户(可写)
    ]


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
    ToolButton,
    TransparentPushButton,
    TransparentToggleToolButton,
    ToolTipPosition,
)
from qfluentwidgetspro import RoundTableWidget as ProRoundTableWidget
import matplotlib  # noqa: E402

# matplotlib 后端切换要点:
#   1. 必须在 FigureCanvasQTAgg 导入之前调用 matplotlib.use()
#   2. 使用 force=True 确保即使 main.py 设置了 MPLBACKEND=Agg 也能切换
#   3. 关闭 IPython 的 matplotlib 自动集成(避免 VSCode 调试器下 IPython
#      试图 hook 进 QApplication 触发 QtGui.QApplication AttributeError)
#   4. 设置 figure.max_open_warning = 0 防止大量图表时刷屏警告
#   5. interactive(False) 防止 pyplot 在交互模式下抢事件循环
matplotlib.use("QtAgg", force=True)
matplotlib.set_loglevel("warning")
import matplotlib.pyplot as _plt_for_backend

_plt_for_backend.ioff()  # 关闭交互模式(避免 pyplot 抢 Qt 事件循环)
del _plt_for_backend
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvas,
)  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402

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
from app.view.widgets.freq_analyzer.concordance_widget import ConcordanceWidget


# 设置 matplotlib 中文字体
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False


# CorpusStore 已在 app.view.widgets.freq_analyzer.corpus_store 中实现(SQLite + FTS5)。
# 这里 re-export 以保持向后兼容的导入路径。
from app.view.widgets.freq_analyzer.corpus_store import CorpusStore  # noqa: E402,F401


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


class TextLoadWorker(QThread):
    """后台加载纯文本 / Markdown / Word(.docx) 文件，避免大文件阻塞 UI 线程。

    设计要点:
        1. 复用现有的 loadTextFile / loadDocxFile,但调用前/中可被取消
        2. 文本文件支持流式 iter_lines(read1 → line by line),避免一次性 read() 占满内存
        3. 单文件失败不影响后续文件,失败通过 failed 信号上报
        4. 取消通过 cancel() 发起,内部轮询 self._isCanceled 退出

    信号:
        progress = Signal(int, str)         # (当前完成数/总数, 当前文件名)
        finished = Signal(dict)             # {baseName: text}
        failed  = Signal(str, str)           # (fileName, errorMsg)
    """

    progress = Signal(int, str)
    finished = Signal(dict)
    failed = Signal(str, str)

    def __init__(self, files: List[str], parent=None):
        super().__init__(parent)
        self._files = list(files)
        self._isCanceled = False

    def cancel(self):
        """请求取消(由 UI 在用户点「取消」或关闭面板时调用)"""
        self._isCanceled = True

    def _streamLoadText(self, filePath: str, chunkSize: int = 65536) -> str:
        """流式读取文本文件,逐块拼接,避免一次性 read() 大文件占满内存。

        编码自动嗅探:utf-8 → gbk → utf-16 → latin-1 → utf-8(ignore)。
        """
        encodings = ("utf-8", "gbk", "utf-16", "latin-1")
        for enc in encodings:
            try:
                with open(filePath, "r", encoding=enc) as fp:
                    chunks: List[str] = []
                    while not self._isCanceled:
                        chunk = fp.read(chunkSize)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    if self._isCanceled:
                        return ""  # 取消时丢弃已读取内容
                    return "".join(chunks)
            except UnicodeDecodeError:
                continue
        # fallback:忽略错误字符(必须保留原行为)
        with open(filePath, "r", encoding="utf-8", errors="ignore") as fp:
            return fp.read()

    def _streamLoadDocx(self, filePath: str) -> str:
        """读取 .docx,与原 loadDocxFile 行为一致(全量解析段落/表格)。

        docx 库内部已对 body 元素做流式解析,这里复用其 API。
        """
        from app.view.widgets.freq_analyzer.freq_engine import loadDocxFile

        return loadDocxFile(filePath)

    def run(self):
        result: Dict[str, str] = {}
        total = len(self._files)
        for idx, f in enumerate(self._files):
            if self._isCanceled:
                logger.info("[TextLoadWorker] 已取消,提前结束")
                break
            baseName = os.path.basename(f)
            self.progress.emit(idx + 1, baseName)
            try:
                ext = os.path.splitext(f)[1].lower()
                if ext in (".docx",):
                    text = self._streamLoadDocx(f)
                else:
                    text = self._streamLoadText(f)
                if self._isCanceled:
                    break
                result[baseName] = text
            except Exception as e:
                logger.error(f"[TextLoadWorker] 读取文件 {baseName} 失败: {e}")
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
        unigramMinFreq: int = 1,
        stopwords: Optional[set] = None,
        posTags: Optional[set] = None,
        posEnabled: bool = False,
        tokenCache=None,
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
        self.unigramMinFreq = max(1, int(unigramMinFreq))  # 主词频最低频次
        self.stopwords = set(stopwords) if stopwords else None  # None=默认
        self.posTags = set(posTags) if posTags else None
        self.posEnabled = bool(posEnabled and posTags)
        self.tokenCache = tokenCache  # 分词缓存(加速重复分词)
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
                stopwords=self.stopwords,
                posTags=self.posTags,
                posEnabled=self.posEnabled,
                tokenCache=self.tokenCache,
            )

            self.progress.emit(30, "正在分词与统计...")
            if self._isCanceled:
                return

            unigramDf = analyzer.analyzeCorpus(
                self.fileToText, minFreq=self.unigramMinFreq
            )

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
from app.view.widgets.freq_analyzer.network_widget import NetworkWidget
from app.view.widgets.freq_analyzer.sentiment_widget import SentimentWidget
from app.view.widgets.freq_analyzer.word_analysis_widget import WordAnalysisWidget
from app.view.widgets.freq_analyzer.collocation_widget import CollocationWidget
from app.view.widgets.freq_analyzer.construction_widget import ConstructionWidget
from app.view.widgets.freq_analyzer.word_cloud_widget import WordCloudWidget
from app.view.widgets.freq_analyzer.dependency_widget import DependencyWidget
from app.view.widgets.freq_analyzer.keyword_list_widget import KeywordListWidget


class FreqAnalyzerInterface(QWidget):
    """词频分析 / KWIC 内置界面（对标 AntConc）

    集成:
        - 语料库切换 (CorpusManager + CorpusSwitcherWidget)
        - 词频分析（FreqAnalyzerWidget）
        - 词语分析（WordAnalysisWidget：词汇指标 / 高频词 / 词汇分布）
        - 语境分析 KWIC（ConcordanceWidget）
        - 共现网络图（NetworkWidget）

    通过 SegmentedWidget 在面板之间切换。

    多语料库支持:
        - self.corpusManager: 全局 CorpusManager(单例,QObject)
        - self.corpusStore:   当前活动语料库对应的 CorpusStore
        - 当 CorpusManager.activeCorpusChanged 触发时:
            1. 销毁旧 CorpusStore
            2. 基于新 dbPath 创建新 CorpusStore
            3. 重新绑定到所有子面板
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("freqAnalyzerInterface")

        # 1) 语料库管理器(全局单例)
        from app.view.widgets.freq_analyzer.corpus_manager import CorpusManager

        self.corpusManager = CorpusManager(parent=self)

        # 2) 当前活动语料库的 CorpusStore(根据 manager 初始化)
        active = self.corpusManager.activeCorpus()
        if active is None:
            # 没有任何已注册语料库(0 语料库启动场景)
            # → 创建一个指向 default 占位路径的占位 store。
            #   CorpusStore 会在该路径自动创建空 db 文件,
            #   用户后续创建第一个语料库后,manager 触发 activeCorpusChanged,
            #   本类的 _onActiveCorpusChanged 会接管并切换到真实 store。
            from app.core.utils.data_paths import DEFAULT_CORPUS_FILE

            placeholderPath = str(DEFAULT_CORPUS_FILE)
            logger.warning(
                "[FreqAnalyzerInterface] 当前没有可用语料库,"
                "创建占位 store 等待用户后续创建: %s",
                placeholderPath,
            )
            self.corpusStore = CorpusStore(dbPath=placeholderPath, parent=self)
        else:
            self.corpusStore = CorpusStore(dbPath=active.dbPath, parent=self)
            # 通知 manager 同步统计
            self.corpusManager.updateStats(
                active.id,
                self.corpusStore.fileCount(),
                self.corpusStore.totalChars(),
            )

        # 1.4) TokenCache 已经在 CorpusStore 内部创建,这里转发给 panel

        # 1.5) 清洗协调器(异步后台执行清洗,避免 UI 卡顿)
        from app.view.widgets.freq_analyzer.clean_coordinator import CleanCoordinator

        self.cleanCoordinator = CleanCoordinator(self.corpusStore, parent=self)

        # 监听切换信号:重新创建 store + 重新分发到子面板
        self.corpusManager.activeCorpusChanged.connect(self._onActiveCorpusChanged)
        self.corpusManager.registryChanged.connect(self._onRegistryChanged)

        self._buildUi()

    def _buildUi(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        # 顶部导航行:中间 SegmentedWidget + 右侧「?」帮助按钮
        self._currentRouteKey = "corpusImport"  # 默认面板 key
        navRow = QHBoxLayout()
        navRow.setContentsMargins(0, 0, 0, 0)
        navRow.setSpacing(12)

        # 分段控件(居中、可伸展)
        self.segmented = SegmentedWidget(self)
        navRow.addWidget(self.segmented, 1, Qt.AlignmentFlag.AlignCenter)

        # 帮助按钮(右侧):显示当前面板的术语解释
        self.helpButton = ToolButton(FluentIcon.QUESTION, self)
        self.helpButton.setToolTip("查看当前子页面的术语解释")
        self.helpButton.setFixedSize(28, 28)
        self.helpButton.clicked.connect(self._onHelpClicked)
        navRow.addWidget(self.helpButton, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addLayout(navRow)

        # 面板容器
        panelContainer = QWidget(self)
        self._panelLayout = QVBoxLayout(panelContainer)
        self._panelLayout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panelContainer, 1)

        # 实例化六个子面板，全部注入共享 CorpusStore
        self._panels = {
            "corpusImport": CorpusImportWidget(
                panelContainer,
                corpusStore=self.corpusStore,
                corpusManager=self.corpusManager,
                cleanCoordinator=self.cleanCoordinator,
            ),
            "freqAnalyzer": FreqAnalyzerWidget(
                panelContainer, corpusStore=self.corpusStore
            ),
            "wordAnalysis": WordAnalysisWidget(
                panelContainer, corpusStore=self.corpusStore
            ),
            "concordance": ConcordanceWidget(
                panelContainer, corpusStore=self.corpusStore
            ),
            "sentiment": SentimentWidget(panelContainer, corpusStore=self.corpusStore),
            "network": NetworkWidget(panelContainer, corpusStore=self.corpusStore),
            "collocation": CollocationWidget(
                panelContainer, corpusStore=self.corpusStore
            ),
            "construction": ConstructionWidget(
                panelContainer, corpusStore=self.corpusStore
            ),
            "wordCloud": WordCloudWidget(panelContainer, corpusStore=self.corpusStore),
            "dependency": DependencyWidget(
                panelContainer, corpusStore=self.corpusStore
            ),
            "keywordList": KeywordListWidget(
                panelContainer,
                corpusStore=self.corpusStore,
                corpusManager=self.corpusManager,
            ),
        }
        for key, widget in self._panels.items():
            widget.setObjectName(key)
            self._panelLayout.addWidget(widget)
            widget.hide()

        self.segmented.addItem("corpusImport", "语料导入与清洗")
        self.segmented.addItem("freqAnalyzer", "词频分析")
        self.segmented.addItem("wordAnalysis", "词语分析")
        self.segmented.addItem("keywordList", "主题词分析")
        self.segmented.addItem("concordance", "语境分析")
        self.segmented.addItem("sentiment", "情感分析")
        self.segmented.addItem("collocation", "搭配分析")
        self.segmented.addItem("construction", "构式搭配强度")
        self.segmented.addItem("wordCloud", "词语云图")
        self.segmented.addItem("network", "共现网络图")
        self.segmented.addItem("dependency", "句法依存图")
        self.segmented.setCurrentItem("corpusImport")
        self._panels["corpusImport"].show()

        self.segmented.currentItemChanged.connect(self._onItemChanged)

    def _onItemChanged(self, routeKey: str) -> None:
        # 记录当前面板 key(供 _onHelpClicked 使用)
        self._currentRouteKey = routeKey

        for key, panel in self._panels.items():
            if key == routeKey:
                panel.show()
                # 当切换到情感分析面板时,刷新模型状态显示
                if hasattr(panel, "_refreshModelStatus"):
                    panel._refreshModelStatus()
                # 当切换到词频分析面板时,刷新后端下拉框(可能新后端已加载完成)
                if hasattr(panel, "_refreshBackendCombo"):
                    panel._refreshBackendCombo()
            else:
                panel.hide()

    def _onHelpClicked(self):
        """点击帮助按钮:弹出当前子面板的术语解释弹窗"""
        try:
            from app.view.widgets.freq_analyzer.glossary_dialog import (
                showGlossaryDialog,
            )

            logger.info(
                f"[FreqAnalyzerInterface] 用户点击帮助按钮,面板={self._currentRouteKey}"
            )
            showGlossaryDialog(self._currentRouteKey, parent=self.window())
        except Exception as e:
            logger.error(f"[FreqAnalyzerInterface] 弹出术语解释弹窗失败: {e}")

    # ------------------------------------------------------------------
    # 多语料库:活动语料库切换处理
    # ------------------------------------------------------------------
    def _onActiveCorpusChanged(self, newId: int):
        """活动语料库变更时,重建 CorpusStore 并重新分发到所有面板

        处理两种情况:
        1) 切到有效语料库:重建 store 并通知所有子面板
        2) 切到 0(全部被删除):回退到占位 store,等待用户后续创建
        """
        logger.info(
            f"[FreqAnalyzerInterface] 切换语料库: id={newId}, "
            f"旧 store = {self.corpusStore.dbPath}"
        )
        newActive = self.corpusManager.activeCorpus()
        if newActive is None:
            # 全部语料库被删除 → 回退到占位 store,避免子面板持有已删除的 db
            logger.warning(
                "[FreqAnalyzerInterface] 当前无可用语料库," "回退到占位 store"
            )
            try:
                if hasattr(self, "cleanCoordinator"):
                    self.cleanCoordinator.cancelPending()
            except Exception:
                pass
            try:
                self.corpusStore.close()
            except Exception:
                pass
            from app.core.utils.data_paths import DEFAULT_CORPUS_FILE

            self.corpusStore = CorpusStore(dbPath=str(DEFAULT_CORPUS_FILE), parent=self)
            if hasattr(self, "cleanCoordinator"):
                # P0-fix:通过公开 setter 切换,避免直接访问私有成员
                try:
                    if hasattr(self.cleanCoordinator, "setCorpusStore"):
                        self.cleanCoordinator.setCorpusStore(self.corpusStore)
                    else:
                        self.cleanCoordinator._store = self.corpusStore
                        self.cleanCoordinator._currentHash = self.corpusStore._ruleHash(
                            self.corpusStore._cleanRule
                        )
                except Exception:
                    pass
            for panel in self._panels.values():
                if hasattr(panel, "setCorpusStore"):
                    try:
                        panel.setCorpusStore(self.corpusStore)
                    except Exception as e:
                        logger.error(
                            f"[FreqAnalyzerInterface] "
                            f"重绑 {type(panel).__name__} 失败: {e}"
                        )
            return

        # 0) 取消在途的清洗任务(防止脏数据跨语料库)
        try:
            if hasattr(self, "cleanCoordinator"):
                self.cleanCoordinator.cancelPending()
                self.cleanCoordinator._currentWorker = None
        except Exception:
            pass

        # 1) 关闭旧 store
        try:
            self.corpusStore.close()
        except Exception as e:
            logger.warning(f"[FreqAnalyzerInterface] 关闭旧 store 失败: {e}")

        # 2) 创建新 store
        self.corpusStore = CorpusStore(dbPath=newActive.dbPath, parent=self)

        # 2.5) 重建清洗协调器,指向新 store
        # P0-fix:不要直接访问 _store / _currentHash 私有成员,改用公开 setter
        try:
            if hasattr(self.cleanCoordinator, "setCorpusStore"):
                self.cleanCoordinator.setCorpusStore(self.corpusStore)
            else:
                # 兼容旧版本
                self.cleanCoordinator.cancelPending()
                self.cleanCoordinator._store = self.corpusStore
                self.cleanCoordinator._currentHash = self.corpusStore._ruleHash(
                    self.corpusStore._cleanRule
                )
        except Exception as e:
            logger.warning(f"[FreqAnalyzerInterface] 重置 coordinator 失败: {e}")

        # 3) 重新分发到所有子面板(子面板的 _bindCorpusStore 已实现)
        for panel in self._panels.values():
            if hasattr(panel, "setCorpusStore"):
                try:
                    panel.setCorpusStore(self.corpusStore)
                except Exception as e:
                    logger.error(
                        f"[FreqAnalyzerInterface] 重绑 {type(panel).__name__} 失败: {e}"
                    )

        # 4) 通知 manager 统计已变更(用于 UI 列表展示)
        self.corpusManager.updateStats(
            newActive.id,
            self.corpusStore.fileCount(),
            self.corpusStore.totalChars(),
        )

        logger.info(
            f"[FreqAnalyzerInterface] 已切换到语料库「{newActive.name}」, "
            f"db={newActive.dbPath}"
        )

    def _onRegistryChanged(self):
        """注册表变化(新建/删除/重命名) - 各面板无需重建,switcher UI 自动刷新"""
        pass  # CorpusSwitcherWidget 已订阅 registryChanged

    def closeEvent(self, event):  # noqa: D401
        try:
            self.corpusStore.close()
        except Exception:
            pass
        super().closeEvent(event)
