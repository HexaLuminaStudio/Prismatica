# coding: utf-8
"""
HSK 偏误分析界面
"""

import os
import io

import pandas as pd
from loguru import logger
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QColor, QPalette
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QPushButton,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    FlowLayout,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    SubtitleLabel,
    SwitchButton,
    ToolTipFilter,
    ToolTipPosition,
    TransparentPushButton,
    TransparentToggleToolButton,
    CardWidget,
    CheckBox,
    ScrollArea,
    VerticalSeparator,
)
from qfluentwidgetspro import RoundTableWidget
from matplotlib import pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

import pyperclip
import re

# 偏误类型定义
CHARACTERS_TYPES = {
    "错字 [C]": (r"(\S)\[C\]", True),
    "别字 [B]": (r"\[B(?!C|Q|D)([^\]]+)\]", True),
    "漏字 [L]": (r"(\S)\[L\]", True),
    "多字 [D]": (r"\[D([^\]]+)\]", True),
    "繁体字 [F]": (r"\[F([^\]]+)\]", True),
    "异体字 [Y]": (r"\[Y([^\]]+)\]", True),
    "拼音字 [P]": (r"\[P([^\]]+)\]", True),
    "无法识别 [#]": (r"\[\#\]", False),
    "错误标点 [BC]": (r"\[BC([^\]]+)\]", True),
    "空缺标点 [BQ]": (r"\[BQ([^\]]+)\]", True),
    "多余标点 [BD]": (r"\[BD([^\]]+)\]", True),
}

SENTENCES_TYPES = {
    "未完句标记 [WWJ]": (r"\{WWJ\}", False),
    "把字句错误 [CJba]": (r"\{CJba\}", False),
    "被字句错误 [CJbei]": (r"\{CJbei\}", False),
    "比字句错误 [CJbi]": (r"\{CJbi\}", False),
    "连字句错误 [CJl]": (r"\{CJl\}", False),
    "有字句错误 [CJy]": (r"\{CJy\}", False),
    "是字句错误 [CJs]": (r"\{CJs\}", False),
    "\u201c是\u2026\u2026的\u201d句错误 [CJsd]": (r"\{CJsd\}", False),
    "存现句错误 [CJcx]": (r"\{CJcx\}", False),
    "兼语句错误 [CJjy]": (r"\{CJjy\}", False),
    "连动句错误 [CJld]": (r"\{CJld\}", False),
    "双宾语句错误 [CJshb]": (r"\{CJshb\}", False),
    "形容词谓语句错误 [CJxw]": (r"\{CJxw\}", False),
    "句子成分残缺/多余 [CJ-/+]": (r"\{CJ[+-][a-z]+([^\}]*)\}", True),
    "语序错误 [CJX]": (r"\{CJX\}", False),
    "句式杂糅 [CJZR]": (r"\{CJZR\}", False),
    "重叠错误 [CJcd]": (r"\{CJcd\}", False),
    "固定格式错误 [CJgd]": (r"\{CJgd\}", False),
    "句处理存疑 [CJ?]": (r"\{CJ\?\}", False),
}

WORDS_TYPES = {
    "错词 [CC]": (r"\{CC([^\}]*)\}", True),
    "离合词错误 [CLH]": (r"(\S+)\{CLH\}", True),
    "外文词 [W]": (r"\{W(?!WJ)([^\}]*)\}", True),
    "缺词 [CQ]": (r"\{CQ([^\}]*)\}", True),
    "多词 [CD]": (r"\{CD([^\}]*)\}", True),
    "存疑词 [CY]": (r"\{CY\}", False),
}

ERROR_TYPES = CHARACTERS_TYPES | SENTENCES_TYPES | WORDS_TYPES


