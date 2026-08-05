# coding: utf-8
"""账单流水表

修复(2026-08-05):
    1. 表头列宽没设 → 时间列被中文字符串挤到看不到秒
    2. 空态没提示 → 用户看到白屏以为程序卡死
    3. statusText dict 引用可能未导入 BillStatus → 保险起见显式 import
    4. CSV 导出字段没与表头对齐
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QFileDialog, QAbstractItemView,
    QHeaderView,
)
from qfluentwidgets import (
    TableWidget, StrongBodyLabel, CaptionLabel,
    PushButton, BodyLabel, FluentIcon as FIF,
    InfoBar, InfoBarPosition,
)

from app.core.models.billing_models import BillItem, BillStatus
from app.core.services.billing_service import getBillingService
from app.core.utils.signal_bus import signalBus


class BillTableWidget(QWidget):
    """账单流水表(支持按月导出 CSV + 空态提示)"""

    HEADERS = ["时间", "动作", "资源量", "预估(币)", "实际(币)", "扣后余额", "状态"]
    # 列宽权重(相对)
    COL_WEIGHTS = [2.5, 1.8, 1.2, 1.0, 1.0, 1.2, 1.0]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("BillTableRoot")
        self._userId: Optional[str] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # 工具栏
        toolbar = QHBoxLayout()
        self.titleLabel = StrongBodyLabel("账单流水", self)
        toolbar.addWidget(self.titleLabel)
        toolbar.addStretch(1)
        self.exportBtn = PushButton("导出 CSV", self, FIF.SAVE)
        self.exportBtn.clicked.connect(self._onExport)
        toolbar.addWidget(self.exportBtn)
        outer.addLayout(toolbar)

        # 表格 + 空态叠加
        tableContainer = QWidget(self)
        tableContainer.setMinimumHeight(220)
        tableLayout = QVBoxLayout(tableContainer)
        tableLayout.setContentsMargins(0, 0, 0, 0)

        self.table = TableWidget(tableContainer)
        self.table.setColumnCount(len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        # 列宽策略
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for i, w in enumerate(self.COL_WEIGHTS):
            header.setSectionResizeMode(i, QHeaderView.Stretch)
        tableLayout.addWidget(self.table)
        outer.addWidget(tableContainer)

        # 空态(浮在表格上)
        self.emptyHint = QWidget(self)
        self.emptyHint.setAttribute(Qt.WA_TransparentForMouseEvents)
        eh = QVBoxLayout(self.emptyHint)
        eh.setAlignment(Qt.AlignCenter)
        eh.setContentsMargins(0, 0, 0, 0)
        self.emptyIcon = CaptionLabel("暂无账单记录", self.emptyHint)
        self.emptyIcon.setAlignment(Qt.AlignCenter)
        self.emptyIcon.setStyleSheet("font-size: 14px; color: #999; padding: 24px;")
        eh.addWidget(self.emptyIcon)
        self.emptyHint.hide()

        signalBus.balanceChanged.connect(lambda *_: self.refresh())
        signalBus.billCreated.connect(lambda *_: self.refresh())

    # ---------- 公开 API ----------
    def setUserId(self, userId: str) -> None:
        self._userId = userId
        self.refresh()

    def refresh(self) -> None:
        if not self._userId:
            self.table.setRowCount(0)
            self.emptyHint.show()
            return
        bills = getBillingService().listBills(self._userId, limit=200)
        self.table.setRowCount(len(bills))
        if not bills:
            self.emptyHint.show()
        else:
            self.emptyHint.hide()
        for row, bill in enumerate(bills):
            self._fillRow(row, bill)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 让 emptyHint 始终覆盖 table 区域
        if self.table.parent() is not None:
            self.emptyHint.setGeometry(self.table.geometry())

    # ---------- 内部 ----------
    def _fillRow(self, row: int, bill: BillItem) -> None:
        from qfluentwidgets import TableWidgetItem

        statusText = {
            BillStatus.PENDING: "执行中",
            BillStatus.SETTLED: "已完成",
            BillStatus.REFUNDED: "已退款",
            BillStatus.FAILED: "失败",
        }.get(bill.status, bill.status.value)

        items = [
            bill.createdAt.strftime("%Y-%m-%d %H:%M"),
            bill.actionDisplayName or bill.actionType.value,
            f"{bill.resourceUsed:,}",
            f"{bill.estimatedCost}",
            f"{bill.realCost}",
            f"{bill.balanceAfter:,}",
            statusText,
        ]
        for col, text in enumerate(items):
            item = TableWidgetItem(text)
            if col == 0:
                item.setTextAlignment(Qt.AlignCenter)
            elif col >= 2:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            else:
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.table.setItem(row, col, item)

    # ---------- 导出 ----------
    def _onExport(self) -> None:
        if not self._userId:
            return
        bills = getBillingService().listBills(self._userId, limit=2000)
        defaultName = f"bills_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出账单", defaultName, "CSV (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(self.HEADERS)
                for b in bills:
                    writer.writerow([
                        b.createdAt.strftime("%Y-%m-%d %H:%M:%S"),
                        b.actionDisplayName or b.actionType.value,
                        b.resourceUsed,
                        b.estimatedCost,
                        b.realCost,
                        b.balanceAfter,
                        b.status.value,
                    ])
            InfoBar.success(
                title="导出成功",
                content=f"已写入 {len(bills)} 条记录到 {path}",
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )
        except Exception as e:
            InfoBar.error(
                title="导出失败",
                content=str(e),
                parent=self,
                duration=3500,
                position=InfoBarPosition.TOP,
            )