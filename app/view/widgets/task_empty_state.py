# coding: utf-8
"""任务页面统一空状态组件。"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from qfluentwidgets import (
    FluentIcon,
    IconWidget,
    PrimaryPushButton,
    PushButton,
    qconfig,
)

from app.view.widgets.prismatica_theme import shellPalette


class TaskEmptyState(QFrame):
    """与任务管理设计稿一致的居中空状态卡片。"""

    def __init__(
        self,
        title: str,
        description: str,
        *,
        icon=FluentIcon.INBOX if hasattr(FluentIcon, "INBOX") else FluentIcon.MESSAGE,
        primaryText: str = "",
        primaryAction: Optional[Callable[[], None]] = None,
        secondaryText: str = "",
        secondaryAction: Optional[Callable[[], None]] = None,
        shortcutText: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("taskEmptyCard")
        self.setMinimumHeight(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 42, 32, 42)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        iconWrap = QFrame(self)
        iconWrap.setObjectName("emptyIconWrap")
        iconWrap.setFixedSize(72, 72)
        iconLayout = QHBoxLayout(iconWrap)
        iconLayout.setContentsMargins(0, 0, 0, 0)
        iconLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        iconWidget = IconWidget(icon, iconWrap)
        iconWidget.setFixedSize(34, 34)
        iconLayout.addWidget(iconWidget)
        layout.addWidget(iconWrap, 0, Qt.AlignmentFlag.AlignHCenter)

        titleLabel = QLabel(title, self)
        titleLabel.setObjectName("emptyTitle")
        titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(titleLabel, 0, Qt.AlignmentFlag.AlignHCenter)

        descriptionLabel = QLabel(description, self)
        descriptionLabel.setObjectName("emptyDescription")
        descriptionLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        descriptionLabel.setWordWrap(True)
        descriptionLabel.setMaximumWidth(540)
        layout.addWidget(descriptionLabel, 0, Qt.AlignmentFlag.AlignHCenter)

        if primaryText or secondaryText:
            buttonRow = QHBoxLayout()
            buttonRow.setSpacing(8)
            buttonRow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if primaryText:
                primaryButton = PrimaryPushButton(
                    FluentIcon.DOWNLOAD, primaryText, self
                )
                primaryButton.setFixedHeight(34)
                if primaryAction is not None:
                    primaryButton.clicked.connect(primaryAction)
                buttonRow.addWidget(primaryButton)
            if secondaryText:
                secondaryButton = PushButton(
                    FluentIcon.GLOBE, secondaryText, self
                )
                secondaryButton.setFixedHeight(34)
                if secondaryAction is not None:
                    secondaryButton.clicked.connect(secondaryAction)
                buttonRow.addWidget(secondaryButton)
            layout.addSpacing(2)
            layout.addLayout(buttonRow)

        if shortcutText:
            shortcutLabel = QLabel(shortcutText, self)
            shortcutLabel.setObjectName("shortcutHint")
            shortcutLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addSpacing(4)
            layout.addWidget(shortcutLabel, 0, Qt.AlignmentFlag.AlignHCenter)

        self._applyStyle()
        qconfig.themeChangedFinished.connect(self._applyStyle)

    def _applyStyle(self, *_args) -> None:
        palette = shellPalette()
        self.setStyleSheet(
            f"""
            QFrame#taskEmptyCard {{
                background: {palette.surface.name()};
                border: 1px solid {palette.border.name()};
                border-radius: 10px;
            }}
            QFrame#emptyIconWrap {{
                background: {palette.surfaceAlt.name()};
                border: none;
                border-radius: 36px;
            }}
            QLabel {{ background: transparent; border: none; }}
            QLabel#emptyTitle {{
                color: {palette.text.name()};
                font-size: 18px;
                font-weight: 600;
            }}
            QLabel#emptyDescription {{
                color: {palette.mutedText.name()};
                font-size: 13px;
                line-height: 1.6;
            }}
            QLabel#shortcutHint {{
                color: {palette.accentText.name()};
                background: {palette.accentSurface.name()};
                border-radius: 5px;
                padding: 4px 9px;
                font-size: 12px;
            }}
            """
        )
