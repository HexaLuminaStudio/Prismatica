# coding: utf-8
"""
插件市场界面
提供在线浏览和安装插件的功能
"""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit
from qfluentwidgets import (
    ScrollArea,
    CardWidget,
    PrimaryPushButton,
    LineEdit,
    FluentIcon,
    InfoBar,
    InfoBarIcon,
    InfoBarPosition,
    MessageBox,
)

from app.core.utils import logger


class PluginStoreCard(CardWidget):
    """插件市场卡片组件"""

    def __init__(self, plugin_info: dict, parent=None):
        super().__init__(parent)
        self.plugin_info = plugin_info
        self._initUI()

    def _initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # 顶部：名称、版本、价格
        topLayout = QHBoxLayout()
        
        nameLabel = QLabel(self.plugin_info.get("name", ""), self)
        nameLabel.setStyleSheet("font-size: 16px; font-weight: 600;")
        
        versionLabel = QLabel(f"v{self.plugin_info.get('version', '1.0.0')}", self)
        versionLabel.setStyleSheet("color: #888; font-size: 12px;")
        
        topLayout.addWidget(nameLabel)
        topLayout.addWidget(versionLabel)
        topLayout.addStretch()
        
        # 分类标签
        category = self.plugin_info.get("category", "tool")
        categoryLabel = QLabel(self._getCategoryName(category), self)
        categoryLabel.setStyleSheet("""
            background: #E6F7FF;
            color: #1890FF;
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 12px;
        """)
        topLayout.addWidget(categoryLabel)
        
        # 适用版本标签
        tier = self.plugin_info.get("tier", "free")
        tierLabel = QLabel("免费" if tier == "free" else "高级版", self)
        tierLabel.setStyleSheet("""
            background: #F6FFED;
            color: #52C41A;
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 12px;
        """ if tier == "free" else """
            background: #FFF7E6;
            color: #FA8C16;
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 12px;
        """)
        topLayout.addWidget(tierLabel)
        
        layout.addLayout(topLayout)

        # 作者
        authorLabel = QLabel(f"作者：{self.plugin_info.get('author', '未知')}", self)
        authorLabel.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(authorLabel)

        # 描述
        descLabel = QLabel(self.plugin_info.get("description", "暂无描述"), self)
        descLabel.setStyleSheet("color: #666; font-size: 13px;")
        descLabel.setWordWrap(True)
        layout.addWidget(descLabel)

        # 底部：安装按钮和详情链接
        bottomLayout = QHBoxLayout()
        bottomLayout.addStretch()

        # 安装按钮
        self.installBtn = PrimaryPushButton("安装", self)
        self.installBtn.setFixedWidth(80)
        self.installBtn.clicked.connect(self._onInstallClicked)
        bottomLayout.addWidget(self.installBtn)

        layout.addLayout(bottomLayout)

    def _getCategoryName(self, category: str) -> str:
        """获取分类中文名"""
        names = {
            "analysis": "分析插件",
            "visualization": "可视化插件",
            "corpus": "语料处理插件",
            "tool": "工具插件",
        }
        return names.get(category, category)

    def _onInstallClicked(self):
        """安装按钮点击"""
        InfoBar.info(
            "提示",
            "插件安装功能开发中",
            Qt.Orientation.Horizontal,
            True,
            2000,
            InfoBarPosition.TOP_RIGHT,
            self.window()
        )


class PluginStoreInterface(ScrollArea):
    """插件市场界面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("PluginStoreInterface")
        self._initUI()
        self._loadFeaturedPlugins()

    def _initUI(self):
        """初始化UI"""
        # 主容器
        self.scrollWidget = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.scrollWidget)
        self.vBoxLayout.setContentsMargins(20, 20, 20, 20)
        self.vBoxLayout.setSpacing(16)

        # 标题栏
        titleLayout = QHBoxLayout()
        
        titleLabel = QLabel("插件市场", self)
        titleLabel.setStyleSheet("font-size: 24px; font-weight: 600;")
        titleLayout.addWidget(titleLabel)
        
        self.vBoxLayout.addLayout(titleLayout)

        # 搜索栏
        searchLayout = QHBoxLayout()
        
        self.searchEdit = LineEdit(self)
        self.searchEdit.setPlaceholderText("搜索插件...")
        self.searchEdit.setFixedWidth(300)
        self.searchEdit.textChanged.connect(self._onSearchChanged)
        searchLayout.addWidget(self.searchEdit)
        searchLayout.addStretch()
        
        self.vBoxLayout.addLayout(searchLayout)

        # 分类筛选
        categoryLayout = QHBoxLayout()
        categoryLabel = QLabel("分类：", self)
        categoryLabel.setStyleSheet("color: #666;")
        categoryLayout.addWidget(categoryLabel)
        categoryLayout.addStretch()
        
        self.vBoxLayout.addLayout(categoryLayout)

        # 推荐插件标题
        featuredTitle = QLabel("推荐插件", self)
        featuredTitle.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.vBoxLayout.addWidget(featuredTitle)

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

    def _loadFeaturedPlugins(self):
        """加载推荐插件列表（模拟数据）"""
        # 清空现有卡片
        while self.pluginListLayout.count():
            item = self.pluginListLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 模拟插件数据（实际应从服务器获取）
        featured_plugins = [
            {
                "id": "com.prismatica.frequency",
                "name": "频率分析",
                "version": "1.0.0",
                "author": "Prismatica",
                "description": "对语料库进行词频统计分析，支持自定义词典",
                "category": "analysis",
                "tier": "free",
            },
            {
                "id": "com.prismatica.wordcloud",
                "name": "词云生成器",
                "version": "1.2.0",
                "author": "Prismatica",
                "description": "生成美观的词云图片，支持多种样式和配色方案",
                "category": "visualization",
                "tier": "free",
            },
            {
                "id": "com.prismatica.collocate",
                "name": "搭配分析",
                "version": "1.0.0",
                "author": "第三方开发者",
                "description": "分析词语搭配使用情况，生成搭配词表",
                "category": "analysis",
                "tier": "premium",
            },
            {
                "id": "com.prismatica.sentiment",
                "name": "情感分析",
                "version": "2.0.0",
                "author": "第三方开发者",
                "description": "对文本进行情感倾向分析，识别情感词汇",
                "category": "analysis",
                "tier": "premium",
            },
            {
                "id": "com.prismatica.corpus-cleaner",
                "name": "语料清洗器",
                "version": "1.1.0",
                "author": "Prismatica",
                "description": "批量清洗语料，支持去重、过滤、标准化",
                "category": "corpus",
                "tier": "free",
            },
        ]

        for plugin in featured_plugins:
            card = PluginStoreCard(plugin, self)
            self.pluginListLayout.addWidget(card)

    def _onSearchChanged(self, text: str):
        """搜索文本改变"""
        # 简单的过滤逻辑（实际应调用搜索API）
        pass
