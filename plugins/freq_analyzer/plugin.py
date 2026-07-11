# coding: utf-8
"""
词频分析插件（对标 AntConc 词频统计）

主入口：定义 Plugin 类与主界面 FreqAnalyzerWidget
"""

import json
import logging
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

import pandas as pd

# 添加本地 lib 路径到 sys.path（jieba 在 lib/jieba/ 下）
pluginDir = os.path.dirname(os.path.abspath(__file__))
jiebaPath = os.path.join(pluginDir, "lib", "jieba")
PRESETS_DIR = os.path.join(pluginDir, "presets")
if os.path.exists(jiebaPath) and jiebaPath not in sys.path:
    sys.path.insert(0, jiebaPath)

try:
    import jieba  # type: ignore

    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False
    jieba = None

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QTableWidgetItem,
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
    LineEdit,
    MessageBoxBase,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SpinBox,
    SubtitleLabel,
    SwitchButton,
    TransparentPushButton,
    TransparentToggleToolButton,
)
from qfluentwidgetspro import RoundTableWidget as ProRoundTableWidget
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib import pyplot as plt

from app.core.plugin.base import PluginBase

# 导入同目录的 freq_engine（兼容插件作为非 package 加载的场景）
try:
    from .freq_engine import (
        FrequencyAnalyzer,
        loadExcelColumn,
        loadTextFile,
        DEFAULT_STOPWORDS_ZH,
        DEFAULT_STOPWORDS_EN,
        CleanRule,
        TextCleaner,
    )
except ImportError:
    # 兜底：插件作为独立 module 加载时
    import importlib.util as _ilu

    _freqEnginePath = os.path.join(pluginDir, "freq_engine.py")
    _freqEngineModuleName = "freq_engine"
    _spec = _ilu.spec_from_file_location(_freqEngineModuleName, _freqEnginePath)
    _mod = _ilu.module_from_spec(_spec)
    # 必须先注册到 sys.modules，否则 dataclass 装饰器在访问
    # sys.modules.get(cls.__module__).__dict__ 时会报
    # 'NoneType' object has no attribute '__dict__'
    sys.modules[_freqEngineModuleName] = _mod
    _spec.loader.exec_module(_mod)
    FrequencyAnalyzer = _mod.FrequencyAnalyzer
    loadExcelColumn = _mod.loadExcelColumn
    loadTextFile = _mod.loadTextFile
    DEFAULT_STOPWORDS_ZH = _mod.DEFAULT_STOPWORDS_ZH
    DEFAULT_STOPWORDS_EN = _mod.DEFAULT_STOPWORDS_EN
    CleanRule = _mod.CleanRule
    TextCleaner = getattr(_mod, "TextCleaner", None)


# 设置 matplotlib 中文字体
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False


class ExcelLoadWorker(QThread):
    """后台加载 Excel 列文本，避免大文件阻塞 UI 线程"""

    progress = Signal(str)  # 当前正在加载的文件名
    finished = Signal(dict)  # {baseName: text}
    failed = Signal(str, str)  # (fileName, errorMsg)

    def __init__(self, files: List[str], column: Optional[str], parent=None):
        super().__init__(parent)
        self._files = files
        self._column = column

    def run(self):
        result: Dict[str, str] = {}
        for f in self._files:
            baseName = os.path.basename(f)
            self.progress.emit(baseName)
            try:
                text = loadExcelColumn(f, column=self._column)
                result[baseName] = text
            except Exception as e:
                self.failed.emit(baseName, str(e))
        self.finished.emit(result)


class FreqWorkerThread(QThread):
    """词频分析后台线程"""

    progress = Signal(int, str)
    finished = Signal(object, object)  # (unigramDf, ngramDf)
    failed = Signal(str)

    def __init__(
        self,
        fileToText: Dict[str, str],
        minLength: int,
        maxLength: int,
        caseSensitive: bool,
        excludeNumbers: bool,
        useStopwords: bool,
        useJieba: bool,
        ngramN: int = 2,
        ngramMinFreq: int = 2,
        cleanRule: Optional[CleanRule] = None,
    ):
        super().__init__()
        self.fileToText = fileToText
        self.minLength = minLength
        self.maxLength = maxLength
        self.caseSensitive = caseSensitive
        self.excludeNumbers = excludeNumbers
        self.useStopwords = useStopwords
        self.useJieba = useJieba
        self.ngramN = max(2, int(ngramN))  # N-gram 阶数，至少为 2
        self.ngramMinFreq = max(1, int(ngramMinFreq))  # 过滤最低频次
        self.cleanRule = cleanRule or CleanRule()
        self._isCanceled = False

    def cancel(self):
        self._isCanceled = True

    def run(self):
        try:
            self.progress.emit(10, "正在初始化分析器...")

            if self._isCanceled:
                return

            analyzer = FrequencyAnalyzer(
                minLength=self.minLength,
                maxLength=self.maxLength,
                caseSensitive=self.caseSensitive,
                excludeNumbers=self.excludeNumbers,
                useStopwords=self.useStopwords,
                useJieba=self.useJieba and JIEBA_AVAILABLE,
                cleanRule=self.cleanRule,
            )

            self.progress.emit(30, "正在分词与统计...")
            if self._isCanceled:
                return

            unigramDf = analyzer.analyzeCorpus(self.fileToText)

            if self._isCanceled:
                return

            label = "Bigram" if self.ngramN == 2 else f"{self.ngramN}-gram"
            self.progress.emit(70, f"正在生成 {label}...")
            ngramDf = analyzer.analyzeNgrams(self.fileToText, n=self.ngramN)
            if not ngramDf.empty:
                ngramDf = ngramDf[ngramDf["Freq"] >= self.ngramMinFreq]

            self.progress.emit(100, "分析完成")
            self.finished.emit(unigramDf, ngramDf)

        except Exception as e:
            logger.error(f"[FreqWorkerThread] 分析异常: {traceback.format_exc()}")
            self.failed.emit(str(e))


# ---------------------------------------------------------------------------
# 弹窗通用辅助函数
# ---------------------------------------------------------------------------


def _showInfoBar(
    kind: str,
    title: str,
    content: str,
    parent: QWidget,
    duration: int = 2500,
) -> None:
    """统一 InfoBar 调用，避免重复传递固定参数。

    Args:
        kind: "success" | "error" | "warning" | "info"
        title: 通知标题
        content: 通知正文
        parent: 父组件
        duration: 显示时长（毫秒），默认 2500
    """
    getattr(InfoBar, kind)(
        title,
        content,
        Qt.Orientation.Horizontal,
        True,
        duration,
        InfoBarPosition.TOP_RIGHT,
        parent,
    )


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


