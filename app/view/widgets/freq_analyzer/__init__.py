"""词频分析模块的 UI 组件包

公共 API:
    - ConcordanceWidget        语境分析 (KWIC) 主面板
    - CorpusStatusCard         共享语料状态卡
    - CorpusImportWidget       语料导入与清洗面板
    - FreqAnalyzerWidget       词频分析主面板
    - NetworkWidget            词语共现网络图主面板(§2.5.2)
    - DependencyWidget         句法依存图主面板(§2.5.3)
    - ZipfDialog               Zipf 曲线图弹窗
    - NgramDialog              N-gram 频率统计弹窗
    - SelectColumnDialog       Excel 列名选择对话框
    - CleanPreviewDialog       清洗前后对比预览对话框

引擎:
    - FrequencyAnalyzer        频率分析器
    - CooccurrenceEngine       共现网络引擎
    - DependencyParser         句法依存分析抽象接口(HanLP/LTP/spaCy/规则)
    - TextCleaner              文本清洗器
    - CleanRule                清洗规则
"""

from .clean_coordinator import CleanCoordinator, CleanWorker
from .concordance_engine import ConcordanceEngine, KwicHit
from .concordance_widget import (
    ConcordanceWidget,
    CorpusStatusCard,
)  # noqa: F401  类仍保留供外部兼容
from .dependency_engine import (
    DependencyParse,
    DependencyParser,
    DepToken,
    getAvailableParsers,
    getDefaultParser,
    splitSentences,
    toConllU,
)
from .dependency_widget import DependencyAnalysisWorker, DependencyWidget
from .corpus_import_widget import CorpusImportWidget
from .corpus_manager import (
    CORPORA_DIR,
    REGISTRY_DB_PATH,
    CorpusInfo,
    CorpusManager,
    CorpusRegistry,
)
from .corpus_store import CorpusStore
from .corpus_switcher_widget import CorpusSwitcherWidget, NewCorpusDialog
from .dialogs import (
    AdvancedSettingsDialog,
    CleanPreviewDialog,
    NgramDialog,
    PosPreviewDialog,
    SelectColumnDialog,
    StopwordsDialog,
    ZipfDialog,
)
from .freq_analyzer_widget import FreqAnalyzerWidget
from .freq_engine import (
    DEFAULT_STOPWORDS_EN,
    DEFAULT_STOPWORDS_ZH,
    CleanRule,
    FrequencyAnalyzer,
    TextCleaner,
    TextSegmenter,
    availablePosBackend,
    defaultStopwords,
    loadDocxFile,
    loadExcelColumn,
    loadStopwordsFromFile,
    loadTextFile,
    parseStopwordsFromText,
    posTag,
    posTagCategories,
    posTagsFilter,
    saveStopwordsToFile,
)
from .network_engine import (
    CooccurrenceEdge,
    CooccurrenceEngine,
    CooccurrenceNetwork,
    NetworkBuildParams,
    colorForCommunity,
)
from .network_widget import NetworkBuildWorker, NetworkWidget
from .result_summary import MetricCard, MetricColor, ResultSummary
from .token_cache import TokenCache, backendModelVersion, hashText
from .word_analysis_engine import (
    DEFAULT_CONTENT_POS,
    POS_COARSE_CATEGORY,
    STRICT_CONTENT_POS,
    CurvePoint,
    CurveStepMode,
    HighFreqEntry,
    POSTag,
    WordAnalysisEngine,
    WordMetrics,
)
from .word_analysis_widget import WordAnalysisWidget, WordAnalysisWorker
from .sentiment_engine import (
    BUILTIN_NEGATIVE,
    BUILTIN_POSITIVE,
    CorpusSentimentResult,
    DEGREE_WORDS,
    DocumentSentiment,
    NEGATION_WORDS,
    ParagraphSentiment,
    Polarity,
    SentenceSentiment,
    SentimentEngine,
    SentimentHit,
)
from .sentiment_widget import SentimentWidget, SentimentWorker

__all__ = [
    "AdvancedSettingsDialog",
    "BUILTIN_NEGATIVE",
    "BUILTIN_POSITIVE",
    "CORPORA_DIR",
    "CleanCoordinator",
    "CleanWorker",
    "ConcordanceEngine",
    "ConcordanceWidget",
    "CorpusImportWidget",
    "CorpusInfo",
    "CorpusManager",
    "CorpusRegistry",
    "CorpusSentimentResult",
    # "CorpusStatusCard",  页面已移除语料来源卡片,类保留供外部 import
    "CorpusStore",
    "CorpusSwitcherWidget",
    "CooccurrenceEdge",
    "CooccurrenceEngine",
    "CooccurrenceNetwork",
    "CleanPreviewDialog",
    "CleanRule",
    "CurvePoint",
    "CurveStepMode",
    "DEFAULT_CONTENT_POS",
    "DEGREE_WORDS",
    "DEFAULT_STOPWORDS_EN",
    "DEFAULT_STOPWORDS_ZH",
    "DependencyAnalysisWorker",
    "DependencyParse",
    "DependencyParser",
    "DependencyWidget",
    "DepToken",
    "DeviceInfo",
    "DocumentSentiment",
    "FreqAnalyzerWidget",
    "FrequencyAnalyzer",
    "HighFreqEntry",
    "KwicHit",
    "MetricCard",
    "MetricColor",
    "NEGATION_WORDS",
    "NetworkBuildParams",
    "NetworkBuildWorker",
    "NetworkWidget",
    "NewCorpusDialog",
    "NgramDialog",
    "POS_COARSE_CATEGORY",
    "POSTag",
    "ParagraphSentiment",
    "Polarity",
    "PosPreviewDialog",
    "REGISTRY_DB_PATH",
    "ResultSummary",
    "STRICT_CONTENT_POS",
    "SentenceSentiment",
    "SelectColumnDialog",
    "SentimentEngine",
    "SentimentHit",
    "SentimentWidget",
    "SentimentWorker",
    "StopwordsDialog",
    "TextCleaner",
    "TokenCache",
    "ZipfDialog",
    "WordAnalysisEngine",
    "WordAnalysisWidget",
    "WordAnalysisWorker",
    "WordMetrics",
    "availablePosBackend",
    "backendModelVersion",
    "colorForCommunity",
    "defaultStopwords",
    "getAvailableParsers",
    "getDefaultParser",
    "hashText",
    "loadDocxFile",
    "loadExcelColumn",
    "loadStopwordsFromFile",
    "loadTextFile",
    "parseStopwordsFromText",
    "posTag",
    "splitSentences",
    "toConllU",
    "posTagCategories",
    "posTagsFilter",
    "saveStopwordsToFile",
]
