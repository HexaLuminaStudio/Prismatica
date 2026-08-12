# coding: utf-8
"""Concordance 分布图（Plot 视图）— matplotlib 渲染

需求：
    - 横轴 = 文本位置（token index），纵轴 = 文件（多文件时分行）
    - 每个命中点渲染为竖线/色块，展示「关键词在文本中分布是否均匀」
    - 后台计算文件级 token 数，UI 不阻塞
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.core.utils import logger
from app.core.services import beginPaidAnalysisExport
from app.view.widgets.prismatica_theme import applyMatplotlibTheme, shellPalette

# matplotlib 后端必须在导入 pyplot 前设置
import matplotlib  # noqa: E402

matplotlib.use("QtAgg", force=True)

import matplotlib.font_manager as fm  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvas,
)  # noqa: E402

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import PushButton, qconfig

# ---------------------------------------------------------------------------
# 中文字体初始化
# ---------------------------------------------------------------------------
_CHINESE_FONT: Optional[str] = None


def _getChineseFont() -> Optional[str]:
    """查找系统可用的中文字体，找不到返回 None 让 matplotlib 使用默认 fallback。"""
    global _CHINESE_FONT
    if _CHINESE_FONT is not None:
        return _CHINESE_FONT if _CHINESE_FONT else None
    candidates = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "WenQuanYi Micro Hei",
        "PingFang SC",
        "STHeiti",
        "Source Han Sans SC",
    ]
    available = {f.name for f in fm.fontManager.ttflist}
    for name in candidates:
        if name in available:
            _CHINESE_FONT = name
            logger.debug(f"[ConcordancePlot] 使用中文字体: {name}")
            return name
    _CHINESE_FONT = ""
    logger.warning(
        "[ConcordancePlot] 未找到中文字体，将使用默认字体（可能无法显示中文）"
    )
    return None


# ---------------------------------------------------------------------------
# ConcordancePlotCanvas
# ---------------------------------------------------------------------------
class ConcordancePlotCanvas(QWidget):
    """Concordance 分布图 — matplotlib FigureCanvas 嵌入组件

    Usage:
        canvas = ConcordancePlotCanvas()
        canvas.render(
            fileToPositions={"file1.txt": [12, 45, 78, ...], ...},
            fileToTokenCounts={"file1.txt": 200, ...},
            searchWord="学习",
        )
    """

    _MAX_FILES_DISPLAY = 30  # 超过此数量不逐文件渲染，改为全库密度图

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("concordancePlotCanvas")
        self._fig: Optional[Figure] = None
        self._canvas: Optional[FigureCanvas] = None
        self._data: Optional[Tuple] = (
            None  # (fileToPositions, fileToTokenCounts, searchWord)
        )
        self._initUi()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _initUi(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 导出按钮栏
        btnRow = QHBoxLayout()
        btnRow.addStretch()
        self._exportPngBtn = PushButton("导出 PNG", self)
        self._exportPngBtn.setFixedHeight(28)
        self._exportPngBtn.clicked.connect(lambda: self._export("png"))
        btnRow.addWidget(self._exportPngBtn)

        self._exportSvgBtn = PushButton("导出 SVG", self)
        self._exportSvgBtn.setFixedHeight(28)
        self._exportSvgBtn.clicked.connect(lambda: self._export("svg"))
        btnRow.addWidget(self._exportSvgBtn)
        layout.addLayout(btnRow)

        # 初始占位
        placeholder = Figure(
            figsize=(8, 3),
            dpi=100,
            facecolor=shellPalette().surface.name(),
        )
        self._canvas = FigureCanvas(placeholder)
        self._canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._canvas.setMinimumHeight(200)
        layout.addWidget(self._canvas, 1)
        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)

    def _applyTheme(self, *_args) -> None:
        """刷新现有画布，避免图表在主题切换后保留旧配色。"""
        palette = shellPalette()
        self.setStyleSheet(
            "QWidget#concordancePlotCanvas {"
            f" background: {palette.surface.name()};"
            " border: none;"
            "}"
        )
        applyMatplotlibTheme(self)

    # ------------------------------------------------------------------
    # 数据入口
    # ------------------------------------------------------------------
    def render(
        self,
        fileToPositions: Dict[str, List[int]],
        fileToTokenCounts: Dict[str, int],
        searchWord: str = "",
    ) -> None:
        """绘制 Concordance 分布图。

        Args:
            fileToPositions: {文件名: [命中位置 tokenIndex 列表]}
            fileToTokenCounts: {文件名: 该文件总 token 数}
            searchWord: 检索词（用于标题）
        """
        self._data = (fileToPositions, fileToTokenCounts, searchWord)
        self._draw(fileToPositions, fileToTokenCounts, searchWord)

    # ------------------------------------------------------------------
    # 绘图核心
    # ------------------------------------------------------------------
    def _draw(
        self,
        fileToPositions: Dict[str, List[int]],
        fileToTokenCounts: Dict[str, int],
        searchWord: str = "",
    ) -> None:
        fontName = _getChineseFont()
        fontProps = {"family": fontName} if fontName else {}

        # 过滤无命中文件
        hitFiles = {f: p for f, p in fileToPositions.items() if p}
        if not hitFiles:
            self._drawEmpty(searchWord, fontProps)
            return

        nFiles = len(hitFiles)
        fileNames = sorted(hitFiles.keys())

        # 文件太多 → 全库密度图
        if nFiles > self._MAX_FILES_DISPLAY:
            self._drawDensity(fileToPositions, fileToTokenCounts, searchWord, fontProps)
            return

        # ---- 逐文件子图 ----
        self._fig = Figure(
            figsize=(10, max(4, nFiles * 1.2)),
            dpi=100,
            facecolor=shellPalette().surface.name(),
        )

        # 颜色映射
        from matplotlib.cm import Blues

        cmap = Blues

        for idx, fname in enumerate(fileNames):
            ax = self._fig.add_subplot(nFiles, 1, idx + 1)
            positions = sorted(fileToPositions.get(fname, []))
            maxTokens = fileToTokenCounts.get(
                fname, max(positions) + 1 if positions else 1
            )

            # 命中密度（用于颜色深浅）
            if positions and maxTokens > 0:
                bins = np.linspace(0, maxTokens, min(100, maxTokens))
                hist, _ = np.histogram(positions, bins=bins)
                density = hist / max(1, hist.max())

                # 每个命中点画竖线
                for pos, den in zip(
                    positions, np.interp(positions, bins[:-1], density)
                ):
                    color = cmap(0.3 + 0.7 * den)
                    ax.axvline(
                        x=pos, ymin=0, ymax=1, color=color, alpha=0.7, linewidth=1.2
                    )

            # 刻度
            ax.set_xlim(0, maxTokens)
            ax.set_ylim(0, 1)
            ax.set_yticks([])
            ax.set_ylabel(
                _truncateFilename(fname, 40),
                rotation=0,
                ha="right",
                va="center",
                fontsize=8,
                **fontProps,
            )

            # 仅最底部子图显示 x 轴标签
            if idx == nFiles - 1:
                ax.set_xlabel("token 位置", fontsize=9, **fontProps)
            else:
                ax.tick_params(labelbottom=False)

            ax.spines["top"].set_visible(False)
            ax.spines["left"].set_visible(False)
            ax.spines["right"].set_visible(False)

        title = f"Concordance Plot — {searchWord}" if searchWord else "Concordance Plot"
        self._fig.suptitle(title, fontsize=12, fontweight="bold", y=0.99, **fontProps)
        self._fig.tight_layout(rect=[0.0, 0.0, 1.0, 0.98])

        # 替换 canvas
        self._replaceCanvas(self._fig)

    # ------------------------------------------------------------------
    # 全库密度图（文件太多时的 fallback）
    # ------------------------------------------------------------------
    def _drawDensity(
        self,
        fileToPositions: Dict[str, List[int]],
        fileToTokenCounts: Dict[str, int],
        searchWord: str,
        fontProps: dict,
    ) -> None:
        """文件数 > _MAX_FILES_DISPLAY 时：将全部命中的全局位置绘制为密度直方图。"""
        self._fig = Figure(
            figsize=(10, 3),
            dpi=100,
            facecolor=shellPalette().surface.name(),
        )
        ax = self._fig.add_subplot(111)

        # 按文件名排序计算累积偏移
        allPositions: List[float] = []
        cumulative = 0
        fileBoundaries: List[Tuple[float, str]] = []
        for fname in sorted(fileToPositions.keys()):
            tokenCount = fileToTokenCounts.get(fname, 0)
            for pos in fileToPositions.get(fname, []):
                allPositions.append(cumulative + pos)
            cumulative += tokenCount
            if tokenCount > 0:
                fileBoundaries.append((cumulative, fname))

        if not allPositions:
            self._drawEmpty(searchWord, fontProps)
            return

        totalTokens = cumulative
        bins = np.linspace(0, totalTokens, min(200, max(50, totalTokens // 50)))
        ax.hist(
            allPositions,
            bins=bins,
            color="#3b82f6",
            alpha=0.7,
            edgecolor="#1d4ed8",
            linewidth=0.3,
        )

        # 文件边界虚线
        for boundary, _ in fileBoundaries[:-1]:  # 不画最后一个边界
            ax.axvline(
                x=boundary, color="#ef4444", linestyle="--", linewidth=0.6, alpha=0.5
            )

        title = f"Concordance Density — {searchWord}（{len(fileToPositions)} 文件）"
        ax.set_title(title, fontsize=11, fontweight="bold", **fontProps)
        ax.set_xlabel("全库累积 token 索引", fontsize=9, **fontProps)
        ax.set_ylabel("命中频次", fontsize=9, **fontProps)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        self._fig.tight_layout()
        self._replaceCanvas(self._fig)

    # ------------------------------------------------------------------
    # 空数据图
    # ------------------------------------------------------------------
    def _drawEmpty(self, searchWord: str, fontProps: dict) -> None:
        self._fig = Figure(
            figsize=(8, 3),
            dpi=100,
            facecolor=shellPalette().surface.name(),
        )
        ax = self._fig.add_subplot(111)
        ax.text(
            0.5,
            0.5,
            "无命中数据" if not searchWord else f'"{searchWord}" 无命中',
            ha="center",
            va="center",
            fontsize=14,
            color=shellPalette().mutedText.name(),
            transform=ax.transAxes,
            **fontProps,
        )
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        self._replaceCanvas(self._fig)

    # ------------------------------------------------------------------
    # Canvas 替换
    # ------------------------------------------------------------------
    def _replaceCanvas(self, fig: Figure) -> None:
        """替换旧的 FigureCanvas，避免内存泄漏。"""
        old = self._canvas
        if old is not None:
            old.setParent(None)
            old.deleteLater()
        self._canvas = FigureCanvas(fig)
        self._canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._canvas.setMinimumHeight(200)
        self.layout().addWidget(self._canvas, 1)
        self._applyTheme()

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def _export(self, fmt: str) -> None:
        if self._fig is None:
            return
        defaultName = "concordance_plot"
        if self._data:
            _, _, sw = self._data
            if sw:
                defaultName = f"concordance_{sw}"
        filePath, _ = QFileDialog.getSaveFileName(
            self,
            f"导出 {fmt.upper()}",
            os.path.join(os.path.expanduser("~"), "Desktop", f"{defaultName}.{fmt}"),
            f"{fmt.upper()} Files (*.{fmt})",
        )
        if not filePath:
            return
        charge = beginPaidAnalysisExport(self.window(), f"导出 KWIC 分布图 {fmt.upper()}")
        if charge is None:
            return
        try:
            self._fig.savefig(filePath, dpi=150, bbox_inches="tight")
            charge.commit()
            logger.info(f"[ConcordancePlot] 导出: {filePath}")
        except Exception as e:
            charge.refund()
            logger.error(f"[ConcordancePlot] 导出失败: {e}")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._canvas is not None:
            self._canvas.deleteLater()
            self._canvas = None
        if self._fig is not None:
            import matplotlib.pyplot as plt

            plt.close(self._fig)
            self._fig = None
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def computeFileTokenCounts(
    fileToText: Dict[str, str],
    engine,
) -> Dict[str, int]:
    """为每个文件计算 token 数（与 ConcordanceEngine 的分词方式一致）。

    Args:
        fileToText: {文件名: 全文}
        engine: ConcordanceEngine 实例（用于复用分词器）

    Returns:
        {文件名: token 数}
    """
    counts: Dict[str, int] = {}
    for fname, text in fileToText.items():
        if not text:
            counts[fname] = 0
            continue
        # 复用引擎的 _tokenizeLines 确保分词结果一致
        pairs = engine._tokenizeLines(text)  # noqa: SLF001
        counts[fname] = len(pairs)
    return counts


def extractHitPositions(
    result,
) -> Tuple[Dict[str, List[int]], str]:
    """从 ConcordanceResult 提取各文件的命中位置。

    Args:
        result: ConcordanceResult 实例

    Returns:
        ({文件名: [tokenIndex 列表]}, 检索词)
    """
    fileToPositions: Dict[str, List[int]] = {}
    for hit in result.hits:
        positions = fileToPositions.setdefault(hit.sourceFile, [])
        positions.append(hit.tokenIndex)
    return fileToPositions, result.searchWord


def _truncateFilename(fname: str, maxLen: int) -> str:
    if len(fname) <= maxLen:
        return fname
    return fname[: maxLen - 3] + "..."
