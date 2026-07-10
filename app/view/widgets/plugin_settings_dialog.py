# coding: utf-8
"""
插件权限设置对话框
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from qfluentwidgets import (
    MessageBoxBase,
    SubtitleLabel,
    BodyLabel,
    CardWidget,
    ScrollArea,
)

from app.core.plugin import PluginMetadata


class PermissionCard(CardWidget):
    """权限卡片"""

    def __init__(self, permission: dict, parent=None):
        super().__init__(parent)
        self.permission = permission
        self._initUI()

    def _initUI(self):
        mainLayout = QVBoxLayout(self)
        mainLayout.setContentsMargins(12, 8, 12, 8)

        # 顶部：名称和风险等级
        topLayout = QHBoxLayout()

        nameLabel = BodyLabel(self.permission.get("name", ""), self)
        nameLabel.setStyleSheet("font-weight: 600;")
        topLayout.addWidget(nameLabel)

        risk = self.permission.get("risk", "low")
        riskText = {"low": "低风险", "medium": "中风险", "high": "高风险"}.get(
            risk, "低风险"
        )
        riskLabel = BodyLabel(riskText, self)
        riskColor = {"low": "#52C41A", "medium": "#FAAD14", "high": "#F5222D"}.get(
            risk, "#52C41A"
        )
        riskLabel.setStyleSheet(f"color: {riskColor}; font-size: 12px;")
        topLayout.addWidget(riskLabel, 0, Qt.AlignmentFlag.AlignRight)

        mainLayout.addLayout(topLayout)

        # 描述
        descLabel = BodyLabel(self.permission.get("description", ""), self)
        descLabel.setStyleSheet("color: #666; font-size: 12px;")
        descLabel.setWordWrap(True)
        mainLayout.addWidget(descLabel)


class PluginSettingsDialog(MessageBoxBase):
    """插件权限设置对话框"""

    # 权限定义
    PERMISSIONS = [
        {
            "id": "file:read",
            "name": "读取文件",
            "description": "读取用户下载目录中的文件",
            "risk": "low",
        },
        {
            "id": "file:write",
            "name": "写入文件",
            "description": "在用户下载目录中创建或修改文件",
            "risk": "medium",
        },
        {
            "id": "corpus:read",
            "name": "读取语料",
            "description": "读取已导入的语料库数据",
            "risk": "low",
        },
        {
            "id": "corpus:write",
            "name": "修改语料",
            "description": "修改或删除语料库数据",
            "risk": "medium",
        },
        {
            "id": "network",
            "name": "网络请求",
            "description": "发起网络请求访问外部服务",
            "risk": "medium",
        },
        {
            "id": "system",
            "name": "系统命令",
            "description": "执行系统级别的操作命令",
            "risk": "high",
        },
    ]

    def __init__(self, metadata: PluginMetadata, parent=None):
        super().__init__(parent)
        self.metadata = metadata
        self.requestedPermissions = set(metadata.manifest.permissions)
        self._initUI()

    def _initUI(self):
        # 标题
        titleLabel = SubtitleLabel(f"「{self.metadata.name}」权限设置", self)
        titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.viewLayout.addWidget(titleLabel, 0, Qt.AlignmentFlag.AlignCenter)
        self.viewLayout.addSpacing(8)

        # 说明
        descLabel = BodyLabel("插件请求以下权限以正常运行：", self)
        descLabel.setWordWrap(True)
        descLabel.setStyleSheet("color: #666;")
        self.viewLayout.addWidget(descLabel)
        self.viewLayout.addSpacing(8)

        # 权限列表容器
        permScroll = ScrollArea(self)
        permScroll.setWidgetResizable(True)
        permScroll.setMaximumHeight(280)
        permScroll.setMinimumHeight(150)
        permScroll.setStyleSheet("border: none; background: transparent;")

        permContainer = QWidget()
        permLayout = QVBoxLayout(permContainer)
        permLayout.setSpacing(8)

        # 基础权限说明
        baseTitleLabel = BodyLabel("基础权限（始终授予）：", self)
        baseTitleLabel.setStyleSheet("color: #888; font-size: 12px;")
        permLayout.addWidget(baseTitleLabel)

        baseCard = CardWidget(self)
        baseCardLayout = QVBoxLayout(baseCard)
        baseCardLayout.setContentsMargins(12, 8, 12, 8)
        baseInfo = BodyLabel("• 读取软件界面信息\n• 访问已下载的Excel文件路径", self)
        baseInfo.setStyleSheet("color: #666; font-size: 12px;")
        baseCardLayout.addWidget(baseInfo)
        permLayout.addWidget(baseCard)

        # 需要授权的权限
        if self.requestedPermissions:
            permTitleLabel = BodyLabel("需要授权的权限：", self)
            permTitleLabel.setStyleSheet("color: #888; font-size: 12px;")
            permLayout.addWidget(permTitleLabel)

            for permId in self.requestedPermissions:
                perm = next((p for p in self.PERMISSIONS if p["id"] == permId), None)
                if perm:
                    card = PermissionCard(perm, self)
                    permLayout.addWidget(card)

        # 如果没有请求任何权限
        if not self.requestedPermissions:
            noPermLabel = BodyLabel("此插件不需要额外权限", self)
            noPermLabel.setStyleSheet("color: #52C41A; padding: 8px;")
            permLayout.addWidget(noPermLabel)

        permLayout.addStretch()
        permScroll.setWidget(permContainer)
        self.viewLayout.addWidget(permScroll)

        # 高风险警告
        hasHighRisk = any(
            p.get("risk") == "high"
            for p in self.PERMISSIONS
            if p.get("id") in self.requestedPermissions
        )
        if hasHighRisk:
            warningLabel = BodyLabel("⚠️ 此插件包含高风险权限，请确保插件来源可靠", self)
            warningLabel.setStyleSheet(
                """
                color: #F5222D;
                background: #FFF1F0;
                padding: 8px 12px;
                border-radius: 4px;
                font-size: 12px;
            """
            )
            self.viewLayout.addWidget(warningLabel)

        # 设置对话框属性
        self.widget.setFixedWidth(420)
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")
