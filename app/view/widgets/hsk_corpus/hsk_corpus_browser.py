# coding:utf-8
"""
HSK 语料检索主面板(现代化简洁 UI · 多条件组合检索)
====================================================

布局:
    ┌──────────────────────────────────────────────────┐
    │  📖 HSK 作文语料检索                               │
    │  多条件组合检索 · 独立 SQLite + 全 NOCASE 索引     │
    ├──────────────────────────────────────────────────┤
    │  筛选条件列表(可动态添加/删除)                      │
    │  ┌──────────────────────────────────────────────┐│
    │  │ [列][输入区(动态)][删除]              [+]    ││
    │  │ [列][输入区(动态)][删除]                     ││
    │  │ ...                                          ││
    │  └──────────────────────────────────────────────┘│
    │                  [搜索]                           │
    ├──────────────────────────────────────────────────┤
    │  状态条:命中 N 条 · 耗时 X ms                     │
    │  ┌────────────────────────────────────────────┐  │
    │  │ TableView                                  │  │
    │  └────────────────────────────────────────────┘  │
    └──────────────────────────────────────────────────┘

每行筛选条件:
    - 国籍        → ComboBox(从 constant.hskCountryDict 取)
    - 证书级别    → ComboBox(A / B / C / 无)
    - 作文题目    → ComboBox(从 constant.hskEssayList 取)
    - 分数列(5 列) → SpinBox 区间输入(无下界/无上界勾选)

条件之间为 AND 关系(全部满足才命中)。

TableView 拉伸修复策略:
    - QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
    - 表格卡片 sizePolicy = Expanding/Expanding
    - 固定表格显示上限 20 条,杜绝 sizeHint 反向布局

线程模型:
    - 后台 Worker 子线程累积 rows
    - 主线程 QTimer(60ms)节流拉取 snapshot → setAllRows
    - 期间完全静默 Model,只在替换时 emit modelReset
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractItemView,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QMenu,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    CompactSpinBox,
    FluentIcon,
    IconWidget,
    InfoBar,
    InfoBarIcon,
    InfoBarPosition,
    isDarkTheme,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    qconfig,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    ToolButton,
    TableView,
)

from app.core.utils import logger

from app.core.services.hsk_corpus_service import HskCorpusService
from app.view.widgets.prismatica_theme import pageBackgroundColor
from app.core.services.hsk_local_corpus_service import hskLocalCorpusService
from app.core.utils.data_paths import HSK_CORPUS_DB
from app.core.utils.constant import hskCountryDict, hskEssayList
from app.view.widgets.freq_analyzer.worker_utils import WorkerMixin
from app.view.widgets.hsk_corpus.hsk_corpus_model import HskCorpusModel
from app.view.widgets.hsk_corpus.hsk_corpus_detail_drawer import (
    HskCorpusDetailDrawer,
)
from app.view.widgets.hsk_corpus.hsk_corpus_search_worker import (
    HskCorpusSearchWorker,
)
from app.view.widgets.resource_verification_dialog import ResourceVerificationDialog


# 主线程拉取 worker snapshot 的节流间隔(60ms ≈ 16fps)
_UI_PULL_INTERVAL_MS = 60

# 表格单次最多向 UI 渲染的行数(后台仍累计全量,但 UI 只显示前 N 条)
_DISPLAY_LIMIT: int = 20

# 高频阅读列优先显示；其余真实字段可通过「列设置」随时打开。
_DEFAULT_RESULT_COLUMNS = {
    "作文题目",
    "国籍",
    "证书级别",
    "作文分数",
    "总字数",
}

_INTERNAL_RESULT_COLUMNS = {"imported_at"}

# ------------------------------------------------------------------
# 绑定的 HSK 语料库文件路径(内部实现细节,不向用户暴露)
# ------------------------------------------------------------------
# 本页面的所有检索/统计都围绕这一个文件展开。
# 注意:此路径不应在 UI 文本中展示给用户。
# 使用 data_paths 统一路径(<INSTALL_DIR>/datas/corpora/hsk_corpus.db),禁止硬编码。
BOUND_HSK_DB_PATH = HSK_CORPUS_DB


# ======================================================================
# 单条筛选条件行(列选择 + 动态输入区 + 删除按钮)
# ======================================================================
class _ConditionRow(QWidget):
    """单条筛选条件行。

    内部根据当前列类型显示不同的输入区(关键词 / 作文题目 / 国籍 / 证书级别 / 分数区间)。
    切列时,旧输入区被销毁,新输入区被创建。

    Signals:
        removed():   用户点击删除按钮 → 主控件移除本行
        changed():   列切换 / 输入变化时发出(用于父级响应)
    """

    removed = Signal()
    changed = Signal()

    # ----- 列类型枚举 -----
    COL_TYPE_TEXT = "text"
    COL_TYPE_ESSAY = "essay"
    COL_TYPE_COUNTRY = "country"
    COL_TYPE_CERT = "cert"
    COL_TYPE_SCORE = "score"

    def __init__(self, availableColumns: List[str], parent=None) -> None:
        super().__init__(parent)
        self._availableColumns = availableColumns
        # 默认列 = "国籍",对应 ComboBox(避免新建行变成 LineEdit)
        self._currentColumn: str = (
            "国籍"
            if "国籍" in availableColumns
            else (availableColumns[0] if availableColumns else "")
        )
        self._currentType: str = self._getColumnType(self._currentColumn)

        # 控件引用(动态创建/销毁)
        self.columnCombo: Optional[ComboBox] = None
        self._inputContainer: Optional[QWidget] = None  # 包装容器(动态换内容)
        # 各输入控件引用(每次切列只重建当前 type)
        self.keywordEdit: Optional[LineEdit] = None
        # 国籍已改为 LineEdit,与 keywordEdit 共用同一控件引用
        self.countryNames: List[str] = []
        self.essayCombo: Optional[ComboBox] = None
        self.certCombo: Optional[ComboBox] = None
        self.scoreMinBox: Optional[CompactSpinBox] = None
        self.scoreMaxBox: Optional[CompactSpinBox] = None
        self.scoreNoMinCheck: Optional[CheckBox] = None
        self.scoreNoMaxCheck: Optional[CheckBox] = None

        self._initUi()

    # ------------------------------------------------------------------
    # UI 构造
    # ------------------------------------------------------------------
    def _initUi(self) -> None:
        self.setObjectName("hskConditionRow")
        self.setAccessibleName("HSK 作文筛选条件")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 12)
        layout.setSpacing(8)

        selectorRow = QHBoxLayout()
        selectorRow.setContentsMargins(0, 0, 0, 0)
        selectorRow.setSpacing(8)

        # 检索列下拉
        self.columnCombo = ComboBox(self)
        self.columnCombo.setMinimumHeight(34)
        self.columnCombo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.columnCombo.setAccessibleName("筛选字段")
        self.columnCombo.setAccessibleDescription("选择要检索的语料字段")
        for col in self._availableColumns:
            self.columnCombo.addItem(col, userData=col)
        # 显式设默认列为"国籍"(创建 / 重建行时,保证输入区是 ComboBox)
        if self._currentColumn:
            for i in range(self.columnCombo.count()):
                if self.columnCombo.itemData(i) == self._currentColumn:
                    self.columnCombo.setCurrentIndex(i)
                    break
        self.columnCombo.currentIndexChanged.connect(self._onColumnChanged)
        selectorRow.addWidget(self.columnCombo, 1)

        # 删除按钮
        self.removeBtn = ToolButton(FluentIcon.CLOSE, self)
        self.removeBtn.setToolTip("删除此筛选条件")
        self.removeBtn.setAccessibleName("删除筛选条件")
        self.removeBtn.clicked.connect(self.removed)
        selectorRow.addWidget(self.removeBtn)
        layout.addLayout(selectorRow)

        # 输入区容器(包装动态输入控件,布局上保持位置稳定)
        self._inputContainer = QWidget(self)
        self._inputContainer.setObjectName("hskConditionInput")
        icL = QVBoxLayout(self._inputContainer)
        icL.setContentsMargins(0, 0, 0, 0)
        icL.setSpacing(8)
        layout.addWidget(self._inputContainer)

        # 显式触发一次,初始化输入区(避免依赖 currentIndexChanged 在初始化时是否触发)
        self._onColumnChanged(self.columnCombo.currentIndex())

    # ------------------------------------------------------------------
    # 列类型判定
    # ------------------------------------------------------------------
    def _getColumnType(self, col: str) -> str:
        if col == "作文题目":
            return self.COL_TYPE_ESSAY
        if col == "国籍":
            return self.COL_TYPE_COUNTRY
        if col == "证书级别":
            return self.COL_TYPE_CERT
        if col in (
            "听力理解分数",
            "阅读理解分数",
            "综合表达考试分数",
            "口试分数",
            "作文分数",
        ):
            return self.COL_TYPE_SCORE
        return self.COL_TYPE_TEXT

    def _onColumnChanged(self, _index: int) -> None:
        col = self.columnCombo.currentData() if self.columnCombo else None
        if not col:
            return
        self._currentColumn = col
        self._currentType = self._getColumnType(col)
        self._rebuildInputArea(self._currentType)
        self.changed.emit()

    # ------------------------------------------------------------------
    # 重建输入区(根据列类型)
    # ------------------------------------------------------------------
    def _clearInputArea(self) -> None:
        if not self._inputContainer:
            return
        # 销毁旧控件(从布局移除 + deleteLater)
        layout = self._inputContainer.layout()
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        # 清空引用
        self.keywordEdit = None
        self.countryNames = []
        self.essayCombo = None
        self.certCombo = None
        self.scoreMinBox = None
        self.scoreMaxBox = None
        self.scoreNoMinCheck = None
        self.scoreNoMaxCheck = None

    def _rebuildInputArea(self, colType: str) -> None:
        self._clearInputArea()
        layout = self._inputContainer.layout()
        if layout is None:
            return
        if colType == self.COL_TYPE_TEXT:
            self.keywordEdit = LineEdit(self._inputContainer)
            self.keywordEdit.setPlaceholderText("输入关键词(支持中英文)")
            self.keywordEdit.setClearButtonEnabled(True)
            self.keywordEdit.setAccessibleName(f"{self._currentColumn}关键词")
            layout.addWidget(self.keywordEdit)
        elif colType == self.COL_TYPE_ESSAY:
            self.essayCombo = ComboBox(self._inputContainer)
            self.essayCombo.addItems(hskEssayList)
            self.essayCombo.setAccessibleName("作文题目")
            self.essayCombo.setAccessibleDescription("从软件内置的 HSK 作文题目中选择")
            layout.addWidget(self.essayCombo)
        elif colType == self.COL_TYPE_COUNTRY:
            # 国籍改为自由文本输入(模糊匹配)
            # placeholder 展示前若干国家名作为可输入提示,但不限制选项
            self.keywordEdit = LineEdit(self._inputContainer)
            self.countryNames = list(hskCountryDict.keys())
            preview = " / ".join(self.countryNames[:6])
            self.keywordEdit.setPlaceholderText(
                f"输入国家名 例如:{preview} ..."
            )
            self.keywordEdit.setClearButtonEnabled(True)
            self.keywordEdit.setAccessibleName("国籍关键词")
            self.keywordEdit.setAccessibleDescription(
                "输入国家中文名或其中一部分，使用模糊匹配"
            )
            layout.addWidget(self.keywordEdit)
        elif colType == self.COL_TYPE_CERT:
            self.certCombo = ComboBox(self._inputContainer)
            self.certCombo.addItems(["A", "B", "C", "无"])
            self.certCombo.setAccessibleName("证书级别")
            layout.addWidget(self.certCombo)
        elif colType == self.COL_TYPE_SCORE:
            rangeWidget = QWidget(self._inputContainer)
            rangeLayout = QHBoxLayout(rangeWidget)
            rangeLayout.setContentsMargins(0, 0, 0, 0)
            rangeLayout.setSpacing(8)

            self.scoreMinBox = CompactSpinBox(rangeWidget)
            self.scoreMinBox.setRange(0, 150)
            self.scoreMinBox.setValue(0)
            self.scoreMinBox.setAccessibleName(f"{self._currentColumn}最低分")
            rangeLayout.addWidget(self.scoreMinBox, 1)

            sep = BodyLabel("至", rangeWidget)
            sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sep.setObjectName("hskRangeSeparator")
            rangeLayout.addWidget(sep)

            self.scoreMaxBox = CompactSpinBox(rangeWidget)
            self.scoreMaxBox.setRange(0, 150)
            self.scoreMaxBox.setValue(100)
            self.scoreMaxBox.setAccessibleName(f"{self._currentColumn}最高分")
            rangeLayout.addWidget(self.scoreMaxBox, 1)
            layout.addWidget(rangeWidget)

            boundWidget = QWidget(self._inputContainer)
            boundLayout = QHBoxLayout(boundWidget)
            boundLayout.setContentsMargins(0, 0, 0, 0)
            boundLayout.setSpacing(12)
            self.scoreNoMinCheck = CheckBox("不限最低分", boundWidget)
            self.scoreNoMinCheck.setChecked(True)
            boundLayout.addWidget(self.scoreNoMinCheck)
            self.scoreNoMaxCheck = CheckBox("不限最高分", boundWidget)
            self.scoreNoMaxCheck.setChecked(True)
            boundLayout.addWidget(self.scoreNoMaxCheck)
            boundLayout.addStretch(1)
            layout.addWidget(boundWidget)
            self.scoreNoMinCheck.stateChanged.connect(
                lambda _s: self._updateScoreSpinEnabled()
            )
            self.scoreNoMaxCheck.stateChanged.connect(
                lambda _s: self._updateScoreSpinEnabled()
            )
            self._updateScoreSpinEnabled()

    def _updateScoreSpinEnabled(self) -> None:
        if self.scoreMinBox and self.scoreNoMinCheck:
            self.scoreMinBox.setEnabled(not self.scoreNoMinCheck.isChecked())
        if self.scoreMaxBox and self.scoreNoMaxCheck:
            self.scoreMaxBox.setEnabled(not self.scoreNoMaxCheck.isChecked())

    # ------------------------------------------------------------------
    # 收集当前条件
    # ------------------------------------------------------------------
    def currentCondition(self) -> Optional[Dict]:
        """返回当前行的条件字典(若该行未填则返回 None)。"""
        col = self._currentColumn or (
            self.columnCombo.currentData() if self.columnCombo else None
        )
        if not col:
            return None
        ctype = self._currentType or self._getColumnType(col)
        if ctype == self.COL_TYPE_SCORE:
            lo: Optional[int] = None
            hi: Optional[int] = None
            if (
                self.scoreNoMinCheck
                and not self.scoreNoMinCheck.isChecked()
                and self.scoreMinBox
            ):
                lo = int(self.scoreMinBox.value())
            if (
                self.scoreNoMaxCheck
                and not self.scoreNoMaxCheck.isChecked()
                and self.scoreMaxBox
            ):
                hi = int(self.scoreMaxBox.value())
            if lo is None and hi is None:
                return None  # 未填 → 视为空条件,跳过
            return {
                "type": "score",
                "column": col,
                "min": lo,
                "max": hi,
            }
        # 文本 / 作文题目 / 国籍 / 证书级别 → 都用 keyword 字段(LIKE 模糊)
        # 国籍已改为自由文本(LineEdit),走 keywordEdit 读取;
        # 作文题目与证书级别使用 ComboBox 选项。
        if ctype == self.COL_TYPE_CERT and self.certCombo:
            keyword = self.certCombo.currentText().strip()
        elif ctype == self.COL_TYPE_ESSAY and self.essayCombo:
            keyword = self.essayCombo.currentText().strip()
            if keyword == "不限":
                return None
        elif self.keywordEdit:
            keyword = (self.keywordEdit.text() or "").strip()
        else:
            keyword = ""
        # 「证书级别 = 无」是有效筛选条件:db 中 '无' 是真实字符串,
        # 走 LIKE '%无%' 即可命中;哨兵 `__EMPTY__` 由 service 兜底匹配 NULL/空串
        if not keyword:
            return None  # 空条件跳过
        return {
            "type": self.COL_TYPE_TEXT if ctype == self.COL_TYPE_ESSAY else ctype,
            "column": col,
            "keyword": keyword,
        }

    def describe(self) -> str:
        """返回当前条件的可读描述(用于状态条)。"""
        cond = self.currentCondition()
        if not cond:
            return ""
        col = cond["column"]
        if cond["type"] == "score":
            lo = cond.get("min")
            hi = cond.get("max")
            loStr = "∞" if lo is None else str(lo)
            hiStr = "∞" if hi is None else str(hi)
            return f"{col} ∈ [{loStr}, {hiStr}]"
        # 空值哨兵 → 显示为「(无)」,与 UI ComboBox 选项保持一致
        if cond.get("keyword") == "__EMPTY__":
            return f"{col} = '(无)'"
        return f"{col} ~ '{cond['keyword']}'"


# ======================================================================
# 主浏览器
# ======================================================================
class HskCorpusBrowser(QWidget, WorkerMixin):
    """HSK 语料检索主面板(现代化简洁 UI · 多条件组合检索)。"""

    resourcePreparationRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        QWidget.__init__(self, parent)
        WorkerMixin.__init__(self)
        self.setObjectName("HskCorpusBrowser")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._service = HskCorpusService.instance()
        # 强绑定:UI 始终只使用 BOUND_HSK_DB_PATH 这一个具体 db 文件,
        # 覆盖 cfg.HskCorpusDbPath(以防用户在设置页里误改了路径,
        # 仍然能让本页正确加载用户指定的语料库数据)。
        self._service.setDbPath(BOUND_HSK_DB_PATH)
        self._dbPath = BOUND_HSK_DB_PATH
        # schema 准备(若 db 已被删除或首次构建,幂等创建表 + 索引)
        try:
            self._service.ensureSchema()
        except Exception as e:
            logger.warning(f"[HskCorpusBrowser] ensureSchema 失败: {e}")

        # 状态
        self._searchStartTs: float = 0.0
        self._currentWorker: Optional[HskCorpusSearchWorker] = None
        self._matchTotal: int = 0  # 真实总命中数(不限 20 条)

        # PRD-005:最近一次「搜索」成功的条件与导出 worker
        self._lastConditions: List[Dict] = []
        self._lastSearchFinished: bool = False
        self._exportWorker: Optional[Any] = None
        self._exportBillingTransaction: Optional[Any] = None
        self._lastExportDir: Optional[str] = None  # 用于完成后「打开文件夹」

        # 条件行列表(动态增删)
        self._conditionRows: List[_ConditionRow] = []
        self._rowsContainer: Optional[QVBoxLayout] = None

        # 控件引用
        self.addConditionBtn: Optional[PushButton] = None
        self.resetConditionsBtn: Optional[PushButton] = None
        self.searchBtn: Optional[PrimaryPushButton] = None
        self.statusLabel: Optional[StrongBodyLabel] = None
        self.elapsedLabel: Optional[CaptionLabel] = None
        self.tableView: Optional[TableView] = None
        self.model: Optional[HskCorpusModel] = None
        self._dbPathLabel: Optional[CaptionLabel] = None
        self._tableCard: Optional[CardWidget] = None
        self._filterPanel: Optional[CardWidget] = None
        self._workspaceLayout: Optional[QBoxLayout] = None
        self._conditionScroll: Optional[ScrollArea] = None
        self._resultStack: Optional[QStackedWidget] = None
        self._emptyState: Optional[QWidget] = None
        self._emptyStateTitle: Optional[StrongBodyLabel] = None
        self._emptyStateCaption: Optional[CaptionLabel] = None
        self._resourceActionButton: Optional[PrimaryPushButton] = None
        self._resourceDialog: Optional[ResourceVerificationDialog] = None
        self._isPreparingResources = False
        self._corpusCountLabel: Optional[CaptionLabel] = None
        self._conditionSummary: Optional[QFrame] = None
        self._conditionSummaryTitle: Optional[StrongBodyLabel] = None
        self._conditionSummaryText: Optional[CaptionLabel] = None
        self._resultSplitter: Optional[QSplitter] = None
        self._detailDrawer: Optional[HskCorpusDetailDrawer] = None
        self.columnSettingsBtn: Optional[PushButton] = None
        self._columnMenu: Optional[QMenu] = None
        self._columnActions: Dict[str, QAction] = {}
        self._visibleResultColumns = set(_DEFAULT_RESULT_COLUMNS)

        # 主线程节流拉取 timer
        self._pullTimer = QTimer(self)
        self._pullTimer.setInterval(_UI_PULL_INTERVAL_MS)
        self._pullTimer.timeout.connect(self._onPullSnapshot)
        self._lastSnapshotTotal: int = 0

        self._initUi()
        self._updateDbStatus()

        # 订阅事件
        self._service.onSchemaReady(self._onSchemaReady)
        self._service.onImported(self._onImported)

    # ------------------------------------------------------------------
    # UI 构造
    # ------------------------------------------------------------------
    def _initUi(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 24)
        root.setSpacing(18)

        # ---------- 顶部标题区 ----------
        headerLayout = QHBoxLayout()
        headerLayout.setSpacing(12)

        titleIconHost = QFrame(self)
        titleIconHost.setObjectName("hskTitleIconHost")
        titleIconHost.setFixedSize(44, 44)
        titleIconLayout = QVBoxLayout(titleIconHost)
        titleIconLayout.setContentsMargins(11, 11, 11, 11)
        titleIcon = IconWidget(FluentIcon.DICTIONARY, titleIconHost)
        titleIcon.setFixedSize(22, 22)
        titleIconLayout.addWidget(titleIcon)
        headerLayout.addWidget(titleIconHost, 0, Qt.AlignmentFlag.AlignTop)

        headerText = QVBoxLayout()
        headerText.setSpacing(3)
        title = SubtitleLabel("HSK 作文检索", self)
        title.setObjectName("hskPageTitle")
        headerText.addWidget(title)
        subtitle = BodyLabel(
            "组合真实语料字段进行检索，右侧预览元数据并导出全部命中作文。",
            self,
        )
        subtitle.setObjectName("hskPageSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setMinimumWidth(0)
        subtitle.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        headerText.addWidget(subtitle)
        headerLayout.addLayout(headerText, 1)

        self._corpusCountLabel = CaptionLabel("正在读取语料库…", self)
        self._corpusCountLabel.setObjectName("hskCorpusCountChip")
        headerLayout.addWidget(
            self._corpusCountLabel, 0, Qt.AlignmentFlag.AlignTop
        )
        root.addLayout(headerLayout)

        # ---------- 左侧筛选 / 右侧结果 ----------
        workspace = QWidget(self)
        workspace.setObjectName("hskWorkspace")
        self._workspaceLayout = QBoxLayout(QBoxLayout.Direction.LeftToRight, workspace)
        self._workspaceLayout.setContentsMargins(0, 0, 0, 0)
        self._workspaceLayout.setSpacing(16)

        self._filterPanel = CardWidget(workspace)
        self._filterPanel.setObjectName("hskFilterPanel")
        self._filterPanel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._filterPanel.setMinimumWidth(300)
        self._filterPanel.setMaximumWidth(360)
        self._filterPanel.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        filterLayout = QVBoxLayout(self._filterPanel)
        filterLayout.setContentsMargins(18, 18, 18, 16)
        filterLayout.setSpacing(12)

        filterHeader = QHBoxLayout()
        filterHeader.setSpacing(8)
        filterTitle = StrongBodyLabel("检索条件", self._filterPanel)
        filterTitle.setObjectName("hskSectionTitle")
        filterHeader.addWidget(filterTitle)
        filterHeader.addStretch(1)
        self.resetConditionsBtn = PushButton("重置", self._filterPanel)
        self.resetConditionsBtn.setAccessibleName("重置全部检索条件")
        self.resetConditionsBtn.clicked.connect(self._resetConditions)
        filterHeader.addWidget(self.resetConditionsBtn)
        filterLayout.addLayout(filterHeader)

        filterHint = CaptionLabel(
            "可添加多个条件，所有条件之间使用 AND 关系。",
            self._filterPanel,
        )
        filterHint.setObjectName("hskSectionCaption")
        filterHint.setWordWrap(True)
        filterLayout.addWidget(filterHint)

        self._conditionScroll = ScrollArea(self._filterPanel)
        self._conditionScroll.setObjectName("hskConditionScroll")
        self._conditionScroll.setWidgetResizable(True)
        self._conditionScroll.setFrameShape(QFrame.Shape.NoFrame)
        self._conditionScroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        rowsBox = QWidget(self._conditionScroll)
        rowsBox.setObjectName("hskConditionList")
        rowsOuter = QVBoxLayout(rowsBox)
        rowsOuter.setContentsMargins(0, 0, 4, 0)
        rowsOuter.setSpacing(0)
        self._rowsContainer = QVBoxLayout()
        self._rowsContainer.setContentsMargins(0, 0, 0, 0)
        self._rowsContainer.setSpacing(0)
        rowsOuter.addLayout(self._rowsContainer)
        rowsOuter.addStretch(1)
        self._conditionScroll.setWidget(rowsBox)
        filterLayout.addWidget(self._conditionScroll, 1)

        # 默认添加一行(避免界面空白)
        self._addConditionRow()

        # 操作区固定在筛选面板底部
        actionRow = QHBoxLayout()
        actionRow.setSpacing(8)
        self.addConditionBtn = PushButton(
            "添加条件", self._filterPanel, FluentIcon.ADD
        )
        self.addConditionBtn.setToolTip("添加一条筛选条件")
        self.addConditionBtn.setAccessibleName("添加筛选条件")
        self.addConditionBtn.clicked.connect(self._onAddConditionClicked)
        actionRow.addWidget(self.addConditionBtn, 1)
        self.searchBtn = PrimaryPushButton(
            "开始检索", self._filterPanel, FluentIcon.SEARCH
        )
        self.searchBtn.setAccessibleName("开始检索 HSK 作文语料")
        self.searchBtn.clicked.connect(self._onSearchClicked)
        actionRow.addWidget(self.searchBtn, 1)
        filterLayout.addLayout(actionRow)

        # ---------- 结果面板 ----------
        self._tableCard = CardWidget(workspace)
        self._tableCard.setObjectName("hskResultPanel")
        self._tableCard.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        resultLayout = QVBoxLayout(self._tableCard)
        resultLayout.setContentsMargins(18, 18, 18, 14)
        resultLayout.setSpacing(12)
        self._tableCard.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        resultHeader = QHBoxLayout()
        resultHeader.setSpacing(10)
        resultText = QVBoxLayout()
        resultText.setSpacing(2)
        resultTitle = StrongBodyLabel("检索结果", self._tableCard)
        resultTitle.setObjectName("hskSectionTitle")
        resultText.addWidget(resultTitle)
        self.elapsedLabel = CaptionLabel(
            "结果表仅显示真实语料字段，最多预览前 20 条。",
            self._tableCard,
        )
        self.elapsedLabel.setObjectName("hskSectionCaption")
        self.elapsedLabel.setWordWrap(True)
        self.elapsedLabel.setMinimumWidth(0)
        self.elapsedLabel.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        resultText.addWidget(self.elapsedLabel)
        resultHeader.addLayout(resultText, 1)

        self.statusLabel = StrongBodyLabel("就绪", self._tableCard)
        self.statusLabel.setObjectName("hskStatusChip")
        self.statusLabel.setProperty("state", "ok")
        self.statusLabel.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        resultHeader.addWidget(self.statusLabel)

        self.columnSettingsBtn = PushButton(
            "列设置", self._tableCard, FluentIcon.SETTING
        )
        self.columnSettingsBtn.setToolTip("选择结果表中显示的字段")
        self.columnSettingsBtn.setAccessibleName("设置检索结果列")
        resultHeader.addWidget(self.columnSettingsBtn)

        # PRD-005:导出最近一次搜索的全部命中作文
        self.exportAllBtn = PushButton(
            "导出全部命中", self._tableCard, FluentIcon.SAVE
        )
        self.exportAllBtn.setToolTip(
            "按最近一次「搜索」命中的全部作文(不限 20 条),从本地镜像库提取原文"
        )
        self.exportAllBtn.setAccessibleName("导出全部命中作文")
        self.exportAllBtn.setEnabled(False)
        self.exportAllBtn.clicked.connect(self._onExportAllClicked)
        resultHeader.addWidget(self.exportAllBtn)
        resultLayout.addLayout(resultHeader)

        self._conditionSummary = QFrame(self._tableCard)
        self._conditionSummary.setObjectName("hskConditionSummary")
        self._conditionSummary.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        summaryLayout = QVBoxLayout(self._conditionSummary)
        summaryLayout.setContentsMargins(12, 9, 12, 9)
        summaryLayout.setSpacing(2)
        self._conditionSummaryTitle = StrongBodyLabel(
            "尚未应用检索条件", self._conditionSummary
        )
        self._conditionSummaryTitle.setObjectName("hskConditionSummaryTitle")
        summaryLayout.addWidget(self._conditionSummaryTitle)
        self._conditionSummaryText = CaptionLabel(
            "完成检索后，这里会保留本次实际使用的条件。",
            self._conditionSummary,
        )
        self._conditionSummaryText.setObjectName("hskConditionSummaryText")
        self._conditionSummaryText.setWordWrap(True)
        summaryLayout.addWidget(self._conditionSummaryText)
        resultLayout.addWidget(self._conditionSummary)

        self._resultStack = QStackedWidget(self._tableCard)
        self._resultStack.setObjectName("hskResultStack")

        self._emptyState = QWidget(self._resultStack)
        self._emptyState.setObjectName("hskEmptyState")
        emptyLayout = QVBoxLayout(self._emptyState)
        emptyLayout.setContentsMargins(24, 24, 24, 24)
        emptyLayout.setSpacing(8)
        emptyLayout.addStretch(1)
        emptyIcon = IconWidget(FluentIcon.SEARCH, self._emptyState)
        emptyIcon.setFixedSize(38, 38)
        emptyLayout.addWidget(emptyIcon, 0, Qt.AlignmentFlag.AlignHCenter)
        self._emptyStateTitle = StrongBodyLabel(
            "设置条件后开始检索", self._emptyState
        )
        self._emptyStateTitle.setObjectName("hskEmptyTitle")
        self._emptyStateTitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emptyLayout.addWidget(self._emptyStateTitle)
        self._emptyStateCaption = CaptionLabel(
            "支持作文题目、国籍、证书级别和五项考试分数。",
            self._emptyState,
        )
        self._emptyStateCaption.setObjectName("hskEmptyCaption")
        self._emptyStateCaption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._emptyStateCaption.setWordWrap(True)
        self._emptyStateCaption.setMinimumWidth(0)
        self._emptyStateCaption.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        emptyLayout.addWidget(self._emptyStateCaption)
        self._resourceActionButton = PrimaryPushButton(
            "准备作文资源",
            self._emptyState,
            FluentIcon.DOWNLOAD,
        )
        self._resourceActionButton.setAccessibleName("自动准备 HSK 作文资源")
        self._resourceActionButton.setToolTip(
            "自动完成登录（如需要）、资源检查、下载与页面刷新"
        )
        self._resourceActionButton.clicked.connect(
            self._onResourcePreparationClicked
        )
        self._resourceActionButton.hide()
        emptyLayout.addWidget(
            self._resourceActionButton,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        emptyLayout.addStretch(1)
        self._resultStack.addWidget(self._emptyState)

        self.tableView = TableView(self._resultStack)
        self.tableView.setObjectName("hskResultTable")
        self.tableView.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
        )
        self.tableView.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tableView.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.tableView.setWordWrap(True)
        self.tableView.verticalHeader().setVisible(False)
        self.tableView.verticalHeader().setDefaultSectionSize(40)
        self.tableView.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.tableView.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tableView.setAlternatingRowColors(True)
        self.tableView.setAccessibleName("HSK 作文检索结果表")

        self.model = HskCorpusModel(self.tableView)
        self.model.setHeaderMap(self._service.columnHeaderMap())
        self.tableView.setModel(self.model)
        self.model.reset()
        self.tableView.selectionModel().currentRowChanged.connect(
            self._onResultCurrentRowChanged
        )
        self.tableView.clicked.connect(self._onResultIndexActivated)
        self._resultStack.addWidget(self.tableView)

        self._detailDrawer = HskCorpusDetailDrawer(self._tableCard)
        self._detailDrawer.closed.connect(self._hideDetailDrawer)
        self._detailDrawer.hide()

        self._resultSplitter = QSplitter(
            Qt.Orientation.Horizontal, self._tableCard
        )
        self._resultSplitter.setObjectName("hskResultSplitter")
        self._resultSplitter.setChildrenCollapsible(False)
        self._resultSplitter.setHandleWidth(8)
        self._resultSplitter.addWidget(self._resultStack)
        self._resultSplitter.addWidget(self._detailDrawer)
        self._resultSplitter.setStretchFactor(0, 1)
        self._resultSplitter.setStretchFactor(1, 0)
        resultLayout.addWidget(self._resultSplitter, 1)

        self._createColumnMenu()
        self._applyColumnVisibility()

        self._dbPathLabel = CaptionLabel("", self._tableCard)
        self._dbPathLabel.setObjectName("hskDatabaseMessage")
        self._dbPathLabel.setWordWrap(True)
        self._dbPathLabel.setMaximumHeight(36)
        resultLayout.addWidget(self._dbPathLabel)

        self._workspaceLayout.addWidget(self._filterPanel)
        self._workspaceLayout.addWidget(self._tableCard, 1)
        self._workspaceLayout.setStretch(0, 0)
        self._workspaceLayout.setStretch(1, 1)
        root.addWidget(workspace, 1)

        qconfig.themeChangedFinished.connect(self._applyTheme)
        self._applyTheme()
        self._showEmptyState(
            "设置条件后开始检索",
            "支持作文题目、国籍、证书级别和五项考试分数。",
        )
        QTimer.singleShot(0, self._applyResponsiveLayout)

    def _applyTheme(self) -> None:
        """按当前主题应用本页面的层级色与状态色。"""
        if isDarkTheme():
            pageBackground = pageBackgroundColor(True).name()
            surface = "#252525"
            surfaceMuted = "#2D2D2D"
            border = "#3B3B3B"
            text = "#F3F3F3"
            muted = "#B7B7B7"
            accent = "#00B09C"
            accentSurface = "rgba(0, 176, 156, 0.18)"
            accentText = "#5DE0CF"
            dangerSurface = "rgba(255, 99, 99, 0.16)"
            dangerText = "#FF9A9A"
        else:
            pageBackground = pageBackgroundColor(False).name()
            surface = "#FFFFFF"
            surfaceMuted = "#F5F8F8"
            border = "#D9E2E2"
            text = "#1F2A2A"
            muted = "#5D6B6B"
            accent = "#007C70"
            accentSurface = "rgba(0, 176, 156, 0.12)"
            accentText = "#007C70"
            dangerSurface = "#FDEBEC"
            dangerText = "#A4262C"

        self.setStyleSheet(
            f"""
            QWidget#HskCorpusBrowser {{
                background: {pageBackground};
            }}
            QWidget#hskWorkspace {{
                background: transparent;
            }}
            QWidget#hskFilterPanel,
            QWidget#hskResultPanel {{
                background: {surface};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            QFrame#hskTitleIconHost {{
                background: {accentSurface};
                border: none;
                border-radius: 10px;
            }}
            QLabel#hskPageSubtitle,
            QLabel#hskSectionCaption,
            QLabel#hskEmptyCaption,
            QLabel#hskDatabaseMessage,
            QLabel#hskRangeSeparator {{
                color: {muted};
            }}
            QLabel#hskCorpusCountChip {{
                color: {accentText};
                background: {accentSurface};
                border-radius: 6px;
                padding: 6px 10px;
            }}
            QWidget#hskConditionList,
            QScrollArea#hskConditionScroll,
            QScrollArea#hskConditionScroll > QWidget > QWidget {{
                background: transparent;
                border: none;
            }}
            QWidget#hskConditionRow {{
                background: transparent;
                border: none;
                border-bottom: 1px solid {border};
            }}
            QWidget#hskConditionInput {{
                background: transparent;
            }}
            QFrame#hskConditionSummary {{
                background: {accentSurface};
                border: none;
                border-left: 3px solid {accent};
                border-radius: 7px;
            }}
            QLabel#hskConditionSummaryTitle {{
                color: {accentText};
            }}
            QLabel#hskConditionSummaryText {{
                color: {muted};
            }}
            QStackedWidget#hskResultStack {{
                background: transparent;
                border: none;
            }}
            QSplitter#hskResultSplitter {{
                background: transparent;
                border: none;
            }}
            QSplitter#hskResultSplitter::handle {{
                background: transparent;
            }}
            QFrame#hskDetailDrawer {{
                background: {surfaceMuted};
                border: 1px solid {border};
                border-radius: 10px;
            }}
            QLabel#hskDetailMuted,
            QLabel#hskDetailSectionLabel {{
                color: {muted};
            }}
            QLabel#hskDetailTitle,
            QLabel#hskDetailMetaValue {{
                color: {text};
            }}
            QLabel#hskDetailBodyState {{
                color: {muted};
                background: {surface};
                border-radius: 5px;
                padding: 3px 7px;
            }}
            QLabel#hskDetailBodyState[available="true"] {{
                color: {accentText};
                background: {accentSurface};
            }}
            QPlainTextEdit#hskDetailBody {{
                color: {text};
                background: {surface};
                border: 1px solid {border};
                border-radius: 8px;
                padding: 8px;
                selection-background-color: {accent};
            }}
            QWidget#hskEmptyState {{
                background: {surfaceMuted};
                border: 1px solid {border};
                border-radius: 10px;
            }}
            QLabel#hskEmptyTitle {{
                color: {text};
            }}
            QLabel#hskStatusChip {{
                color: {accentText};
                background: {accentSurface};
                border-radius: 6px;
                padding: 5px 9px;
            }}
            QLabel#hskStatusChip[state="running"] {{
                color: {muted};
                background: {surfaceMuted};
            }}
            QLabel#hskStatusChip[state="bad"] {{
                color: {dangerText};
                background: {dangerSurface};
            }}
            QTableView#hskResultTable {{
                background: {surface};
                alternate-background-color: {surfaceMuted};
                border: 1px solid {border};
                border-radius: 10px;
                gridline-color: {border};
            }}
            """
        )

    def _applyResponsiveLayout(self) -> None:
        """在较窄窗口中把筛选区移到结果区上方。"""
        if self._workspaceLayout is None or self._filterPanel is None:
            return
        isCompact = self.width() < 980
        direction = (
            QBoxLayout.Direction.TopToBottom
            if isCompact
            else QBoxLayout.Direction.LeftToRight
        )
        if self._workspaceLayout.direction() != direction:
            self._workspaceLayout.setDirection(direction)
        if isCompact:
            self._filterPanel.setMinimumWidth(0)
            self._filterPanel.setMaximumWidth(16777215)
            self._filterPanel.setMaximumHeight(360)
        else:
            self._filterPanel.setMinimumWidth(300)
            self._filterPanel.setMaximumWidth(360)
            self._filterPanel.setMaximumHeight(16777215)

        if self._resultSplitter is not None and self._detailDrawer is not None:
            detailOrientation = (
                Qt.Orientation.Vertical
                if self.width() < 1280
                else Qt.Orientation.Horizontal
            )
            if self._resultSplitter.orientation() != detailOrientation:
                self._resultSplitter.setOrientation(detailOrientation)
            if detailOrientation == Qt.Orientation.Vertical:
                self._detailDrawer.setMinimumWidth(0)
                self._detailDrawer.setMaximumWidth(16777215)
                self._detailDrawer.setMaximumHeight(360)
            else:
                self._detailDrawer.setMinimumWidth(300)
                self._detailDrawer.setMaximumWidth(380)
                self._detailDrawer.setMaximumHeight(16777215)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._applyResponsiveLayout()

    def _showEmptyState(self, title: str, caption: str) -> None:
        if self._emptyStateTitle is not None:
            self._emptyStateTitle.setText(title)
        if self._emptyStateCaption is not None:
            self._emptyStateCaption.setText(caption)
        if self._resultStack is not None and self._emptyState is not None:
            self._resultStack.setCurrentWidget(self._emptyState)

    def _setResourceActionVisible(self, isVisible: bool) -> None:
        """仅在语料资源不可用时显示单一恢复入口。"""
        if self._resourceActionButton is None:
            return
        self._resourceActionButton.setVisible(bool(isVisible))
        self._resourceActionButton.setEnabled(not self._isPreparingResources)
        self._resourceActionButton.setText(
            "正在准备…" if self._isPreparingResources else "准备作文资源"
        )

    def _onResourcePreparationClicked(self) -> None:
        if self._isPreparingResources:
            return
        self.resourcePreparationRequested.emit()

    def startResourcePreparation(self) -> None:
        """在本页面续接资源检查、自动修复与刷新流程。"""
        if self._isPreparingResources:
            dialog = self._resourceDialog
            if dialog is not None:
                dialog.raise_()
                dialog.activateWindow()
            return

        self._isPreparingResources = True
        self._setResourceActionVisible(True)
        self._setStatusRunning("正在准备语料库")
        self._showEmptyState(
            "正在准备作文资源",
            "应用会自动检查并下载缺失数据，完成后即可直接检索。",
        )

        dialog = ResourceVerificationDialog(
            parent=self.window(),
            autoRepair=True,
        )
        self._resourceDialog = dialog
        dialog.resourcesReady.connect(self._activatePreparedResources)
        dialog.finished.connect(self._onResourceDialogFinished)
        dialog.exec()

    def _activatePreparedResources(self) -> None:
        """资源安装完成后立即让当前页面读取新数据库。"""
        try:
            self._service.setDbPath(BOUND_HSK_DB_PATH)
            self._service.ensureSchema()
            if self.model is not None:
                self.model.reset()
                self._applyColumnVisibility()
            self._updateDbStatus()
            if self._service.isAvailable():
                InfoBar.success(
                    title="作文资源已就绪",
                    content="现在可以直接设置条件并检索，无需重置页面。",
                    parent=self,
                    duration=2500,
                    position=InfoBarPosition.TOP,
                )
        except Exception as error:
            logger.exception(f"[HskCorpusBrowser] 激活作文资源失败: {error}")
            self._setStatusBad("语料库加载失败")
            self._showEmptyState(
                "资源已下载，但加载失败",
                "请点击下方按钮重试；若问题持续出现，请检查磁盘权限。",
            )

    def _onResourceDialogFinished(self, _result: int) -> None:
        self._resourceDialog = None
        self._isPreparingResources = False
        self._updateDbStatus()

    def _showTableState(self) -> None:
        if self._resultStack is not None and self.tableView is not None:
            self._resultStack.setCurrentWidget(self.tableView)

    def showEvent(self, event) -> None:
        """每次回到页面都自动刷新资源状态，不依赖手动重置。"""
        super().showEvent(event)
        QTimer.singleShot(0, self.refreshResourceState)

    def refreshResourceState(self) -> None:
        """重新读取本地资源状态并同步当前页面。"""
        self._updateDbStatus()

    # ------------------------------------------------------------------
    # 结果列、条件摘要与详情抽屉
    # ------------------------------------------------------------------
    def _createColumnMenu(self) -> None:
        if self.columnSettingsBtn is None or self.model is None:
            return
        self._columnMenu = QMenu(self.columnSettingsBtn)
        self._columnMenu.setAccessibleName("HSK 检索结果列设置")
        self._columnActions.clear()
        for columnName in self.model.columns():
            if columnName in _INTERNAL_RESULT_COLUMNS:
                continue
            action = QAction(columnName, self._columnMenu)
            action.setCheckable(True)
            action.setChecked(columnName in self._visibleResultColumns)
            action.toggled.connect(
                lambda checked, name=columnName: self._setColumnVisible(
                    name, checked
                )
            )
            self._columnMenu.addAction(action)
            self._columnActions[columnName] = action
        self._columnMenu.addSeparator()
        restoreAction = QAction("恢复默认列", self._columnMenu)
        restoreAction.triggered.connect(self._restoreDefaultColumns)
        self._columnMenu.addAction(restoreAction)
        self.columnSettingsBtn.setMenu(self._columnMenu)

    def _setColumnVisible(self, columnName: str, isVisible: bool) -> bool:
        """切换真实字段显示状态，并确保结果表至少保留一列。"""
        if columnName in _INTERNAL_RESULT_COLUMNS:
            return False
        if isVisible:
            self._visibleResultColumns.add(columnName)
        elif columnName in self._visibleResultColumns:
            if len(self._visibleResultColumns) == 1:
                action = self._columnActions.get(columnName)
                if action is not None:
                    action.blockSignals(True)
                    action.setChecked(True)
                    action.blockSignals(False)
                return False
            self._visibleResultColumns.remove(columnName)
        self._applyColumnVisibility()
        return True

    def _restoreDefaultColumns(self) -> None:
        self._visibleResultColumns = set(_DEFAULT_RESULT_COLUMNS)
        for columnName, action in self._columnActions.items():
            action.blockSignals(True)
            action.setChecked(columnName in self._visibleResultColumns)
            action.blockSignals(False)
        self._applyColumnVisibility()

    def _applyColumnVisibility(self) -> None:
        if self.tableView is None or self.model is None:
            return
        for columnIndex, columnName in enumerate(self.model.columns()):
            shouldShow = (
                columnName not in _INTERNAL_RESULT_COLUMNS
                and columnName in self._visibleResultColumns
            )
            self.tableView.setColumnHidden(columnIndex, not shouldShow)
        self._applyResultColumnWidths()

    def _applyResultColumnWidths(self) -> None:
        if self.tableView is None or self.model is None:
            return
        preferredWidths = {
            "作文题目": 260,
            "国籍": 110,
            "证书级别": 96,
            "作文分数": 96,
            "总字数": 88,
        }
        for columnIndex, columnName in enumerate(self.model.columns()):
            if self.tableView.isColumnHidden(columnIndex):
                continue
            width = preferredWidths.get(columnName, 120)
            self.tableView.setColumnWidth(columnIndex, width)

    @staticmethod
    def _describeAppliedCondition(condition: Dict) -> str:
        columnName = str(condition.get("column") or "")
        displayColumn = "题目" if columnName == "作文题目" else columnName
        if condition.get("type") == "score":
            minimum = condition.get("min")
            maximum = condition.get("max")
            if minimum is not None and maximum is not None:
                return f"{displayColumn}：{minimum}–{maximum}"
            if minimum is not None:
                return f"{displayColumn}：≥ {minimum}"
            if maximum is not None:
                return f"{displayColumn}：≤ {maximum}"
            return ""
        keyword = condition.get("keyword")
        if keyword == "__EMPTY__":
            return f"{displayColumn}：无"
        if keyword in (None, ""):
            return ""
        return f"{displayColumn}：包含「{keyword}」"

    def _updateConditionSummary(self, conditions: List[Dict]) -> None:
        descriptions = [
            self._describeAppliedCondition(condition)
            for condition in conditions
        ]
        descriptions = [item for item in descriptions if item]
        if self._conditionSummaryTitle is None or self._conditionSummaryText is None:
            return
        if not descriptions:
            self._conditionSummaryTitle.setText("尚未应用检索条件")
            self._conditionSummaryText.setText(
                "完成检索后，这里会保留本次实际使用的条件。"
            )
            return
        self._conditionSummaryTitle.setText(
            f"已应用 {len(descriptions)} 个条件（全部满足）"
        )
        self._conditionSummaryText.setText("  且  ".join(descriptions))

    def _onResultCurrentRowChanged(self, current, previous) -> None:
        del previous
        self._showResultDetail(current.row())

    def _onResultIndexActivated(self, index) -> None:
        self._showResultDetail(index.row())

    def _showResultDetail(self, rowIndex: int) -> None:
        if self.model is None or self._detailDrawer is None:
            return
        record = self.model.recordAt(rowIndex)
        if record is None:
            return
        zwhao = str(record.get("作文母号") or "")
        localRecord = hskLocalCorpusService.getRecord(zwhao) if zwhao else None
        self._detailDrawer.setRecord(record, localRecord)
        self._detailDrawer.show()
        self._applyResponsiveLayout()
        if self._resultSplitter is not None:
            if self._resultSplitter.orientation() == Qt.Orientation.Horizontal:
                totalWidth = max(640, self._resultSplitter.width())
                self._resultSplitter.setSizes([max(320, totalWidth - 340), 340])
            else:
                totalHeight = max(520, self._resultSplitter.height())
                self._resultSplitter.setSizes(
                    [max(260, totalHeight - 300), 300]
                )

    def _hideDetailDrawer(self) -> None:
        if self._detailDrawer is not None:
            self._detailDrawer.hide()

    # ------------------------------------------------------------------
    # 条件行管理
    # ------------------------------------------------------------------
    def _addConditionRow(self) -> _ConditionRow:
        columns = self._service.availableColumns()
        row = _ConditionRow(columns, self)
        row.removed.connect(lambda r=row: self._removeConditionRow(r))
        self._conditionRows.append(row)
        if self._rowsContainer is not None:
            self._rowsContainer.addWidget(row)
        return row

    def _removeConditionRow(self, row: _ConditionRow) -> None:
        if row not in self._conditionRows:
            return
        self._conditionRows.remove(row)
        if self._rowsContainer is not None:
            self._rowsContainer.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        # 至少保留一行(避免空界面误操作)
        if not self._conditionRows:
            self._addConditionRow()

    def _onAddConditionClicked(self) -> None:
        self._addConditionRow()
        if self._conditionScroll is not None:
            QTimer.singleShot(
                0,
                lambda: self._conditionScroll.verticalScrollBar().setValue(
                    self._conditionScroll.verticalScrollBar().maximum()
                ),
            )

    def _clearConditionRows(self) -> None:
        """清空条件行，不触发“至少保留一行”的自动补行逻辑。"""
        for row in list(self._conditionRows):
            if self._rowsContainer is not None:
                self._rowsContainer.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._conditionRows.clear()

    def _resetConditions(self) -> None:
        """恢复初始检索状态并取消当前后台检索。"""
        self.disposeWorker(waitMs=0)
        self._pullTimer.stop()
        self._currentWorker = None
        self._lastSnapshotTotal = 0
        self._matchTotal = 0
        self._lastConditions = []
        self._lastSearchFinished = False
        self._clearConditionRows()
        self._addConditionRow()
        if self.model is not None:
            self.model.reset()
            self._applyColumnVisibility()
        self._hideDetailDrawer()
        self._updateConditionSummary([])
        if hasattr(self, "exportAllBtn"):
            self.exportAllBtn.setEnabled(False)
        if self.elapsedLabel is not None:
            self.elapsedLabel.setText(
                "结果表仅显示真实语料字段，最多预览前 20 条。"
            )
        self._showEmptyState(
            "设置条件后开始检索",
            "支持作文题目、国籍、证书级别和五项考试分数。",
        )
        self._updateDbStatus()

    def _collectConditions(self) -> List[Dict]:
        """收集所有非空条件,组合成 conditions 列表(供 service/worker 使用)。"""
        conds: List[Dict] = []
        for r in self._conditionRows:
            c = r.currentCondition()
            if c:
                conds.append(c)
        return conds

    # ------------------------------------------------------------------
    # db 状态
    # ------------------------------------------------------------------
    def _updateDbStatus(self) -> None:
        if not self.statusLabel:
            return
        # ---- 路径不存在 → 在当前页面提供唯一恢复动作 ----
        if not self._dbPath.exists():
            self._setStatusBad("语料库未就绪")
            if self._corpusCountLabel:
                self._corpusCountLabel.setText("语料库未就绪")
            if self.searchBtn:
                self.searchBtn.setEnabled(False)
            if self._dbPathLabel:
                self._dbPathLabel.setText("作文资源尚未准备完成，可在当前页面自动处理。")
            self._showEmptyState(
                "准备好资源，即可开始检索",
                "点击一次即可完成登录（如需要）、检查和下载，不必前往设置页。",
            )
            self._setResourceActionVisible(True)
            return
        # ---- 文件存在但无数据 ----
        try:
            n = self._service.rowCount()
        except Exception as error:
            logger.warning(f"[HskCorpusBrowser] 读取语料库状态失败: {error}")
            n = 0
        if n == 0:
            self._setStatusBad("语料库暂无数据")
            if self._corpusCountLabel:
                self._corpusCountLabel.setText("暂无语料")
            if self.searchBtn:
                self.searchBtn.setEnabled(False)
            if self._dbPathLabel:
                self._dbPathLabel.setText("作文资源为空或不完整，可在当前页面自动修复。")
            self._showEmptyState(
                "准备好资源，即可开始检索",
                "应用会自动下载并校验作文数据，完成后页面会立即恢复。",
            )
            self._setResourceActionVisible(True)
            return
        # ---- schema 校验(列是否齐全)----
        cols = self._service.availableColumns()
        if not cols:
            self._setStatusBad("语料库结构异常")
            if self._corpusCountLabel:
                self._corpusCountLabel.setText("结构异常")
            if self.searchBtn:
                self.searchBtn.setEnabled(False)
            if self._dbPathLabel:
                self._dbPathLabel.setText("作文资源结构异常，可在当前页面自动修复。")
            self._showEmptyState(
                "作文资源需要修复",
                "点击下方按钮自动重新检查并下载，不必离开当前页面。",
            )
            self._setResourceActionVisible(True)
            return
        # ---- 一切正常 ----
        self._setStatusOk(f"已加载 {n:,} 条语料")
        if self._corpusCountLabel:
            self._corpusCountLabel.setText(f"共 {n:,} 条作文")
        if self.searchBtn:
            self.searchBtn.setEnabled(True)
        if self._dbPathLabel:
            self._dbPathLabel.setText("")
        self._setResourceActionVisible(False)

    def _setStatus(self, text: str, state: str) -> None:
        if not self.statusLabel:
            return
        self.statusLabel.setText(text)
        self.statusLabel.setProperty("state", state)
        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

    def _setStatusOk(self, text: str) -> None:
        self._setStatus(text, "ok")

    def _setStatusBad(self, text: str) -> None:
        self._setStatus(text, "bad")

    def _setStatusRunning(self, text: str) -> None:
        self._setStatus(text, "running")

    # ------------------------------------------------------------------
    # Worker 事件回调
    # ------------------------------------------------------------------
    def _onSchemaReady(self) -> None:
        # 重建所有行(列定义可能变化)
        self._clearConditionRows()
        self._addConditionRow()
        self._updateDbStatus()

    def _onImported(self, rows: int) -> None:
        self._updateDbStatus()
        if rows > 0:
            logger.info(f"[HskCorpusBrowser] 语料导入完成, rows={rows}")
            InfoBar.success(
                title="导入完成",
                content=f"已导入 {rows:,} 行,可开始检索",
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )

    # ------------------------------------------------------------------
    # 搜索入口
    # ------------------------------------------------------------------
    def _onSearchClicked(self) -> None:
        if not self.model:
            return

        conditions = self._collectConditions()
        if not conditions:
            InfoBar.warning(
                title="无有效筛选条件",
                content="请至少在一行中填写检索条件",
                parent=self,
                duration=2000,
                position=InfoBarPosition.TOP,
            )
            return

        # PRD-005:新一次搜索 → 重置导出状态(避免用户在不同时段误用旧 conditions)
        self._lastConditions = list(conditions)
        self._lastSearchFinished = False
        if hasattr(self, "exportAllBtn"):
            self.exportAllBtn.setEnabled(False)

        # 取消旧 worker
        self.disposeWorker(waitMs=0)
        self._pullTimer.stop()
        self._lastSnapshotTotal = 0
        self._matchTotal = 0

        # 重置 Model
        self.model.reset()
        self._applyColumnVisibility()
        self._hideDetailDrawer()
        # 状态条描述:把所有条件的描述拼起来
        descList = [
            self._describeAppliedCondition(condition)
            for condition in conditions
        ]
        queryDesc = " 且 ".join(item for item in descList if item)
        self._updateConditionSummary(conditions)
        columns = [str(item.get("column", "")) for item in conditions]
        logger.info(
            f"[HskCorpusBrowser] 开始检索, conditions={len(conditions)}, "
            f"columns={columns}"
        )
        self.model.setLastQuery("(多条件)", queryDesc)
        self._searchStartTs = time.perf_counter()
        self._setStatusRunning("检索中")
        if self.elapsedLabel:
            self.elapsedLabel.setText(f"已应用 {len(conditions)} 个筛选条件")
        self._showEmptyState(
            "正在检索语料",
            "正在组合筛选条件并读取本地 HSK 语料库，请稍候。",
        )

        # 先拿真实总命中数(< 50ms,可主线程同步)
        try:
            self._matchTotal = int(self._service.countByConditions(conditions) or 0)
            if self._matchTotal > 0 and self.statusLabel:
                self._setStatusRunning(f"命中 {self._matchTotal:,} 条")
        except Exception as e:
            logger.warning(f"[HskCorpusBrowser] countByConditions 失败: {e}")

        worker = HskCorpusSearchWorker(
            dbPath=str(self._dbPath),
            pageSize=1000,
            parent=self,
            conditions=conditions,
        )
        worker.progress.connect(self._onWorkerProgress)
        worker.failed.connect(self._onWorkerFailed)
        worker.dataReady.connect(self._onWorkerDataReady)
        worker.finishedWithResult.connect(self._onWorkerFinished)
        self._currentWorker = worker
        self.startWorker(worker)

        # 启动节流拉取(60ms/次)
        self._pullTimer.start()

    # ------------------------------------------------------------------
    # 主线程节流拉取
    # ------------------------------------------------------------------
    def _onWorkerDataReady(self) -> None:
        self._pullSnapshotOnce()

    def _onPullSnapshot(self) -> None:
        self._pullSnapshotOnce()

    def _pullSnapshotOnce(self) -> None:
        worker = self._currentWorker
        if not worker:
            return
        try:
            rows, total, finished = worker.snapshot()
        except Exception as e:
            logger.error(f"[HskCorpusBrowser] snapshot 失败: {e}")
            return

        if total != self._lastSnapshotTotal:
            if self.model:
                self.model.setAllRows(rows[:_DISPLAY_LIMIT], total)
            self._lastSnapshotTotal = total
            if total > 0:
                self._showTableState()
            if total > self._matchTotal:
                self._matchTotal = total
            if self.statusLabel:
                if finished:
                    self._setStatusOk(f"命中 {self._matchTotal:,} 条")
                else:
                    self._setStatusRunning(f"已加载 {total:,} 条")

        if finished:
            self._pullTimer.stop()

    # ------------------------------------------------------------------
    # Worker 完成 / 失败
    # ------------------------------------------------------------------
    def _onWorkerProgress(self, pct: int, status: str) -> None:
        self._setStatusRunning(f"[{pct}%] {status}")

    def _onWorkerFinished(self, total) -> None:
        self._pullTimer.stop()
        self._pullSnapshotOnce()
        elapsedMs = (time.perf_counter() - self._searchStartTs) * 1000.0
        try:
            totalInt = int(total) if total is not None else 0
        except (TypeError, ValueError):
            totalInt = 0
        if totalInt > self._matchTotal:
            self._matchTotal = totalInt
        self._setStatusOk(f"命中 {self._matchTotal:,} 条")
        if self.elapsedLabel:
            previewCount = min(self._matchTotal, _DISPLAY_LIMIT)
            self.elapsedLabel.setText(
                f"耗时 {elapsedMs:.0f} ms · 共 {self._matchTotal:,} 条"
                f" · 当前预览 {previewCount:,} 条"
            )
        if self._matchTotal > 0:
            self._showTableState()
        else:
            self._showEmptyState(
                "未找到匹配语料",
                "请减少筛选条件，或放宽关键词与分数范围后重试。",
            )
        if self.tableView and self.model and self.model.columnCount() > 0:
            self._applyColumnVisibility()
        self._currentWorker = None

        # PRD-005:搜索成功后启用「导出所有命中作文」按钮
        self._lastSearchFinished = True
        if hasattr(self, "exportAllBtn") and self._matchTotal > 0:
            self.exportAllBtn.setEnabled(True)
        logger.info(
            f"[HskCorpusBrowser] 检索完成, hits={self._matchTotal}, "
            f"elapsedMs={elapsedMs:.1f}"
        )

    def _onWorkerFailed(self, errMsg: str) -> None:
        self._pullTimer.stop()
        self._currentWorker = None
        elapsedMs = (time.perf_counter() - self._searchStartTs) * 1000.0
        logger.error(
            f"[HskCorpusBrowser] 检索失败, elapsedMs={elapsedMs:.1f}: {errMsg}"
        )
        self._setStatusBad("检索失败")
        self._showEmptyState(
            "检索未完成",
            "请检查语料库状态后重试；若问题持续出现，请联系管理员。",
        )
        InfoBar.error(
            title="检索失败",
            content=errMsg,
            parent=self,
            duration=4000,
            position=InfoBarPosition.TOP,
        )

    # ------------------------------------------------------------------
    # PRD-005 导出所有命中作文(一体化对话框版本)
    # ------------------------------------------------------------------
    def _onExportAllClicked(self) -> None:
        """导出按钮槽函数:一体化对话框 → 后台 Worker。

        流程:
            1) 防御性检查(未检索 / 命中空)
            2) 流式拉 zwhao(从 hsk_corpus.db)
            3) 弹一体化对话框(目录/格式/范围 + 实时预览)
            4) 启动 Worker
        """
        from app.core.services.hsk_local_corpus_service import hskLocalCorpusService
        from app.core.services.hsk_corpus_service import hskCorpusService
        from app.view.widgets.hsk_corpus.hsk_corpus_export_dialog import (
            HskCorpusExportOptionsDialog,
        )
        from app.view.widgets.hsk_corpus.hsk_corpus_export_worker import (
            HskCorpusExportWorker,
        )

        # ---- 0. 防御性检查 ----
        if not self._lastSearchFinished or not self._lastConditions:
            InfoBar.warning(
                title="未检索",
                content="请先点「搜索」命中作文后再导出",
                parent=self,
                duration=2500,
                position=InfoBarPosition.TOP,
            )
            return
        if self._matchTotal <= 0:
            InfoBar.warning(
                title="命中为空",
                content="上一次搜索没有命中任何作文,无法导出",
                parent=self,
                duration=2500,
                position=InfoBarPosition.TOP,
            )
            return
        if not hskLocalCorpusService.isAvailable():
            InfoBar.error(
                title="本地镜像库不可用",
                content="请检查 datas/corpora/hsk_corpus_local.db 是否存在",
                parent=self,
                duration=3500,
                position=InfoBarPosition.TOP,
            )
            return

        # ---- 1. 流式拉 zwhao ----
        zwhaoList: List[str] = []
        try:
            for page in hskCorpusService.iterZwhaoByConditions(
                self._lastConditions, pageSize=2000
            ):
                zwhaoList.extend(page)
        except Exception as e:
            logger.error(f"[HskCorpusBrowser] 流式拉 zwhao 失败: {e}")
            InfoBar.error(
                title="导出启动失败",
                content=f"获取命中列表失败: {e}",
                parent=self,
                duration=3500,
                position=InfoBarPosition.TOP,
            )
            return

        if not zwhaoList:
            InfoBar.warning(
                title="无作文可导出",
                content="当前筛选条件下没有可导出的作文",
                parent=self,
                duration=2500,
                position=InfoBarPosition.TOP,
            )
            return

        # ---- 2. 一体化对话框 ----
        dlg = HskCorpusExportOptionsDialog(
            zwhaoList=zwhaoList,
            localAvailable=True,
            parent=self.window(),
        )
        if not dlg.exec():
            return  # 用户取消

        v = dlg.value
        from app.core.services import HSK_ESSAY_EXPORT_FEATURE, beginPaidMeteredAction

        transaction = beginPaidMeteredAction(
            self.window(),
            HSK_ESSAY_EXPORT_FEATURE,
            int(v["billedEssayCount"]),
            f"导出 {int(v['billedEssayCount']):,} 篇 HSK 作文",
            confirmedCost=int(v["quotedCost"]),
            showConfirmation=False,
        )
        if transaction is None:
            return
        self._exportBillingTransaction = transaction
        # ---- 3. 启动 Worker ----
        try:
            self._exportWorker = HskCorpusExportWorker(
                zwhaoList=v["zwhaoList"],
                outputDir=v["outputDir"],
                fileFormat=v["fileFormat"],
                skipMissingTitle=False,  # 作文母号(zwhao)是唯一标识,不再用 Title 过滤
                mergeMode=v["mergeMode"],
                mergeFileName=v["mergeFileName"],
                parent=self,
            )
        except Exception:
            transaction.refund()
            self._exportBillingTransaction = None
            raise
        self._exportWorker.progress.connect(self._onExportProgress)
        self._exportWorker.finishedWithResult.connect(self._onExportFinished)
        self._exportWorker.failed.connect(self._onExportFailed)
        # 保存输出目录(完成后用于「打开文件夹」)
        self._lastExportDir = v["outputDir"]
        self.exportAllBtn.setEnabled(False)
        self.exportAllBtn.setText("导出中...")
        try:
            self._exportWorker.start()
        except Exception:
            transaction.refund()
            self._exportBillingTransaction = None
            self._exportWorker = None
            raise

        InfoBar.info(
            title="开始导出",
            content=(
                f"后台导出 {v['previewTotal']:,} 篇 → "
                f"{v['fileFormat'].upper()}"
                + (
                    f" · 合并到 {v['mergeFileName']}.{v['fileFormat']}"
                    if v["mergeMode"]
                    else " · 分文件"
                )
            ),
            parent=self,
            duration=3000,
            position=InfoBarPosition.TOP,
        )

    def _onExportProgress(self, current: int, total: int) -> None:
        """导出进度回调。"""
        if self.statusLabel:
            self.statusLabel.setText(f"导出中 {current:,}/{total:,}")

    def _onExportFinished(
        self, successCount: int, skippedCount: int, failCount: int
    ) -> None:
        """导出完成回调:InfoBar + 「打开文件夹」按钮。"""
        self.exportAllBtn.setEnabled(True)
        self.exportAllBtn.setText("导出命中作文")
        if self.statusLabel:
            self.statusLabel.setText("就绪")

        billingSettled = True
        if self._exportBillingTransaction is not None:
            if successCount > 0:
                billingSettled = self._exportBillingTransaction.commit()
            else:
                self._exportBillingTransaction.refund()
            self._exportBillingTransaction = None

        content = (
            f"成功 {successCount:,} 篇 · 跳过 {skippedCount:,} 篇 · "
            f"失败 {failCount:,} 篇"
        )

        # 用 InfoBar.new 而非 .success,以便挂自定义按钮
        if not billingSettled:
            content += " · 文件已生成，但账单同步暂未完成"
        bar = InfoBar.new(
            InfoBarIcon.SUCCESS if billingSettled else InfoBarIcon.WARNING,
            title="导出完成" if billingSettled else "导出完成，账单待同步",
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            duration=6000,
            position=InfoBarPosition.TOP,
            parent=self,
        )

        # 「打开文件夹」按钮
        if getattr(self, "_lastExportDir", None):
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            openBtn = PushButton("打开文件夹", bar)
            exportDir = self._lastExportDir
            openBtn.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(exportDir))
            )
            bar.widgetLayout.addWidget(openBtn)
            bar.widgetLayout.addSpacing(8)

        logger.info(f"[HskCorpusBrowser] 导出完成: {content}")
        self._exportWorker = None

    def _onExportFailed(self, errorMsg: str) -> None:
        """导出失败回调。"""
        self.exportAllBtn.setEnabled(True)
        self.exportAllBtn.setText("导出命中作文")
        if self.statusLabel:
            self.statusLabel.setText("就绪")
        if self._exportBillingTransaction is not None:
            self._exportBillingTransaction.refund()
            self._exportBillingTransaction = None

        InfoBar.error(
            title="导出失败",
            content=errorMsg[:80],
            parent=self,
            duration=4000,
            position=InfoBarPosition.TOP,
        )
        logger.error(f"[HskCorpusBrowser] 导出失败: {errorMsg}")
        self._exportWorker = None

    # ------------------------------------------------------------------
    # 析构
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        try:
            self._pullTimer.stop()
            self.disposeWorker(waitMs=300)
        except Exception:
            pass
        # PRD-005:同步停止导出 worker(避免线程悬挂)
        try:
            if self._exportWorker is not None and self._exportWorker.isRunning():
                self._exportWorker.stop()
                # 给 worker 100ms 主动退出,不强等
                if not self._exportWorker.wait(100):
                    logger.warning("[HskCorpusBrowser] 导出 worker 未在 100ms 内退出")
        except Exception as e:
            logger.warning(f"[HskCorpusBrowser] 停止导出 worker 失败: {e}")
        if self._exportBillingTransaction is not None:
            self._exportBillingTransaction.refund()
            self._exportBillingTransaction = None
        super().closeEvent(event)
