# coding: utf-8
"""句法依存图 UI 组件 — 需求 §2.5.3 (FR-DEP-001 ~ FR-DEP-005)

子页面:
    1. 句输入框(支持多句,自动切分)
    2. 「开始分析」按钮(PrimaryPushButton)
    3. 结果摘要(后端 / 句子数 / 节点数 / 边数)
    4. 双视图切换:树状图 + 弧状图
    5. 当前句子选择下拉框(多句时切换)
    6. 悬停交互(节点高亮 / 标签说明)
    7. 导出:PNG / SVG / CoNLL-U

架构:
    - 引擎层: dependency_engine.DependencyParser(可插拔)
    - UI 层:   DependencyWidget(纯渲染,不依赖具体后端)
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.patches import FancyArrowPatch

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    StrongBodyLabel,
    SubtitleLabel,
)

from app.core.models.project import RESOURCE_TYPE_DEPENDENCY
from app.view.widgets.freq_analyzer.dependency_engine import (
    DependencyParse,
    DependencyParser,
    DepToken,
    getDefaultParser,
    splitSentences,
    toConllU,
)
from app.view.widgets.freq_analyzer.ui_helpers import _showInfoBar
from app.view.widgets.freq_analyzer.ai_insight_mixin import AiInsightMixin
from app.view.widgets.freq_analyzer.resource_sink_mixin import ResourceSinkMixin
from app.view.widgets.prismatica_theme import setThemeRole, shellPalette

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from app.core.utils import logger
from app.core.services import beginPaidAnalysisExport


# ---------------------------------------------------------------------------
# 依存标签中文说明(FR-DEP-004 悬停提示)
# ---------------------------------------------------------------------------
DEPREL_DESCRIPTIONS_ZH: Dict[str, str] = {
    "ROOT": "根节点 — 句子的核心谓词",
    "SBV": "主语(subject)",
    "VOB": "宾语(object)",
    "IOB": "间接宾语(indirect object)",
    "ATT": "定语(attribute)",
    "ADV": "状语(adverbial)",
    "CMP": "补语(complement)",
    "COO": "并列(coordinate)",
    "HED": "核心(head)",
    "MT": "标记/助词",
    "DE": "依存关系(默认)",
    "PUNCT": "标点符号",
    "CC": "并列连词",
    "AUX": "助动词",
    "AP": "同位语",
    "DEPRL_UNKNOWN": "未识别关系",
}


# ---------------------------------------------------------------------------
# 后台分析 Worker
# ---------------------------------------------------------------------------
class DependencyAnalysisWorker(QThread):
    """在后台线程执行句法依存分析,避免 UI 卡顿"""

    progress = Signal(int, str)
    finished = Signal(list, str)  # List[DependencyParse], backend
    failed = Signal(str)

    def __init__(self, parser: DependencyParser, sentences: List[str]):
        super().__init__()
        self._parser = parser
        self._sentences = sentences

    def cancel(self) -> None:
        """请求取消任务(由 UI 线程调用)"""
        self.requestInterruption()

    def run(self):
        try:
            results: List[DependencyParse] = []
            total = max(1, len(self._sentences))
            for i, sent in enumerate(self._sentences):
                if self.isInterruptionRequested():
                    return
                self.progress.emit(
                    int((i / total) * 100),
                    f"分析第 {i + 1}/{total} 句",
                )
                result = self._parser.parse(sent)
                results.append(result)
            self.progress.emit(100, f"分析完成,共 {len(results)} 句")
            self.finished.emit(results, self._parser.name)
        except Exception as e:
            logger.exception("[DependencyWorker] 分析失败")
            self.failed.emit(str(e))


# ---------------------------------------------------------------------------
# 主控件
# ---------------------------------------------------------------------------
class DependencyWidget(AiInsightMixin, ResourceSinkMixin, QWidget):
    """句法依存分析子页面

    复用项目标准 UI 模式:
        - 顶部参数卡(输入 + 操作按钮)
        - 中部结果卡(图 + 摘要 + 视图切换)
        - 继承 AiInsightMixin 提供「AI 解读」抽屉能力
        - 继承 ResourceSinkMixin 提供分析结果自动归档到当前激活项目的能力
        - 主线程不阻塞,使用 QThread 后台分析
    """

    _AI_INSIGHT_PANEL_NAME = "依存分析"
    _AI_INSIGHT_TYPE = "dependency"

    # PRD-002 资源归档配置(REQ-PROJ-001)
    _RESOURCE_TYPE = RESOURCE_TYPE_DEPENDENCY
    _RESOURCE_TITLE_PREFIX = "依存分析"

    def __init__(self, parent: Optional[QWidget] = None, corpusStore=None):
        super().__init__(parent=parent)
        self._corpusStore = corpusStore
        self._parser: DependencyParser = getDefaultParser()
        self._results: List[DependencyParse] = []
        self._currentIndex: int = 0
        self._worker: Optional[DependencyAnalysisWorker] = None

        self._buildUi()
        self._refreshBackendLabel()

    def closeEvent(self, event) -> None:
        """关闭前停止后台线程,避免线程悬挂或泄漏(P0-fix)"""
        worker = self._worker
        if worker is not None:
            try:
                if hasattr(worker, "cancel"):
                    worker.cancel()
                if worker.isRunning():
                    worker.wait(2000)
            except Exception:
                pass
            # deleteLater 让 Qt 在事件循环中安全释放 QThread 资源
            worker.deleteLater()
            self._worker = None
        super().closeEvent(event)

    # =====================================================================
    # UI 构建(与其他子页面风格一致: outer 边距 20,标题 + 说明 + ScrollArea)
    # =====================================================================
    def _buildUi(self) -> None:
        # 1) 顶层 outer — 与其他子页面统一(20, 20, 20, 20)
        outerLayout = QVBoxLayout(self)
        outerLayout.setContentsMargins(20, 20, 20, 20)
        outerLayout.setSpacing(12)

        # 2) 标题
        titleLabel = SubtitleLabel("句法依存图", self)
        outerLayout.addWidget(titleLabel)

        # 3) 说明
        hint = CaptionLabel(
            "基于 HanLP RESTful API 的依存句法分析。"
            "支持中文(简体、繁体)及英文,可视化展示句子的句法结构,"
            "可导出 PNG/SVG/CoNLL-U。",
            self,
        )
        setThemeRole(hint, "muted", "font-size: 12px;")
        hint.setWordWrap(True)
        outerLayout.addWidget(hint)

        # 4) 滚动容器
        self._scrollArea = ScrollArea(self)
        self._scrollArea.setWidgetResizable(True)
        self._scrollArea.setStyleSheet("border: none; background: transparent;")
        outerLayout.addWidget(self._scrollArea, 1)

        # 5) 滚动内容根布局
        self._contentWidget = QWidget(self._scrollArea)
        self._contentWidget.setObjectName("dependencyContent")
        root = QVBoxLayout(self._contentWidget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        self._scrollArea.setWidget(self._contentWidget)

        # 6) 输入 + 控制卡
        root.addWidget(self._buildInputCard())

        # 7) 结果卡(占据剩余高度)
        root.addWidget(self._buildResultCard(), 1)

    def _buildInputCard(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        # 与其他子页面一致:卡片内部标准边距 (16, 12, 16, 12),spacing=8
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(StrongBodyLabel("输入文本", card))

        # 多行输入
        self.textEdit = PlainTextEdit(card)
        self.textEdit.setPlaceholderText(
            "在此粘贴或输入一段中文(或英文)文本。\n"
            "可输入多个句子,程序会自动按句号、问号、感叹号切分。"
        )
        self.textEdit.setMinimumHeight(100)
        self.textEdit.setMaximumHeight(140)
        layout.addWidget(self.textEdit)

        # 提示行:句数估计
        self.previewLabel = CaptionLabel("", card)
        setThemeRole(self.previewLabel, "muted", "font-size: 11px;")
        self.textEdit.textChanged.connect(self._updateSentencePreview)
        layout.addWidget(self.previewLabel)

        # 操作按钮行
        opRow = QHBoxLayout()
        opRow.setSpacing(8)

        # 后端显示
        self.backendLabel = CaptionLabel("", card)
        setThemeRole(self.backendLabel, "muted", "font-size: 11px;")
        opRow.addWidget(self.backendLabel)

        opRow.addStretch(1)

        # 示例文本
        self.exampleBtn = PushButton("示例文本", card)
        self.exampleBtn.setIcon(FIF.DOCUMENT)
        self.exampleBtn.clicked.connect(self._onExampleClicked)
        opRow.addWidget(self.exampleBtn)

        # 清空
        self.clearBtn = PushButton("清空", card)
        self.clearBtn.setIcon(FIF.CLOSE)
        self.clearBtn.clicked.connect(self._onClearClicked)
        opRow.addWidget(self.clearBtn)

        # 主按钮
        self.runBtn = PrimaryPushButton("开始分析", card)
        self.runBtn.setIcon(FIF.PLAY)
        self.runBtn.clicked.connect(self._onRunClicked)
        opRow.addWidget(self.runBtn)
        # AI 解读按钮（PRD-001 REQ-AI-001）— 通过 Mixin 一行接入
        self._aiInsightBtn = PrimaryPushButton("AI 解读", card)
        self._aiInsightBtn.setIcon(FIF.HEART)
        self.setupAiInsightButton(self._aiInsightBtn)
        opRow.addWidget(self._aiInsightBtn)

        layout.addLayout(opRow)
        return card

    def _buildResultCard(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        # 与其他子页面一致:卡片内部标准边距 (16, 12, 16, 12),spacing=8
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # 顶部: 摘要 + 句子选择
        headerRow = QHBoxLayout()
        layout.addWidget(StrongBodyLabel("依存结构", card))

        subRow = QHBoxLayout()
        self.summaryLabel = CaptionLabel("尚未分析", card)
        setThemeRole(self.summaryLabel, "muted", "font-size: 11px;")
        subRow.addWidget(self.summaryLabel)
        subRow.addStretch(1)
        subRow.addWidget(QLabel("句子:", card))
        self.sentenceSelector = ComboBox(card)
        self.sentenceSelector.setMinimumWidth(200)
        self.sentenceSelector.currentIndexChanged.connect(self._onSentenceChanged)
        subRow.addWidget(self.sentenceSelector)
        layout.addLayout(subRow)

        # 视图切换(树状 / 弧状)
        viewRow = QHBoxLayout()
        viewRow.addWidget(QLabel("视图:", card))
        self.viewSeg = SegmentedWidget(card)
        self.viewSeg.addItem("tree", "树状图")
        self.viewSeg.addItem("arc", "弧状图")
        self.viewSeg.setCurrentItem("tree")
        self.viewSeg.currentItemChanged.connect(self._onViewChanged)
        viewRow.addWidget(self.viewSeg)
        viewRow.addStretch(1)
        layout.addLayout(viewRow)

        # matplotlib 画布
        self.figure = Figure(figsize=(10, 6), dpi=100, facecolor="white")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas.setMinimumHeight(360)
        layout.addWidget(self.canvas, 1)

        # 悬停提示标签
        self.deprelLegend = CaptionLabel("", card)
        setThemeRole(
            self.deprelLegend,
            "text",
            "font-size: 11px; padding: 4px 0;",
        )
        layout.addWidget(self.deprelLegend)

        # 导出按钮
        exportRow = QHBoxLayout()
        exportRow.addStretch(1)
        self.exportPngBtn = PushButton("导出 PNG", card)
        self.exportPngBtn.setIcon(FIF.SAVE)
        self.exportPngBtn.clicked.connect(lambda: self._onExport("png"))
        exportRow.addWidget(self.exportPngBtn)

        self.exportSvgBtn = PushButton("导出 SVG", card)
        self.exportSvgBtn.setIcon(FIF.SAVE)
        self.exportSvgBtn.clicked.connect(lambda: self._onExport("svg"))
        exportRow.addWidget(self.exportSvgBtn)

        self.exportConlluBtn = PushButton("导出 CoNLL-U", card)
        self.exportConlluBtn.setIcon(FIF.DOCUMENT)
        self.exportConlluBtn.clicked.connect(self._onExportConllU)
        exportRow.addWidget(self.exportConlluBtn)
        layout.addLayout(exportRow)

        # 初始空图
        self._drawEmpty("请输入文本并点击「开始分析」")
        return card

    # =====================================================================
    # 信号处理
    # =====================================================================
    def _refreshBackendLabel(self) -> None:
        text = f"分析引擎:{self._parser.name}({self._parser.describe()})"
        # HanLP 初始化失败时,附加错误说明
        err = getattr(self._parser, "getLastError", lambda: None)()
        if err:
            text += f"   |   ⚠ {err}"
        self.backendLabel.setText(text)
        setThemeRole(
            self.backendLabel,
            "danger" if err else "muted",
            "font-size: 11px;",
        )

    def _updateSentencePreview(self) -> None:
        text = self.textEdit.toPlainText().strip()
        if not text:
            self.previewLabel.setText("")
            return
        sents = splitSentences(text)
        if len(sents) == 1:
            self.previewLabel.setText(f"共 1 句,长度 {len(text)} 字符")
        else:
            self.previewLabel.setText(f"共 {len(sents)} 句,总长 {len(text)} 字符")

    def _onExampleClicked(self) -> None:
        self.textEdit.setPlainText(
            "我爱自然语言处理。\n"
            "小明在北京大学的图书馆里认真地学习中文。\n"
            "中国人民从此站起来了。\n"
        )

    def _onClearClicked(self) -> None:
        self.textEdit.clear()
        self._results = []
        self._currentIndex = 0
        self.sentenceSelector.clear()
        self.summaryLabel.setText("尚未分析")
        self.deprelLegend.setText("")
        self._drawEmpty("请输入文本并点击「开始分析」")

    def _onRunClicked(self) -> None:
        text = self.textEdit.toPlainText().strip()
        if not text:
            _showInfoBar("warning", "无法分析", "请先输入文本", self)
            return

        sentences = splitSentences(text)
        if not sentences:
            _showInfoBar("warning", "无法分析", "未能切分出句子", self)
            return

        # 启动后台 worker(P0-fix:统一使用 self._worker 命名)
        self.runBtn.setEnabled(False)
        self.summaryLabel.setText("正在分析...")
        self._worker = DependencyAnalysisWorker(self._parser, sentences)
        self._worker.progress.connect(self._onProgress)
        self._worker.finished.connect(self._onFinished)
        self._worker.failed.connect(self._onFailed)
        self._worker.start()

    def _onProgress(self, pct: int, msg: str) -> None:
        self.summaryLabel.setText(f"[{pct}%] {msg}")

    def _onFinished(self, results: List[DependencyParse], backend: str) -> None:
        self.runBtn.setEnabled(True)
        self._results = results
        self._backend = backend
        self._currentIndex = 0
        self._refreshSentenceSelector()
        if results:
            self._renderCurrentSentence()
            self.summaryLabel.setText(f"分析完成 — 共 {len(results)} 句,后端:{backend}")
        else:
            self.summaryLabel.setText("分析完成 — 无结果")
        # AI 解读:有结果后启用按钮
        self.refreshAiInsightButton()
        # PRD-002:归档到当前激活项目(若有)
        if results:
            self.notifyResourceCreated()

    # ------------------------------------------------------------------
    # AI 解读协议（PRD-001 REQ-AI-001）— 由 AiInsightMixin 调用
    # ------------------------------------------------------------------
    def _aiHasResult(self) -> bool:
        return bool(getattr(self, "_results", []))

    def _aiCollectExplainArgs(self):
        if not self._aiHasResult():
            return None
        return ("dependency", {"result": self._results})

    # ------------------------------------------------------------------
    # PRD-002 资源归档钩子(ResourceSinkMixin)
    # ------------------------------------------------------------------
    def _collectResourcePayload(self):
        """依存分析结果 → 项目资源 payload"""
        results: List[DependencyParse] = getattr(self, "_results", []) or []
        if not results:
            return None
        try:
            sentenceSnippets = []
            for p in results[:3]:
                if p.text:
                    snippet = p.text if len(p.text) <= 40 else p.text[:40] + "…"
                    sentenceSnippets.append(snippet)
            totalEdges = sum(len(p.edges) for p in results)
            summary = (
                f"依存分析 {len(results)} 句,{totalEdges} 条依存边,"
                f"后端:{getattr(self, '_backend', '')}。"
                f"样例:{' | '.join(sentenceSnippets)}"
            )
        except Exception:
            summary = f"依存分析 {len(results)} 句"
        try:
            conlluSnippets = []
            for p in results[:5]:
                conlluSnippets.append(toConllU(p))
        except Exception:
            conlluSnippets = []
        snapshotData = {
            "sentenceCount": len(results),
            "totalEdges": sum(len(p.edges) for p in results),
            "backend": getattr(self, "_backend", ""),
            "conlluSnippets": conlluSnippets,
        }
        parameters = {
            "sentenceCount": len(results),
            "backend": getattr(self, "_backend", ""),
        }
        ts = self._buildDefaultTitle().split(" ", 1)[1]
        return {
            "title": f"依存分析 ({len(results)} 句) ({ts})",
            "summary": summary[:200],
            "parameters": parameters,
            "snapshotData": snapshotData,
        }

    def _onFailed(self, err: str) -> None:
        self.runBtn.setEnabled(True)
        self.summaryLabel.setText(f"分析失败:{err}")
        # HanLP 常见错误:未授权、配额耗尽、网络超时、参数非法
        # 给出可操作的修复提示
        msg = err
        if "401" in err or "Unauthorized" in err or "auth" in err.lower():
            msg += "\n\n提示:HanLP 鉴权失败。请检查 dependency_engine.py 中"
            msg += " HanLPDependencyParser.HANLP_AUTH 是否正确。"
        elif "timeout" in err.lower() or "timed out" in err.lower():
            msg += "\n\n提示:网络超时。可稍后重试或检查代理设置。"
        elif "429" in err or "rate" in err.lower():
            msg += "\n\n提示:API 配额已用完。可稍后重试或更换密钥。"
        elif "Invalid tasks" in err or "Available tasks" in err:
            msg += "\n\n提示:HanLP 任务名非法。请检查 dependency_engine.py 中"
            msg += " HanLPDependencyParser.HANLP_TASKS 是否为官方支持的任务名。"
        MessageBox("分析失败", msg, self).exec()

    def _onSentenceChanged(self, index: int) -> None:
        self._currentIndex = index
        self._renderCurrentSentence()

    def _onViewChanged(self, key: str) -> None:
        self._renderCurrentSentence()

    # =====================================================================
    # 渲染
    # =====================================================================
    def _refreshSentenceSelector(self) -> None:
        self.sentenceSelector.blockSignals(True)
        self.sentenceSelector.clear()
        for i, p in enumerate(self._results):
            preview = p.text[:18] + ("..." if len(p.text) > 18 else "")
            self.sentenceSelector.addItem(f"#{i + 1} {preview}")
        self.sentenceSelector.setCurrentIndex(0)
        self.sentenceSelector.blockSignals(False)

    def _currentParse(self) -> Optional[DependencyParse]:
        if not self._results:
            return None
        if self._currentIndex < 0 or self._currentIndex >= len(self._results):
            return None
        return self._results[self._currentIndex]

    def _renderCurrentSentence(self) -> None:
        parse = self._currentParse()
        if parse is None or not parse.tokens:
            self._drawEmpty("无数据")
            self.deprelLegend.setText("")
            return

        view = self.viewSeg.currentRouteKey() or "tree"
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.set_facecolor("white")

        if view == "tree":
            self._drawTree(ax, parse)
        else:
            self._drawArc(ax, parse)

        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        self.figure.tight_layout()
        self.canvas.draw_idle()

        # 更新图例
        tokens = len(parse.tokens)
        edges = len(parse.edges)
        self.deprelLegend.setText(
            f"当前句:{parse.text[:40]}{'...' if len(parse.text) > 40 else ''}  |  "
            f"节点 {tokens} · 边 {edges} · 引擎 {parse.backend}"
        )

    def _drawEmpty(self, msg: str) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.set_facecolor("white")
        ax.text(
            0.5,
            0.5,
            msg,
            ha="center",
            va="center",
            fontsize=14,
                color=shellPalette().mutedText.name(),
            transform=ax.transAxes,
        )
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        self.canvas.draw_idle()

    # ---------- 树状图(FR-DEP-002) ----------
    def _drawTree(self, ax, parse: DependencyParse) -> None:
        """树状图布局:ROOT 在顶部,递归向下"""
        n = len(parse.tokens)
        if n == 0:
            return

        # 构建树(子节点列表)
        children: Dict[int, List[DepToken]] = {t.id: [] for t in parse.tokens}
        rootId = 0
        for tok in parse.tokens:
            if tok.head == 0:
                rootId = tok.id
            elif tok.head in children:
                children[tok.head].append(tok)

        # 计算每个节点的 x 坐标(后序遍历分配叶子位置)
        positions: Dict[int, float] = {}

        def assignX(nodeId: int) -> float:
            kids = children.get(nodeId, [])
            if not kids:
                positions[nodeId] = len(positions)
                return positions[nodeId]
            # 平均子节点 x
            xs = [assignX(k.id) for k in kids]
            mid = sum(xs) / len(xs)
            positions[nodeId] = mid
            return mid

        # 计算树深度(决定 y)
        depths: Dict[int, int] = {}

        def assignDepth(nodeId: int, d: int) -> None:
            depths[nodeId] = d
            for k in children.get(nodeId, []):
                assignDepth(k.id, d + 1)

        if rootId == 0:
            # 没有显式 ROOT,把第一个 token 作为根
            rootId = parse.tokens[0].id
        assignDepth(rootId, 0)

        maxDepth = max(depths.values()) if depths else 0
        assignX(rootId)

        # 缩放因子(根据节点数)
        scaleX = max(1.0, n * 0.7)
        scaleY = max(1.0, (maxDepth + 1) * 1.0)

        # 画边(弧 + 箭头,父→子)
        for tok in parse.tokens:
            if tok.head == 0 or tok.head == tok.id:
                continue
            x1 = positions.get(tok.head, 0) * 1.5
            y1 = -depths.get(tok.head, 0)
            x2 = positions.get(tok.id, 0) * 1.5
            y2 = -depths.get(tok.id, 0)
            arrow = FancyArrowPatch(
                (x1, y1),
                (x2, y2),
                arrowstyle="-|>",
                mutation_scale=15,
                color="#3a76d8",
                linewidth=1.2,
                connectionstyle="arc3,rad=0.12",
            )
            ax.add_patch(arrow)
            # 边标签(在弧中央)
            mx = (x1 + x2) / 2 + 0.12 * (y1 - y2)
            my = (y1 + y2) / 2 + 0.04 * abs(x1 - x2)
            ax.text(
                mx,
                my,
                tok.deprel,
                fontsize=8,
                color="#d04a3a",
                ha="center",
                va="center",
                bbox=dict(
                    boxstyle="round,pad=0.18",
                    facecolor=shellPalette().warningSurface.name(),
                    edgecolor="#d04a3a",
                    linewidth=0.5,
                ),
            )

        # 画节点
        for tok in parse.tokens:
            x = positions.get(tok.id, 0) * 1.5
            y = -depths.get(tok.id, 0)
            ax.plot(x, y, "o", markersize=11, color="#3a76d8", zorder=3)
            ax.text(
                x,
                y,
                tok.form,
                fontsize=10,
                ha="center",
                va="center",
                color="white",
                weight="bold",
                zorder=4,
            )
            # POS 标注(节点下方小字)
            ax.text(
                x,
                y - 0.18,
                f"({tok.pos})",
                fontsize=7,
                ha="center",
                va="top",
                    color=shellPalette().mutedText.name(),
            )

        # 设置坐标范围
        allX = list(positions.values())
        if allX:
            ax.set_xlim(min(allX) * 1.5 - 1, max(allX) * 1.5 + 1)
        ax.set_ylim(-maxDepth - 0.5, 0.5)

    # ---------- 弧状图(FR-DEP-003) ----------
    def _drawArc(self, ax, parse: DependencyParse) -> None:
        """弧状图:线性排列词语,弧线表示依存关系"""
        n = len(parse.tokens)
        if n == 0:
            return

        # 节点 x 坐标 = 索引
        xs = {tok.id: i for i, tok in enumerate(parse.tokens)}

        # 计算弧的最高点(深度)
        arcLevel: Dict[Tuple[int, int], int] = {}

        def levelFor(headId: int, depId: int) -> int:
            key = (headId, depId)
            if key in arcLevel:
                return arcLevel[key]
            # 简单策略:根据跨度大小
            span = abs(xs[depId] - xs[headId])
            level = 0
            # 同层弧不交叉 → 用 span 决定层级
            existing = [v for v in arcLevel.values()]
            while level in existing:
                level += 1
            arcLevel[key] = level
            return level

        # 画线
        for tok in parse.tokens:
            if tok.head == 0 or tok.head == tok.id:
                # ROOT 节点显示在底部
                if tok.head == 0:
                    x = xs[tok.id]
                    ax.plot([x, x], [0, -0.4], color="#3a76d8", linewidth=1.5)
                continue
            # 防御性:head 可能因 parser 数据异常不在 xs 中
            # (虽然 _sanitizeHeads 应已修复,UI 层再加一道保险)
            if tok.head not in xs:
                logger.warning(
                    f"[DependencyWidget] token id={tok.id} ({tok.form!r}) "
                    f"head={tok.head} 不在节点列表中,fallback 为 ROOT"
                )
                # 临时把 ROOT 指示线画出来
                x = xs[tok.id]
                ax.plot([x, x], [0, -0.4], color="#3a76d8", linewidth=1.5)
                continue
            x1 = xs[tok.head]
            x2 = xs[tok.id]
            level = levelFor(tok.head, tok.id)
            h = (level + 1) * 0.4  # 弧高
            midX = (x1 + x2) / 2
            ax.plot(
                [x1, midX, x2],
                [0, h, 0],
                color="#3a76d8",
                linewidth=1.0,
                alpha=0.85,
            )
            # 弧顶标签
            ax.text(
                midX,
                h + 0.06,
                tok.deprel,
                fontsize=7.5,
                color="#d04a3a",
                ha="center",
                va="bottom",
                bbox=dict(
                    boxstyle="round,pad=0.15",
                    facecolor=shellPalette().warningSurface.name(),
                    edgecolor="#d04a3a",
                    linewidth=0.4,
                ),
            )

        # 底部词语
        for tok in parse.tokens:
            x = xs[tok.id]
            ax.text(
                x,
                0,
                tok.form,
                fontsize=10,
                ha="center",
                va="top",
                bbox=dict(
                    boxstyle="round,pad=0.3", facecolor="#3a76d8", edgecolor="none"
                ),
                color="white",
                weight="bold",
            )

        # 顶部 ROOT 标记
        rootTok = next((t for t in parse.tokens if t.head == 0), None)
        if rootTok is not None:
            ax.text(
                xs[rootTok.id],
                -0.6,
                f"ROOT: {rootTok.form}",
                fontsize=8,
                ha="center",
                va="top",
                    color=shellPalette().mutedText.name(),
            )

        ax.set_xlim(-0.5, n - 0.5)
        ax.set_ylim(-0.8, max(arcLevel.values()) * 0.4 + 1.0 if arcLevel else 1.0)

    # =====================================================================
    # 导出
    # =====================================================================
    def _currentFigureSavePath(self, ext: str) -> Optional[str]:
        parse = self._currentParse()
        if parse is None:
            _showInfoBar("warning", "无法导出", "当前没有结果", self)
            return None
        defaultName = f"dep_tree_{parse.backend}_n{len(parse.tokens)}.{ext}"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存图片",
            defaultName,
            f"{ext.upper()} 文件 (*.{ext})",
        )
        return path or None

    def _onExport(self, ext: str) -> None:
        path = self._currentFigureSavePath(ext)
        if not path:
            return
        charge = beginPaidAnalysisExport(self, f"导出句法依存图 {ext.upper()}")
        if charge is None:
            return
        try:
            self.figure.savefig(path, format=ext, dpi=150, bbox_inches="tight")
            if charge.commit():
                _showInfoBar("success", "导出成功", path, self, duration=2500)
        except Exception as e:
            charge.refund()
            logger.exception("[DependencyWidget] 导出失败")
            MessageBox("导出失败", str(e), self).exec()

    def _onExportConllU(self) -> None:
        parse = self._currentParse()
        if parse is None:
            _showInfoBar("warning", "无法导出", "当前没有结果", self)
            return
        defaultName = f"dep_{parse.backend}.conllu"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存 CoNLL-U",
            defaultName,
            "CoNLL-U 文件 (*.conllu *.txt)",
        )
        if not path:
            return
        charge = beginPaidAnalysisExport(self, "导出句法依存 CoNLL-U")
        if charge is None:
            return
        try:
            Path(path).write_text(toConllU(parse), encoding="utf-8")
            if charge.commit():
                _showInfoBar("success", "导出成功", path, self, duration=2500)
        except Exception as e:
            charge.refund()
            logger.exception("[DependencyWidget] CoNLL-U 导出失败")
            MessageBox("导出失败", str(e), self).exec()

    # =====================================================================
    # 外部绑定(corpusStore 重绑 / 切换语料库)
    # =====================================================================
    def setCorpusStore(self, corpusStore) -> None:
        """被 FreqAnalyzerInterface 在切换语料库时调用"""
        if self._corpusStore is corpusStore:
            return
        self._corpusStore = corpusStore
        # 句法依存分析不直接消费 corpusStore,这里仅记录引用
        # 如未来需要「从语料中取句子」,可在 _onRunClicked 中读取
        # corpusStore.effectiveTexts() 并送入分析
        # P0-fix:切换语料库时清空旧分析结果,避免与新语料错配
        self._resetResultsForCorpusSwitch()

    def _resetResultsForCorpusSwitch(self) -> None:
        """切换语料库时清空旧依存分析结果与 UI(P0-fix)"""
        self._results = []
        self._currentIndex = 0
        # 取消正在运行的 worker(P0-fix:统一使用 self._worker 命名)
        worker = getattr(self, "_worker", None)
        if worker is not None:
            try:
                if hasattr(worker, "cancel"):
                    worker.cancel()
                if worker.isRunning():
                    worker.wait(200)
            except Exception:
                pass
            self._worker = None
        # 句子选择器 / 摘要 / 图例复位
        sel = getattr(self, "sentenceSelector", None)
        if sel is not None:
            try:
                sel.clear()
            except Exception:
                pass
        if hasattr(self, "summaryLabel") and self.summaryLabel is not None:
            try:
                self.summaryLabel.setText("已切换语料库,请重新分析")
            except Exception:
                pass
        if hasattr(self, "deprelLegend") and self.deprelLegend is not None:
            try:
                self.deprelLegend.setText("")
            except Exception:
                pass
        # 清空画布
        try:
            self._drawEmpty("已切换语料库,请重新输入文本并点击「开始分析」")
        except Exception:
            pass
        # 文本编辑区也清空,避免误导用户(可注释掉:用户可能想保留输入)
        # 当前选择清空:因为依存分析与输入文本一一对应,旧文本在新库语境下无意义
        try:
            if hasattr(self, "textEdit") and self.textEdit is not None:
                self.textEdit.clear()
        except Exception:
            pass
        try:
            if hasattr(self, "previewLabel") and self.previewLabel is not None:
                self.previewLabel.setText("")
        except Exception:
            pass
