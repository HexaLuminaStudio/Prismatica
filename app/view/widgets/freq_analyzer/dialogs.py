"""词频分析模块的弹窗组件集合

包含:
    - ZipfDialog:           Zipf 曲线图弹窗
    - NgramDialog:          N-gram 频率统计弹窗
    - SelectColumnDialog:   Excel 列名选择对话框
    - CleanPreviewDialog:   清洗前后对比预览对话框
"""

from typing import Dict, List, Optional

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
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
    MessageBoxBase,
    PlainTextEdit,
    PushButton,
)
from qfluentwidgetspro import RoundTableWidget as ProRoundTableWidget

from .ui_helpers import (
    _makeAlignedItem,
    _makeDialogHeader,
    _makeScrollArea,
    _setupDialogClose,
    _showInfoBar,
)


class ZipfDialog(MessageBoxBase):
    """Zipf 曲线图弹窗"""

    def __init__(self, df, parent=None):
        super().__init__(parent)
        self.df = df
        self._figure = None

        _makeDialogHeader(self, ":app/icons/Chart.svg", "Zipf 曲线图", self.accept)

        self.canvas = FigureCanvas(Figure(figsize=(7, 5), dpi=100))
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        btnLayout = QHBoxLayout()
        btnLayout.addStretch(1)
        pngBtn = PushButton("导出 PNG", self)
        pngBtn.clicked.connect(lambda: self._export("png"))
        svgBtn = PushButton("导出 SVG", self)
        svgBtn.clicked.connect(lambda: self._export("svg"))
        btnLayout.addWidget(pngBtn)
        btnLayout.addWidget(svgBtn)

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

        # 关键修复：必须在构造完 previewTable 后再连接 itemSelectionChanged
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
