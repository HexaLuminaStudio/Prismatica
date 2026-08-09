"""HSK 作文检索结果区、条件摘要和详情抽屉回归测试。"""

from PySide6.QtCore import Qt

from app.view.resource import resource  # noqa: F401
from app.view.widgets.hsk_corpus.hsk_corpus_browser import (
    HskCorpusBrowser,
    hskLocalCorpusService,
)
from app.view.widgets.hsk_corpus.hsk_corpus_detail_drawer import (
    HskCorpusDetailDrawer,
)
from app.view.widgets.hsk_corpus.hsk_corpus_model import HskCorpusModel


_RESULT_RECORD = {
    "作文题目": "一次难忘的旅行",
    "证书级别": "B",
    "国籍": "日本",
    "总词数": 168,
    "总字数": 246,
    "听力理解分数": 82,
    "阅读理解分数": 88,
    "综合表达考试分数": 84,
    "口试分数": 79,
    "作文分数": 86,
    "作文母号": "ZW-001",
    "性别": "女",
    "imported_at": "2026-08-09",
}


def test_model_exposes_record_copy_for_detail_view(qtbot) -> None:
    model = HskCorpusModel()
    model.reset()
    model.setAllRows([_RESULT_RECORD])

    record = model.recordAt(0)
    assert record == _RESULT_RECORD
    assert record is not _RESULT_RECORD
    assert model.recordAt(-1) is None
    assert model.recordAt(1) is None


def test_result_table_defaults_to_five_research_columns(qtbot) -> None:
    browser = HskCorpusBrowser()
    qtbot.addWidget(browser)

    visibleColumns = {
        columnName
        for columnIndex, columnName in enumerate(browser.model.columns())
        if not browser.tableView.isColumnHidden(columnIndex)
    }
    assert visibleColumns == {
        "作文题目",
        "国籍",
        "证书级别",
        "作文分数",
        "总字数",
    }
    assert browser.tableView.isColumnHidden(
        browser.model.columns().index("imported_at")
    )

    assert browser._setColumnVisible("总词数", True)
    assert not browser.tableView.isColumnHidden(
        browser.model.columns().index("总词数")
    )


def test_condition_summary_uses_readable_and_semantics(qtbot) -> None:
    browser = HskCorpusBrowser()
    qtbot.addWidget(browser)

    browser._updateConditionSummary(
        [
            {"type": "text", "column": "国籍", "keyword": "日本"},
            {
                "type": "score",
                "column": "作文分数",
                "min": 80,
                "max": 90,
            },
        ]
    )

    assert browser._conditionSummaryTitle.text() == "已应用 2 个条件（全部满足）"
    assert browser._conditionSummaryText.text() == (
        "国籍：包含「日本」  且  作文分数：80–90"
    )


def test_selecting_result_opens_real_body_detail(qtbot, monkeypatch) -> None:
    browser = HskCorpusBrowser()
    qtbot.addWidget(browser)
    browser.resize(1400, 820)
    browser.show()
    browser.model.setAllRows([_RESULT_RECORD])
    browser._showTableState()
    monkeypatch.setattr(
        hskLocalCorpusService,
        "getRecord",
        lambda zwhao: {
            "zwhao": zwhao,
            "data": "Title 一次难忘的旅行\n这是本地镜像中的真实作文正文。",
        },
    )

    browser._showResultDetail(0)
    qtbot.wait(10)

    assert not browser._detailDrawer.isHidden()
    assert browser._detailDrawer.idLabel.text() == "作文母号：ZW-001"
    assert "真实作文正文" in browser._detailDrawer.bodyEdit.toPlainText()
    assert browser._resultSplitter.orientation() == Qt.Orientation.Horizontal

    browser.resize(1100, 760)
    qtbot.wait(10)
    assert browser._resultSplitter.orientation() == Qt.Orientation.Vertical


def test_detail_drawer_marks_missing_body_without_fake_content(qtbot) -> None:
    drawer = HskCorpusDetailDrawer()
    qtbot.addWidget(drawer)

    drawer.setRecord(_RESULT_RECORD, None)

    assert drawer.bodyStateLabel.text() == "正文未就绪"
    assert "未找到这条记录" in drawer.bodyEdit.toPlainText()
    assert drawer.titleLabel.text() == "一次难忘的旅行"