class MultiSelectFilter(QWidget):
    """多选筛选组件"""

    selectionChanged = Signal(list)

    def __init__(self, items: list, parent=None):
        super().__init__(parent)
        self.items = items
        self.checkboxes = {}
        self._initUi()

    def _initUi(self):
        # 创建主布局
        mainLayout = QVBoxLayout()
        mainLayout.setContentsMargins(0, 4, 0, 4)
        mainLayout.setSpacing(6)
        self.setLayout(mainLayout)

        # 标题栏
        headerLayout = QHBoxLayout()
        headerLayout.setSpacing(8)

        titleLabel = BodyLabel("类型:", self)
        titleLabel.setStyleSheet("color: #666; font-size: 12px;")
        headerLayout.addWidget(titleLabel)

        self.checkAllBtn = QPushButton("全选/取消", self)
        self.checkAllBtn.setStyleSheet(
            """
            QPushButton {
                border: none;
                color: #1890FF;
                font-size: 11px;
                padding: 2px 6px;
                background: transparent;
            }
            QPushButton:hover {
                background: #E6F7FF;
                border-radius: 3px;
            }
        """
        )
        self.checkAllBtn.clicked.connect(self._toggleAll)
        headerLayout.addWidget(self.checkAllBtn)

        headerLayout.addStretch()

        self.countLabel = CaptionLabel("已选: 0", self)
        self.countLabel.setStyleSheet("color: #999; font-size: 11px;")
        headerLayout.addWidget(self.countLabel)

        mainLayout.addLayout(headerLayout)

        # 创建流式布局（不传入 self）
        flowLayout = FlowLayout(needAni=False)
        # flowLayout.setAnimation(200, Qt.OutQuad)
        flowLayout.setContentsMargins(4, 4, 4, 4)
        flowLayout.setVerticalSpacing(6)
        flowLayout.setHorizontalSpacing(10)

        for item in self.items:
            cb = CheckBox(item, self)
            cb.setStyleSheet("font-size: 12px; padding: 4px 8px;")
            cb.stateChanged.connect(lambda s, name=item: self._onChecked(s, name))
            self.checkboxes[item] = cb
            flowLayout.addWidget(cb)

        mainLayout.addLayout(flowLayout)

    def _onChecked(self, state, name):
        self._updateCount()
        self.selectionChanged.emit(self.selectedTexts())

    def _toggleAll(self):
        """切换全选/取消"""
        allChecked = all(cb.isChecked() for cb in self.checkboxes.values())
        for cb in self.checkboxes.values():
            cb.setChecked(not allChecked)

    def _updateCount(self):
        count = sum(1 for cb in self.checkboxes.values() if cb.isChecked())
        self.countLabel.setText(f"已选: {count}")

    def selectedTexts(self) -> list:
        """获取选中的文本列表"""
        return [name for name, cb in self.checkboxes.items() if cb.isChecked()]

    def clearSelection(self):
        """清空选择"""
        for cb in self.checkboxes.values():
            cb.setChecked(False)


class FileLoaderThread(QThread):
    """文件加载线程"""

    progress = Signal(int, int, str, float)
    fileLoaded = Signal(str, object, int)
    error = Signal(str)
    finished = Signal()

    def __init__(self, filePaths, chunkSize: int = 20000):
        super().__init__()
        self.filePaths = filePaths
        self.chunkSize = chunkSize
        self._isCanceled = False

    def cancel(self):
        self._isCanceled = True

    def _loadFile(self, filePath: str) -> tuple:
        """加载文件"""
        import openpyxl

        try:
            df = pd.read_excel(
                filePath,
                engine="openpyxl",
                header=0,
                dtype=str,
                na_filter=False,
            )
            return df, len(df)
        except Exception as e:
            logger.warning(f"[Bias] 降级到 openpyxl: {e}")
            wb = openpyxl.load_workbook(filePath, read_only=True, data_only=True)
            sheet = wb.worksheets[0]
            rows = list(sheet.values)
            wb.close()

            if not rows:
                return pd.DataFrame(), 0

            columns = rows[0]
            dataRows = rows[1:]
            df = pd.DataFrame(dataRows, columns=columns)
            return df, len(df)

    def run(self):
        totalFiles = len(self.filePaths)
        logger.info(f"[Bias] 开始加载 {totalFiles} 个文件")

        for i, filePath in enumerate(self.filePaths):
            if self._isCanceled:
                break

            fileSizeMb = os.path.getsize(filePath) / (1024 * 1024)
            self.progress.emit(i + 1, totalFiles, filePath, fileSizeMb)

            try:
                df, totalRows = self._loadFile(filePath)
                self.fileLoaded.emit(filePath, df, totalRows)
            except Exception as e:
                logger.error(f"[Bias] 文件读取失败: {filePath}, 错误: {e}")
                self.error.emit(f"读取失败：{os.path.basename(filePath)}\n{str(e)}")

        self.finished.emit()


