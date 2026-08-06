# coding: utf-8
"""
HSK 偏误分析界面
"""

import os
import io

import numpy as np
import pandas as pd
from app.core.utils import logger
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QColor, QPalette
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QDoubleSpinBox,
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
    DoubleSpinBox,
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

# matplotlib 必须使用 QtAgg(在 FigureCanvasQTAgg 导入前)
# 用 force=True 确保即使 MPLBACKEND=Agg 也能切换
import matplotlib

matplotlib.use("QtAgg", force=True)
from matplotlib import pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

import pyperclip
import re
from typing import Dict, List, Tuple

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


class AprioriWorkerThread(QThread):
    """Apriori 关联规则挖掘后台线程（FR-ERR-003）"""

    progress = Signal(int, str)  # (percent 0-100, status)
    finished = Signal(object)  # (rulesDf) DataFrame
    failed = Signal(str)  # error msg

    def __init__(
        self,
        transactions: list,
        minSupport: float = 0.1,
        minConfidence: float = 0.5,
    ):
        super().__init__()
        self.transactions = transactions
        self.minSupport = minSupport
        self.minConfidence = minConfidence
        self._isCanceled = False

    def cancel(self):
        self._isCanceled = True

    def run(self):
        try:
            from mlxtend.preprocessing import TransactionEncoder
            from mlxtend.frequent_patterns import apriori, association_rules
            import pandas as pd

            if not self.transactions:
                self.failed.emit("无有效事务数据")
                return

            # 进度提示
            self.progress.emit(10, "正在编码事务...")

            # 编码
            te = TransactionEncoder()
            teArray = te.fit(self.transactions).transform(self.transactions)
            df = pd.DataFrame(teArray, columns=te.columns_)

            # 进度提示
            self.progress.emit(30, "正在挖掘频繁项集...")

            if self._isCanceled:
                return

            # 挖掘频繁项集
            frequentItemsets = apriori(
                df,
                min_support=self.minSupport,
                use_colnames=True,
                max_len=3,
            )

            if self._isCanceled:
                return

            if frequentItemsets.empty:
                self.progress.emit(100, "未找到满足最小支持度的频繁项集")
                self.finished.emit(pd.DataFrame())
                return

            # 进度提示
            self.progress.emit(70, "正在生成关联规则...")

            # 生成规则
            rules = association_rules(
                frequentItemsets,
                metric="confidence",
                min_threshold=self.minConfidence,
                num_itemsets=len(frequentItemsets),
            )

            if rules.empty:
                self.progress.emit(100, "未找到满足置信度的关联规则")
                self.finished.emit(pd.DataFrame())
                return

            # 排序（按置信度降序）
            rules = rules.sort_values(
                ["confidence", "lift"], ascending=[False, False]
            ).reset_index(drop=True)

            self.progress.emit(100, f"挖掘完成，共 {len(rules)} 条规则")
            self.finished.emit(rules)

        except Exception as e:
            logger.error(f"[Bias] Apriori 计算失败: {e}")
            self.failed.emit(str(e))


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


class ColumnConfigDialog(MessageBoxBase):
    """等级 / 国籍列配置弹窗（用于偏误分析中的热力图分组）"""

    def __init__(
        self,
        allColumns: list,
        currentLevel: str = None,
        currentCountry: str = None,
        parent=None,
    ):
        super().__init__(parent)
        self.allColumns = list(allColumns)
        self.resultLevel = currentLevel
        self.resultCountry = currentCountry

        # 标题栏
        iconLabel = QSvgWidget(":app/icons/Setting.svg", self)
        iconLabel.setFixedSize(20, 20)

        titleLabel = SubtitleLabel("列配置", self)
        titleLabel.setAlignment(Qt.AlignmentFlag.AlignLeft)

        closeBtn = TransparentToggleToolButton(FluentIcon.CLOSE, self)
        closeBtn.clicked.connect(self.reject)

        # 说明
        hintLabel = CaptionLabel(
            "为「等级」「国籍」分别指定 Excel 表头列。\n"
            "未设置时将根据列名自动识别（包含 level/hsk/等级 等关键词）。",
            self,
        )
        hintLabel.setStyleSheet("color: #666; font-size: 12px; padding: 4px 0;")
        hintLabel.setWordWrap(True)

        # 等级下拉
        levelLayout = QHBoxLayout()
        levelLabel = BodyLabel("等级列：", self)
        levelLabel.setStyleSheet("font-size: 13px; min-width: 70px;")
        self.levelCombo = ComboBox(self)
        self.levelCombo.addItem("（不设置）", userData=None)
        for col in self.allColumns:
            self.levelCombo.addItem(str(col), userData=str(col))
        if currentLevel and currentLevel in self.allColumns:
            self.levelCombo.setCurrentText(str(currentLevel))
        else:
            self.levelCombo.setCurrentIndex(0)
        levelLayout.addWidget(levelLabel)
        levelLayout.addWidget(self.levelCombo, 1)

        # 国籍下拉
        countryLayout = QHBoxLayout()
        countryLabel = BodyLabel("国籍列：", self)
        countryLabel.setStyleSheet("font-size: 13px; min-width: 70px;")
        self.countryCombo = ComboBox(self)
        self.countryCombo.addItem("（不设置）", userData=None)
        for col in self.allColumns:
            self.countryCombo.addItem(str(col), userData=str(col))
        if currentCountry and currentCountry in self.allColumns:
            self.countryCombo.setCurrentText(str(currentCountry))
        else:
            self.countryCombo.setCurrentIndex(0)
        countryLayout.addWidget(countryLabel)
        countryLayout.addWidget(self.countryCombo, 1)

        # 自动识别按钮
        self.autoDetectBtn = PushButton("根据列名自动识别", self)
        self.autoDetectBtn.setIcon(":app/icons/Refresh.svg")
        self.autoDetectBtn.clicked.connect(self._onAutoDetect)

        btnRow = QHBoxLayout()
        btnRow.addStretch(1)
        btnRow.addWidget(self.autoDetectBtn)

        # 布局
        self.viewLayout.setContentsMargins(20, 18, 20, 12)
        self.viewLayout.setSpacing(8)

        headerLayout = QHBoxLayout()
        headerLayout.addWidget(iconLabel, 0, Qt.AlignmentFlag.AlignLeft)
        headerLayout.addWidget(titleLabel, 0, Qt.AlignmentFlag.AlignLeft)
        headerLayout.addStretch(1)
        headerLayout.addWidget(closeBtn, 0, Qt.AlignmentFlag.AlignRight)
        self.viewLayout.addLayout(headerLayout)

        self.viewLayout.addSpacing(4)
        self.viewLayout.addWidget(hintLabel)
        self.viewLayout.addSpacing(6)
        self.viewLayout.addLayout(levelLayout)
        self.viewLayout.addLayout(countryLayout)
        self.viewLayout.addSpacing(4)
        self.viewLayout.addLayout(btnRow)

        # 底部按钮
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")

        self.yesButton.clicked.connect(self._onAccept)
        self.cancelButton.clicked.connect(self.reject)

        self.widget.setFixedWidth(460)

    def _onAutoDetect(self):
        """根据列名自动识别等级与国籍列"""
        levelKeywords = ["level", "hsk", "等级", "级别", "水准"]
        countryKeywords = ["country", "nationality", "国籍", "国家", "nation"]

        detectedLevel = None
        detectedCountry = None
        for col in self.allColumns:
            colLower = str(col).lower()
            if detectedLevel is None and any(kw in colLower for kw in levelKeywords):
                detectedLevel = col
            if detectedCountry is None and any(
                kw in colLower for kw in countryKeywords
            ):
                detectedCountry = col

        if detectedLevel:
            self.levelCombo.setCurrentText(str(detectedLevel))
        else:
            self.levelCombo.setCurrentIndex(0)
        if detectedCountry:
            self.countryCombo.setCurrentText(str(detectedCountry))
        else:
            self.countryCombo.setCurrentIndex(0)

    def _onAccept(self):
        self.resultLevel = self.levelCombo.currentData()
        self.resultCountry = self.countryCombo.currentData()
        self.accept()

    def getResult(self) -> tuple:
        """获取配置结果：(levelCol, countryCol)"""
        return self.resultLevel, self.resultCountry


