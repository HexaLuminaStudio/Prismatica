from __future__ import annotations

from PySide6.QtWidgets import QLineEdit


def test_setting_interface_uses_overview_card_structure(qtbot):
    from app.view.setting_interface import SettingInterface

    page = SettingInterface()
    qtbot.addWidget(page)

    assert page.titleLabel.text() == "设置"
    assert page.contentWidget.width() == 832
    assert page.softwareSettingWidget.groupCount() == 6
    assert page.aiChatSettingWidget.groupCount() == 5
    assert page.aiInsightSettingWidget.groupCount() == 1
    assert page.aboutSettingWidget.groupCount() == 2
    for card in (
        page.softwareSettingWidget,
        page.aiChatSettingWidget,
        page.aiInsightSettingWidget,
        page.aboutSettingWidget,
    ):
        assert card.graphicsEffect() is None


def test_sensitive_tokens_are_represented_by_status_only(qtbot):
    from app.core.utils import cfg, qconfig
    from app.view.setting_interface import SoftwareSettingWidget

    widget = SoftwareSettingWidget()
    qtbot.addWidget(widget)

    hskToken = qconfig.get(cfg.HSKLoginToken) or ""
    globalToken = qconfig.get(cfg.GlobalLoginToken) or ""
    assert widget.groupWidgets[4].content().startswith("用于访问 HSK")
    assert widget.groupWidgets[5].content().startswith("用于访问公共语料")
    if hskToken:
        assert hskToken not in widget.groupWidgets[4].content()
    if globalToken:
        assert globalToken not in widget.groupWidgets[5].content()
    assert widget.hskTokenBadge.text() in {"● 已配置", "● 未配置"}
    assert widget.globalTokenBadge.text() in {"● 已配置", "● 未配置"}


def test_ai_key_visibility_and_numeric_combo_data(qtbot):
    from app.view.setting_interface import AiChatSettingWidget, SoftwareSettingWidget

    aiWidget = AiChatSettingWidget()
    softwareWidget = SoftwareSettingWidget()
    qtbot.addWidget(aiWidget)
    qtbot.addWidget(softwareWidget)

    assert aiWidget.apiKeyEdit.echoMode() == QLineEdit.EchoMode.Password
    aiWidget.apiKeyVisibilityButton.click()
    assert aiWidget.apiKeyEdit.echoMode() == QLineEdit.EchoMode.Normal
    assert aiWidget.apiKeyVisibilityButton.text() == "隐藏"

    assert isinstance(aiWidget.maxHistoryCombo.currentData(), int)
    assert isinstance(softwareWidget.pageNumsComboBox.currentData(), int)
    assert "轮" in aiWidget.maxHistoryCombo.currentText()
    assert "条 / 页" in softwareWidget.pageNumsComboBox.currentText()


def test_theme_refresh_has_no_stylesheet_parse_errors(qtbot):
    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import Theme, qconfig, setTheme

    from app.view.setting_interface import SettingInterface

    page = SettingInterface()
    qtbot.addWidget(page)
    page.show()

    messages = []

    def messageHandler(_messageType, _context, message):
        messages.append(message)

    originalTheme = qconfig.theme
    targetTheme = Theme.DARK if originalTheme != Theme.DARK else Theme.LIGHT
    previousHandler = qInstallMessageHandler(messageHandler)
    try:
        setTheme(targetTheme)
        QApplication.processEvents()
        setTheme(originalTheme)
        QApplication.processEvents()
    finally:
        qInstallMessageHandler(previousHandler)

    parseErrors = [m for m in messages if "parse stylesheet" in m.lower()]
    assert parseErrors == []

    # 卡片 QSS 在主题刷新后仍然存在，文字标签明确保持透明。
    assert "FluentLabelBase { background-color: transparent; }" in (
        page.softwareSettingWidget.styleSheet()
    )
    for group in page.softwareSettingWidget.groupWidgets:
        assert not group.titleLabel.autoFillBackground()
        assert not group.contentLabel.autoFillBackground()