class CountResultDialog(MessageBoxBase):
    """偏误计数结果弹窗"""

    def __init__(self, typeCounts: dict, parent=None):
        super().__init__(parent)
        self.typeCounts = typeCounts
        self.total = sum(typeCounts.values())

        # 标题栏
        iconLabel = QSvgWidget(":app/icons/Number.svg", self)
        iconLabel.setFixedSize(20, 20)

        titleLabel = SubtitleLabel("计数结果", self)
        titleLabel.setAlignment(Qt.AlignmentFlag.AlignLeft)

        closeBtn = TransparentToggleToolButton(FluentIcon.CLOSE, self)
        closeBtn.clicked.connect(self.accept)

        # 表格
        self.table = RoundTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["偏误类型", "计数", "占比"])
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(RoundTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(RoundTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)

        # 填充数据
        rows = []
        for name, count in typeCounts.items():
            pct = (count / self.total * 100) if self.total > 0 else 0
            rows.append((name, count, f"{pct:.1f}%"))

        rows.sort(key=lambda x: x[1], reverse=True)
        self.table.setRowCount(len(rows))

        for i, (name, count, pct) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(str(name)))
            self.table.setItem(i, 1, QTableWidgetItem(str(count)))
            self.table.setItem(i, 2, QTableWidgetItem(str(pct)))

        # 列宽设置
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Fixed
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed
        )
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 100)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setAlternatingRowColors(True)

        # 汇总
        summaryLabel = BodyLabel(f"总计：{self.total} 条偏误", self)
        summaryLabel.setAlignment(Qt.AlignmentFlag.AlignRight)
        summaryLabel.setStyleSheet("color: #666; font-size: 13px; padding: 4px 8px;")

        # 布局
        self.viewLayout.setContentsMargins(15, 15, 15, 10)

        headerLayout = QHBoxLayout()
        headerLayout.addWidget(iconLabel, 0, Qt.AlignmentFlag.AlignLeft)
        headerLayout.addWidget(titleLabel, 0, Qt.AlignmentFlag.AlignLeft)
        headerLayout.addStretch(1)
        headerLayout.addWidget(closeBtn, 0, Qt.AlignmentFlag.AlignRight)
        self.viewLayout.addLayout(headerLayout)

        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(self.table)
        self.viewLayout.addSpacing(4)
        self.viewLayout.addWidget(summaryLabel)

        self.buttonGroup.hide()
        self.widget.setFixedWidth(440)


