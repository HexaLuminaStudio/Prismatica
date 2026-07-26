# coding: utf-8
"""
AI 解读抽屉内部视图（PRD-001 REQ-AI-001）

结构（自上而下）：
    [ 标题"AI 研究助理 - {面板名}" + 关闭按钮 ]
    [ 风格下拉 | 模型名 CaptionLabel ]
    [ 流式输出 QTextBrowser (只读) ]
    [ 错误 / 置信度标签 ]
    [ 按钮行: 复制 / 重新生成 / 有帮助 / 需改进 ]

内容渲染策略：
    - 流式阶段：QTextBrowser 以纯文本增量显示，O(1) 写入
    - 结束阶段：统一调用 setMarkdown() 渲染为富文本
    - DeepSeek-R1 / Qwen3 等模型可能输出 <think>...</think> 思考块，
      实时累积 raw buffer，每 token 后用正则统一剥离，跨 chunk 不会出错

参考官方 Demo 写法（test/PySide6-Fluent-Widgets-Pro-Examples-v0.12.0/examples/dialog_flyout/drawer/demo.py）：
    - 顶层 QWidget + QVBoxLayout 命名 vBoxLayout
    - 标题行 QHBoxLayout 命名 topLayout
    - 关闭按钮命名 closeButton（用于 emit closeRequested）

注意：本视图不直接控制 Drawer 的展开/收起；
Drawer 由宿主 widget 创建，外部连接 closeRequested / regenerateRequested。
"""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TransparentToolButton,
    TextBrowser,
)

from app.core.utils import cfg, logger, qconfig


# 风格选项（与 cfg.AiInsightStyle 选项保持一致）
_STYLE_OPTIONS = ["学术", "通俗", "简洁"]

# 匹配 LLM 的思考块:
#  <think> ... </think>
# <|begin_of_thought|> ... <|end_of_thought|>
# <thinking> ... </thinking>
# 跨 chunk 也无所谓:每 token 都在累积的 rawBuffer 上整体匹配
_THINK_BLOCK_RE = re.compile(
    r"<think>.*?</think>"
    r"|<\|begin_of_thought\|>.*?<\|end_of_thought\|>"
    r"|<thinking>.*?</thinking>",
    re.DOTALL | re.IGNORECASE,
)


