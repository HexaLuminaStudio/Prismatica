# coding: utf-8
"""Prismatica 项目内置表格组件回归测试。"""
from pathlib import Path

from PySide6.QtWidgets import QAbstractItemView, QTableWidget

from app.view.widgets.prismatica_table import PrismaticaTableWidget


def testPrismaticaTablePreservesNativeTableApi(qtbot) -> None:
    table = PrismaticaTableWidget()
    qtbot.addWidget(table)

    table.setColumnCount(2)
    table.setRowCount(1)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setBorderRadius(14)
    table.setBorderVisible(False)

    assert isinstance(table, QTableWidget)
    assert table.columnCount() == 2
    assert table.rowCount() == 1
    assert table.verticalHeader().defaultSectionSize() == 38
    assert "border-radius: 14px" in table.styleSheet()
    assert "border: 1px solid transparent" in table.styleSheet()


def testProRoundTableImportsHaveBeenRemoved() -> None:
    projectRoot = Path(__file__).resolve().parents[1]
    sourceFiles = [
        projectRoot / "app/view/bias_interface.py",
        projectRoot / "app/view/freq_analyzer_interface.py",
        projectRoot / "app/view/widgets/freq_analyzer/dialogs.py",
        projectRoot / "app/view/widgets/freq_analyzer/freq_analyzer_widget.py",
        projectRoot / "app/view/widgets/freq_analyzer/sentiment_widget.py",
    ]

    for sourceFile in sourceFiles:
        source = sourceFile.read_text(encoding="utf-8")
        assert "qfluentwidgetspro" not in source
        assert "ProRoundTableWidget" not in source