def _makeScrollArea(dialog: "MessageBoxBase", widget: QWidget) -> ScrollArea:
    """将 widget 包裹进透明无边框 ScrollArea 并返回。"""
    scrollArea = ScrollArea(dialog)
    scrollArea.setWidget(widget)
    scrollArea.setWidgetResizable(True)
    scrollArea.setStyleSheet("border: none; background: transparent;")
    return scrollArea


def _setupDialogClose(dialog: "MessageBoxBase", width: int = 640) -> None:
    """在弹窗底部加关闭按钮并隐藏默认 buttonGroup，设置固定宽度。"""
    closeBtn = PushButton("关闭", dialog)
    closeBtn.clicked.connect(dialog.accept)
    dialog.buttonLayout.addWidget(closeBtn)
    dialog.buttonGroup.hide()
    dialog.widget.setFixedWidth(width)


def _makeAlignedItem(text: str) -> QTableWidgetItem:
    """创建右对齐 + 垂直居中的表格项。"""
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


def _makeSwitchButton(text: str, parent: QWidget) -> "SwitchButton":
    """创建 SwitchButton，并固定 on/off 文本一致，避免勾选后变 "On"。

    qfluentwidgets 的 SwitchButton 默认 on/off 显示为 "On"/"Off"，
    通过同时调用 setOnText/setOffText 为同一文本，可保持 UI 文字稳定。
    """
    btn = SwitchButton(text, parent)
    btn.setOnText(text)
    btn.setOffText(text)
    return btn


class ZipfDialog(MessageBoxBase):
    """Zipf 曲线图弹窗"""

    def __init__(self, df, parent=None):
        super().__init__(parent)
        self.df = df
        self._figure = None

        # 标题栏
        _makeDialogHeader(self, ":app/icons/Chart.svg", "Zipf 曲线图", self.accept)

        # 画布
        self.canvas = FigureCanvas(Figure(figsize=(7, 5), dpi=100))
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # 导出按钮行
        btnLayout = QHBoxLayout()
        btnLayout.addStretch(1)
        pngBtn = PushButton("导出 PNG", self)
        pngBtn.clicked.connect(lambda: self._export("png"))
        svgBtn = PushButton("导出 SVG", self)
        svgBtn.clicked.connect(lambda: self._export("svg"))
        btnLayout.addWidget(pngBtn)
        btnLayout.addWidget(svgBtn)

        # 布局
        self.viewLayout.setContentsMargins(15, 15, 15, 10)
        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(_makeScrollArea(self, self.canvas), 1)
        self.viewLayout.addLayout(btnLayout)

        _setupDialogClose(self)
        self._draw()

    def _draw(self):
        if self._figure:
            plt.close(self._figure)
        fig = Figure(figsize=(7, 5), dpi=100)
        ax = fig.add_subplot(111)
        self._figure = fig

        if self.df is None or self.df.empty:
            ax.text(0.5, 0.5, "无数据", ha="center", va="center", fontsize=14)
            ax.axis("off")
        else:
            ranks = self.df["Rank"].values
            freqs = self.df["Freq"].values
            ax.loglog(ranks, freqs, "o-", markersize=4, color="#4477AA", alpha=0.7)
            ax.set_xlabel("词频排名 (Rank)", fontsize=11)
            ax.set_ylabel("词频 (Frequency)", fontsize=11)
            ax.set_title(
                f"Zipf 分布曲线（共 {len(self.df)} 个词）",
                fontsize=12,
                pad=12,
            )
            ax.grid(linestyle="--", alpha=0.4, which="both")
            ax.legend(["实际分布"], loc="upper right")

        fig.tight_layout()
        self.canvas.figure = fig
        self.canvas.draw()

    def _export(self, fmt: str):
        if not self._figure:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出图表",
            f"Zipf曲线.{fmt}",
            f"{fmt.upper()} Files (*.{fmt})" if fmt == "svg" else "PNG Files (*.png)",
        )
        if not path:
            return
        if not path.endswith(f".{fmt}"):
            path += f".{fmt}"
        self._figure.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
        _showInfoBar("success", "导出成功", f"图片已保存至：{path}", self)


class NgramDialog(MessageBoxBase):
    """N-gram 频率统计弹窗（支持任意阶数 n>=2）"""

    def __init__(self, ngramDf, n: int = 2, parent=None):
        super().__init__(parent)
        self.df = ngramDf
        self.n = max(2, int(n))
        self.label = "Bigram" if self.n == 2 else f"{self.n}-gram"

        # 标题栏
        _makeDialogHeader(
            self,
            ":app/icons/Chart.svg",
            f"{self.label} {self.n} 元组频率统计",
            self.accept,
        )

        # 表格
        self.table = ProRoundTableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["排名", self.label, "频次", "范围", "占比"]
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
        for col, w in [(0, 60), (2, 80), (3, 80), (4, 80)]:
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Fixed
            )
            self.table.setColumnWidth(col, w)

        if ngramDf is not None and not ngramDf.empty:
            self.table.setRowCount(len(ngramDf))
            for i in range(len(ngramDf)):
                row = ngramDf.iloc[i]
                self.table.setItem(i, 0, QTableWidgetItem(str(int(row["Rank"]))))
                self.table.setItem(i, 1, QTableWidgetItem(str(row["Ngram"])))
                self.table.setItem(i, 4, _makeAlignedItem(f"{row['Pct']:.2f}%"))
                self.table.setItem(i, 2, _makeAlignedItem(str(int(row["Freq"]))))
                self.table.setItem(i, 3, _makeAlignedItem(str(int(row["Range"]))))

        scrollArea = _makeScrollArea(self, self.table)

        # 状态
        if ngramDf is None or ngramDf.empty:
            statusText = f"无 {self.label} 数据（请先分析并调低阈值）"
        else:
            statusText = f"共 {len(ngramDf)} 个 {self.label}"
        statusLabel = CaptionLabel(statusText, self)
        statusLabel.setStyleSheet("color: #666; font-size: 12px;")

        # 导出按钮
        exportLayout = QHBoxLayout()
        exportLayout.addStretch(1)
        exportCsvBtn = PushButton("导出 CSV", self)
        exportCsvBtn.clicked.connect(self._exportCsv)
        exportLayout.addWidget(exportCsvBtn)

        # 布局
        self.viewLayout.setContentsMargins(15, 15, 15, 10)
        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(scrollArea, 1)
        self.viewLayout.addWidget(statusLabel)
        self.viewLayout.addLayout(exportLayout)

        _setupDialogClose(self)

    def _exportCsv(self):
        if self.df is None or self.df.empty:
            return
        defaultName = (
            "bigrams.csv" if self.n == 2 else f"{self.label.replace('-', '')}.csv"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, f"导出 {self.label} CSV", defaultName, "CSV Files (*.csv)"
        )
        if not path:
            return
        if not path.endswith(".csv"):
            path += ".csv"
        try:
            self.df.to_csv(path, index=False, encoding="utf-8-sig")
            _showInfoBar("success", "导出成功", f"已保存：{path}", self)
        except Exception as e:
            _showInfoBar("error", "导出失败", str(e), self, duration=3000)


