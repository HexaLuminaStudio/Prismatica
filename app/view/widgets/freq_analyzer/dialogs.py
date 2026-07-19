"""词频分析模块的弹窗组件集合

包含:
    - ZipfDialog:               Zipf 曲线图弹窗
    - NgramDialog:              N-gram 频率统计弹窗
    - SelectColumnDialog:       Excel 列名选择对话框
    - CleanPreviewDialog:       清洗前后对比预览对话框
    - AdvancedSettingsDialog:   词频分析高级参数弹窗（主词频最低频次 / N-gram 阶数 / N-gram 最低频次）
    - StopwordsDialog:          停用词导入与编辑弹窗
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
    FluentIcon,
    MessageBoxBase,
    PlainTextEdit,
    PushButton,
    RadioButton,
    SpinBox,
    StrongBodyLabel,
)
from qfluentwidgetspro import RoundTableWidget as ProRoundTableWidget

from .ui_helpers import (
    _makeAlignedItem,
    _makeDialogHeader,
    _makeScrollArea,
    _setupDialogClose,
    _showInfoBar,
)
from .freq_engine import (
    defaultStopwords,
    loadStopwordsFromFile,
    parseStopwordsFromText,
    saveStopwordsToFile,
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
    """N-gram 频率统计弹窗（支持任意阶数 n>=2）

    N>=3 时提供「聚簇分析」按钮，可在后台多线程中执行 t-SNE + KMeans 聚类并可视化。
    """

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

        # 导出按钮 + 聚簇分析按钮
        exportLayout = QHBoxLayout()
        exportLayout.addStretch(1)

        # N>=3 时显示「聚簇分析」按钮（后台多线程执行，不阻塞 UI）
        if self.n >= 3:
            from app.view.widgets.freq_analyzer.ngram_cluster_widget import (
                NgramClusterDialog,
            )

            self._clusterBtn = PushButton("聚簇分析", self)
            self._clusterBtn.setIcon(FluentIcon.SEARCH)
            self._clusterBtn.clicked.connect(self._openClusterAnalysis)
            exportLayout.addWidget(self._clusterBtn)

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

    def _openClusterAnalysis(self) -> None:
        """打开聚簇分析弹窗（后台线程执行，不阻塞 UI）"""
        if self.df is None or self.df.empty:
            _showInfoBar(
                "warning",
                "提示",
                "无 N-gram 数据，无法进行聚簇分析",
                self,
                duration=2000,
            )
            return
        from app.view.widgets.freq_analyzer.ngram_cluster_widget import (
            NgramClusterDialog,
        )

        NgramClusterDialog.show(
            ngramDf=self.df,
            n=self.n,
            parent=self.window(),
        )

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


class AdvancedSettingsDialog(MessageBoxBase):
    """词频分析高级参数设置弹窗

    收纳从主页参数行移出的"次要"选项，避免主页参数行拥挤：
        - 主词频最低频次（unigramMinFreq）
        - N-gram 阶数      （ngramN）
        - N-gram 最低频次  （ngramMinFreq）

    弹窗行为：
        - 点「确定」→ 调用 self.accept()，外部读取 _getSettings() 同步到主页控件
        - 点「取消」→ 调用 self.reject()，外部不读取，保留原值
        - 点关闭按钮 → 同「取消」

    API:
        settings = AdvancedSettingsDialog.getSettings(
            unigramMinFreq, ngramN, ngramMinFreq, parent
        )
        # 返回 dict 或 None（取消时）
    """

    def __init__(
        self,
        unigramMinFreq: int = 1,
        ngramN: int = 2,
        ngramMinFreq: int = 2,
        parent=None,
    ):
        super().__init__(parent)
        self._initial = {
            "unigramMinFreq": int(unigramMinFreq),
            "ngramN": int(ngramN),
            "ngramMinFreq": int(ngramMinFreq),
        }

        # 顶部标题
        _makeDialogHeader(self, ":app/icons/Setting.svg", "高级设置", self.accept)

        # ----- 主词频最低频次 -----
        unigramTitle = StrongBodyLabel("主词频筛选", self)
        unigramHint = CaptionLabel(
            "仅显示出现次数 ≥ 该阈值的词；设为 1 不过滤（显示所有词）", self
        )
        unigramHint.setStyleSheet("color: #666; font-size: 11px;")

        unigramRow = QHBoxLayout()
        unigramLabel = BodyLabel("主词频最低频次:", self)
        unigramLabel.setFixedWidth(150)
        self.unigramMinFreqSpin = SpinBox(self)
        self.unigramMinFreqSpin.setRange(1, 1000)
        self.unigramMinFreqSpin.setValue(self._initial["unigramMinFreq"])
        unigramRow.addWidget(unigramLabel)
        unigramRow.addWidget(self.unigramMinFreqSpin)
        unigramRow.addStretch(1)

        # ----- N-gram 设置 -----
        ngramTitle = StrongBodyLabel("N-gram 设置", self)
        ngramHint = CaptionLabel(
            "Bigram / Trigram 等 N 元组频次的阶数与最低频次过滤", self
        )
        ngramHint.setStyleSheet("color: #666; font-size: 11px;")

        ngramNRow = QHBoxLayout()
        ngramNLabel = BodyLabel("N-gram 阶数:", self)
        ngramNLabel.setFixedWidth(150)
        self.ngramNSpin = SpinBox(self)
        self.ngramNSpin.setRange(2, 5)
        self.ngramNSpin.setValue(self._initial["ngramN"])
        ngramNRow.addWidget(ngramNLabel)
        ngramNRow.addWidget(self.ngramNSpin)
        ngramNRow.addStretch(1)

        ngramFreqRow = QHBoxLayout()
        ngramFreqLabel = BodyLabel("N-gram 最低频次:", self)
        ngramFreqLabel.setFixedWidth(150)
        self.ngramMinFreqSpin = SpinBox(self)
        self.ngramMinFreqSpin.setRange(1, 1000)
        self.ngramMinFreqSpin.setValue(self._initial["ngramMinFreq"])
        ngramFreqRow.addWidget(ngramFreqLabel)
        ngramFreqRow.addWidget(self.ngramMinFreqSpin)
        ngramFreqRow.addStretch(1)

        # ----- 整体布局 -----
        self.viewLayout.setContentsMargins(20, 16, 20, 12)
        self.viewLayout.setSpacing(8)

        self.viewLayout.addWidget(unigramTitle)
        self.viewLayout.addWidget(unigramHint)
        self.viewLayout.addLayout(unigramRow)
        self.viewLayout.addSpacing(8)

        self.viewLayout.addWidget(ngramTitle)
        self.viewLayout.addWidget(ngramHint)
        self.viewLayout.addLayout(ngramNRow)
        self.viewLayout.addLayout(ngramFreqRow)
        self.viewLayout.addStretch(1)

        self.buttonGroup.hide()
        self.widget.setFixedWidth(460)
        self.widget.setFixedHeight(360)

    def _getSettings(self) -> Dict[str, int]:
        """读取当前弹窗内的设置（仅在 accept() 之后由外部调用）。"""
        return {
            "unigramMinFreq": int(self.unigramMinFreqSpin.value()),
            "ngramN": int(self.ngramNSpin.value()),
            "ngramMinFreq": int(self.ngramMinFreqSpin.value()),
        }

    @staticmethod
    def getSettings(
        unigramMinFreq: int,
        ngramN: int,
        ngramMinFreq: int,
        parent=None,
    ) -> Optional[Dict[str, int]]:
        """静态便捷方法：弹出对话框并返回用户确定的设置；取消则返回 None。

        Args:
            unigramMinFreq: 当前主词频最低频次（用作弹窗初始值）
            ngramN:         当前 N-gram 阶数（用作弹窗初始值）
            ngramMinFreq:   当前 N-gram 最低频次（用作弹窗初始值）
            parent:         父窗口
        Returns:
            字典 {'unigramMinFreq': int, 'ngramN': int, 'ngramMinFreq': int}，或 None（取消）
        """
        dlg = AdvancedSettingsDialog(
            unigramMinFreq=unigramMinFreq,
            ngramN=ngramN,
            ngramMinFreq=ngramMinFreq,
            parent=parent,
        )
        if dlg.exec():
            return dlg._getSettings()
        return None


class StopwordsDialog(MessageBoxBase):
    """停用词导入与编辑弹窗

    核心功能:
        1. 查看当前停用词列表（只读浏览,实时统计总数）
        2. 直接在弹窗内编辑（每行一个词;支持 # 开头注释行）
        3. 导入 TXT 文件:
            - 选择"追加"模式:在当前列表末尾追加文件中读出的词（自动去重）
            - 选择"替换"模式:完全使用文件内容替换当前列表
        4. 一键恢复默认中英文停用词表
        5. 导出当前列表到 TXT 文件

    弹窗行为:
        - 点「保存」→ 返回新的停用词列表(已去重);外部用 setStopwords 替换
        - 点「取消」→ 返回 None,外部保留原值
        - 点关闭按钮 → 同「取消」

    静态便捷方法:
        result = StopwordsDialog.edit(currentWords, parent)
        # 返回 List[str] 或 None
    """

    def __init__(
        self,
        currentWords: Optional[List[str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        # 当前停用词 → 字符串列表
        self._initial: List[str] = (
            list(currentWords) if currentWords else defaultStopwords()
        )

        # 顶部标题
        _makeDialogHeader(self, ":app/icons/Dictionary.svg", "停用词管理", self.reject)

        # ----- 顶部摘要 + 操作按钮 -----
        topRow = QHBoxLayout()
        self.summaryLabel = CaptionLabel(
            self._makeSummaryText(len(self._initial)), self
        )
        self.summaryLabel.setStyleSheet("color: #666; font-size: 11px;")
        topRow.addWidget(self.summaryLabel)
        topRow.addStretch(1)

        importBtn = PushButton("导入 TXT…", self)
        importBtn.setIcon(FluentIcon.DOWNLOAD)
        importBtn.clicked.connect(self._onImportClicked)
        topRow.addWidget(importBtn)

        exportBtn = PushButton("导出 TXT", self)
        exportBtn.setIcon(FluentIcon.SAVE)
        exportBtn.clicked.connect(self._onExportClicked)
        topRow.addWidget(exportBtn)

        resetBtn = PushButton("恢复默认", self)
        resetBtn.setIcon(FluentIcon.RETURN)
        resetBtn.clicked.connect(self._onResetClicked)
        topRow.addWidget(resetBtn)

        # ----- 导入模式选择 -----
        modeRow = QHBoxLayout()
        modeLabel = BodyLabel("导入模式:", self)
        modeLabel.setFixedWidth(80)
        self.appendRadio = RadioButton("追加到当前列表", self)
        self.appendRadio.setChecked(True)
        self.replaceRadio = RadioButton("完全替换当前列表", self)
        modeRow.addWidget(modeLabel)
        modeRow.addWidget(self.appendRadio)
        modeRow.addWidget(self.replaceRadio)
        modeRow.addStretch(1)

        # ----- 可编辑文本框 -----
        hintLabel = CaptionLabel(
            "每行一个停用词；以 # 开头的行视为注释。可直接编辑后点「保存」生效。",
            self,
        )
        hintLabel.setStyleSheet("color: #888; font-size: 11px;")
        hintLabel.setWordWrap(True)

        self.editor = PlainTextEdit(self)
        self.editor.setPlainText("\n".join(self._initial))
        self.editor.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 12px;"
        )
        self.editor.setMinimumHeight(280)
        # 实时同步统计
        self.editor.textChanged.connect(self._onTextChanged)

        # ----- 整体布局 -----
        self.viewLayout.setContentsMargins(20, 16, 20, 12)
        self.viewLayout.setSpacing(8)
        self.viewLayout.addLayout(topRow)
        self.viewLayout.addLayout(modeRow)
        self.viewLayout.addWidget(hintLabel)
        self.viewLayout.addWidget(self.editor, 1)

        # 底部按钮: 取消 + 保存
        okBtn = PushButton("保存", self)
        okBtn.setFixedWidth(96)
        okBtn.clicked.connect(self.accept)
        cancelBtn = PushButton("取消", self)
        cancelBtn.setFixedWidth(96)
        cancelBtn.clicked.connect(self.reject)

        self.buttonGroup.hide()
        self.buttonLayout.addStretch(1)
        self.buttonLayout.addWidget(cancelBtn)
        self.buttonLayout.addSpacing(8)
        self.buttonLayout.addWidget(okBtn)
        self.widget.setFixedWidth(640)
        self.widget.setFixedHeight(560)

    # ------------------------------------------------------------------
    # 摘要 / 状态
    # ------------------------------------------------------------------
    @staticmethod
    def _makeSummaryText(count: int) -> str:
        return f"当前停用词共 {count} 个"

    def _refreshSummary(self) -> None:
        words = parseStopwordsFromText(self.editor.toPlainText())
        self.summaryLabel.setText(self._makeSummaryText(len(words)))

    def _onTextChanged(self) -> None:
        """编辑器内容变化时实时更新摘要计数。"""
        self._refreshSummary()

    # ------------------------------------------------------------------
    # 按钮回调
    # ------------------------------------------------------------------
    def _onImportClicked(self) -> None:
        """从 TXT 文件导入停用词;按选定模式合并到编辑器。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择停用词文件",
            "",
            "Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            newWords = loadStopwordsFromFile(path)
        except FileNotFoundError as e:
            _showInfoBar("error", "导入失败", str(e), self, duration=2500)
            return
        except Exception as e:
            _showInfoBar(
                "error", "导入失败", f"读取停用词文件失败: {e}", self, duration=2500
            )
            return

        if not newWords:
            _showInfoBar(
                "warning",
                "导入为空",
                "文件中未找到有效停用词（可能全为空行或注释）",
                self,
                duration=2500,
            )
            return

        if self.replaceRadio.isChecked():
            # 完全替换:保留导入的词 + 头部注释行
            self.editor.setPlainText("\n".join(newWords))
            modeText = "已替换"
        else:
            # 追加模式:合并到当前文本末尾
            current = parseStopwordsFromText(self.editor.toPlainText())
            currentSet = set(current)
            added: List[str] = []
            for w in newWords:
                if w not in currentSet:
                    added.append(w)
                    currentSet.add(w)
            # 在文本末尾追加新行（若已有内容,加换行符）
            existingText = self.editor.toPlainText()
            addition = (
                "\n" if existingText and not existingText.endswith("\n") else ""
            ) + "\n".join(added)
            self.editor.setPlainText(existingText + addition)
            modeText = f"已追加 {len(added)} 个"

        self._refreshSummary()
        _showInfoBar(
            "success",
            "导入成功",
            f"从文件导入 {len(newWords)} 个词,{modeText}",
            self,
            duration=2200,
        )

    def _onExportClicked(self) -> None:
        """把当前编辑器内容（去除空行）导出为 TXT。"""
        words = parseStopwordsFromText(self.editor.toPlainText())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出停用词",
            "stopwords.txt",
            "Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            saveStopwordsToFile(path, words)
        except Exception as e:
            _showInfoBar("error", "导出失败", f"写入文件失败: {e}", self, duration=2500)
            return
        _showInfoBar(
            "success",
            "导出成功",
            f"已保存 {len(words)} 个停用词到 {path}",
            self,
            duration=2200,
        )

    def _onResetClicked(self) -> None:
        """恢复为默认中英文停用词表（确认后覆盖）。"""
        words = defaultStopwords()
        self.editor.setPlainText("\n".join(words))
        self._refreshSummary()
        _showInfoBar(
            "info",
            "已恢复默认",
            f"已填充 {len(words)} 个默认停用词（点击「保存」后生效）",
            self,
            duration=2200,
        )

    # ------------------------------------------------------------------
    # 取值（仅在 accept() 后由外部读取）
    # ------------------------------------------------------------------
    def getWords(self) -> List[str]:
        """从编辑器中解析出停用词列表(已去重、跳过空行/注释)。"""
        return parseStopwordsFromText(self.editor.toPlainText())

    @staticmethod
    def edit(
        currentWords: Optional[List[str]] = None,
        parent=None,
    ) -> Optional[List[str]]:
        """静态便捷方法:弹出对话框,返回用户确认的停用词列表;取消则返回 None。"""
        dlg = StopwordsDialog(currentWords=currentWords, parent=parent)
        if dlg.exec():
            return dlg.getWords()
        return None


