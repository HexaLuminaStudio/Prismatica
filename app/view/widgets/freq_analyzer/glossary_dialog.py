# coding: utf-8
"""
术语解释弹窗

根据当前子面板的 routeKey,从 glossary 模块取出对应术语列表,
以滚动卡片形式展示。

设计要点:
    - 使用 MessageBoxBase 复用 qfluentwidgets 的弹窗样式
    - 标题 + 副标题固定显示在顶部,术语列表装入 ScrollArea
    - ScrollArea 设最大高度,超出时自动出现垂直滚动条,术语不会被挤压
    - 弹窗整体高度上限,保证按钮始终可见,避免弹窗被内容撑爆屏幕
    - 关闭按钮重写 yesButton 文案为「关闭」
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    MessageBoxBase,
    ScrollArea,
    StrongBodyLabel,
)

from .glossary import getGlossaryFor, getPanelDisplayName
from app.view.widgets.prismatica_theme import setThemeRole


# 弹窗尺寸常量(集中管理,方便调整)
_DIALOG_WIDTH = 540
_DIALOG_MAX_HEIGHT = 660
_TERM_LIST_MAX_HEIGHT = 460


class GlossaryDialog(MessageBoxBase):
    """术语解释弹窗

    Args:
        routeKey: 当前子面板的 key
        parent:   父窗口
    """

    def __init__(self, routeKey: str, parent=None):
        # P0-fix 兼容:parent=None 时回退到 activeWindow / 临时 widget
        if parent is None:
            from PySide6.QtWidgets import QApplication, QWidget as _Q

            app = QApplication.instance()
            if app is not None:
                parent = app.activeWindow()
            if parent is None:
                parent = _Q()
                parent.resize(800, 600)
        super().__init__(parent=parent)

        self._routeKey = routeKey
        self._buildUi()
        self._wireEvents()

    def _buildUi(self):
        panelName = getPanelDisplayName(self._routeKey)
        self.titleLabel = StrongBodyLabel(f"「{panelName}」术语说明", self)
        self.titleLabel.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.viewLayout.addWidget(self.titleLabel)

        # 副标题
        self.subtitleLabel = CaptionLabel(
            "以下是该子页面涉及的主要专业术语及释义。", self
        )
        setThemeRole(self.subtitleLabel, "muted")
        self.viewLayout.addWidget(self.subtitleLabel)
        self.viewLayout.addSpacing(4)

        # ---- 术语列表(放进 ScrollArea 防止术语过多时挤压)----
        terms = getGlossaryFor(self._routeKey)
        if not terms:
            emptyLabel = BodyLabel("该子页面暂无术语解释。", self)
            setThemeRole(emptyLabel, "muted")
            self.viewLayout.addWidget(emptyLabel)
            self._termsScrollArea = None
        else:
            self._termsScrollArea = self._buildTermsScrollArea(terms)
            self.viewLayout.addWidget(self._termsScrollArea)

            # 统计条
            countLabel = CaptionLabel(f"共 {len(terms)} 条术语", self)
            setThemeRole(countLabel, "muted")
            self.viewLayout.addWidget(countLabel)

        # ---- 弹窗尺寸约束 ----
        # 宽度固定;高度上限保证按钮始终可见且不超出屏幕
        self.widget.setFixedWidth(_DIALOG_WIDTH)
        self.widget.setMaximumHeight(_DIALOG_MAX_HEIGHT)

    def _buildTermsScrollArea(self, terms) -> ScrollArea:
        """构造承载术语列表的 ScrollArea"""
        scrollArea = ScrollArea(self.widget)
        scrollArea.setWidgetResizable(True)
        scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scrollArea.setMaximumHeight(_TERM_LIST_MAX_HEIGHT)
        # 透明背景,与弹窗融合
        scrollArea.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )

        # 内部容器(装 ScrollArea 的 widget)
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        containerLayout = QVBoxLayout(container)
        containerLayout.setContentsMargins(0, 0, 0, 0)
        containerLayout.setSpacing(6)

        for term, definition in terms:
            containerLayout.addWidget(self._makeTermRow(term, definition, container))

        # 加 stretch 让术语靠顶部排列,不被散开
        containerLayout.addStretch(1)

        scrollArea.setWidget(container)
        return scrollArea

    def _makeTermRow(self, term: str, definition: str, parent: QWidget) -> QWidget:
        """构造一条术语卡片:术语名 + 释义"""
        row = QWidget(parent)
        rowLayout = QVBoxLayout(row)
        rowLayout.setContentsMargins(0, 4, 0, 4)
        rowLayout.setSpacing(2)

        # 术语标题(加粗)
        termLabel = StrongBodyLabel(term, row)
        setThemeRole(termLabel, "accent", "font-size: 13px;")
        termLabel.setWordWrap(True)
        rowLayout.addWidget(termLabel)

        # 释义
        defLabel = BodyLabel(definition, row)
        defLabel.setWordWrap(True)
        setThemeRole(defLabel, "muted", "font-size: 12px;")
        rowLayout.addWidget(defLabel)

        return row

    def _wireEvents(self):
        """按钮文案调整为「关闭」"""
        self.yesButton.setText("关闭")
        self.cancelButton.setVisible(False)
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self.accept)


def showGlossaryDialog(routeKey: str, parent=None) -> GlossaryDialog:
    """显示指定子面板的术语解释弹窗(便捷函数)"""
    dialog = GlossaryDialog(routeKey, parent=parent)
    dialog.setWindowTitle("术语说明")
    dialog.exec()
    return dialog