class SelectColumnDialog(MessageBoxBase):
    """Excel 列名选择对话框

    - 左侧列出所有列名（高亮"共同列"）
    - 右侧预览前 5 行非空值
    - 顶部提供"全选/全不选"等快捷按钮
    """

    def __init__(
        self,
        allColumns: List[str],
        commonColumns: List[str],
        previews: Dict[str, Dict[str, List[str]]],
        selectedBefore: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._result: Optional[str] = None
        self._commonCols = set(commonColumns)
        self._allCols = list(allColumns)
        self._previews = previews
        # 构造期间设为 False，期间任何信号回调都会直接返回，
        # 避免访问尚未初始化的 previewTable
        self._ready: bool = False

        # 标题栏（关闭时 reject）
        _makeDialogHeader(
            self, ":app/icons/Setting.svg", "选择 Excel 列名", self.reject
        )

        # 说明
        infoLabel = CaptionLabel(
            f"共 {len(allColumns)} 列（其中 {len(commonColumns)} 列在所有文件中都有）",
            self,
        )
        infoLabel.setStyleSheet("color: #666; font-size: 12px;")

        # 左侧：列名列表
        self.columnList = ProRoundTableWidget(self)
        self.columnList.setColumnCount(3)
        self.columnList.setHorizontalHeaderLabels(["列名", "类型", "状态"])
        self.columnList.setSelectionBehavior(
            ProRoundTableWidget.SelectionBehavior.SelectRows
        )
        self.columnList.verticalHeader().setVisible(False)
        self.columnList.setEditTriggers(ProRoundTableWidget.EditTrigger.NoEditTriggers)
        self.columnList.setShowGrid(False)
        # 注意：itemSelectionChanged 在 previewTable 创建之后再连接（见下方）
        self.columnList.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.columnList.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Fixed
        )
        self.columnList.setColumnWidth(1, 70)
        self.columnList.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed
        )
        self.columnList.setColumnWidth(2, 80)

        self.columnList.setRowCount(len(allColumns))
        self._columnItems = []
        for i, col in enumerate(allColumns):
            typeGuess = self._guessType(previews, col)
            status = "共同列" if col in self._commonCols else "独有"
            nameItem = QTableWidgetItem(str(col))
            typeItem = QTableWidgetItem(typeGuess)
            typeItem.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            statusItem = QTableWidgetItem(status)
            statusItem.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            # 共同列加粗 + 蓝色
            if col in self._commonCols:
                font = nameItem.font()
                font.setBold(True)
                nameItem.setFont(font)
                nameItem.setForeground(Qt.GlobalColor.blue)
            self.columnList.setItem(i, 0, nameItem)
            self.columnList.setItem(i, 1, typeItem)
            self.columnList.setItem(i, 2, statusItem)
            self._columnItems.append((col, typeGuess))

        # 关键修复：必须在构造完 previewTable 后再连接 itemSelectionChanged，
        # 否则 selectRow() 触发的信号会在 _onSelectionChanged 中访问未初始化的 previewTable。
        # 同时把初始选中也延后到信号连接之后，避免提前触发。
        self.columnList.itemSelectionChanged.connect(self._onSelectionChanged)

        # 选中预选项
        if selectedBefore and selectedBefore in allColumns:
            for i, (col, _) in enumerate(self._columnItems):
                if col == selectedBefore:
                    self.columnList.selectRow(i)
                    break
        else:
            # 默认选中第一个共同列
            for i, (col, _) in enumerate(self._columnItems):
                if col in self._commonCols:
                    self.columnList.selectRow(i)
                    break

        leftWrap = _makeScrollArea(self, self.columnList)

        # 右侧：预览
        self.previewTable = ProRoundTableWidget(self)
        self.previewTable.setColumnCount(2)
        self.previewTable.setHorizontalHeaderLabels(["文件", "前 5 行预览"])
        self.previewTable.verticalHeader().setVisible(False)
        self.previewTable.setEditTriggers(
            ProRoundTableWidget.EditTrigger.NoEditTriggers
        )
        self.previewTable.setShowGrid(False)
        self.previewTable.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.previewTable.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self.previewTable.setColumnWidth(0, 160)
        rightWrap = _makeScrollArea(self, self.previewTable)

        splitLayout = QHBoxLayout()
        splitLayout.addWidget(leftWrap, 1)
        splitLayout.addWidget(rightWrap, 1)

        # 布局
        self.viewLayout.setContentsMargins(15, 15, 15, 10)
        self.viewLayout.addSpacing(4)
        self.viewLayout.addWidget(infoLabel)
        self.viewLayout.addSpacing(4)
        self.viewLayout.addLayout(splitLayout, 1)

        # 底部按钮
        self.yesButton.setText("确定选择")
        self.cancelButton.setText("取消")
        self.yesButton.clicked.connect(self._onAccept)
        self.cancelButton.clicked.connect(self.reject)

        self.widget.setFixedWidth(720)
        self.widget.setFixedHeight(460)

        # 所有子部件构建完毕，再放行信号回调
        self._ready = True

        # 触发初始预览（此时 previewTable 已存在，可安全刷新）
        self._onSelectionChanged()

    def _guessType(self, previews: Dict[str, Dict[str, List[str]]], col: str) -> str:
        """猜测列类型"""
        allVals: List[str] = []
        for filePrev in previews.values():
            allVals.extend(filePrev.get(col, []))
        if not allVals:
            return "空"
        nums = sum(
            1 for v in allVals if v.replace(".", "", 1).replace("-", "", 1).isdigit()
        )
        if nums == len(allVals):
            return "数字"
        if any(0x4E00 <= ord(c) <= 0x9FFF for v in allVals for c in v):
            return "中文"
        if any(c.isalpha() for c in "".join(allVals)):
            return "英文"
        return "文本"

    def _onSelectionChanged(self):
        """选中行变化时刷新预览"""
        # 构造期间信号可能提前触发，统一直接返回
        if not getattr(self, "_ready", False):
            return
        if not hasattr(self, "previewTable") or self.previewTable is None:
            return
        selectedCol = self._currentSelectedColumn()
        if not selectedCol:
            return
        rows = []
        for fileName, colPreviews in self._previews.items():
            vals = colPreviews.get(selectedCol, [])
            preview = " | ".join(v[:30] for v in vals) or "（无数据）"
            rows.append((fileName, preview))
        self.previewTable.setRowCount(len(rows))
        for i, (fname, prev) in enumerate(rows):
            self.previewTable.setItem(i, 0, QTableWidgetItem(fname))
            self.previewTable.setItem(i, 1, QTableWidgetItem(prev))

    def _currentSelectedColumn(self) -> Optional[str]:
        rows = self.columnList.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
        if 0 <= idx < len(self._columnItems):
            return self._columnItems[idx][0]
        return None

    def _onAccept(self):
        col = self._currentSelectedColumn()
        if col is None:
            _showInfoBar("warning", "提示", "请先选择一列", self, duration=2000)
            return
        self._result = col
        self.accept()

    def getSelectedColumn(self) -> Optional[str]:
        return self._result


