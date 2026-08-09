# coding: utf-8
"""HSK 作文检索结果详情抽屉。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    StrongBodyLabel,
    ToolButton,
)


class HskCorpusDetailDrawer(QFrame):
    """展示一条检索结果的真实元数据与本地镜像正文。"""

    closed = Signal()

    _META_FIELDS = (
        ("国籍", "国籍"),
        ("证书级别", "证书级别"),
        ("性别", "性别"),
        ("总字数", "总字数"),
        ("总词数", "总词数"),
        ("作文分数", "作文分数"),
        ("听力理解分数", "听力"),
        ("阅读理解分数", "阅读"),
        ("综合表达考试分数", "综合表达"),
        ("口试分数", "口试"),
    )

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("hskDetailDrawer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(300)
        self.setMaximumWidth(380)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        headerText = QVBoxLayout()
        headerText.setSpacing(2)
        title = StrongBodyLabel("作文详情", self)
        title.setObjectName("hskDetailHeading")
        headerText.addWidget(title)
        self.idLabel = CaptionLabel("选择一条结果查看详情", self)
        self.idLabel.setObjectName("hskDetailMuted")
        headerText.addWidget(self.idLabel)
        header.addLayout(headerText, 1)

        closeBtn = ToolButton(FluentIcon.CLOSE, self)
        closeBtn.setToolTip("关闭详情")
        closeBtn.setAccessibleName("关闭作文详情")
        closeBtn.clicked.connect(self.closed.emit)
        header.addWidget(closeBtn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.titleLabel = BodyLabel("", self)
        self.titleLabel.setObjectName("hskDetailTitle")
        self.titleLabel.setWordWrap(True)
        root.addWidget(self.titleLabel)

        metaTitle = CaptionLabel("语料元数据", self)
        metaTitle.setObjectName("hskDetailSectionLabel")
        root.addWidget(metaTitle)

        metaHost = QWidget(self)
        metaHost.setObjectName("hskDetailMeta")
        metaGrid = QGridLayout(metaHost)
        metaGrid.setContentsMargins(0, 0, 0, 0)
        metaGrid.setHorizontalSpacing(14)
        metaGrid.setVerticalSpacing(8)
        self._metaValueLabels: Dict[str, QLabel] = {}
        for index, (fieldName, displayName) in enumerate(self._META_FIELDS):
            rowIndex = index // 2
            columnOffset = (index % 2) * 2
            keyLabel = CaptionLabel(displayName, metaHost)
            keyLabel.setObjectName("hskDetailMuted")
            valueLabel = StrongBodyLabel("—", metaHost)
            valueLabel.setObjectName("hskDetailMetaValue")
            valueLabel.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            metaGrid.addWidget(keyLabel, rowIndex, columnOffset)
            metaGrid.addWidget(valueLabel, rowIndex, columnOffset + 1)
            self._metaValueLabels[fieldName] = valueLabel
        metaGrid.setColumnStretch(1, 1)
        metaGrid.setColumnStretch(3, 1)
        root.addWidget(metaHost)

        bodyHeader = QHBoxLayout()
        bodyHeader.setSpacing(8)
        bodyTitle = CaptionLabel("原始作文记录", self)
        bodyTitle.setObjectName("hskDetailSectionLabel")
        bodyHeader.addWidget(bodyTitle)
        bodyHeader.addStretch(1)
        self.bodyStateLabel = CaptionLabel("", self)
        self.bodyStateLabel.setObjectName("hskDetailBodyState")
        bodyHeader.addWidget(self.bodyStateLabel)
        root.addLayout(bodyHeader)

        self.bodyEdit = QPlainTextEdit(self)
        self.bodyEdit.setObjectName("hskDetailBody")
        self.bodyEdit.setReadOnly(True)
        self.bodyEdit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.bodyEdit.setAccessibleName("作文原始正文")
        root.addWidget(self.bodyEdit, 1)

    def setRecord(
        self,
        record: Dict[str, Any],
        localRecord: Optional[Dict[str, Any]] = None,
    ) -> None:
        """用真实检索记录刷新详情，不构造缺失正文。"""
        zwhao = str(record.get("作文母号") or "未提供")
        self.idLabel.setText(f"作文母号：{zwhao}")
        self.titleLabel.setText(str(record.get("作文题目") or "未命名作文"))

        for fieldName, valueLabel in self._metaValueLabels.items():
            value = record.get(fieldName)
            valueLabel.setText("—" if value in (None, "") else str(value))

        dataText = str((localRecord or {}).get("data") or "")
        if dataText:
            self.bodyStateLabel.setText("本地镜像")
            self.bodyStateLabel.setProperty("available", True)
            self.bodyEdit.setPlainText(dataText)
        else:
            self.bodyStateLabel.setText("正文未就绪")
            self.bodyStateLabel.setProperty("available", False)
            self.bodyEdit.setPlainText(
                "本地作文正文镜像中未找到这条记录。\n"
                "当前仍可查看检索库中的全部元数据。"
            )
        self.bodyStateLabel.style().unpolish(self.bodyStateLabel)
        self.bodyStateLabel.style().polish(self.bodyStateLabel)
        self.bodyEdit.moveCursor(QTextCursor.MoveOperation.Start)
