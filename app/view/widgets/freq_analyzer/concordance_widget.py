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
import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QStackedWidget,
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
    Pivot,
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
from app.view.widgets.freq_analyzer.concordance_plot_widget import (
    ConcordancePlotCanvas,
    computeFileTokenCounts,
    extractHitPositions,
)
from app.view.widgets.freq_analyzer.result_summary import MetricColor
from app.core.utils import cfg, qconfig  # AI 解读配置（PRD-001 REQ-AI-001）

# AI 解读 Mixin（PRD-001 REQ-AI-001）
from app.view.widgets.freq_analyzer.ai_insight_mixin import AiInsightMixin
from app.view.widgets.freq_analyzer.resource_sink_mixin import ResourceSinkMixin
from app.core.models.project import RESOURCE_TYPE_KWIC

# P0-fix:统一使用 loguru,与项目其它模块保持一致
from app.core.utils import logger


# 节点词高亮颜色（柔和黄色背景）
_NODE_HIGHLIGHT_COLOR = QColor("#FFF7B0")


def _makeCleanSwitchButton(text: str, parent: QWidget) -> "SwitchButton":
    """SwitchButton 工厂：开关文字始终保持不变

    SwitchButton 默认在勾选后切换为内置的 "On"/"Off" 文本，
    这里把 on/off 文本固定为同一 text，避免用户混淆。
    """
    btn = SwitchButton(text, parent)
    btn.setOnText(text)
    btn.setOffText(text)
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

    def cancel(self) -> None:
        """请求取消任务(由 UI 线程调用)"""
        self.requestInterruption()

    def run(self):
        try:
            # P2-8 修复:阶段回调,worker → UI signal
            def _onProgress(stageMsg: str):
                if self.isInterruptionRequested():
                    return
                self.progress.emit(stageMsg)

            if self.isInterruptionRequested():
                return
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
                progressCallback=_onProgress,
            )
            if self.isInterruptionRequested():
                return
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
        - 显示清洗状态徽章（已启用 / 未启用但有规则 / 未配置）
    """

    def __init__(self, parent=None, corpusStore: Optional["QObject"] = None):
        super().__init__(parent)
        self._corpusStore: Optional[QObject] = corpusStore
        self._boundCorpusStore: Optional[QObject] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        self._titleLabel = StrongBodyLabel("语料来源", self)
        layout.addWidget(self._titleLabel)

        self._countLabel = CaptionLabel("未加载文件", self)
        self._countLabel.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._countLabel)

        # 清洗状态徽章(新建)
        self._cleanBadge = CaptionLabel("", self)
        self._cleanBadge.setWordWrap(True)
        self._cleanBadge.setStyleSheet("color: #888; font-size: 11px; padding: 2px 0;")
        layout.addWidget(self._cleanBadge)

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
        if self._boundCorpusStore is store:
            return
        oldStore = self._boundCorpusStore
        if oldStore is not None:
            for signal in (oldStore.textsChanged, oldStore.cleanRuleChanged):
                try:
                    signal.disconnect(self._refresh)
                except (RuntimeError, TypeError):
                    pass
        try:
            store.textsChanged.connect(self._refresh)
            store.cleanRuleChanged.connect(self._refresh)
            self._boundCorpusStore = store
        except AttributeError:
            pass

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        if self._corpusStore is None:
            self._countLabel.setText("未加载文件")
            self._cleanBadge.setText("")
            return
        try:
            n = self._corpusStore.fileCount()
            total = self._corpusStore.totalChars()
        except AttributeError:
            self._countLabel.setText("未加载文件")
            self._cleanBadge.setText("")
            return

        if n == 0:
            self._countLabel.setText("未加载文件")
            self._cleanBadge.setText("")
            return

        self._countLabel.setText(f"已加载 {n} 个文件，{total:,} 字符（原文）")
        self._refreshCleanBadge()

    def _refreshCleanBadge(self) -> None:
        """刷新清洗状态徽章

        三种状态:
            1) clean_enabled=True  → 绿色徽章,显示原文/清洗后字符对比
            2) clean_enabled=False 且有规则 → 黄色警告徽章
            3) 无规则 → 灰色提示徽章
        """
        if self._corpusStore is None:
            self._cleanBadge.setText("")
            return

        # 优先使用新 API;若 store 不支持(版本不一致)则降级
        statusFn = getattr(self._corpusStore, "cleaningStatus", None)
        cleanEnabledFn = getattr(self._corpusStore, "cleanEnabled", None)

        if statusFn is None:
            # 旧 store:仅显示开关状态
            enabled = bool(cleanEnabledFn()) if cleanEnabledFn else False
            if enabled:
                self._cleanBadge.setText("🟢 清洗已启用")
                self._cleanBadge.setStyleSheet(
                    "color: #2c8a4a; font-size: 11px; padding: 2px 0;"
                )
            else:
                self._cleanBadge.setText("⚪ 清洗未启用（将使用原文）")
                self._cleanBadge.setStyleSheet(
                    "color: #888; font-size: 11px; padding: 2px 0;"
                )
            return

        try:
            status = statusFn()
        except Exception:
            self._cleanBadge.setText("")
            return

        enabled = status["enabled"]
        hasRule = status.get("hasRule", False)
        raw = status.get("rawChars", 0)
        eff = status.get("effectiveChars")

        if enabled:
            if eff is None:
                # 缓存尚未预热(后台清洗进行中)或无规则
                coverage = status.get("cacheCoverage")
                if (
                    coverage is not None
                    and coverage.get("total", 0) > 0
                    and coverage.get("coverage", 1.0) < 1.0
                ):
                    pct = int(coverage["coverage"] * 100)
                    text = f"⏳ 清洗后台预热中…  {coverage['cached']}/{coverage['total']} 文件已就绪 ({pct}%)"
                    color = "#1677ff"
                else:
                    text = "🟢 清洗已启用"
                    color = "#2c8a4a"
            else:
                saved = raw - eff
                if saved > 0:
                    text = (
                        f"🟢 清洗已启用 → {eff:,} 字符"
                        f"（节省 {saved:,} 字符 / {raw:,}）"
                    )
                else:
                    text = f"🟢 清洗已启用 → {eff:,} 字符（与原文相同）"
                color = "#2c8a4a"
        elif hasRule:
            # ⚠️ 关键状态:有规则但开关关着 — 容易被忽视
            text = (
                "⚠ 清洗规则已配置但未启用 → 当前使用原文。"
                "请到「语料导入与清洗」开启「启用清洗」开关。"
            )
            color = "#c97a00"
        else:
            text = "ℹ 未配置清洗规则,使用原文分析。"
            color = "#888"

        self._cleanBadge.setText(text)
        self._cleanBadge.setStyleSheet(
            f"color: {color}; font-size: 11px; padding: 2px 0;"
        )


# ===========================================================================
# 主面板
# ===========================================================================
class ConcordanceWidget(AiInsightMixin, ResourceSinkMixin, QWidget):
    # PRD-002 资源归档配置(REQ-PROJ-001)
    _RESOURCE_TYPE = RESOURCE_TYPE_KWIC
    _RESOURCE_TITLE_PREFIX = "KWIC 检索"
    """语境分析（KWIC）主面板

    继承 AiInsightMixin 提供「AI 解读」抽屉能力
    """

    _AI_INSIGHT_PANEL_NAME = "KWIC 检索"
    _AI_INSIGHT_TYPE = "kwic"

    def __init__(self, parent=None, corpusStore=None):
        super().__init__(parent)
        # 与 FreqAnalyzerWidget 共享：CorpusStore 为权威，本地仅做缓存
        from app.view.freq_analyzer_interface import CorpusStore  # 局部避免循环

        self._corpusStore: Optional[CorpusStore] = corpusStore
        self._boundCorpusStore: Optional[CorpusStore] = None
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
            self._onCorpusChanged()
            return
        self._corpusStore = store
        self._bindCorpusStore(store)
        self._onCorpusChanged()

    def _bindCorpusStore(self, store) -> None:
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
        self.fileToText = {}
        # 语料/规则变更 → 清空当前 KWIC 结果与二次检索历史
        self._currentResult = None
        if hasattr(self, "resultTable"):
            self.resultTable.setRowCount(0)
        if hasattr(self, "_resultSummary"):
            self._resultSummary.setPlaceholder("语料已变更，请重新检索")
        if hasattr(self, "statusLabel"):
            self.statusLabel.setText("就绪")
        self._secondaryStack = []
        # 切换回表格视图
        if hasattr(self, "_viewPivot"):
            self._viewPivot.setCurrentItem("table")
        # _updateFileCount 已废弃：原用于更新页面底部 fileCountLabel，
        # 该 UI 元素已被顶部 CorpusStatusCard 替代。

    def _reloadEffectiveTexts(self) -> bool:
        if self._corpusStore is None:
            return True
        try:
            coverage = self._corpusStore.cacheCoverage()
            if self._corpusStore.cleanEnabled and coverage["coverage"] < 1.0:
                _showInfoBar(
                    "info", "语料准备中", "清洗缓存完成后即可检索", self
                )
                return False
            self.fileToText = self._corpusStore.effectiveTextsFromCacheOnly()
            return True
        except Exception as exc:
            logger.exception(f"[ConcordanceWidget] 读取语料失败: {exc}")
            _showInfoBar("error", "读取失败", str(exc), self)
            return False

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

        scrollLayout.addWidget(self._buildSearchCard())
        scrollLayout.addWidget(self._buildResultCard())
        scrollLayout.addStretch(1)

    def _buildSearchCard(self) -> CardWidget:
        """检索参数卡片"""
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(StrongBodyLabel("检索参数", card))

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

        # AI 解读按钮（PRD-001 REQ-AI-001）— 通过 Mixin 一行接入
        # 统一为 PrimaryPushButton,放在「开始检索」旁边
        self._aiInsightBtn = PrimaryPushButton("AI 解读", card)
        self._aiInsightBtn.setIcon(FluentIcon.HEART)
        self.setupAiInsightButton(self._aiInsightBtn)
        btnRow.addWidget(self._aiInsightBtn)

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
        """结果列表卡片（含表格视图 / Plot 视图切换）"""
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # 标题行 + 视图切换 Pivot
        titleRow = QHBoxLayout()
        titleRow.addWidget(StrongBodyLabel("检索结果", card))
        titleRow.addStretch()
        self._viewPivot = Pivot(card)
        self._viewPivot.addItem(routeKey="table", text="表格视图")
        self._viewPivot.addItem(routeKey="plot", text="Plot视图")
        self._viewPivot.setCurrentItem("table")
        self._viewPivot.currentItemChanged.connect(self._onViewTabChanged)
        titleRow.addWidget(self._viewPivot)
        layout.addLayout(titleRow)

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

        # 视图堆栈
        self._viewStack = QStackedWidget(card)

        # --- 页 0: 表格视图 ---
        tablePage = QWidget()
        tableLayout = QVBoxLayout(tablePage)
        tableLayout.setContentsMargins(0, 0, 0, 0)
        tableLayout.setSpacing(0)

        self.resultTable = ProRoundTableWidget(tablePage)
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
        tableLayout.addWidget(self.resultTable)
        self._viewStack.addWidget(tablePage)

        # --- 页 1: Plot 视图 ---
        self._plotCanvas = ConcordancePlotCanvas(self)
        self._viewStack.addWidget(self._plotCanvas)

        layout.addWidget(self._viewStack, 1)

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
                logger.error(f"[ConcordanceWidget] 读取 {f} 失败: {e}")
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
        """关闭前取消后台任务,避免线程悬挂或泄漏(P0-fix)

        关键修复:wait() 之后必须调用 deleteLater(),让 Qt 在事件循环中
        安全释放 QThread 资源,避免旧 worker 对象泄漏。
        """
        worker = self._worker
        if worker is not None:
            try:
                if hasattr(worker, "cancel"):
                    worker.cancel()
                if worker.isRunning():
                    worker.wait(2000)
            except Exception:
                pass
            worker.deleteLater()
            self._worker = None
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
        # 若当前在 Plot 视图，同步刷新
        if (
            getattr(self, "_viewPivot", None)
            and self._viewPivot.currentItem() == "plot"
        ):
            self._refreshPlotView()

    # ------------------------------------------------------------------
    # 视图切换（表格 / Plot）
    # ------------------------------------------------------------------
    def _onViewTabChanged(self, routeKey: str) -> None:
        """Pivot 切换：「表格视图」↔「Plot视图」"""
        if routeKey == "table":
            self._viewStack.setCurrentIndex(0)
        elif routeKey == "plot":
            self._viewStack.setCurrentIndex(1)
            self._refreshPlotView()

    def _refreshPlotView(self) -> None:
        """根据当前检索结果渲染 Plot 视图。

        后台计算各文件的 token 数与命中位置，然后交给 ConcordancePlotCanvas 渲染。
        token 数计算复用 ConcordanceEngine 的分词器，确保与检索时的位置索引一致。
        """
        if not self._currentResult or not self._currentResult.hits:
            return
        if not self.fileToText:
            return

        # 提取命中位置
        fileToPositions, searchWord = extractHitPositions(self._currentResult)

        # 计算各文件 token 数（复用 engine 分词）
        fileToTokenCounts = computeFileTokenCounts(self.fileToText, self._engine)

        # 渲染
        self._plotCanvas.render(fileToPositions, fileToTokenCounts, searchWord)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def _runSearch(self):
        word = self.searchEdit.text().strip()
        if not word:
            _showInfoBar("warning", "提示", "请输入节点词", self, duration=2000)
            return
        if not self._reloadEffectiveTexts():
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
        logger.error(f"[ConcordanceWidget] 检索失败: {err}")
        _showInfoBar("error", "检索失败", err, self, duration=3000)

    def _onSearchFinished(self, result: ConcordanceResult):
        self.searchBtn.setEnabled(True)
        self._currentResult = result
        self._refreshTableFromResult(result)
        # AI 解读入口：有命中时启用
        if hasattr(self, "_aiInsightBtn"):
            self._aiInsightBtn.setEnabled(result is not None and len(result.hits) > 0)
        logger.info(
            f"[ConcordanceWidget] 节点词={result.searchWord!r} "
            f"命中={result.totalMatches} 展示={len(result.hits)}"
        )

        # PRD-002:归档到当前激活项目(若有)
        self.notifyResourceCreated()

    # ------------------------------------------------------------------
    # PRD-002 资源归档钩子(ResourceSinkMixin)
    # ------------------------------------------------------------------
    def _collectResourcePayload(self):
        """KWIC 检索结果 → 项目资源 payload"""
        result = getattr(self, "_currentResult", None)
        if result is None or len(getattr(result, "hits", [])) == 0:
            return None
        try:
            totalMatches = result.totalMatches
            shown = len(result.hits)
            query = result.searchWord
            summary = (
                f"KWIC 检索「{query}」:命中 {totalMatches:,} 条," f"本次展示 {shown} 条"
            )
        except Exception:
            summary = f"KWIC 检索结果"
        # 保留前 5 条 sample
        try:
            samples = []
            for hit in result.hits[:5]:
                samples.append(
                    {
                        "left": hit.leftText,
                        "node": hit.nodeText,
                        "right": hit.rightText,
                        "source": hit.sourceFile,
                    }
                )
        except Exception:
            samples = []
        snapshotData = {
            "query": result.searchWord,
            "totalMatches": result.totalMatches,
            "samples": samples,
        }
        parameters = {
            "query": result.searchWord,
            "leftWidth": self.leftSpin.value(),
            "rightWidth": self.rightSpin.value(),
            "sortMode": getattr(self, "sortMode", "left"),
        }
        return {
            "title": f"KWIC「{result.searchWord}」({self._buildDefaultTitle().split(' ', 1)[1]})",
            "summary": summary[:200],
            "parameters": parameters,
            "snapshotData": snapshotData,
        }

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
            logger.error(f"[ConcordanceWidget] 扩展失败: {e}")
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
                logger.error(f"[ConcordanceWidget] TXT 导出失败: {e}")
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
                logger.error(f"[ConcordanceWidget] CSV 导出失败: {e}")
                _showInfoBar("error", "导出失败", str(e), self, duration=3000)

    # ------------------------------------------------------------------
    # AI 解读协议（PRD-001 REQ-AI-001）— 由 AiInsightMixin 调用
    # ------------------------------------------------------------------
    def _aiHasResult(self) -> bool:
        return self._currentResult is not None and bool(self._currentResult.hits)

    def _aiCollectExplainArgs(self):
        if not self._aiHasResult():
            return None
        query = self._currentResult.searchWord or self.searchEdit.text().strip()
        return (
            "kwic",
            {
                "hits": self._currentResult.hits,
                "query": query,
            },
        )

    def _collectCorpusMeta(self) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "corpusName": "当前语料",
            "fileCount": 0,
            "totalChars": 0,
        }
        if self._corpusStore is not None:
            try:
                meta["fileCount"] = self._corpusStore.fileCount()
                meta["totalChars"] = self._corpusStore.totalChars()
            except Exception:
                pass
            try:
                from pathlib import Path as _Path

                meta["corpusName"] = _Path(self._corpusStore.dbPath).stem or "当前语料"
            except Exception:
                pass
        return meta


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
