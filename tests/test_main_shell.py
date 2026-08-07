"""主窗口应用外壳视觉令牌与顶栏组件测试。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.view.widgets.prismatica_theme import shellPalette
from app.view.widgets.project_switcher_widget import (
    ProjectSwitcher,
    _SENTINEL_MANAGE,
    _SENTINEL_NEW,
)


_app = QApplication.instance() or QApplication(sys.argv)


def _mainWindowMethod(name: str) -> ast.FunctionDef:
    sourcePath = Path(__file__).parents[1] / "app" / "view" / "main_window.py"
    module = ast.parse(sourcePath.read_text(encoding="utf-8"))
    mainWindow = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    return next(
        node
        for node in mainWindow.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _calledAttributes(method: ast.FunctionDef) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(method)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_shell_palette_has_distinct_window_content_and_border_layers() -> None:
    light = shellPalette(False)
    dark = shellPalette(True)

    assert light.window.name() == "#eef3f6"
    assert light.content.name() == "#fcfdfd"
    assert light.border != light.content
    assert dark.window.lightness() < light.window.lightness()
    assert dark.text.lightness() > dark.window.lightness()


def test_main_window_installs_committed_shell_and_navigation_components() -> None:
    initCalls = _calledAttributes(_mainWindowMethod("__init__"))
    navigationCalls = _calledAttributes(_mainWindowMethod("initNavigation"))

    assert "_configurePrismaticaShell" in initCalls
    assert "_installPrismaticaNavigation" in initCalls
    assert "addSectionHeader" in navigationCalls
    assert "_connectTaskNavigationBadge" in navigationCalls


def test_logged_out_account_entry_routes_to_embedded_login_page() -> None:
    openAccount = _mainWindowMethod("_openAccountPanel")
    called = _calledAttributes(openAccount)
    referencedAttributes = {
        node.attr for node in ast.walk(openAccount) if isinstance(node, ast.Attribute)
    }

    assert "loginInterface" in referencedAttributes
    assert "switchTo" in called
    assert not any(
        isinstance(node, ast.Name) and node.id == "LoginDialog"
        for node in ast.walk(openAccount)
    )


def test_auth_page_bypasses_position_based_page_animation() -> None:
    switchTo = _mainWindowMethod("switchTo")
    directSwitches = [
        node
        for node in ast.walk(switchTo)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "QStackedWidget"
        and node.func.attr == "setCurrentWidget"
    ]

    assert directSwitches


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
    assert all(
        "📁" not in item.text and "➕" not in item.text
        for item in actions.values()
    )
