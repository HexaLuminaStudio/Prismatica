# coding: utf-8
"""桌面端共享组件的暗色主题自动化回归。"""
from __future__ import annotations

import pytest
from matplotlib import colors as matplotlibColors
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import PrimaryPushButton, TableWidget, Theme, setTheme

from app.core.utils import cfg, qconfig
from app.view import bias_interface as biasModule
from app.view.bias_interface import AssociationRulesDialog
from app.view.setting_interface import AiChatSettingWidget, SoftwareSettingWidget
from app.view.task_interface import TaskInterface
from app.view.widgets.account import login_dialog as loginModule
from app.view.widgets.account.login_dialog import LoginInterface
from app.view.widgets.freq_analyzer.result_summary import (
    MetricCard,
    MetricColor,
    ResultSummary,
)
from app.view.widgets.freq_analyzer.concordance_plot_widget import (
    ConcordancePlotCanvas,
)
from app.view.widgets.prismatica_theme import applyMatplotlibTheme, shellPalette
from app.view.widgets.titlebar_widget import CustomTitleBar


@pytest.fixture
def darkTheme():
    previousTheme = qconfig.get(cfg.themeMode)
    setTheme(Theme.DARK, save=False)
    QApplication.processEvents()
    yield shellPalette(True)
    setTheme(previousTheme, save=False)
    QApplication.processEvents()


def testLoginTaskAndAiSettingsUseDarkSurfaces(
    qtbot,
    monkeypatch,
    darkTheme,
) -> None:
    monkeypatch.setattr(
        loginModule,
        "IndeterminateProgressPushButton",
        PrimaryPushButton,
    )
    login = LoginInterface()
    task = TaskInterface()
    aiSettings = AiChatSettingWidget()
    for widget in (login, task, aiSettings):
        qtbot.addWidget(widget)

    assert darkTheme.surface.name() in login.styleSheet()
    assert darkTheme.surfaceAlt.name() in login._loginEmailEdit.styleSheet()
    assert darkTheme.surfaceAlt.name() in task.styleSheet()
    assert darkTheme.surface.name() in task.downloadingScrollArea.emptyCard.styleSheet()
    assert darkTheme.surfaceAlt.name() in aiSettings.statusFooter.styleSheet()
    assert darkTheme.text.name() in aiSettings.modelPill.styleSheet()


def testResultSummaryUsesThemeSpecificMetricColors(qtbot, darkTheme) -> None:
    summary = ResultSummary()
    qtbot.addWidget(summary)
    summary.setMetrics(
        [
            ("词种数", "8,428", MetricColor.PRIMARY),
            ("Token 总数", "87,530", MetricColor.SUCCESS),
            ("Top 词", "的", MetricColor.NEUTRAL),
        ]
    )

    cards = summary.findChildren(MetricCard)
    assert darkTheme.surface.name() in summary.styleSheet()
    assert darkTheme.text.name() in summary._titleLabel.styleSheet()
    assert "#183a58" in cards[0].styleSheet().lower()
    assert "#223a28" in cards[1].styleSheet().lower()
    assert "#32383d" in cards[2].styleSheet().lower()
    assert all(darkTheme.mutedText.name() in card.textLabel.styleSheet() for card in cards)


def testAssociationRuleLabelsRemainReadableInDarkTheme(
    qtbot,
    monkeypatch,
    darkTheme,
) -> None:
    monkeypatch.setattr(biasModule, "RoundTableWidget", TableWidget)
    monkeypatch.setattr(AssociationRulesDialog, "_recompute", lambda self: None)
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = AssociationRulesDialog([], parent=parent)
    qtbot.addWidget(dialog)

    assert all(
        darkTheme.text.name() in label.styleSheet()
        for label in dialog.parameterLabels
    )
    assert darkTheme.mutedText.name() in dialog.methodLabel.styleSheet()


