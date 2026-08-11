# coding: utf-8
"""偏误与 KWIC 结果表的轻量 Qt 模型。"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont
from qfluentwidgets import isDarkTheme, qconfig


class BiasResultTableModel(QAbstractTableModel):
    """按需向视图提供偏误记录，避免为全量结果创建单元格对象。"""

    HEADERS = ("文件", "行号", "句子", "偏误类型", "标记内容", "等级", "国籍")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._records: list[tuple[Any, ...]] = []
        self._sortColumn = -1
        self._sortOrder = Qt.SortOrder.AscendingOrder

    def setRecords(self, records: Iterable[Sequence[Any]]) -> None:
        self.beginResetModel()
        self._records = [tuple(record) for record in records]
        if self._sortColumn >= 0:
            self._records.sort(
                key=self._sortKey,
                reverse=self._sortOrder == Qt.SortOrder.DescendingOrder,
            )
        self.endResetModel()

    def clear(self) -> None:
        self.setRecords(())

    def recordAt(self, rowIndex: int) -> Optional[tuple[Any, ...]]:
        if 0 <= rowIndex < len(self._records):
            return self._records[rowIndex]
        return None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        rowIndex = index.row()
        columnIndex = index.column()
        if not (0 <= rowIndex < len(self._records)):
            return None
        record = self._records[rowIndex]
        value = record[columnIndex] if columnIndex < len(record) else ""
        text = "" if value is None else str(value)

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return text
        if role == Qt.ItemDataRole.ToolTipRole and len(text) > 48:
            return text
        if role == Qt.ItemDataRole.TextAlignmentRole and columnIndex == 1:
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.UserRole:
            return value
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
            return None
        return str(section + 1)

    def sort(
        self,
        column: int,
        order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
    ) -> None:
        if not (0 <= column < len(self.HEADERS)):
            return

        self._sortColumn = column
        self._sortOrder = order
        if len(self._records) < 2:
            return

        self.layoutAboutToBeChanged.emit()
        self._records.sort(
            key=self._sortKey,
            reverse=order == Qt.SortOrder.DescendingOrder,
        )
        self.layoutChanged.emit()

    def _sortKey(self, record: tuple[Any, ...]):
        column = self._sortColumn
        value = record[column] if column < len(record) else ""
        if column == 1:
            try:
                return 0, int(value)
            except (TypeError, ValueError):
                return 1, str(value).casefold()
        return 0, "" if value is None else str(value).casefold()


class KwicResultTableModel(QAbstractTableModel):
    """KWIC 命中模型，仅在视图请求可见索引时生成显示数据。"""

    HEADERS = ("来源文件", "左侧语境", "节点词", "右侧语境")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hits: list[Any] = []
        self._nodeFont = QFont()
        self._nodeFont.setBold(True)
        qconfig.themeChangedFinished.connect(self._notifyThemeChanged)

    def setHits(self, hits: Iterable[Any]) -> None:
        self.beginResetModel()
        self._hits = list(hits)
        self.endResetModel()

    def clear(self) -> None:
        self.setHits(())

    def hitAt(self, rowIndex: int):
        if 0 <= rowIndex < len(self._hits):
            return self._hits[rowIndex]
        return None

    def _notifyThemeChanged(self, *_args) -> None:
        if not self._hits:
            return
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(len(self._hits) - 1, len(self.HEADERS) - 1),
            [
                Qt.ItemDataRole.ForegroundRole,
                Qt.ItemDataRole.BackgroundRole,
            ],
        )

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._hits)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        rowIndex = index.row()
        columnIndex = index.column()
        if not (0 <= rowIndex < len(self._hits)):
            return None
        hit = self._hits[rowIndex]
        values = (hit.sourceFile, hit.leftText, hit.nodeText, hit.rightText)
        text = values[columnIndex]

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return text
        if role == Qt.ItemDataRole.ToolTipRole and text:
            return text
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if columnIndex == 1:
                return int(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            if columnIndex == 2:
                return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            if columnIndex == 2:
                return QColor("#FFD86A" if isDarkTheme() else "#A33A00")
            if columnIndex in (1, 3):
                return QColor("#B3B3B3" if isDarkTheme() else "#616161")
        if role == Qt.ItemDataRole.BackgroundRole and columnIndex == 2:
            return QColor("#5D5016" if isDarkTheme() else "#FFF7B0")
        if role == Qt.ItemDataRole.FontRole and columnIndex == 2:
            return self._nodeFont
        if role == Qt.ItemDataRole.UserRole:
            return hit
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
            return None
        return str(section + 1)


__all__ = ["BiasResultTableModel", "KwicResultTableModel"]