class ChartDialog(MessageBoxBase):
    """偏误图表弹窗"""

    COLORS = [
        "#4477AA",
        "#EE6677",
        "#228833",
        "#CCBB44",
        "#66CCEE",
        "#AA3377",
        "#BBBBBB",
        "#332288",
        "#FF5555",
        "#50C8FF",
    ]

    def __init__(self, typeCounts: dict, parent=None):
        super().__init__(parent)
        self.typeCounts = typeCounts
        self.total = sum(typeCounts.values())

        plt.rcParams["font.sans-serif"] = [
            "Microsoft YaHei",
            "SimHei",
            "Arial Unicode MS",
        ]
        plt.rcParams["axes.unicode_minus"] = False

        # 标题栏
        iconLabel = QSvgWidget(":app/icons/Chart.svg", self)
        iconLabel.setFixedSize(20, 20)

        titleLabel = SubtitleLabel("图表", self)
        titleLabel.setAlignment(Qt.AlignmentFlag.AlignLeft)

        closeBtn = TransparentToggleToolButton(FluentIcon.CLOSE, self)
        closeBtn.clicked.connect(self.accept)

        # 图表类型切换
        typeSegment = SegmentedWidget(self)
        typeSegment.addItem("pie", "饼状图")
        typeSegment.addItem("bar", "条形图")
        typeSegment.setCurrentItem("pie")
        typeSegment.currentItemChanged.connect(self._onTypeChanged)

        # 图表画布
        self.canvas = FigureCanvas(Figure(figsize=(6, 5), dpi=100))
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._currentFigure = None

        scrollArea = ScrollArea(self)
        scrollArea.setWidget(self.canvas)
        scrollArea.setWidgetResizable(True)
        scrollArea.setStyleSheet("border: none; background: transparent;")

        # 导出按钮
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

        headerLayout = QHBoxLayout()
        headerLayout.addWidget(iconLabel, 0, Qt.AlignmentFlag.AlignLeft)
        headerLayout.addWidget(titleLabel, 0, Qt.AlignmentFlag.AlignLeft)
        headerLayout.addStretch(1)
        headerLayout.addWidget(closeBtn, 0, Qt.AlignmentFlag.AlignRight)
        self.viewLayout.addLayout(headerLayout)

        self.viewLayout.addSpacing(6)
        self.viewLayout.addWidget(typeSegment)
        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(scrollArea, 1)
        self.viewLayout.addSpacing(8)
        self.viewLayout.addLayout(btnLayout)

        # 按钮
        copyBtn = PrimaryPushButton("复制图片", self)
        copyBtn.setFixedWidth(100)
        copyBtn.clicked.connect(self._copyImage)

        cancelBtn = PushButton("关闭", self)
        cancelBtn.setFixedWidth(80)
        cancelBtn.clicked.connect(self.reject)

        self.buttonLayout.addWidget(cancelBtn)
        self.buttonLayout.addWidget(copyBtn)
        self.buttonGroup.hide()

        self.widget.setFixedWidth(560)
        self._drawPie()

    def _onTypeChanged(self, key: str):
        if key == "pie":
            self._drawPie()
        else:
            self._drawBar()

    def _drawPie(self):
        if self._currentFigure:
            plt.close(self._currentFigure)

        fig = Figure(figsize=(6, 5), dpi=100)
        ax = fig.add_subplot(111)
        self._currentFigure = fig

        data = [(k, v) for k, v in self.typeCounts.items() if v > 0]
        data.sort(key=lambda x: x[1], reverse=True)

        if not data:
            ax.text(0.5, 0.5, "无有效数据", ha="center", va="center", fontsize=14)
            ax.axis("off")
        else:
            labels = [d[0] for d in data]
            sizes = [d[1] for d in data]
            colorList = [self.COLORS[i % len(self.COLORS)] for i in range(len(data))]

            wedges, texts, autotexts = ax.pie(
                sizes,
                labels=None,
                colors=colorList,
                autopct=lambda p: f"{p:.1f}%" if p > 3 else "",
                startangle=90,
                wedgeprops={"edgecolor": "white", "linewidth": 1.5},
                pctdistance=0.75,
            )

            for at in autotexts:
                at.set_fontsize(10)
                at.set_color("white")
                at.set_weight("bold")

            ax.legend(
                wedges,
                labels,
                loc="center left",
                bbox_to_anchor=(1, 0, 0.5, 1),
                fontsize=9,
            )

        ax.set_title(f"偏误类型占比分布（总计 {self.total} 条）", fontsize=12, pad=12)
        fig.tight_layout()
        self.canvas.figure = fig
        self.canvas.draw()

    def _drawBar(self):
        if self._currentFigure:
            plt.close(self._currentFigure)

        fig = Figure(figsize=(6, 5), dpi=100)
        ax = fig.add_subplot(111)
        self._currentFigure = fig

        data = [(k, v) for k, v in self.typeCounts.items() if v > 0]
        data.sort(key=lambda x: x[1], reverse=True)

        if not data:
            ax.text(0.5, 0.5, "无有效数据", ha="center", va="center", fontsize=14)
            ax.axis("off")
        else:
            names = [d[0] for d in data]
            counts = [d[1] for d in data]
            colorList = [self.COLORS[i % len(self.COLORS)] for i in range(len(data))]

            bars = ax.barh(
                names, counts, color=colorList, edgecolor="white", linewidth=0.8
            )

            for bar, count in zip(bars, counts):
                pct = count / self.total * 100
                ax.text(
                    bar.get_width() + max(counts) * 0.01,
                    bar.get_y() + bar.get_height() / 2,
                    f"{count} ({pct:.1f}%)",
                    va="center",
                    ha="left",
                    fontsize=9,
                )

            ax.set_xlabel("计数", fontsize=11)
            ax.set_xlim(0, max(counts) * 1.35)
            ax.invert_yaxis()
            ax.grid(axis="x", linestyle="--", alpha=0.4, color="#ccc")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)

        ax.set_title(f"偏误类型数量统计（总计 {self.total} 条）", fontsize=12, pad=12)
        fig.tight_layout()
        self.canvas.figure = fig
        self.canvas.draw()

    def _export(self, fmt: str):
        defaultName = f"偏误统计.{fmt}"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出图表",
            defaultName,
            f"{fmt.upper()} Files (*.{fmt})" if fmt == "svg" else f"PNG Files (*.png)",
        )
        if not path:
            return

        if not path.endswith(f".{fmt}"):
            path += f".{fmt}"

        self._currentFigure.savefig(
            path, dpi=300, bbox_inches="tight", facecolor="white"
        )
        logger.info(f"[Bias] 图表已导出: {path}")
        InfoBar.success(
            "导出成功",
            f"图表已保存至：{path}",
            Qt.Orientation.Horizontal,
            True,
            2500,
            InfoBarPosition.TOP_RIGHT,
            self,
        )

    def _copyImage(self):
        buf = io.BytesIO()
        self._currentFigure.savefig(
            buf, format="png", dpi=150, bbox_inches="tight", facecolor="white"
        )
        buf.seek(0)

        from PySide6.QtGui import QPixmap

        pixmap = QPixmap()
        pixmap.loadFromData(buf.getvalue())
        self.window().clipboard().setPixmap(pixmap)
        buf.close()

        logger.info("[Bias] 图表已复制到剪贴板")
        InfoBar.success(
            "复制成功",
            "图表已复制到剪贴板",
            Qt.Orientation.Horizontal,
            True,
            2000,
            InfoBarPosition.TOP_RIGHT,
            self,
        )
        self.accept()