class CleanPreviewDialog(MessageBoxBase):
    """清洗前后对比预览对话框"""

    def __init__(self, fileName: str, original: str, cleaned: str, parent=None):
        super().__init__(parent)
        self._fileName = fileName
        self._original = original
        self._cleaned = cleaned

        # 标题栏
        _makeDialogHeader(
            self, ":app/icons/Setting.svg", f"清洗预览 - {fileName}", self.accept
        )

        # 原文
        originalBox = self._buildTextBox("原文（前 500 字）", original, "#FFF7E6")
        # 清洗后
        cleanedBox = self._buildTextBox("清洗后", cleaned, "#F6FFED")

        diffLabel = CaptionLabel(
            f"原文长度: {len(original)}  →  清洗后长度: {len(cleaned)}  "
            f"（共移除 {max(0, len(original) - len(cleaned))} 字符）",
            self,
        )
        diffLabel.setStyleSheet("color: #666; font-size: 12px;")

        # 布局
        self.viewLayout.setContentsMargins(15, 15, 15, 10)
        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(originalBox, 1)
        self.viewLayout.addSpacing(4)
        self.viewLayout.addWidget(diffLabel)
        self.viewLayout.addSpacing(4)
        self.viewLayout.addWidget(cleanedBox, 1)

        _setupDialogClose(self)
        self.widget.setFixedHeight(520)

    def _buildTextBox(self, title: str, text: str, bgColor: str) -> QWidget:
        """构造一个带标题的多行只读文本框"""
        wrap = QWidget(self)
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        titleLabel = BodyLabel(title, self)
        titleLabel.setStyleSheet("font-size: 12px; font-weight: 600;")
        v.addWidget(titleLabel)
        edit = PlainTextEdit(self)
        edit.setPlainText(text or "")
        edit.setReadOnly(True)
        edit.setStyleSheet(f"background: {bgColor}; border-radius: 4px;")
        edit.setFixedHeight(180)
        v.addWidget(edit, 1)
        return wrap


