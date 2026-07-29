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
    - 作文题目    → 文本(关键词模糊)
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
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QHBoxLayout,
    QSizePolicy,
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
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    StrongBodyLabel,
    TitleLabel,
    ToolButton,
    TableView,
)

from loguru import logger

from app.core.services.hsk_corpus_service import HskCorpusService
from app.core.utils.constant import hskCountryDict
from app.view.widgets.freq_analyzer.worker_utils import WorkerMixin
from app.view.widgets.hsk_corpus.hsk_corpus_model import HskCorpusModel
from app.view.widgets.hsk_corpus.hsk_corpus_search_worker import (
    HskCorpusSearchWorker,
)


# 主线程拉取 worker snapshot 的节流间隔(60ms ≈ 16fps)
_UI_PULL_INTERVAL_MS = 60

# 表格单次最多向 UI 渲染的行数(后台仍累计全量,但 UI 只显示前 N 条)
_DISPLAY_LIMIT: int = 20

# ------------------------------------------------------------------
# 绑定的 HSK 语料库文件路径(内部实现细节,不向用户暴露)
# ------------------------------------------------------------------
# 本页面的所有检索/统计都围绕这一个文件展开。
# 注意:此路径不应在 UI 文本中展示给用户。
BOUND_HSK_DB_PATH = Path(r"e:\Prismatica\datas\corpora\hsk_corpus.db")