class HeatmapDialog(MessageBoxBase):
    """偏误分布热力图弹窗（FR-ERR-002）"""

    COLORMAPS = ["YlOrRd", "viridis", "coolwarm", "Blues", "Greens"]

    def __init__(
        self,
        heatmapData: dict,
        selectedTypes: list,
        allGroups: list,
        levelGroups: list,
        countryGroups: list,
        parent=None,
    ):
        super().__init__(parent)
        self.heatmapData = heatmapData
        self.selectedTypes = selectedTypes
        self.allGroups = allGroups
        self.levelGroups = levelGroups
        self.countryGroups = countryGroups
        self._currentFigure = None
        self._currentMode = "level"
        self._currentColormap = "YlOrRd"
        self._drillDownCallback = None

        plt.rcParams["font.sans-serif"] = [
            "Microsoft YaHei",
            "SimHei",
            "Arial Unicode MS",
        ]
        plt.rcParams["axes.unicode_minus"] = False

        # 标题栏
        iconLabel = QSvgWidget(":app/icons/Chart.svg", self)
        iconLabel.setFixedSize(20, 20)

        titleLabel = SubtitleLabel("偏误分布热力图", self)
        titleLabel.setAlignment(Qt.AlignmentFlag.AlignLeft)

        closeBtn = TransparentToggleToolButton(FluentIcon.CLOSE, self)
        closeBtn.clicked.connect(self.accept)

        # 分组切换
        modeSegment = SegmentedWidget(self)
        if levelGroups:
            modeSegment.addItem("level", "按等级")
        if countryGroups:
            modeSegment.addItem("country", "按国籍")
        if levelGroups:
            modeSegment.setCurrentItem("level")
            self._currentMode = "level"
        elif countryGroups:
            modeSegment.setCurrentItem("country")
            self._currentMode = "country"
        else:
            modeSegment.addItem("level", "按等级")
            modeSegment.setCurrentItem("level")

        modeSegment.currentItemChanged.connect(self._onModeChanged)

        # 配色下拉
        colormapLayout = QHBoxLayout()
        colormapLabel = BodyLabel("配色:", self)
        colormapLabel.setStyleSheet("font-size: 12px; color: #666;")
        self.colormapCombo = ComboBox(self)
        for cm in self.COLORMAPS:
            self.colormapCombo.addItem(cm)
        self.colormapCombo.setCurrentText(self._currentColormap)
        self.colormapCombo.setFixedWidth(120)
        self.colormapCombo.currentTextChanged.connect(self._onColormapChanged)
        colormapLayout.addWidget(colormapLabel)
        colormapLayout.addWidget(self.colormapCombo)
        colormapLayout.addStretch()

        # 画布
        self.canvas = FigureCanvas(Figure(figsize=(7, 6), dpi=100))
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # 点击事件用于下钻
        self.canvas.mpl_connect("button_press_event", self._onCanvasClick)

        scrollArea = ScrollArea(self)
        scrollArea.setWidget(self.canvas)
        scrollArea.setWidgetResizable(True)
        scrollArea.setStyleSheet("border: none; background: transparent;")

        # 操作按钮
        btnLayout = QHBoxLayout()
        btnLayout.addStretch(1)

        pngBtn = PushButton("导出 PNG", self)
        pngBtn.clicked.connect(lambda: self._export("png"))
        svgBtn = PushButton("导出 SVG", self)
        svgBtn.clicked.connect(lambda: self._export("svg"))
        btnLayout.addWidget(pngBtn)
        btnLayout.addWidget(svgBtn)

        # 提示标签
        self.hintLabel = CaptionLabel(
            "提示：点击热力图单元格可在主表格中下钻查看", self
        )
        self.hintLabel.setStyleSheet("color: #888; font-size: 11px; padding: 2px 4px;")

        # 布局
        self.viewLayout.setContentsMargins(15, 15, 15, 10)

        headerLayout = QHBoxLayout()
        headerLayout.addWidget(iconLabel, 0, Qt.AlignmentFlag.AlignLeft)
        headerLayout.addWidget(titleLabel, 0, Qt.AlignmentFlag.AlignLeft)
        headerLayout.addStretch()
        headerLayout.addWidget(closeBtn, 0, Qt.AlignmentFlag.AlignRight)
        self.viewLayout.addLayout(headerLayout)

        self.viewLayout.addSpacing(6)
        self.viewLayout.addWidget(modeSegment)
        self.viewLayout.addLayout(colormapLayout)
        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(scrollArea, 1)
        self.viewLayout.addWidget(self.hintLabel)
        self.viewLayout.addSpacing(4)
        self.viewLayout.addLayout(btnLayout)

        # 底部按钮
        copyBtn = PrimaryPushButton("复制图片", self)
        copyBtn.setFixedWidth(100)
        copyBtn.clicked.connect(self._copyImage)

        cancelBtn = PushButton("关闭", self)
        cancelBtn.setFixedWidth(80)
        cancelBtn.clicked.connect(self.reject)

        self.buttonLayout.addWidget(cancelBtn)
        self.buttonLayout.addWidget(copyBtn)
        self.buttonGroup.hide()

        self.widget.setFixedWidth(640)
        self._drawHeatmap()

    def setDrillDownCallback(self, callback):
        """设置下钻回调：callback(errorName, groupValue)"""
        self._drillDownCallback = callback

    def _getCurrentGroups(self) -> list:
        if self._currentMode == "level":
            return self.levelGroups
        return self.countryGroups

    def _onModeChanged(self, key: str):
        if key and key != self._currentMode:
            self._currentMode = key
            self._drawHeatmap()

    def _onColormapChanged(self, name: str):
        if name and name != self._currentColormap:
            self._currentColormap = name
            self._drawHeatmap()

    def _buildMatrix(self) -> tuple:
        """构建 (matrix, xLabels, yLabels) 矩阵
        行：selectedTypes（偏误类型）
        列：当前模式下的分组值
        """
        groups = self._getCurrentGroups()
        if not groups or not self.selectedTypes:
            return None, [], []

        # 按出现频次排序：列与行按计数总和降序
        colSums = {g: 0 for g in groups}
        rowSums = {t: 0 for t in self.selectedTypes}

        for (errName, groupVal), records in self.heatmapData.items():
            if groupVal in colSums and errName in rowSums:
                colSums[groupVal] += len(records)
                rowSums[errName] += len(records)

        sortedCols = sorted(colSums.keys(), key=lambda g: colSums[g], reverse=True)
        sortedRows = sorted(rowSums.keys(), key=lambda t: rowSums[t], reverse=True)

        # 只保留有数据的行
        sortedRows = [t for t in sortedRows if rowSums[t] > 0]
        if not sortedRows:
            return None, sortedCols, []

        matrix = np.zeros((len(sortedRows), len(sortedCols)), dtype=int)

        for i, errName in enumerate(sortedRows):
            for j, groupVal in enumerate(sortedCols):
                key = (errName, groupVal)
                if key in self.heatmapData:
                    matrix[i, j] = len(self.heatmapData[key])

        return matrix, sortedCols, sortedRows

    def _drawHeatmap(self):
        if self._currentFigure:
            plt.close(self._currentFigure)

        fig = Figure(figsize=(7, 6), dpi=100)
        ax = fig.add_subplot(111)
        self._currentFigure = fig

        matrix, xLabels, yLabels = self._buildMatrix()
        if matrix is None or matrix.size == 0:
            ax.text(
                0.5,
                0.5,
                "无有效数据\n（请先分析且选择偏误类型）",
                ha="center",
                va="center",
                fontsize=13,
            )
            ax.axis("off")
        else:
            im = ax.imshow(
                matrix,
                aspect="auto",
                cmap=self._currentColormap,
                interpolation="nearest",
            )

            # 坐标轴
            ax.set_xticks(np.arange(len(xLabels)))
            ax.set_yticks(np.arange(len(yLabels)))
            ax.set_xticklabels(xLabels, rotation=45, ha="right", fontsize=10)
            ax.set_yticklabels(yLabels, fontsize=10)

            # 在每个格子中标注数值
            maxVal = matrix.max() if matrix.max() > 0 else 1
            for i in range(len(yLabels)):
                for j in range(len(xLabels)):
                    val = matrix[i, j]
                    color = "white" if val > maxVal * 0.5 else "black"
                    ax.text(
                        j,
                        i,
                        str(val),
                        ha="center",
                        va="center",
                        color=color,
                        fontsize=9,
                        fontweight="bold",
                    )

            # 颜色条
            cbar = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.03)
            cbar.set_label("偏误计数", fontsize=10)

            modeLabel = "HSK 等级" if self._currentMode == "level" else "国籍"
            ax.set_xlabel(modeLabel, fontsize=11)
            ax.set_ylabel("偏误类型", fontsize=11)

            title = f"偏误类型 × {modeLabel} 分布热力图"
            ax.set_title(title, fontsize=12, pad=12)

            fig.tight_layout()

        self.canvas.figure = fig
        self.canvas.draw()

    def _onCanvasClick(self, event):
        """点击热力图单元格触发下钻"""
        if not self._drillDownCallback or event.inaxes is None:
            return
        if event.xdata is None or event.ydata is None:
            return

        matrix, xLabels, yLabels = self._buildMatrix()
        if matrix is None:
            return

        j = int(round(event.xdata))
        i = int(round(event.ydata))
        if i < 0 or i >= len(yLabels) or j < 0 or j >= len(xLabels):
            return

        errorName = yLabels[i]
        groupVal = xLabels[j]

        logger.info(f"[Bias] 热力图下钻: 偏误={errorName}, 分组={groupVal}")
        self._drillDownCallback(errorName, groupVal)
        self.accept()

    def _export(self, fmt: str):
        if not self._currentFigure:
            return
        defaultName = f"偏误热力图.{fmt}"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出热力图",
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
        logger.info(f"[Bias] 热力图已导出: {path}")
        InfoBar.success(
            "导出成功",
            f"热力图已保存至：{path}",
            Qt.Orientation.Horizontal,
            True,
            2500,
            InfoBarPosition.TOP_RIGHT,
            self,
        )

    def _copyImage(self):
        if not self._currentFigure:
            return
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

        logger.info("[Bias] 热力图已复制到剪贴板")
        InfoBar.success(
            "复制成功",
            "热力图已复制到剪贴板",
            Qt.Orientation.Horizontal,
            True,
            2000,
            InfoBarPosition.TOP_RIGHT,
            self,
        )
        self.accept()


