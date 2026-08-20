# coding: utf-8
"""
HSK 偏误分析界面
"""

import os
import io

import numpy as np
import pandas as pd
from app.core.services import (
    BIAS_TEXT_COLUMN,
    biasDocumentService,
    beginPaidAnalysisExport,
)
from app.core.services.association_rule_service import mineAssociationRules
from app.core.utils import logger
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QBoxLayout,
    QFileDialog,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QStackedWidget,
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
    TitleLabel,
    TransparentToggleToolButton,
    CheckBox,
    isDarkTheme,
    qconfig,
    ScrollArea,
    SpinBox,
    TableView,
)
from app.view.widgets.prismatica_table import PrismaticaTableWidget
from app.view.widgets.result_table_models import BiasResultTableModel
from app.view.widgets.prismatica_theme import shellPalette

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

        self.checkAllBtn = QPushButton("全选", self)
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

        self.countLabel = CaptionLabel("已选 0", self)
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
            cb.stateChanged.connect(lambda s, name=item: self._onChecked(s, name))
            self.checkboxes[item] = cb
            flowLayout.addWidget(cb)

        mainLayout.addLayout(flowLayout)
        self.applyTheme()

    def applyTheme(self, palette=None) -> None:
        """同步筛选器文字、计数与交互态的语义色。"""
        palette = palette or shellPalette()
        text = palette.text.name()
        muted = palette.mutedText.name()
        accent = palette.accentText.name()
        hover = palette.accentSurface.name()

        self.checkAllBtn.setStyleSheet(
            "QPushButton {"
            " border: none;"
            f" color: {accent};"
            " font-size: 11px; padding: 2px 6px; background: transparent;"
            "}"
            "QPushButton:hover {"
            f" background: {hover}; border-radius: 3px;"
            "}"
        )
        self.countLabel.setStyleSheet(f"color: {muted}; font-size: 11px;")
        for checkbox in self.checkboxes.values():
            checkbox.setStyleSheet(
                "QCheckBox {"
                f" color: {text};"
                " font-size: 12px; padding: 4px 8px;"
                "}"
                f"QCheckBox:disabled {{ color: {muted}; }}"
            )

    def _onChecked(self, state, name):
        self._updateCount()
        self.selectionChanged.emit(self.selectedTexts())

    def _toggleAll(self):
        """切换全选与清空。"""
        allChecked = all(cb.isChecked() for cb in self.checkboxes.values())
        for cb in self.checkboxes.values():
            cb.setChecked(not allChecked)

    def _updateCount(self):
        count = sum(1 for cb in self.checkboxes.values() if cb.isChecked())
        self.countLabel.setText(f"已选 {count}")
        self.checkAllBtn.setText("清空" if count == len(self.checkboxes) else "全选")

    def selectedTexts(self) -> list:
        """获取选中的文本列表"""
        return [name for name, cb in self.checkboxes.items() if cb.isChecked()]

    def clearSelection(self):
        """清空选择"""
        for cb in self.checkboxes.values():
            cb.setChecked(False)


class BiasEmptyState(QWidget):
    """偏误分析结果页的统一空状态。"""

    def __init__(
        self,
        title: str,
        description: str,
        iconPath: str = ":app/icons/Chart.svg",
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("biasEmptyState")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 48, 24, 48)
        layout.setSpacing(8)
        layout.addStretch(1)

        self.iconWidget = QSvgWidget(iconPath, self)
        self.iconWidget.setFixedSize(40, 40)
        layout.addWidget(
            self.iconWidget,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )

        self.titleLabel = SubtitleLabel(title, self)
        self.titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.titleLabel)

        self.descriptionLabel = CaptionLabel(description, self)
        self.descriptionLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.descriptionLabel.setWordWrap(True)
        self.descriptionLabel.setStyleSheet("color: #7A7A7A;")
        layout.addWidget(self.descriptionLabel)
        layout.addStretch(1)

    def setContent(self, title: str, description: str) -> None:
        """更新空状态文案。"""
        self.titleLabel.setText(title)
        self.descriptionLabel.setText(description)


class FileLoaderThread(QThread):
    """偏误分析文件加载线程。"""

    progress = Signal(int, int, str, float)
    fileLoaded = Signal(str, object, int)
    error = Signal(str)
    finished = Signal()

    def __init__(self, filePaths, documentService=None):
        super().__init__()
        self.filePaths = filePaths
        self.documentService = documentService or biasDocumentService
        self._isCanceled = False

    def cancel(self):
        self._isCanceled = True

    def _loadFile(self, filePath: str) -> tuple:
        """通过服务层加载并标准化文件。"""
        return self.documentService.loadFile(filePath)

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


