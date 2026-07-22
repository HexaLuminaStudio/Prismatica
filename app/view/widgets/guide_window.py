# coding: utf-8
"""
首次启动引导窗口

参照 qfluentwidgetspro.GuideWindow 的多页引导样式,在用户首次启动时
展示软件基本功能介绍 + 文件保存路径 + HSK / Global 令牌配置流程。

包含页面:
    1. WelcomeInterface             欢迎 + 功能简介
    2. FeatureOverviewInterface     主要功能概览
    3. SavePathGuideInterface       文件保存路径
    4. HskTokenGuideInterface       HSK 令牌配置
    5. GlobalTokenGuideInterface    Global 令牌配置
    6. AiChatGuideInterface         AI 聊天配置(API Key / 模型 / 历史轮数)
    7. FinalInterface               完成引导

启动逻辑(由 main.py 协调):
    - 读取 cfg.FirstLaunch;若为 True 则弹出本窗口
    - 用户点击「完成」后,将 cfg.FirstLaunch 设为 False,主窗口继续启动
    - 用户关闭引导窗口等同于完成引导(同样写入 False)

设计约定:
    - 左侧图标 96×96,右侧标题 + 正文,水平 spacing 24,垂直间距统一 10
    - 边距水平 48,顶部/底部 0(由 GuideWindow 自身的标题栏占位)
    - 不使用硬编码 styleSheet 覆盖主题色,优先用组件的 setTextColor 等语义化 API
    - BodyLabel 多行文本通过连续 addWidget 实现段落,避免 \\n 不生效的问题
"""

from PySide6.QtCore import Qt, QEventLoop, QObject, Signal
from PySide6.QtGui import QColor, QCloseEvent, QFont
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    HyperlinkButton,
    ImageLabel,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    TransparentPushButton,
    setFont,
)

from app.core.utils import cfg, logger, qconfig


# ----------------------------------------------------------------------
# 调色板(集中维护,避免散落硬编码)
# ----------------------------------------------------------------------
_TEXT_COLOR_LIGHT = QColor(96, 96, 96)
_TEXT_COLOR_DARK = QColor(216, 216, 216)
_LINK_COLOR = QColor(0, 120, 212)


# ----------------------------------------------------------------------
# 通用页面基类
# ----------------------------------------------------------------------
class _BaseGuidePage(QWidget):
    """引导窗口单页基类

    布局:
        [icon 96×96]  |  [title 22px Bold]
                      |  [body / 自定义内容]
    边距水平 48,垂直 0;图标与右侧 spacing = 24。
    子类通过 self.contentLayout 在标题下方插入额外组件。
    """

    def __init__(
        self,
        iconPath: str,
        title: str,
        bodyLines: list[str] | None = None,
        parent=None,
    ):
        super().__init__(parent=parent)

        # ---- 左侧图标 ----
        self.icon = ImageLabel(iconPath)
        self.icon.setFixedSize(96, 96)
        # 防止 SVG 在大屏被放大模糊:保持 96×96 但允许缩小
        self.icon.setMinimumSize(72, 72)
        self.icon.setMaximumSize(120, 120)

        # ---- 右侧标题 ----
        self.titleLabel = BodyLabel(title)
        setFont(self.titleLabel, 22, QFont.Weight.Bold)

        # ---- 内容容器(子类可直接往里 addWidget)----
        self.contentLayout = QVBoxLayout()
        self.contentLayout.setContentsMargins(0, 0, 0, 0)
        self.contentLayout.setSpacing(10)
        self.contentLayout.addWidget(self.titleLabel)

        # ---- 正文多行(避免 \\n 不渲染)----
        if bodyLines:
            for line in bodyLines:
                lineLabel = BodyLabel(line)
                lineLabel.setWordWrap(True)
                lineLabel.setTextColor(_TEXT_COLOR_LIGHT, _TEXT_COLOR_DARK)
                self.contentLayout.addWidget(lineLabel)

        # ---- 顶层水平布局 ----
        self._hBoxLayout = QHBoxLayout(self)
        self._hBoxLayout.setContentsMargins(48, 0, 48, 0)
        self._hBoxLayout.setSpacing(24)
        self._hBoxLayout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._hBoxLayout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._hBoxLayout.addLayout(self.contentLayout, 1)


# ----------------------------------------------------------------------
# 页面 1:欢迎页
# ----------------------------------------------------------------------
class WelcomeInterface(_BaseGuidePage):
    """欢迎页 - 引导用户进入配置流程"""

    def __init__(self, parent=None):
        super().__init__(
            iconPath=":/app/images/logo.png",
            title="欢迎使用 Prismatica 棱溯",
            bodyLines=[
                "Prismatica 是一款面向汉语教学研究的语料客户端。",
                "提供 HSK / 全球中介语料库的全量下载、检索与多维度分析。",
                "在开始使用前,请跟随引导完成基本配置 —— 仅需 1~2 分钟。",
            ],
            parent=parent,
        )
        # 底部操作链接
        self.manualLink = HyperlinkButton("", "查看用户手册")
        self.manualLink.clicked.connect(
            lambda: logger.info("[Guide] 用户点击查看用户手册")
        )
        self.contentLayout.addSpacing(4)
        self.contentLayout.addWidget(self.manualLink, 0, Qt.AlignmentFlag.AlignLeft)


