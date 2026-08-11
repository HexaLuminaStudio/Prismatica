# coding: utf-8
"""个人中心关键控件布局回归测试。"""

from __future__ import annotations

from app.view.account_interface import AccountInterface


def testDangerButtonsReserveTextSpaceForIcons(qtbot) -> None:
    interface = AccountInterface()
    qtbot.addWidget(interface)
    buttons = (
        interface._logoutButton,
        interface._overview._deleteAccountButton,
    )

    for button in buttons:
        reservedWidth = (
            button.minimumSizeHint().width()
            - button.fontMetrics().horizontalAdvance(button.text())
        )
        assert button.property("hasIcon") is True
        assert reservedWidth >= button.iconSize().width() + 24