class BiasInterface(QWidget):
    """HSK 偏误分析主界面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("BiasInterface")
        logger.info("[Bias] 初始化 BiasInterface")

        self.filesList = []
        self.dfs = {}
        self.loadThread = None
        self.selectedColumn = None

        # 偏误统计
        self.currentRecords = []
        self.typeCounts = {
            **{name: 0 for name in CHARACTERS_TYPES},
            **{name: 0 for name in SENTENCES_TYPES},
            **{name: 0 for name in WORDS_TYPES},
        }
        self.totalCounts = None

        self._initUi()

    def _initUi(self):
        # 外层滚动区域
        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setStyleSheet("border: none; background: transparent;")
        self.scrollArea.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # 滚动区域内的主容器
        scrollContent = QWidget()
        scrollLayout = QVBoxLayout(scrollContent)
        scrollLayout.setContentsMargins(16, 16, 16, 16)
        scrollLayout.setSpacing(12)

        # 顶部：文件选择和模式切换
        topLayout = QHBoxLayout()

        self.chooseFileBtn = PrimaryPushButton("选择文件", self)
        self.chooseFileBtn.setIcon(FluentIcon.FOLDER)
        self.chooseFileBtn.clicked.connect(self._onChooseFile)

        self.switchBtn = SwitchButton("单文件模式", self)
        self.switchBtn.setToolTip(
            "单文件模式: 仅统计单个文件\n多文件模式: 统计所有文件中的偏误"
        )
        self.switchBtn.installEventFilter(
            ToolTipFilter(self.switchBtn, 200, ToolTipPosition.TOP)
        )
        self.switchBtn.checkedChanged.connect(self._onModeChanged)

        topLayout.addWidget(self.chooseFileBtn)
        topLayout.addWidget(self.switchBtn)
        topLayout.addStretch()

        # 统计列选择
        columnLayout = QHBoxLayout()

        columnLabel = BodyLabel("统计列:", self)
        columnLabel.setStyleSheet("font-size: 13px;")

        self.columnCombobox = ComboBox(self)
        self.columnCombobox.setEnabled(False)
        self.columnCombobox.currentIndexChanged.connect(self._onColumnChanged)

        columnLayout.addWidget(columnLabel)
        columnLayout.addWidget(self.columnCombobox)
        columnLayout.addStretch()

        scrollLayout.addLayout(topLayout)
        scrollLayout.addLayout(columnLayout)

        # 偏误类型筛选卡片
        filterCard = CardWidget(self)
        filterLayout = QVBoxLayout(filterCard)
        filterLayout.setContentsMargins(16, 12, 16, 12)
        filterLayout.setSpacing(8)

        filterTitle = BodyLabel("偏误类型筛选", self)
        filterTitle.setStyleSheet("font-size: 13px; font-weight: 600;")
        filterLayout.addWidget(filterTitle)

        # 字符偏误行
        charRow = QHBoxLayout()
        charRow.setSpacing(12)

        charLabel = BodyLabel("字符:", self)
        charLabel.setStyleSheet("color: #666; font-size: 12px; min-width: 40px;")
        charRow.addWidget(charLabel)

        self.charLineEdit = LineEdit(self)
        self.charLineEdit.setFixedWidth(70)
        self.charLineEdit.setPlaceholderText("筛选字")

        self.charFilter = MultiSelectFilter(list(CHARACTERS_TYPES.keys()), self)
        self.charFilter.selectionChanged.connect(self._onFilterChanged)

        charRow.addWidget(self.charLineEdit)
        charRow.addWidget(self.charFilter)
        filterLayout.addLayout(charRow)

        # 词语偏误行
        wordRow = QHBoxLayout()
        wordRow.setSpacing(12)

        wordLabel = BodyLabel("词语:", self)
        wordLabel.setStyleSheet("color: #666; font-size: 12px; min-width: 40px;")
        wordRow.addWidget(wordLabel)

        self.wordLineEdit = LineEdit(self)
        self.wordLineEdit.setFixedWidth(70)
        self.wordLineEdit.setPlaceholderText("筛选词")

        self.wordFilter = MultiSelectFilter(list(WORDS_TYPES.keys()), self)
        self.wordFilter.selectionChanged.connect(self._onFilterChanged)

        wordRow.addWidget(self.wordLineEdit)
        wordRow.addWidget(self.wordFilter)
        filterLayout.addLayout(wordRow)

        # 句子偏误行
        sentRow = QHBoxLayout()
        sentRow.setSpacing(12)

        sentLabel = BodyLabel("句子:", self)
        sentLabel.setStyleSheet("color: #666; font-size: 12px; min-width: 40px;")
        sentRow.addWidget(sentLabel)

        self.sentFilter = MultiSelectFilter(list(SENTENCES_TYPES.keys()), self)
        self.sentFilter.selectionChanged.connect(self._onFilterChanged)

        sentRow.addWidget(self.sentFilter)
        filterLayout.addLayout(sentRow)

        scrollLayout.addWidget(filterCard)

        # 操作栏（横向布局）
        actionLayout = QHBoxLayout()
        actionLayout.setSpacing(12)

        self.analyzeBtn = TransparentPushButton("分析", self)
        self.analyzeBtn.setIcon(":app/icons/Check.svg")
        self.analyzeBtn.clicked.connect(self._runMatching)
        actionLayout.addWidget(self.analyzeBtn)

        self.chartBtn = TransparentPushButton("图表", self)
        self.chartBtn.setIcon(":app/icons/Chart.svg")
        self.chartBtn.clicked.connect(self._runChart)
        actionLayout.addWidget(self.chartBtn)

        self.countBtn = TransparentPushButton("计数", self)
        self.countBtn.setIcon(":app/icons/Number.svg")
        self.countBtn.clicked.connect(self._runCount)
        actionLayout.addWidget(self.countBtn)

        actionLayout.addWidget(VerticalSeparator(self))

        self.exportBtn = TransparentPushButton("导出", self)
        self.exportBtn.setIcon(":app/icons/Save.svg")
        self.exportBtn.clicked.connect(self._exportResults)
        actionLayout.addWidget(self.exportBtn)
        actionLayout.addStretch()

        scrollLayout.addLayout(actionLayout)

        # 结果表格
        self.tableWidget = RoundTableWidget(self)
        self.tableWidget.setMinimumHeight(400)
        self.tableWidget.setColumnCount(5)
        self.tableWidget.setHorizontalHeaderLabels(
            ["文件", "行号", "句子", "偏误类型", "标记内容"]
        )
        self.tableWidget.setSortingEnabled(True)
        self.tableWidget.setSelectionBehavior(
            RoundTableWidget.SelectionBehavior.SelectRows
        )
        self.tableWidget.setAlternatingRowColors(True)
        self.tableWidget.setEditTriggers(RoundTableWidget.EditTrigger.NoEditTriggers)

        # 列宽设置
        self.tableWidget.setColumnWidth(0, 100)
        self.tableWidget.setColumnWidth(1, 60)
        self.tableWidget.setColumnWidth(3, 120)
        self.tableWidget.setColumnWidth(4, 150)
        self.tableWidget.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )

        scrollLayout.addWidget(self.tableWidget, 1)  # stretch=1 让表格占据剩余空间

        # 设置滚动区域
        self.scrollArea.setWidget(scrollContent)

        # 主布局
        mainLayout = QVBoxLayout(self)
        mainLayout.setContentsMargins(0, 0, 0, 0)
        mainLayout.addWidget(self.scrollArea)

        # 输入框互斥逻辑
        self.charLineEdit.textChanged.connect(lambda t: self._onInputMutual(t, "char"))
        self.wordLineEdit.textChanged.connect(lambda t: self._onInputMutual(t, "word"))

    def _onChooseFile(self):
        """选择文件"""
        if not self.switchBtn.isChecked():
            # 单文件模式
            self._clearAllData()
            file, _ = QFileDialog.getOpenFileName(
                self, "选择 Excel 文件", "", "Excel Files (*.xlsx *.xls)"
            )
            files = [file] if file else []
        else:
            # 多文件模式
            files, _ = QFileDialog.getOpenFileNames(
                self, "选择多个 Excel 文件", "", "Excel Files (*.xlsx *.xls)"
            )
            newFiles = [f for f in files if f not in self.filesList]
            if not newFiles:
                if files:
                    InfoBar.warning(
                        "提示",
                        "所有文件都已加载",
                        Qt.Orientation.Horizontal,
                        True,
                        2000,
                        InfoBarPosition.TOP_RIGHT,
                        self,
                    )
                return
            files = newFiles

        if not files:
            return

        self._startLoading(files)

    def _startLoading(self, filePaths):
        """开始加载文件"""
        if self.loadThread and self.loadThread.isRunning():
            return

        self.chooseFileBtn.setEnabled(False)

        self.loadThread = FileLoaderThread(filePaths)
        self.loadThread.progress.connect(self._onProgress)
        self.loadThread.fileLoaded.connect(self._onFileLoaded)
        self.loadThread.error.connect(self._onError)
        self.loadThread.finished.connect(self._onFinished)
        self.loadThread.start()

    def _onProgress(self, index: int, total: int, filePath: str, sizeMb: float):
        fileName = os.path.basename(filePath)
        logger.debug(f"[Bias] 加载进度: {index}/{total} - {fileName} ({sizeMb:.1f} MB)")

    def _onFileLoaded(self, filePath: str, df: pd.DataFrame, totalRows: int):
        self.filesList.append(filePath)
        self.dfs[filePath] = df
        self._updateColumns()

    def _onError(self, errMsg: str):
        InfoBar.error(
            "错误",
            errMsg,
            Qt.Orientation.Horizontal,
            True,
            3000,
            InfoBarPosition.TOP_RIGHT,
            self,
        )

    def _onFinished(self):
        self.loadThread = None
        self.chooseFileBtn.setEnabled(True)
        InfoBar.success(
            "加载成功",
            f"已加载 {len(self.filesList)} 个文件",
            Qt.Orientation.Horizontal,
            True,
            2000,
            InfoBarPosition.TOP_RIGHT,
            self,
        )

    def _updateColumns(self):
        """更新可选列"""
        if not self.dfs:
            self.columnCombobox.clear()
            self.columnCombobox.setEnabled(False)
            return

        if self.switchBtn.isChecked():
            columnSets = [set(df.columns) for df in self.dfs.values()]
            commonColumns = set.intersection(*columnSets) if columnSets else set()
            commonColumns = sorted(commonColumns)

            if not commonColumns:
                self.columnCombobox.clear()
                self.columnCombobox.setEnabled(False)
                return

            self.columnCombobox.clear()
            self.columnCombobox.addItems(commonColumns)
            self.columnCombobox.setEnabled(True)
        else:
            if self.filesList:
                lastFile = self.filesList[-1]
                if lastFile in self.dfs:
                    columns = list(self.dfs[lastFile].columns)
                    self.columnCombobox.clear()
                    self.columnCombobox.addItems(columns)
                    self.columnCombobox.setEnabled(True)

    def _onModeChanged(self, isChecked: bool):
        """切换模式"""
        text = "多文件模式" if isChecked else "单文件模式"
        self.switchBtn.setText(text)
        self._clearAllData()

    def _onColumnChanged(self):
        self.selectedColumn = self.columnCombobox.currentText()

    def _onFilterChanged(self):
        """筛选条件改变"""
        charSelected = self.charFilter.selectedTexts()
        wordSelected = self.wordFilter.selectedTexts()

        # 控制输入框可用性
        self.charLineEdit.setEnabled(True)
        self.wordLineEdit.setEnabled(True)

        if "无法识别 [#]" in charSelected:
            self.charLineEdit.setEnabled(False)
        if "离合词错误 [CLH]" in wordSelected or "存疑词 [CY]" in wordSelected:
            self.wordLineEdit.setEnabled(False)

    def _onInputMutual(self, text: str, inputType: str):
        """输入框互斥逻辑"""
        if text:
            if inputType == "char":
                self.wordLineEdit.clear()
            else:
                self.charLineEdit.clear()

    def _clearAllData(self):
        """清空所有数据"""
        self.filesList = []
        self.dfs = {}
        self.selectedColumn = None
        self.currentRecords = []
        self.typeCounts = {
            **{name: 0 for name in CHARACTERS_TYPES},
            **{name: 0 for name in SENTENCES_TYPES},
            **{name: 0 for name in WORDS_TYPES},
        }
        self.totalCounts = None
        self.tableWidget.setRowCount(0)
        self.columnCombobox.clear()
        self.columnCombobox.setEnabled(False)

    def _runMatching(self):
        """执行匹配分析"""
        if not self.dfs:
            InfoBar.warning(
                "提示",
                "请先加载文件",
                Qt.Orientation.Horizontal,
                True,
                3000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        if not self.selectedColumn:
            InfoBar.warning(
                "提示",
                "请先选择统计列",
                Qt.Orientation.Horizontal,
                True,
                3000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        selectTypes = (
            self.charFilter.selectedTexts()
            + self.wordFilter.selectedTexts()
            + self.sentFilter.selectedTexts()
        )

        if not selectTypes:
            InfoBar.warning(
                "提示",
                "请至少选择一种偏误类型",
                Qt.Orientation.Horizontal,
                True,
                3000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        charInput = self.charLineEdit.text().strip()
        wordInput = self.wordLineEdit.text().strip()

        # 重置计数
        for name in self.typeCounts:
            self.typeCounts[name] = 0
        self.currentRecords = []
        self.tableWidget.setRowCount(0)

        for filePath, df in self.dfs.items():
            if self.selectedColumn not in df.columns:
                continue

            textSeries = df[self.selectedColumn].astype(str).fillna("")
            fileName = os.path.basename(filePath)

            for idx, text in enumerate(textSeries):
                if not text or text == "nan":
                    continue

                for errorName in selectTypes:
                    patternStr, hasContent = ERROR_TYPES[errorName]
                    pattern = re.compile(patternStr)

                    for match in re.finditer(pattern, text):
                        if hasContent:
                            content = match.group(1) if match.lastindex else ""
                            if not content:
                                content = match.group()
                        else:
                            content = match.group()

                        # 过滤
                        matchFilter = True
                        if charInput and charInput not in content:
                            matchFilter = False
                        elif wordInput and wordInput not in content:
                            matchFilter = False

                        if matchFilter:
                            self.typeCounts[errorName] += 1
                            self.currentRecords.append(
                                (fileName, idx + 2, text, errorName, content)
                            )

        # 填充表格
        self.tableWidget.setRowCount(len(self.currentRecords))
        for i, (fname, rowNum, sentence, errName, mark) in enumerate(
            self.currentRecords
        ):
            self.tableWidget.setItem(i, 0, QTableWidgetItem(fname))
            self.tableWidget.setItem(i, 1, QTableWidgetItem(str(rowNum)))
            self.tableWidget.setItem(i, 2, QTableWidgetItem(sentence))
            self.tableWidget.setItem(i, 3, QTableWidgetItem(errName))
            self.tableWidget.setItem(i, 4, QTableWidgetItem(mark))

        self.totalCounts = {name: self.typeCounts[name] for name in selectTypes}

        if not self.currentRecords:
            InfoBar.warning(
                "提示",
                "未匹配到任何偏误",
                Qt.Orientation.Horizontal,
                True,
                2000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )

    def _runCount(self):
        """显示计数结果"""
        if not self.totalCounts:
            InfoBar.warning(
                "提示",
                "请先进行分析",
                Qt.Orientation.Horizontal,
                True,
                2000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        dialog = CountResultDialog(self.totalCounts, self.window())
        dialog.exec()

    def _runChart(self):
        """显示图表"""
        if not self.totalCounts:
            InfoBar.warning(
                "提示",
                "请先进行分析",
                Qt.Orientation.Horizontal,
                True,
                2000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        dialog = ChartDialog(self.totalCounts, self.window())
        dialog.exec()

    def _exportResults(self):
        """导出结果"""
        if not self.currentRecords:
            InfoBar.warning(
                "提示",
                "没有可导出的数据",
                Qt.Orientation.Horizontal,
                True,
                2000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        filePath, _ = QFileDialog.getSaveFileName(
            self, "保存结果", "", "Excel Files (*.xlsx)"
        )
        if not filePath:
            return

        if not filePath.endswith(".xlsx"):
            filePath += ".xlsx"

        dfExport = pd.DataFrame(
            self.currentRecords,
            columns=["文件", "行号", "句子", "偏误类型", "标记内容"],
        )

        try:
            dfExport.to_excel(filePath, index=False, engine="openpyxl")
            InfoBar.success(
                "导出成功",
                f"结果已保存至：{filePath}",
                Qt.Orientation.Horizontal,
                True,
                3000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
        except Exception as e:
            InfoBar.error(
                "导出失败",
                str(e),
                Qt.Orientation.Horizontal,
                True,
                3000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
