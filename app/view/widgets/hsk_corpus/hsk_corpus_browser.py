# coding:utf-8
"""
HSK 语料检索主面板(qfluentwidgets 风格)
========================================

布局:
    ┌──────────────────────────────────────────────────┐
    │  📖 HSK 作文语料检索                               │
    │  独立 SQLite + 全 NOCASE 索引 · 子线程流式检索    │
    ├──────────────────────────────────────────────────┤
    │  🔍 [列:______] [关键词____________] [搜索] [⛏]   │
    ├──────────────────────────────────────────────────┤
    │  ┃ 命中 N 条 · 耗时 X ms                          │
    │  ┌────────────────────────────────────────────┐  │
    │  │ TableView (原生样式,不修改任何属性)        │  │
    │  └────────────────────────────────────────────┘  │
    │  db: ...                                          │
    └──────────────────────────────────────────────────┘

TableView 样式策略:
    - **不做任何属性调整**(不设 sizePolicy / minimumHeight / fixed row height
      / alternatingRowColors / 字体 / header 模式 等)
    - 完全依赖父布局分配的 stretch=1 + Qt 原生 sizeHint
    - 这样拖动窗口时不会出现「QTableView.sizeHint() 被重新计算并异常放大」的拉伸问题

线程修复要点:
    1. 子线程累积 rows(无 UI 信号载荷)
    2. 主线程 QTimer(60ms)节流拉取 snapshot → setAllRows
    3. dataReady() 信号只用来「请求下次拉取」,主线程不会因信号密集到达而卡顿
    4. 检索期间完全静默 Model,只有 setAllRows 时才 emit modelReset(最多 60ms/次)
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
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
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
    ToolButton,
    TableView,
)

from loguru import logger

from app.core.services.hsk_corpus_service import HskCorpusService
from app.view.widgets.freq_analyzer.worker_utils import WorkerMixin
from app.view.widgets.hsk_corpus.hsk_corpus_model import HskCorpusModel
from app.view.widgets.hsk_corpus.hsk_corpus_search_worker import (
    HskCorpusSearchWorker,
)


# 主线程拉取 worker snapshot 的节流间隔(60ms ≈ 16fps,流畅且不卡)
_UI_PULL_INTERVAL_MS = 60

# 表格单次最多向 UI 渲染的行数(后台仍累计全量,但 UI 只显示前 N 条,
# 既避免 viewport 渲染开销,又杜绝大列表触发布局异常撑高整窗)
_DISPLAY_LIMIT: int = 20


class HskCorpusBrowser(QWidget, WorkerMixin):
    """HSK 语料检索主面板(qfluentwidgets 风格)。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        QWidget.__init__(self, parent)
        WorkerMixin.__init__(self)
        self.setObjectName("HskCorpusBrowser")

        self._service = HskCorpusService.instance()
        from app.core.utils.config import cfg

        configured = cfg.HskCorpusDbPath.value
        if configured:
            self._service.setDbPath(configured)
        self._dbPath = self._service.dbPath

        # 状态
        self._searchStartTs: float = 0.0
        self._currentWorker: Optional[HskCorpusSearchWorker] = None
        # 真实总命中数(不限 20 条)— 用于状态条提示用户
        self._matchTotal: int = 0

        # UI 控件
        self.columnCombo: Optional[ComboBox] = None
        self.keywordEdit: Optional[SearchLineEdit] = None
        self.searchBtn: Optional[PrimaryPushButton] = None
        self.importBtn: Optional[ToolButton] = None
        self.refreshBtn: Optional[ToolButton] = None
        self.statusIcon: Optional[ToolButton] = None
        self.statusLabel: Optional[CaptionLabel] = None
        self.elapsedLabel: Optional[CaptionLabel] = None
        self.tableView: Optional[TableView] = None
        self.model: Optional[HskCorpusModel] = None
        self._dbPathLabel: Optional[CaptionLabel] = None
        self._tableCard: Optional[CardWidget] = None

        # 主线程节流拉取 timer
        self._pullTimer = QTimer(self)
        self._pullTimer.setInterval(_UI_PULL_INTERVAL_MS)
        self._pullTimer.timeout.connect(self._onPullSnapshot)
        self._lastSnapshotTotal: int = 0  # 上一轮 snapshot 的总行数

        self._initUi()
        self._populateColumns()
        self._updateDbStatus()

        # 订阅事件
        self._service.onSchemaReady(self._onSchemaReady)
        self._service.onImported(self._onImported)

    # ------------------------------------------------------------------
    # UI 构造
    # ------------------------------------------------------------------
    def _initUi(self) -> None:
        root = QVBoxLayout(self)
        # 边距统一固定,不在内容变化时伸缩
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(12)

        # ---------- 顶部标题区(高度固定) ----------
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
            "11337 条 HSK 作文语料 · 独立 SQLite + 全 NOCASE 索引 · "
            "无返回上限 · 子线程流式检索,UI 不卡顿",
            self,
        )
        subtitle.setStyleSheet("color: #666;")
        hLayout.addWidget(subtitle)

        headerCard.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        root.addWidget(headerCard)

        # ---------- 检索栏(高度固定) ----------
        searchCard = CardWidget(self)
        sLayout = QVBoxLayout(searchCard)
        sLayout.setContentsMargins(16, 14, 16, 14)
        sLayout.setSpacing(10)
        searchCard.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        row1 = QHBoxLayout()
        row1.setSpacing(10)

        colBox = QWidget(self)
        colL = QVBoxLayout(colBox)
        colL.setContentsMargins(0, 0, 0, 0)
        colL.setSpacing(2)
        colL.addWidget(CaptionLabel("检索列", self))
        self.columnCombo = ComboBox(self)
        self.columnCombo.setMinimumWidth(180)
        colL.addWidget(self.columnCombo)
        row1.addWidget(colBox)

        kwBox = QWidget(self)
        kwL = QVBoxLayout(kwBox)
        kwL.setContentsMargins(0, 0, 0, 0)
        kwL.setSpacing(2)
        kwL.addWidget(CaptionLabel("关键词", self))
        self.keywordEdit = SearchLineEdit(self)
        self.keywordEdit.setPlaceholderText("输入要检索的关键词(支持中英文)")
        self.keywordEdit.setClearButtonEnabled(True)
        self.keywordEdit.returnPressed.connect(self._onSearchClicked)
        kwL.addWidget(self.keywordEdit)
        row1.addWidget(kwBox, 1)

        sLayout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.searchBtn = PrimaryPushButton("搜索", self, FluentIcon.SEARCH)
        self.searchBtn.clicked.connect(self._onSearchClicked)
        row2.addWidget(self.searchBtn)

        self.importBtn = ToolButton(FluentIcon.ADD, self)
        self.importBtn.setToolTip("从 Excel 导入到 SQLite")
        self.importBtn.clicked.connect(self._onImportClicked)
        row2.addWidget(self.importBtn)

        self.refreshBtn = ToolButton(FluentIcon.SYNC, self)
        self.refreshBtn.setToolTip("刷新 db 状态")
        self.refreshBtn.clicked.connect(self._updateDbStatus)
        row2.addWidget(self.refreshBtn)

        row2.addStretch(1)

        self.statusIcon = ToolButton(FluentIcon.INFO, self)
        self.statusIcon.setEnabled(False)
        self.statusIcon.setToolTip("db 状态")
        row2.addWidget(self.statusIcon)

        sLayout.addLayout(row2)
        root.addWidget(searchCard)

        # ---------- 表格卡片(关键:固定高度) ----------
        self._tableCard = CardWidget(self)
        tLayout = QVBoxLayout(self._tableCard)
        tLayout.setContentsMargins(12, 12, 12, 12)
        tLayout.setSpacing(6)
        # 关键修复 3:卡片高度 Expanding,绝不向父布局反馈「内容高度」,
        # 否则拖动窗口时 TableView 的 heightHint 重算会让 stackedWidget
        # 反向撑大整窗。
        self._tableCard.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        stateRow = QHBoxLayout()
        stateRow.setSpacing(6)
        stateIcon = ToolButton(FluentIcon.HEART, self)
        stateIcon.setEnabled(False)
        stateRow.addWidget(stateIcon)
        self.statusLabel = StrongBodyLabel("就绪", self)
        self.statusLabel.setStyleSheet("color: #00b09c; font-weight: 600;")
        stateRow.addWidget(self.statusLabel)
        stateRow.addStretch(1)
        self.elapsedLabel = CaptionLabel("", self)
        self.elapsedLabel.setStyleSheet("color: #888;")
        stateRow.addWidget(self.elapsedLabel)
        tLayout.addLayout(stateRow)

        # 表格(完全保留 TableView 原生样式,不做任何属性调整,避免 sizeHint 重算
        # 导致拖动窗口时 heightHint 被异常放大)
        self.tableView = TableView(self)

        # 关键修复:Qt 的 QAbstractScrollArea 默认 sizeAdjustPolicy 是
        # AdjustToContentsOnFirstShow,会按「行数 × 行高 + 表头」算 heightHint;
        # 在 stackedWidget 子页面里,布局重排(比如拖窗口边缘)会让 heightHint
        # 触发反向布局,把整窗撑高去适应表格所有数据。
        # 显式设为 AdjustIgnored 后,TableView 不再参与布局的高度决策,
        # 多出的行由内部 verticalScrollBar 滚动,绝不影响外层窗口尺寸。
        self.tableView.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
        )
        # 滚动条按需出现(默认 Qt 在某些主题下会按行数自动腾出空间,这里强制固定)
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
    # 列名 / db 状态
    # ------------------------------------------------------------------
    def _populateColumns(self) -> None:
        if not self.columnCombo:
            return
        self.columnCombo.clear()
        headerMap = self._service.columnHeaderMap()
        for col in self._service.availableColumns():
            cn = headerMap.get(col, col)
            self.columnCombo.addItem(cn, userData=col)
        for i in range(self.columnCombo.count()):
            if self.columnCombo.itemData(i) == "作文题目":
                self.columnCombo.setCurrentIndex(i)
                break

    def _updateDbStatus(self) -> None:
        if not self.statusLabel:
            return
        if not self._dbPath.exists():
            self._setStatusBad("db 不存在,点击 [+] 导入 Excel")
            self.searchBtn.setEnabled(False)
            self._dbPathLabel.setText(str(self._dbPath))
            return
        n = self._service.rowCount()
        if n == 0:
            self._setStatusBad("db 已建表但无数据,点击 [+] 导入")
            self.searchBtn.setEnabled(False)
            self._dbPathLabel.setText(str(self._dbPath))
            return
        self._setStatusOk(f"已加载 {n:,} 行")
        self.searchBtn.setEnabled(True)
        self._dbPathLabel.setText(f"db: {self._dbPath}")

    def _setStatusOk(self, text: str) -> None:
        if self.statusLabel:
            self.statusLabel.setText(text)
            self.statusLabel.setStyleSheet("color: #00b09c; font-weight: 600;")
        if self.statusIcon:
            self.statusIcon.setIcon(FluentIcon.ACCEPT)

    def _setStatusBad(self, text: str) -> None:
        if self.statusLabel:
            self.statusLabel.setText(text)
            self.statusLabel.setStyleSheet("color: #c75050; font-weight: 600;")
        if self.statusIcon:
            self.statusIcon.setIcon(FluentIcon.CLOSE)

    def _setStatusRunning(self, text: str) -> None:
        if self.statusLabel:
            self.statusLabel.setText(text)
            self.statusLabel.setStyleSheet("color: #888; font-weight: 500;")
        if self.statusIcon:
            self.statusIcon.setIcon(FluentIcon.SYNC)

    # ------------------------------------------------------------------
    # Worker 事件回调
    # ------------------------------------------------------------------
    def _onSchemaReady(self) -> None:
        self._populateColumns()
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

    def _onImportClicked(self) -> None:
        from app.core.utils.config import cfg

        xlsxPath = cfg.HskCorpusXlsxPath.value
        InfoBar.info(
            title="数据导入",
            content=(
                "请在终端运行:python scripts/build_hsk_corpus_db.py "
                f'--xlsx "{xlsxPath}"'
            ),
            parent=self,
            duration=8000,
            position=InfoBarPosition.TOP,
        )

    # ------------------------------------------------------------------
    # 搜索入口
    # ------------------------------------------------------------------
    def _onSearchClicked(self) -> None:
        if not self.model:
            return
        keyword = (self.keywordEdit.text() or "").strip()
        if not keyword:
            InfoBar.warning(
                title="关键词为空",
                content="请输入要检索的关键词",
                parent=self,
                duration=2000,
                position=InfoBarPosition.TOP,
            )
            return
        colData = self.columnCombo.currentData()
        if not colData:
            return

        # 取消旧 worker
        self.disposeWorker(waitMs=0)
        # 停掉旧 timer(若还在跑)
        self._pullTimer.stop()
        self._lastSnapshotTotal = 0
        self._matchTotal = 0

        # 重置 Model
        self.model.reset()
        self.model.setLastQuery(colData, keyword)
        self._searchStartTs = time.perf_counter()
        self._setStatusRunning(f"正在检索:{colData} LIKE '%{keyword}%'")
        if self.elapsedLabel:
            self.elapsedLabel.setText("")

        # 先拿真实总命中数(走 service.countMatches,LIKE 全量 < 50ms,可主线程同步),
        # 用来在状态条上提示「共 N 条,仅显示前 20 条」。
        try:
            self._matchTotal = int(self._service.countMatches(colData, keyword) or 0)
            if self._matchTotal > 0:
                if self.statusLabel:
                    self.statusLabel.setText(
                        f"命中 {self._matchTotal:,} 条 · 仅显示前 {_DISPLAY_LIMIT} 条"
                    )
        except Exception as e:
            logger.warning(f"[HskCorpusBrowser] countMatches 失败: {e}")

        worker = HskCorpusSearchWorker(
            dbPath=str(self._dbPath),
            column=colData,
            keyword=keyword,
            pageSize=1000,
            parent=self,
        )
        worker.progress.connect(self._onWorkerProgress)
        worker.failed.connect(self._onWorkerFailed)
        worker.dataReady.connect(self._onWorkerDataReady)
        worker.finishedWithResult.connect(self._onWorkerFinished)
        self._currentWorker = worker
        self.startWorker(worker)

        # 启动节流拉取(60ms/次),即便 dataReady 没触发也会刷新
        self._pullTimer.start()

    # ------------------------------------------------------------------
    # 主线程节流拉取
    # ------------------------------------------------------------------
    def _onWorkerDataReady(self) -> None:
        """子线程攒了一页 → 请求主线程拉(信号零数据载荷)。

        主线程不直接处理此信号,而是依赖 _pullTimer.timeout 周期性拉取。
        此信号仅用于「触发立刻拉取一次」(缩短用户感知延迟)。
        """
        self._pullSnapshotOnce()

    def _onPullSnapshot(self) -> None:
        """QTimer 定时回调(每 60ms)。"""
        self._pullSnapshotOnce()

    def _pullSnapshotOnce(self) -> None:
        """从子线程拉一次快照 → 主线程 setAllRows(仅前 _DISPLAY_LIMIT 条)。"""
        worker = self._currentWorker
        if not worker:
            return
        # 子线程已结束,这次拉完就停 timer
        try:
            rows, total, finished = worker.snapshot()
        except Exception as e:
            logger.error(f"[HskCorpusBrowser] snapshot 失败: {e}")
            return

        if total != self._lastSnapshotTotal:
            # 只在总行数变了时才更新 UI(否则空触发不重绘)
            # UI 仅承载前 N 条,后台继续累积全量
            if self.model:
                self.model.setAllRows(rows[:_DISPLAY_LIMIT], total)
            self._lastSnapshotTotal = total
            # 真实总命中数取 max(snapshot total, countMatches)— 防御 countMatches 失败
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
        # 兜底:确保 timer 停掉,做最后一次刷新
        self._pullTimer.stop()
        self._pullSnapshotOnce()
        elapsedMs = (time.perf_counter() - self._searchStartTs) * 1000.0
        try:
            totalInt = int(total) if total is not None else 0
        except (TypeError, ValueError):
            totalInt = 0
        # 真实总命中数(可能 > 20)— 用于提示用户
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
        # 列宽只调一次(在 UI 不再被 setAllRows 打断时)
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