class PosPreviewDialog(MessageBoxBase):
    """POS 标注预览弹窗(只读,带「复制到剪贴板」)。

    设计目标:
        - 接收已格式化的多行文本(由 caller 构造)
        - 使用等宽字体显示,行号靠左
        - 提供「复制全部」按钮(方便粘到外部编辑器/文档)
    """

    def __init__(
        self,
        text: str,
        title: str = "POS 预览",
        parent=None,
    ):
        super().__init__(parent)
        _makeDialogHeader(self, ":app/icons/Information.svg", title, self.reject)

        self.viewLayout.setContentsMargins(20, 16, 20, 12)
        self.viewLayout.setSpacing(8)

        self.editor = PlainTextEdit(self)
        self.editor.setPlainText(text or "")
        self.editor.setReadOnly(True)
        # self.editor.setStyleSheet(
        #     "font-family: Consolas, 'Courier New', monospace; font-size: 12px;"
        # )
        self.editor.setMinimumHeight(360)
        self.viewLayout.addWidget(self.editor, 1)

        copyBtn = PushButton("复制全部", self)
        copyBtn.clicked.connect(self._copyAll)
        okBtn = PushButton("关闭", self)
        okBtn.clicked.connect(self.accept)

        self.buttonGroup.hide()
        self.buttonLayout.addWidget(copyBtn)
        self.buttonLayout.addStretch(1)
        self.buttonLayout.addWidget(okBtn)
        self.widget.setFixedWidth(560)
        self.widget.setFixedHeight(500)

    def _copyAll(self) -> None:
        try:
            from PySide6.QtGui import QGuiApplication

            QGuiApplication.clipboard().setText(self.editor.toPlainText())
            _showInfoBar(
                "success", "已复制", "预览内容已复制到剪贴板", self, duration=1800
            )
        except Exception as e:
            _showInfoBar("error", "复制失败", str(e), self, duration=2200)

    @staticmethod
    def showPreview(
        text: str,
        title: str = "POS 预览",
        parent=None,
    ) -> None:
        """静态便捷方法:弹出只读预览弹窗。"""
        dlg = PosPreviewDialog(text=text, title=title, parent=parent)
        dlg.exec()
