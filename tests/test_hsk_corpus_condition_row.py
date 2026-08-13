# coding: utf-8
"""HSK 作文语料检索条件行回归测试。"""
from __future__ import annotations

from app.core.utils.constant import hskEssayList
from app.view.widgets.hsk_corpus.hsk_corpus_browser import _ConditionRow


def _selectColumn(row: _ConditionRow, columnName: str) -> None:
    for index in range(row.columnCombo.count()):
        if row.columnCombo.itemData(index) == columnName:
            row.columnCombo.setCurrentIndex(index)
            return
    raise AssertionError(f"未找到检索字段: {columnName}")


def testEssayTitleConditionUsesConfiguredComboBox(qtbot) -> None:
    row = _ConditionRow(["国籍", "作文题目", "作文分数"])
    qtbot.addWidget(row)

    _selectColumn(row, "作文题目")

    assert row.keywordEdit is None
    assert row.essayCombo is not None
    assert [
        row.essayCombo.itemText(index)
        for index in range(row.essayCombo.count())
    ] == hskEssayList
    assert row.essayCombo.accessibleName() == "作文题目"


def testEssayTitleConditionSkipsUnlimitedAndReturnsSelectedTitle(qtbot) -> None:
    row = _ConditionRow(["国籍", "作文题目"])
    qtbot.addWidget(row)
    _selectColumn(row, "作文题目")

    assert row.essayCombo.currentText() == "不限"
    assert row.currentCondition() is None

    selectedTitle = hskEssayList[1]
    row.essayCombo.setCurrentIndex(row.essayCombo.findText(selectedTitle))

    assert row.currentCondition() == {
        "type": "text",
        "column": "作文题目",
        "keyword": selectedTitle,
    }
