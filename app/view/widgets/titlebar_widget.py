from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout
from qframelesswindow import TitleBar
from qfluentwidgets import qconfig

from app.view.widgets.project_switcher_widget import ProjectSwitcher
from app.view.widgets.prismatica_theme import shellPalette


class CustomTitleBar(TitleBar):
    """Title bar with icon and title"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.hBoxLayout.removeWidget(self.minBtn)
        self.hBoxLayout.removeWidget(self.maxBtn)
        self.hBoxLayout.removeWidget(self.closeBtn)

        # add window icon
        self.iconLabel = QLabel(self)
        self.iconLabel.setFixedSize(20, 20)
        self.hBoxLayout.insertSpacing(0, 15)
        self.hBoxLayout.insertWidget(
            1,
            self.iconLabel,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.window().windowIconChanged.connect(self.setIcon)

        # add title label
        self.titleLabel = QLabel(self)
        self.hBoxLayout.insertWidget(
            2,
            self.titleLabel,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.hBoxLayout.insertSpacing(2, 10)
        self.titleLabel.setObjectName("titleLabel")
        titleFont = QFont("Segoe UI")
        titleFont.setFamilies(
            ["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"]
        )
        titleFont.setPixelSize(14)
        titleFont.setWeight(QFont.Weight.DemiBold)
        self.titleLabel.setFont(titleFont)
        self.window().windowTitleChanged.connect(self.setTitle)

        # PRD-002:项目切换器(放到标题栏右侧)
        self.projectSwitcher = ProjectSwitcher(self)
        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.addWidget(
            self.projectSwitcher,
            0,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
        )
        self.hBoxLayout.addSpacing(20)

        self.vBoxLayout = QVBoxLayout()
        self.buttonLayout = QHBoxLayout()
        self.buttonLayout.setSpacing(0)
        self.buttonLayout.setContentsMargins(0, 0, 0, 0)
        self.buttonLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.buttonLayout.addWidget(self.minBtn)
        self.buttonLayout.addWidget(self.maxBtn)
        self.buttonLayout.addWidget(self.closeBtn)
        self.vBoxLayout.addLayout(self.buttonLayout)
        self.vBoxLayout.addStretch(1)
        self.hBoxLayout.addLayout(self.vBoxLayout, 0)
        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)

    def setTitle(self, title):
        self.titleLabel.setText(title)
        self.titleLabel.adjustSize()

    def setIcon(self, icon):
        self.iconLabel.setPixmap(QIcon(icon).pixmap(20, 20))

    def _applyTheme(self) -> None:
        palette = shellPalette()
        self.titleLabel.setStyleSheet(
            f"QLabel#titleLabel {{ color: {palette.text.name()}; "
            "background: transparent; padding: 0; }}"
        )
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        palette = shellPalette()
        painter = QPainter(self)
        painter.fillRect(self.rect(), palette.titleBar)
        painter.setPen(QPen(palette.border, 1))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