class AssociationRulesDialog(MessageBoxBase):
    """偏误关联规则挖掘结果弹窗（FR-ERR-003）

    使用 Apriori 算法挖掘偏误类型间的关联规则,
    支持表格展示、散点图、网络图三种可视化。
    """

    def __init__(
        self,
        transactions: list,
        minSupport: float = 0.1,
        minConfidence: float = 0.5,
        parent=None,
    ):
        super().__init__(parent)
        self.transactions = transactions
        self.minSupport = minSupport
        self.minConfidence = minConfidence
        self.rulesDf = None  # 当前规则 DataFrame
        self._scatterFigure = None
        self._networkFigure = None
        self._workerThread = None

        plt.rcParams["font.sans-serif"] = [
            "Microsoft YaHei",
            "SimHei",
            "Arial Unicode MS",
        ]
        plt.rcParams["axes.unicode_minus"] = False

        # 标题栏
        iconLabel = QSvgWidget(":app/icons/Chart.svg", self)
        iconLabel.setFixedSize(20, 20)

        titleLabel = SubtitleLabel("偏误关联规则挖掘", self)
        titleLabel.setAlignment(Qt.AlignmentFlag.AlignLeft)

        closeBtn = TransparentToggleToolButton(FluentIcon.CLOSE, self)
        closeBtn.clicked.connect(self._onClose)

        # 阈值参数面板
        paramWidget = QWidget()
        paramLayout = QHBoxLayout(paramWidget)
        paramLayout.setContentsMargins(0, 0, 0, 0)
        paramLayout.setSpacing(12)

        supportLabel = BodyLabel("最小支持度:", self)
        supportLabel.setStyleSheet("font-size: 12px;")
        self.supportSpin = DoubleSpinBox(self)
        self.supportSpin.setRange(0.01, 1.0)
        self.supportSpin.setSingleStep(0.05)
        self.supportSpin.setDecimals(2)
        self.supportSpin.setValue(minSupport)
        self.supportSpin.setFixedWidth(180)

        confidenceLabel = BodyLabel("最小置信度:", self)
        confidenceLabel.setStyleSheet("font-size: 12px;")
        self.confidenceSpin = DoubleSpinBox(self)
        self.confidenceSpin.setRange(0.05, 1.0)
        self.confidenceSpin.setSingleStep(0.05)
        self.confidenceSpin.setDecimals(2)
        self.confidenceSpin.setValue(minConfidence)
        self.confidenceSpin.setFixedWidth(180)

        self.recomputeBtn = PushButton("重新计算", self)
        self.recomputeBtn.setIcon(":app/icons/Refresh.svg")
        self.recomputeBtn.clicked.connect(self._recompute)

        paramLayout.addWidget(supportLabel)
        paramLayout.addWidget(self.supportSpin)
        paramLayout.addWidget(confidenceLabel)
        paramLayout.addWidget(self.confidenceSpin)
        paramLayout.addStretch(1)
        paramLayout.addWidget(self.recomputeBtn)

        # Tab 切换：表格 / 散点图 / 网络图
        self.viewSegment = SegmentedWidget(self)
        self.viewSegment.addItem("table", "表格")
        self.viewSegment.addItem("scatter", "散点图")
        self.viewSegment.addItem("network", "网络图")
        self.viewSegment.setCurrentItem("table")
        self.viewSegment.currentItemChanged.connect(self._onViewChanged)

        # --- 表格视图 ---
        self.table = RoundTableWidget(self)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["前项", "后项", "支持度", "置信度", "提升度", "杠杆值"]
        )
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(RoundTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(RoundTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        # 数值列需要更宽以显示小数与百分号
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        # 列宽：支持度/置信度需容纳 "100.0%"，提升度需 "1.250" 等小数
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 110)
        for col in (2, 3, 4, 5):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Fixed
            )

        self.tableWrap = ScrollArea(self)
        self.tableWrap.setWidget(self.table)
        self.tableWrap.setWidgetResizable(True)
        self.tableWrap.setStyleSheet("border: none; background: transparent;")

        # --- 散点图视图 ---
        self.scatterCanvas = FigureCanvas(Figure(figsize=(7, 5), dpi=100))
        self.scatterCanvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.scatterScroll = ScrollArea(self)
        self.scatterScroll.setWidget(self.scatterCanvas)
        self.scatterScroll.setWidgetResizable(True)
        self.scatterScroll.setStyleSheet("border: none; background: transparent;")
        self.scatterScroll.hide()

        # --- 网络图视图 ---
        self.networkCanvas = FigureCanvas(Figure(figsize=(7, 6), dpi=100))
        self.networkCanvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.networkScroll = ScrollArea(self)
        self.networkScroll.setWidget(self.networkCanvas)
        self.networkScroll.setWidgetResizable(True)
        self.networkScroll.setStyleSheet("border: none; background: transparent;")
        self.networkScroll.hide()

        # 状态/进度
        self.statusLabel = CaptionLabel("点击「重新计算」开始挖掘...", self)
        self.statusLabel.setStyleSheet("color: #666; font-size: 11px;")

        # 导出
        exportLayout = QHBoxLayout()
        exportLayout.addStretch(1)

        self.exportCsvBtn = PushButton("导出 CSV", self)
        self.exportCsvBtn.clicked.connect(self._exportCsv)
        self.exportCsvBtn.setEnabled(False)
        exportLayout.addWidget(self.exportCsvBtn)

        self.exportPngBtn = PushButton("导出图 PNG", self)
        self.exportPngBtn.clicked.connect(self._exportPng)
        self.exportPngBtn.setEnabled(False)
        exportLayout.addWidget(self.exportPngBtn)

        # 布局
        self.viewLayout.setContentsMargins(15, 15, 15, 10)

        headerLayout = QHBoxLayout()
        headerLayout.addWidget(iconLabel, 0, Qt.AlignmentFlag.AlignLeft)
        headerLayout.addWidget(titleLabel, 0, Qt.AlignmentFlag.AlignLeft)
        headerLayout.addStretch()
        headerLayout.addWidget(closeBtn, 0, Qt.AlignmentFlag.AlignRight)
        self.viewLayout.addLayout(headerLayout)

        self.viewLayout.addSpacing(6)
        self.viewLayout.addWidget(paramWidget)
        self.viewLayout.addSpacing(4)
        self.viewLayout.addWidget(self.viewSegment)
        self.viewLayout.addSpacing(6)
        self.viewLayout.addWidget(self.tableWrap, 1)
        self.viewLayout.addWidget(self.scatterScroll, 1)
        self.viewLayout.addWidget(self.networkScroll, 1)
        self.viewLayout.addSpacing(2)
        self.viewLayout.addWidget(self.statusLabel)
        self.viewLayout.addSpacing(4)
        self.viewLayout.addLayout(exportLayout)

        # 底部按钮
        cancelBtn = PushButton("关闭", self)
        cancelBtn.setFixedWidth(80)
        cancelBtn.clicked.connect(self._onClose)
        self.buttonLayout.addWidget(cancelBtn)
        self.buttonGroup.hide()

        self.widget.setFixedWidth(760)
        self.widget.setFixedHeight(560)

        # 初始计算
        self._recompute()

    def _onClose(self):
        if self._workerThread and self._workerThread.isRunning():
            self._workerThread.cancel()
            self._workerThread.wait(2000)
        self.accept()

    def _onViewChanged(self, key: str):
        self.tableWrap.setVisible(key == "table")
        self.scatterScroll.setVisible(key == "scatter")
        self.networkScroll.setVisible(key == "network")
        if key == "scatter":
            self._renderScatter()
        elif key == "network":
            self._renderNetwork()

    def _recompute(self):
        """启动后台 Apriori 线程"""
        if self._workerThread and self._workerThread.isRunning():
            return

        self.minSupport = float(self.supportSpin.value())
        self.minConfidence = float(self.confidenceSpin.value())
        self.recomputeBtn.setEnabled(False)
        self.exportCsvBtn.setEnabled(False)
        self.exportPngBtn.setEnabled(False)
        self.statusLabel.setText("正在挖掘...")

        self._workerThread = AprioriWorkerThread(
            self.transactions, self.minSupport, self.minConfidence
        )
        self._workerThread.progress.connect(self._onProgress)
        self._workerThread.finished.connect(self._onFinished)
        self._workerThread.failed.connect(self._onFailed)
        self._workerThread.start()

    def _onProgress(self, percent: int, status: str):
        self.statusLabel.setText(f"[{percent}%] {status}")

    def _onFailed(self, err: str):
        self.recomputeBtn.setEnabled(True)
        self.statusLabel.setText(f"计算失败: {err}")
        InfoBar.error(
            "挖掘失败",
            err,
            Qt.Orientation.Horizontal,
            True,
            3000,
            InfoBarPosition.TOP_RIGHT,
            self,
        )

    def _onFinished(self, rulesDf):
        self.recomputeBtn.setEnabled(True)
        self.rulesDf = rulesDf

        if rulesDf is None or rulesDf.empty:
            self.statusLabel.setText("未找到满足条件的关联规则，请降低阈值重试")
            self.table.setRowCount(0)
            return

        self._populateTable(rulesDf)
        self._renderScatter()
        self._renderNetwork()
        self.exportCsvBtn.setEnabled(True)
        self.exportPngBtn.setEnabled(True)

        # 计算项目数与统计摘要
        numTransactions = len(self.transactions)
        allItems = set()
        for t in self.transactions:
            allItems.update(t)
        numItems = len(allItems)

        # 统计指标范围
        confMin = rulesDf["confidence"].min()
        confMax = rulesDf["confidence"].max()
        liftMin = rulesDf["lift"].min()
        liftMax = rulesDf["lift"].max()

        # 样本量警告
        warning = ""
        if numTransactions < 10:
            warning = (
                f" ⚠ 样本量较少（事务={numTransactions}），统计结论仅供参考，"
                f"建议加载更多文件后再挖掘。"
            )
        elif numItems > numTransactions * 3:
            warning = (
                f" ⚠ 项目数({numItems})远多于事务数({numTransactions})，"
                f"统计可能不稳定。"
            )

        self.statusLabel.setText(
            f"完成：{len(rulesDf)} 条规则 | "
            f"事务数={numTransactions}  项目数={numItems}  "
            f"置信度范围=[{confMin * 100:.1f}%, {confMax * 100:.1f}%]  "
            f"提升度=[{liftMin:.2f}, {liftMax:.2f}]{warning}"
        )
        logger.info(
            f"[Bias] 关联规则挖掘完成: {len(rulesDf)} 条, "
            f"事务={numTransactions}, 项目={numItems}, "
            f"置信度范围=[{confMin:.3f}, {confMax:.3f}], "
            f"提升度范围=[{liftMin:.3f}, {liftMax:.3f}]"
        )
        InfoBar.success(
            "挖掘完成",
            f"共 {len(rulesDf)} 条关联规则（事务={numTransactions}，项目={numItems}）",
            Qt.Orientation.Horizontal,
            True,
            2000,
            InfoBarPosition.TOP_RIGHT,
            self,
        )

    def _populateTable(self, rulesDf):
        """填充表格"""
        self.table.setRowCount(len(rulesDf))

        def _formatSet(s):
            return ", ".join(sorted(list(s))) if s is not None else ""

        def _fmtPct(v):
            # 百分比格式：保留 1 位小数 + 百分号，直观且不易误读为整数
            try:
                return f"{float(v) * 100:.1f}%"
            except (TypeError, ValueError):
                return "—"

        def _fmtLift(v):
            # 提升度保留 3 位小数，避免显示为 1.00 时被误认为整数
            try:
                f = float(v)
                import math as _math

                if _math.isnan(f) or _math.isinf(f):
                    return "—"
                return f"{f:.3f}"
            except (TypeError, ValueError):
                return "—"

        def _fmtLev(v):
            # 杠杆值通常很小，保留 5 位小数 + 科学计数法
            try:
                f = float(v)
                import math as _math

                if _math.isnan(f) or _math.isinf(f):
                    return "—"
                if abs(f) < 0.0001 and f != 0:
                    return f"{f:.2e}"
                return f"{f:.5f}"
            except (TypeError, ValueError):
                return "—"

        for i in range(len(rulesDf)):
            row = rulesDf.iloc[i]
            self.table.setItem(
                i, 0, QTableWidgetItem(_formatSet(row.get("antecedents")))
            )
            self.table.setItem(
                i, 1, QTableWidgetItem(_formatSet(row.get("consequents")))
            )
            # 数值列：右侧对齐 + 显示真实小数
            supportItem = QTableWidgetItem(_fmtPct(row.get("support")))
            supportItem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(i, 2, supportItem)

            confItem = QTableWidgetItem(_fmtPct(row.get("confidence")))
            confItem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(i, 3, confItem)

            liftItem = QTableWidgetItem(_fmtLift(row.get("lift")))
            liftItem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(i, 4, liftItem)

            levItem = QTableWidgetItem(_fmtLev(row.get("leverage")))
            levItem.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(i, 5, levItem)

    def _renderScatter(self):
        """支持度 vs 置信度 散点图（点大小=提升度）"""
        if self._scatterFigure:
            plt.close(self._scatterFigure)
        fig = Figure(figsize=(7, 5), dpi=100)
        ax = fig.add_subplot(111)
        self._scatterFigure = fig

        if self.rulesDf is None or self.rulesDf.empty:
            ax.text(
                0.5,
                0.5,
                "暂无规则",
                ha="center",
                va="center",
                fontsize=14,
            )
            ax.axis("off")
        else:
            support = self.rulesDf["support"].values
            confidence = self.rulesDf["confidence"].values
            lift = self.rulesDf["lift"].values

            # 点大小按提升度映射
            sizes = np.clip((lift - 0.5) * 80, 20, 300)

            scatter = ax.scatter(
                support,
                confidence,
                s=sizes,
                c=lift,
                cmap="viridis",
                alpha=0.7,
                edgecolors="white",
                linewidth=0.5,
            )
            ax.set_xlabel("支持度 (Support)", fontsize=11)
            ax.set_ylabel("置信度 (Confidence)", fontsize=11)
            ax.set_title(
                f"关联规则散点图（共 {len(self.rulesDf)} 条，点大小/颜色=提升度）",
                fontsize=12,
                pad=12,
            )
            ax.grid(linestyle="--", alpha=0.4)
            ax.set_xlim(0, max(0.05, float(support.max()) * 1.1))
            ax.set_ylim(0, 1.02)
            cbar = fig.colorbar(scatter, ax=ax, fraction=0.04, pad=0.03)
            cbar.set_label("提升度 (Lift)", fontsize=10)

        fig.tight_layout()
        self.scatterCanvas.figure = fig
        self.scatterCanvas.draw()

    def _renderNetwork(self):
        """网络图：节点=偏误类型，边=关联（权重=置信度）"""
        if self._networkFigure:
            plt.close(self._networkFigure)
        fig = Figure(figsize=(7, 6), dpi=100)
        ax = fig.add_subplot(111)
        self._networkFigure = fig

        if self.rulesDf is None or self.rulesDf.empty:
            ax.text(
                0.5,
                0.5,
                "暂无规则",
                ha="center",
                va="center",
                fontsize=14,
            )
            ax.axis("off")
        else:
            try:
                import networkx as nx
                from itertools import chain

                G = nx.DiGraph()
                for _, row in self.rulesDf.iterrows():
                    for ant in row["antecedents"]:
                        for con in row["consequents"]:
                            if G.has_edge(ant, con):
                                # 取最大置信度
                                G[ant][con]["weight"] = max(
                                    G[ant][con]["weight"], float(row["confidence"])
                                )
                            else:
                                G.add_edge(
                                    ant,
                                    con,
                                    weight=float(row["confidence"]),
                                    lift=float(row["lift"]),
                                )

                if G.number_of_edges() == 0:
                    ax.text(
                        0.5,
                        0.5,
                        "无网络关系",
                        ha="center",
                        va="center",
                        fontsize=14,
                    )
                    ax.axis("off")
                else:
                    pos = nx.spring_layout(G, k=1.2, seed=42)
                    edges = G.edges(data=True)
                    weights = [d["weight"] * 3 for _, _, d in edges]
                    lifts = [d["lift"] for _, _, d in edges]

                    nx.draw_networkx_nodes(
                        G,
                        pos,
                        node_size=900,
                        node_color="#88CCEE",
                        edgecolors="white",
                        linewidths=1.5,
                        ax=ax,
                    )
                    nx.draw_networkx_edges(
                        G,
                        pos,
                        width=weights,
                        edge_color=lifts,
                        edge_cmap=plt.cm.viridis,
                        edge_vmin=min(lifts) if lifts else 0.5,
                        edge_vmax=max(lifts) if lifts else 2.0,
                        alpha=0.7,
                        arrows=True,
                        arrowsize=14,
                        ax=ax,
                    )
                    # 截断过长的标签
                    shortLabels = {
                        n: (n[:8] + "…") if len(n) > 10 else n for n in G.nodes()
                    }
                    nx.draw_networkx_labels(
                        G,
                        pos,
                        labels=shortLabels,
                        font_size=8,
                        font_family="Microsoft YaHei",
                        ax=ax,
                    )
                    ax.set_title(
                        f"偏误共现网络图（共 {G.number_of_edges()} 条边）",
                        fontsize=12,
                        pad=12,
                    )
                    ax.axis("off")
            except ImportError:
                ax.text(
                    0.5,
                    0.5,
                    "缺少 networkx 库\n请 pip install networkx",
                    ha="center",
                    va="center",
                    fontsize=13,
                )
                ax.axis("off")

        fig.tight_layout()
        self.networkCanvas.figure = fig
        self.networkCanvas.draw()

    def _exportCsv(self):
        if self.rulesDf is None or self.rulesDf.empty:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出规则 CSV", "偏误关联规则.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        if not path.endswith(".csv"):
            path += ".csv"

        try:
            exportDf = self.rulesDf.copy()
            exportDf["antecedents"] = exportDf["antecedents"].apply(
                lambda s: ", ".join(sorted(list(s))) if s is not None else ""
            )
            exportDf["consequents"] = exportDf["consequents"].apply(
                lambda s: ", ".join(sorted(list(s))) if s is not None else ""
            )
            exportDf.to_csv(path, index=False, encoding="utf-8-sig")
            InfoBar.success(
                "导出成功",
                f"规则已保存至：{path}",
                Qt.Orientation.Horizontal,
                True,
                2500,
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

    def _exportPng(self):
        currentFig = None
        if self.viewSegment.currentItem() == "scatter":
            currentFig = self._scatterFigure
        elif self.viewSegment.currentItem() == "network":
            currentFig = self._networkFigure
        if currentFig is None:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出图 PNG", "偏误关联规则.png", "PNG Files (*.png)"
        )
        if not path:
            return
        if not path.endswith(".png"):
            path += ".png"
        currentFig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
        InfoBar.success(
            "导出成功",
            f"图片已保存至：{path}",
            Qt.Orientation.Horizontal,
            True,
            2500,
            InfoBarPosition.TOP_RIGHT,
            self,
        )


