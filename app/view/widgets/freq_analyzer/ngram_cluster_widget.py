# coding: utf-8
"""N-gram 聚簇分析可视化组件

包含:
    - NgramClusterWorker:  后台聚类分析线程（QThread）
    - NgramClusterDialog:  聚类结果可视化弹窗（散点图 + 簇摘要）

设计：
    - 聚类计算在后台线程执行，通过 progress 信号报告进度
    - 完成后在主线程渲染 matplotlib 散点图，UI 全程不阻塞
    - 进度分阶段展示：解析数据 → 降维 → 聚类 → 渲染
"""

from __future__ import annotations

import traceback
from typing import List, Optional

import numpy as np
import pandas as pd

from loguru import logger

# matplotlib 后端必须在导入 pyplot 前设置
import matplotlib  # noqa: E402

matplotlib.use("QtAgg", force=True)

import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvas,
)  # noqa: E402

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

from app.view.widgets.freq_analyzer.ngram_cluster_engine import (
    NgramClusterEngine,
    NgramClusterResult,
)
from app.view.widgets.freq_analyzer.ai_insight_mixin import AiInsightMixin
from app.view.widgets.freq_analyzer.ui_helpers import (
    _makeDialogHeader,
    _makeScrollArea,
    _setupDialogClose,
    _showInfoBar,
)

# ---------------------------------------------------------------------------
# CJK 字体检测（与 network_widget 保持一致）
# ---------------------------------------------------------------------------
_CJK_FONT_CANDIDATES = (
    "Microsoft YaHei",
    "Microsoft YaHei UI",
    "SimHei",
    "SimSun",
    "Yu Gothic",
    "Malgun Gothic",
    "Noto Sans CJK SC",
    "Source Han Sans CN",
    "PingFang SC",
    "Heiti SC",
    "WenQuanYi Micro Hei",
    "Arial Unicode MS",
    "Noto Sans",
)


def _availableCjkFonts() -> List[str]:
    try:
        installed = {f.name for f in fm.fontManager.ttflist}
    except Exception:
        return []
    result: List[str] = []
    for name in _CJK_FONT_CANDIDATES:
        if name in installed:
            result.append(name)
    if not result:
        logger.warning(
            "[NgramClusterDialog] 系统未安装 CJK 字体，中文标签可能无法正常显示"
        )
    return result


# 初始化 matplotlib 中文字体
_cjkFonts = _availableCjkFonts()
if _cjkFonts:
    plt.rcParams["font.sans-serif"] = _cjkFonts
    plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["axes.unicode_minus"] = False


# ---------------------------------------------------------------------------
# 颜色映射
# ---------------------------------------------------------------------------
# 10 种视觉可区分颜色（ColorBrewer Set3 子集 + 自定义）
_CLUSTER_COLORS = [
    "#4C72B0",  # 蓝
    "#DD8452",  # 橙
    "#55A868",  # 绿
    "#C44E52",  # 红
    "#8172B3",  # 紫
    "#937860",  # 棕
    "#DA8BC3",  # 粉
    "#8C8C8C",  # 灰
    "#CCB974",  # 黄
    "#64B5CD",  # 青
]


def _clusterColor(cid: int) -> str:
    """为簇编号分配固定颜色"""
    return _CLUSTER_COLORS[cid % len(_CLUSTER_COLORS)]


# ---------------------------------------------------------------------------
# 后台分析线程
# ---------------------------------------------------------------------------


class NgramClusterWorker(QThread):
    """后台 N-gram 聚簇分析线程

    Signals:
        progress(int, str)          进度 (百分比, 阶段描述)
        finished(NgramClusterResult) 分析完成
        failed(str)                  错误信息
    """

    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        ngramDf: pd.DataFrame,
        n: int,
        maxClusters: int = 8,
        minFreq: int = 3,
        maxNgrams: int = 2000,
        parent=None,
    ):
        super().__init__(parent)
        self._ngramDf = ngramDf
        self._n = n
        self._maxClusters = maxClusters
        self._minFreq = minFreq
        self._maxNgrams = maxNgrams

    def run(self) -> None:
        try:
            engine = NgramClusterEngine()

            def _onProgress(pct: int, msg: str) -> None:
                if self.isInterruptionRequested():
                    return
                self.progress.emit(int(pct), str(msg))

            result = engine.analyze(
                ngramDf=self._ngramDf,
                n=self._n,
                maxClusters=self._maxClusters,
                minNgramFreq=self._minFreq,
                maxNgrams=self._maxNgrams,
                progressCallback=_onProgress,
            )

            if self.isInterruptionRequested():
                return

            if result is None:
                self.failed.emit("聚类分析失败：数据不足（N-gram 数量过少或频次过低）")
                return

            self.finished.emit(result)

        except Exception as e:
            logger.exception("[NgramClusterWorker] 分析异常")
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# 聚类结果可视化弹窗
# ---------------------------------------------------------------------------


