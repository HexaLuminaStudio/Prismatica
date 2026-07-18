# coding: utf-8
"""
统一结果显示组件

目标:
    - 4 个语料分析面板(词频 / 语境 / 情感 / 共现网络)的底部结果区
      使用统一的视觉规范,提升可读性和信息密度
    - 大尺寸指标卡(MetricCard):醒目的数字 + 标签 + 可选颜色
    - 结果卡(ResultSummary):横向排列多个 MetricCard + 详情描述

设计:
    - 4 列表格自适应宽度,过长时自动滚动
    - 数字字号 24-28px,加粗,色彩与状态关联(绿/红/灰/蓝)
    - 标签字号 11px,灰色,辅助描述
    - 占位/空状态统一为灰色提示
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CardWidget

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from loguru import logger


# ---------------------------------------------------------------------------
# 配色
# ---------------------------------------------------------------------------
class MetricColor(Enum):
    PRIMARY = ("#1890ff", "#e6f7ff")  # 蓝(主)
    SUCCESS = ("#52c41a", "#f6ffed")  # 绿(成功)
    WARNING = ("#faad14", "#fffbe6")  # 橙(警告)
    ERROR = ("#f5222d", "#fff1f0")  # 红(错误)
    NEUTRAL = ("#666666", "#fafafa")  # 灰(中性)
    ACCENT = ("#722ed1", "#f9f0ff")  # 紫(强调)


# ---------------------------------------------------------------------------
# MetricCard — 单个指标卡
# ---------------------------------------------------------------------------
class MetricCard(QFrame):
    """单个指标卡片

    显示一个大数字 + 一个标签;支持配色和占位状态。
    """

    def __init__(
        self,
        label: str,
        value: str = "—",
        color: MetricColor = MetricColor.NEUTRAL,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(86)

        self._labelText = label
        self._valueText = value
        self._color = color

        self._initUi()

    def _initUi(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        # 数字(大)
        self.valueLabel = QLabel(self._valueText, self)
        font = self.valueLabel.font()
        font.setPointSize(20)
        font.setBold(True)
        self.valueLabel.setFont(font)
        self.valueLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.valueLabel)

        # 标签(小)
        self.textLabel = QLabel(self._labelText, self)
        self.textLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.textLabel.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.textLabel)

        self._applyColor()

    def _applyColor(self):
        """根据 _color 应用配色"""
        fg, bg = self._color.value
        self.setStyleSheet(
            f"QFrame#MetricCard {{"
            f"  background: {bg};"
            f"  border: 1px solid #e8e8e8;"
            f"  border-radius: 8px;"
            f"}}"
        )
        self.valueLabel.setStyleSheet(
            f"color: {fg}; font-size: 22px; font-weight: 700;"
        )

    def setValue(self, value: str, color: Optional[MetricColor] = None):
        """更新数值;可同时更新颜色"""
        self._valueText = value
        self.valueLabel.setText(value)
        if color is not None:
            self._color = color
            self._applyColor()

    def setLabel(self, label: str):
        self._labelText = label
        self.textLabel.setText(label)

    def setPlaceholder(self, text: str = "—"):
        """设为占位状态(灰色)"""
        self._valueText = text
        self.valueLabel.setText(text)
        self._color = MetricColor.NEUTRAL
        self._applyColor()


# ---------------------------------------------------------------------------
# ResultSummary — 多指标结果汇总卡
# ---------------------------------------------------------------------------
class ResultSummary(CardWidget):
    """结果汇总卡片

    横向排列 2-4 个 MetricCard,加上一个可选的详情描述行。
    支持自适应宽度:超过 4 个时换行(2 行布局)。

    用法:
        summary = ResultSummary(parent)
        summary.setMetrics([
            ("命中数", "1,234", MetricColor.PRIMARY),
            ("覆盖率", "78.5%", MetricColor.SUCCESS),
            ("Top 词", "学习", MetricColor.NEUTRAL),
        ])
        summary.setDetail("检索词: 学习 | 范围: 全部语料 | 耗时: 0.2s")
        summary.setPlaceholder("请先加载语料并点击「开始分析」")
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("ResultSummary")
        self._initUi()

    def _initUi(self):
        self._outerLayout = QVBoxLayout(self)
        self._outerLayout.setContentsMargins(16, 14, 16, 14)
        self._outerLayout.setSpacing(10)

        # 标题
        self._titleLabel = QLabel("分析结果", self)
        titleFont = self._titleLabel.font()
        titleFont.setBold(True)
        titleFont.setPointSize(13)
        self._titleLabel.setFont(titleFont)
        self._outerLayout.addWidget(self._titleLabel)

        # 指标区(横向网格)
        self._metricsWidget = QWidget(self)
        self._metricsLayout = QHBoxLayout(self._metricsWidget)
        self._metricsLayout.setContentsMargins(0, 0, 0, 0)
        self._metricsLayout.setSpacing(10)
        self._outerLayout.addWidget(self._metricsWidget)

        # 占位状态
        self._placeholderLabel = QLabel("", self)
        self._placeholderLabel.setStyleSheet("color: #999; font-size: 12px;")
        self._placeholderLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholderLabel.setMinimumHeight(56)
        self._placeholderLabel.hide()
        self._outerLayout.addWidget(self._placeholderLabel)

        # 详情描述(可选)
        self._detailLabel = QLabel("", self)
        self._detailLabel.setStyleSheet(
            "color: #555; font-size: 12px; line-height: 1.6;"
        )
        self._detailLabel.setWordWrap(True)
        self._detailLabel.hide()
        self._outerLayout.addWidget(self._detailLabel)

        # 高亮条目(Top 词等)
        self._topWordsLabel = QLabel("", self)
        self._topWordsLabel.setStyleSheet(
            "color: #444; font-size: 12px; padding-top: 4px;"
        )
        self._topWordsLabel.setWordWrap(True)
        self._topWordsLabel.hide()
        self._outerLayout.addWidget(self._topWordsLabel)

    def setTitle(self, title: str):
        self._titleLabel.setText(title)

    def setMetrics(self, metrics: List[Tuple[str, str, MetricColor]]):
        """设置指标列表

        Args:
            metrics: [(label, value, color), ...]
        """
        # 清空旧的
        while self._metricsLayout.count():
            item = self._metricsLayout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not metrics:
            return

        for i, (label, value, color) in enumerate(metrics):
            card = MetricCard(label, value, color, self._metricsWidget)
            self._metricsLayout.addWidget(card, 1)

        # 占位状态时,指标区隐藏
        self._metricsWidget.show()

    def setDetail(self, text: str):
        """设置详情描述行(支持 HTML)"""
        if not text:
            self._detailLabel.hide()
            return
        self._detailLabel.setText(text)
        self._detailLabel.setTextFormat(Qt.TextFormat.RichText)
        self._detailLabel.show()

    def setTopWords(
        self,
        positive: Optional[List[Tuple[str, int]]] = None,
        negative: Optional[List[Tuple[str, int]]] = None,
    ):
        """设置 Top 词条(情感分析用,支持彩色)

        Args:
            positive: [(word, count), ...]
            negative: [(word, count), ...]
        """
        if not positive and not negative:
            self._topWordsLabel.hide()
            return

        parts = []
        if positive:
            posStr = ", ".join(f"{w}({c})" for w, c in positive)
            parts.append(f'<span style="color:#52c41a;">正面:</span> {posStr}')
        if negative:
            negStr = ", ".join(f"{w}({c})" for w, c in negative)
            parts.append(f'<span style="color:#f5222d;">负面:</span> {negStr}')

        self._topWordsLabel.setText("&nbsp;&nbsp;|&nbsp;&nbsp;".join(parts))
        self._topWordsLabel.setTextFormat(Qt.TextFormat.RichText)
        self._topWordsLabel.show()

    def setPlaceholder(self, text: str):
        """设为占位状态(隐藏指标,显示提示文字)"""
        self._metricsWidget.hide()
        self._topWordsLabel.hide()
        self._placeholderLabel.setText(text)
        self._placeholderLabel.show()

    def clear(self):
        """重置为初始状态"""
        self._placeholderLabel.setText("")
        self._placeholderLabel.hide()
        self._detailLabel.setText("")
        self._detailLabel.hide()
        self._topWordsLabel.setText("")
        self._topWordsLabel.hide()
        while self._metricsLayout.count():
            item = self._metricsLayout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
