# coding: utf-8
"""
插件管理界面
"""

import os
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from qfluentwidgets import (
    ScrollArea,
    CardWidget,
    PrimaryPushButton,
    SwitchButton,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    BodyLabel,
)

from app.core.plugin import getPluginManager, getPluginConfig, PluginMetadata


class PluginCard(CardWidget):
    """插件卡片组件"""

    def __init__(self, metadata: PluginMetadata, parent=None):
        super().__init__(parent)
        self.metadata = metadata
        self._manager = getPluginManager()
        self._initUi()

    def _initUi(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # 顶部：图标、名称和版本
        topLayout = QHBoxLayout()

        # 插件图标
        iconLabel = QLabel(self)
        iconLabel.setFixedSize(40, 40)
        iconPath = self.metadata.iconPath
        if iconPath and os.path.exists(iconPath):
            icon = QIcon(iconPath)
        else:
            icon = QIcon(":app/icons/Plugin.svg")
        iconLabel.setPixmap(icon.pixmap(40, 40))
        topLayout.addWidget(iconLabel)

        # 名称和版本
        nameVersionLayout = QVBoxLayout()
        nameVersionLayout.setSpacing(2)

        nameLabel = BodyLabel(self.metadata.name, self)
        nameLabel.setStyleSheet("font-size: 16px; font-weight: 600;")

        versionLabel = BodyLabel(f"v{self.metadata.version}", self)
        versionLabel.setStyleSheet("color: #888; font-size: 12px;")

        nameVersionLayout.addWidget(nameLabel)
        nameVersionLayout.addWidget(versionLabel)
        topLayout.addLayout(nameVersionLayout)

        topLayout.addStretch()

        # 分类标签
        categoryLabel = BodyLabel(self.metadata.category, self)
        categoryLabel.setStyleSheet(
            """
            background: #E6F7FF;
            color: #1890FF;
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 12px;
        """
        )
        topLayout.addWidget(categoryLabel)

        layout.addLayout(topLayout)

        # 描述
        descLabel = BodyLabel(self.metadata.description or "无描述", self)
        descLabel.setStyleSheet("color: #666; font-size: 13px;")
        descLabel.setWordWrap(True)
        layout.addWidget(descLabel)

        # 底部：启用开关
        bottomLayout = QHBoxLayout()

        self.enableSwitch = SwitchButton(self)
        self.enableSwitch.setChecked(self.metadata.enabled)
        self.enableSwitch.checkedChanged.connect(self._onEnableChanged)

        bottomLayout.addWidget(self.enableSwitch)
        bottomLayout.addStretch()

        layout.addLayout(bottomLayout)

    def _onEnableChanged(self, checked: bool):
        """启用状态改变"""
        if checked:
            # 1. 先检查依赖
            depsOk, missingDeps = self._manager.checkDependencies(self.metadata)
            if not depsOk:
                self.enableSwitch.setChecked(False)
                depList = ", ".join(missingDeps)
                InfoBar.error(
                    "依赖缺失",
                    f"{self.metadata.name} 缺少依赖: {depList}",
                    Qt.Orientation.Horizontal,
                    True,
                    5000,
                    InfoBarPosition.TOP_RIGHT,
                    self.window(),
                )
                return

            # 2. 依赖满足，显示权限设置
            from .widgets.plugin_settings_dialog import PluginSettingsDialog

            dialog = PluginSettingsDialog(self.metadata, self.window())
            if dialog.exec():
                # 3. 用户确认后启用插件
                success = self._manager.enablePlugin(self.metadata.pluginId)
                if success:
                    InfoBar.success(
                        "启用成功",
                        f"{self.metadata.name} 已启用",
                        Qt.Orientation.Horizontal,
                        True,
                        3000,
                        InfoBarPosition.TOP_RIGHT,
                        self.window(),
                    )
                else:
                    self.enableSwitch.setChecked(False)
                    InfoBar.error(
                        "启用失败",
                        f"{self.metadata.name} 启用失败",
                        Qt.Orientation.Horizontal,
                        True,
                        3000,
                        InfoBarPosition.TOP_RIGHT,
                        self.window(),
                    )
            else:
                self.enableSwitch.setChecked(False)
        else:
            self._manager.disablePlugin(self.metadata.pluginId)
            InfoBar.info(
                "已禁用",
                f"{self.metadata.name} 已禁用",
                Qt.Orientation.Horizontal,
                True,
                3000,
                InfoBarPosition.TOP_RIGHT,
                self.window(),
            )


class PluginInterface(ScrollArea):
    """插件管理界面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("PluginInterface")
        self._manager = getPluginManager()
        self._initUi()
        self.loadPlugins()

    def _initUi(self):
        """初始化UI"""
        # 主容器
        self.scrollWidget = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.scrollWidget)
        self.vBoxLayout.setContentsMargins(20, 20, 20, 20)
        self.vBoxLayout.setSpacing(16)

        # 标题栏
        titleLayout = QHBoxLayout()

        titleLabel = BodyLabel("插件管理", self)
        titleLabel.setStyleSheet("font-size: 24px; font-weight: 600;")
        titleLayout.addWidget(titleLabel)
        titleLayout.addStretch()

        # 刷新按钮
        self.refreshBtn = PrimaryPushButton("刷新插件", self)
        self.refreshBtn.setIcon(FluentIcon.SYNC)
        self.refreshBtn.clicked.connect(self._onRefreshClicked)
        titleLayout.addWidget(self.refreshBtn)

        self.vBoxLayout.addLayout(titleLayout)

        # 统计信息
        self.statsLabel = BodyLabel("", self)
        self.statsLabel.setStyleSheet("color: #888; font-size: 13px;")
        self.vBoxLayout.addWidget(self.statsLabel)

        # 插件列表容器
        self.pluginListWidget = QWidget(self)
        self.pluginListLayout = QVBoxLayout(self.pluginListWidget)
        self.pluginListLayout.setContentsMargins(0, 0, 0, 0)
        self.pluginListLayout.setSpacing(12)

        self.vBoxLayout.addWidget(self.pluginListWidget)
        self.vBoxLayout.addStretch()

        # 设置滚动区域
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setStyleSheet("background: transparent; border: none;")

    def loadPlugins(self):
        """加载插件列表"""
        # 清空现有卡片
        while self.pluginListLayout.count():
            item = self.pluginListLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 扫描并加载插件
        plugins = self._manager.loadPlugins()

        if not plugins:
            # 空状态
            emptyLabel = BodyLabel("暂未安装任何插件", self)
            emptyLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
            emptyLabel.setStyleSheet("color: #999; font-size: 14px; padding: 40px;")
            self.pluginListLayout.addWidget(emptyLabel)
            self.statsLabel.setText("")
        else:
            # 按分类排序
            plugins.sort(key=lambda x: (x.category, x.name))

            for plugin in plugins:
                card = PluginCard(plugin, self)
                self.pluginListLayout.addWidget(card)

            # 更新统计
            enabledCount = len([p for p in plugins if p.enabled])
            self.statsLabel.setText(
                f"共 {len(plugins)} 个插件，已启用 {enabledCount} 个"
            )

    def _onRefreshClicked(self):
        """刷新按钮点击"""
        self.loadPlugins()
        InfoBar.info(
            "已刷新",
            f"已加载 {len(self._manager.getAllPlugins())} 个插件",
            Qt.Orientation.Horizontal,
            True,
            2000,
            InfoBarPosition.TOP_RIGHT,
            self,
        )

    def refreshPlugins(self):
        """刷新插件列表（供外部调用）"""
        self.loadPlugins()
