# coding: utf-8
"""Prismatica 项目内置加载按钮回归测试。"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton

from app.view.widgets.prismatica_button import PrismaticaProgressButton


def testProgressButtonExposesLoadingLifecycle(qtbot) -> None:
    button = PrismaticaProgressButton("登录")
    qtbot.addWidget(button)

    button.load()
    firstAngle = button._spinnerAngle
    QTimer.singleShot(70, lambda: None)
    qtbot.wait(90)

    assert isinstance(button, QPushButton)
    assert button.loading is True
    assert button.isLoading() is True
    assert button._spinnerTimer.isActive() is True
    assert button._spinnerAngle != firstAngle
    assert button.accessibleDescription() == "正在处理"

    button.normal()

    assert button.loading is False
    assert button.isLoading() is False
    assert button._spinnerTimer.isActive() is False
    assert button.accessibleDescription() == ""


def testProgressButtonTracksAccessibleName(qtbot) -> None:
    button = PrismaticaProgressButton("登录")
    qtbot.addWidget(button)

    button.setText("正在连接云端…")

    assert button.accessibleName() == "正在连接云端…"