# ======================================================================
# 单条筛选条件行(列选择 + 动态输入区 + 删除按钮)
# ======================================================================
class _ConditionRow(QWidget):
    """单条筛选条件行。

    内部根据当前列类型显示不同的输入区(关键词 / 国籍 / 证书级别 / 分数区间)。
    切列时,旧输入区被销毁,新输入区被创建。

    Signals:
        removed():   用户点击删除按钮 → 主控件移除本行
        changed():   列切换 / 输入变化时发出(用于父级响应)
    """

    removed = Signal()
    changed = Signal()

    # ----- 列类型枚举 -----
    COL_TYPE_TEXT = "text"
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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 检索列下拉
        self.columnCombo = ComboBox(self)
        self.columnCombo.setMinimumWidth(150)
        for col in self._availableColumns:
            self.columnCombo.addItem(col, userData=col)
        # 显式设默认列为"国籍"(创建 / 重建行时,保证输入区是 ComboBox)
        if self._currentColumn:
            for i in range(self.columnCombo.count()):
                if self.columnCombo.itemData(i) == self._currentColumn:
                    self.columnCombo.setCurrentIndex(i)
                    break
        self.columnCombo.currentIndexChanged.connect(self._onColumnChanged)
        layout.addWidget(self.columnCombo)

        # 输入区容器(包装动态输入控件,布局上保持位置稳定)
        self._inputContainer = QWidget(self)
        icL = QHBoxLayout(self._inputContainer)
        icL.setContentsMargins(0, 0, 0, 0)
        icL.setSpacing(8)
        layout.addWidget(self._inputContainer, 1)

        # 删除按钮
        self.removeBtn = ToolButton(FluentIcon.CLOSE, self)
        self.removeBtn.setToolTip("删除此筛选条件")
        self.removeBtn.clicked.connect(self.removed)
        layout.addWidget(self.removeBtn)

        # 显式触发一次,初始化输入区(避免依赖 currentIndexChanged 在初始化时是否触发)
        self._onColumnChanged(self.columnCombo.currentIndex())

    # ------------------------------------------------------------------
    # 列类型判定
    # ------------------------------------------------------------------
    def _getColumnType(self, col: str) -> str:
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
            layout.addWidget(self.keywordEdit)
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
            layout.addWidget(self.keywordEdit, 1)
        elif colType == self.COL_TYPE_CERT:
            self.certCombo = ComboBox(self._inputContainer)
            self.certCombo.setMinimumWidth(160)
            self.certCombo.addItems(["A", "B", "C", "无"])
            layout.addWidget(self.certCombo)
        elif colType == self.COL_TYPE_SCORE:
            self.scoreNoMinCheck = CheckBox("无下界", self._inputContainer)
            self.scoreNoMinCheck.setChecked(True)
            layout.addWidget(self.scoreNoMinCheck)
            self.scoreMinBox = CompactSpinBox(self._inputContainer)
            self.scoreMinBox.setRange(0, 150)
            self.scoreMinBox.setValue(0)
            layout.addWidget(self.scoreMinBox)
            sep = BodyLabel("—", self._inputContainer)
            sep.setStyleSheet("color: #666; font-weight: 600;")
            layout.addWidget(sep)
            self.scoreMaxBox = CompactSpinBox(self._inputContainer)
            self.scoreMaxBox.setRange(0, 150)
            self.scoreMaxBox.setValue(100)
            layout.addWidget(self.scoreMaxBox)
            self.scoreNoMaxCheck = CheckBox("无上界", self._inputContainer)
            self.scoreNoMaxCheck.setChecked(True)
            layout.addWidget(self.scoreNoMaxCheck)
            self.scoreNoMinCheck.stateChanged.connect(
                lambda _s: self._updateScoreSpinEnabled()
            )
            self.scoreNoMaxCheck.stateChanged.connect(
                lambda _s: self._updateScoreSpinEnabled()
            )
            self._updateScoreSpinEnabled()
        layout.addStretch(1)

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
        # 文本 / 国籍 / 证书级别 → 都用 keyword 字段(LIKE 模糊)
        # 国籍已改为自由文本(LineEdit),走 keywordEdit 读取;
        # 证书级别保留 ComboBox 选项。
        if ctype == self.COL_TYPE_CERT and self.certCombo:
            keyword = self.certCombo.currentText().strip()
        elif self.keywordEdit:
            keyword = (self.keywordEdit.text() or "").strip()
        else:
            keyword = ""
        # 「证书级别 = 无」是有效筛选条件:db 中 '无' 是真实字符串,
        # 走 LIKE '%无%' 即可命中;哨兵 `__EMPTY__` 由 service 兜底匹配 NULL/空串
        if not keyword:
            return None  # 空条件跳过
        return {
            "type": ctype,
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

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        QWidget.__init__(self, parent)
        WorkerMixin.__init__(self)
        self.setObjectName("HskCorpusBrowser")

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

        # 条件行列表(动态增删)
        self._conditionRows: List[_ConditionRow] = []
        self._rowsContainer: Optional[QVBoxLayout] = None

        # 控件引用
        self.addConditionBtn: Optional[ToolButton] = None
        self.searchBtn: Optional[PrimaryPushButton] = None
        self.statusLabel: Optional[StrongBodyLabel] = None
        self.elapsedLabel: Optional[CaptionLabel] = None
        self.tableView: Optional[TableView] = None
        self.model: Optional[HskCorpusModel] = None
        self._dbPathLabel: Optional[CaptionLabel] = None
        self._tableCard: Optional[CardWidget] = None

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
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(12)

        # ---------- 顶部标题区 ----------
        headerCard = QWidget(self)
        hLayout = QVBoxLayout(headerCard)
        hLayout.setContentsMargins(0, 0, 0, 0)
        hLayout.setSpacing(2)

        titleRow = QHBoxLayout()
        titleRow.setSpacing(8)
        titleIcon = ToolButton(FluentIcon.DICTIONARY, self)
        titleIcon.setEnabled(False)
        titleIcon.setFixedSize(28, 28)
        titleRow.addWidget(titleIcon)
        title = TitleLabel("HSK 作文语料检索", self)
        titleRow.addWidget(title)
        titleRow.addStretch(1)
        hLayout.addLayout(titleRow)

        subtitle = CaptionLabel(
            "多条件组合检索(AND) · 独立 SQLite + 全 NOCASE 索引 · 子线程流式",
            self,
        )
        subtitle.setStyleSheet("color: #666;")
        hLayout.addWidget(subtitle)

        headerCard.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        root.addWidget(headerCard)

        # ---------- 检索栏 ----------
        searchCard = CardWidget(self)
        sLayout = QVBoxLayout(searchCard)
        sLayout.setContentsMargins(16, 14, 16, 14)
        sLayout.setSpacing(10)
        # 检索栏高度 Preferred,行数动态变化时不会撑大主窗
        searchCard.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        searchCard.setMaximumHeight(380)

        # 条件行容器
        rowsBox = QWidget(searchCard)
        rowsOuter = QVBoxLayout(rowsBox)
        rowsOuter.setContentsMargins(0, 0, 0, 0)
        rowsOuter.setSpacing(6)
        self._rowsContainer = QVBoxLayout()
        self._rowsContainer.setContentsMargins(0, 0, 0, 0)
        self._rowsContainer.setSpacing(6)
        # 让条件行自适应行内输入区高度
        rowsOuter.addLayout(self._rowsContainer)
        rowsOuter.addStretch(1)
        sLayout.addWidget(rowsBox)

        # 默认添加一行(避免界面空白)
        self._addConditionRow()

        # 操作行:添加条件 + 搜索
        actionRow = QHBoxLayout()
        actionRow.setSpacing(8)
        self.addConditionBtn = ToolButton(FluentIcon.ADD, self)
        self.addConditionBtn.setToolTip("添加一条筛选条件")
        self.addConditionBtn.clicked.connect(self._onAddConditionClicked)
        actionRow.addWidget(self.addConditionBtn)

        addCaption = CaptionLabel("添加筛选条件(条件之间为 AND)", self)
        addCaption.setStyleSheet("color: #888;")
        actionRow.addWidget(addCaption)
        actionRow.addStretch(1)

        self.searchBtn = PrimaryPushButton("搜索", self, FluentIcon.SEARCH)
        self.searchBtn.clicked.connect(self._onSearchClicked)
        actionRow.addWidget(self.searchBtn)

        sLayout.addLayout(actionRow)
        root.addWidget(searchCard)

        # ---------- 表格卡片(关键:固定高度) ----------
        self._tableCard = CardWidget(self)
        tLayout = QVBoxLayout(self._tableCard)
        tLayout.setContentsMargins(12, 12, 12, 12)
        tLayout.setSpacing(6)
        self._tableCard.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        stateRow = QHBoxLayout()
        stateRow.setSpacing(6)
        self.statusLabel = StrongBodyLabel("就绪", self)
        self.statusLabel.setStyleSheet("color: #00b09c; font-weight: 600;")
        stateRow.addWidget(self.statusLabel)
        stateRow.addStretch(1)
        self.elapsedLabel = CaptionLabel("", self)
        self.elapsedLabel.setStyleSheet("color: #888;")
        stateRow.addWidget(self.elapsedLabel)
        tLayout.addLayout(stateRow)

        self.tableView = TableView(self)
        self.tableView.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
        )
        self.tableView.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tableView.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self.model = HskCorpusModel(self.tableView)
        self.model.setHeaderMap(self._service.columnHeaderMap())
        self.tableView.setModel(self.model)
        tLayout.addWidget(self.tableView, 1)

        self._dbPathLabel = CaptionLabel("", self)
        self._dbPathLabel.setStyleSheet("color: #999;")
        self._dbPathLabel.setWordWrap(True)
        self._dbPathLabel.setMaximumHeight(36)
        tLayout.addWidget(self._dbPathLabel)

        root.addWidget(self._tableCard, 1)

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
        # ---- 路径不存在 → 通用提示,不向用户暴露文件位置 ----
        if not self._dbPath.exists():
            self._setStatusBad("语料库未就绪")
            if self.searchBtn:
                self.searchBtn.setEnabled(False)
            if self._dbPathLabel:
                self._dbPathLabel.setText("⚠ 语料库文件缺失,请联系管理员补全数据。")
            InfoBar.error(
                title="语料库未找到",
                content="语料库文件缺失,请联系管理员补全数据后重试。",
                parent=self,
                duration=4000,
                position=InfoBarPosition.TOP,
            )
            return
        # ---- 文件存在但无数据 ----
        n = self._service.rowCount()
        if n == 0:
            self._setStatusBad("语料库暂无数据")
            if self.searchBtn:
                self.searchBtn.setEnabled(False)
            if self._dbPathLabel:
                self._dbPathLabel.setText("⚠ 语料库为空,请等待数据导入完成后重试。")
            return
        # ---- schema 校验(列是否齐全)----
        cols = self._service.availableColumns()
        if not cols:
            self._setStatusBad("语料库结构异常")
            if self.searchBtn:
                self.searchBtn.setEnabled(False)
            return
        # ---- 一切正常 ----
        self._setStatusOk(f"已加载 {n:,} 条语料")
        if self.searchBtn:
            self.searchBtn.setEnabled(True)
        if self._dbPathLabel:
            self._dbPathLabel.setText("")

    def _setStatusOk(self, text: str) -> None:
        if self.statusLabel:
            self.statusLabel.setText(text)
            self.statusLabel.setStyleSheet("color: #00b09c; font-weight: 600;")

    def _setStatusBad(self, text: str) -> None:
        if self.statusLabel:
            self.statusLabel.setText(text)
            self.statusLabel.setStyleSheet("color: #c75050; font-weight: 600;")

    def _setStatusRunning(self, text: str) -> None:
        if self.statusLabel:
            self.statusLabel.setText(text)
            self.statusLabel.setStyleSheet("color: #888; font-weight: 500;")

    # ------------------------------------------------------------------
    # Worker 事件回调
    # ------------------------------------------------------------------
    def _onSchemaReady(self) -> None:
        # 重建所有行(列定义可能变化)
        for r in list(self._conditionRows):
            self._removeConditionRow(r)
        self._addConditionRow()
        self._updateDbStatus()

    def _onImported(self, rows: int) -> None:
        self._updateDbStatus()
        if rows > 0:
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

        # 取消旧 worker
        self.disposeWorker(waitMs=0)
        self._pullTimer.stop()
        self._lastSnapshotTotal = 0
        self._matchTotal = 0

        # 重置 Model
        self.model.reset()
        # 状态条描述:把所有条件的描述拼起来
        descList = [r.describe() for r in self._conditionRows if r.describe()]
        queryDesc = " AND ".join(descList)
        self.model.setLastQuery("(多条件)", queryDesc)
        self._searchStartTs = time.perf_counter()
        self._setStatusRunning(f"正在检索:{queryDesc}")
        if self.elapsedLabel:
            self.elapsedLabel.setText("")

        # 先拿真实总命中数(< 50ms,可主线程同步)
        try:
            self._matchTotal = int(self._service.countByConditions(conditions) or 0)
            if self._matchTotal > 0 and self.statusLabel:
                self.statusLabel.setText(
                    f"命中 {self._matchTotal:,} 条 · 仅显示前 {_DISPLAY_LIMIT} 条"
                )
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
            if total > self._matchTotal:
                self._matchTotal = total
            if self.statusLabel:
                displayHint = (
                    f" · 仅显示前 {_DISPLAY_LIMIT} 条"
                    if self._matchTotal > _DISPLAY_LIMIT
                    else ""
                )
                if finished:
                    self._setStatusOk(f"命中 {self._matchTotal:,} 条{displayHint}")
                else:
                    self.statusLabel.setText(
                        f"已加载 {self._matchTotal:,} 条(检索中){displayHint}"
                    )

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
        displayHint = (
            f" · 仅显示前 {_DISPLAY_LIMIT} 条"
            if self._matchTotal > _DISPLAY_LIMIT
            else ""
        )
        self._setStatusOk(f"命中 {self._matchTotal:,} 条{displayHint}")
        if self.elapsedLabel:
            self.elapsedLabel.setText(
                f"耗时 {elapsedMs:.0f} ms · 共 {self._matchTotal:,} 条"
            )
        if self.tableView and self.model and self.model.columnCount() > 0:
            self.tableView.resizeColumnsToContents()
            for col in range(self.model.columnCount()):
                if self.tableView.columnWidth(col) > 280:
                    self.tableView.setColumnWidth(col, 280)
        self._currentWorker = None

    def _onWorkerFailed(self, errMsg: str) -> None:
        self._pullTimer.stop()
        self._currentWorker = None
        logger.error(f"[HskCorpusBrowser] 检索失败: {errMsg}")
        self._setStatusBad(f"检索失败:{errMsg}")
        InfoBar.error(
            title="检索失败",
            content=errMsg,
            parent=self,
            duration=4000,
            position=InfoBarPosition.TOP,
        )

    # ------------------------------------------------------------------
    # 析构
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        try:
            self._pullTimer.stop()
            self.disposeWorker(waitMs=300)
        except Exception:
            pass
        super().closeEvent(event)