class NgramClusterDialog(AiInsightMixin, MessageBoxBase):
    """N-gram 聚簇分析可视化弹窗

    继承 AiInsightMixin 提供「AI 解读」抽屉能力

    功能：
        - 后台线程执行聚类分析，UI 全程不阻塞
        - 进度条 + 状态文本展示分析进度
        - t-SNE 散点图展示聚类结果（颜色区分簇）
        - 点大小映射 N-gram 频次
        - 悬停提示显示 N-gram 文本
        - 右侧/底部显示每个簇的 Top N-gram 摘要
        - 导出 PNG / SVG

    用法:
        dialog = NgramClusterDialog.show(
            ngramDf, n=3, parent=parentWindow
        )
    """

    _AI_INSIGHT_PANEL_NAME = "N-gram 聚类"
    _AI_INSIGHT_TYPE = "ngram_cluster"

    def __init__(
        self,
        ngramDf: pd.DataFrame,
        n: int = 3,
        maxClusters: int = 8,
        minFreq: int = 3,
        maxNgrams: int = 2000,
        parent=None,
    ):
        super().__init__(parent)
        self._ngramDf = ngramDf
        self._n = max(2, int(n))
        self._maxClusters = maxClusters
        self._minFreq = minFreq
        self._maxNgrams = maxNgrams
        self._result: Optional[NgramClusterResult] = None
        self._worker: Optional[NgramClusterWorker] = None
        self._figure: Optional[Figure] = None
        self._canvas: Optional[FigureCanvas] = None
        self._hasValidResult = False

        # 标题栏
        label = "Bigram" if self._n == 2 else f"{self._n}-gram"
        _makeDialogHeader(
            self,
            ":app/icons/Chart.svg",
            f"{label} 聚簇分析",
            self.reject,
        )

        # ---- 进度区域 ----
        self._progressBar = QProgressBar(self)
        self._progressBar.setRange(0, 100)
        self._progressBar.setValue(0)
        self._progressBar.setTextVisible(True)
        self._progressBar.setFormat("准备中...")
        self._progressBar.setFixedHeight(24)

        self._progressLabel = CaptionLabel("正在启动分析...", self)
        self._progressLabel.setStyleSheet("color: #666; font-size: 12px;")
        self._progressLabel.setWordWrap(True)

        # ---- 图表区域（占位） ----
        self._chartPlaceholder = CaptionLabel("聚类分析进行中，请稍候...", self)
        self._chartPlaceholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chartPlaceholder.setStyleSheet(
            "color: #999; font-size: 14px; padding: 40px;"
        )

        # ---- 簇摘要区域（占位） ----
        self._summaryWidget = QWidget(self)
        self._summaryLayout = QVBoxLayout(self._summaryWidget)
        self._summaryLayout.setContentsMargins(0, 0, 0, 0)
        self._summaryLayout.setSpacing(6)
        self._summaryWidget.setVisible(False)

        # ---- 底部按钮 ----
        self._infoLabel = CaptionLabel("", self)
        self._infoLabel.setStyleSheet("color: #666; font-size: 11px;")

        btnLayout = QHBoxLayout()
        btnLayout.addWidget(self._infoLabel)
        btnLayout.addStretch(1)

        self._rerunBtn = PushButton("重新分析", self)
        self._rerunBtn.setIcon(FluentIcon.SYNC)
        self._rerunBtn.clicked.connect(self._startAnalysis)
        self._rerunBtn.setEnabled(False)
        btnLayout.addWidget(self._rerunBtn)

        self._exportPngBtn = PushButton("导出 PNG", self)
        self._exportPngBtn.setIcon(FluentIcon.SAVE)
        self._exportPngBtn.clicked.connect(lambda: self._export("png"))
        self._exportPngBtn.setEnabled(False)
        btnLayout.addWidget(self._exportPngBtn)

        self._exportSvgBtn = PushButton("导出 SVG", self)
        self._exportSvgBtn.setIcon(FluentIcon.SAVE)
        self._exportSvgBtn.clicked.connect(lambda: self._export("svg"))
        self._exportSvgBtn.setEnabled(False)
        btnLayout.addWidget(self._exportSvgBtn)
        # AI 解读按钮（PRD-001 REQ-AI-001）— 通过 Mixin 一行接入
        # 统一为 PrimaryPushButton,放在「重新分析」旁边
        self._aiInsightBtn = PrimaryPushButton("AI 解读", self)
        self._aiInsightBtn.setIcon(FluentIcon.HEART)
        self.setupAiInsightButton(self._aiInsightBtn)
        btnLayout.addWidget(self._aiInsightBtn)

        # ---- 整体布局 ----
        self.viewLayout.setContentsMargins(20, 16, 20, 12)
        self.viewLayout.setSpacing(10)

        # 进度条 + 状态
        self.viewLayout.addWidget(self._progressBar)
        self.viewLayout.addWidget(self._progressLabel)

        # 图表区域（先放占位，完成后替换为 FigureCanvas）
        self._chartContainer = QWidget(self)
        self._chartContainerLayout = QVBoxLayout(self._chartContainer)
        self._chartContainerLayout.setContentsMargins(0, 0, 0, 0)
        self._chartContainerLayout.addWidget(self._chartPlaceholder)
        self.viewLayout.addWidget(self._chartContainer, 1)

        # 摘要
        self.viewLayout.addWidget(self._summaryWidget)

        # 按钮
        self.viewLayout.addLayout(btnLayout)

        _setupDialogClose(self)

        self.widget.setFixedWidth(800)
        self.widget.setFixedHeight(680)

        # 启动分析
        self._startAnalysis()

    # ------------------------------------------------------------------
    # 分析流程
    # ------------------------------------------------------------------

    def _startAnalysis(self) -> None:
        """启动后台聚类分析"""
        # 清理旧结果
        self._cleanupCanvas()
        self._hasValidResult = False
        self._result = None
        self._summaryWidget.setVisible(False)
        self._exportPngBtn.setEnabled(False)
        self._exportSvgBtn.setEnabled(False)
        self._rerunBtn.setEnabled(False)

        # 显示占位
        self._chartPlaceholder.setText("聚类分析进行中，请稍候...")
        self._chartPlaceholder.setVisible(True)

        # 重置进度
        self._progressBar.setValue(0)
        self._progressBar.setFormat("准备中...")
        self._progressLabel.setText("正在启动分析线程...")

        # 创建并启动 worker
        self._worker = NgramClusterWorker(
            ngramDf=self._ngramDf,
            n=self._n,
            maxClusters=self._maxClusters,
            minFreq=self._minFreq,
            maxNgrams=self._maxNgrams,
            parent=self,
        )
        self._worker.progress.connect(self._onProgress)
        self._worker.finished.connect(self._onFinished)
        self._worker.failed.connect(self._onFailed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._worker.start()

    def _onProgress(self, pct: int, msg: str) -> None:
        """更新进度条和状态文本"""
        self._progressBar.setValue(pct)
        self._progressBar.setFormat(f"{msg}  %p%")
        self._progressLabel.setText(f"阶段：{msg}")

    def _onFinished(self, result: NgramClusterResult) -> None:
        """分析完成，渲染可视化"""
        self._result = result
        self._hasValidResult = True
        # AI 解读:有结果后启用按钮
        self.refreshAiInsightButton()

        self._progressBar.setValue(100)
        self._progressBar.setFormat("完成")
        ngramLabel = "Bigram" if self._n == 2 else f"{self._n}-gram"

        # 评估质量提示
        sil = result.silhouette
        if sil < 0.0:
            quality = "较差（簇重叠严重）"
        elif sil < 0.25:
            quality = "一般"
        elif sil < 0.5:
            quality = "良好"
        else:
            quality = "优秀"

        self._progressLabel.setText(
            f"分析完成：{result.ngram_count} 个 {ngramLabel}，"
            f"{result.k} 个簇，轮廓系数={sil:.3f}（{quality}）"
        )

        # 渲染图表
        self._renderChart(result)

        # 渲染簇摘要
        self._renderClusterSummary(result)

        # 启用操作按钮
        self._exportPngBtn.setEnabled(True)
        self._exportSvgBtn.setEnabled(True)
        self._rerunBtn.setEnabled(True)

        self._infoLabel.setText(
            f"共 {result.ngram_count} 个 {ngramLabel}  ·  "
            f"{result.k} 个簇  ·  语料 {result.file_count} 个文件"
        )

    # ------------------------------------------------------------------
    # AI 解读协议（PRD-001 REQ-AI-001）— 由 AiInsightMixin 调用
    # ------------------------------------------------------------------
    def _aiHasResult(self) -> bool:
        return getattr(self, "_hasValidResult", False) and self._result is not None

    def _aiCollectExplainArgs(self):
        if not self._aiHasResult():
            return None
        return ("ngram_cluster", {"result": self._result})

    def _onFailed(self, errMsg: str) -> None:
        """分析失败"""
        self._progressBar.setValue(0)
        self._progressBar.setFormat("失败")
        self._progressLabel.setText(f"分析失败：{errMsg}")
        self._chartPlaceholder.setText(f"分析失败\n{errMsg}")
        self._chartPlaceholder.setStyleSheet(
            "color: #C44E52; font-size: 13px; padding: 40px;"
        )
        self._rerunBtn.setEnabled(True)
        _showInfoBar("error", "聚类分析失败", errMsg, self, duration=5000)

    # ------------------------------------------------------------------
    # 图表渲染
    # ------------------------------------------------------------------

    def _cleanupCanvas(self) -> None:
        """清理旧的 matplotlib 画布"""
        if self._canvas is not None:
            self._chartContainerLayout.removeWidget(self._canvas)
            self._canvas.setParent(None)
            self._canvas.deleteLater()
            self._canvas = None
        if self._figure is not None:
            plt.close(self._figure)
            self._figure = None

    def _renderChart(self, result: NgramClusterResult) -> None:
        """在弹窗中渲染聚类散点图"""
        self._cleanupCanvas()
        self._chartPlaceholder.setVisible(False)

        # 创建 figure
        fig = Figure(figsize=(7.2, 5.2), dpi=100)
        self._figure = fig
        ax = fig.add_subplot(111)

        points = result.points_2d
        clusterIds = result.cluster_ids
        freqs = np.array(result.ngram_freqs, dtype=np.float64)
        labels = result.ngram_labels
        k = result.k

        # 计算点大小：映射频次到 20~200
        if freqs.max() > freqs.min():
            sizes = 20 + (freqs - freqs.min()) / (freqs.max() - freqs.min()) * 180
        else:
            sizes = np.full_like(freqs, 60.0)

        # 按簇分别绘制散点（以便 legend 区分）
        for cid in range(k):
            mask = clusterIds == cid
            if not mask.any():
                continue
            ax.scatter(
                points[mask, 0],
                points[mask, 1],
                s=sizes[mask],
                c=_clusterColor(cid),
                alpha=0.75,
                edgecolors="white",
                linewidths=0.5,
                label=f"簇 {cid + 1}（{result.cluster_sizes.get(cid, 0)} 个）",
                zorder=3,
            )

        # 悬停注释（简化版：使用 matplotlib 的 picker 机制）
        self._scatterData = {
            "points": points,
            "labels": labels,
            "clusterIds": clusterIds,
            "k": k,
        }
        # 为每个点的 scatter 添加 picker
        for coll in ax.collections:
            coll.set_picker(True)

        # hover annotation
        self._hoverAnnotation = ax.annotate(
            "",
            xy=(0, 0),
            xytext=(15, 15),
            textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.4", fc="#ffffe0", ec="#999", alpha=0.95),
            arrowprops=dict(arrowstyle="->", color="#666"),
            fontsize=9,
            zorder=10,
        )
        self._hoverAnnotation.set_visible(False)

        fig.canvas.mpl_connect("pick_event", self._onPick)
        fig.canvas.mpl_connect("motion_notify_event", self._onHover)

        # 样式
        ax.set_title(
            f"{self._n}-gram 聚簇分析（t-SNE 降维，k={k}，轮廓系数={result.silhouette:.3f}）",
            fontsize=11,
            pad=12,
        )
        ax.set_xlabel("t-SNE 维度 1", fontsize=9)
        ax.set_ylabel("t-SNE 维度 2", fontsize=9)
        ax.grid(linestyle="--", alpha=0.25)
        ax.legend(
            loc="upper left",
            fontsize=8,
            framealpha=0.85,
            ncol=1,
            markerscale=0.7,
        )

        fig.tight_layout()

        # 嵌入 Qt
        self._canvas = FigureCanvas(fig)
        self._canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._canvas.setMinimumHeight(320)
        self._chartContainerLayout.addWidget(self._canvas)

    def _onPick(self, event) -> None:
        """点击某个点时高亮并显示其 N-gram 文本"""
        ind = event.ind
        if ind is None or len(ind) == 0:
            return
        idx = ind[0]  # 取第一个被选中的索引
        data = getattr(self, "_scatterData", None)
        if data is None:
            return
        labels = data["labels"]
        if 0 <= idx < len(labels):
            cid = data["clusterIds"][idx]
            _showInfoBar(
                "info",
                "N-gram 详情",
                f"「{labels[idx]}」— 簇 {cid + 1}",
                self,
                duration=2000,
            )

    def _onHover(self, event) -> None:
        """鼠标悬停时显示 N-gram 文本气泡"""
        if event.inaxes is None:
            if self._hoverAnnotation is not None:
                self._hoverAnnotation.set_visible(False)
                if self._canvas is not None:
                    self._canvas.draw_idle()
            return

        data = getattr(self, "_scatterData", None)
        if data is None:
            return

        points = data["points"]
        labels = data["labels"]

        # 找最近的散点
        mx, my = event.xdata, event.ydata
        if mx is None or my is None:
            return
        dists = np.sqrt((points[:, 0] - mx) ** 2 + (points[:, 1] - my) ** 2)
        nearestIdx = int(np.argmin(dists))
        nearestDist = float(dists[nearestIdx])

        # 阈值：点大小平均值对应半径约 10 像素
        threshold = (points[:, 0].max() - points[:, 0].min()) * 0.04
        if nearestDist < threshold and 0 <= nearestIdx < len(labels):
            if self._hoverAnnotation is not None:
                self._hoverAnnotation.xy = (
                    points[nearestIdx, 0],
                    points[nearestIdx, 1],
                )
                self._hoverAnnotation.set_text(labels[nearestIdx])
                self._hoverAnnotation.set_visible(True)
                if self._canvas is not None:
                    self._canvas.draw_idle()
        else:
            if self._hoverAnnotation is not None:
                self._hoverAnnotation.set_visible(False)
                if self._canvas is not None:
                    self._canvas.draw_idle()

    # ------------------------------------------------------------------
    # 簇摘要渲染
    # ------------------------------------------------------------------

    def _renderClusterSummary(self, result: NgramClusterResult) -> None:
        """在右下区域渲染每个簇的 Top N-grams 摘要"""
        # 清空旧内容
        while self._summaryLayout.count() > 0:
            item = self._summaryLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        title = StrongBodyLabel("簇摘要", self)
        self._summaryLayout.addWidget(title)

        k = result.k
        for cid in range(k):
            topNgrams = result.cluster_top_ngrams.get(cid, [])
            size = result.cluster_sizes.get(cid, 0)
            if not topNgrams:
                continue

            color = _clusterColor(cid)
            # 将 top N-grams 连成一行，用 " | " 分隔
            text = f"簇 {cid + 1}（{size} 个）：" + "  |  ".join(topNgrams)
            label = CaptionLabel(text, self)
            label.setWordWrap(True)
            label.setStyleSheet(
                f"color: {color}; font-size: 11px; "
                f"border-left: 3px solid {color}; padding-left: 6px;"
            )
            self._summaryLayout.addWidget(label)

        self._summaryWidget.setVisible(True)

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    def _export(self, fmt: str) -> None:
        """导出图表为 PNG / SVG"""
        if self._figure is None:
            return
        label = "bigram" if self._n == 2 else f"{self._n}gram"
        defaultName = f"{label}_cluster.{fmt}"
        filterStr = (
            f"{fmt.upper()} Files (*.{fmt})" if fmt == "svg" else "PNG Files (*.png)"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "导出聚簇分析图", defaultName, filterStr
        )
        if not path:
            return
        if not path.endswith(f".{fmt}"):
            path += f".{fmt}"
        try:
            self._figure.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
            _showInfoBar("success", "导出成功", f"图片已保存至：{path}", self)
        except Exception as e:
            _showInfoBar("error", "导出失败", str(e), self, duration=3000)

    # ------------------------------------------------------------------
    # 静态便捷方法
    # ------------------------------------------------------------------

    @staticmethod
    def show(
        ngramDf: pd.DataFrame,
        n: int = 3,
        maxClusters: int = 8,
        minFreq: int = 3,
        maxNgrams: int = 2000,
        parent=None,
    ) -> None:
        """便捷方法：弹出聚类分析对话框（非模态）"""
        dlg = NgramClusterDialog(
            ngramDf=ngramDf,
            n=n,
            maxClusters=maxClusters,
            minFreq=minFreq,
            maxNgrams=maxNgrams,
            parent=parent,
        )
        dlg.exec()
