# coding: utf-8
"""从 freq_analyzer_interface.py 拆分而来

保留全部实现，仅补充必要的 imports。
"""

import json
import traceback
from typing import Any, Dict, List, Optional

from app.core.utils.data_paths import FREQ_ANALYZER_SETTINGS_FILE
from app.core.utils import cfg, qconfig  # AI 解读配置（PRD-001 REQ-AI-001）

# AI 解读 Mixin（PRD-001 REQ-AI-001）— 全部分析子页面统一接入
from app.view.widgets.freq_analyzer.ai_insight_mixin import AiInsightMixin
from app.view.widgets.freq_analyzer.resource_sink_mixin import ResourceSinkMixin
from app.view.widgets.prismatica_theme import setThemeRole
from app.core.models.project import RESOURCE_TYPE_FREQ
from app.core.services import beginPaidAnalysisExport, stopwordService

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from app.core.utils import logger

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
    QTableWidgetItem,
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

from app.view.widgets.freq_analyzer.result_summary import (
    MetricColor,
    ResultSummary,
)

from app.view.widgets.freq_analyzer.dialogs import (
    AdvancedSettingsDialog,
    NgramDialog,
    ZipfDialog,
)
from app.view.widgets.freq_analyzer.freq_engine import CleanRule
from app.view.widgets.freq_analyzer.ui_helpers import (
    _makeAlignedItem,
    _makeSwitchButton,
    _showInfoBar,
)

# FreqWorkerThread 在主文件 freq_analyzer_interface.py 中定义，
# 为避免循环 import，在 _runAnalysis 方法内延迟导入。