# ----------------------------------------------------------------------
# 页面 2:功能概览
# ----------------------------------------------------------------------
class FeatureOverviewInterface(_BaseGuidePage):
    """功能概览 - 紧凑功能列表"""

    def __init__(self, parent=None):
        super().__init__(
            iconPath=":/app/icons/Analysis.svg",
            title="主要功能",
            bodyLines=[
                "Prismatica 提供以下核心能力,所有模块可在左侧导航中访问:",
            ],
            parent=parent,
        )

        # 功能条目(单层 QVBoxLayout,不再多余包 QWidget)
        self.featureList = QVBoxLayout()
        self.featureList.setContentsMargins(0, 0, 0, 0)
        self.featureList.setSpacing(6)
        for iconPath, title, desc in [
            (
                ":/app/icons/Hsk.svg",
                "HSK 语料下载",
                "支持 HSK 全级别作文 / 文本语料的批量下载与索引",
            ),
            (
                ":/app/icons/Global.svg",
                "全球中介语料下载",
                "全球中介语料库(Global)检索与下载",
            ),
            (
                ":/app/icons/Bias.svg",
                "偏误统计",
                "基于中介语料库的偏误频次、类型与分布统计",
            ),
            (
                ":/app/icons/Analysis.svg",
                "语料分析",
                "词频 / 搭配 / 索引 / 共现网络 / 依存 / 词云等",
            ),
            (
                ":/app/icons/Setting.svg",
                "高级设置",
                "下载路径、并发数、代理、令牌等参数可调",
            ),
        ]:
            self._addFeatureRow(iconPath, title, desc)
        self.contentLayout.addLayout(self.featureList)

    def _addFeatureRow(self, iconPath: str, title: str, desc: str):
        """添加一行功能说明:图标 + 标题/描述(无多余 wrap widget)"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        icon = ImageLabel(iconPath)
        icon.setFixedSize(18, 18)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        textCol = QVBoxLayout()
        textCol.setContentsMargins(0, 0, 0, 0)
        textCol.setSpacing(0)

        titleLbl = BodyLabel(title)
        setFont(titleLbl, 13, QFont.Weight.DemiBold)
        descLbl = CaptionLabel(desc)
        descLbl.setTextColor(_TEXT_COLOR_LIGHT, _TEXT_COLOR_DARK)
        descLbl.setWordWrap(True)

        textCol.addWidget(titleLbl)
        textCol.addWidget(descLbl)
        row.addLayout(textCol, 1)
        self.featureList.addLayout(row)


# ----------------------------------------------------------------------
# 页面 3:文件保存路径
# ----------------------------------------------------------------------
class SavePathGuideInterface(_BaseGuidePage):
    """文件保存路径配置页

    让用户选择语料下载 / 缓存数据的本地保存目录,
    与设置页的「下载功能设置 → 选择保存路径」保持一致的行为。
    用户也可保持默认路径直接跳过(后续在设置页随时修改)。
    """

    def __init__(self, parent=None):
        super().__init__(
            iconPath=":/app/icons/SavePath.svg",
            title="文件保存路径",
            bodyLines=[
                "选择语料文件下载与缓存的本地保存目录。",
                "默认使用软件安装目录下的 download 文件夹,也可自定义。",
                "该设置后续可在「设置 → 下载功能设置」中随时修改。",
            ],
            parent=parent,
        )

        # ---- 当前路径显示 + 选择按钮 ----
        self.pathLineEdit = LineEdit()
        self.pathLineEdit.setReadOnly(True)
        self.pathLineEdit.setText(qconfig.get(cfg.DownloadSavePath) or "")

        self.browseButton = PushButton("选择目录…")
        self.browseButton.clicked.connect(self._onBrowseClicked)

        pathRow = QHBoxLayout()
        pathRow.setContentsMargins(0, 0, 0, 0)
        pathRow.setSpacing(8)
        pathRow.addWidget(self.pathLineEdit, 1)
        pathRow.addWidget(self.browseButton)
        self.contentLayout.addLayout(pathRow)

        # ---- 状态提示 ----
        self.statusLabel = CaptionLabel("")
        self.statusLabel.setWordWrap(True)
        self.contentLayout.addWidget(self.statusLabel)

    def _onBrowseClicked(self):
        """弹出目录选择对话框"""
        logger.info("[Guide] 用户点击选择保存目录")
        currentPath = qconfig.get(cfg.DownloadSavePath) or ""
        folderPath = QFileDialog.getExistingDirectory(
            self,
            "选择下载保存路径",
            currentPath,
        )
        if not folderPath:
            self.statusLabel.setText("已取消选择,保留原路径。")
            self.statusLabel.setStyleSheet("color: #888;")
            return

        qconfig.set(cfg.DownloadSavePath, folderPath)
        self.pathLineEdit.setText(folderPath)
        self.statusLabel.setText(f"已设置:{folderPath}")
        self.statusLabel.setStyleSheet("color: #2c8a4a;")
        logger.info(f"[Guide] 保存路径已更新: {folderPath}")


# ----------------------------------------------------------------------
# 页面 4 & 5:令牌配置(共用基类)
# ----------------------------------------------------------------------
class _TokenGuideBase(_BaseGuidePage):
    """令牌配置页基类 - 子类实现 _refreshThreadFactory

    校验规则:
        - 启动时若 cfg.<Key>Token 已存在(用户上次成功过),该页视为"已通过",
          「下一步」可用
        - 用户填账号+密码并成功点「获取 Token」后,通过
        - **永远不阻断**:即使未通过,用户也可点「上一步」返回或关闭引导退出,
          我们只禁用「下一步」按钮,提示用户必须先获取 token
    """

    # 验证状态变化(成功 / 失败)时通知 GuideWindow 重新评估「下一步」
    validationChanged = Signal()

    def __init__(
        self,
        iconPath: str,
        title: str,
        introLines: list[str],
        usernamePlaceholder: str,
        passwordPlaceholder: str,
        usernameConfigKey: str,
        passwordConfigKey: str,
        tokenConfigKey: str,
        parent=None,
    ):
        super().__init__(
            iconPath=iconPath,
            title=title,
            bodyLines=introLines,
            parent=parent,
        )

        # ---- 表单字段(预填已保存值)----
        self._usernameConfigKey = usernameConfigKey
        self._passwordConfigKey = passwordConfigKey
        self._tokenConfigKey = tokenConfigKey

        # 是否已通过验证(初始值由 _hasExistingToken() 决定)
        self._validated = self._hasExistingToken()

        self.tokenUsernameEdit = LineEdit()
        self.tokenPasswordEdit = PasswordLineEdit()

        self.tokenUsernameEdit.setPlaceholderText(usernamePlaceholder)
        self.tokenPasswordEdit.setPlaceholderText(passwordPlaceholder)

        self.tokenUsernameEdit.setText(
            qconfig.get(getattr(cfg, usernameConfigKey)) or ""
        )
        self.tokenPasswordEdit.setText(
            qconfig.get(getattr(cfg, passwordConfigKey)) or ""
        )

        # ---- 表单布局:label + field 两列 ----
        formLayout = QVBoxLayout()
        formLayout.setContentsMargins(0, 0, 0, 0)
        formLayout.setSpacing(8)
        self._addFormRow(formLayout, "账号", self.tokenUsernameEdit)
        self._addFormRow(formLayout, "密码", self.tokenPasswordEdit)
        self.contentLayout.addLayout(formLayout)

        # ---- 按钮行(仅「获取 Token」)----
        self.refreshButton = PushButton("获取 Token")
        self.refreshButton.setFixedWidth(120)
        self.refreshButton.clicked.connect(self._onRefreshClicked)

        buttonRow = QHBoxLayout()
        buttonRow.setContentsMargins(0, 0, 0, 0)
        buttonRow.setSpacing(12)
        buttonRow.addWidget(self.refreshButton)
        buttonRow.addStretch(1)
        self.contentLayout.addLayout(buttonRow)

        # ---- 状态 / 安全说明 ----
        self.statusLabel = CaptionLabel("")
        self.statusLabel.setWordWrap(True)
        self.contentLayout.addWidget(self.statusLabel)

        noticeLabel = CaptionLabel(
            "说明:密码仅在本地用于请求官方 Token,不发送到任何第三方服务器。"
        )
        noticeLabel.setTextColor(_TEXT_COLOR_LIGHT, _TEXT_COLOR_DARK)
        noticeLabel.setWordWrap(True)
        self.contentLayout.addWidget(noticeLabel)

        # 启动时立即把状态写好(否则进入页面后状态栏空白)
        if self._validated:
            self._setStatus("已检测到已保存的 Token,可直接进入下一步。", success=True)
        else:
            self._setStatus(
                "尚未配置 Token,请填写账号密码后点击「获取 Token」进行验证。",
                neutral=True,
            )

    def _addFormRow(self, parentLayout: QVBoxLayout, labelText: str, widget: QWidget):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        lbl = BodyLabel(labelText)
        lbl.setFixedWidth(40)
        row.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(widget, 1)
        parentLayout.addLayout(row)

    # ------------------------------------------------------------------
    # 校验接口(由 GuideWindow 在切换页面时调用)
    # ------------------------------------------------------------------
    def _hasExistingToken(self) -> bool:
        """检查 cfg 中是否已存在有效 Token(用于跳过重新验证)"""
        existing = qconfig.get(getattr(cfg, self._tokenConfigKey))
        return bool(existing and str(existing).strip())

    def isValidated(self) -> bool:
        """外部(GuideWindow)读取此属性决定是否允许「下一步」"""
        return self._validated

    # ---- 由子类实现:返回 (thread, onSuccess, onError) ----
    def _createRefreshThread(self, username: str, password: str):
        """子类必须重写:返回带 finished(token)/error(str) 信号的对象"""
        raise NotImplementedError

    def _onRefreshClicked(self):
        username = self.tokenUsernameEdit.text().strip()
        password = self.tokenPasswordEdit.text().strip()

        if not username or not password:
            self._setStatus("请先填写账号与密码", error=True)
            return

        logger.info(f"[Guide] 用户点击获取 Token ({self._usernameConfigKey})")
        self._setStatus("正在请求 Token,请稍候...", warn=True)
        self.refreshButton.setEnabled(False)
        self.refreshButton.setText("获取中...")

        self._refreshThread = self._createRefreshThread(username, password)
        self._refreshThread.finished.connect(self._onTokenRefreshed)
        self._refreshThread.error.connect(self._onRefreshError)
        self._refreshThread.start()

    def _onTokenRefreshed(self, token: str):
        self.refreshButton.setEnabled(True)
        self.refreshButton.setText("获取 Token")
        qconfig.set(
            getattr(cfg, self._usernameConfigKey), self.tokenUsernameEdit.text().strip()
        )
        qconfig.set(
            getattr(cfg, self._passwordConfigKey), self.tokenPasswordEdit.text().strip()
        )
        qconfig.set(getattr(cfg, self._tokenConfigKey), token)
        # 标记为已通过,触发 GuideWindow 重新评估「下一步」按钮
        self._validated = True
        self._setStatus("Token 已保存,可进入下一步", success=True)
        logger.info(f"[Guide] {self._usernameConfigKey} Token 引导配置成功")

        # 通知外部:验证状态变化
        self.validationChanged.emit()

    def _onRefreshError(self, error: str):
        self.refreshButton.setEnabled(True)
        self.refreshButton.setText("获取 Token")
        # 验证失败时保持 _validated=False,确保「下一步」被禁用
        self._validated = False
        self._setStatus(f"获取失败:{error}", error=True)
        logger.error(f"[Guide] {self._usernameConfigKey} Token 获取失败: {error}")

        self.validationChanged.emit()

    # ---- 统一状态展示(集中颜色,避免散落)----
    def _setStatus(
        self,
        text: str,
        *,
        success: bool = False,
        error: bool = False,
        warn: bool = False,
        neutral: bool = False,
    ):
        self.statusLabel.setText(text)
        if success:
            self.statusLabel.setStyleSheet("color: #2c8a4a;")
        elif error:
            self.statusLabel.setStyleSheet("color: #b00;")
        elif warn:
            self.statusLabel.setStyleSheet("color: #c97a00;")
        else:
            self.statusLabel.setStyleSheet("color: #888;")


# ----------------------------------------------------------------------
# 页面 3:HSK 令牌配置
# ----------------------------------------------------------------------
class HskTokenGuideInterface(_TokenGuideBase):
    """HSK 令牌配置页"""

    def __init__(self, parent=None):
        super().__init__(
            iconPath=":/app/icons/Hsk.svg",
            title="HSK 令牌配置",
            introLines=[
                "输入 HSK 官方账号密码后点击「获取 Token」即可自动登录并保存;",
                "也可直接跳过,后续在「设置 → 下载功能设置」中补配。",
            ],
            usernamePlaceholder="请输入 HSK 账号(邮箱)",
            passwordPlaceholder="请输入 HSK 密码",
            usernameConfigKey="HSKLoginUsername",
            passwordConfigKey="HSKLoginPassword",
            tokenConfigKey="HSKLoginToken",
            parent=parent,
        )

    def _createRefreshThread(self, username: str, password: str):
        from app.core.services import HskTokenRefreshThread

        return HskTokenRefreshThread(username, password)


# ----------------------------------------------------------------------
# 页面 4:Global 令牌配置
# ----------------------------------------------------------------------
class GlobalTokenGuideInterface(_TokenGuideBase):
    """Global 令牌配置页"""

    def __init__(self, parent=None):
        super().__init__(
            iconPath=":/app/icons/Global.svg",
            title="Global 令牌配置",
            introLines=[
                "输入 Global 官方 UserID 与密码后点击「获取 Token」即可自动登录并保存;",
                "也可直接跳过,后续在「设置 → 下载功能设置」中补配。",
            ],
            usernamePlaceholder="请输入 Global UserID",
            passwordPlaceholder="请输入 Global Password",
            usernameConfigKey="GlobalLoginUsername",
            passwordConfigKey="GlobalLoginPassword",
            tokenConfigKey="GlobalLoginToken",
            parent=parent,
        )

    def _createRefreshThread(self, username: str, password: str):
        from app.core.services import GlobalTokenRefreshThread

        return GlobalTokenRefreshThread(username, password)


# ----------------------------------------------------------------------
# 页面 6:AI 聊天配置
# ----------------------------------------------------------------------
class AiChatGuideInterface(_BaseGuidePage):
    """AI 聊天配置页

    让用户在首次启动时一次性配置 AI 聊天所需的全部参数,
    与设置页的「AI 聊天设置」保持一致的行为:
        - API Key(密码框)
        - API Base URL(默认 DeepSeek,支持任意 OpenAI 兼容服务)
        - Chat 模型 ID(自由输入,默认 deepseek-chat)
        - 多轮上下文轮数(下拉 5 / 10 / 20 / 50,默认 10)
        - 系统提示词文件(可选)

    任何字段都可留空跳过,后续在「设置 → AI 聊天设置」随时修改。
    留空时不会阻断引导流程,AI 聊天页在未配置 API Key 时会在发送时给出友好提示。
    """

    def __init__(self, parent=None):
        super().__init__(
            iconPath=":/app/icons/Robot.svg",
            title="AI 聊天配置",
            bodyLines=[
                "配置 Prismatica 内置 AI 助手的访问参数。",
                "默认对接 DeepSeek(OpenAI 兼容协议),也可填入任何兼容服务",
                "(如 OpenAI / 通义千问 / 月之暗面等)。",
                "全部字段均可留空跳过,后续在「设置 → AI 聊天设置」中补配。",
            ],
            parent=parent,
        )

        # ---- API Key(密码框)----
        self.apiKeyEdit = PasswordLineEdit()
        self.apiKeyEdit.setPlaceholderText("如 DeepSeek / OpenAI / Qwen 的 API Key")
        self.apiKeyEdit.setText(qconfig.get(cfg.AiApiKey) or "")

        # ---- API Base URL ----
        self.baseUrlEdit = LineEdit()
        self.baseUrlEdit.setPlaceholderText("https://api.deepseek.com")
        self.baseUrlEdit.setText(
            qconfig.get(cfg.AiBaseUrl) or "https://api.deepseek.com"
        )

        # ---- Chat 模型 ID(自由输入)----
        self.chatModelEdit = LineEdit()
        self.chatModelEdit.setPlaceholderText(
            "如 deepseek-chat / gpt-4o / qwen-max / deepseek-reasoner …"
        )
        self.chatModelEdit.setText(qconfig.get(cfg.AiModelChat) or "deepseek-chat")

        # ---- 多轮上下文轮数(下拉)----
        self.maxHistoryCombo = ComboBox()
        for n in (5, 10, 20, 50):
            self.maxHistoryCombo.addItem(str(n))
        self.maxHistoryCombo.setCurrentText(str(qconfig.get(cfg.AiMaxHistory) or 10))

        # ---- 表单布局(label + field 两列)----
        formLayout = QVBoxLayout()
        formLayout.setContentsMargins(0, 0, 0, 0)
        formLayout.setSpacing(8)
        self._addFormRow(formLayout, "API Key", self.apiKeyEdit)
        self._addFormRow(formLayout, "Base URL", self.baseUrlEdit)
        self._addFormRow(formLayout, "Chat 模型", self.chatModelEdit)
        self._addFormRow(formLayout, "历史轮数", self.maxHistoryCombo)
        self.contentLayout.addLayout(formLayout)

        # ---- 状态 / 操作行 ----
        # 把"跳过此步"做成显式按钮,与正式提交按钮并排,避免用户误解为必填
        self.statusLabel = CaptionLabel("")
        self.statusLabel.setWordWrap(True)
        self.contentLayout.addWidget(self.statusLabel)

        actionRow = QHBoxLayout()
        actionRow.setContentsMargins(0, 0, 0, 0)
        actionRow.setSpacing(8)

        self.saveButton = PrimaryPushButton("保存配置")
        self.saveButton.clicked.connect(self._onSaveClicked)

        self.skipButton = TransparentPushButton("暂时跳过")
        self.skipButton.clicked.connect(self._onSkipClicked)

        actionRow.addWidget(self.saveButton)
        actionRow.addWidget(self.skipButton)
        actionRow.addStretch(1)
        self.contentLayout.addLayout(actionRow)

        # ---- 安全说明 ----
        noticeLabel = CaptionLabel(
            "说明:API Key 仅保存在本地 config.json,用于调用对应大模型服务;"
            "不会上传到任何第三方服务器。点击「暂时跳过」可在后续任何时候"
            "前往「设置 → AI 聊天设置」补配。"
        )
        noticeLabel.setTextColor(_TEXT_COLOR_LIGHT, _TEXT_COLOR_DARK)
        noticeLabel.setWordWrap(True)
        self.contentLayout.addWidget(noticeLabel)

        # 默认展示"可跳过"提示,让用户安心
        self._setHint()

    def _addFormRow(self, parentLayout: QVBoxLayout, labelText: str, widget: QWidget):
        """表单行:左侧固定宽度 label + 右侧自适应 field"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        lbl = BodyLabel(labelText)
        lbl.setFixedWidth(64)
        row.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(widget, 1)
        parentLayout.addLayout(row)

    # ------------------------------------------------------------------
    # 跳过 / 保存 行为
    # ------------------------------------------------------------------
    def _onSkipClicked(self) -> None:
        """用户点击「暂时跳过」:
        - 清空所有字段(避免误把半填内容写入 cfg)
        - 在状态栏给出明确反馈
        """
        logger.info("[Guide] 用户主动跳过 AI 聊天配置")
        self.apiKeyEdit.clear()
        self.baseUrlEdit.clear()
        self.chatModelEdit.clear()
        self._setStatus(
            "已跳过 AI 聊天配置。后续可在「设置 → AI 聊天设置」中随时补配。",
            success=True,
        )

    def _onSaveClicked(self) -> None:
        """用户点击「保存配置」:触发校验,失败时给出提示但不阻断流程"""
        if self._validateInput(showSuccess=True):
            self._setStatus(
                "配置已就绪,点「下一步」继续,或点「完成」启动 Prismatica。",
                success=True,
            )

    def validate(self) -> bool:
        """页面校验:由 GuideWindow 在切换下一页前调用

        设计原则:**永不阻断引导流程**。该页全部可跳过,任何校验失败
        仅在状态栏展示,允许用户继续。

        Returns:
            始终返回 True(不阻断)
        """
        self._validateInput(showSuccess=False)
        return True

    def _validateInput(self, *, showSuccess: bool) -> bool:
        """实际校验逻辑

        规则(宽松,不阻断):
            - 全部留空:视为跳过,允许继续
            - 填写了 API Key,则 Base URL 与 Chat 模型必须非空
            - 单独填 Base URL 或模型(无 Key)允许,但在状态栏提示"无效"

        Returns:
            True  - 通过(可继续)
            False - 校验失败(已在状态栏展示原因)
        """
        apiKey = self.apiKeyEdit.text().strip()
        baseUrl = self.baseUrlEdit.text().strip()
        chatModel = self.chatModelEdit.text().strip()

        # 全部留空:跳过
        if not apiKey and not baseUrl and not chatModel:
            if showSuccess:
                self._setStatus("已跳过 AI 聊天配置。", neutral=True)
            return True

        # 必填联动校验:有 Key 就要有 URL 和 模型
        if apiKey and not baseUrl:
            self._setStatus(
                "已填写 API Key,但 Base URL 为空,请补全或清空 API Key。",
                error=True,
            )
            return False

        if apiKey and not chatModel:
            self._setStatus(
                "已填写 API Key,但 Chat 模型为空,请补全或清空 API Key。",
                error=True,
            )
            return False

        # 仅填 Base URL / 模型(无 Key):提示但不阻断
        if not apiKey and (baseUrl or chatModel):
            self._setStatus(
                "提示:未填写 API Key,已填的 Base URL / 模型将不会保存。",
                warn=True,
            )
            return True

        # 完整填写:成功
        if showSuccess:
            self._setStatus("AI 聊天配置已就绪。", success=True)
        return True

    def save(self) -> None:
        """页面数据保存:写入 cfg(由 GuideWindow 在用户点「完成」时统一调用)

        设计:
            - **留空字段不覆盖**已有值,避免误删用户之前的配置
            - 只有完整的(apiKey + baseUrl + chatModel 三者齐全)才落盘三者
            - 仅有部分字段时,丢弃这些字段(避免出现"有 Key 但无 URL"的不一致状态)
            - **历史轮数总是保存**(有默认值 10)
        """
        apiKey = self.apiKeyEdit.text().strip()
        baseUrl = self.baseUrlEdit.text().strip()
        chatModel = self.chatModelEdit.text().strip()
        maxHistory = int(self.maxHistoryCombo.currentText())

        # 仅当 API Key + Base URL + 模型 三者都非空时,才落盘 AI 字段
        if apiKey and baseUrl and chatModel:
            qconfig.set(cfg.AiApiKey, apiKey)
            qconfig.set(cfg.AiBaseUrl, baseUrl)
            qconfig.set(cfg.AiModelChat, chatModel)
            logger.info(
                f"[Guide] AI 聊天配置已完整保存 "
                f"(apiKey=***{apiKey[-4:]}, baseUrl={baseUrl}, "
                f"chatModel={chatModel})"
            )
        else:
            logger.info(
                "[Guide] AI 聊天配置不完整或被跳过,保持 cfg 原值不变 "
                f"(apiKey={'已填' if apiKey else '空'}, "
                f"baseUrl={'已填' if baseUrl else '空'}, "
                f"chatModel={'已填' if chatModel else '空'})"
            )

        # 历史轮数总是保存(下拉框必有值)
        qconfig.set(cfg.AiMaxHistory, maxHistory)

    def _setHint(self) -> None:
        """页面初始提示,明确告知用户此页可跳过"""
        self.statusLabel.setText(
            "提示:本页面全部可暂时跳过,后续在「设置 → AI 聊天设置」随时补配。"
        )
        self.statusLabel.setStyleSheet("color: #888;")

    def _setStatus(
        self,
        text: str,
        *,
        success: bool = False,
        error: bool = False,
        warn: bool = False,
        neutral: bool = False,
    ):
        self.statusLabel.setText(text)
        if success:
            self.statusLabel.setStyleSheet("color: #2c8a4a;")
        elif error:
            self.statusLabel.setStyleSheet("color: #b00;")
        elif warn:
            self.statusLabel.setStyleSheet("color: #c97a00;")
        else:
            self.statusLabel.setStyleSheet("color: #888;")