class AssociationRuleWorkerThread(QThread):
    """句子级偏误关联统计后台线程（FR-ERR-003）。"""

    progress = Signal(int, str)  # (percent 0-100, status)
    finished = Signal(object)  # (rulesDf) DataFrame
    failed = Signal(str)  # error msg

    def __init__(
        self,
        transactions: list,
        minSupport: float = 0.01,
        minConfidence: float = 0.5,
        minJointCount: int = 3,
        familyWiseAlpha: float = 0.05,
    ):
        super().__init__()
        self.transactions = transactions
        self.minSupport = minSupport
        self.minConfidence = minConfidence
        self.minJointCount = minJointCount
        self.familyWiseAlpha = familyWiseAlpha
        self._isCanceled = False

    def cancel(self):
        self._isCanceled = True

    def run(self):
        try:
            if not self.transactions:
                self.failed.emit("无有效事务数据")
                return

            self.progress.emit(15, "正在构建句子级列联表...")

            if self._isCanceled:
                return

            self.progress.emit(45, "正在执行 Fisher 精确检验...")
            rules = mineAssociationRules(
                self.transactions,
                minSupport=self.minSupport,
                minConfidence=self.minConfidence,
                minJointCount=self.minJointCount,
                familyWiseAlpha=self.familyWiseAlpha,
            )

            if self._isCanceled:
                return

            if rules.empty:
                self.progress.emit(100, "未发现通过多重比较校正的正关联")
                self.finished.emit(rules)
                return

            self.progress.emit(100, f"统计完成，共保留 {len(rules)} 条方向规则")
            self.finished.emit(rules)

        except Exception as e:
            logger.error(f"[Bias] 关联统计失败: {e}")
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
        self.table = PrismaticaTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["偏误类型", "计数", "占比"])
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(
            PrismaticaTableWidget.SelectionBehavior.SelectRows
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            PrismaticaTableWidget.EditTrigger.NoEditTriggers
        )
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
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.canvas.setMinimumSize(0, 0)
        self._currentFigure = self.canvas.figure

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
        fig = self.canvas.figure
        fig.clear()
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
        self.canvas.draw()

    def _drawBar(self):
        fig = self.canvas.figure
        fig.clear()
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

        transaction = beginPaidAnalysisExport(self.window(), f"偏误统计图 {fmt.upper()}")
        if transaction is None:
            return
        try:
            self._currentFigure.savefig(
                path, dpi=300, bbox_inches="tight", facecolor="white"
            )
            if not transaction.commit():
                raise RuntimeError("导出文件已生成，但计费结算失败；本次费用已释放")
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
        except Exception as error:
            transaction.refund()
            InfoBar.error("导出失败", str(error), Qt.Orientation.Horizontal, True, 3000, InfoBarPosition.TOP_RIGHT, self)

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
        if not getattr(self, "_isEmbedded", False):
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
            "为「等级」「国籍」分别指定表格字段。\n"
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
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.canvas.setMinimumSize(0, 0)
        self._currentFigure = self.canvas.figure

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
        fig = self.canvas.figure
        fig.clear()
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
        if not getattr(self, "_isEmbedded", False):
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
        transaction = beginPaidAnalysisExport(self.window(), f"偏误热力图 {fmt.upper()}")
        if transaction is None:
            return
        try:
            self._currentFigure.savefig(
                path, dpi=300, bbox_inches="tight", facecolor="white"
            )
            if not transaction.commit():
                raise RuntimeError("导出文件已生成，但计费结算失败；本次费用已释放")
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
        except Exception as error:
            transaction.refund()
            InfoBar.error("导出失败", str(error), Qt.Orientation.Horizontal, True, 3000, InfoBarPosition.TOP_RIGHT, self)

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
        if not getattr(self, "_isEmbedded", False):
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
        self.paramWidget = QWidget()
        paramLayout = QGridLayout(self.paramWidget)
        paramLayout.setContentsMargins(0, 0, 0, 0)
        paramLayout.setHorizontalSpacing(10)
        paramLayout.setVerticalSpacing(8)

        supportLabel = BodyLabel("最小支持度:", self)
        self.supportSpin = DoubleSpinBox(self)
        self.supportSpin.setRange(0.01, 1.0)
        self.supportSpin.setSingleStep(0.01)
        self.supportSpin.setDecimals(2)
        self.supportSpin.setValue(minSupport)
        self.supportSpin.setMinimumWidth(110)
        self.supportSpin.setMaximumWidth(150)
        self.supportSpin.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        confidenceLabel = BodyLabel("最小置信度:", self)
        self.confidenceSpin = DoubleSpinBox(self)
        self.confidenceSpin.setRange(0.05, 1.0)
        self.confidenceSpin.setSingleStep(0.05)
        self.confidenceSpin.setDecimals(2)
        self.confidenceSpin.setValue(minConfidence)
        self.confidenceSpin.setMinimumWidth(110)
        self.confidenceSpin.setMaximumWidth(150)
        self.confidenceSpin.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        jointCountLabel = BodyLabel("最小共现次数:", self)
        self.parameterLabels = (supportLabel, confidenceLabel, jointCountLabel)
        self.jointCountSpin = SpinBox(self)
        self.jointCountSpin.setRange(2, 100)
        self.jointCountSpin.setValue(3)
        self.jointCountSpin.setMinimumWidth(110)
        self.jointCountSpin.setMaximumWidth(150)
        self.jointCountSpin.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        self.recomputeBtn = PushButton("重新计算", self)
        self.recomputeBtn.setIcon(":app/icons/Refresh.svg")
        self.recomputeBtn.clicked.connect(self._recompute)

        paramLayout.addWidget(supportLabel, 0, 0)
        paramLayout.addWidget(self.supportSpin, 0, 1)
        paramLayout.addWidget(confidenceLabel, 1, 0)
        paramLayout.addWidget(self.confidenceSpin, 1, 1)
        paramLayout.addWidget(jointCountLabel, 2, 0)
        paramLayout.addWidget(self.jointCountSpin, 2, 1)
        paramLayout.addWidget(
            self.recomputeBtn,
            0,
            2,
            3,
            1,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        paramLayout.setColumnStretch(1, 1)

        self.methodLabel = CaptionLabel(
            "句子级事务(每个有效表格行或文档段落一个事务,含零命中事务) · "
            "方向规则族单侧 Fisher 精确检验 · Holm 校正 α=0.05 · 仅保留提升度 > 1。"
            "同一作者或篇章内句子可能相关，结果用于探索而非因果推断。",
            self,
        )
        self.methodLabel.setWordWrap(True)

        # Tab 切换：表格 / 散点图 / 网络图
        self.viewSegment = SegmentedWidget(self)
        self.viewSegment.addItem("table", "表格")
        self.viewSegment.addItem("scatter", "散点图")
        self.viewSegment.addItem("network", "网络图")
        self.viewSegment.setCurrentItem("table")
        self.viewSegment.currentItemChanged.connect(self._onViewChanged)

        # --- 表格视图 ---
        self.table = PrismaticaTableWidget(self)
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            [
                "前项",
                "后项",
                "共现次数",
                "支持度",
                "置信度",
                "提升度",
                "杠杆值",
                "确信度",
                "Holm 校正 p",
            ]
        )
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(
            PrismaticaTableWidget.SelectionBehavior.SelectRows
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            PrismaticaTableWidget.EditTrigger.NoEditTriggers
        )
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        numericColumnWidths = {
            2: 78,
            3: 82,
            4: 82,
            5: 76,
            6: 92,
            7: 82,
            8: 102,
        }
        for col, width in numericColumnWidths.items():
            self.table.setColumnWidth(col, width)
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
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.scatterCanvas.setMinimumSize(0, 0)
        self.scatterScroll = ScrollArea(self)
        self.scatterScroll.setWidget(self.scatterCanvas)
        self.scatterScroll.setWidgetResizable(True)
        self.scatterScroll.setStyleSheet("border: none; background: transparent;")
        self.scatterScroll.hide()

        # --- 网络图视图 ---
        self.networkCanvas = FigureCanvas(Figure(figsize=(7, 6), dpi=100))
        self.networkCanvas.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.networkCanvas.setMinimumSize(0, 0)
        self.networkScroll = ScrollArea(self)
        self.networkScroll.setWidget(self.networkCanvas)
        self.networkScroll.setWidgetResizable(True)
        self.networkScroll.setStyleSheet("border: none; background: transparent;")
        self.networkScroll.hide()

        # 状态/进度
        self.statusLabel = CaptionLabel("点击「重新计算」开始挖掘...", self)
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

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
        self.viewLayout.addWidget(self.paramWidget)
        self.viewLayout.addWidget(self.methodLabel)
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

        self.widget.setMinimumSize(440, 480)
        self.widget.resize(680, 560)
        self._applyDialogTheme()
        qconfig.themeChangedFinished.connect(self._applyDialogTheme)

        # 初始计算
        self._recompute()

    def _applyDialogTheme(self, *_args) -> None:
        palette = shellPalette()
        for label in self.parameterLabels:
            label.setStyleSheet(
                f"color: {palette.text.name()}; font-size: 12px;"
            )
        for label in (self.methodLabel, self.statusLabel):
            label.setStyleSheet(
                f"color: {palette.mutedText.name()}; font-size: 11px;"
            )

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
        self.minJointCount = int(self.jointCountSpin.value())
        self.recomputeBtn.setEnabled(False)
        self.exportCsvBtn.setEnabled(False)
        self.exportPngBtn.setEnabled(False)
        self.statusLabel.setText("正在挖掘...")

        self._workerThread = AssociationRuleWorkerThread(
            self.transactions,
            self.minSupport,
            self.minConfidence,
            self.minJointCount,
            0.05,
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
        if rulesDf is None:
            rulesDf = pd.DataFrame()
        self.rulesDf = rulesDf
        transactionCount = int(
            rulesDf.attrs.get("transactionCount", len(self.transactions))
        )
        testedPairCount = int(rulesDf.attrs.get("testedPairCount", 0))
        testedHypothesisCount = int(
            rulesDf.attrs.get("testedHypothesisCount", testedPairCount * 2)
        )
        familyWiseAlpha = float(rulesDf.attrs.get("familyWiseAlpha", 0.05))

        if rulesDf is None or rulesDf.empty:
            self.statusLabel.setText(
                f"未发现通过统计筛选的正关联：共 {transactionCount} 个句子事务，"
                f"检验 {testedPairCount} 组偏误关系 / "
                f"{testedHypothesisCount} 个方向假设，Holm 校正 α={familyWiseAlpha:.2f}。"
                "可以降低支持度或置信度查看，但不建议降低显著性标准。"
            )
            self.table.setRowCount(0)
            return

        self._populateTable(rulesDf)
        self._renderScatter()
        self._renderNetwork()
        self.exportCsvBtn.setEnabled(True)
        self.exportPngBtn.setEnabled(True)

        # 计算项目数与统计摘要
        numTransactions = transactionCount
        allItems = set()
        for t in self.transactions:
            allItems.update(t)
        numItems = len(allItems)

        # 统计指标范围
        confMin = rulesDf["confidence"].min()
        confMax = rulesDf["confidence"].max()
        liftMin = rulesDf["lift"].min()
        liftMax = rulesDf["lift"].max()
        adjustedPMax = rulesDf["adjusted p-value"].max()

        # 样本量警告
        warning = ""
        if numTransactions < 30:
            warning = (
                f" 样本量较少（句子={numTransactions}），结果仅适合作为探索性线索。"
            )
        elif numItems > numTransactions * 3:
            warning = (
                f" ⚠ 项目数({numItems})远多于事务数({numTransactions})，"
                f"统计可能不稳定。"
            )

        self.statusLabel.setText(
            f"完成：{len(rulesDf)} 条规则 | "
            f"句子事务={numTransactions}  偏误类型={numItems}  "
            f"方向假设={testedHypothesisCount}  "
            f"置信度范围=[{confMin * 100:.1f}%, {confMax * 100:.1f}%]  "
            f"提升度=[{liftMin:.2f}, {liftMax:.2f}]  "
            f"最大校正 p={adjustedPMax:.4g}。关联不表示因果。{warning}"
        )
        logger.info(
            f"[Bias] 关联规则挖掘完成: {len(rulesDf)} 条, "
            f"事务={numTransactions}, 项目={numItems}, "
            f"置信度范围=[{confMin:.3f}, {confMax:.3f}], "
            f"提升度范围=[{liftMin:.3f}, {liftMax:.3f}], "
            f"最大Holm校正p={adjustedPMax:.6g}"
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
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rulesDf))

        def _formatSet(s):
            return ", ".join(sorted(list(s))) if s is not None else ""

        def _fmtPct(v):
            # 百分比格式：保留 1 位小数 + 百分号，直观且不易误读为整数
            try:
                return f"{float(v) * 100:.1f}%"
            except (TypeError, ValueError):
                return "—"

        def _fmtMetric(v, digits=3):
            try:
                f = float(v)
                import math as _math

                if _math.isnan(f):
                    return "—"
                if _math.isinf(f):
                    return "∞"
                return f"{f:.{digits}f}"
            except (TypeError, ValueError):
                return "—"

        def _fmtSmallNumber(v):
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

        def _setNumericItem(rowIndex, columnIndex, text, value):
            item = QTableWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, value)
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(rowIndex, columnIndex, item)

        for i in range(len(rulesDf)):
            row = rulesDf.iloc[i]
            self.table.setItem(
                i, 0, QTableWidgetItem(_formatSet(row.get("antecedents")))
            )
            self.table.setItem(
                i, 1, QTableWidgetItem(_formatSet(row.get("consequents")))
            )
            _setNumericItem(
                i,
                2,
                str(int(row.get("joint count", 0))),
                int(row.get("joint count", 0)),
            )
            _setNumericItem(
                i,
                3,
                _fmtPct(row.get("support")),
                float(row.get("support", 0)),
            )
            _setNumericItem(
                i,
                4,
                _fmtPct(row.get("confidence")),
                float(row.get("confidence", 0)),
            )
            _setNumericItem(
                i,
                5,
                _fmtMetric(row.get("lift")),
                float(row.get("lift", 0)),
            )
            _setNumericItem(
                i,
                6,
                _fmtSmallNumber(row.get("leverage")),
                float(row.get("leverage", 0)),
            )
            _setNumericItem(
                i,
                7,
                _fmtMetric(row.get("conviction")),
                float(row.get("conviction", 0)),
            )
            _setNumericItem(
                i,
                8,
                _fmtSmallNumber(row.get("adjusted p-value")),
                float(row.get("adjusted p-value", 1)),
            )

        self.table.setSortingEnabled(True)

    def _renderScatter(self):
        """提升度与置信度散点图，编码共现证据量和校正显著性。"""
        fig = self.scatterCanvas.figure
        fig.clear()
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
            confidence = self.rulesDf["confidence"].values
            lift = self.rulesDf["lift"].values
            jointCounts = self.rulesDf["joint count"].values
            adjustedPValues = np.clip(
                self.rulesDf["adjusted p-value"].values.astype(float),
                1e-12,
                1.0,
            )
            significance = -np.log10(adjustedPValues)

            sizes = np.clip(jointCounts * 12, 30, 320)

            scatter = ax.scatter(
                lift,
                confidence,
                s=sizes,
                c=significance,
                cmap="viridis",
                alpha=0.7,
                edgecolors="white",
                linewidth=0.5,
            )
            ax.set_xlabel("提升度 (Lift)", fontsize=11)
            ax.set_ylabel("置信度 (Confidence)", fontsize=11)
            ax.set_title(
                "显著正关联分布（点大小=共现次数，颜色=-log10 校正p）",
                fontsize=12,
                pad=12,
            )
            ax.grid(linestyle="--", alpha=0.4)
            ax.axvline(1.0, color="#888", linestyle="--", linewidth=1)
            ax.set_xlim(0.95, max(1.05, float(lift.max()) * 1.08))
            ax.set_ylim(0, 1.02)
            cbar = fig.colorbar(scatter, ax=ax, fraction=0.04, pad=0.03)
            cbar.set_label("-log10(Holm 校正 p)", fontsize=10)

        fig.tight_layout()
        self.scatterCanvas.draw()

    def _renderNetwork(self):
        """网络图：节点=偏误类型，边=关联（权重=置信度）"""
        fig = self.networkCanvas.figure
        fig.clear()
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

        transaction = beginPaidAnalysisExport(self.window(), "偏误关联规则 CSV")
        if transaction is None:
            return
        try:
            exportDf = self.rulesDf.copy()
            exportDf["antecedents"] = exportDf["antecedents"].apply(
                lambda s: ", ".join(sorted(list(s))) if s is not None else ""
            )
            exportDf["consequents"] = exportDf["consequents"].apply(
                lambda s: ", ".join(sorted(list(s))) if s is not None else ""
            )
            exportDf.to_csv(path, index=False, encoding="utf-8-sig")
            if not transaction.commit():
                raise RuntimeError("导出文件已生成，但计费结算失败；本次费用已释放")
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
            transaction.refund()
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
        transaction = beginPaidAnalysisExport(self.window(), "偏误关联规则图 PNG")
        if transaction is None:
            return
        try:
            currentFig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
            if not transaction.commit():
                raise RuntimeError("导出文件已生成，但计费结算失败；本次费用已释放")
            InfoBar.success(
                "导出成功",
                f"图片已保存至：{path}",
                Qt.Orientation.Horizontal,
                True,
                2500,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
        except Exception as error:
            transaction.refund()
            InfoBar.error("导出失败", str(error), Qt.Orientation.Horizontal, True, 3000, InfoBarPosition.TOP_RIGHT, self)


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
            transactions: List[List[str]] = []

            total = max(1, len(self._rows))
            for i, (fileName, excelRow, text, rowLevel, rowCountry) in enumerate(
                self._rows
            ):
                if self.isInterruptionRequested():
                    return
                if i % 200 == 0:
                    self.progress.emit(int((i / total) * 100), f"匹配中 {i}/{total}")

                rowTypes = set()
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
                        rowTypes.add(errorName)
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

                # 每个有效句子都是一个事务；空列表代表该句未命中所选偏误。
                transactions.append(sorted(rowTypes))

            self.progress.emit(100, f"完成，共 {len(records)} 条命中")
            payload = {
                "records": records,
                "typeCounts": typeCounts,
                "heatmapData": heatmapData,
                "heatmapGroups": heatmapGroups,
                "transactions": transactions,
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
        self._pendingFileCount = 0
        self._failedFileCount = 0
        self._isAnalysisRunning = False
        self.selectedColumn = None
        self.levelColumn = None
        self.countryColumn = None

        # 用户手动配置的列（优先级高于自动识别）
        self.manualLevelColumn = None
        self.manualCountryColumn = None

        # 偏误统计
        self.currentRecords = []
        self.associationTransactions = []
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
        # 外层仅负责纵向滚动，窄窗口不再产生整页横向滚动。
        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setStyleSheet("border: none; background: transparent;")
        self.scrollArea.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.scrollContent = QWidget()
        self.scrollContent.setObjectName("biasScrollContent")
        scrollLayout = QVBoxLayout(self.scrollContent)
        scrollLayout.setContentsMargins(24, 20, 24, 24)
        scrollLayout.setSpacing(16)

        # 页面标题只保留全局信息，不再堆叠全部操作。
        titleLayout = QHBoxLayout()
        titleLayout.setSpacing(12)
        titleTextLayout = QVBoxLayout()
        titleTextLayout.setSpacing(2)
        pageTitle = TitleLabel("偏误分析", self.scrollContent)
        self.pageDescription = CaptionLabel(
            "定位偏误句，并查看统计关系",
            self.scrollContent,
        )
        self.pageDescription.setWordWrap(True)
        titleTextLayout.addWidget(pageTitle)
        titleTextLayout.addWidget(self.pageDescription)
        titleLayout.addLayout(titleTextLayout, 1)
        scrollLayout.addLayout(titleLayout)

        # 主工作区：宽屏左右排列，窄屏上下排列。
        self.workspaceLayout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.workspaceLayout.setContentsMargins(0, 0, 0, 0)
        self.workspaceLayout.setSpacing(16)

        self.conditionCard = QWidget(self.scrollContent)
        self.conditionCard.setObjectName("biasConditionCard")
        self.conditionCard.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        conditionLayout = QVBoxLayout(self.conditionCard)
        conditionLayout.setContentsMargins(16, 16, 16, 16)
        conditionLayout.setSpacing(10)

        conditionTitle = SubtitleLabel("分析条件", self.conditionCard)
        conditionLayout.addWidget(conditionTitle)

        self.chooseFileBtn = PushButton("选择文件", self.conditionCard)
        self.chooseFileBtn.setIcon(FluentIcon.FOLDER)
        self.chooseFileBtn.setAccessibleName("选择偏误分析文件")
        self.chooseFileBtn.setToolTip("支持 XLSX、TXT、DOCX 和 DOC")
        self.chooseFileBtn.clicked.connect(self._onChooseFile)
        conditionLayout.addWidget(self.chooseFileBtn)

        self.sourceStatusLabel = CaptionLabel(
            "尚未加载文件 · 支持 XLSX、TXT、DOCX、DOC",
            self.conditionCard,
        )
        self.sourceStatusLabel.setWordWrap(True)
        self.sourceStatusLabel.setStyleSheet("color: #7A7A7A;")
        conditionLayout.addWidget(self.sourceStatusLabel)

        self.columnLabel = CaptionLabel("分析字段", self.conditionCard)
        conditionLayout.addWidget(self.columnLabel)

        columnLayout = QHBoxLayout()
        columnLayout.setSpacing(8)
        self.columnCombobox = ComboBox(self.conditionCard)
        self.columnCombobox.setEnabled(False)
        self.columnCombobox.currentIndexChanged.connect(self._onColumnChanged)
        self.columnCombobox.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        columnLayout.addWidget(self.columnCombobox, 1)

        self.columnConfigBtn = PushButton("配置", self.conditionCard)
        self.columnConfigBtn.setIcon(":app/icons/Setting.svg")
        self.columnConfigBtn.setEnabled(False)
        self.columnConfigBtn.clicked.connect(self._openColumnConfig)
        columnLayout.addWidget(self.columnConfigBtn)
        conditionLayout.addLayout(columnLayout)

        self.columnCompatibilityLabel = CaptionLabel("", self.conditionCard)
        self.columnCompatibilityLabel.setWordWrap(True)
        self.columnCompatibilityLabel.setStyleSheet("color: #A15C00;")
        self.columnCompatibilityLabel.hide()
        conditionLayout.addWidget(self.columnCompatibilityLabel)

        filterTitleLayout = QHBoxLayout()
        self.filterTitle = BodyLabel("偏误类型", self.conditionCard)
        self.filterTitle.setStyleSheet("font-weight: 600;")
        filterTitleLayout.addWidget(self.filterTitle)
        filterTitleLayout.addStretch(1)
        self.selectionSummaryLabel = CaptionLabel("未选择", self.conditionCard)
        self.selectionSummaryLabel.setStyleSheet("color: #707070;")
        filterTitleLayout.addWidget(self.selectionSummaryLabel)
        conditionLayout.addLayout(filterTitleLayout)

        self.filterSegment = SegmentedWidget(self.conditionCard)
        self.filterSegment.addItem("character", "字符")
        self.filterSegment.addItem("word", "词语")
        self.filterSegment.addItem("sentence", "句子")
        self.filterSegment.setCurrentItem("character")
        conditionLayout.addWidget(self.filterSegment)

        self.filterStack = QStackedWidget(self.conditionCard)
        self.filterStack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.filterStack.setFixedHeight(280)

        charPage = QWidget(self.filterStack)
        charLayout = QVBoxLayout(charPage)
        charLayout.setContentsMargins(0, 4, 0, 0)
        charLayout.setSpacing(8)
        self.charLineEdit = LineEdit(charPage)
        self.charLineEdit.setPlaceholderText("可选：仅匹配指定字符")
        self.charFilter = MultiSelectFilter(list(CHARACTERS_TYPES.keys()), charPage)
        self.charFilter.selectionChanged.connect(self._onFilterChanged)
        charScroll = ScrollArea(charPage)
        charScroll.setWidgetResizable(True)
        charScroll.setStyleSheet("border: none; background: transparent;")
        charScroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        charScroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        charScroll.setWidget(self.charFilter)
        charLayout.addWidget(self.charLineEdit)
        charLayout.addWidget(charScroll, 1)
        self.filterStack.addWidget(charPage)

        wordPage = QWidget(self.filterStack)
        wordLayout = QVBoxLayout(wordPage)
        wordLayout.setContentsMargins(0, 4, 0, 0)
        wordLayout.setSpacing(8)
        self.wordLineEdit = LineEdit(wordPage)
        self.wordLineEdit.setPlaceholderText("可选：仅匹配指定词语")
        self.wordFilter = MultiSelectFilter(list(WORDS_TYPES.keys()), wordPage)
        self.wordFilter.selectionChanged.connect(self._onFilterChanged)
        wordScroll = ScrollArea(wordPage)
        wordScroll.setWidgetResizable(True)
        wordScroll.setStyleSheet("border: none; background: transparent;")
        wordScroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        wordScroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        wordScroll.setWidget(self.wordFilter)
        wordLayout.addWidget(self.wordLineEdit)
        wordLayout.addWidget(wordScroll, 1)
        self.filterStack.addWidget(wordPage)

        sentencePage = QWidget(self.filterStack)
        sentenceLayout = QVBoxLayout(sentencePage)
        sentenceLayout.setContentsMargins(0, 4, 0, 0)
        self.sentFilter = MultiSelectFilter(
            list(SENTENCES_TYPES.keys()),
            sentencePage,
        )
        self.sentFilter.selectionChanged.connect(self._onFilterChanged)
        sentenceScroll = ScrollArea(sentencePage)
        sentenceScroll.setWidgetResizable(True)
        sentenceScroll.setStyleSheet("border: none; background: transparent;")
        sentenceScroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        sentenceScroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        sentenceScroll.setWidget(self.sentFilter)
        sentenceLayout.addWidget(sentenceScroll, 1)
        self.filterStack.addWidget(sentencePage)

        self.filterSegment.currentItemChanged.connect(self._onFilterPageChanged)
        conditionLayout.addWidget(self.filterStack)

        conditionLayout.addStretch(1)

        self.analyzeBtn = PrimaryPushButton("开始分析", self.conditionCard)
        self.analyzeBtn.setIcon(":app/icons/Check.svg")
        self.analyzeBtn.setEnabled(False)
        self.analyzeBtn.clicked.connect(self._runMatching)
        conditionLayout.addWidget(self.analyzeBtn)

        self.workspaceLayout.addWidget(self.conditionCard)
        self.workspaceLayout.setAlignment(
            self.conditionCard,
            Qt.AlignmentFlag.AlignTop,
        )

        self.resultCard = QWidget(self.scrollContent)
        self.resultCard.setObjectName("biasResultCard")
        self.resultCard.setMinimumHeight(540)
        self.resultCard.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        resultLayout = QVBoxLayout(self.resultCard)
        resultLayout.setContentsMargins(18, 18, 18, 18)
        resultLayout.setSpacing(12)

        resultHeader = QHBoxLayout()
        resultTitleLayout = QVBoxLayout()
        resultTitleLayout.setSpacing(2)
        resultTitle = SubtitleLabel("分析结果", self.resultCard)
        self.statusLabel = CaptionLabel("等待加载数据", self.resultCard)
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setStyleSheet("color: #707070;")
        resultTitleLayout.addWidget(resultTitle)
        resultTitleLayout.addWidget(self.statusLabel)
        resultHeader.addLayout(resultTitleLayout, 1)

        self.exportBtn = PushButton("导出明细", self.resultCard)
        self.exportBtn.setIcon(":app/icons/Save.svg")
        self.exportBtn.setEnabled(False)
        self.exportBtn.clicked.connect(self._exportResults)
        resultHeader.addWidget(
            self.exportBtn,
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        resultLayout.addLayout(resultHeader)

        self.resultSegment = SegmentedWidget(self.resultCard)
        self.resultSegment.addItem("records", "偏误明细")
        self.resultSegment.addItem("count", "计数")
        self.resultSegment.addItem("chart", "图表")
        self.resultSegment.addItem("heatmap", "热力图")
        self.resultSegment.addItem("rules", "关联规则")
        self.resultSegment.setCurrentItem("records")
        self.resultSegment.currentItemChanged.connect(self._onResultTabChanged)
        resultLayout.addWidget(self.resultSegment)

        self.resultStack = QStackedWidget(self.resultCard)
        self.resultPages = {}
        self.resultEmptyStates = {}
        self._embeddedDialogs = {}

        detailPage = QWidget(self.resultStack)
        detailLayout = QVBoxLayout(detailPage)
        detailLayout.setContentsMargins(0, 0, 0, 0)
        self.detailStack = QStackedWidget(detailPage)
        self.detailEmptyState = BiasEmptyState(
            "等待分析",
            "选择 XLSX、TXT 或 Word 文件、分析字段和至少一种偏误类型后开始分析。",
            ":app/icons/Check.svg",
            self.detailStack,
        )
        self.detailStack.addWidget(self.detailEmptyState)

        self.tableWidget = TableView(self.detailStack)
        self.tableWidget.setMinimumHeight(420)
        self.tableModel = BiasResultTableModel(self.tableWidget)
        self.tableWidget.setModel(self.tableModel)
        self.tableWidget.setSortingEnabled(True)
        self.tableWidget.setSelectionBehavior(
            TableView.SelectionBehavior.SelectRows
        )
        self.tableWidget.setAlternatingRowColors(True)
        self.tableWidget.setEditTriggers(TableView.EditTrigger.NoEditTriggers)
        self.tableWidget.verticalHeader().setVisible(False)
        self.tableWidget.setAccessibleName("偏误分析明细表")

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
        self.detailStack.addWidget(self.tableWidget)
        self.detailStack.setCurrentWidget(self.detailEmptyState)
        detailLayout.addWidget(self.detailStack)
        self.resultPages["records"] = detailPage
        self.resultStack.addWidget(detailPage)

        emptyDescriptions = {
            "count": "完成分析后，这里会按偏误类型汇总计数和占比。",
            "chart": "完成分析后，可在饼图和条形图之间切换。",
            "heatmap": "完成分析并配置等级或国籍列后，可查看交叉分布。",
            "rules": "至少需要 10 个句子事务且包含两种偏误类型,才能进行探索性关联统计。",
        }
        for key in ("count", "chart", "heatmap", "rules"):
            page = QWidget(self.resultStack)
            pageLayout = QVBoxLayout(page)
            pageLayout.setContentsMargins(0, 0, 0, 0)
            emptyState = BiasEmptyState(
                "暂无结果",
                emptyDescriptions[key],
                parent=page,
            )
            pageLayout.addWidget(emptyState)
            self.resultPages[key] = page
            self.resultEmptyStates[key] = emptyState
            self.resultStack.addWidget(page)

        resultLayout.addWidget(self.resultStack, 1)
        self.workspaceLayout.addWidget(self.resultCard, 1)
        scrollLayout.addLayout(self.workspaceLayout, 1)

        self.scrollArea.setWidget(self.scrollContent)

        mainLayout = QVBoxLayout(self)
        mainLayout.setContentsMargins(0, 0, 0, 0)
        mainLayout.addWidget(self.scrollArea)

        # 输入框互斥逻辑
        self.charLineEdit.textChanged.connect(lambda t: self._onInputMutual(t, "char"))
        self.wordLineEdit.textChanged.connect(lambda t: self._onInputMutual(t, "word"))
        self._configureFocusChain()
        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)
        self._applyResponsiveLayout(self.width())

    def _configureFocusChain(self) -> None:
        focusChain = [
            self.chooseFileBtn,
            self.columnCombobox,
            self.columnConfigBtn,
            *self.filterSegment.items.values(),
            self.charLineEdit,
            self.charFilter.checkAllBtn,
            *self.charFilter.checkboxes.values(),
            self.wordLineEdit,
            self.wordFilter.checkAllBtn,
            *self.wordFilter.checkboxes.values(),
            self.sentFilter.checkAllBtn,
            *self.sentFilter.checkboxes.values(),
            self.analyzeBtn,
            self.exportBtn,
            *self.resultSegment.items.values(),
            self.tableWidget,
        ]
        for currentWidget, nextWidget in zip(focusChain, focusChain[1:]):
            QWidget.setTabOrder(currentWidget, nextWidget)

    def _applyTheme(self, *_args) -> None:
        dark = isDarkTheme()
        palette = shellPalette()
        surface = "#2B2B2B" if dark else "#FFFFFF"
        border = "#454545" if dark else "#DCE4EF"
        muted = "#B8B8B8" if dark else "#616161"
        warning = "#F2C97D" if dark else "#8A4B00"
        cardStyle = (
            "QWidget#{objectName} {{"
            f" background-color: {surface};"
            f" border: 1px solid {border};"
            " border-radius: 12px;"
            "}}"
        )
        self.conditionCard.setStyleSheet(
            cardStyle.format(objectName="biasConditionCard")
        )
        self.resultCard.setStyleSheet(
            cardStyle.format(objectName="biasResultCard")
        )
        for label in (
            self.pageDescription,
            self.sourceStatusLabel,
            self.columnLabel,
            self.selectionSummaryLabel,
            self.statusLabel,
        ):
            label.setStyleSheet(f"color: {muted};")
        self.filterTitle.setStyleSheet(
            f"color: {palette.text.name()}; font-weight: 600;"
        )
        self.columnCompatibilityLabel.setStyleSheet(f"color: {warning};")
        for filterWidget in (self.charFilter, self.wordFilter, self.sentFilter):
            filterWidget.applyTheme(palette)
        for emptyState in self.findChildren(BiasEmptyState):
            emptyState.descriptionLabel.setStyleSheet(f"color: {muted};")

    def _onFilterPageChanged(self, key: str) -> None:
        pageIndex = {
            "character": 0,
            "word": 1,
            "sentence": 2,
        }.get(key, 0)
        self.filterStack.setCurrentIndex(pageIndex)

    def _applyResponsiveLayout(self, width: int) -> None:
        """根据可用宽度切换左右/上下工作区，避免整页横向溢出。"""
        isCompact = width < 1040
        targetDirection = (
            QBoxLayout.Direction.TopToBottom
            if isCompact
            else QBoxLayout.Direction.LeftToRight
        )
        if self.workspaceLayout.direction() != targetDirection:
            self.workspaceLayout.setDirection(targetDirection)

        if isCompact:
            self.conditionCard.setMinimumWidth(0)
            self.conditionCard.setMaximumWidth(16777215)
            self.resultCard.setMinimumHeight(560)
        else:
            self.conditionCard.setFixedWidth(340)
            self.resultCard.setMinimumHeight(620)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._applyResponsiveLayout(event.size().width())

    def _isMultiFileMode(self) -> bool:
        """根据当前数据源数量自动判断是否为多文件模式。"""
        return max(
            len(self.filesList),
            len(self.dfs),
            self._pendingFileCount,
        ) > 1

    def _refreshAnalyzeState(self) -> None:
        """同步分析按钮与多文件字段兼容性提示。"""
        selectedTypes = (
            self.charFilter.selectedTexts()
            + self.wordFilter.selectedTexts()
            + self.sentFilter.selectedTexts()
        )
        missingTextFiles = []
        missingGroupFiles = []

        if self.selectedColumn:
            missingTextFiles = [
                os.path.basename(filePath)
                for filePath, dataFrame in self.dfs.items()
                if self.selectedColumn not in dataFrame.columns
            ]

        groupColumns = [
            columnName
            for columnName in (self.levelColumn, self.countryColumn)
            if columnName
        ]
        if groupColumns:
            missingGroupFiles = [
                os.path.basename(filePath)
                for filePath, dataFrame in self.dfs.items()
                if any(
                    columnName not in dataFrame.columns
                    for columnName in groupColumns
                )
            ]

        hasNoCommonColumns = bool(
            self.dfs
            and self._isMultiFileMode()
            and self.columnCombobox.count() == 0
        )
        if hasNoCommonColumns:
            self.columnCompatibilityLabel.setText(
                "所选文件没有共同字段，请更换文件或使用单文件模式"
            )
            self.columnCompatibilityLabel.show()
        elif missingTextFiles:
            self.columnCompatibilityLabel.setText(
                f"{len(missingTextFiles)} 个文件缺少“{self.selectedColumn}”，"
                "请重新选择分析字段"
            )
            self.columnCompatibilityLabel.show()
        elif missingGroupFiles:
            self.columnCompatibilityLabel.setText(
                f"{len(missingGroupFiles)} 个文件缺少分组字段，结果将记为“未知”"
            )
            self.columnCompatibilityLabel.show()
        else:
            self.columnCompatibilityLabel.clear()
            self.columnCompatibilityLabel.hide()

        isLoading = self.loadThread is not None
        isReady = bool(
            self.dfs
            and self.selectedColumn
            and selectedTypes
            and not missingTextFiles
            and not isLoading
            and not self._isAnalysisRunning
        )
        self.analyzeBtn.setEnabled(isReady)

    def _onResultTabChanged(self, key: str) -> None:
        pageOrder = ["records", "count", "chart", "heatmap", "rules"]
        if key not in pageOrder:
            return
        self.resultStack.setCurrentWidget(self.resultPages[key])
        if key != "records":
            self._prepareEmbeddedResult(key)

    def _selectResultTab(self, key: str) -> None:
        self.resultSegment.setCurrentItem(key)
        self._onResultTabChanged(key)

    def _clearPageLayout(self, page: QWidget) -> None:
        layout = page.layout()
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    @staticmethod
    def _clearDropShadows(rootWidget: QWidget) -> None:
        """移除结果内容中的投影，保留其他图形效果。"""
        for widget in (rootWidget, *rootWidget.findChildren(QWidget)):
            if isinstance(widget.graphicsEffect(), QGraphicsDropShadowEffect):
                widget.setGraphicsEffect(None)

    def _disposeEmbeddedResult(self, key: str) -> None:
        dialog = self._embeddedDialogs.pop(key, None)
        if dialog is None:
            return

        worker = getattr(dialog, "_workerThread", None)
        if worker is not None and worker.isRunning():
            try:
                worker.cancel()
                worker.wait(1500)
            except Exception:
                pass

        contentWidget = getattr(dialog, "widget", None)
        if contentWidget is not None:
            contentWidget.hide()
            contentWidget.setParent(None)
            contentWidget.deleteLater()
        dialog.deleteLater()

    def _resetEmbeddedResults(self) -> None:
        for key in list(self._embeddedDialogs):
            self._disposeEmbeddedResult(key)

        descriptions = {
            "count": "完成分析后，这里会按偏误类型汇总计数和占比。",
            "chart": "完成分析后，可在饼图和条形图之间切换。",
            "heatmap": "完成分析并配置等级或国籍列后，可查看交叉分布。",
            "rules": "至少需要 10 个句子事务且包含两种偏误类型,才能进行探索性关联统计。",
        }
        for key, description in descriptions.items():
            self._showResultMessage(key, "暂无结果", description)

    def _showResultMessage(self, key: str, title: str, description: str) -> None:
        page = self.resultPages[key]
        self._clearPageLayout(page)
        emptyState = BiasEmptyState(title, description, parent=page)
        page.layout().addWidget(emptyState)
        self.resultEmptyStates[key] = emptyState

    def _mountDialogContent(self, key: str, dialog: MessageBoxBase) -> None:
        """将原结果视图装入分页容器，不再执行模态弹窗。"""
        self._disposeEmbeddedResult(key)
        page = self.resultPages[key]
        self._clearPageLayout(page)

        dialog._isEmbedded = True
        contentWidget = dialog.widget
        contentWidget.setParent(page)
        self._clearDropShadows(contentWidget)
        contentWidget.setMinimumSize(0, 0)
        contentWidget.setMaximumSize(16777215, 16777215)
        contentWidget.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )
        for closeButton in contentWidget.findChildren(TransparentToggleToolButton):
            closeButton.hide()

        page.layout().addWidget(contentWidget)
        contentWidget.show()
        page.layout().invalidate()
        page.updateGeometry()
        self.resultStack.updateGeometry()
        self._embeddedDialogs[key] = dialog

    def _prepareEmbeddedResult(self, key: str) -> None:
        if key in self._embeddedDialogs:
            return
        if not self.totalCounts:
            self._showResultMessage(
                key,
                "等待分析",
                "先在左侧完成条件设置并运行分析，再查看此结果。",
            )
            return

        if key == "count":
            self._mountDialogContent(
                key,
                CountResultDialog(self.totalCounts, self.window()),
            )
            return

        if key == "chart":
            self._mountDialogContent(
                key,
                ChartDialog(self.totalCounts, self.window()),
            )
            return

        if key == "heatmap":
            self._prepareHeatmapResult()
            return

        if key == "rules":
            self._prepareAssociationResult()

    def _prepareHeatmapResult(self) -> None:
        if not self.heatmapData:
            self._showResultMessage(
                "heatmap",
                "暂无热力图数据",
                "热力图需要 Excel 中的等级或国籍字段；TXT 与 Word 文档仍可进行偏误明细、计数和关联分析。",
            )
            return

        selectedTypes = list(self.totalCounts.keys())
        levelGroups = []
        countryGroups = []
        for groupValue in self.heatmapGroups:
            if self._looksLikeLevel(groupValue):
                levelGroups.append(groupValue)
            else:
                countryGroups.append(groupValue)
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
        self._mountDialogContent("heatmap", dialog)

    def _prepareAssociationResult(self) -> None:
        transactions = list(self.associationTransactions)
        if not transactions:
            rowToTypes = {}
            for record in self.currentRecords:
                if len(record) < 4:
                    continue
                rowKey = (record[0], record[1])
                rowToTypes.setdefault(rowKey, set()).add(record[3])
            transactions = [sorted(types) for types in rowToTypes.values()]

        transactionCount = len(transactions)
        uniqueTypes = (
            set().union(*(set(items) for items in transactions))
            if transactions
            else set()
        )
        if transactionCount < 10 or len(uniqueTypes) < 2:
            self._showResultMessage(
                "rules",
                "样本数量不足",
                "为避免极小样本直接生成规则，本工具最低要求 10 个有效句子事务，"
                "并且至少有两种偏误类型；达到最低值也不代表样本已经充分。",
            )
            return

        minSupport = max(0.01, min(0.10, round(3 / transactionCount, 2)))

        logger.info(
            f"[Bias] 启动句子级关联统计: 事务数={transactionCount}, "
            f"偏误类型数={len(uniqueTypes)}, 支持度阈值={minSupport}, "
            "最小共现次数=3, Holm alpha=0.05"
        )
        dialog = AssociationRulesDialog(
            transactions,
            minSupport,
            0.5,
            self.window(),
        )
        self._mountDialogContent("rules", dialog)

    def _onChooseFile(self):
        """选择一个或多个文件，并根据数量自动确定处理模式。"""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择一个或多个偏误分析文件",
            "",
            "支持的文件 (*.xlsx *.txt *.docx *.doc);;"
            "Excel (*.xlsx);;TXT 文本 (*.txt);;Word 文档 (*.docx *.doc)",
        )
        if not files:
            return

        uniqueFiles = list(dict.fromkeys(files))
        self._clearAllData()
        self._startLoading(uniqueFiles)

    def _startLoading(self, filePaths):
        """开始加载文件"""
        if self.loadThread and self.loadThread.isRunning():
            return

        self.chooseFileBtn.setEnabled(False)
        self._pendingFileCount = len(filePaths)
        self._failedFileCount = 0
        self.sourceStatusLabel.setText(f"正在加载 {len(filePaths)} 个文件…")
        self.statusLabel.setText("正在读取数据，请稍候")

        self.loadThread = FileLoaderThread(filePaths)
        self._refreshAnalyzeState()
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
        totalRowsLoaded = sum(len(dataFrame) for dataFrame in self.dfs.values())
        modeLabel = "多文件" if self._isMultiFileMode() else "单文件"
        self.sourceStatusLabel.setText(
            f"{modeLabel} · {len(self.filesList)} 个文件 · {totalRowsLoaded:,} 条文本"
        )

    def _onError(self, errMsg: str):
        self._failedFileCount += 1
        InfoBar.error(
            "文件读取失败",
            errMsg,
            Qt.Orientation.Horizontal,
            True,
            3000,
            InfoBarPosition.TOP_RIGHT,
            self,
        )

    def _onFinished(self):
        self.loadThread = None
        self._pendingFileCount = 0
        self.chooseFileBtn.setEnabled(True)
        self._refreshAnalyzeState()

        if not self.filesList:
            self.statusLabel.setText("没有成功加载文件，请检查格式或文件内容")
            self.sourceStatusLabel.setText("未加载成功 · 支持 XLSX、TXT、DOCX、DOC")
            self.chooseFileBtn.setText("选择文件")
            return

        self.statusLabel.setText("数据已就绪，请选择偏误类型后开始分析")
        self.chooseFileBtn.setText("重新选择")
        modeLabel = "多文件" if self._isMultiFileMode() else "单文件"
        failureText = (
            f" · {self._failedFileCount} 个失败" if self._failedFileCount else ""
        )
        InfoBar.success(
            "加载成功",
            f"已自动识别为{modeLabel}模式 · {len(self.filesList)} 个文件{failureText}",
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
            self.columnConfigBtn.setEnabled(False)
            self._refreshAnalyzeState()
            return

        hasExcelSource = any(
            dataFrame.attrs.get("sourceKind") == "excel"
            for dataFrame in self.dfs.values()
        )

        if self._isMultiFileMode():
            columnSets = [set(df.columns) for df in self.dfs.values()]
            commonColumns = set.intersection(*columnSets) if columnSets else set()
            commonColumns = sorted(commonColumns)

            if not commonColumns:
                self.columnCombobox.clear()
                self.columnCombobox.setEnabled(False)
                self.columnConfigBtn.setEnabled(False)
                self.columnCompatibilityLabel.setText(
                    "所选文件没有共同字段，请更换文件或使用单文件模式"
                )
                self.columnCompatibilityLabel.show()
                self._refreshAnalyzeState()
                return

            self.columnCombobox.clear()
            self.columnCombobox.addItems(commonColumns)
            self.columnCombobox.setEnabled(True)
            self.columnConfigBtn.setEnabled(hasExcelSource)
        else:
            if self.filesList:
                lastFile = self.filesList[-1]
                if lastFile in self.dfs:
                    columns = list(self.dfs[lastFile].columns)
                    self.columnCombobox.clear()
                    self.columnCombobox.addItems(columns)
                    self.columnCombobox.setEnabled(True)
                    self.columnConfigBtn.setEnabled(hasExcelSource)

        if self.columnCombobox.findText(BIAS_TEXT_COLUMN) >= 0:
            self.columnCombobox.setCurrentText(BIAS_TEXT_COLUMN)
        self.selectedColumn = self.columnCombobox.currentText() or None

        # 自动识别等级/国籍列（用于热力图）
        self._detectGroupColumns()
        self._refreshAnalyzeState()

    def _detectGroupColumns(self):
        """自动识别等级列与国籍列"""
        self.levelColumn = None
        self.countryColumn = None

        if not self.dfs:
            return

        # 多文件模式只能使用所有文件共有的分组列，避免从首个文件识别出的
        # 列名被直接用于其他结构不同的 DataFrame。
        firstDf = next(iter(self.dfs.values()))
        firstColumns = list(firstDf.columns)
        if self._isMultiFileMode():
            commonColumns = set(firstColumns)
            for dataFrame in self.dfs.values():
                commonColumns.intersection_update(dataFrame.columns)
            columns = [column for column in firstColumns if column in commonColumns]
        elif self.filesList and self.filesList[-1] in self.dfs:
            columns = list(self.dfs[self.filesList[-1]].columns)
        else:
            columns = firstColumns

        levelKeywords = ["level", "hsk", "等级", "级别", "水准"]
        countryKeywords = ["country", "nationality", "国籍", "国家", "nation"]

        for col in columns:
            colLower = str(col).lower()
            if self.levelColumn is None and any(kw in colLower for kw in levelKeywords):
                self.levelColumn = col
            if self.countryColumn is None and any(
                kw in colLower for kw in countryKeywords
            ):
                self.countryColumn = col

        # 手动配置优先：若用户已指定，则覆盖自动识别结果
        if self.manualLevelColumn and self.manualLevelColumn in columns:
            self.levelColumn = self.manualLevelColumn
        if self.manualCountryColumn and self.manualCountryColumn in columns:
            self.countryColumn = self.manualCountryColumn

        logger.info(
            f"[Bias] 分组列: 等级={self.levelColumn}, 国籍={self.countryColumn}"
        )

    def _openColumnConfig(self):
        """打开列配置弹窗"""
        if not self.dfs:
            InfoBar.warning(
                "提示",
                "请先加载文件",
                Qt.Orientation.Horizontal,
                True,
                2000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        if not any(
            dataFrame.attrs.get("sourceKind") == "excel"
            for dataFrame in self.dfs.values()
        ):
            InfoBar.info(
                "无需配置",
                "TXT 与 Word 文档只有“文本”字段，不包含等级或国籍元数据",
                Qt.Orientation.Horizontal,
                True,
                3000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
            return

        # 收集所有列（多文件模式取交集，单文件取当前文件列）
        if self._isMultiFileMode():
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
            self._refreshAnalyzeState()
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

    def _onColumnChanged(self):
        self.selectedColumn = self.columnCombobox.currentText()
        self._refreshAnalyzeState()

    def _onFilterChanged(self):
        """筛选条件改变"""
        charSelected = self.charFilter.selectedTexts()
        wordSelected = self.wordFilter.selectedTexts()
        sentenceSelected = self.sentFilter.selectedTexts()

        # 控制输入框可用性
        self.charLineEdit.setEnabled(True)
        self.wordLineEdit.setEnabled(True)

        if "无法识别 [#]" in charSelected:
            self.charLineEdit.setEnabled(False)
        if "离合词错误 [CLH]" in wordSelected or "存疑词 [CY]" in wordSelected:
            self.wordLineEdit.setEnabled(False)

        selectedCount = len(charSelected) + len(wordSelected) + len(sentenceSelected)
        if selectedCount:
            self.selectionSummaryLabel.setText(
                f"已选 {selectedCount} 种"
            )
        else:
            self.selectionSummaryLabel.setText("未选择")
        self._refreshAnalyzeState()

    def _onInputMutual(self, text: str, inputType: str):
        """输入框互斥逻辑"""
        if text:
            if inputType == "char":
                self.wordLineEdit.clear()
            else:
                self.charLineEdit.clear()

    def _clearAllData(self):
        """清空所有数据"""
        matchingWorker = getattr(self, "_matchingWorker", None)
        if matchingWorker is not None:
            try:
                matchingWorker.cancel()
                if matchingWorker.isRunning():
                    matchingWorker.wait(1500)
                matchingWorker.deleteLater()
            except RuntimeError:
                pass
            self._matchingWorker = None
        self._isAnalysisRunning = False

        self.filesList = []
        self.dfs = {}
        self._pendingFileCount = 0
        self._failedFileCount = 0
        self.selectedColumn = None
        self.levelColumn = None
        self.countryColumn = None
        self.currentRecords = []
        self.associationTransactions = []
        self.heatmapData = {}
        self.heatmapGroups = []
        self.typeCounts = {
            **{name: 0 for name in CHARACTERS_TYPES},
            **{name: 0 for name in SENTENCES_TYPES},
            **{name: 0 for name in WORDS_TYPES},
        }
        self.totalCounts = None
        self.tableModel.clear()
        self.detailStack.setCurrentWidget(self.detailEmptyState)
        self.detailEmptyState.setContent(
            "等待分析",
            "选择 XLSX、TXT 或 Word 文件、分析字段和至少一种偏误类型后开始分析。",
        )
        self.columnCombobox.clear()
        self.columnCombobox.setEnabled(False)
        self.columnConfigBtn.setEnabled(False)
        self.chooseFileBtn.setText("选择文件")
        self.sourceStatusLabel.setText("尚未加载文件 · 支持 XLSX、TXT、DOCX、DOC")
        self.statusLabel.setText("等待加载数据")
        self.exportBtn.setEnabled(False)
        self.analyzeBtn.setText("开始分析")
        self._resetEmbeddedResults()
        self._refreshAnalyzeState()

    def _runMatching(self):
        """执行匹配分析（P1-fix）

        重构：
        - 正则在启动时一次性 re.compile，避免内层循环反复编译
        - 把 100MB×25 类型×10 万行的匹配移到 MatchingWorker(QThread)，
          防止主线程冻结
        - 主线程仅负责数据收集 + UI 渲染
        """
        if self._isAnalysisRunning:
            return

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

        missingTextFiles = [
            os.path.basename(filePath)
            for filePath, dataFrame in self.dfs.items()
            if self.selectedColumn not in dataFrame.columns
        ]
        if missingTextFiles:
            self._refreshAnalyzeState()
            InfoBar.error(
                "字段不一致",
                f"有 {len(missingTextFiles)} 个文件缺少“{self.selectedColumn}”，"
                "请重新选择分析字段",
                Qt.Orientation.Horizontal,
                True,
                3500,
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
        self.associationTransactions = []
        self.heatmapData = {}
        self.heatmapGroups = []
        self.tableModel.clear()
        self.totalCounts = None
        self.exportBtn.setEnabled(False)
        self._resetEmbeddedResults()
        self.detailEmptyState.setContent(
            "正在分析",
            "正在匹配偏误标记并整理结果，请稍候。",
        )
        self.detailStack.setCurrentWidget(self.detailEmptyState)
        self.statusLabel.setText("正在准备分析任务…")
        self._isAnalysisRunning = True
        self._refreshAnalyzeState()

        # P1-fix:预编译正则在主线程一次性完成,避免内层循环重复 re.compile
        compiledPatterns: List[Tuple[str, "re.Pattern[str]", bool]] = []
        for errorName in selectTypes:
            patternStr, hasContent = ERROR_TYPES[errorName]
            compiledPatterns.append((errorName, re.compile(patternStr), hasContent))

        # 把 DataFrame 行转成不可变 tuple list,工作线程不触碰 pandas
        rows: List[Tuple[str, int, str, str, str]] = []
        skippedFiles = []
        for filePath, df in self.dfs.items():
            if self.selectedColumn not in df.columns:
                continue

            fileName = os.path.basename(filePath)
            sourcePositions = df.attrs.get("sourcePositions", [])
            try:
                textSeries = df[self.selectedColumn].astype(str).fillna("")
                levelSeries = (
                    df[self.levelColumn].astype(str).fillna("")
                    if self.levelColumn and self.levelColumn in df.columns
                    else None
                )
                countrySeries = (
                    df[self.countryColumn].astype(str).fillna("")
                    if self.countryColumn and self.countryColumn in df.columns
                    else None
                )
            except Exception as error:
                skippedFiles.append(fileName)
                logger.exception(f"[Bias] 文件 {fileName} 读取分析列失败: {error}")
                continue

            missingGroupColumns = [
                columnName
                for columnName in (self.levelColumn, self.countryColumn)
                if columnName and columnName not in df.columns
            ]
            if missingGroupColumns:
                logger.warning(
                    f"[Bias] 文件 {fileName} 缺少分组列 "
                    f"{missingGroupColumns}，对应值按未知处理"
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

                sourcePosition = (
                    sourcePositions[idx]
                    if idx < len(sourcePositions)
                    else idx + (2 if df.attrs.get("sourceKind") == "excel" else 1)
                )
                rows.append(
                    (fileName, sourcePosition, text, rowLevel, rowCountry)
                )

        if skippedFiles:
            InfoBar.warning(
                "部分文件已跳过",
                f"{len(skippedFiles)} 个文件无法读取所选字段，其余文件继续分析",
                Qt.Orientation.Horizontal,
                True,
                3500,
                InfoBarPosition.TOP_RIGHT,
                self,
            )

        if not rows:
            self._isAnalysisRunning = False
            self._refreshAnalyzeState()
            self.statusLabel.setText("所选统计列中没有可分析的文本")
            self.detailEmptyState.setContent(
                "没有可分析文本",
                "请检查分析字段是否选择正确，或更换文件。",
            )
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
        self._isAnalysisRunning = False
        records = payload["records"]
        typeCounts = payload["typeCounts"]
        self.currentRecords = records
        self.associationTransactions = payload.get("transactions", [])
        self.heatmapData = payload["heatmapData"]
        self.heatmapGroups = payload["heatmapGroups"]

        # 同步 typeCounts（仅 update 已选类型）
        for name, count in typeCounts.items():
            self.typeCounts[name] = count

        selectTypes = getattr(self, "_matchingSelectTypes", [])
        self.totalCounts = {name: self.typeCounts[name] for name in selectTypes}
        self._resetEmbeddedResults()

        self.tableModel.setRecords(records)

        self.exportBtn.setEnabled(bool(records))
        self.analyzeBtn.setText("重新分析")
        self._refreshAnalyzeState()
        self._selectResultTab("records")
        if records:
            self.detailStack.setCurrentWidget(self.tableWidget)
            self.statusLabel.setText(
                f"共定位 {len(records):,} 条偏误记录 · {len(selectTypes)} 种类型"
            )
        else:
            self.detailEmptyState.setContent(
                "没有匹配结果",
                "当前条件下未发现偏误标记，可调整类型或指定字符/词语后重试。",
            )
            self.detailStack.setCurrentWidget(self.detailEmptyState)
            self.statusLabel.setText("分析完成，当前条件下没有匹配结果")
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
        self._isAnalysisRunning = False
        self._refreshAnalyzeState()
        self.statusLabel.setText(f"分析失败：{err}")
        self.detailEmptyState.setContent(
            "分析失败",
            "请检查数据格式和统计列后重试。",
        )
        self.detailStack.setCurrentWidget(self.detailEmptyState)
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

    @staticmethod
    def _stopOwnedWorker(owner, attributeName: str) -> None:
        worker = getattr(owner, attributeName, None)
        if worker is None:
            return
        try:
            if hasattr(worker, "cancel"):
                worker.cancel()
            if hasattr(worker, "isRunning") and worker.isRunning():
                if not worker.wait(2000):
                    logger.warning(
                        f"[Bias] 关闭时等待后台任务超时: {attributeName}"
                    )
                    worker.wait()
            if hasattr(worker, "deleteLater"):
                worker.deleteLater()
        except RuntimeError:
            pass
        setattr(owner, attributeName, None)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._isAnalysisRunning = False
        self._stopOwnedWorker(self, "loadThread")
        self._stopOwnedWorker(self, "_matchingWorker")
        for dialog in getattr(self, "_embeddedDialogs", {}).values():
            self._stopOwnedWorker(dialog, "_workerThread")
        super().closeEvent(event)

    def _runCount(self):
        """切换到计数结果页。"""
        self._selectResultTab("count")

    def _runChart(self):
        """切换到图表结果页。"""
        self._selectResultTab("chart")

    def _runHeatmap(self):
        """切换到偏误热力图结果页（FR-ERR-002）。"""
        self._selectResultTab("heatmap")

    def _runAssociationRules(self):
        """切换到偏误关联规则结果页（FR-ERR-003）。"""
        self._selectResultTab("rules")

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

        self.tableModel.setRecords(filtered)

        self.detailStack.setCurrentWidget(self.tableWidget)
        self._selectResultTab("records")
        self.statusLabel.setText(
            f"已下钻到 {errorName} × {groupVal} · {len(filtered)} 条记录"
        )

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

        transaction = beginPaidAnalysisExport(self.window(), "偏误分析结果 Excel")
        if transaction is None:
            return
        try:
            dfExport.to_excel(filePath, index=False, engine="openpyxl")
            if not transaction.commit():
                raise RuntimeError("导出文件已生成，但计费结算失败；本次费用已释放")
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
            transaction.refund()
            InfoBar.error(
                "导出失败",
                str(e),
                Qt.Orientation.Horizontal,
                True,
                3000,
                InfoBarPosition.TOP_RIGHT,
                self,
            )
