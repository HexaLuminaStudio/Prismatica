# coding: utf-8
"""偏误与 KWIC 模型化结果表回归测试。"""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QTableView

from app.view.bias_interface import BiasInterface
from app.view.widgets.freq_analyzer.concordance_engine import KwicHit
from app.view.widgets.freq_analyzer.concordance_widget import ConcordanceWidget
from app.view.widgets.result_table_models import (
    BiasResultTableModel,
    KwicResultTableModel,
)


def testBiasResultModelLoadsAndSortsNumericRows() -> None:
    model = BiasResultTableModel()
    model.setRecords(
        [
            ("b.xlsx", 10, "第二句", "错字 [C]", "字", "HSK 5", "日本"),
            ("a.xlsx", 2, "第一句", "错词 [CC]", "词", "HSK 4", "韩国"),
        ]
    )

    assert isinstance(model, QAbstractTableModel)
    assert model.rowCount() == 2
    assert model.columnCount() == 7
    assert model.headerData(2, Qt.Orientation.Horizontal) == "句子"

    model.sort(1, Qt.SortOrder.AscendingOrder)
    assert model.recordAt(0)[1] == 2
    assert model.data(model.index(0, 0)) == "a.xlsx"

    model.setRecords(
        [
            ("c.xlsx", 30, "第三句", "错字 [C]", "字", "", ""),
            ("d.xlsx", 4, "第四句", "错字 [C]", "字", "", ""),
        ]
    )
    assert model.recordAt(0)[1] == 4


def testBiasResultModelHandlesLargeResultWithoutCellItems() -> None:
    model = BiasResultTableModel()
    records = [
        ("sample.xlsx", rowIndex + 2, "偏误句", "错字 [C]", "字", "", "")
        for rowIndex in range(10000)
    ]

    model.setRecords(records)

    assert model.rowCount() == 10000
    assert model.recordAt(9999)[1] == 10001


def testKwicResultModelProvidesAlignmentAndNodeHighlight() -> None:
    hit = KwicHit(
        leftContext=["这是", "左侧"],
        node=["节点词"],
        rightContext=["右侧", "语境"],
        sourceFile="sample.txt",
    )
    model = KwicResultTableModel()
    model.setHits([hit])

    nodeIndex = model.index(0, 2)
    leftIndex = model.index(0, 1)
    assert model.data(nodeIndex) == "节点词"
    assert isinstance(model.data(nodeIndex, Qt.ItemDataRole.BackgroundRole), QColor)
    assert isinstance(model.data(nodeIndex, Qt.ItemDataRole.FontRole), QFont)
    assert model.data(nodeIndex, Qt.ItemDataRole.FontRole).bold() is True
    assert model.data(leftIndex, Qt.ItemDataRole.TextAlignmentRole) == int(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )
    assert model.hitAt(0) is hit


def testBiasAndKwicViewsUseTableModels(qtbot) -> None:
    bias = BiasInterface()
    qtbot.addWidget(bias)
    kwic = ConcordanceWidget()
    qtbot.addWidget(kwic)

    assert isinstance(bias.tableWidget, QTableView)
    assert bias.tableWidget.model() is bias.tableModel
    assert isinstance(bias.tableModel, BiasResultTableModel)
    assert bias.tableWidget.accessibleName() == "偏误分析明细表"

    assert isinstance(kwic.resultTable, QTableView)
    assert kwic.resultTable.model() is kwic.resultModel
    assert isinstance(kwic.resultModel, KwicResultTableModel)
    assert kwic.resultTable.accessibleName() == "KWIC 检索结果表"
