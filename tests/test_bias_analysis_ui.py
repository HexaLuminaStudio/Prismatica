"""偏误分析第一阶段界面结构回归测试。"""

import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QBoxLayout, QFileDialog, QTableWidget


proModule = types.ModuleType("qfluentwidgetspro")
proModule.RoundTableWidget = QTableWidget
sys.modules["qfluentwidgetspro"] = proModule

from app.view.resource import resource  # noqa: F401, E402
from app.view.bias_interface import BiasInterface, CHARACTERS_TYPES  # noqa: E402


def _getApp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _buildPayload() -> tuple[list[str], dict]:
    selectedTypes = [next(iter(CHARACTERS_TYPES))]
    record = (
        "示例.xlsx",
        2,
        "这是一条例句",
        selectedTypes[0],
        "错",
        "HSK4",
        "中国",
    )
    payload = {
        "records": [record],
        "typeCounts": {selectedTypes[0]: 1},
        "heatmapData": {(selectedTypes[0], "HSK4"): [record]},
        "heatmapGroups": ["HSK4"],
    }
    return selectedTypes, payload


def test_bias_workspace_switches_without_page_horizontal_overflow():
    app = _getApp()
    widget = BiasInterface()
    widget.show()

    widget.resize(1400, 900)
    app.processEvents()
    assert widget.workspaceLayout.direction() == QBoxLayout.Direction.LeftToRight
    assert widget.conditionCard.width() == 340

    widget.resize(760, 900)
    app.processEvents()
    assert widget.workspaceLayout.direction() == QBoxLayout.Direction.TopToBottom
    assert widget.scrollArea.horizontalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert widget.scrollArea.horizontalScrollBar().maximum() == 0
    assert widget.conditionCard.width() == widget.resultCard.width()
    widget.close()


def test_bias_results_use_five_in_page_views_and_empty_state():
    _getApp()
    widget = BiasInterface()

    assert list(widget.resultPages) == [
        "records",
        "count",
        "chart",
        "heatmap",
        "rules",
    ]
    assert widget.detailStack.currentWidget() is widget.detailEmptyState
    assert widget.exportBtn.isEnabled() is False
    assert widget.analyzeBtn.isEnabled() is False
    widget.close()


def test_bias_count_and_heatmap_are_mounted_in_result_pages():
    app = _getApp()
    widget = BiasInterface()
    selectedTypes, payload = _buildPayload()
    widget._matchingSelectTypes = selectedTypes
    widget._onMatchingFinished(payload)

    assert widget.detailStack.currentWidget() is widget.tableWidget
    assert widget.tableWidget.rowCount() == 1
    assert widget.exportBtn.isEnabled() is True

    widget._runCount()
    app.processEvents()
    countDialog = widget._embeddedDialogs["count"]
    assert countDialog.isVisible() is False
    assert countDialog.widget.parent() is widget.resultPages["count"]

    widget._runHeatmap()
    app.processEvents()
    heatmapDialog = widget._embeddedDialogs["heatmap"]
    assert heatmapDialog.isVisible() is False
    assert heatmapDialog.widget.parent() is widget.resultPages["heatmap"]
    widget.close()


def test_multifile_analysis_tolerates_missing_country_column(qtbot):
    widget = BiasInterface()
    qtbot.addWidget(widget)
    firstPath = "第一个文件.xlsx"
    secondPath = "第二个文件.xlsx"
    widget.filesList = [firstPath, secondPath]
    widget.dfs = {
        firstPath: pd.DataFrame(
            {
                "text": ["错[C]"],
                "level": ["HSK4"],
                "authornationality": ["法国"],
            }
        ),
        secondPath: pd.DataFrame(
            {
                "text": ["误[C]"],
                "level": ["HSK5"],
            }
        ),
    }

    widget._detectGroupColumns()
    assert widget.levelColumn == "level"
    assert widget.countryColumn is None

    # 模拟旧状态残留的非共有列，逐文件读取仍应安全降级为“未知”。
    widget.selectedColumn = "text"
    widget.countryColumn = "authornationality"
    selectedType = next(iter(CHARACTERS_TYPES))
    widget.charFilter.checkboxes[selectedType].setChecked(True)
    assert widget.analyzeBtn.isEnabled() is True

    widget._runMatching()
    qtbot.waitUntil(lambda: len(widget.currentRecords) == 2, timeout=3000)

    assert len(widget.currentRecords) == 2
    assert widget.currentRecords[0][6] == "法国"
    assert widget.currentRecords[1][6] == "未知"


def test_analysis_is_blocked_when_required_field_is_not_shared(qtbot):
    widget = BiasInterface()
    qtbot.addWidget(widget)
    widget.filesList = ["a.xlsx", "b.xlsx"]
    widget.dfs = {
        "a.xlsx": pd.DataFrame({"shared": ["甲"], "text": ["错[C]"]}),
        "b.xlsx": pd.DataFrame({"shared": ["乙"], "content": ["误[C]"]}),
    }
    widget._updateColumns()
    widget.selectedColumn = "text"
    selectedType = next(iter(CHARACTERS_TYPES))
    widget.charFilter.checkboxes[selectedType].setChecked(True)
    widget._refreshAnalyzeState()

    assert widget.analyzeBtn.isEnabled() is False
    assert widget.columnCompatibilityLabel.isHidden() is False
    assert "1 个文件缺少“text”" in widget.columnCompatibilityLabel.text()

    widget._runMatching()
    assert getattr(widget, "_matchingWorker", None) is None


def test_import_automatically_detects_single_or_multiple_files(qtbot, monkeypatch):
    widget = BiasInterface()
    qtbot.addWidget(widget)
    capturedSelections = []
    selectedFiles = ["a.xlsx"]

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileNames",
        lambda *args, **kwargs: (list(selectedFiles), "Excel Files (*.xlsx)"),
    )
    monkeypatch.setattr(
        widget,
        "_startLoading",
        lambda filePaths: capturedSelections.append(list(filePaths)),
    )

    widget._onChooseFile()
    assert capturedSelections[-1] == ["a.xlsx"]
    widget.filesList = ["a.xlsx"]
    assert widget._isMultiFileMode() is False

    selectedFiles[:] = ["a.xlsx", "b.xlsx", "a.xlsx"]
    widget._onChooseFile()
    assert capturedSelections[-1] == ["a.xlsx", "b.xlsx"]
    widget.filesList = ["a.xlsx", "b.xlsx"]
    assert widget._isMultiFileMode() is True
    assert hasattr(widget, "switchBtn") is False