class AiInsightDrawerView(QWidget):
    """AI 解读抽屉内部视图

    Signals:
        regenerateRequested(): 用户点击「重新生成」
        closeRequested(): 用户点击关闭按钮
        styleChanged(str): 风格切换
    """

    regenerateRequested = Signal()
    closeRequested = Signal()
    styleChanged = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setObjectName("AiInsightDrawerView")
        self.setMinimumWidth(420)
        self.setMinimumHeight(400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # 参照 demo.py 命名的两个核心容器
        self.vBoxLayout = QVBoxLayout(self)
        self.topLayout = QHBoxLayout()

        self.titleLabel = SubtitleLabel("AI 研究助理", self)
        self.closeButton = TransparentToolButton(FluentIcon.CLOSE, self)

        self.styleLabel = BodyLabel("风格:", self)
        self.styleCombo = ComboBox(self)

        self.modelLabel = CaptionLabel("", self)

        # 用 QTextBrowser 替代 QPlainTextEdit:支持 setMarkdown() 富文本渲染
        self.outputEdit = TextBrowser(self)
        self.statusLabel = CaptionLabel("", self)

        self.copyBtn = PushButton("复制", self)
        self.thumbUpBtn = PushButton("有帮助", self)
        self.thumbDownBtn = PushButton("需改进", self)
        self.regenerateBtn = PrimaryPushButton("重新生成", self)

        # 流式渲染内部状态:raw 为 LLM 真实输出,visible 为已剥离 think 后的可见文本
        # 两者用累积 buffer 思路保证跨 chunk 的 think 标签能被正确剥离
        self._rawBuffer: str = ""
        self._visibleBuffer: str = ""

        self._initWidget()
        self._initLayout()
        self._connectSignals()

        # 初始:流式输出为空、按钮禁用
        self._setRunning(False)

    # ------------------------------------------------------------------
    # 部件属性(参照 demo.py 命名规范:_initWidget / _initLayout)
    # ------------------------------------------------------------------
    def _initWidget(self) -> None:
        """初始化部件属性"""
        # 关闭按钮:固定 36x36,小图标
        self.closeButton.setFixedSize(36, 36)
        self.closeButton.setIconSize(QSize(12, 12))

        # 风格下拉
        self.styleCombo.addItems(_STYLE_OPTIONS)
        currentStyle = qconfig.get(cfg.AiInsightStyle) or "学术"
        if currentStyle in _STYLE_OPTIONS:
            self.styleCombo.setCurrentText(currentStyle)

        # 模型名显示
        self.modelLabel.setStyleSheet("color: #888; font-size: 12px;")
        self.modelLabel.setText(self._modelText())

        # 流式输出区:QTextBrowser
        # openExternalLinks 关闭,避免点击 Markdown 链接时打开浏览器
        self.outputEdit.setOpenExternalLinks(False)
        self.outputEdit.setOpenLinks(False)
        self.outputEdit.setPlaceholderText("点击「重新生成」开始 AI 解读…")
        outputFont = QFont()
        outputFont.setPointSize(11)
        # self.outputEdit.setFont(outputFont)
        # # 文档默认打开方式:富文本(让 setMarkdown 走 Markdown 解析路径)
        # self.outputEdit.document().setDefaultStyleSheet(
        #     "p{margin:4px 0;}h1,h2,h3{font-weight:bold;}"
        #     "code{background:#f4f4f5;padding:0 4px;border-radius:3px;}"
        #     "pre{background:#f4f4f5;padding:6px;border-radius:4px;}"
        #     "table{border-collapse:collapse;}"
        #     "th,td{border:1px solid #ccc;padding:2px 6px;}"
        #     "ul,ol{margin:4px 0;padding-left:20px;}"
        # )

        # 状态 / 错误标签
        self.statusLabel.setStyleSheet("color: #888; font-size: 11px;")
        self.statusLabel.setWordWrap(True)

        # 复制按钮
        self.copyBtn.setIcon(FluentIcon.DOCUMENT)
        self.copyBtn.setEnabled(False)

        # 重新生成按钮
        self.regenerateBtn.setIcon(FluentIcon.SYNC)

        # 反馈按钮
        self.thumbUpBtn.setEnabled(False)
        self.thumbDownBtn.setEnabled(False)

    def _initLayout(self) -> None:
        """初始化布局(参照 demo.py 边距 16/12/8/16)"""
        self.vBoxLayout.setContentsMargins(16, 12, 8, 16)
        self.vBoxLayout.setSpacing(8)

        # 标题行:titleLabel + stretch + closeButton
        self.topLayout.addWidget(self.titleLabel)
        self.topLayout.addStretch(1)
        self.topLayout.addWidget(self.closeButton)
        self.vBoxLayout.addLayout(self.topLayout)

        # 控制行:风格 + 拉伸 + 模型名
        ctrlLayout = QHBoxLayout()
        ctrlLayout.addWidget(self.styleLabel)
        ctrlLayout.addWidget(self.styleCombo)
        ctrlLayout.addStretch(1)
        ctrlLayout.addWidget(self.modelLabel)
        self.vBoxLayout.addLayout(ctrlLayout)

        # 输出区
        self.vBoxLayout.addWidget(self.outputEdit, 1)

        # 状态行
        self.vBoxLayout.addWidget(self.statusLabel)

        # 按钮行:复制 / 反馈 / 拉伸 / 重新生成
        btnLayout = QHBoxLayout()
        btnLayout.addWidget(self.copyBtn)
        btnLayout.addWidget(self.thumbUpBtn)
        btnLayout.addWidget(self.thumbDownBtn)
        btnLayout.addStretch(1)
        btnLayout.addWidget(self.regenerateBtn)
        self.vBoxLayout.addLayout(btnLayout)

    def _connectSignals(self) -> None:
        self.closeButton.clicked.connect(self.closeRequested)
        self.regenerateBtn.clicked.connect(self._onRegenerateClicked)
        self.copyBtn.clicked.connect(self._onCopyClicked)
        self.thumbUpBtn.clicked.connect(self._onThumbUp)
        self.thumbDownBtn.clicked.connect(self._onThumbDown)
        self.styleCombo.currentTextChanged.connect(self.styleChanged)

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    def setPanelTitle(self, panelName: str) -> None:
        """设置标题,例如 'AI 研究助理 - 词频分析'"""
        self.titleLabel.setText(f"AI 研究助理 - {panelName}")

    def setStreamText(self, chunk: str, tokenUsage: int = 0) -> None:
        """增量追加一段流式 token

        - 在累积的 _rawBuffer 上做 think 剥离,保证跨 chunk 标签拼接正确
        - 正常情况:把"新出现的可见部分"以纯文本增量写入,O(n) 不卡 UI
        - 特殊情况:刚闭合 think 块时,newVisible 反而比 _visibleBuffer 短,
          此时需要清空 widget 并重新插入全部可见文本
        """
        if not chunk:
            return
        self._rawBuffer += chunk
        newVisible = self._stripThink(self._rawBuffer)
        if len(newVisible) < len(self._visibleBuffer):
            # think 块刚刚闭合:整体重渲染
            self.outputEdit.setPlainText(newVisible)
            self._visibleBuffer = newVisible
            self._scrollToEnd()
            return
        delta = newVisible[len(self._visibleBuffer) :]
        if delta:
            self.outputEdit.insertPlainText(delta)
            self._visibleBuffer = newVisible
            self._scrollToEnd()

    def setFinalText(self, text: str) -> None:
        """流式结束后:统一做 think 剥离 + Markdown 渲染

        - 用 setMarkdown() 一次性把可见文本渲染为富文本
        - 结束时 QTextBrowser 自身支持回退到 HTML
        """
        if text is None:
            text = ""
        self._rawBuffer = text
        self._visibleBuffer = self._stripThink(text)
        # 渲染为 Markdown(QTextBrowser 内部走 markdown → html → 渲染)
        try:
            self.outputEdit.setMarkdown(self._visibleBuffer)
        except Exception:
            # 兜底:Markdown 解析失败时显示纯文本
            self.outputEdit.setPlainText(self._visibleBuffer)
        self._scrollToEnd()
        self._setRunning(False)
        self._setCompleted()

    def clearText(self) -> None:
        """清空输出(开始新一轮解读前)"""
        self.outputEdit.clear()
        self._rawBuffer = ""
        self._visibleBuffer = ""
        self.statusLabel.setText("")
        self.statusLabel.setStyleSheet("color: #888; font-size: 11px;")

    def setError(self, msg: str) -> None:
        """显示错误信息,并启用「重新生成」"""
        self.statusLabel.setText(f"⚠ {msg}")
        self.statusLabel.setStyleSheet("color: #d33; font-size: 11px;")
        self._setRunning(False)
        self._setFailed()
        logger.warning(f"[AiInsightDrawerView] {msg}")

    def setStatus(self, msg: str) -> None:
        """设置一般状态文本(灰色)"""
        self.statusLabel.setText(msg)
        self.statusLabel.setStyleSheet("color: #888; font-size: 11px;")

    def setRunning(self, running: bool) -> None:
        """切换运行状态"""
        self._setRunning(running)

    def setStyle(self, style: str) -> None:
        """外部更新风格下拉"""
        if style in _STYLE_OPTIONS:
            self.styleCombo.setCurrentText(style)

    def setModelName(self, modelName: str) -> None:
        """外部更新模型名显示"""
        self.modelLabel.setText(f"模型: {modelName}")

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _stripThink(text: str) -> str:
        """去除 LLM 输出中的思考块

        支持的标签:
            <think> ... </think>
            <|begin_of_thought|> ... <|end_of_thought|>
            <thinking> ... </thinking>
        """
        if not text:
            return ""
        cleaned = _THINK_BLOCK_RE.sub("", text)
        # 去掉可能被空 think 块遗留下的多余空行
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    def _scrollToEnd(self) -> None:
        """滚动到输出区底部"""
        bar = self.outputEdit.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def _setRunning(self, running: bool) -> None:
        if running:
            self.statusLabel.setText("AI 思考中…")
            self.statusLabel.setStyleSheet("color: #888; font-size: 11px;")
            self.regenerateBtn.setEnabled(False)
            self.regenerateBtn.setText("生成中…")
            self.copyBtn.setEnabled(False)
            self.thumbUpBtn.setEnabled(False)
            self.thumbDownBtn.setEnabled(False)
        else:
            self.regenerateBtn.setEnabled(True)
            self.regenerateBtn.setText("重新生成")

    def _setCompleted(self) -> None:
        """完成态:显示置信度徽章 + 启用复制 / 反馈按钮"""
        self.statusLabel.setText("● 中置信度（基于现有数据，但解读仅供参考）")
        self.statusLabel.setStyleSheet("color: #1a7f37; font-size: 11px;")
        self.copyBtn.setEnabled(True)
        self.thumbUpBtn.setEnabled(True)
        self.thumbDownBtn.setEnabled(True)

    def _setFailed(self) -> None:
        """失败态:禁用复制 / 反馈;保留「重新生成」"""
        self.copyBtn.setEnabled(False)
        self.thumbUpBtn.setEnabled(False)
        self.thumbDownBtn.setEnabled(False)

    def _modelText(self) -> str:
        """当前模型显示(与 AI 聊天共用 cfg.AiModelChat)"""
        return f"模型: {qconfig.get(cfg.AiModelChat) or 'deepseek-chat'}"

    # ------------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------------
    def _onRegenerateClicked(self) -> None:
        self.regenerateRequested.emit()

    def _onCopyClicked(self) -> None:
        # 复制为 Markdown 源文本(用户拿到可以直接粘到 Notion / Typora / Word)
        text = self._visibleBuffer or self.outputEdit.toPlainText()
        if not text:
            return
        try:
            clipboard = QGuiApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
                self.setStatus("已复制为 Markdown 源文本")
        except Exception as e:
            logger.warning(f"[AiInsightDrawerView] 复制失败: {e}")
            self.setError(f"复制失败: {e}")

    def _onThumbUp(self) -> None:
        logger.info("[AiInsightDrawerView] 反馈: 有帮助")
        self.setStatus("已记录正向反馈（MVP 阶段不持久化）")

    def _onThumbDown(self) -> None:
        logger.info("[AiInsightDrawerView] 反馈: 需改进")
        self.setStatus("已记录负向反馈（MVP 阶段不持久化）")
