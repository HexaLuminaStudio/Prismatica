"""词频分析模块的 UI 组件包

公共 API:
    - ConcordanceWidget        语境分析 (KWIC) 主面板
    - CorpusStatusCard         共享语料状态卡
    - CorpusImportWidget       语料导入与清洗面板
    - FreqAnalyzerWidget       词频分析主面板
    - ZipfDialog               Zipf 曲线图弹窗
    - NgramDialog              N-gram 频率统计弹窗
    - SelectColumnDialog       Excel 列名选择对话框
    - CleanPreviewDialog       清洗前后对比预览对话框

引擎:
    - FrequencyAnalyzer        频率分析器
    - TextCleaner              文本清洗器
    - CleanRule                清洗规则
"""

from .concordance_engine import ConcordanceEngine, KwicHit
from .concordance_widget import ConcordanceWidget, CorpusStatusCard
from .corpus_import_widget import CorpusImportWidget
from .dialogs import (
    CleanPreviewDialog,
    NgramDialog,
    SelectColumnDialog,
    ZipfDialog,
)
from .freq_analyzer_widget import FreqAnalyzerWidget
from .freq_engine import (
    DEFAULT_STOPWORDS_EN,
    DEFAULT_STOPWORDS_ZH,
    CleanRule,
    FrequencyAnalyzer,
    TextCleaner,
    loadExcelColumn,
    loadTextFile,
)

__all__ = [
    "ConcordanceEngine",
    "ConcordanceWidget",
    "CorpusImportWidget",
    "CorpusStatusCard",
    "CleanPreviewDialog",
    "CleanRule",
    "DEFAULT_STOPWORDS_EN",
    "DEFAULT_STOPWORDS_ZH",
    "FreqAnalyzerWidget",
    "FrequencyAnalyzer",
    "KwicHit",
    "NgramDialog",
    "SelectColumnDialog",
    "TextCleaner",
    "ZipfDialog",
    "loadExcelColumn",
    "loadTextFile",
]