class FreqAnalyzerWidget(QWidget):
    """词频分析主界面（对标 AntConc）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FreqAnalyzerInterface")

        self.fileToText: Dict[str, str] = {}
        self.unigramDf = None
        self.ngramDf = None
        self.ngramN = 2  # 当前 N-gram 阶数
        self._worker = None
        self._excelLoader = None  # ExcelLoadWorker 引用，防止被 GC

        self._initUi()

    def _initUi(self):
        # 外层滚动
        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setStyleSheet("border: none; background: transparent;")

        scrollContent = QWidget()
        scrollLayout = QVBoxLayout(scrollContent)
        scrollLayout.setContentsMargins(16, 16, 16, 16)
        scrollLayout.setSpacing(12)

        # 标题
        titleLabel = SubtitleLabel("词频分析统计", scrollContent)
        titleLabel.setStyleSheet("font-size: 18px; font-weight: 600;")
        scrollLayout.addWidget(titleLabel)

        # ===== 文件选择与参数 =====
        fileCard = CardWidget(self)
        fileLayout = QVBoxLayout(fileCard)
        fileLayout.setContentsMargins(16, 12, 16, 12)
        fileLayout.setSpacing(8)

        # 加载按钮
        loadLayout = QHBoxLayout()
        self.excelBtn = PrimaryPushButton("加载 Excel", self)
        self.excelBtn.setIcon(FluentIcon.FOLDER)
        self.excelBtn.clicked.connect(self._loadExcel)

        self.textBtn = PushButton("加载文本文件", self)
        self.textBtn.setIcon(FluentIcon.DOCUMENT)
        self.textBtn.clicked.connect(self._loadText)

        self.clearBtn = TransparentPushButton("清空", self)
        self.clearBtn.clicked.connect(self._clearAll)

        loadLayout.addWidget(self.excelBtn)
        loadLayout.addWidget(self.textBtn)
        loadLayout.addStretch(1)
        loadLayout.addWidget(self.clearBtn)
        fileLayout.addLayout(loadLayout)

        # Excel 列选择（点击按钮从列表中选）
        columnRow = QHBoxLayout()
        columnLabel = BodyLabel("Excel 列名:", self)
        columnLabel.setStyleSheet("font-size: 12px; min-width: 80px;")
        self.columnEdit = LineEdit(self)
        self.columnEdit.setPlaceholderText("（留空则使用全部文本列）")
        self.columnEdit.setFixedWidth(180)
        self.columnEdit.setReadOnly(True)
        self.pickColumnBtn = PushButton("选择列…", self)
        self.pickColumnBtn.setIcon(FluentIcon.MENU)
        self.pickColumnBtn.clicked.connect(self._pickExcelColumn)
        columnRow.addWidget(columnLabel)
        columnRow.addWidget(self.columnEdit)
        columnRow.addWidget(self.pickColumnBtn)
        columnRow.addStretch(1)
        self.fileCountLabel = CaptionLabel("未加载文件", self)
        self.fileCountLabel.setStyleSheet("color: #666;")
        columnRow.addWidget(self.fileCountLabel)
        fileLayout.addLayout(columnRow)

        # 参数设置
        paramCard = CardWidget(self)
        paramLayout = QVBoxLayout(paramCard)
        paramLayout.setContentsMargins(16, 12, 16, 12)
        paramLayout.setSpacing(8)

        paramTitle = BodyLabel("分析参数", self)
        paramTitle.setStyleSheet("font-size: 13px; font-weight: 600;")
        paramLayout.addWidget(paramTitle)

        paramRow1 = QHBoxLayout()
        paramRow1.setSpacing(20)

        minLabel = BodyLabel("最短词长:", self)
        minLabel.setStyleSheet("font-size: 12px;")
        self.minSpin = SpinBox(self)
        self.minSpin.setRange(1, 20)
        self.minSpin.setValue(1)
        # self.minSpin.setFixedWidth(70)

        maxLabel = BodyLabel("最长词长:", self)
        maxLabel.setStyleSheet("font-size: 12px;")
        self.maxSpin = SpinBox(self)
        self.maxSpin.setRange(1, 100)
        self.maxSpin.setValue(50)
        # self.maxSpin.setFixedWidth(70)

        ngramNLabel = BodyLabel("N-gram 阶数:", self)
        ngramNLabel.setStyleSheet("font-size: 12px;")
        self.ngramNSpin = SpinBox(self)
        self.ngramNSpin.setRange(2, 5)
        self.ngramNSpin.setValue(2)

        ngramMinFreqLabel = BodyLabel("N-gram 最低频次:", self)
        ngramMinFreqLabel.setStyleSheet("font-size: 12px;")
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

        self.zipfBtn = TransparentPushButton("Zipf 曲线图", self)
        self.zipfBtn.setIcon(FluentIcon.CHAT)
        self.zipfBtn.clicked.connect(self._showZipf)
        self.zipfBtn.setEnabled(False)

        self.ngramBtn = TransparentPushButton(self._ngramButtonText(2), self)
        self.ngramBtn.setIcon(FluentIcon.SCROLL)
        self.ngramBtn.clicked.connect(self._showNgram)
        self.ngramBtn.setEnabled(False)

        # 阶数变化时同步按钮标题，让用户看到当前会分析的 N-gram 类型
        self.ngramNSpin.valueChanged.connect(self._onNgramNChanged)

        self.exportBtn = TransparentPushButton("导出 CSV", self)
        self.exportBtn.setIcon(FluentIcon.SAVE)
        self.exportBtn.clicked.connect(self._exportCsv)
        self.exportBtn.setEnabled(False)

        opRow.addWidget(self.analyzeBtn)
        opRow.addWidget(self.zipfBtn)
        opRow.addWidget(self.ngramBtn)
        opRow.addWidget(self.exportBtn)
        opRow.addStretch(1)
        paramLayout.addLayout(opRow)

        # 状态
        self.statusLabel = CaptionLabel("加载文件后点击「开始分析」", self)
        self.statusLabel.setStyleSheet("color: #666; font-size: 12px;")
        paramLayout.addWidget(self.statusLabel)

        scrollLayout.addWidget(fileCard)
        scrollLayout.addWidget(paramCard)
        scrollLayout.addWidget(self._buildCleanCard(scrollContent))

        # ===== 词频表 =====
        tableCard = CardWidget(self)
        tableLayout = QVBoxLayout(tableCard)
        tableLayout.setContentsMargins(16, 12, 16, 12)
        tableLayout.setSpacing(8)

        tableTitle = BodyLabel("词频统计表", self)
        tableTitle.setStyleSheet("font-size: 13px; font-weight: 600;")
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
        mainLayout = QVBoxLayout(self)
        mainLayout.setContentsMargins(0, 0, 0, 0)
        mainLayout.addWidget(self.scrollArea)

    def _buildCleanCard(self, parent: QWidget) -> CardWidget:
        """构造「自定义清洗规则」卡片（分词前的预处理）

        提供以下子规则：
            - 移除英文 / 数字 / 标点 / 特殊符号（emoji、货币、数学符号等）
            - 统一小写
            - 自定义移除字符串（每行一项）
            - 自定义正则表达式（每行一项）
            - 自定义替换（格式：原串=>新串）
        """
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        title = BodyLabel("自定义清洗规则（分词前预处理）", self)
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout.addWidget(title)

        hint = CaptionLabel(
            "提示：规则按以下顺序应用：替换 → 移除英文/数字/标点/特殊符号 → 自定义字符串/正则 → 合并空白 → 小写化",
            self,
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ===== 第一行：开关 =====
        switchRow = QHBoxLayout()
        switchRow.setSpacing(20)

        self.cleanEnableSwitch = _makeSwitchButton("启用清洗", self)
        self.cleanEnableSwitch.setChecked(False)
        switchRow.addWidget(self.cleanEnableSwitch)

        self.cleanEnglishSwitch = _makeSwitchButton("移除英文", self)
        self.cleanEnglishSwitch.setChecked(False)
        switchRow.addWidget(self.cleanEnglishSwitch)

        self.cleanDigitSwitch = _makeSwitchButton("移除数字", self)
        self.cleanDigitSwitch.setChecked(False)
        switchRow.addWidget(self.cleanDigitSwitch)

        self.cleanPunctSwitch = _makeSwitchButton("移除标点", self)
        self.cleanPunctSwitch.setChecked(False)
        switchRow.addWidget(self.cleanPunctSwitch)

        self.cleanSpecialSwitch = _makeSwitchButton("移除特殊符号", self)
        self.cleanSpecialSwitch.setChecked(False)
        switchRow.addWidget(self.cleanSpecialSwitch)

        self.cleanLowerSwitch = _makeSwitchButton("统一小写", self)
        self.cleanLowerSwitch.setChecked(False)
        switchRow.addWidget(self.cleanLowerSwitch)
        switchRow.addStretch(1)
        layout.addLayout(switchRow)

        # ===== 第二行：自定义输入（三列多行文本框） =====
        inputRow = QHBoxLayout()
        inputRow.setSpacing(12)

        removeWrap = QVBoxLayout()
        removeLabel = BodyLabel("自定义移除字符串（每行一项）", self)
        removeLabel.setStyleSheet("font-size: 12px;")
        self.cleanRemoveEdit = PlainTextEdit(self)
        self.cleanRemoveEdit.setPlaceholderText(
            "例如：\n【示例】\n[广告]\nhttp://\nwww."
        )
        self.cleanRemoveEdit.setFixedHeight(110)
        removeWrap.addWidget(removeLabel)
        removeWrap.addWidget(self.cleanRemoveEdit, 1)
        inputRow.addLayout(removeWrap, 1)

        regexWrap = QVBoxLayout()
        regexLabel = BodyLabel("自定义正则表达式（每行一项）", self)
        regexLabel.setStyleSheet("font-size: 12px;")
        self.cleanRegexEdit = PlainTextEdit(self)
        self.cleanRegexEdit.setPlaceholderText(
            "例如：\n\\d{4,}  (4 位以上数字)\n\\b[A-Z]+\\b  (全大写单词)"
        )
        self.cleanRegexEdit.setFixedHeight(110)
        regexWrap.addWidget(regexLabel)
        regexWrap.addWidget(self.cleanRegexEdit, 1)
        inputRow.addLayout(regexWrap, 1)

        replaceWrap = QVBoxLayout()
        replaceLabel = BodyLabel("自定义替换（原串=>新串，每行一项）", self)
        replaceLabel.setStyleSheet("font-size: 12px;")
        self.cleanReplaceEdit = PlainTextEdit(self)
        self.cleanReplaceEdit.setPlaceholderText("例如：\n人工智能=>AI\n机器学习=>ML")
        self.cleanReplaceEdit.setFixedHeight(110)
        replaceWrap.addWidget(replaceLabel)
        replaceWrap.addWidget(self.cleanReplaceEdit, 1)
        inputRow.addLayout(replaceWrap, 1)

        layout.addLayout(inputRow)

        # ===== 第三行：操作按钮 =====
        btnRow = QHBoxLayout()

        # 预设区（只读：仅读取 presets/ 目录中的 JSON 官方预设）
        presetLabel = BodyLabel("清洗预设:", self)
        presetLabel.setStyleSheet("font-size: 12px;")
        btnRow.addWidget(presetLabel)
        self.presetCombo = ComboBox(self)
        self.presetCombo.setMinimumWidth(260)
        self._reloadPresetCombo()
        btnRow.addWidget(self.presetCombo)
        applyPresetBtn = PushButton("应用预设", self)
        applyPresetBtn.setIcon(FluentIcon.DOWNLOAD)
        applyPresetBtn.clicked.connect(self._applyPreset)
        btnRow.addWidget(applyPresetBtn)

        # 预览 / 重置
        previewBtn = PushButton("预览清洗效果", self)
        previewBtn.setIcon(FluentIcon.VIEW)
        previewBtn.clicked.connect(self._previewCleaning)
        btnRow.addWidget(previewBtn)

        resetBtn = TransparentPushButton("恢复默认", self)
        resetBtn.clicked.connect(self._resetCleanUi)
        btnRow.addWidget(resetBtn)

        btnRow.addStretch(1)
        self.cleanSummaryLabel = CaptionLabel("", self)
        self.cleanSummaryLabel.setStyleSheet("color: #666; font-size: 11px;")
        btnRow.addWidget(self.cleanSummaryLabel)
        layout.addLayout(btnRow)

        # 联动
        self.cleanEnableSwitch.checkedChanged.connect(self._onCleanEnableChanged)
        self._onCleanEnableChanged(self.cleanEnableSwitch.isChecked())
        for w in (
            self.cleanEnglishSwitch,
            self.cleanDigitSwitch,
            self.cleanPunctSwitch,
            self.cleanSpecialSwitch,
            self.cleanLowerSwitch,
            self.cleanRemoveEdit,
            self.cleanRegexEdit,
            self.cleanReplaceEdit,
        ):
            if isinstance(w, SwitchButton):
                w.checkedChanged.connect(self._refreshCleanSummary)
            else:
                w.textChanged.connect(self._refreshCleanSummary)
        self._refreshCleanSummary()
        return card

    def _onCleanEnableChanged(self, checked: bool) -> None:
        """总开关切换时启用/禁用子项"""
        for w in (
            self.cleanEnglishSwitch,
            self.cleanDigitSwitch,
            self.cleanPunctSwitch,
            self.cleanSpecialSwitch,
            self.cleanLowerSwitch,
            self.cleanRemoveEdit,
            self.cleanRegexEdit,
            self.cleanReplaceEdit,
        ):
            w.setEnabled(checked)

    def _refreshCleanSummary(self) -> None:
        """根据 UI 状态刷新摘要标签"""
        rule = self._collectCleanRule()
        if not rule.isEnabled():
            self.cleanSummaryLabel.setText("当前未启用任何清洗规则")
            return
        bits: List[str] = []
        if rule.removeEnglish:
            bits.append("英文")
        if rule.removeDigits:
            bits.append("数字")
        if rule.removePunct:
            bits.append("标点")
        if rule.removeSpecialSymbols:
            bits.append("特殊符号")
        if rule.customRemoveList:
            bits.append(f"自定义字符串×{len(rule.customRemoveList)}")
        if rule.customRegexList:
            bits.append(f"自定义正则×{len(rule.customRegexList)}")
        if rule.replaceMap:
            bits.append(f"自定义替换×{len(rule.replaceMap)}")
        if rule.lowercase:
            bits.append("统一小写")
        self.cleanSummaryLabel.setText("已启用：" + " / ".join(bits))

    def _collectCleanRule(self) -> CleanRule:
        """从 UI 收集清洗规则（无论总开关是否打开均返回完整规则对象）"""

        def _splitLines(text: str) -> List[str]:
            return [line.strip() for line in (text or "").splitlines() if line.strip()]

        replaceMap: Dict[str, str] = {}
        for line in _splitLines(self.cleanReplaceEdit.toPlainText()):
            if "=>" in line:
                src, _, dst = line.partition("=>")
                src = src.strip()
                dst = dst.strip()
                if src:
                    replaceMap[src] = dst

        return CleanRule(
            removeEnglish=self.cleanEnglishSwitch.isChecked(),
            removeDigits=self.cleanDigitSwitch.isChecked(),
            removePunct=self.cleanPunctSwitch.isChecked(),
            removeSpecialSymbols=self.cleanSpecialSwitch.isChecked(),
            lowercase=self.cleanLowerSwitch.isChecked(),
            customRemoveList=_splitLines(self.cleanRemoveEdit.toPlainText()),
            customRegexList=_splitLines(self.cleanRegexEdit.toPlainText()),
            replaceMap=replaceMap,
        )

    def _resetCleanUi(self) -> None:
        """恢复默认（关闭所有清洗规则，清空自定义输入）"""
        self.cleanEnableSwitch.setChecked(False)
        self.cleanEnglishSwitch.setChecked(False)
        self.cleanDigitSwitch.setChecked(False)
        self.cleanPunctSwitch.setChecked(False)
        self.cleanSpecialSwitch.setChecked(False)
        self.cleanLowerSwitch.setChecked(False)
        self.cleanRemoveEdit.setPlainText("")
        self.cleanRegexEdit.setPlainText("")
        self.cleanReplaceEdit.setPlainText("")
        self._onCleanEnableChanged(False)

    # ------------------------------------------------------------------
    # 清洗预设
    # ------------------------------------------------------------------
    def _scanPresetFiles(self) -> List[Tuple[str, str]]:
        """扫描 presets/ 目录，返回 [(displayName, absPath), ...]

        - 仅扫描 *.json 文件
        - 以下划线 `_` 开头的文件被视作模板/草稿，会被忽略
        - 若目录不存在则自动创建空目录
        """
        try:
            os.makedirs(PRESETS_DIR, exist_ok=True)
            entries: List[Tuple[str, str]] = []
            for name in sorted(os.listdir(PRESETS_DIR)):
                if not name.lower().endswith(".json"):
                    continue
                if name.startswith("_"):
                    # 以下划线开头视为模板/草稿，跳过
                    continue
                absPath = os.path.join(PRESETS_DIR, name)
                if not os.path.isfile(absPath):
                    continue
                try:
                    with open(absPath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    presetName = str(data.get("name") or os.path.splitext(name)[0])
                    entries.append((presetName, absPath))
                except Exception as e:
                    logger.error(f"[_scanPresetFiles] 解析预设失败 {absPath}: {e}")
            return entries
        except Exception as e:
            logger.error(f"[_scanPresetFiles] 扫描预设目录失败: {e}")
            return []

    def _reloadPresetCombo(self) -> None:
        """重新载入预设下拉框内容"""
        if not hasattr(self, "presetCombo"):
            return
        self.presetCombo.clear()
        files = self._scanPresetFiles()
        if not files:
            self.presetCombo.addItem("(无可用预设)", userData=None)
            return
        for name, absPath in files:
            # 纯预设名直接展示，userData 为文件绝对路径
            self.presetCombo.addItem(name, userData=absPath)

    def _applyPreset(self) -> None:
        """根据当前选中的预设项，一键应用清洗规则到 UI

        预设仅从 plugins/freq_analyzer/presets/ 目录读取，
        严禁运行时写入，确保规则来源可控。
        """
        path = self.presetCombo.currentData()
        if not path or not isinstance(path, str):
            _showInfoBar("info", "提示", "请先选择预设项", self, duration=2000)
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            ruleDict = payload.get("rule", {})
            rule = self._ruleFromDict(ruleDict)
            label = str(
                payload.get("name") or os.path.splitext(os.path.basename(path))[0]
            )
        except Exception as e:
            logger.error(f"[_applyPreset] 加载预设失败 {path}: {e}")
            _showInfoBar("error", "应用失败", f"预设加载失败：{e}", self, duration=3000)
            return

        # 把规则映射到 UI（同步开启总开关）
        self._applyRuleToUi(rule)
        self.cleanEnableSwitch.setChecked(True)
        self._onCleanEnableChanged(True)
        self._refreshCleanSummary()
        logger.info(f"[_applyPreset] 已应用预设：{label}")
        _showInfoBar("success", "预设已应用", f"已加载：{label}", self, duration=2000)

    @classmethod
    def _ruleFromDict(cls, d: Dict[str, Any]) -> CleanRule:
        return CleanRule(
            removeEnglish=bool(d.get("removeEnglish", False)),
            removeDigits=bool(d.get("removeDigits", False)),
            removePunct=bool(d.get("removePunct", False)),
            removeWhitespace=bool(d.get("removeWhitespace", True)),
            removeSpecialSymbols=bool(d.get("removeSpecialSymbols", False)),
            customRemoveList=list(d.get("customRemoveList", []) or []),
            customRegexList=list(d.get("customRegexList", []) or []),
            replaceMap=dict(d.get("replaceMap", {}) or {}),
            lowercase=bool(d.get("lowercase", False)),
        )

    def _applyRuleToUi(self, rule: CleanRule) -> None:
        """把 CleanRule 写入清洗卡片 UI（不切换总开关）"""
        self.cleanEnglishSwitch.setChecked(rule.removeEnglish)
        self.cleanDigitSwitch.setChecked(rule.removeDigits)
        self.cleanPunctSwitch.setChecked(rule.removePunct)
        self.cleanSpecialSwitch.setChecked(rule.removeSpecialSymbols)
        self.cleanLowerSwitch.setChecked(rule.lowercase)
        self.cleanRemoveEdit.setPlainText("\n".join(rule.customRemoveList or []))
        self.cleanRegexEdit.setPlainText("\n".join(rule.customRegexList or []))
        replaceLines = [f"{k}=>{v}" for k, v in (rule.replaceMap or {}).items()]
        self.cleanReplaceEdit.setPlainText("\n".join(replaceLines))

    def _previewCleaning(self) -> None:
        """预览前 500 字清洗效果"""
        if not self.fileToText:
            _showInfoBar("warning", "提示", "请先加载语料文件", self, duration=2000)
            return
        if not self.cleanEnableSwitch.isChecked():
            _showInfoBar(
                "info", "提示", "请先开启「启用清洗」开关", self, duration=2000
            )
            return
        firstName, firstText = next(iter(self.fileToText.items()))
        sample = (firstText or "")[:500]
        rule = self._collectCleanRule()
        logger.debug(f"[_previewCleaning] 文件={firstName}, 规则={rule}")

        # TextCleaner 在模块顶部已通过相对导入或 importlib 兜底加载，直接引用
        if TextCleaner is not None:
            cleanedSample = TextCleaner(rule).clean(sample)
        else:
            tmpAnalyzer = FrequencyAnalyzer(cleanRule=rule)
            cleanedSample = tmpAnalyzer.cleaner.clean(sample)

        CleanPreviewDialog(firstName, sample, cleanedSample, self.window()).exec()

    def _loadExcel(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 Excel 文件",
            "",
            "Excel Files (*.xlsx *.xls *.XLSX *.XLS *.xIsx *.Xlsx *.Xls);;All Files (*)",
        )
        if not files:
            return

        # 1) 解析所有文件的列名（多文件取交集）
        fileToColumns: Dict[str, List[str]] = {}
        filePreviews: Dict[str, Dict[str, List[str]]] = {}
        for f in files:
            try:
                df = pd.read_excel(f, engine="openpyxl", dtype=str, nrows=5)
                cols = [str(c) for c in df.columns]
                fileToColumns[os.path.basename(f)] = cols
                # 每列预览前 5 行非空值
                preview: Dict[str, List[str]] = {}
                for c in cols:
                    vals = df[c].astype(str).fillna("").replace("nan", "").tolist()
                    vals = [v for v in vals if v]
                    preview[c] = vals[:5]
                filePreviews[os.path.basename(f)] = preview
            except Exception as e:
                logger.error(f"[_loadExcel] 读取文件 {os.path.basename(f)} 失败: {e}")
                _showInfoBar(
                    "error",
                    "读取失败",
                    f"{os.path.basename(f)}: {e}",
                    self,
                    duration=3000,
                )
                return

        if not fileToColumns:
            return

        # 取所有文件共有的列（intersection）
        commonCols = set(next(iter(fileToColumns.values())))
        for cols in fileToColumns.values():
            commonCols &= set(cols)

        if not commonCols:
            _showInfoBar(
                "error", "列名不一致", "所选文件没有共同的列名", self, duration=3000
            )
            return

        # 2) 弹出列选择对话框
        allCols = list(next(iter(fileToColumns.values())))
        selectedSoFar = self.columnEdit.text().strip()
        dialog = SelectColumnDialog(
            allCols,
            commonCols,
            filePreviews,
            selectedSoFar,
            self.window(),
        )
        if not dialog.exec():
            return
        column = dialog.getSelectedColumn()

        # 3) 保存选中的列名到 UI
        self.columnEdit.setText(column or "")

        # 4) 后台线程加载文本，避免大文件阻塞 UI
        self._startExcelLoad(files, column)

    def _startExcelLoad(self, files: List[str], column: Optional[str]) -> None:
        """启动后台 ExcelLoadWorker，加载期间禁用操作按钮并显示进度。"""
        logger.info(
            f"[_startExcelLoad] 开始后台加载 {len(files)} 个文件，列={column!r}"
        )
        self.excelBtn.setEnabled(False)
        self.textBtn.setEnabled(False)
        self.analyzeBtn.setEnabled(False)
        self.statusLabel.setText("正在加载文件...")

        loader = ExcelLoadWorker(files, column, self)
        self._excelLoader = loader  # 持有引用，防止被 GC

        loader.progress.connect(
            lambda name: self.statusLabel.setText(f"正在加载：{name}")
        )
        loader.failed.connect(self._onExcelLoadFailed)
        loader.finished.connect(self._onExcelLoadFinished)
        loader.start()

    def _onExcelLoadFailed(self, fileName: str, errMsg: str) -> None:
        logger.error(f"[ExcelLoadWorker] 加载失败 {fileName}: {errMsg}")
        _showInfoBar("error", "加载失败", f"{fileName}: {errMsg}", self, duration=3000)

    def _onExcelLoadFinished(self, result: Dict[str, str]) -> None:
        logger.info(f"[_onExcelLoadFinished] 加载完成，共 {len(result)} 个文件")
        self.fileToText.update(result)
        self._updateFileCount()
        self.excelBtn.setEnabled(True)
        self.textBtn.setEnabled(True)
        self.analyzeBtn.setEnabled(True)
        self.statusLabel.setText("文件加载完成，点击「开始分析」")

    def _pickExcelColumn(self):
        """手动打开列选择对话框（无需新加载文件）"""
        if not self.fileToText:
            _showInfoBar("warning", "提示", "请先加载 Excel 文件，再选择列", self)
            return
        # 重用 _loadExcel 的列解析逻辑：弹出文件选择对话框
        self._loadExcel()

    def _loadText(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择文本文件", "", "Text Files (*.txt *.md);;All Files (*)"
        )
        if not files:
            return
        for f in files:
            try:
                text = loadTextFile(f)
                self.fileToText[os.path.basename(f)] = text
            except Exception as e:
                logger.error(f"[_loadText] 读取文件 {os.path.basename(f)} 失败: {e}")
                _showInfoBar(
                    "error",
                    "加载失败",
                    f"{os.path.basename(f)}: {e}",
                    self,
                    duration=3000,
                )
        self._updateFileCount()

    def _clearAll(self):
        self.fileToText = {}
        self.unigramDf = None
        self.ngramDf = None
        self.table.setRowCount(0)
        self.zipfBtn.setEnabled(False)
        self.ngramBtn.setEnabled(False)
        self.exportBtn.setEnabled(False)
        self._updateFileCount()
        self.statusLabel.setText("已清空")

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
        n = len(self.fileToText)
        total = sum(len(t) for t in self.fileToText.values())
        self.fileCountLabel.setText(f"已加载 {n} 个文件，{total:,} 字符")

    def _runAnalysis(self):
        if not self.fileToText:
            _showInfoBar("warning", "提示", "请先加载语料文件", self, duration=2000)
            return

        if self._worker and self._worker.isRunning():
            return

        self.ngramN = self.ngramNSpin.value()
        logger.info(
            f"[_runAnalysis] 开始分析，文件数={len(self.fileToText)}, N={self.ngramN}"
        )
        self.analyzeBtn.setEnabled(False)
        self.statusLabel.setText("正在分析...")

        self._worker = FreqWorkerThread(
            self.fileToText,
            minLength=self.minSpin.value(),
            maxLength=self.maxSpin.value(),
            caseSensitive=self.caseSwitch.isChecked(),
            excludeNumbers=self.numberSwitch.isChecked(),
            useStopwords=self.stopSwitch.isChecked(),
            useJieba=self.jiebaSwitch.isChecked(),
            ngramN=self.ngramN,
            ngramMinFreq=self.ngramMinFreqSpin.value(),
            cleanRule=(
                self._collectCleanRule()
                if self.cleanEnableSwitch.isChecked()
                else CleanRule()
            ),
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
                i, 3, _makeAlignedItem(f"{int(row['Range'])}/{len(self.fileToText)}")
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


class Plugin(PluginBase):
    """词频分析插件"""

    manifest = {
        "id": "com.prismatica.freq_analyzer",
        "name": "词频分析",
        "version": "1.0.0",
        "apiVersion": "1.0",
        "description": "对标 AntConc 的词频统计模块",
        "author": "Prismatica",
        "category": "analysis",
        "permissions": ["file:read"],
        "dependencies": {"python": ["jieba", "pandas", "openpyxl", "matplotlib"]},
        "entry": "plugin.py",
        "minAppVersion": "1.0.0",
    }

    def onLoad(self) -> bool:
        logger.info("[Plugin] 词频分析插件已加载")
        return True

    def onActivate(self):
        logger.info("[Plugin] 词频分析插件已激活")

    def onDeactivate(self):
        logger.info("[Plugin] 词频分析插件已停用")

    def onUnload(self):
        logger.info("[Plugin] 词频分析插件已卸载")

    def getIconPath(self) -> str:
        pluginDir = os.path.dirname(os.path.abspath(__file__))
        if os.path.exists(os.path.join(pluginDir, "icon.png")):
            return "icon.png"
        if os.path.exists(os.path.join(pluginDir, "icon.svg")):
            return "icon.svg"
        return ""

    def getInterface(self) -> QWidget:
        return FreqAnalyzerWidget()
