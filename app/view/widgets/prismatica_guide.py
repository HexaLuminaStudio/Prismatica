# coding: utf-8
"""Prismatica 项目内置多页引导窗口。"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.utils import qconfig
from app.view.widgets.prismatica_theme import ACCENT, shellPalette


class _PageIndicator(QLabel):
    """以文本显示当前引导页和总页数。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pageCount = 0
        self._currentIndex = 0
        self.setObjectName("guidePageIndicator")

    def setPageNumber(self, pageCount: int) -> None:
        self._pageCount = max(0, int(pageCount))
        self._refresh()

    def setCurrentIndex(self, index: int) -> None:
        self._currentIndex = max(0, int(index))
        self._refresh()

    def _refresh(self) -> None:
        current = min(self._currentIndex + 1, self._pageCount) if self._pageCount else 0
        self.setText(f"{current} / {self._pageCount}")
        self.setAccessibleName(f"引导进度：第 {current} 页，共 {self._pageCount} 页")


class PrismaticaGuideWindow(QDialog):
    """不依赖 QFluentWidgets Pro 的多页首次启动引导窗口。"""

    appStarted = Signal()
    currentIndexChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("prismaticaGuideWindow")
        self.setSizeGripEnabled(True)

        self.stackedWidget = QStackedWidget(self)
        self.stackedWidget.setMinimumWidth(0)
        self.stackedWidget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.pipsPager = _PageIndicator(self)
        self.previousButton = QPushButton("上一步", self)
        self.nextButton = QPushButton("下一步", self)
        self.launchButton = QPushButton("完成", self)
        self.previousButton.setObjectName("guideSecondaryButton")
        self.nextButton.setObjectName("guidePrimaryButton")
        self.launchButton.setObjectName("guidePrimaryButton")

        for button in (
            self.previousButton,
            self.nextButton,
            self.launchButton,
        ):
            button.setMinimumSize(92, 36)
        self.previousButton.setAccessibleName("返回上一个引导步骤")
        self.nextButton.setAccessibleName("进入下一个引导步骤")
        self.launchButton.setAccessibleName("完成首次启动引导")

        self.bottomLayout = QHBoxLayout()
        self.bottomLayout.setContentsMargins(0, 0, 0, 0)
        self.bottomLayout.setSpacing(10)
        self.bottomLayout.addWidget(self.pipsPager)
        self.bottomLayout.addStretch(1)
        self.bottomLayout.addWidget(self.previousButton)
        self.bottomLayout.addWidget(self.nextButton)
        self.bottomLayout.addWidget(self.launchButton)

        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(20, 20, 20, 18)
        self.vBoxLayout.setSpacing(14)
        self.vBoxLayout.addWidget(self.stackedWidget, 1)
        self.vBoxLayout.addLayout(self.bottomLayout)

        self.previousButton.clicked.connect(self.previousPage)
        self.launchButton.clicked.connect(self.appStarted.emit)
        self.stackedWidget.currentChanged.connect(self._onCurrentIndexChanged)
        qconfig.themeChanged.connect(self._applyTheme)

        self._applyTheme()
        self._updateNavigationState()

    def addPage(self, widget: QWidget) -> None:
        """添加一个引导页面。"""
        self.stackedWidget.addWidget(widget)
        self.pipsPager.setPageNumber(self.pageCount())
        if self.pageCount() == 1:
            self.setCurrentIndex(0)
        self._updateNavigationState()

    def nextPage(self) -> None:
        """进入下一页。"""
        self.setCurrentIndex(min(self.currentIndex() + 1, self.pageCount() - 1))

    def previousPage(self) -> None:
        """返回上一页。"""
        self.setCurrentIndex(max(0, self.currentIndex() - 1))

    def setCurrentIndex(self, index: int) -> None:
        """切换到指定页面。"""
        if 0 <= int(index) < self.pageCount():
            self.stackedWidget.setCurrentIndex(int(index))

    def currentPage(self) -> QWidget | None:
        return self.stackedWidget.currentWidget()

    def currentIndex(self) -> int:
        return self.stackedWidget.currentIndex()

    def pageCount(self) -> int:
        return self.stackedWidget.count()

    def indexOf(self, page: QWidget) -> int:
        return self.stackedWidget.indexOf(page)

    def _onCurrentIndexChanged(self, index: int) -> None:
        self.pipsPager.setCurrentIndex(index)
        self._updateNavigationState()
        self.currentIndexChanged.emit(index)

    def _updateNavigationState(self) -> None:
        index = self.currentIndex()
        pageCount = self.pageCount()
        isLastPage = pageCount > 0 and index == pageCount - 1
        self.previousButton.setEnabled(index > 0)
        self.nextButton.setVisible(pageCount > 0 and not isLastPage)
        self.launchButton.setVisible(isLastPage)

    def _applyTheme(self, *_args) -> None:
        palette = shellPalette()
        hoverColor = ACCENT.lighter(108).name()
        pressedColor = ACCENT.darker(112).name()
        self.setStyleSheet(
            f"""
            QDialog#prismaticaGuideWindow {{
                background-color: {palette.window.name()};
                color: {palette.text.name()};
            }}
            QLabel#guidePageIndicator {{
                color: {palette.mutedText.name()};
                font-size: 13px;
            }}
            QPushButton#guideSecondaryButton {{
                background-color: {palette.surface.name()};
                color: {palette.text.name()};
                border: 1px solid {palette.border.name()};
                border-radius: 8px;
                padding: 6px 14px;
            }}
            QPushButton#guideSecondaryButton:hover {{
                background-color: {palette.surfaceAlt.name()};
            }}
            QPushButton#guidePrimaryButton {{
                background-color: {ACCENT.name()};
                color: #FFFFFF;
                border: 1px solid {ACCENT.name()};
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 600;
            }}
            QPushButton#guidePrimaryButton:hover {{
                background-color: {hoverColor};
                border-color: {hoverColor};
            }}
            QPushButton#guidePrimaryButton:pressed {{
                background-color: {pressedColor};
                border-color: {pressedColor};
            }}
            QPushButton:disabled {{
                background-color: {palette.surfaceAlt.name()};
                color: {palette.mutedText.name()};
                border-color: {palette.border.name()};
            }}
            """
        )


__all__ = ["PrismaticaGuideWindow"]
