# coding: utf-8

import json
from typing import Literal, Dict, Any
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QFrame, QScrollArea
from PySide6.QtCore import Qt

from qfluentwidgets import (
    MessageBoxBase,
    SubtitleLabel,
    BodyLabel,
    CardWidget,
    ImageLabel,
)

from app.core.services import GetTotalWorker, GlobalGetTotalWorker


# 参数标签映射（中文化）
PARAM_LABELS = {
    # HSK参数
    "keyword": "关键词",
    "nationality": "国籍",
    "hsk_level": "HSK等级",
    "essay_title": "作文题目",
    "score": "分数",
    "写作文体": "写作文体",
    "作文长度": "作文长度",
    "性别": "性别",
    "第一语言": "第一语言",
    "第二语言": "第二语言",
    "page": "页码",
    "per_page": "每页数量",
    "title": "作文题目",
    "level": "作文等级",
    "nation": "国籍",
    # Global参数
    "keystr": "关键字",
    "tablename": "语料类型",
    "shou": "首字符串",
    "kaishi": "前词",
    "num": "距离",
    "jieshu": "后词",
    "wei": "尾字符串",
    "orderstr": "排序方向",
    "showlenght": "检索后字符数",
    "tag": "标签",
    "txt": "文本",
    "mothertongue": "母语",
    "shkgrade": "HSK等级",
    "ext1": "汉语水平",
    "authornationality": "作者国籍",
    "ft": "语料类型",
    "corp_org_id": "机构ID",
    "isDeptCheck": "部门检查",
}


def formatParams(params: Dict[str, Any]) -> str:
    """格式化参数字典为易读字符串"""
    if not params:
        return "无"

    lines = []
    for key, value in params.items():
        label = PARAM_LABELS.get(key, key)
        if isinstance(value, list):
            value_str = ", ".join(str(v) for v in value)
        elif isinstance(value, bool):
            value_str = "是" if value else "否"
        else:
            value_str = str(value)
        lines.append(f"{label}: {value_str}")

    return "\n".join(lines)


