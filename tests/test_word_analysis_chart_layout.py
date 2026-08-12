"""词汇增长曲线布局回归测试。"""

from __future__ import annotations

from PySide6.QtWidgets import QSizePolicy

from app.view.widgets.freq_analyzer.word_analysis_widget import WordAnalysisWidget
from app.view.widgets.freq_analyzer.word_analysis_engine import CurvePoint, WordMetrics


def testTypeTokenChartKeepsReadableHeightInsideScrollArea(qtbot) -> None:
    widget = WordAnalysisWidget()
    qtbot.addWidget(widget)
    widget.resize(900, 620)
    widget.show()
    qtbot.wait(20)

    assert widget._canvas.minimumHeight() >= 340
    assert (
        widget._canvas.sizePolicy().verticalPolicy()
        == QSizePolicy.Policy.MinimumExpanding
    )
    assert widget._canvas.height() >= 340
    # Fluent ScrollArea 用覆盖层滚动条，原生策略固定为 AlwaysOff；
    # forceHidden=False 才代表覆盖层纵向滚动条按需可用。
    assert widget._scrollArea.scrollDelagate.vScrollBar._isForceHidden is False


def testTypeTokenCurveKeepsTitleAndAxisLabelsInsideFigure(qtbot) -> None:
    widget = WordAnalysisWidget()
    qtbot.addWidget(widget)
    widget.resize(900, 620)
    widget.show()
    metrics = WordMetrics(
        totalTokens=1000,
        totalTypes=180,
        typeTokenCurve=[
            CurvePoint(tokenCount=0, typeCount=0),
            CurvePoint(tokenCount=500, typeCount=120),
            CurvePoint(tokenCount=1000, typeCount=180),
        ],
    )

    widget._plotTypeTokenCurve(metrics)
    renderer = widget._canvas.get_renderer()
    figureBox = widget._figure.bbox
    artists = [widget._ax.title, widget._ax.xaxis.label, widget._ax.yaxis.label]

    for artist in artists:
        box = artist.get_window_extent(renderer)
        assert box.x0 >= figureBox.x0
        assert box.y0 >= figureBox.y0
        assert box.x1 <= figureBox.x1
        assert box.y1 <= figureBox.y1
