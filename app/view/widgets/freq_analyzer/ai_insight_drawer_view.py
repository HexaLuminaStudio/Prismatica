# coding: utf-8
"""
AI 解读抽屉内部视图（PRD-001 REQ-AI-001）

现代化视觉结构（自上而下）：
    ┌─────────────────────────────────────────────────┐
    │ [🪄] AI 研究助理 · 词频分析            [✕]     │  ← Header 卡（品牌色顶条）
    │ 模型: deepseek-chat                              │  ← CaptionLabel 副标题
    ├─────────────────────────────────────────────────┤
    │ 风格  [ 学术 ▼ ]            中置信度   🟡         │  ← 控制 + 置信度徽章
    ├─────────────────────────────────────────────────┤
    │                                                  │
    │   Markdown 富文本区 (打字机渲染)                  │  ← 主体内容
    │                                                  │
    ├─────────────────────────────────────────────────┤
    │ [ℹ] 解读仅供参考,请结合原始数据                │  ← 状态条
    ├─────────────────────────────────────────────────┤
    │ [👍 有用] [👎 需改进]   [📋 复制]  [🔄 重新生成] │  ← 反馈 + 操作
    └─────────────────────────────────────────────────┘

视觉规范：
    - 主题感知（自动浅/深色）
    - 流式阶段:ProgressRing + 状态文案 + 动态置信度条
    - 完成阶段:置信度徽章静态显示 + 按钮启用
    - 失败阶段:错误图标 + 错误文案 + 仅「重新生成」保留
    - 引用样式：Markdown 中的 [数据:...] 用 inline code 风格展示
    - 空状态：Hero Icon + 引导文案

参考 demo.py 命名：
    - 顶层 QWidget + vBoxLayout
    - 标题行 topLayout
    - 关闭按钮 closeButton（用于 emit closeRequested）
"""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    AvatarWidget,
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    IndeterminateProgressRing,
    PillPushButton,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TextBrowser,
    TransparentToolButton,
    isDarkTheme,
    themeColor,
)

from app.core.utils import cfg, logger, qconfig


# 风格选项（与 cfg.AiInsightStyle 选项保持一致）
_STYLE_OPTIONS = ["学术", "通俗", "简洁"]

# 匹配 LLM 的思考块:
#   跨 chunk 也无所谓:每 token 都在累积的 rawBuffer 上整体匹配
#   - 思考...结束思考 (DeepSeek-R1 等)
#   - <|begin_of_thought|>...<|end_of_thought|> (Qwen3 等)
#   - <thinking>...</thinking> (部分模型)
_THINK_BLOCK_RE = re.compile(
    r"思考.*?结束思考|"
    r"<\|begin_of_thought\|>.*?<\|end_of_thought\|>|"
    r"<thinking>.*?</thinking>",
    re.DOTALL | re.IGNORECASE,
)

# 主题感知的 Markdown 样式:列表/表格/代码块/段落/标题均统一配色
_MD_LIGHT = """
h1, h2, h3, h4 {{
    color: #1f1f1f;
    font-weight: 600;
    margin-top: 8px;
    margin-bottom: 4px;
}}
p {{ margin: 6px 0; line-height: 1.7; color: #2b2b2b; }}
strong {{ color: {theme}; font-weight: 600; }}
em {{ color: #555; font-style: normal; }}
code {{
    background: rgba(0, 0, 0, 0.06);
    color: {theme};
    padding: 1px 6px;
    border-radius: 4px;
    font-family: "Consolas", "Microsoft YaHei", monospace;
    font-size: 90%;
}}
pre {{
    background: rgba(0, 0, 0, 0.05);
    color: #1f1f1f;
    padding: 8px 12px;
    border-radius: 6px;
    border-left: 3px solid {theme};
}}
blockquote {{
    border-left: 3px solid {theme};
    background: rgba(0, 0, 0, 0.04);
    margin: 6px 0;
    padding: 6px 12px;
    color: #444;
}}
ul, ol {{ margin: 6px 0; padding-left: 22px; }}
li {{ line-height: 1.7; margin: 2px 0; }}
table {{
    border-collapse: collapse;
    margin: 8px 0;
}}
th, td {{
    border: 1px solid #d0d0d0;
    padding: 4px 10px;
    text-align: left;
}}
th {{ background: rgba(0, 0, 0, 0.04); font-weight: 600; }}
a {{ color: {theme}; text-decoration: none; }}
"""