class FreqAnalyzerWidget(AiInsightMixin, ResourceSinkMixin, QWidget):
    # PRD-002 资源归档配置(REQ-PROJ-001)
    _RESOURCE_TYPE = RESOURCE_TYPE_FREQ
    _RESOURCE_TITLE_PREFIX = "词频分析"
    """词频分析主界面（对标 AntConc）

    设计：
        - 语料与清洗由顶层 CorpusStore 提供，本面板只负责分析与展示
        - 接收 corpusStore（可选）以响应语料变化
        - 继承 AiInsightMixin 提供「AI 解读」抽屉能力
    """

    _AI_INSIGHT_PANEL_NAME = "词频分析"
    _AI_INSIGHT_TYPE = "freq"

    def __init__(self, parent=None, corpusStore: Optional["CorpusStore"] = None):
        super().__init__(parent)
        self.setObjectName("FreqAnalyzerInterface")

        self._corpusStore: Optional[CorpusStore] = corpusStore
        self._boundCorpusStore: Optional[CorpusStore] = None
        # rawTexts 仅作为本地只读缓存；权威数据由 CorpusStore 持有
        self.rawTexts: Dict[str, str] = {}
        self.unigramDf = None
        self.ngramDf = None
        self.ngramN = 2  # 当前 N-gram 阶数（与 AdvancedSettingsDialog 共享）
        self.unigramMinFreq: int = 1  # 主词频最低频次（弹窗中设置）
        self.ngramMinFreq: int = 2  # N-gram 最低频次（弹窗中设置）
        self._worker = None

        # 从持久化文件恢复上次的高级设置
        self._loadAdvancedSettings()

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
            self._onCorpusChanged()
            return
        self._corpusStore = store
        self._bindCorpusStore(store)
        self._onCorpusChanged()

    def _bindCorpusStore(self, store: "CorpusStore") -> None:
        if self._boundCorpusStore is store:
            return
        oldStore = self._boundCorpusStore
        if oldStore is not None:
            for signal in (oldStore.textsChanged, oldStore.cleanRuleChanged):
                try:
                    signal.disconnect(self._onCorpusChanged)
                except (RuntimeError, TypeError):
                    pass
        store.textsChanged.connect(self._onCorpusChanged)
        store.cleanRuleChanged.connect(self._onCorpusChanged)
        self._boundCorpusStore = store

    def _onCorpusChanged(self) -> None:
        # 同步本地 rawTexts 缓存
        if self._corpusStore is not None:
            self.rawTexts = dict(self._corpusStore.rawTexts)
        # 语料/清洗规则变更后清空当前分析结果（避免与新语料不匹配）
        self._resetAnalysisResults()
        if hasattr(self, "_updateFileCount"):
            self._updateFileCount()
        if hasattr(self, "_resultSummary"):
            self._resultSummary.setPlaceholder("请加载语料并点击「开始分析」")
        self._currentFileNames: List[str] = []
        # _refreshCleanSummary 已废弃：原用于更新清洗规则摘要 UI 标签，
        # 当前页面已移除该 UI 元素，对应方法不再存在。如未来重新引入
        # 清洗摘要展示，请在此处调用新方法。

    def _resetAnalysisResults(self) -> None:
        self.unigramDf = None
        self.ngramDf = None
        if hasattr(self, "table"):
            self.table.setRowCount(0)
        if hasattr(self, "zipfBtn"):
            self.zipfBtn.setEnabled(False)
        if hasattr(self, "ngramBtn"):
            self.ngramBtn.setEnabled(False)
        if hasattr(self, "clusterBtn"):
            self.clusterBtn.setEnabled(False)
        if hasattr(self, "exportBtn"):
            self.exportBtn.setEnabled(False)
        if hasattr(self, "_aiInsightBtn"):
            self._aiInsightBtn.setEnabled(False)
        if hasattr(self, "statusLabel"):
            self.statusLabel.setText("语料已变更，请重新分析")

    # ------------------------------------------------------------------
    # AI 解读协议（PRD-001 REQ-AI-001）— 由 AiInsightMixin 调用
    # ------------------------------------------------------------------
    def _aiHasResult(self) -> bool:
        return self.unigramDf is not None and not self.unigramDf.empty

    def _aiCollectExplainArgs(self):
        if not self._aiHasResult():
            return None
        # insight_prompts.buildFreqPrompt 期望 rows 为 list[dict],
        # 不能直接传 DataFrame —— 否则 "data.get("rows") or []"
        # 会在 DataFrame 上触发真值判断(ValueError: ambiguous truth value)
        try:
            rows = self.unigramDf.head(100).to_dict(orient="records")
        except Exception:
            rows = []
        return ("freq", {"rows": rows})

    def _collectCorpusMeta(self) -> Dict[str, Any]:
        """汇总语料元信息，喂给 AI 服务"""
        meta: Dict[str, Any] = {
            "corpusName": "当前语料",
            "fileCount": 0,
            "totalChars": 0,
            "tokenCount": 0,
        }
        if self._corpusStore is not None:
            try:
                fileCount = self._corpusStore.fileCount()
                totalChars = self._corpusStore.totalChars()
            except Exception:
                fileCount, totalChars = 0, 0
            meta["fileCount"] = fileCount
            meta["totalChars"] = totalChars
            # 词种数 = 当前 unigramDf 行数
            if self.unigramDf is not None:
                try:
                    meta["tokenCount"] = int(len(self.unigramDf))
                except Exception:
                    pass
            # 用 dbPath 末段作为语料名
            try:
                dbPath = self._corpusStore.dbPath
                from pathlib import Path as _Path

                meta["corpusName"] = _Path(dbPath).stem or "当前语料"
            except Exception:
                pass
        return meta

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

        # 「次要」参数（主词频最低频次 / N-gram 阶数 / N-gram 最低频次）
        # 统一收纳到「高级设置」弹窗中，避免主页参数行拥挤。
        self.advancedBtn = PushButton("高级设置…", self)
        self.advancedBtn.setIcon(FluentIcon.SETTING)
        self.advancedBtn.setFixedWidth(140)
        self.advancedBtn.clicked.connect(self._showAdvancedSettings)
        # 状态指示：显示当前生效的频次筛选摘要
        self.advancedHint = CaptionLabel(self._advancedHintText(), self)
        setThemeRole(self.advancedHint, "muted", "font-size: 11px;")

        paramRow1.addWidget(minLabel)
        paramRow1.addWidget(self.minSpin)
        paramRow1.addWidget(maxLabel)
        paramRow1.addWidget(self.maxSpin)
        paramRow1.addWidget(self.advancedBtn)
        paramRow1.addWidget(self.advancedHint)
        paramRow1.addStretch(1)
        paramLayout.addLayout(paramRow1)

        paramRow2 = QHBoxLayout()
        paramRow2.setSpacing(20)

        self.caseSwitch = _makeSwitchButton("区分大小写", self)
        self.caseSwitch.setChecked(False)

        self.numberSwitch = _makeSwitchButton("排除纯数字", self)
        self.numberSwitch.setChecked(True)

        paramRow2.addWidget(self.caseSwitch)
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

        # 阶数变化由「高级设置」弹窗触发,在 _showAdvancedSettings 中同步按钮标题
        # (ngramNSpin 已移除,不再监听 valueChanged)

        self.exportBtn = PushButton("导出 CSV", self)
        self.exportBtn.setIcon(FluentIcon.SAVE)
        self.exportBtn.clicked.connect(self._exportCsv)
        self.exportBtn.setEnabled(False)

        # AI 解读按钮（PRD-001 REQ-AI-001）— 通过 Mixin 一行接入
        # 统一为 PrimaryPushButton,放在「开始分析」旁边
        self._aiInsightBtn = PrimaryPushButton("AI 解读", self)
        self._aiInsightBtn.setIcon(FluentIcon.HEART)
        self.setupAiInsightButton(self._aiInsightBtn)

        # N-gram 聚簇分析按钮(N>=3 时可见)
        self.clusterBtn = PushButton("聚簇分析", self)
        self.clusterBtn.setIcon(FluentIcon.SEARCH)
        self.clusterBtn.clicked.connect(self._showCluster)
        self.clusterBtn.setEnabled(False)
        self.clusterBtn.setVisible(self.ngramN >= 3)

        # P0-AC3 fix 2026-07-18:占比 vs PMW 切换按钮(列 4 在两种模式间切换)
        # PMW = per million words,用于跨语料规模比较的归一化指标
        self._showPmw = False
        self.pctModeBtn = PushButton("占比 (%)", self)
        self.pctModeBtn.setIcon(FluentIcon.SEND)
        self.pctModeBtn.clicked.connect(self._togglePctMode)

        opRow.addWidget(self.analyzeBtn)
        opRow.addWidget(self._aiInsightBtn)
        opRow.addWidget(self.zipfBtn)
        opRow.addWidget(self.ngramBtn)
        opRow.addWidget(self.clusterBtn)
        opRow.addWidget(self.pctModeBtn)
        opRow.addWidget(self.exportBtn)
        opRow.addStretch(1)
        paramLayout.addLayout(opRow)

        # 状态（L3：状态文案）
        self.statusLabel = CaptionLabel("加载文件后点击「开始分析」", self)
        setThemeRole(self.statusLabel, "muted", "font-size: 11px;")
        paramLayout.addWidget(self.statusLabel)

        scrollLayout.addWidget(paramCard)

        # ===== 结果摘要卡(优化:大指标卡显示) =====
        self._resultSummary = ResultSummary(self)
        self._resultSummary.setTitle("分析结果")
        self._resultSummary.setPlaceholder("请加载语料并点击「开始分析」")
        scrollLayout.addWidget(self._resultSummary)

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
        if hasattr(self, "clusterBtn") and self.clusterBtn is not None:
            self.clusterBtn.setVisible(value >= 3)

    # ------------------------------------------------------------------
    # 高级设置弹窗（收纳主页参数行中的次要选项）
    # ------------------------------------------------------------------
    def _advancedHintText(self) -> str:
        """生成主页上参数摘要文案，让用户在不打开弹窗时也能看到当前设置。"""
        unigramPart = (
            "主词频无过滤"
            if self.unigramMinFreq <= 1
            else f"主词频 ≥ {self.unigramMinFreq}"
        )
        return f"当前：{unigramPart} · N-gram 阶数={self.ngramN} · N-gram 最低频次={self.ngramMinFreq}"

    def _showAdvancedSettings(self) -> None:
        """弹出高级设置弹窗；用户在弹窗内修改后点「确定」才同步到 widget。"""
        result = AdvancedSettingsDialog.getSettings(
            unigramMinFreq=self.unigramMinFreq,
            ngramN=self.ngramN,
            ngramMinFreq=self.ngramMinFreq,
            parent=self.window(),
        )
        if result is None:
            # 用户取消,保留原值
            return
        # 用户点「确定」,同步到 widget 实例属性
        self.unigramMinFreq = int(result["unigramMinFreq"])
        self.ngramN = int(result["ngramN"])
        self.ngramMinFreq = int(result["ngramMinFreq"])
        # 持久化到磁盘,重启后自动恢复
        self._saveAdvancedSettings()
        # 同步按钮标题与摘要文案
        self.ngramBtn.setText(self._ngramButtonText(self.ngramN))
        if hasattr(self, "clusterBtn") and self.clusterBtn is not None:
            self.clusterBtn.setVisible(self.ngramN >= 3)
            # 若已有 ngramDf 数据且 N>=3，启用聚簇分析按钮
            if self.ngramN >= 3 and self.ngramDf is not None and not self.ngramDf.empty:
                self.clusterBtn.setEnabled(True)
        if hasattr(self, "advancedHint") and self.advancedHint is not None:
            self.advancedHint.setText(self._advancedHintText())
        _showInfoBar(
            "success",
            "设置已更新",
            "高级参数已生效；下次点击「开始分析」时使用最新值",
            self,
            duration=1800,
        )

    # ------------------------------------------------------------------
    # 高级设置持久化（重启后恢复上次的设置值）
    # ------------------------------------------------------------------
    def _loadAdvancedSettings(self) -> None:
        """从磁盘恢复上次保存的高级设置;文件不存在或损坏时静默跳过。"""
        try:
            if not FREQ_ANALYZER_SETTINGS_FILE.exists():
                return
            data = json.loads(FREQ_ANALYZER_SETTINGS_FILE.read_text(encoding="utf-8"))
            self.unigramMinFreq = int(data.get("unigramMinFreq", self.unigramMinFreq))
            self.ngramN = int(data.get("ngramN", self.ngramN))
            self.ngramMinFreq = int(data.get("ngramMinFreq", self.ngramMinFreq))
        except Exception as e:
            logger.warning(f"[FreqAnalyzerWidget] 读取高级设置失败: {e}")

    def _saveAdvancedSettings(self) -> None:
        """将当前高级设置写入磁盘。"""
        try:
            FREQ_ANALYZER_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "unigramMinFreq": self.unigramMinFreq,
                "ngramN": self.ngramN,
                "ngramMinFreq": self.ngramMinFreq,
            }
            FREQ_ANALYZER_SETTINGS_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"[FreqAnalyzerWidget] 已保存高级设置: {payload}")
        except Exception as e:
            logger.error(f"[FreqAnalyzerWidget] 保存高级设置失败: {e}")

    def _updateFileCount(self):
        # 已移除 CorpusStatusCard,此方法保留为空以避免外部调用崩溃
        pass

    def _runAnalysis(self):
        # 每次点击都从权威 CorpusStore 获取当前文本。这样即使面板在
        # CorpusStore 恢复完成后才创建，也不会依赖一次可能错过的 Qt 信号。
        effective = self.effectiveTexts
        if not effective:
            _showInfoBar("warning", "提示", "请先加载语料文件", self, duration=2000)
            return

        if self._worker and self._worker.isRunning():
            return

        # ngramN / unigramMinFreq / ngramMinFreq 已在 __init__ 初始化默认值,
        # 之后由「高级设置」弹窗通过 _showAdvancedSettings 修改。
        # 清洗规则来自 CorpusImportWidget，已通过 CorpusStore 共享给 KWIC
        if self._corpusStore is not None:
            rule = self._corpusStore.cleanRule
            cleanEnabled = self._corpusStore.cleanEnabled
        else:
            rule = CleanRule()
            cleanEnabled = False
        logger.info(
            f"[FreqAnalyzerWidget] 开始分析,文件数={len(effective)}, "
            f"N={self.ngramN}, cleanEnabled={cleanEnabled}"
        )
        self.analyzeBtn.setEnabled(False)
        self.statusLabel.setText("正在分析...")

        # 延迟 import：避免与主文件 freq_analyzer_interface 循环依赖
        from app.view.freq_analyzer_interface import FreqWorkerThread

        stopwordEnabled = stopwordService.isEnabled()
        stopwords = set(stopwordService.words())
        self._worker = FreqWorkerThread(
            effective,
            minLength=self.minSpin.value(),
            maxLength=self.maxSpin.value(),
            caseSensitive=self.caseSwitch.isChecked(),
            excludeNumbers=self.numberSwitch.isChecked(),
            useStopwords=stopwordEnabled,
            useJieba=True,
            ngramN=self.ngramN,
            ngramMinFreq=self.ngramMinFreq,
            cleanRule=rule,
            unigramMinFreq=self.unigramMinFreq,
            stopwords=stopwords,
            posTags=(
                self._corpusStore.posTags
                if (self._corpusStore is not None and self._corpusStore.posEnabled)
                else None
            ),
            posEnabled=(self._corpusStore is not None and self._corpusStore.posEnabled),
            tokenCache=(
                self._corpusStore.tokenCache()
                if self._corpusStore is not None
                else None
            ),
        )
        self._worker.progress.connect(self._onProgress)
        self._worker.finished.connect(self._onFinished)
        self._worker.failed.connect(self._onFailed)
        # 记录开始时间,用于计算耗时
        import time as _time

        self._analysisStartedAt = _time.time()
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

        # 计算耗时(若未记录开始时间则默认 0)
        import time as _time

        startedAt = getattr(self, "_analysisStartedAt", None)
        elapsed = _time.time() - startedAt if startedAt is not None else 0.0

        # 同步按钮标题为本次分析使用的阶数
        self.ngramBtn.setText(self._ngramButtonText(self.ngramN))

        # 过滤最低频次（仅对 unigram 表生效,P2-1 修复:应用用户配置值）
        # 之前硬编码 minFreq = 2 会覆盖用户在「高级设置」中设置的 unigramMinFreq
        minFreq = max(1, int(getattr(self, "unigramMinFreq", 1) or 1))
        if unigramDf is not None and not unigramDf.empty and minFreq > 1:
            unigramDf = unigramDf[unigramDf["Freq"] >= minFreq].reset_index(drop=True)
            unigramDf["Rank"] = unigramDf.index + 1

        self._populateTable(unigramDf)
        self.zipfBtn.setEnabled(unigramDf is not None and not unigramDf.empty)
        self.ngramBtn.setEnabled(ngramDf is not None and not ngramDf.empty)
        if hasattr(self, "clusterBtn") and self.clusterBtn is not None:
            clusterOk = ngramDf is not None and not ngramDf.empty and self.ngramN >= 3
            self.clusterBtn.setEnabled(clusterOk)
        self.exportBtn.setEnabled(unigramDf is not None and not unigramDf.empty)
        # AI 解读入口：仅在主词频表有结果时启用
        if hasattr(self, "_aiInsightBtn"):
            self._aiInsightBtn.setEnabled(unigramDf is not None and not unigramDf.empty)

        nTypes = len(unigramDf) if unigramDf is not None else 0
        totalTokens = int(unigramDf["Freq"].sum()) if nTypes > 0 else 0
        ngramCount = len(ngramDf) if ngramDf is not None else 0
        ngramLabel = "Bigram" if self.ngramN == 2 else f"{self.ngramN}-gram"
        self.statusLabel.setText(
            f"分析完成：{nTypes} 个不同词，{totalTokens:,} token；{ngramCount} 个 {ngramLabel}"
        )

        # ===== 更新结果摘要 =====
        top1Word = "—"
        top1Freq = 0
        if unigramDf is not None and not unigramDf.empty:
            topRow = unigramDf.iloc[0]
            top1Word = str(topRow["Word"])
            top1Freq = int(topRow["Freq"])

        self._resultSummary.clear()
        self._resultSummary.setMetrics(
            [
                ("词种数", f"{nTypes:,}", MetricColor.PRIMARY),
                ("Token 总数", f"{totalTokens:,}", MetricColor.SUCCESS),
                (f"{ngramLabel} 数", f"{ngramCount:,}", MetricColor.ACCENT),
                ("Top 词", top1Word, MetricColor.NEUTRAL),
            ]
        )
        self._resultSummary.setDetail(
            f"📊 分析完成 &nbsp;|&nbsp; "
            f"最高频词 <b>{top1Word}</b> 出现 <b>{top1Freq:,}</b> 次 &nbsp;|&nbsp; "
            f"耗时 <b>{elapsed:.2f}s</b>"
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

        # PRD-002:归档到当前激活项目(若有)
        self.notifyResourceCreated()

    # ------------------------------------------------------------------
    # PRD-002 资源归档钩子(ResourceSinkMixin)
    # ------------------------------------------------------------------
    def _collectResourcePayload(self):
        """词频分析结果 → 项目资源 payload"""
        unigramDf = getattr(self, "unigramDf", None)
        ngramDf = getattr(self, "ngramDf", None)
        if unigramDf is None or len(unigramDf) == 0:
            return None
        # 摘要:Top-10 高频词 + token 总数
        try:
            topRows = unigramDf.head(10)
            topList = [f"{r['Word']}({int(r['Freq'])})" for _, r in topRows.iterrows()]
            topText = "、".join(topList)
            totalTokens = int(unigramDf["Freq"].sum())
            ngramCount = len(ngramDf) if ngramDf is not None else 0
            ngramLabel = "Bigram" if self.ngramN == 2 else f"{self.ngramN}-gram"
            summary = (
                f"{len(unigramDf)} 个不同词,{totalTokens:,} tokens,"
                f"{ngramCount} 个 {ngramLabel}。"
                f"Top 10:{topText}"
            )
        except Exception:
            summary = f"{len(unigramDf)} 个不同词"
        # 快照:仅保留 Top-200 + 总数(避免大表)
        try:
            topForSnapshot = unigramDf.head(200).to_dict(orient="records")
        except Exception:
            topForSnapshot = []
        snapshotData = {
            "unigramTop": topForSnapshot,
            "ngramCount": len(ngramDf) if ngramDf is not None else 0,
        }
        parameters = {
            "minLength": self.minSpin.value(),
            "maxLength": self.maxSpin.value(),
            "caseSensitive": self.caseSwitch.isChecked(),
            "useStopwords": stopwordService.isEnabled(),
            # useJieba 在 _runAnalysis 里固定传 True(暂未暴露开关),这里同步写死
            "useJieba": True,
            "ngramN": self.ngramN,
            "unigramMinFreq": self.unigramMinFreq,
            "excludeNumbers": self.numberSwitch.isChecked(),
        }
        title = f"词频分析 ({self._buildDefaultTitle().split(' ', 1)[1]})"
        return {
            "title": title,
            "summary": summary[:200],
            "parameters": parameters,
            "snapshotData": snapshotData,
        }

    def _populateTable(self, df):
        self.table.setRowCount(len(df))
        # P0-AC3 fix 2026-07-18:根据当前显示模式渲染列 4
        # _showPmw=False → 显示「占比 %」;True → 显示「PMW」
        showPmw = bool(getattr(self, "_showPmw", False))
        # 同步表头(只在标题文字变化时调用,避免触发 layout 抖动)
        targetHeader = "PMW" if showPmw else "占比"
        currentHeader = self.table.horizontalHeaderItem(4).text()
        if currentHeader != targetHeader:
            self.table.setHorizontalHeaderItem(4, QTableWidgetItem(targetHeader))
        for i in range(len(df)):
            row = df.iloc[i]
            self.table.setItem(i, 0, _makeAlignedItem(str(int(row["Rank"]))))
            self.table.setItem(i, 1, QTableWidgetItem(str(row["Word"])))
            self.table.setItem(i, 2, _makeAlignedItem(str(int(row["Freq"]))))
            self.table.setItem(
                i, 3, _makeAlignedItem(f"{int(row['Range'])}/{len(self.rawTexts)}")
            )
            # 列 4:占比 % 或 PMW(整数 PMW 用千分位分隔;小数用 .2f)
            if showPmw:
                pmwVal = float(row.get("Pmw", 0.0))
                self.table.setItem(i, 4, _makeAlignedItem(f"{pmwVal:,.1f}"))
            else:
                self.table.setItem(i, 4, _makeAlignedItem(f"{row['Pct']:.2f}%"))
            self.table.setItem(i, 5, _makeAlignedItem(f"{row['Freq'] * row['Rank']:,}"))

    def _togglePctMode(self):
        """切换列 4 显示:占比(%) ↔ PMW(每百万词频次)

        P0-AC3 fix 2026-07-18:提供 UI 切换,不重新分析,只是重渲染表
        """
        self._showPmw = not bool(getattr(self, "_showPmw", False))
        if self._showPmw:
            self.pctModeBtn.setText("PMW")
        else:
            self.pctModeBtn.setText("占比 (%)")
        # 重新填充表格(复用现有数据,不重新跑分析)
        if getattr(self, "unigramDf", None) is not None:
            self._populateTable(self.unigramDf)

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

    def _showCluster(self):
        """打开 N-gram 聚簇分析弹窗（后台线程执行，不阻塞 UI）"""
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
        from app.view.widgets.freq_analyzer.ngram_cluster_widget import (
            NgramClusterDialog,
        )

        NgramClusterDialog.show(
            ngramDf=self.ngramDf,
            n=self.ngramN,
            parent=self.window(),
        )

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
        transaction = beginPaidAnalysisExport(self.window(), "词频表 CSV")
        if transaction is None:
            return
        try:
            self.unigramDf.to_csv(path, index=False, encoding="utf-8-sig")
            if not transaction.commit():
                raise RuntimeError("导出文件已生成，但计费结算失败；本次费用已释放")
            _showInfoBar("success", "导出成功", f"已保存：{path}", self)
        except Exception as e:
            transaction.refund()
            logger.error(f"[FreqAnalyzerWidget] CSV 导出失败: {e}")
            _showInfoBar("error", "导出失败", str(e), self, duration=3000)


# ===========================================================================