class ParamDisplay(QFrame):
    """参数展示框，支持多行显示"""

    def __init__(self, params: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.params = params
        self.setMinimumHeight(60)
        self.setMaximumHeight(150)
        self.setupUi(params)

    def setupUi(self, params: Dict[str, Any]):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        # 遍历参数，创建每一行的显示
        for key, value in params.items():
            if key == "page":
                continue  # 跳过分页参数

            label = PARAM_LABELS.get(key, key)
            itemLayout = QHBoxLayout()
            itemLayout.setSpacing(8)

            # 标签
            labelWidget = BodyLabel(f"{label}：", self)
            labelWidget.setFixedWidth(80)
            labelWidget.setStyleSheet("color: #888;")

            # 值
            if isinstance(value, list):
                value_str = ", ".join(str(v) for v in value)
            elif isinstance(value, bool):
                value_str = "是" if value else "否"
            else:
                value_str = str(value)

            valueWidget = BodyLabel(value_str, self)
            valueWidget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            valueWidget.setWordWrap(True)

            itemLayout.addWidget(labelWidget)
            itemLayout.addWidget(valueWidget, 1)
            layout.addLayout(itemLayout)

        self.setLayout(layout)


class InfoItem(QFrame):
    """信息展示项：图标 + 标签 + 内容"""

    def __init__(self, iconPath: str, label: str, value: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setupUi(iconPath, label, value)

    def setupUi(self, iconPath: str, label: str, value: str):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(16)

        # 图标
        self.iconLabel = ImageLabel(iconPath, self)
        self.iconLabel.scaledToHeight(32)
        self.iconLabel.scaledToWidth(32)
        self.iconLabel.setFixedSize(32, 32)
        self.iconLabel.setScaledContents(True)

        # 标签
        self.labelWidget = BodyLabel(label, self)
        self.labelWidget.setFixedWidth(100)
        self.labelWidget.setStyleSheet("color: #666;")

        # 内容
        self.valueWidget = BodyLabel(value, self)
        self.valueWidget.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        layout.addWidget(self.iconLabel)
        layout.addWidget(self.labelWidget)
        layout.addWidget(self.valueWidget, 1)  # stretch=1 使内容区域自动扩展

        self.setLayout(layout)

    def updateValue(self, value: str):
        """更新内容值"""
        self.valueWidget.setText(value)


class DownloadApplyWidget(MessageBoxBase):
    def __init__(
        self,
        downloadType: Literal["Hsk", "Global"],
        infoDict: Dict[str, Any],
        parent=None,
    ):
        super().__init__(parent=parent)

        # 保存参数
        self.downloadType = downloadType
        self.infoDict = infoDict

        # 根据下载类型选择图标
        iconName = "Hsk" if downloadType == "Hsk" else "Global"
        iconPath = f":/app/icons/{iconName}.svg"

        # 标题区域
        self.titleLabel = SubtitleLabel("申请下载任务", self)
        self.titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 信息卡片容器
        self.cardWidget = CardWidget(self)
        self.cardLayout = QVBoxLayout(self.cardWidget)
        self.cardLayout.setContentsMargins(8, 8, 8, 8)
        self.cardLayout.setSpacing(8)

        # 下载类型信息项
        self.downloadTypeItem = InfoItem(
            iconPath, "下载来源：", downloadType, self.cardWidget
        )

        # 下载参数标签
        self.paramsLabel = BodyLabel("下载参数：", self.cardWidget)
        self.paramsLabel.setStyleSheet("color: #666; font-weight: bold;")

        # 下载参数展示
        self.paramsDisplay = ParamDisplay(
            self.infoDict.get("payload", {}), self.cardWidget
        )

        # 语料数量信息项
        self.numberItem = InfoItem(
            ":app/icons/Number.svg", "语料数量：", "查询中...", self.cardWidget
        )

        # 添加到卡片布局
        self.cardLayout.addWidget(self.downloadTypeItem)
        self.cardLayout.addWidget(self.paramsLabel)
        self.cardLayout.addWidget(self.paramsDisplay)
        self.cardLayout.addWidget(self.numberItem)

        # 添加到视图布局
        self.viewLayout.addWidget(self.titleLabel, 0, Qt.AlignmentFlag.AlignCenter)
        self.viewLayout.addWidget(self.cardWidget, 0, Qt.AlignmentFlag.AlignCenter)

        # 设置按钮文本
        self.yesButton.setText("确认")
        self.yesButton.setEnabled(False)
        self.cancelButton.setText("取消")

        # 设置对话框宽度
        self.widget.setFixedWidth(480)

        # 启动查询线程
        self.worker = None
        self.startQuery()

    def startQuery(self):
        """启动查询线程"""
        if self.worker is None:
            # 根据下载类型选择不同的Worker
            if self.downloadType == "Hsk":
                self.worker = GetTotalWorker(self.infoDict)
            else:
                self.worker = GlobalGetTotalWorker(self.infoDict)
            self.worker.finished.connect(self.onQueryFinished)
            self.worker.failed.connect(self.onQueryFailed)
            self.worker.start()

    def onQueryFinished(self, total: int):
        """查询成功回调"""
        if total > 0:
            self.numberItem.updateValue(f"{total} 条")
            self.yesButton.setEnabled(True)
        else:
            self.numberItem.updateValue("未找到数据")
            self.yesButton.setEnabled(False)
        self.cleanupWorker()

    def onQueryFailed(self, errorMsg: str):
        """查询失败回调"""
        self.numberItem.updateValue(f"查询失败")
        self.yesButton.setEnabled(False)
        self.cleanupWorker()

    def cleanupWorker(self):
        """清理工作线程"""
        if self.worker:
            self.worker.stop()
            self.worker.wait(1000)
            self.worker.deleteLater()
            self.worker = None

    def closeEvent(self, event):
        """对话框关闭时清理资源"""
        self.cleanupWorker()
        super().closeEvent(event)
