"""主窗口应用外壳视觉令牌与顶栏组件测试。"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.view.widgets.prismatica_theme import shellPalette
from app.view.widgets.project_switcher_widget import (
    ProjectSwitcher,
    _SENTINEL_MANAGE,
    _SENTINEL_NEW,
)


_app = QApplication.instance() or QApplication(sys.argv)


def test_shell_palette_has_distinct_window_content_and_border_layers() -> None:
    light = shellPalette(False)
    dark = shellPalette(True)

    assert light.window.name() == "#eef3f6"
    assert light.content.name() == "#fcfdfd"
    assert light.border != light.content
    assert dark.window.lightness() < light.window.lightness()
    assert dark.text.lightness() > dark.window.lightness()


def test_project_switcher_uses_fluent_icons_for_actions() -> None:
    switcher = ProjectSwitcher()
    actions = {
        item.userData: item
        for item in switcher._comboBox.items
        if item.userData in {_SENTINEL_MANAGE, _SENTINEL_NEW}
    }

    assert switcher._iconWidget.size().width() == 18
    assert switcher._comboBox.height() == 34
    assert set(actions) == {_SENTINEL_MANAGE, _SENTINEL_NEW}
    assert all(not item.icon.isNull() for item in actions.values())
    assert all("📁" not in item.text and "➕" not in item.text for item in actions.values())
