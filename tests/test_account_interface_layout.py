# coding: utf-8
"""个人中心关键控件布局回归测试。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from qfluentwidgets import FluentIcon

from app.view.account_interface import AccountInterface, _LeadingIconPrimaryPushButton


def testRedeemButtonKeepsIconAtLeadingInset(qtbot) -> None:
    button = _LeadingIconPrimaryPushButton(FluentIcon.TAG, "兑换码")
    button.resize(526, 40)
    qtbot.addWidget(button)

    iconRect = button._iconRect()

    assert iconRect.left() == button.ICON_INSET
    assert iconRect.center().y() == button.height() / 2


def testRedeemButtonMirrorsIconForRightToLeftLayout(qtbot) -> None:
    button = _LeadingIconPrimaryPushButton(FluentIcon.TAG, "兑换码")
    button.resize(526, 40)
    button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    qtbot.addWidget(button)

    iconRect = button._iconRect()

    assert button.width() - iconRect.right() == button.ICON_INSET


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