def testTitleBarButtonsAndMatplotlibFollowDarkTheme(qtbot, darkTheme) -> None:
    window = QWidget()
    qtbot.addWidget(window)
    titleBar = CustomTitleBar(window)
    assert titleBar.minBtn.getNormalColor() == darkTheme.text
    assert titleBar.maxBtn.getNormalColor() == darkTheme.text
    assert titleBar.closeBtn.getNormalColor() == darkTheme.text

    chartHost = QWidget()
    qtbot.addWidget(chartHost)
    canvas = FigureCanvasQTAgg(Figure())
    canvas.setParent(chartHost)
    axes = canvas.figure.add_subplot(111)
    annotation = axes.text(0.5, 0.5, "分析结果", color="black")

    applyMatplotlibTheme(chartHost, dark=True)

    assert canvas.figure.get_facecolor()[:3] == pytest.approx(
        darkTheme.surface.getRgbF()[:3],
        abs=0.01,
    )
    assert annotation.get_color() == darkTheme.text.name()


def testExistingComponentsRefreshWhenThemeChanges(
    qtbot,
    monkeypatch,
) -> None:
    previousTheme = qconfig.get(cfg.themeMode)
    monkeypatch.setattr(
        loginModule,
        "IndeterminateProgressPushButton",
        PrimaryPushButton,
    )
    try:
        setTheme(Theme.LIGHT, save=False)
        QApplication.processEvents()
        login = LoginInterface()
        task = TaskInterface()
        summary = ResultSummary()
        aiSettings = AiChatSettingWidget()
        for widget in (login, task, summary, aiSettings):
            qtbot.addWidget(widget)
        lightPalette = shellPalette(False)
        assert lightPalette.surface.name() in login.styleSheet()
        assert lightPalette.surfaceAlt.name() in task.styleSheet()

        setTheme(Theme.DARK, save=False)
        qtbot.wait(30)
        QApplication.processEvents()
        darkPalette = shellPalette(True)

        assert darkPalette.surface.name() in login.styleSheet()
        assert darkPalette.surfaceAlt.name() in task.styleSheet()
        assert darkPalette.surface.name() in summary.styleSheet()
        assert darkPalette.surfaceAlt.name() in aiSettings.statusFooter.styleSheet()
    finally:
        setTheme(previousTheme, save=False)
        QApplication.processEvents()


def testConcordancePlotCanvasKeepsOneThemeAcrossLiveSwitch(
    qtbot,
    darkTheme,
) -> None:
    plot = ConcordancePlotCanvas()
    qtbot.addWidget(plot)
    plot.render(
        {"sample.txt": [2, 8, 15]},
        {"sample.txt": 20},
        "学习",
    )

    figure = plot._canvas.figure
    axes = figure.axes[0]
    assert darkTheme.surface.name() in plot.styleSheet()
    assert matplotlibColors.to_hex(figure.get_facecolor()) == darkTheme.surface.name()
    assert matplotlibColors.to_hex(axes.get_facecolor()) == darkTheme.surface.name()
    assert matplotlibColors.to_hex(figure._suptitle.get_color()) == darkTheme.text.name()

    setTheme(Theme.LIGHT, save=False)
    qtbot.wait(30)
    QApplication.processEvents()
    lightPalette = shellPalette(False)
    assert lightPalette.surface.name() in plot.styleSheet()
    assert matplotlibColors.to_hex(figure.get_facecolor()) == lightPalette.surface.name()
    assert matplotlibColors.to_hex(axes.get_facecolor()) == lightPalette.surface.name()
    assert matplotlibColors.to_hex(figure._suptitle.get_color()) == lightPalette.text.name()


def testPathDisplaysShareVisibleBordersInBothThemes(qtbot, darkTheme) -> None:
    softwareSettings = SoftwareSettingWidget()
    aiSettings = AiChatSettingWidget()
    qtbot.addWidget(softwareSettings)
    qtbot.addWidget(aiSettings)

    for theme, isDark in ((Theme.LIGHT, False), (Theme.DARK, True)):
        setTheme(theme, save=False)
        qtbot.wait(30)
        QApplication.processEvents()
        palette = shellPalette(isDark)
        downloadStyle = softwareSettings.downloadPathLabel.styleSheet()
        promptStyle = aiSettings.systemPromptFileLabel.styleSheet()

        assert downloadStyle == promptStyle
        assert palette.surfaceAlt.name() in downloadStyle
        assert palette.border.name() in downloadStyle
        assert palette.mutedText.name() in downloadStyle
