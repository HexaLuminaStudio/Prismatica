# coding: utf-8
"""
语料统计插件
提供基于 jieba 的分词和词频统计功能
"""

import os
import sys

# 添加本地 lib 路径到 sys.path
pluginDir = os.path.dirname(os.path.abspath(__file__))
# jieba 在 lib/jieba/ 目录下
jiebaPath = os.path.join(pluginDir, "lib", "jieba")
if os.path.exists(jiebaPath) and jiebaPath not in sys.path:
    sys.path.insert(0, jiebaPath)

# 尝试导入 jieba
try:
    import jieba # type: ignore

    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False
    jieba = None

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QScrollArea,
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
        "description": "对文本进行分词和词频统计分析",
        "author": "Prismatica",
        "category": "tool",
        "permissions": [],
        "dependencies": {"python": ["jieba"]},
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
        import os

        pluginDir = os.path.dirname(os.path.abspath(__file__))
        if os.path.exists(os.path.join(pluginDir, "icon.png")):
            return "icon.png"
        elif os.path.exists(os.path.join(pluginDir, "icon.svg")):
            return "icon.svg"
        return ""

    def getInterface(self) -> QWidget:
        """获取插件界面组件"""
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

        # jieba 状态提示
        if not JIEBA_AVAILABLE:
            warningLabel = BodyLabel("⚠️ jieba 库未加载，分词功能不可用", self)
            warningLabel.setStyleSheet("color: #FAAD14; font-size: 12px;")
            mainLayout.addWidget(warningLabel)

        # 输入区域
        inputCard = CardWidget(self)
        inputLayout = QVBoxLayout(inputCard)
        inputLayout.setContentsMargins(12, 12, 12, 12)

        inputLabel = BodyLabel("请输入文本：", self)
        inputLayout.addWidget(inputLabel)

        self.textInput = QTextEdit(self)
        self.textInput.setPlaceholderText("在此输入或粘贴文本...")
        self.textInput.setMinimumHeight(120)
        inputLayout.addWidget(self.textInput)

        mainLayout.addWidget(inputCard)

        # 统计按钮
        btnLayout = QHBoxLayout()
        btnLayout.addStretch()

        self.analyzeBtn = PrimaryPushButton("分词分析", self)
        self.analyzeBtn.setIcon(FluentIcon.SEARCH)
        self.analyzeBtn.clicked.connect(self._onAnalyze)
        btnLayout.addWidget(self.analyzeBtn)

        mainLayout.addLayout(btnLayout)

        # 基本统计结果
        basicCard = CardWidget(self)
        basicLayout = QVBoxLayout(basicCard)
        basicLayout.setContentsMargins(12, 12, 12, 12)
        basicLayout.setSpacing(8)

        basicLabel = BodyLabel("基本统计：", self)
        basicLabel.setStyleSheet("font-weight: 600;")
        basicLayout.addWidget(basicLabel)

        self.basicResultLabel = BodyLabel("字符数：0  |  词数：0  |  行数：0", self)
        self.basicResultLabel.setStyleSheet(
            "font-size: 13px; padding: 8px; background: #F5F5F5; border-radius: 4px;"
        )
        basicLayout.addWidget(self.basicResultLabel)
        mainLayout.addWidget(basicCard)

        # 分词结果
        segCard = CardWidget(self)
        segLayout = QVBoxLayout(segCard)
        segLayout.setContentsMargins(12, 12, 12, 12)
        segLayout.setSpacing(8)

        segLabel = BodyLabel("分词结果：", self)
        segLabel.setStyleSheet("font-weight: 600;")
        segLayout.addWidget(segLabel)

        # 分词结果滚动区域
        segScrollArea = QScrollArea(self)
        segScrollArea.setWidgetResizable(True)
        segScrollArea.setStyleSheet("border: none;")
        segScrollArea.setMaximumHeight(150)

        self.segResultWidget = QWidget()
        self.segResultLayout = QVBoxLayout(self.segResultWidget)
        self.segResultLayout.setContentsMargins(0, 0, 0, 0)

        self.segResultLabel = BodyLabel("请输入文本后点击「分词分析」", self)
        self.segResultLabel.setWordWrap(True)
        self.segResultLabel.setStyleSheet(
            "font-size: 12px; padding: 8px; background: #F5F5F5; border-radius: 4px; color: #666;"
        )
        self.segResultLayout.addWidget(self.segResultLabel)
        segScrollArea.setWidget(self.segResultWidget)
        segLayout.addWidget(segScrollArea)
        mainLayout.addWidget(segCard)

        # 词频统计
        freqCard = CardWidget(self)
        freqLayout = QVBoxLayout(freqCard)
        freqLayout.setContentsMargins(12, 12, 12, 12)
        freqLayout.setSpacing(8)

        freqLabel = BodyLabel("词频统计（Top 10）：", self)
        freqLabel.setStyleSheet("font-weight: 600;")
        freqLayout.addWidget(freqLabel)

        self.freqResultLabel = BodyLabel("暂无数据", self)
        self.freqResultLabel.setStyleSheet(
            "font-size: 12px; padding: 8px; background: #F5F5F5; border-radius: 4px;"
        )
        freqLayout.addWidget(self.freqResultLabel)
        mainLayout.addWidget(freqCard)

        mainLayout.addStretch()

    def _onAnalyze(self):
        """分析按钮点击"""
        text = self.textInput.toPlainText()

        if not text.strip():
            self.basicResultLabel.setText("字符数：0  |  词数：0  |  行数：0")
            self.segResultLabel.setText("请输入文本后点击「分词分析」")
            self.freqResultLabel.setText("暂无数据")
            return

        # 基本统计
        charCount = len(text)
        lineCount = len([l for l in text.splitlines() if l.strip()])

        # 分词
        if JIEBA_AVAILABLE and jieba:
            # 使用 jieba 分词
            words = list(jieba.cut(text))
            words = [w.strip() for w in words if w.strip()]
            wordCount = len(words)

            # 分词结果
            segText = " / ".join(words[:100])  # 最多显示100个词
            if len(words) > 100:
                segText += f" ... (共 {len(words)} 个词)"
            self.segResultLabel.setText(segText if segText else "未识别到词语")

            # 词频统计
            wordFreq = {}
            for word in words:
                wordFreq[word] = wordFreq.get(word, 0) + 1

            # 按频率排序
            sortedWords = sorted(wordFreq.items(), key=lambda x: x[1], reverse=True)
            top10 = sortedWords[:10]

            if top10:
                freqText = "\n".join([f"  {word}: {count}次" for word, count in top10])
                self.freqResultLabel.setText(freqText)
            else:
                self.freqResultLabel.setText("暂无数据")
        else:
            # 回退到简单统计
            wordCount = len(text.split())
            self.segResultLabel.setText("jieba 未加载，无法分词")
            self.freqResultLabel.setText("jieba 未加载，无法统计词频")

        self.basicResultLabel.setText(
            f"字符数：{charCount}  |  词数：{wordCount}  |  行数：{lineCount}"
        )
