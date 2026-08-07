"""Prismatica 品牌侧边栏行为测试。"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon, NavigationItemPosition

from app.view.widgets.account.account_nav import AccountNavWidget
from app.view.widgets.prismatica_navigation import PrismaticaNavigationBar


_app = QApplication.instance() or QApplication(sys.argv)


def _build_sidebar():
    sidebar = PrismaticaNavigationBar()
    first = sidebar.addItem("home", FluentIcon.HOME, "首页", lambda: None)
    sidebar.addSectionHeader("研究", NavigationItemPosition.SCROLL)
    task = sidebar.addItem(
        "tasks",
        FluentIcon.COMPLETED,
        "任务管理",
        lambda: None,
        position=NavigationItemPosition.BOTTOM,
    )
    sidebar.addSectionHeader("系统", NavigationItemPosition.BOTTOM)
    account = AccountNavWidget()
    sidebar.addWidget(
        "account",
        account,
        position=NavigationItemPosition.BOTTOM,
    )
    return sidebar, first, task, account


def test_sidebar_expands_and_collapses_all_custom_entries() -> None:
    sidebar, first, task, account = _build_sidebar()

    assert sidebar.isExpanded()
    assert sidebar.width() == 250
    assert first.width() == task.width() == account.width() == 226
    assert sidebar._expansionAnimation.duration() == 240

    sidebar.setExpanded(False)
    assert not sidebar.isExpanded()
    assert sidebar.width() == 64
    assert first.width() == task.width() == account.width() == 48
    assert all(header.width() == 48 for header in sidebar.sectionHeaders)

    sidebar.setExpanded(True)
    assert sidebar.width() == 250
    assert first.width() == task.width() == account.width() == 226


def test_visible_sidebar_animates_between_compact_and_expanded_widths() -> None:
    sidebar, first, _task, account = _build_sidebar()
    originalEffect = QApplication.isEffectEnabled(Qt.UIEffect.UI_General)

    try:
        QApplication.setEffectEnabled(Qt.UIEffect.UI_General, True)
        sidebar.resize(250, 720)
        sidebar.show()
        _app.processEvents()

        sidebar.setExpanded(False)
        QTest.qWait(80)
        assert 64 < sidebar.width() < 250

        QTest.qWait(260)
        assert sidebar.width() == 64
        assert first.width() == account.width() == 48
    finally:
        sidebar.hide()
        QApplication.setEffectEnabled(Qt.UIEffect.UI_General, originalEffect)


def test_sidebar_selection_and_task_badge_keep_navigation_protocol() -> None:
    sidebar, first, task, account = _build_sidebar()

    sidebar.setCurrentItem("home")
    assert sidebar.currentItem() is first
    assert first.isSelected
    assert account.isSelectable is False

    task.setBadgeCount(7)
    assert task.badgeCount() == 7
    task.setBadgeCount(0)
    assert task.badgeCount() == 0


def test_logged_out_account_does_not_show_membership_tier() -> None:
    account = AccountNavWidget()

    account.setLoggedIn(False)
    assert account._emailLabel.text() == "未登录"
    assert account._tierLabel.isHidden()
    assert "登录" in account._balanceLabel.text()
