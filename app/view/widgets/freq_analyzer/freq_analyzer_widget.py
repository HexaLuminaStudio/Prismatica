# coding: utf-8
"""从 freq_analyzer_interface.py 拆分而来

保留全部实现，仅补充必要的 imports。
"""

import logging
import traceback
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import jieba  # type: ignore

    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

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
    FluentIcon,
    PushButton,
    ScrollArea,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
    PrimaryPushButton,
    TransparentPushButton,
)
from qfluentwidgetspro import RoundTableWidget as ProRoundTableWidget

from app.view.widgets.freq_analyzer.concordance_widget import CorpusStatusCard
from app.view.widgets.freq_analyzer.dialogs import NgramDialog, ZipfDialog
from app.view.widgets.freq_analyzer.freq_engine import CleanRule
from app.view.widgets.freq_analyzer.ui_helpers import (
    _makeAlignedItem,
    _makeSwitchButton,
    _showInfoBar,
)

# FreqWorkerThread 在主文件 freq_analyzer_interface.py 中定义，
# 为避免循环 import，在 _runAnalysis 方法内延迟导入。


class FreqAnalyzerWidget(QWidget):
    """词频分析主界面（对标 AntConc）

    设计：
        - 语料与清洗由顶层 CorpusStore 提供，本面板只负责分析与展示
        - 接收 corpusStore（可选）以响应语料变化
    """

    def __init__(self, parent=None, corpusStore: Optional["CorpusStore"] = None):
        super().__init__(parent)
        self.setObjectName("FreqAnalyzerInterface")

        self._corpusStore: Optional[CorpusStore] = corpusStore
        # rawTexts 仅作为本地只读缓存；权威数据由 CorpusStore 持有
        self.rawTexts: Dict[str, str] = {}
        self.unigramDf = None
        self.ngramDf = None
        self.ngramN = 2  # 当前 N-gram 阶数
        self._worker = None

        self._initUi()

        # 监听语料变化（store 可能后绑定）
        if self._corpusStore is not None:
            self._bindCorpusStore(self._corpusStore)

    @property
    def effectiveTexts(self) -> Dict[str, str]:
        """根据 store 的清洗开关与规则派生最终文本（同时供词频与 KWIC 使用）"""
        if self._corpusStore is None:
            return dict(self.rawTexts)
        return self._corpusStore.effectiveTexts()

    # ------------------------------------------------------------------
    # 语料状态绑定
    # ------------------------------------------------------------------
    def setCorpusStore(self, store: "CorpusStore") -> None:
        """运行时注入 CorpusStore（用于面板已构造后再绑定的场景）"""
        if self._corpusStore is store:
            return
        self._corpusStore = store
        self._bindCorpusStore(store)
        # 把 store 同步给语料状态卡
        if hasattr(self, "_corpusStatusCard") and self._corpusStatusCard is not None:
            self._corpusStatusCard.setStore(store)
        self._onCorpusChanged()

    def _bindCorpusStore(self, store: "CorpusStore") -> None:
        store.textsChanged.connect(self._onCorpusChanged)
        store.cleanRuleChanged.connect(self._onCorpusChanged)

    def _onCorpusChanged(self) -> None:
        # 同步本地 rawTexts 缓存
        if self._corpusStore is not None:
            self.rawTexts = dict(self._corpusStore.rawTexts)
        # 语料/清洗规则变更后清空当前分析结果（避免与新语料不匹配）
        self._resetAnalysisResults()
        self._updateFileCount()
        self._refreshCleanSummary()

    def _resetAnalysisResults(self) -> None:
        self.unigramDf = None
        self.ngramDf = None
        if hasattr(self, "table"):
            self.table.setRowCount(0)
        if hasattr(self, "zipfBtn"):
            self.zipfBtn.setEnabled(False)
        if hasattr(self, "ngramBtn"):
            self.ngramBtn.setEnabled(False)
        if hasattr(self, "exportBtn"):
            self.exportBtn.setEnabled(False)
        if hasattr(self, "statusLabel"):
            self.statusLabel.setText("语料已变更，请重新分析")
        self._currentFileNames: List[str] = []

    def _initUi(self):
        # 外层布局：与其他两个面板保持一致（边距 20px、间距 12px）
        outerLayout = QVBoxLayout(self)
        outerLayout.setContentsMargins(20, 20, 20, 20)
        outerLayout.setSpacing(12)

        # 滚动区域
        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setStyleSheet("border: none; background: transparent;")
        outerLayout.addWidget(self.scrollArea, 1)

        scrollContent = QWidget()
        scrollLayout = QVBoxLayout(scrollContent)
        # scrollContent 的内部边距 0，避免与 outerLayout 重复（与 CorpusImportWidget/ConcordanceWidget 一致）
        scrollLayout.setContentsMargins(0, 0, 0, 0)
        scrollLayout.setSpacing(12)

        # 标题（L0：SubtitleLabel）
        titleLabel = SubtitleLabel("词频分析统计", scrollContent)
        scrollLayout.addWidget(titleLabel)

        # ===== 语料来源提示（导入/清空 已迁移到 CorpusImportWidget） =====
        infoCard = CorpusStatusCard(self, corpusStore=self._corpusStore)
        self._corpusStatusCard = infoCard  # 保留引用，setCorpusStore 时调用 setStore

        # 参数设置
        paramCard = CardWidget(self)
        paramLayout = QVBoxLayout(paramCard)
        paramLayout.setContentsMargins(16, 12, 16, 12)
        paramLayout.setSpacing(8)

        paramTitle = StrongBodyLabel("分析参数", self)
        paramLayout.addWidget(paramTitle)

        paramRow1 = QHBoxLayout()
        paramRow1.setSpacing(20)

        minLabel = BodyLabel("最短词长:", self)
        self.minSpin = SpinBox(self)
        self.minSpin.setRange(1, 20)
        self.minSpin.setValue(1)
        # self.minSpin.setFixedWidth(70)

        maxLabel = BodyLabel("最长词长:", self)
        self.maxSpin = SpinBox(self)
        self.maxSpin.setRange(1, 100)
        self.maxSpin.setValue(50)
        # self.maxSpin.setFixedWidth(70)

        ngramNLabel = BodyLabel("N-gram 阶数:", self)
        self.ngramNSpin = SpinBox(self)
        self.ngramNSpin.setRange(2, 5)
        self.ngramNSpin.setValue(2)

        ngramMinFreqLabel = BodyLabel("N-gram 最低频次:", self)
        self.ngramMinFreqSpin = SpinBox(self)
        self.ngramMinFreqSpin.setRange(1, 1000)
        self.ngramMinFreqSpin.setValue(2)

        paramRow1.addWidget(minLabel)
        paramRow1.addWidget(self.minSpin)
        paramRow1.addWidget(maxLabel)
        paramRow1.addWidget(self.maxSpin)
        paramRow1.addWidget(ngramNLabel)
        paramRow1.addWidget(self.ngramNSpin)
        paramRow1.addWidget(ngramMinFreqLabel)
        paramRow1.addWidget(self.ngramMinFreqSpin)
        paramRow1.addStretch(1)
        paramLayout.addLayout(paramRow1)

        paramRow2 = QHBoxLayout()
        paramRow2.setSpacing(20)

        self.caseSwitch = _makeSwitchButton("区分大小写", self)
        self.caseSwitch.setChecked(False)

        self.jiebaSwitch = _makeSwitchButton(f"中文 jieba 分词", self)
        self.jiebaSwitch.setChecked(JIEBA_AVAILABLE)
        if not JIEBA_AVAILABLE:
            self.jiebaSwitch.setEnabled(False)

        self.stopSwitch = _makeSwitchButton("过滤停用词", self)
        self.stopSwitch.setChecked(False)

        self.numberSwitch = _makeSwitchButton("排除纯数字", self)
        self.numberSwitch.setChecked(True)

        paramRow2.addWidget(self.caseSwitch)
        paramRow2.addWidget(self.jiebaSwitch)
        paramRow2.addWidget(self.stopSwitch)
        paramRow2.addWidget(self.numberSwitch)
        paramRow2.addStretch(1)
        paramLayout.addLayout(paramRow2)

        # 操作按钮
        opRow = QHBoxLayout()
        self.analyzeBtn = PrimaryPushButton("开始分析", self)
        self.analyzeBtn.setIcon(FluentIcon.PLAY)
        self.analyzeBtn.clicked.connect(self._runAnalysis)

        self.zipfBtn = PushButton("Zipf 曲线图", self)
        self.zipfBtn.setIcon(FluentIcon.CHAT)
        self.zipfBtn.clicked.connect(self._showZipf)
        self.zipfBtn.setEnabled(False)

        self.ngramBtn = PushButton(self._ngramButtonText(2), self)
        self.ngramBtn.setIcon(FluentIcon.SCROLL)
        self.ngramBtn.clicked.connect(self._showNgram)
        self.ngramBtn.setEnabled(False)

        # 阶数变化时同步按钮标题，让用户看到当前会分析的 N-gram 类型
        self.ngramNSpin.valueChanged.connect(self._onNgramNChanged)

        self.exportBtn = PushButton("导出 CSV", self)
        self.exportBtn.setIcon(FluentIcon.SAVE)
        self.exportBtn.clicked.connect(self._exportCsv)
        self.exportBtn.setEnabled(False)

        opRow.addWidget(self.analyzeBtn)
        opRow.addWidget(self.zipfBtn)
        opRow.addWidget(self.ngramBtn)
        opRow.addWidget(self.exportBtn)
        opRow.addStretch(1)
        paramLayout.addLayout(opRow)

        # 状态（L3：状态文案）
        self.statusLabel = CaptionLabel("加载文件后点击「开始分析」", self)
        self.statusLabel.setStyleSheet("color: #666; font-size: 11px;")
        paramLayout.addWidget(self.statusLabel)

        scrollLayout.addWidget(infoCard)
        scrollLayout.addWidget(paramCard)

        # ===== 词频表 =====
        tableCard = CardWidget(self)
        tableLayout = QVBoxLayout(tableCard)
        tableLayout.setContentsMargins(16, 12, 16, 12)
        tableLayout.setSpacing(8)

        tableTitle = StrongBodyLabel("词频统计表", self)
        tableLayout.addWidget(tableTitle)

        self.table = ProRoundTableWidget(self)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["排名", "词", "频次", "范围", "占比", "Zipf参考"]
        )
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(
            ProRoundTableWidget.SelectionBehavior.SelectRows
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(ProRoundTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)

        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for col, w in [(0, 60), (2, 80), (3, 70), (4, 80), (5, 90)]:
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Fixed
            )
            self.table.setColumnWidth(col, w)

        tableLayout.addWidget(self.table, 1)
        scrollLayout.addWidget(tableCard, 1)

        self.scrollArea.setWidget(scrollContent)

    @staticmethod
    def _ngramButtonText(n: int) -> str:
        """根据阶数生成 N-gram 按钮标题（Bigram 统计 / Trigram 统计 / N-gram 统计）"""
        if n == 2:
            return "Bigram 统计"
        if n == 3:
            return "Trigram 统计"
        return f"{n}-gram 统计"

    def _onNgramNChanged(self, value: int):
        """阶数变化时即时更新按钮标题（用户感知当前会分析的 N）"""
        self.ngramBtn.setText(self._ngramButtonText(value))

    def _updateFileCount(self):
        # 委托给 CorpusStatusCard；_refresh 会从 store 重新读取最新数据
        if hasattr(self, "_corpusStatusCard") and self._corpusStatusCard is not None:
            self._corpusStatusCard._refresh()

    def _runAnalysis(self):
        if not self.rawTexts:
            _showInfoBar("warning", "提示", "请先加载语料文件", self, duration=2000)
            return

        if self._worker and self._worker.isRunning():
            return

        self.ngramN = self.ngramNSpin.value()
        # 清洗规则来自 CorpusImportWidget，已通过 CorpusStore 共享给 KWIC
        if self._corpusStore is not None:
            rule = self._corpusStore.cleanRule
            cleanEnabled = self._corpusStore.cleanEnabled
        else:
            rule = CleanRule()
            cleanEnabled = False
        effective = self.effectiveTexts
        logger.info(
            f"[_runAnalysis] 开始分析，文件数={len(effective)}, N={self.ngramN}, "
            f"cleanEnabled={cleanEnabled}"
        )
        self.analyzeBtn.setEnabled(False)
        self.statusLabel.setText("正在分析...")

        # 延迟 import：避免与主文件 freq_analyzer_interface 循环依赖
        from app.view.freq_analyzer_interface import FreqWorkerThread

        self._worker = FreqWorkerThread(
            effective,
            minLength=self.minSpin.value(),
            maxLength=self.maxSpin.value(),
            caseSensitive=self.caseSwitch.isChecked(),
            excludeNumbers=self.numberSwitch.isChecked(),
            useStopwords=self.stopSwitch.isChecked(),
            useJieba=self.jiebaSwitch.isChecked(),
            ngramN=self.ngramN,
            ngramMinFreq=self.ngramMinFreqSpin.value(),
            cleanRule=rule,
        )
        self._worker.progress.connect(self._onProgress)
        self._worker.finished.connect(self._onFinished)
        self._worker.failed.connect(self._onFailed)
        self._worker.start()

    def _onProgress(self, pct: int, status: str):
        self.statusLabel.setText(f"[{pct}%] {status}")

    def _onFailed(self, err: str):
        self.analyzeBtn.setEnabled(True)
        self.statusLabel.setText(f"分析失败: {err}")
        _showInfoBar("error", "分析失败", err, self, duration=3000)

    def _onFinished(self, unigramDf, ngramDf):
        self.analyzeBtn.setEnabled(True)
        self.unigramDf = unigramDf
        self.ngramDf = ngramDf

        # 同步按钮标题为本次分析使用的阶数
        self.ngramBtn.setText(self._ngramButtonText(self.ngramN))

        # 过滤最低频次（仅对 unigram 表生效）
        minFreq = 2
        if unigramDf is not None and not unigramDf.empty and minFreq > 1:
            unigramDf = unigramDf[unigramDf["Freq"] >= minFreq].reset_index(drop=True)
            unigramDf["Rank"] = unigramDf.index + 1

        self._populateTable(unigramDf)
        self.zipfBtn.setEnabled(unigramDf is not None and not unigramDf.empty)
        self.ngramBtn.setEnabled(ngramDf is not None and not ngramDf.empty)
        self.exportBtn.setEnabled(unigramDf is not None and not unigramDf.empty)

        nTypes = len(unigramDf) if unigramDf is not None else 0
        totalTokens = int(unigramDf["Freq"].sum()) if nTypes > 0 else 0
        ngramCount = len(ngramDf) if ngramDf is not None else 0
        ngramLabel = "Bigram" if self.ngramN == 2 else f"{self.ngramN}-gram"
        self.statusLabel.setText(
            f"分析完成：{nTypes} 个不同词，{totalTokens:,} token；{ngramCount} 个 {ngramLabel}"
        )
        logger.info(
            f"[FreqAnalyzerWidget] 分析完成：{nTypes} 个不同词，{totalTokens:,} tokens, "
            f"{ngramCount} 个 {ngramLabel}"
        )
        _showInfoBar(
            "success",
            "分析完成",
            f"共 {nTypes} 个不同词，{ngramCount} 个 {ngramLabel}",
            self,
            duration=2000,
        )

    def _populateTable(self, df):
        self.table.setRowCount(len(df))
        for i in range(len(df)):
            row = df.iloc[i]
            self.table.setItem(i, 0, _makeAlignedItem(str(int(row["Rank"]))))
            self.table.setItem(i, 1, QTableWidgetItem(str(row["Word"])))
            self.table.setItem(i, 2, _makeAlignedItem(str(int(row["Freq"]))))
            self.table.setItem(
                i, 3, _makeAlignedItem(f"{int(row['Range'])}/{len(self.rawTexts)}")
            )
            self.table.setItem(i, 4, _makeAlignedItem(f"{row['Pct']:.2f}%"))
            self.table.setItem(i, 5, _makeAlignedItem(f"{row['Freq'] * row['Rank']:,}"))

    def _showZipf(self):
        if self.unigramDf is None or self.unigramDf.empty:
            return
        dialog = ZipfDialog(self.unigramDf, self.window())
        dialog.exec()

    def _showNgram(self):
        if self.ngramDf is None or self.ngramDf.empty:
            ngramLabel = "Bigram" if self.ngramN == 2 else f"{self.ngramN}-gram"
            _showInfoBar(
                "warning",
                "提示",
                f"无 {ngramLabel} 数据，请调低「N-gram 最低频次」后重试",
                self,
                duration=2000,
            )
            return
        dialog = NgramDialog(self.ngramDf, self.ngramN, self.window())
        dialog.exec()

    def _exportCsv(self):
        if self.unigramDf is None or self.unigramDf.empty:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出词频表", "wordlist.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        if not path.endswith(".csv"):
            path += ".csv"
        try:
            self.unigramDf.to_csv(path, index=False, encoding="utf-8-sig")
            _showInfoBar("success", "导出成功", f"已保存：{path}", self)
        except Exception as e:
            logger.error(f"[_exportCsv] 导出失败: {e}")
            _showInfoBar("error", "导出失败", str(e), self, duration=3000)


# ===========================================================================