class MatchingWorker(QThread):
    """偏误匹配后台线程（P1-fix）

    设计要点：
    - 正则在 __init__ 中一次性预编译，避免内层循环反复 re.compile
    - 输入数据在主线程已转换为不可变 List/Tuple 基础类型，
      工作线程无需触碰 pandas DataFrame，避免线程安全问题
    - 通过 requestInterruption() 支持取消
    """

    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        rows: List[Tuple[str, int, str, str, str]],
        compiledPatterns: List[Tuple[str, "re.Pattern[str]", bool]],
        charInput: str,
        wordInput: str,
        parent=None,
    ):
        super().__init__(parent)
        # rows: List[(fileName, excelRow, text, level, country)]
        self._rows = rows
        # compiledPatterns: List[(errorName, compiled_pattern, hasContent)]
        self._patterns = compiledPatterns
        self._charInput = charInput
        self._wordInput = wordInput

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            records: List[Tuple[str, int, str, str, str, str, str]] = []
            typeCounts: Dict[str, int] = {}
            heatmapData: Dict[
                Tuple[str, str], List[Tuple[str, int, str, str, str, str]]
            ] = {}
            heatmapGroups: List[str] = []
            seenGroup: set = set()

            total = max(1, len(self._rows))
            for i, (fileName, excelRow, text, rowLevel, rowCountry) in enumerate(
                self._rows
            ):
                if self.isInterruptionRequested():
                    return
                if i % 200 == 0:
                    self.progress.emit(int((i / total) * 100), f"匹配中 {i}/{total}")

                for errorName, pattern, hasContent in self._patterns:
                    for match in pattern.finditer(text):
                        if hasContent:
                            content = match.group(1) if match.lastindex else ""
                            if not content:
                                content = match.group()
                        else:
                            content = match.group()

                        # 字符/词过滤
                        if self._charInput and self._charInput not in content:
                            continue
                        if self._wordInput and self._wordInput not in content:
                            continue

                        typeCounts[errorName] = typeCounts.get(errorName, 0) + 1
                        records.append(
                            (
                                fileName,
                                excelRow,
                                text,
                                errorName,
                                content,
                                rowLevel,
                                rowCountry,
                            )
                        )

                        # 收集热力图分组数据
                        for groupVal in (rowLevel, rowCountry):
                            if groupVal == "未知":
                                continue
                            key = (errorName, groupVal)
                            if key not in heatmapData:
                                heatmapData[key] = []
                            heatmapData[key].append(
                                (
                                    fileName,
                                    excelRow,
                                    text,
                                    content,
                                    rowLevel,
                                    rowCountry,
                                )
                            )
                            if groupVal not in seenGroup:
                                seenGroup.add(groupVal)
                                heatmapGroups.append(groupVal)

            self.progress.emit(100, f"完成，共 {len(records)} 条命中")
            payload = {
                "records": records,
                "typeCounts": typeCounts,
                "heatmapData": heatmapData,
                "heatmapGroups": heatmapGroups,
            }
            self.finished.emit(payload)
        except Exception as e:
            logger.exception("[MatchingWorker] 匹配异常")
            self.failed.emit(str(e))


