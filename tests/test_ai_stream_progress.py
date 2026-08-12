"""AI 解读阶段进度与界面状态回归测试。"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, qconfig, setTheme

from app.core.services.ai_insight_service import AiInsightService
from app.view.widgets.freq_analyzer.ai_insight_drawer_view import AiInsightDrawerView
from app.view.widgets.prismatica_theme import pageBackgroundColor, shellPalette


def testAiInsightService_ForwardsServerProgress(qtbot) -> None:
    service = AiInsightService()
    progress = []
    service.progressChanged.connect(
        lambda stage, percent, message: progress.append((stage, percent, message))
    )

    service._chat.progressChanged.emit("preauthorizing", 12, "正在确认余额")

    assert progress == [("preauthorizing", 12, "正在确认余额")]


def testAiInsightDrawer_ShowsStageProgressWithoutClaimingTokenPercent(qtbot) -> None:
    view = AiInsightDrawerView()
    qtbot.addWidget(view)
    view.setRunning(True)

    view.setProgress("generating", 20, "AI 正在生成解读")

    assert view.streamingHintLabel.text() == "AI 正在生成解读 · 流程 20%"
    assert view.statusLabel.text() == "AI 正在生成解读"
    assert view.progressRing.isVisibleTo(view)


def testAiInsightDrawer_RefreshesSurfacesAcrossThemeRoundTrip(qtbot) -> None:
    previousTheme = qconfig.theme
    view = AiInsightDrawerView()
    qtbot.addWidget(view)
    try:
        setTheme(Theme.LIGHT, save=False)
        QApplication.processEvents()
        lightPage = pageBackgroundColor(False).name()
        lightText = shellPalette(False).text.name()
        assert lightPage in view.styleSheet()
        assert lightText in view.styleSheet()

        setTheme(Theme.DARK, save=False)
        QApplication.processEvents()
        darkPage = pageBackgroundColor(True).name()
        darkText = shellPalette(True).text.name()
        assert darkPage in view.styleSheet()
        assert darkText in view.styleSheet()

        setTheme(Theme.LIGHT, save=False)
        QApplication.processEvents()
        assert lightPage in view.styleSheet()
        assert lightText in view.styleSheet()
    finally:
        setTheme(previousTheme, save=False)