_MD_DARK = """
h1, h2, h3, h4 {{
    color: #f5f5f5;
    font-weight: 600;
    margin-top: 8px;
    margin-bottom: 4px;
}}
p {{ margin: 6px 0; line-height: 1.7; color: #d8d8d8; }}
strong {{ color: {theme}; font-weight: 600; }}
em {{ color: #aaa; font-style: normal; }}
code {{
    background: rgba(255, 255, 255, 0.08);
    color: {theme};
    padding: 1px 6px;
    border-radius: 4px;
    font-family: "Consolas", "Microsoft YaHei", monospace;
    font-size: 90%;
}}
pre {{
    background: rgba(255, 255, 255, 0.06);
    color: #e6e6e6;
    padding: 8px 12px;
    border-radius: 6px;
    border-left: 3px solid {theme};
}}
blockquote {{
    border-left: 3px solid {theme};
    background: rgba(255, 255, 255, 0.04);
    margin: 6px 0;
    padding: 6px 12px;
    color: #bbb;
}}
ul, ol {{ margin: 6px 0; padding-left: 22px; }}
li {{ line-height: 1.7; margin: 2px 0; }}
table {{
    border-collapse: collapse;
    margin: 8px 0;
}}
th, td {{
    border: 1px solid #555;
    padding: 4px 10px;
    text-align: left;
}}
th {{ background: rgba(255, 255, 255, 0.06); font-weight: 600; }}
a {{ color: {theme}; text-decoration: none; }}
"""


def _buildMarkdownStyle() -> str:
    """按当前主题返回 Markdown CSS,主题色用 setThemeColor 后的全局色。"""
    style = _MD_DARK if isDarkTheme() else _MD_LIGHT
    # 取当前主题色,降级到 Fluent 默认蓝
    try:
        c = themeColor()
        hexColor = c.name() if hasattr(c, "name") else "#0078d4"
    except Exception:
        hexColor = "#0078d4"
    return style.format(theme=hexColor)


class _ConfidenceBadge(QWidget):
    """置信度徽章:圆点 + 文本,语义颜色随状态变化。"""

    LEVEL_HIGH = ("高", "#16a34a")  # 绿
    LEVEL_MID = ("中", "#eab308")  # 黄
    LEVEL_LOW = ("低", "#dc2626")  # 红
    LEVEL_RUNNING = ("生成中", "#3b82f6")  # 蓝
    LEVEL_IDLE = ("待解读", "#9ca3af")  # 灰

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.dotLabel = BodyLabel("●", self)
        self.textLabel = CaptionLabel("待解读", self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.dotLabel)
        layout.addWidget(self.textLabel)
        self._applyLevel(self.LEVEL_IDLE)

    def _applyLevel(self, levelTuple) -> None:
        label, color = levelTuple
        self.textLabel.setText(f"{label}置信度")
        self.dotLabel.setStyleSheet(f"color:{color}; font-size:14px;")
        self.textLabel.setStyleSheet(f"color:{color}; font-weight:600;")

    def setLevel(self, levelTuple) -> None:
        self._applyLevel(levelTuple)