class BiasInterface(QWidget):
    """HSK 偏误分析主界面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("BiasInterface")

        self.filesList = []
        self.dfs = {}
        self.loadThread = None
        self.selectedColumn = None
        self.levelColumn = None
        self.countryColumn = None

        # 用户手动配置的列（优先级高于自动识别）
        self.manualLevelColumn = None
        self.manualCountryColumn = None

        # 偏误统计
        self.currentRecords = []
        self.typeCounts = {
            **{name: 0 for name in CHARACTERS_TYPES},
            **{name: 0 for name in SENTENCES_TYPES},
            **{name: 0 for name in WORDS_TYPES},
        }
        self.totalCounts = None

        # 热力图数据：{(errorName, groupValue): [records]}
        self.heatmapData: dict = {}
        self.heatmapGroups: list = []

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
        self.switchBtn.installEventFilter(
            ToolTipFilter(self.switchBtn, 200, ToolTipPosition.TOP)
        )
        self.switchBtn.checkedChanged.connect(self._onModeChanged)

        topLayout.addWidget(self.chooseFileBtn)
        topLayout.addWidget(self.switchBtn)
        topLayout.addStretch()

        # 统计列选择和操作栏
        columnLayout = QHBoxLayout()
        columnLayout.setSpacing(12)

        columnLabel = BodyLabel("统计列:", self)
        columnLabel.setStyleSheet("font-size: 13px;")

        self.columnCombobox = ComboBox(self)
        self.columnCombobox.setEnabled(False)
        self.columnCombobox.currentIndexChanged.connect(self._onColumnChanged)

        self.analyzeBtn = TransparentPushButton("分析", self)
        self.analyzeBtn.setIcon(":app/icons/Check.svg")
        self.analyzeBtn.clicked.connect(self._runMatching)

        self.chartBtn = TransparentPushButton("图表", self)
        self.chartBtn.setIcon(":app/icons/Chart.svg")
        self.chartBtn.clicked.connect(self._runChart)

        self.countBtn = TransparentPushButton("计数", self)
        self.countBtn.setIcon(":app/icons/Number.svg")
        self.countBtn.clicked.connect(self._runCount)

        self.exportBtn = TransparentPushButton("导出", self)
        self.exportBtn.setIcon(":app/icons/Save.svg")
        self.exportBtn.clicked.connect(self._exportResults)

        self.heatmapBtn = TransparentPushButton("热力图", self)
        self.heatmapBtn.setIcon(":app/icons/Chart.svg")
        self.heatmapBtn.clicked.connect(self._runHeatmap)

        self.rulesBtn = TransparentPushButton("关联规则", self)
        self.rulesBtn.setIcon(":app/icons/Chart.svg")
        self.rulesBtn.clicked.connect(self._runAssociationRules)

        self.columnConfigBtn = TransparentPushButton("列配置", self)
        self.columnConfigBtn.setIcon(":app/icons/Setting.svg")
        self.columnConfigBtn.clicked.connect(self._openColumnConfig)

        columnLayout.addWidget(columnLabel)
        columnLayout.addWidget(self.columnCombobox)
        columnLayout.addWidget(self.columnConfigBtn)
        columnLayout.addWidget(VerticalSeparator(self))
        columnLayout.addWidget(self.analyzeBtn)
        columnLayout.addWidget(self.chartBtn)
        columnLayout.addWidget(self.countBtn)
        columnLayout.addWidget(self.heatmapBtn)
        columnLayout.addWidget(self.rulesBtn)
        columnLayout.addWidget(VerticalSeparator(self))
        columnLayout.addWidget(self.exportBtn)
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

        # 结果表格
        self.tableWidget = RoundTableWidget(self)
        self.tableWidget.setMinimumHeight(400)
        self.tableWidget.setColumnCount(7)
        self.tableWidget.setHorizontalHeaderLabels(
            ["文件", "行号", "句子", "偏误类型", "标记内容", "等级", "国籍"]
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
        self.tableWidget.setColumnWidth(5, 80)
        self.tableWidget.setColumnWidth(6, 100)
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

        # 自动识别等级/国籍列（用于热力图）
        self._detectGroupColumns()

    def _detectGroupColumns(self):
        """自动识别等级列与国籍列"""
        self.levelColumn = None
        self.countryColumn = None

        if not self.dfs:
            return

        # 取首个 DataFrame 的列做匹配
        firstDf = next(iter(self.dfs.values()))
        cols = list(firstDf.columns)

        levelKeywords = ["level", "hsk", "等级", "级别", "水准"]
        countryKeywords = ["country", "nationality", "国籍", "国家", "nation"]

        for col in cols:
            colLower = str(col).lower()
            if self.levelColumn is None and any(kw in colLower for kw in levelKeywords):
                self.levelColumn = col
            if self.countryColumn is None and any(
                kw in colLower for kw in countryKeywords
            ):
                self.countryColumn = col

        # 手动配置优先：若用户已指定，则覆盖自动识别结果
        if self.manualLevelColumn and self.manualLevelColumn in cols:
            self.levelColumn = self.manualLevelColumn
        if self.manualCountryColumn and self.manualCountryColumn in cols:
            self.countryColumn = self.manualCountryColumn

        logger.info(
            f"[Bias] 分组列: 等级={self.levelColumn}, 国籍={self.countryColumn}"
        )

    def _openColumnConfig(self):
        """打开列配置弹窗"""
        if not self.dfs:
            InfoBar.warning(
                "提示",
                "请先加载 Excel 文件",
                Qt.Orientation.Horizontal,
                True,
                2000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        # 收集所有列（多文件模式取交集，单文件取当前文件列）
        if self.switchBtn.isChecked() and len(self.dfs) > 1:
            columnSets = [set(df.columns) for df in self.dfs.values()]
            allColumns = sorted(set.intersection(*columnSets))
        else:
            lastFile = self.filesList[-1] if self.filesList else None
            if lastFile and lastFile in self.dfs:
                allColumns = list(self.dfs[lastFile].columns)
            else:
                firstDf = next(iter(self.dfs.values()))
                allColumns = list(firstDf.columns)

        if not allColumns:
            InfoBar.warning(
                "提示",
                "未找到可用列",
                Qt.Orientation.Horizontal,
                True,
                2000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        dialog = ColumnConfigDialog(
            allColumns,
            self.manualLevelColumn or self.levelColumn,
            self.manualCountryColumn or self.countryColumn,
            self.window(),
        )
        if dialog.exec():
            newLevel, newCountry = dialog.getResult()
            self.manualLevelColumn = newLevel
            self.manualCountryColumn = newCountry
            # 重新解析列
            self._detectGroupColumns()
            InfoBar.success(
                "配置已保存",
                f"等级列：{newLevel or '未设置'}  |  国籍列：{newCountry or '未设置'}",
                Qt.Orientation.Horizontal,
                True,
                2500,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            logger.info(f"[Bias] 列配置更新: 等级={newLevel}, 国籍={newCountry}")

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
        self.levelColumn = None
        self.countryColumn = None
        self.currentRecords = []
        self.heatmapData = {}
        self.heatmapGroups = []
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
        """执行匹配分析（P1-fix）

        重构：
        - 正则在启动时一次性 re.compile，避免内层循环反复编译
        - 把 100MB×25 类型×10 万行的匹配移到 MatchingWorker(QThread)，
          防止主线程冻结
        - 主线程仅负责数据收集 + UI 渲染
        """
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

        # P1-fix:启动前若已有 worker,先清理旧实例,避免线程泄漏
        oldWorker = getattr(self, "_matchingWorker", None)
        if oldWorker is not None:
            try:
                if hasattr(oldWorker, "cancel"):
                    oldWorker.cancel()
                if oldWorker.isRunning():
                    oldWorker.wait(1000)
                oldWorker.deleteLater()
            except Exception:
                pass
            self._matchingWorker = None

        charInput = self.charLineEdit.text().strip()
        wordInput = self.wordLineEdit.text().strip()

        # 重置计数
        for name in self.typeCounts:
            self.typeCounts[name] = 0
        self.currentRecords = []
        self.heatmapData = {}
        self.heatmapGroups = []
        self.tableWidget.setRowCount(0)
        self.totalCounts = None

        # P1-fix:预编译正则在主线程一次性完成,避免内层循环重复 re.compile
        compiledPatterns: List[Tuple[str, "re.Pattern[str]", bool]] = []
        for errorName in selectTypes:
            patternStr, hasContent = ERROR_TYPES[errorName]
            compiledPatterns.append((errorName, re.compile(patternStr), hasContent))

        # 把 DataFrame 行转成不可变 tuple list,工作线程不触碰 pandas
        rows: List[Tuple[str, int, str, str, str]] = []
        for filePath, df in self.dfs.items():
            if self.selectedColumn not in df.columns:
                continue

            textSeries = df[self.selectedColumn].astype(str).fillna("")
            fileName = os.path.basename(filePath)

            levelSeries = (
                df[self.levelColumn].astype(str).fillna("")
                if self.levelColumn
                else None
            )
            countrySeries = (
                df[self.countryColumn].astype(str).fillna("")
                if self.countryColumn
                else None
            )

            for idx, text in enumerate(textSeries):
                if not text or text == "nan":
                    continue

                rowLevel = levelSeries.iloc[idx] if levelSeries is not None else ""
                rowCountry = (
                    countrySeries.iloc[idx] if countrySeries is not None else ""
                )
                rowLevel = rowLevel if rowLevel and rowLevel != "nan" else "未知"
                rowCountry = (
                    rowCountry if rowCountry and rowCountry != "nan" else "未知"
                )

                rows.append((fileName, idx + 2, text, rowLevel, rowCountry))

        if not rows:
            InfoBar.warning(
                "提示",
                "未匹配到任何偏误",
                Qt.Orientation.Horizontal,
                True,
                2000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        # 启动后台 worker
        self.analyzeBtn.setEnabled(False)
        worker = MatchingWorker(
            rows=rows,
            compiledPatterns=compiledPatterns,
            charInput=charInput,
            wordInput=wordInput,
            parent=self,
        )
        worker.progress.connect(self._onMatchingProgress)
        worker.finished.connect(self._onMatchingFinished)
        worker.failed.connect(self._onMatchingFailed)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        self._matchingWorker = worker
        self._matchingSelectTypes = list(selectTypes)
        worker.start()

    def _onMatchingProgress(self, pct: int, msg: str) -> None:
        self.statusLabel.setText(f"[{pct}%] {msg}")

    def _onMatchingFinished(self, payload: dict) -> None:
        """后台匹配完成,主线程消费结果并刷新 UI"""
        self.analyzeBtn.setEnabled(True)
        records = payload["records"]
        typeCounts = payload["typeCounts"]
        self.currentRecords = records
        self.heatmapData = payload["heatmapData"]
        self.heatmapGroups = payload["heatmapGroups"]

        # 同步 typeCounts（仅 update 已选类型）
        for name, count in typeCounts.items():
            self.typeCounts[name] = count

        selectTypes = getattr(self, "_matchingSelectTypes", [])
        self.totalCounts = {name: self.typeCounts[name] for name in selectTypes}

        # 填充表格
        self.tableWidget.setRowCount(len(records))
        for i, (fname, rowNum, sentence, errName, mark, level, country) in enumerate(
            records
        ):
            self.tableWidget.setItem(i, 0, QTableWidgetItem(fname))
            self.tableWidget.setItem(i, 1, QTableWidgetItem(str(rowNum)))
            self.tableWidget.setItem(i, 2, QTableWidgetItem(sentence))
            self.tableWidget.setItem(i, 3, QTableWidgetItem(errName))
            self.tableWidget.setItem(i, 4, QTableWidgetItem(mark))
            self.tableWidget.setItem(i, 5, QTableWidgetItem(level))
            self.tableWidget.setItem(i, 6, QTableWidgetItem(country))

        if not records:
            InfoBar.warning(
                "提示",
                "未匹配到任何偏误",
                Qt.Orientation.Horizontal,
                True,
                2000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )

    def _onMatchingFailed(self, err: str) -> None:
        self.analyzeBtn.setEnabled(True)
        logger.error(f"[Bias] 匹配失败: {err}")
        InfoBar.error(
            "错误",
            f"匹配失败: {err}",
            Qt.Orientation.Horizontal,
            True,
            3000,
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

    def _runHeatmap(self):
        """显示偏误热力图（FR-ERR-002）"""
        if not self.heatmapData:
            InfoBar.warning(
                "提示",
                "请先进行分析\n（热力图需要等级或国籍列数据）",
                Qt.Orientation.Horizontal,
                True,
                2500,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        selectedTypes = list(self.totalCounts.keys()) if self.totalCounts else []

        # 拆分等级与国籍两组
        levelGroups = []
        countryGroups = []
        for g in self.heatmapGroups:
            # 根据值特征（HSK/数字级别 vs 其他）粗略区分
            if self._looksLikeLevel(g):
                levelGroups.append(g)
            else:
                countryGroups.append(g)

        # 若两列均未识别且都没数据，按等级优先
        if not levelGroups and not countryGroups:
            levelGroups = list(self.heatmapGroups)

        dialog = HeatmapDialog(
            self.heatmapData,
            selectedTypes,
            list(self.heatmapGroups),
            levelGroups,
            countryGroups,
            self.window(),
        )
        dialog.setDrillDownCallback(self._drillDownFromHeatmap)
        dialog.exec()

    def _runAssociationRules(self):
        """偏误关联规则挖掘（FR-ERR-003）

        按文件级别构建事务：每个文件中的偏误类型集合 = 一个事务。
        """
        if not self.currentRecords:
            InfoBar.warning(
                "提示",
                "请先进行分析",
                Qt.Orientation.Horizontal,
                True,
                2500,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        # 按文件聚合偏误类型
        fileToTypes: dict = {}
        for record in self.currentRecords:
            if len(record) < 7:
                continue
            fileName = record[0]
            errName = record[3]
            fileToTypes.setdefault(fileName, set()).add(errName)

        transactions = [sorted(list(types)) for types in fileToTypes.values() if types]

        if len(transactions) < 2:
            InfoBar.warning(
                "提示",
                "事务数量不足（至少需要 2 个文件包含偏误）",
                Qt.Orientation.Horizontal,
                True,
                3000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        # 根据事务数自适应阈值：事务少时降低支持度门槛
        if len(transactions) <= 5:
            minSupport = 0.2
        elif len(transactions) <= 20:
            minSupport = 0.1
        else:
            minSupport = 0.05

        logger.info(
            f"[Bias] 启动关联规则挖掘: 事务数={len(transactions)}, "
            f"支持度阈值={minSupport}"
        )

        dialog = AssociationRulesDialog(
            transactions,
            minSupport,
            0.5,
            self.window(),
        )
        dialog.exec()

    def _looksLikeLevel(self, value: str) -> bool:
        """简易判断：HSK 等级 / 数字级别"""
        if not value:
            return False
        v = str(value).strip().lower()
        if v.startswith("hsk"):
            return True
        if v in {"1", "2", "3", "4", "5", "6", "7", "8", "9"}:
            return True
        if v in {
            "a",
            "b",
            "c",
            "初级",
            "中级",
            "高级",
            "beginner",
            "intermediate",
            "advanced",
        }:
            return True
        return False

    def _drillDownFromHeatmap(self, errorName: str, groupVal: str):
        """热力图单元格点击下钻：在主表格过滤对应 (偏误类型, 分组) 的记录"""
        if not self.currentRecords:
            return

        # 根据分组值确定该行的 level/country
        # 重新从完整记录中过滤：(errorName 匹配) AND (等级或国籍 == groupVal)
        mode = "level" if self._looksLikeLevel(groupVal) else "country"

        filtered = []
        for record in self.currentRecords:
            if len(record) < 7:
                continue
            fileName, rowNum, text, errName, content, rowLevel, rowCountry = record
            if errName != errorName:
                continue

            # 直接从记录中的 level/country 字段判断（无需再查 df）
            if mode == "level":
                match = rowLevel == groupVal
            else:
                match = rowCountry == groupVal

            if match:
                filtered.append(record)

        if not filtered:
            # 退化：仅按 errorName 过滤
            filtered = [r for r in self.currentRecords if r[3] == errorName]

        # 重绘主表格
        self.tableWidget.setRowCount(len(filtered))
        for i, record in enumerate(filtered):
            if len(record) < 7:
                fname, rowNum, sentence, errName, mark = record[:5]
                level, country = "", ""
            else:
                fname, rowNum, sentence, errName, mark, level, country = record
            self.tableWidget.setItem(i, 0, QTableWidgetItem(fname))
            self.tableWidget.setItem(i, 1, QTableWidgetItem(str(rowNum)))
            self.tableWidget.setItem(i, 2, QTableWidgetItem(sentence))
            self.tableWidget.setItem(i, 3, QTableWidgetItem(errName))
            self.tableWidget.setItem(i, 4, QTableWidgetItem(mark))
            self.tableWidget.setItem(i, 5, QTableWidgetItem(level))
            self.tableWidget.setItem(i, 6, QTableWidgetItem(country))

        modeZh = "等级" if mode == "level" else "国籍"
        InfoBar.success(
            "下钻成功",
            f"已过滤：{errorName} × {groupVal}（{modeZh}）\n共 {len(filtered)} 条记录",
            Qt.Orientation.Horizontal,
            True,
            2500,
            InfoBarPosition.TOP_RIGHT,
            self,
        )

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
            columns=["文件", "行号", "句子", "偏误类型", "标记内容", "等级", "国籍"],
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
