# coding:utf-8
"""
HSK 语料 QSqlQueryModel
========================

设计:
    - 继承 QAbstractTableModel(不是 QSqlQueryModel),完全控制数据更新
    - rows 在子线程累积,UI 主线程通过 setAllRows 一次性替换
    - 期间不 emit dataChanged / rowsInserted,完全静默
    - 只有最终替换才 emit modelReset,确保 UI 只重绘一次

关键 API:
    - reset()                       清空 + 锁定列结构(检索开始前调用)
    - setAllRows(rows, totalCount)  一次性替换全部行(主线程用)
    - rowCount() / columnCount()    QTableView 渲染查询
    - data() / headerData()         QTableView 渲染查询
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from app.core.services.hsk_corpus_service import HskCorpusService


class HskCorpusModel(QAbstractTableModel):
    """HSK 语料检索结果 Model(线程安全的「数据在子线程 / 渲染在主线程」)。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: List[Dict] = []
        self._columns: List[str] = []
        self._headerMap: Dict[str, str] = {}
        self._lastKeyword: str = ""
        self._lastColumn: str = ""

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def setHeaderMap(self, headerMap: Dict[str, str]) -> None:
        """设置列名 → 中文表头映射(由 service 提供)。"""
        self._headerMap = dict(headerMap)

    def reset(self) -> None:
        """清空 Model(检索开始前调用,锁定列结构)。"""
        self.beginResetModel()
        self._rows = []
        self._columns = HskCorpusService.instance().allColumns()
        self.endResetModel()

    # 单页最多向 UI 渲染的行数(后台仍然累计全量,只显示前 N 条)
    DISPLAY_LIMIT: int = 20

    def setAllRows(self, rows: List[Dict], totalCount: Optional[int] = None) -> None:
        """一次性替换全部行(主线程渲染钩子)。

        子线程累积 rows,UI 通过 QTimer 节流调用本方法(默认 60ms/次),
        避免 beginInsertRows + viewport 反复重绘造成的卡顿。

        显示策略:无论后台拉回多少行,Model 仅承载前
        [DISPLAY_LIMIT](file:///e:/Prismatica/app/view/widgets/hsk_corpus/hsk_corpus_model.py#L51-L52) 条,防止大量数据同时塞进 viewport 造成渲染开销 / 拖窗异常。

        Args:
            rows:       本次要渲染的行(由调用方截断到 DISPLAY_LIMIT)
            totalCount: 全量命中数(给 UI 状态条用,不参与显示)
        """
        self.beginResetModel()
        self._rows = list(rows or [])[: self.DISPLAY_LIMIT]
        if self._rows and not self._columns:
            self._columns = HskCorpusService.instance().allColumns()
        self.endResetModel()
        # 注:不调用 resizeColumnsToContents(那是浏览器自己的事)

    def setLastQuery(self, column: str, keyword: str) -> None:
        self._lastColumn = column
        self._lastKeyword = keyword

    def lastKeyword(self) -> str:
        return self._lastKeyword

    def lastColumn(self) -> str:
        return self._lastColumn

    def columns(self) -> List[str]:
        """返回当前稳定列顺序，供结果表列设置使用。"""
        return list(self._columns)

    def recordAt(self, rowIndex: int) -> Optional[Dict]:
        """返回指定行的记录副本，供详情视图读取。"""
        if rowIndex < 0 or rowIndex >= len(self._rows):
            return None
        return dict(self._rows[rowIndex])

    # ------------------------------------------------------------------
    # QAbstractTableModel 接口
    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid():
            return 0
        return len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self._rows):
            return None
        if col < 0 or col >= len(self._columns):
            return None

        rec = self._rows[row]
        colName = self._columns[col]
        value = rec.get(colName)

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if isinstance(value, (int, float)):
                return int(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            text = "" if value is None else str(value)
            return text if len(text) > 80 else None
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
            if 0 <= section < len(self._columns):
                colName = self._columns[section]
                return self._headerMap.get(colName, colName)
        else:
            return str(section + 1)
        return None