# ----------------------------------------------------------------------
# 页面 5:完成页
# ----------------------------------------------------------------------
class FinalInterface(_BaseGuidePage):
    """完成页"""

    def __init__(self, parent=None):
        super().__init__(
            iconPath=":/app/icons/Check.svg",
            title="配置完成",
            bodyLines=[
                "引导流程已结束,Prismatica 将使用刚才的配置启动主窗口。",
            ],
            parent=parent,
        )
        # 紧凑提示列表
        tipsLayout = QVBoxLayout()
        tipsLayout.setContentsMargins(0, 4, 0, 0)
        tipsLayout.setSpacing(4)
        for tip in [
            "• Token 后续可在「设置 → 下载功能设置」中随时修改",
            "• AI 聊天相关参数后续可在「设置 → AI 聊天设置」中修改",
            "• 内测版用户无需激活码,所有功能默认开放",
            "• 如遇问题,可在「设置 → 关于软件」中提交反馈",
        ]:
            tipLbl = CaptionLabel(tip)
            tipLbl.setTextColor(_TEXT_COLOR_LIGHT, _TEXT_COLOR_DARK)
            tipLbl.setWordWrap(True)
            tipsLayout.addWidget(tipLbl)
        self.contentLayout.addLayout(tipsLayout)


# ----------------------------------------------------------------------
# 引导窗口(对外暴露)
# ----------------------------------------------------------------------
class GuideWindow(QObject):
    """首次启动引导窗口(轻量包装)

    使用 qfluentwidgetspro.GuideWindow 作为底层窗口,
    在第一次启动时依次展示 5 个页面。
    完成或关闭后,通过 cfg.FirstLaunch 持久化为 False,
    下次启动不再弹出。

    实现要点:
        - 继承 QObject 才能让 `finished = Signal()` 作为类属性正常工作
          (在 __init__ 里实例化 Signal 会得到 Signal 类本身,没有 connect)。
        - 通过动态继承 ProGuideWindow 并重写 closeEvent,避免对原生窗口
          做 monkey-patch(原 closeEvent 是 C 层方法,赋值会丢失绑定)。
        - exec() 用 QEventLoop 阻塞,直到用户完成引导或关闭窗口。
    """

    # 引导完成后发出(main.py 据此显示主窗口)
    finished = Signal()
    # 用户在未完成引导时尝试关闭,引导窗口拒绝关闭并发出此信号
    # main.py 据此选择退出整个程序
    rejected = Signal()

    # 紧凑窗口尺寸:跟随 ProGuideWindow 默认,但显式设一个合理范围
    _WINDOW_MIN_SIZE = (760, 520)
    _WINDOW_DEFAULT_SIZE = (860, 540)

    def __init__(self):
        super().__init__()
        # 防止 _onFinished 被重复触发的重入锁
        self._finished = False

        # 延迟导入 pro 组件,避免主程序未安装 pro 时影响启动
        from qfluentwidgetspro import GuideWindow as ProGuideWindow

        # 动态继承 ProGuideWindow,重写 closeEvent 把"关闭"等同"完成引导"。
        class _GuideWindowImpl(ProGuideWindow):
            """包装后的引导窗口,关闭时通知外部"""

            _outer = None  # 反向引用,由 GuideWindow.__init__ 注入

            def __init__(self):
                super().__init__()
                # 紧凑尺寸:不让窗口过大或过小
                self.setMinimumSize(*GuideWindow._WINDOW_MIN_SIZE)
                self.resize(*GuideWindow._WINDOW_DEFAULT_SIZE)
                self.previousButton.setText("上一步")
                self.nextButton.setText("下一步")
                self.launchButton.setText("完成")

            def closeEvent(self, event: QCloseEvent):
                """关闭按钮处理:
                - 若引导已完成(self._outer._finished=True),放行关闭
                - 若未完成,拒绝关闭 + 发 rejected 信号,
                  由 main.py 退出整个程序

                注意:此处**不再弹出模态 MessageBox**。
                原实现 MessageBox.exec() 与 _outer.exec() 在同一 GUI 线程
                嵌套调用 QEventLoop,会造成死锁(测试卡住验证过)。
                改为:仅 ignore + emit rejected,提示留给 main.py。
                """
                outer = _GuideWindowImpl._outer
                if outer is None:
                    event.accept()
                    return

                if outer._finished:
                    # 已经走完引导(appStarted / launchButton),允许关闭
                    event.accept()
                    return

                # 未完成引导:拒绝关闭 + 发出 rejected 信号让主程序退出
                logger.warning("[Guide] 用户在未完成引导时尝试关闭,拒绝并请求退出程序")
                event.ignore()
                outer.rejected.emit()

        self._window = _GuideWindowImpl()
        type(self._window)._outer = self

        # 保存带 save()/validate() 接口的页面引用,
        # 以便在用户点「完成」时统一落盘,并在切换下一页前校验
        self._savablePages: list = []

        self._buildPages()
        self._wireEvents()
        logger.info("[Guide] 首次启动引导窗口已初始化")

    def _buildPages(self):
        """注册引导页"""
        self._window.addPage(WelcomeInterface())
        self._window.addPage(FeatureOverviewInterface())
        self._window.addPage(SavePathGuideInterface())

        # HSK / Global Token 页(需根据验证状态启用「下一步」)
        self._hskTokenPage = HskTokenGuideInterface()
        self._globalTokenPage = GlobalTokenGuideInterface()
        self._tokenPages = [self._hskTokenPage, self._globalTokenPage]
        self._window.addPage(self._hskTokenPage)
        self._window.addPage(self._globalTokenPage)

        # AI 聊天配置页(在 Global 之后,Final 之前)
        self._aiGuidePage = AiChatGuideInterface()
        self._window.addPage(self._aiGuidePage)
        self._savablePages.append(self._aiGuidePage)

        self._window.addPage(FinalInterface())

        self._window.setWindowTitle("Prismatica - 首次启动引导")

    def _wireEvents(self):
        """绑定信号"""
        # 用户点「下一步」:在切到下一页前对当前页校验
        self._window.nextButton.clicked.connect(self._onNextClicked)
        # 用户点「完成 / 启动应用」时,appStarted 触发
        self._window.appStarted.connect(self._onFinished)
        # 页面切换:重新评估「下一步」按钮可用性(token 页要等验证通过)
        self._window.currentIndexChanged.connect(self._onPageChanged)
        # token 页验证结果变化时,重新评估「下一步」按钮
        for tokenPage in getattr(self, "_tokenPages", []):
            tokenPage.validationChanged.connect(self._updateNextButtonState)

        # 初始评估一次(若当前正好在 token 页)
        self._updateNextButtonState()

    def _onPageChanged(self, _index: int) -> None:
        """页面切换:更新「下一步」按钮状态"""
        self._updateNextButtonState()

    def _updateNextButtonState(self) -> None:
        """根据当前页的验证状态启用 / 禁用「下一步」按钮

        规则:
            - 当前页是 token 页(HSK / Global)且未通过验证 → 禁用
            - 否则 → 启用
        """
        try:
            # currentPage 是 method(ProGuideWindow 的 Cython 实现),必须调用
            currentPage = self._window.currentPage()
        except Exception:
            currentPage = None
        if currentPage is None:
            return

        isValidated = True  # 默认允许
        if isinstance(currentPage, _TokenGuideBase):
            isValidated = currentPage.isValidated()

        self._window.nextButton.setEnabled(isValidated)
        if not isValidated:
            logger.debug(
                f"[Guide] 当前页 {type(currentPage).__name__} 未通过验证,"
                f"已禁用「下一步」"
            )

    def _onNextClicked(self) -> None:
        """点击「下一步」时,对当前页执行 validate()(若该页实现)

        双重保护:
            1. token 页未通过验证 → 禁用 nextButton,通常点不到
            2. 即使用户通过其它方式触发(键盘 / a11y),这里再次拦截

        ProGuideWindow 会在我们返回后自动切到下一页。
        """
        try:
            # ProGuideWindow 暴露的当前页接口是 currentPage()(method),
            # 不是属性。
            currentPage = self._window.currentPage()
            if currentPage is None:
                return

            # ---- Token 页硬拦截:未通过验证禁止切到下一页 ----
            if (
                isinstance(currentPage, _TokenGuideBase)
                and not currentPage.isValidated()
            ):
                currentPage._setStatus(
                    "请先完成 Token 验证(点击「获取 Token」)后再进入下一步。",
                    error=True,
                )
                logger.warning(
                    f"[Guide] 用户尝试跳过未验证的 {type(currentPage).__name__},"
                    f"已在 click handler 中再次拦截"
                )
                # 重新强制禁用按钮(防御性)
                self._window.nextButton.setEnabled(False)
                return

            validateFn = getattr(currentPage, "validate", None)
            if callable(validateFn):
                validateFn()
        except Exception as e:
            logger.warning(f"[Guide] 校验当前页失败: {e}")

    def _onFinished(self):
        """引导结束:写入 FirstLaunch=False,关闭引导窗口,发出 finished 信号

        必须主动 close() 引导窗口,否则引导窗口仍然显示,
        会遮挡后续弹出的主窗口。

        注意:_onFinished 可能被两条路径同时触发:
            - appStarted 信号(用户点「完成 / 启动应用」)
            - closeEvent 重写(用户点窗口关闭按钮 / 我们自己 close())
        用 self._finished 标志防止重入。
        """
        if self._finished:
            return
        self._finished = True

        # 0) 先调用所有可保存页面的 save(),落盘用户配置
        for page in self._savablePages:
            saveFn = getattr(page, "save", None)
            if callable(saveFn):
                try:
                    saveFn()
                except Exception as e:
                    logger.warning(f"[Guide] 保存引导页配置失败: {e}")

        # 1) 写持久化标记
        try:
            qconfig.set(cfg.FirstLaunch, False)
            logger.info("[Guide] 引导已完成,cfg.FirstLaunch = False")
        except Exception as e:
            logger.warning(f"[Guide] 写入 FirstLaunch 失败: {e}")

        # 2) 主动关闭引导窗口
        try:
            if self._window is not None and self._window.isVisible():
                self._window.close()
        except Exception as e:
            logger.warning(f"[Guide] 关闭引导窗口失败: {e}")

        # 3) 通知外部(QEventLoop.quit)
        try:
            self.finished.emit()
        except (RuntimeError, AttributeError):
            pass

    def exec(self) -> bool:
        """阻塞显示引导窗口,直到用户完成或被拒

        Returns:
            True  - 用户完成引导,可继续启动主窗口
            False - 用户在未完成时尝试关闭引导,主程序应退出
        """
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

        loop = QEventLoop()
        self.finished.connect(loop.quit)
        self.rejected.connect(loop.quit)
        self._window.destroyed.connect(loop.quit)
        loop.exec()

        return self._finished

    def show(self):
        """非阻塞显示(供调试使用)"""
        self._window.show()

    def close(self):
        self._onFinished()
        self._window.close()
