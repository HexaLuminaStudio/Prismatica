# coding: utf-8
"""Prismatica 项目内置多页引导窗口回归测试。"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from app.view.widgets.prismatica_guide import PrismaticaGuideWindow


def testGuideWindowNavigatesPagesAndUpdatesActions(qtbot) -> None:
    window = PrismaticaGuideWindow()
    qtbot.addWidget(window)
    pages = [QLabel("欢迎"), QLabel("配置"), QLabel("完成")]
    for page in pages:
        window.addPage(page)

    assert window.pageCount() == 3
    assert window.currentPage() is pages[0]
    assert window.previousButton.isEnabled() is False
    assert window.nextButton.isHidden() is False
    assert window.launchButton.isHidden() is True
    assert window.pipsPager.text() == "1 / 3"

    window.nextPage()
    assert window.currentPage() is pages[1]
    assert window.previousButton.isEnabled() is True
    assert window.pipsPager.text() == "2 / 3"

    window.nextPage()
    assert window.currentPage() is pages[2]
    assert window.nextButton.isHidden() is True
    assert window.launchButton.isHidden() is False
    assert window.pipsPager.text() == "3 / 3"


def testGuideWindowLaunchButtonEmitsCompletion(qtbot) -> None:
    window = PrismaticaGuideWindow()
    qtbot.addWidget(window)
    window.addPage(QLabel("完成"))

    with qtbot.waitSignal(window.appStarted, timeout=500):
        qtbot.mouseClick(window.launchButton, Qt.MouseButton.LeftButton)


def testProGuideWindowImportHasBeenRemoved() -> None:
    projectRoot = Path(__file__).resolve().parents[1]
    source = (projectRoot / "app/view/widgets/guide_window.py").read_text(
        encoding="utf-8"
    )

    assert "qfluentwidgetspro" not in source
    assert "ProGuideWindow" not in source
