# coding: utf-8
"""设置页主题模式卡片回归测试。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout
from qfluentwidgets import Theme, setTheme

from app.core.utils import cfg, qconfig
from app.view import setting_interface as settingModule
from app.view.setting_interface import DisplaySettingWidget, SettingInterface
from app.view.widgets.prismatica_theme import shellPalette


def testThemeAndDpiControlsShareDisplayGroup(qtbot) -> None:
    widget = DisplaySettingWidget()
    qtbot.addWidget(widget)

    assert widget.title == "外观与缩放"
    assert len(widget.groupWidgets) == 2
    assert [group.titleLabel.text() for group in widget.groupWidgets] == [
        "界面主题",
        "界面缩放",
    ]
    assert widget.themeModeComboBox.currentData() == qconfig.get(cfg.themeMode)
    assert [
        widget.themeModeComboBox.itemData(index)
        for index in range(widget.themeModeComboBox.count())
    ] == [Theme.AUTO, Theme.LIGHT, Theme.DARK]
    assert widget.themeModeComboBox.accessibleName() == "界面主题模式"
    assert widget.dpiScaleComboBox.accessibleName() == "界面显示缩放比例"


def testThemeSelectionAppliesImmediatelyAndRequestsPersistence(
    qtbot,
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        settingModule,
        "setTheme",
        lambda theme, save=False: calls.append((theme, save)),
    )
    widget = DisplaySettingWidget()
    qtbot.addWidget(widget)
    currentTheme = widget.themeModeComboBox.currentData()
    targetTheme = Theme.DARK if currentTheme != Theme.DARK else Theme.LIGHT

    widget.themeModeComboBox.setCurrentIndex(
        widget.themeModeComboBox.findData(targetTheme)
    )

    assert calls == [(targetTheme, True)]


def testAppearanceCardKeepsReadableHeaderColorsInDarkTheme(
    qtbot,
    monkeypatch,
) -> None:
    widget = DisplaySettingWidget()
    qtbot.addWidget(widget)
    darkPalette = shellPalette(True)
    monkeypatch.setattr(settingModule, "shellPalette", lambda: darkPalette)

    widget._applyCardStyle()

    assert darkPalette.text.name() in widget.styleSheet()
    assert darkPalette.mutedText.name() in widget.headerSummaryLabel.styleSheet()


def testThemeControlPrecedesDpiInKeyboardAndCompactOrder(qtbot) -> None:
    widget = DisplaySettingWidget()
    qtbot.addWidget(widget)
    widget.resize(420, 420)
    widget.setCompactLayout(True)
    widget.show()
    widget.activateWindow()
    widget.raise_()
    QTest.qWait(30)

    widget.themeModeComboBox.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(widget.themeModeComboBox, Qt.Key.Key_Tab)

    assert QApplication.focusWidget() is widget.dpiScaleComboBox
    assert all(
        group.hBoxLayout.direction() == QBoxLayout.Direction.TopToBottom
        for group in widget.groupWidgets
    )


def testSettingInterfaceUsesCombinedAppearanceCard(qtbot) -> None:
    interface = SettingInterface()
    qtbot.addWidget(interface)

    assert isinstance(interface.displaySettingWidget, DisplaySettingWidget)
    assert interface.contentLayout.indexOf(interface.displaySettingWidget) == 1


def testSettingPageTextRefreshesAcrossThemeSwitch(qtbot) -> None:
    previousTheme = qconfig.get(cfg.themeMode)
    try:
        setTheme(Theme.LIGHT, save=False)
        QApplication.processEvents()
        interface = SettingInterface()
        qtbot.addWidget(interface)
        interface.show()
        QApplication.processEvents()

        lightPalette = shellPalette(False)
        assert lightPalette.text.name() in interface.titleLabel.styleSheet()
        assert lightPalette.mutedText.name() in interface.subtitleLabel.styleSheet()

        setTheme(Theme.DARK, save=False)
        qtbot.wait(30)
        QApplication.processEvents()
        darkPalette = shellPalette(True)

        assert darkPalette.text.name() in interface.titleLabel.styleSheet()
        assert darkPalette.mutedText.name() in interface.subtitleLabel.styleSheet()
        assert darkPalette.mutedText.name() in interface.infoLabel.styleSheet()
        assert all(
            darkPalette.text.name() in group.titleLabel.styleSheet()
            for card in (
                interface.displaySettingWidget,
                interface.analysisSettingWidget,
                interface.softwareSettingWidget,
                interface.aiChatSettingWidget,
                interface.aiInsightSettingWidget,
                interface.aboutSettingWidget,
            )
            for group in card.groupWidgets
        )
        assert all(
            darkPalette.text.name() in label.styleSheet()
            for label in interface.aboutSettingWidget.systemInfoValueLabels
        )

        setTheme(Theme.LIGHT, save=False)
        qtbot.wait(30)
        QApplication.processEvents()
        assert lightPalette.text.name() in interface.titleLabel.styleSheet()
        assert lightPalette.mutedText.name() in interface.subtitleLabel.styleSheet()
    finally:
        setTheme(previousTheme, save=False)
        QApplication.processEvents()
