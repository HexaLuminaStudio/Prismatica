# coding: utf-8
"""
语料统计插件
提供简单的语料统计分析功能
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QPushButton,
)
from qfluentwidgets import CardWidget, BodyLabel, PrimaryPushButton, FluentIcon

from app.core.plugin.base import PluginBase


class Plugin(PluginBase):
    """语料统计插件类"""

    # 插件清单
    manifest = {
        "id": "com.prismatica.example.info",
        "name": "语料统计",
        "version": "1.0.0",
        "apiVersion": "1.0",
        "description": "对文本进行简单的统计分析，包括字符数、词数、行数统计",
        "author": "Prismatica",
        "category": "tool",
        "permissions": [],
        "dependencies": {},
        "entry": "plugin.py",
        "minAppVersion": "1.0.0",
    }

    def onLoad(self) -> bool:
        """插件加载"""
        print("[Plugin] 语料统计插件已加载")
        return True

    def onActivate(self):
        """插件激活"""
        print("[Plugin] 语料统计插件已激活")

    def onDeactivate(self):
        """插件停用"""
        print("[Plugin] 语料统计插件已停用")

    def onUnload(self):
        """插件卸载"""
        print("[Plugin] 语料统计插件已卸载")

    def getIconPath(self) -> str:
        """获取插件图标路径"""
        # 优先使用 png，其次 svg
        import os

        pluginDir = os.path.dirname(os.path.abspath(__file__))
        if os.path.exists(os.path.join(pluginDir, "icon.png")):
            return "icon.png"
        elif os.path.exists(os.path.join(pluginDir, "icon.svg")):
            return "icon.svg"
        return ""

    def getInterface(self) -> QWidget:
        """
        获取插件界面组件

        Returns:
            插件的UI组件，如果插件没有界面则返回None
        """
        return CorpusStatsWidget()


class CorpusStatsWidget(QWidget):
    """语料统计界面组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._initUi()

    def _initUi(self):
        """初始化界面"""
        mainLayout = QVBoxLayout(self)
        mainLayout.setContentsMargins(16, 16, 16, 16)
        mainLayout.setSpacing(12)

        # 标题
        titleLabel = BodyLabel("语料统计工具", self)
        titleLabel.setStyleSheet("font-size: 18px; font-weight: 600;")
        mainLayout.addWidget(titleLabel)

        # 输入区域
        inputCard = CardWidget(self)
        inputLayout = QVBoxLayout(inputCard)
        inputLayout.setContentsMargins(12, 12, 12, 12)

        inputLabel = BodyLabel("请输入文本：", self)
        inputLayout.addWidget(inputLabel)

        self.textInput = QTextEdit(self)
        self.textInput.setPlaceholderText("在此输入或粘贴文本...")
        self.textInput.setMinimumHeight(150)
        inputLayout.addWidget(self.textInput)

        mainLayout.addWidget(inputCard)

        # 统计按钮
        btnLayout = QHBoxLayout()
        btnLayout.addStretch()

        self.analyzeBtn = PrimaryPushButton("分析", self)
        self.analyzeBtn.setIcon(FluentIcon.SEARCH)
        self.analyzeBtn.clicked.connect(self._onAnalyze)
        btnLayout.addWidget(self.analyzeBtn)

        mainLayout.addLayout(btnLayout)

        # 结果区域
        resultCard = CardWidget(self)
        resultLayout = QVBoxLayout(resultCard)
        resultLayout.setContentsMargins(12, 12, 12, 12)
        resultLayout.setSpacing(8)

        resultLabel = BodyLabel("统计结果：", self)
        resultLayout.addWidget(resultLabel)

        self.resultLabel = BodyLabel("字符数：0  |  词数：0  |  行数：0", self)
        self.resultLabel.setStyleSheet(
            "font-size: 14px; padding: 8px; background: #F5F5F5; border-radius: 4px;"
        )
        resultLayout.addWidget(self.resultLabel)

        mainLayout.addWidget(resultCard)
        mainLayout.addStretch()

    def _onAnalyze(self):
        """分析按钮点击"""
        text = self.textInput.toPlainText()

        charCount = len(text)
        wordCount = len(text.split()) if text.strip() else 0
        lineCount = (
            len([l for l in text.splitlines() if l.strip()]) if text.strip() else 0
        )

        self.resultLabel.setText(
            f"字符数：{charCount}  |  词数：{wordCount}  |  行数：{lineCount}"
        )