class AiInsightDrawerView(QWidget):
    """AI 解读抽屉内部视图

    Signals:
        regenerateRequested(): 用户点击「重新生成」
        closeRequested(): 用户点击关闭按钮
        styleChanged(str): 风格切换
        feedbackGiven(int): 反馈信号(1=正向, 0=负向)
    """

    regenerateRequested = Signal()
    closeRequested = Signal()
    styleChanged = Signal(str)
    feedbackGiven = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self.setObjectName("AiInsightDrawerView")
        self.setMinimumWidth(440)
        self.setMinimumHeight(440)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # 参照 demo.py 的命名约定:顶层两个核心容器
        self.vBoxLayout = QVBoxLayout(self)
        self.topLayout = QHBoxLayout()
        # ---------- Header ----------
        self.avatar = AvatarWidget(":app/icons/Robot.svg", self)
        self.avatar.setRadius(18)
        self.titleLabel = SubtitleLabel("AI 研究助理", self)
        self.subtitleLabel = CaptionLabel(self._modelText(), self)
        self.closeButton = TransparentToolButton(FluentIcon.CLOSE, self)

        # ---------- 工具栏 ----------
        self.styleLabel = BodyLabel("解读风格", self)
        self.styleCombo = ComboBox(self)
        self.confidenceBadge = _ConfidenceBadge(self)

        # ---------- 主内容 ----------
        self.outputEdit = TextBrowser(self)
        self.progressRing = IndeterminateProgressRing(self)

        # ---------- 状态 ----------
        self.statusLabel = CaptionLabel("", self)

        # ---------- 操作 ----------
        self.thumbUpBtn = PillPushButton("👍 有用", self)
        self.thumbDownBtn = PillPushButton("👎 需改进", self)
        self.copyBtn = PushButton(FluentIcon.DOCUMENT, "复制", self)
        self.regenerateBtn = PrimaryPushButton(FluentIcon.SYNC, "重新生成", self)

        # 流式渲染内部状态
        self._rawBuffer: str = ""
        self._visibleBuffer: str = ""

        self._initWidget()
        self._initLayout()
        self._connectSignals()

        # 监听主题切换:刷新 Markdown 样式
        qconfig.themeChanged.connect(self._refreshMarkdownStyle)

        # 初始:空闲态
        self._setRunning(False)

    # ------------------------------------------------------------------
    # 部件属性
    # ------------------------------------------------------------------
    def _initWidget(self) -> None:
        """初始化部件属性/样式/尺寸"""
        # 关闭按钮
        self.closeButton.setFixedSize(32, 32)
        self.closeButton.setIconSize(QSize(12, 12))
        self.closeButton.setToolTip("关闭解读面板")

        # 标题/副标题
        self.subtitleLabel.setStyleSheet("color: rgba(0,0,0,55%); font-size: 11px;")

        # 风格下拉
        self.styleCombo.addItems(_STYLE_OPTIONS)
        self.styleCombo.setFixedWidth(110)
        currentStyle = qconfig.get(cfg.AiInsightStyle) or "学术"
        if currentStyle in _STYLE_OPTIONS:
            self.styleCombo.setCurrentText(currentStyle)

        # 流式输出区:QTextBrowser
        self.outputEdit.setOpenExternalLinks(False)
        self.outputEdit.setOpenLinks(False)
        self.outputEdit.setPlaceholderText(
            "点击右下角「重新生成」,让 AI 为本次分析生成可溯源、可证伪、可拒绝的研究解读。"
        )
        outputFont = self.outputEdit.font()
        outputFont.setPointSize(11)
        self.outputEdit.setFont(outputFont)
        # 应用 Markdown 样式
        self._refreshMarkdownStyle()

        # 进度环(流式阶段显示)
        self.progressRing.setFixedSize(14, 14)
        self.progressRing.setStrokeWidth(2)
        self.progressRing.hide()

        # 复制按钮
        self.copyBtn.setIconSize(QSize(14, 14))
        self.copyBtn.setEnabled(False)
        self.copyBtn.setToolTip(
            "复制为 Markdown 源文本,可直接粘到 Notion / Typora / Word"
        )

        # 重新生成按钮(主操作)
        self.regenerateBtn.setIconSize(QSize(14, 14))
        self.regenerateBtn.setToolTip(
            "基于当前风格与数据,重新生成一次 AI 解读 (Ctrl+I)"
        )

        # 反馈按钮
        self.thumbUpBtn.setEnabled(False)
        self.thumbDownBtn.setEnabled(False)
        self.thumbUpBtn.setToolTip("标记本次解读有用,用于改进 prompt")
        self.thumbDownBtn.setToolTip("标记本次解读需要改进,用于改进 prompt")

        # 状态标签
        self.statusLabel.setWordWrap(True)
        self._applyStatusStyle("idle", "AI 解读仅供参考,请结合原始数据使用")

    def _initLayout(self) -> None:
        """初始化布局(参照 demo.py 风格,边距 16/12/8/16)"""
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setSpacing(0)

        # ====== Header 卡 ======
        header = QWidget(self)
        header.setObjectName("insightHeader")
        headerLayout = QVBoxLayout(header)
        headerLayout.setContentsMargins(20, 16, 12, 12)
        headerLayout.setSpacing(6)

        # demo.py 约定的标题行:avatar + 标题/副标题 + stretch + 关闭
        self.topLayout.setSpacing(10)
        self.topLayout.addWidget(self.avatar)
        titleCol = QVBoxLayout()
        titleCol.setSpacing(1)
        titleCol.setContentsMargins(0, 0, 0, 0)
        titleCol.addWidget(self.titleLabel)
        titleCol.addWidget(self.subtitleLabel)
        self.topLayout.addLayout(titleCol, 1)
        self.topLayout.addWidget(self.closeButton, 0, Qt.AlignmentFlag.AlignTop)
        headerLayout.addLayout(self.topLayout)
        self.vBoxLayout.addWidget(header)
        self.vBoxLayout.addWidget(self._buildSeparator())

        # ====== 工具栏 ======
        toolbar = QWidget(self)
        toolbarLayout = QHBoxLayout(toolbar)
        toolbarLayout.setContentsMargins(20, 10, 20, 10)
        toolbarLayout.setSpacing(10)

        styleGroup = QHBoxLayout()
        styleGroup.setSpacing(6)
        styleGroup.addWidget(self.styleLabel)
        styleGroup.addWidget(self.styleCombo)
        toolbarLayout.addLayout(styleGroup)
        toolbarLayout.addStretch(1)
        toolbarLayout.addWidget(self.confidenceBadge)
        self.vBoxLayout.addWidget(toolbar)
        self.vBoxLayout.addWidget(self._buildSeparator())

        # ====== 内容区 ======
        content = QWidget(self)
        contentLayout = QVBoxLayout(content)
        contentLayout.setContentsMargins(20, 12, 20, 12)
        contentLayout.setSpacing(6)

        # 流式状态行:进度环 + 「正在思考」文案
        self.streamingRow = QHBoxLayout()
        self.streamingRow.setSpacing(6)
        self.streamingRow.addWidget(self.progressRing)
        self.streamingHintLabel = CaptionLabel("AI 正在解读中…", self)
        self.streamingRow.addWidget(self.streamingHintLabel)
        self.streamingRow.addStretch(1)
        self.streamingHintLabel.hide()
        contentLayout.addLayout(self.streamingRow)

        contentLayout.addWidget(self.outputEdit, 1)
        self.vBoxLayout.addWidget(content, 1)
        self.vBoxLayout.addWidget(self._buildSeparator())

        # ====== 状态条 ======
        statusBar = QWidget(self)
        statusLayout = QHBoxLayout(statusBar)
        statusLayout.setContentsMargins(20, 8, 20, 8)
        statusLayout.setSpacing(6)
        statusLayout.addWidget(self.statusLabel, 1)
        self.vBoxLayout.addWidget(statusBar)
        self.vBoxLayout.addWidget(self._buildSeparator())

        # ====== 操作栏 ======
        actionBar = QWidget(self)
        actionLayout = QHBoxLayout(actionBar)
        actionLayout.setContentsMargins(16, 12, 16, 16)
        actionLayout.setSpacing(8)

        # 反馈组(贴左)
        actionLayout.addWidget(self.thumbUpBtn)
        actionLayout.addWidget(self.thumbDownBtn)
        actionLayout.addStretch(1)
        # 操作组(贴右)
        actionLayout.addWidget(self.copyBtn)
        actionLayout.addWidget(self.regenerateBtn)
        self.vBoxLayout.addWidget(actionBar)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _buildSeparator(self) -> QFrame:
        """构造低对比度水平分割线(类 Fluent 1px)"""
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(
            "QFrame{background:rgba(0,0,0,8%); border:none;}"
            if not isDarkTheme()
            else "QFrame{background:rgba(255,255,255,8%); border:none;}"
        )
        return line

    def _refreshMarkdownStyle(self) -> None:
        """刷新 QTextBrowser 的 Markdown CSS,使其主题感知"""
        try:
            css = _buildMarkdownStyle()
            self.outputEdit.document().setDefaultStyleSheet(css)
        except Exception as e:
            logger.debug(f"[AiInsightDrawerView] 刷新 Markdown 样式失败: {e}")

    def _applyStatusStyle(self, kind: str, msg: str) -> None:
        """统一管理状态条文案+配色

        kind: "idle" | "info" | "success" | "warning" | "error"
        """
        palette = {
            "idle": ("#6b7280", "rgba(0,0,0,40%)"),
            "info": ("#3b82f6", "rgba(59,130,246,30%)"),
            "success": ("#16a34a", "rgba(22,163,74,25%)"),
            "warning": ("#eab308", "rgba(234,179,8,30%)"),
            "error": ("#dc2626", "rgba(220,38,38,30%)"),
        }
        textColor, bgColor = palette.get(kind, palette["idle"])
        isDark = isDarkTheme()
        textColorAdj = textColor
        bgRgba = (
            bgColor
            if isDark
            else bgColor.replace("30%)", "12%)").replace("25%)", "10%)")
        )
        self.statusLabel.setText(msg)
        self.statusLabel.setStyleSheet(
            f"color:{textColorAdj}; font-size:11px; padding:6px 10px;"
            f"background:{bgRgba}; border-radius:6px;"
        )

    # ------------------------------------------------------------------
    # 信号连接
    # ------------------------------------------------------------------
    def _connectSignals(self) -> None:
        self.closeButton.clicked.connect(self.closeRequested)
        self.regenerateBtn.clicked.connect(self._onRegenerateClicked)
        self.copyBtn.clicked.connect(self._onCopyClicked)
        self.thumbUpBtn.clicked.connect(self._onThumbUp)
        self.thumbDownBtn.clicked.connect(self._onThumbDown)
        self.styleCombo.currentTextChanged.connect(self.styleChanged)

    # ------------------------------------------------------------------
    # 公共接口(协议保持向后兼容)
    # ------------------------------------------------------------------
    def setPanelTitle(self, panelName: str) -> None:
        """设置标题,例如 'AI 研究助理 - 词频分析'"""
        self.titleLabel.setText(f"AI 研究助理 · {panelName}")

    def setStreamText(self, chunk: str, tokenUsage: int = 0) -> None:
        """增量追加一段流式 token"""
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
        """流式结束后:统一做 think 剥离 + Markdown 渲染"""
        if text is None:
            text = ""
        self._rawBuffer = text
        self._visibleBuffer = self._stripThink(text)
        try:
            self.outputEdit.setMarkdown(self._visibleBuffer)
        except Exception:
            self.outputEdit.setPlainText(self._visibleBuffer)
        self._scrollToEnd()
        self._setRunning(False)
        self._setCompleted()

    def clearText(self) -> None:
        """清空输出(开始新一轮解读前)"""
        self.outputEdit.clear()
        self._rawBuffer = ""
        self._visibleBuffer = ""

    def setError(self, msg: str) -> None:
        """显示错误信息,并启用「重新生成」"""
        self._setRunning(False)
        self.confidenceBadge.setLevel(_ConfidenceBadge.LEVEL_LOW)
        self._applyStatusStyle("error", f"⚠ {msg}")
        self.copyBtn.setEnabled(False)
        self.thumbUpBtn.setEnabled(False)
        self.thumbDownBtn.setEnabled(False)
        logger.warning(f"[AiInsightDrawerView] {msg}")

    def setStatus(self, msg: str) -> None:
        """设置一般状态文本(灰色)"""
        self._applyStatusStyle("info", msg)

    def setRunning(self, running: bool) -> None:
        """切换运行状态"""
        self._setRunning(running)

    def setStyle(self, style: str) -> None:
        """外部更新风格下拉"""
        if style in _STYLE_OPTIONS:
            self.styleCombo.setCurrentText(style)

    def setModelName(self, modelName: str) -> None:
        """外部更新模型名显示"""
        self.subtitleLabel.setText(f"模型:{modelName}")

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _stripThink(text: str) -> str:
        """去除 LLM 输出中的思考块"""
        if not text:
            return ""
        cleaned = _THINK_BLOCK_RE.sub("", text)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned

    def _scrollToEnd(self) -> None:
        bar = self.outputEdit.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def _setRunning(self, running: bool) -> None:
        """切换运行态:控件启用 + 动画可见"""
        if running:
            self.progressRing.show()
            self.progressRing.start()
            self.streamingHintLabel.show()
            self.confidenceBadge.setLevel(_ConfidenceBadge.LEVEL_RUNNING)
            self._applyStatusStyle("info", "AI 正在基于本次分析生成解读…")
            self.regenerateBtn.setEnabled(False)
            self.regenerateBtn.setText("生成中…")
            self.copyBtn.setEnabled(False)
            self.thumbUpBtn.setEnabled(False)
            self.thumbDownBtn.setEnabled(False)
        else:
            self.progressRing.stop()
            self.progressRing.hide()
            self.streamingHintLabel.hide()
            self.regenerateBtn.setEnabled(True)
            self.regenerateBtn.setText("重新生成")

    def _setCompleted(self) -> None:
        """完成态:置信度徽章 + 复制/反馈按钮启用"""
        self.confidenceBadge.setLevel(_ConfidenceBadge.LEVEL_MID)
        self._applyStatusStyle(
            "success",
            "✓ 解读已完成 · 中置信度(基于当前数据,解读仅供参考)",
        )
        self.copyBtn.setEnabled(True)
        self.thumbUpBtn.setEnabled(True)
        self.thumbDownBtn.setEnabled(True)

    def _modelText(self) -> str:
        """当前模型显示(与 AI 聊天共用 cfg.AiModelChat)"""
        apiKey = qconfig.get(cfg.AiApiKey)
        if not apiKey:
            return "未配置 API Key(请到「设置 → AI 解读」)"
        return f"模型:{qconfig.get(cfg.AiModelChat) or 'deepseek-chat'}"

    # ------------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------------
    def _onRegenerateClicked(self) -> None:
        self.regenerateRequested.emit()

    def _onCopyClicked(self) -> None:
        text = self._visibleBuffer or self.outputEdit.toPlainText()
        if not text:
            return
        try:
            clipboard = QGuiApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
                self._applyStatusStyle("success", "✓ 已复制为 Markdown 源文本")
        except Exception as e:
            logger.warning(f"[AiInsightDrawerView] 复制失败: {e}")
            self._applyStatusStyle("error", f"复制失败:{e}")

    def _onThumbUp(self) -> None:
        self.feedbackGiven.emit(1)
        self._applyStatusStyle("success", "已记录正向反馈 · 感谢支持")
        logger.info("[AiInsightDrawerView] 反馈: 有帮助")

    def _onThumbDown(self) -> None:
        self.feedbackGiven.emit(0)
        self._applyStatusStyle("warning", "已记录负向反馈 · 我们会持续优化")
        logger.info("[AiInsightDrawerView] 反馈: 需改进")
