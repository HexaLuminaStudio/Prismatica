# coding: utf-8
"""Prismatica 项目内置侧边抽屉回归测试。"""
from pathlib import Path

from PySide6.QtWidgets import QLabel, QWidget

from app.view.widgets.prismatica_drawer import PrismaticaDrawer


def testDrawerExpandsAndCollapsesWithinParent(qtbot) -> None:
    parent = QWidget()
    parent.resize(800, 600)
    qtbot.addWidget(parent)
    parent.show()

    view = QLabel("AI 解读", parent)
    view.setMinimumWidth(440)
    drawer = PrismaticaDrawer(view, parent)

    drawer.expand()
    qtbot.waitUntil(lambda: drawer.geometry() == drawer._shownGeometry(), timeout=1000)

    assert drawer.isVisibleTo(parent)
    assert drawer.isExpanded() is True
    assert drawer.geometry().right() == parent.rect().right()
    assert drawer.view is view

    drawer.collapse()
    qtbot.waitUntil(lambda: not drawer.isVisible(), timeout=1000)

    assert drawer.isExpanded() is False


def testDrawerTracksParentResizeAndOutsideClickSetting(qtbot) -> None:
    parent = QWidget()
    parent.resize(700, 500)
    qtbot.addWidget(parent)
    parent.show()
    drawer = PrismaticaDrawer(QLabel("内容"), parent)
    drawer.setHiddenOnClickOutside(False)
    drawer.expand()
    qtbot.waitUntil(lambda: drawer.geometry() == drawer._shownGeometry(), timeout=1000)

    parent.resize(900, 640)
    qtbot.waitUntil(lambda: drawer.geometry() == drawer._shownGeometry(), timeout=1000)

    assert drawer.height() == 640
    assert drawer.isHiddenOnClickedOutside() is False


def testProDrawerImportHasBeenRemoved() -> None:
    projectRoot = Path(__file__).resolve().parents[1]
    source = (
        projectRoot / "app/view/widgets/freq_analyzer/ai_insight_mixin.py"
    ).read_text(encoding="utf-8")

    assert "qfluentwidgetspro" not in source
    assert "DrawerPosition" not in source
