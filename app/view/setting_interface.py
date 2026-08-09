# coding: utf-8
"""
设置界面模块
提供软件设置、关于信息、激活码管理和用户协议等功能
"""

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    GroupHeaderCardWidget,
    HyperlinkLabel,
    IconWidget,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    VerticalSeparator,
    HyperlinkButton,
    isDarkTheme,
)

from app.core.services import HskTokenRefreshThread, GlobalTokenRefreshThread
from app.core.utils import cfg, qconfig, logger, signalBus
from app.view.widgets.prismatica_theme import pageBackgroundColor


_ACCENT = "#00B09C"
_ACCENT_SOFT = "#EAF8F6"
_TEXT = "#1F1F1F"
_MUTED = "#616161"
_BORDER = "#E5E5E5"


def _setChineseUiFont(widget: QWidget, size: int = 10, weight=QFont.Weight.Normal):
    """为设置页提供稳定的中西文字体回退。"""
    font = QFont("Segoe UI")
    font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
    font.setPointSize(size)
    font.setWeight(weight)
    widget.setFont(font)


def _accentIcon(icon):
    """把 Fluent 图标统一渲染为设计稿的品牌青色。"""
    if hasattr(icon, "icon"):
        return icon.icon(color=QColor(_ACCENT))
    return icon


class SettingStatusBadge(QLabel):
    """设置状态徽标，只表达配置状态，不暴露配置值。"""

    def __init__(self, configured=False, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(68)
        self.setFixedHeight(24)
        _setChineseUiFont(self, 9, QFont.Weight.DemiBold)
        self.setConfigured(configured)

    def setConfigured(
        self,
        configured: bool,
        configuredText: str = "已配置",
        missingText: str = "未配置",
    ) -> None:
        self.setProperty("configured", bool(configured))
        self.setText(f"● {configuredText if configured else missingText}")
        if configured:
            self.setStyleSheet(
                "QLabel { color: #107C10; background: #EAF5EA; "
                "border: 1px solid #CDE5CD; border-radius: 12px; padding: 0 9px; }"
            )
        else:
            self.setStyleSheet(
                "QLabel { color: #A17C00; background: #FBF7E6; "
                "border: 1px solid #E9DDA6; border-radius: 12px; padding: 0 9px; }"
            )


class OverviewGroupCard(GroupHeaderCardWidget):
    """设计稿中的设置概览卡片。"""

    def __init__(self, title: str, headerIcon, summary: str = "", parent=None):
        super().__init__(parent)
        self.setTitle(title)
        self.setFixedWidth(832)
        self.setObjectName("overviewSettingCard")

        self.headerIconContainer = QWidget(self.headerView)
        self.headerIconContainer.setFixedSize(28, 28)
        self.headerIconContainer.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        self.headerIconContainer.setStyleSheet(
            f"background: {_ACCENT_SOFT}; border-radius: 6px;"
        )
        headerIconLayout = QHBoxLayout(self.headerIconContainer)
        headerIconLayout.setContentsMargins(6, 6, 6, 6)
        self.headerIcon = IconWidget(
            _accentIcon(headerIcon), self.headerIconContainer
        )
        self.headerIcon.setFixedSize(16, 16)
        headerIconLayout.addWidget(self.headerIcon)
        self.headerLayout.insertWidget(0, self.headerIconContainer)
        self.headerLayout.setSpacing(12)
        self.headerLayout.setContentsMargins(24, 0, 24, 0)
        self.headerView.setFixedHeight(60)

        self.headerSummaryLabel = CaptionLabel(summary, self.headerView)
        self.headerSummaryLabel.setStyleSheet(f"color: {_MUTED};")
        self.headerLayout.addStretch(1)
        self.headerLayout.addWidget(self.headerSummaryLabel)

        _setChineseUiFont(self.headerLabel, 11, QFont.Weight.DemiBold)
        self.setBorderRadius(8)
        self._applyCardStyle()
        qconfig.themeChangedFinished.connect(self._applyCardStyle)
        # 不使用 QGraphicsDropShadowEffect：设置页在导航的 300ms 切页动画中
        # 会连续重绘四张大卡片，实时模糊阴影会让每帧开销接近翻倍。
        # 设计稿的层级感由 1px 边框和浅色背景保留，避免首次进入与滚动卡顿。

    def _applyCardStyle(self) -> None:
        """主题刷新后恢复卡片样式，并保持所有文字子控件透明。"""
        cardColor = "#2B2B2B" if isDarkTheme() else "#FFFFFF"
        borderColor = "#383838" if isDarkTheme() else _BORDER
        self.setStyleSheet(
            f"#overviewSettingCard {{ background: {cardColor}; "
            f"border: 1px solid {borderColor}; border-radius: 8px; }}"
            f"#overviewSettingCard > #headerView {{ background: {cardColor}; "
            "border-top-left-radius: 8px; border-top-right-radius: 8px; }"
            "#overviewSettingCard > #view { background: transparent; }"
            "#overviewSettingCard FluentLabelBase { background-color: transparent; }"
            "#overviewSettingCard > #headerView > #headerLabel { "
            "background-color: transparent; }"
        )

    def setHeaderSummary(self, text: str) -> None:
        self.headerSummaryLabel.setText(text)

    def addGroup(self, icon, title, content, widget, stretch=0):
        group = super().addGroup(icon, title, content, widget, stretch)
        group.setMinimumHeight(76)
        group.hBoxLayout.setContentsMargins(24, 18, 24, 18)
        group.hBoxLayout.setSpacing(16)
        group.textLayout.setSpacing(3)
        group.textLayout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        group.titleLabel.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        group.contentLabel.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        # CardGroupWidget 默认在文字与控件之间插入 stretch，文字列只按
        # sizeHint 分配宽度，中文说明会过早换行。移除该 spacer 并让文字列
        # 吸收剩余空间，行为与设计稿的 flex: 1 一致。
        spacerItem = group.hBoxLayout.itemAt(2)
        if spacerItem is not None and spacerItem.spacerItem() is not None:
            group.hBoxLayout.takeAt(2)
        group.hBoxLayout.setStretch(1, 1)
        group.hBoxLayout.removeWidget(group.iconWidget)
        group.iconContainer = QWidget(group)
        group.iconContainer.setFixedSize(QSize(36, 36))
        group.iconContainer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        group.iconContainer.setStyleSheet(
            f"background: {_ACCENT_SOFT}; border-radius: 8px;"
        )
        iconLayout = QHBoxLayout(group.iconContainer)
        iconLayout.setContentsMargins(8, 8, 8, 8)
        group.iconWidget.setIcon(_accentIcon(icon))
        group.iconWidget.setParent(group.iconContainer)
        group.iconWidget.setFixedSize(QSize(20, 20))
        group.iconWidget.setStyleSheet("background: transparent;")
        iconLayout.addWidget(group.iconWidget)
        group.hBoxLayout.insertWidget(0, group.iconContainer)
        group.contentLabel.setWordWrap(False)
        group.contentLabel.setTextColor(QColor(_MUTED), QColor("#A8B0BC"))
        _setChineseUiFont(group.titleLabel, 10, QFont.Weight.DemiBold)
        _setChineseUiFont(group.contentLabel, 9)
        return group

    def addInfoBanner(self, text: str) -> QLabel:
        bannerWrapper = QWidget(self.view)
        bannerLayout = QVBoxLayout(bannerWrapper)
        bannerLayout.setContentsMargins(24, 16, 24, 16)
        banner = QLabel(text, bannerWrapper)
        banner.setWordWrap(True)
        banner.setMinimumHeight(50)
        banner.setContentsMargins(14, 8, 14, 8)
        banner.setStyleSheet(
            f"QLabel {{ color: #087B70; background: {_ACCENT_SOFT}; "
            "border: 1px solid #BFECE5; border-radius: 7px; }"
        )
        _setChineseUiFont(banner, 9)
        bannerLayout.addWidget(banner)
        self.groupLayout.addWidget(bannerWrapper)
        return banner


class SoftwareSettingWidget(OverviewGroupCard):
    """软件设置组件"""

    def __init__(self, parent=None):
        super().__init__("下载功能设置", FluentIcon.DOWNLOAD, "HskDataFetcher", parent)

        # 下载保存路径按钮
        self.downloadPathButton = PushButton("选择保存路径", self)
        self.downloadPathButton.setIcon(FluentIcon.FOLDER)
        self.downloadPathButton.setFixedHeight(32)
        self.downloadPathButton.clicked.connect(self._selectDownloadPath)
        self.downloadPathLabel = CaptionLabel(
            qconfig.get(cfg.DownloadSavePath), self
        )
        self.downloadPathLabel.setToolTip(qconfig.get(cfg.DownloadSavePath))
        self.downloadPathLabel.setFixedWidth(228)
        self.downloadPathLabel.setFixedHeight(32)
        pathFont = QFont("Cascadia Mono")
        pathFont.setFamilies(["Cascadia Mono", "Consolas", "Microsoft YaHei UI"])
        pathFont.setPointSize(9)
        self.downloadPathLabel.setFont(pathFont)
        self.downloadPathLabel.setStyleSheet(
            "color: #4B5563; background: #F7F8FA; border: 1px solid #E5E7EB; "
            "border-radius: 6px; padding: 7px 10px;"
        )

        # 每页数量选择
        self.pageNumsComboBox = ComboBox(self)
        self._fillNumericCombo(
            self.pageNumsComboBox,
            (10, 20, 50, 100),
            qconfig.get(cfg.NumberPerDownloads),
            "{} 条 / 页",
        )
        self.pageNumsComboBox.currentIndexChanged.connect(self._onPageNumsChanged)

        # 下载线程数选择
        self.threadsComboBox = ComboBox(self)
        self._fillNumericCombo(
            self.threadsComboBox,
            range(1, 7),
            qconfig.get(cfg.ThreadPerDownloads),
            "{} 线程",
        )
        self.threadsComboBox.currentIndexChanged.connect(self._onThreadsChanged)

        # 最大重试次数选择
        self.maxTriesComboBox = ComboBox(self)
        self._fillNumericCombo(
            self.maxTriesComboBox,
            range(1, 11),
            qconfig.get(cfg.MaximumAttempts),
            "{} 次",
        )
        self.maxTriesComboBox.currentIndexChanged.connect(self._onMaxTriesChanged)

        # HSK Token刷新按钮
        self.hskRefreshButton = PushButton("刷新", self)
        self.hskRefreshButton.setIcon(FluentIcon.SYNC)
        self.hskRefreshButton.setFixedHeight(32)
        self.hskRefreshButton.clicked.connect(self._onHskRefresh)
        self.hskTokenBadge = SettingStatusBadge(bool(qconfig.get(cfg.HSKLoginToken)), self)

        # Global Token刷新按钮
        self.globalRefreshButton = PushButton("刷新", self)
        self.globalRefreshButton.setIcon(FluentIcon.SYNC)
        self.globalRefreshButton.setFixedHeight(32)
        self.globalRefreshButton.clicked.connect(self._onGlobalRefresh)
        self.globalTokenBadge = SettingStatusBadge(
            bool(qconfig.get(cfg.GlobalLoginToken)), self
        )

        # 添加设置组
        self._addSettingGroups()

        for combo in (
            self.pageNumsComboBox,
            self.threadsComboBox,
            self.maxTriesComboBox,
        ):
            combo.setFixedSize(140, 32)

    @staticmethod
    def _fillNumericCombo(combo, values, current, labelTemplate):
        for value in values:
            combo.addItem(labelTemplate.format(value), userData=value)
        index = combo.findData(current)
        combo.setCurrentIndex(max(0, index))

    def _buildPathAction(self) -> QWidget:
        wrapper = QWidget(self)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.downloadPathLabel)
        layout.addWidget(self.downloadPathButton)
        return wrapper

    def _buildTokenAction(self, badge, button) -> QWidget:
        wrapper = QWidget(self)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(badge)
        layout.addWidget(button)
        return wrapper

    def _addSettingGroups(self):
        """添加设置组"""
        self.addGroup(
            FluentIcon.FOLDER,
            "保存路径",
            "语料文件、词表与图片的本地存储根目录",
            self._buildPathAction(),
        )
        self.addGroup(
            FluentIcon.MENU,
            "每页检索数量",
            "单次请求返回的语料条目上限",
            self.pageNumsComboBox,
        )
        self.addGroup(
            FluentIcon.LIBRARY,
            "下载线程数",
            "并发拉取任务的工作线程数 · 1 ～ 6",
            self.threadsComboBox,
        )
        self.addGroup(
            FluentIcon.SYNC,
            "最大尝试次数",
            "下载失败后的最大重试次数",
            self.maxTriesComboBox,
        )
        self.addGroup(
            FluentIcon.VPN,
            "HSK Token",
            "用于访问 HSK 语料接口的身份令牌",
            self._buildTokenAction(self.hskTokenBadge, self.hskRefreshButton),
        )
        self.addGroup(
            FluentIcon.IOT,
            "Global Token",
            "用于访问公共语料网关的身份令牌",
            self._buildTokenAction(self.globalTokenBadge, self.globalRefreshButton),
        )

    def _showSuccessMessage(self, title: str, content: str):
        """显示成功提示"""
        InfoBar.success(
            title,
            content,
            Qt.Orientation.Horizontal,
            True,
            2000,
            InfoBarPosition.TOP_RIGHT,
            self.window(),
        )

    def _showErrorMessage(self, title: str, content: str):
        """显示错误提示"""
        InfoBar.error(
            title,
            content,
            Qt.Orientation.Horizontal,
            True,
            3000,
            InfoBarPosition.TOP_RIGHT,
            self.window(),
        )

    def _selectDownloadPath(self):
        """选择下载保存路径"""
        folderPath = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folderPath:
            qconfig.set(cfg.DownloadSavePath, folderPath)
            self.downloadPathLabel.setText(folderPath)
            self.downloadPathLabel.setToolTip(folderPath)
            logger.info(f"[Setting] 下载保存路径已修改: {folderPath}")
            self._showSuccessMessage("O(∩_∩)O 修改成功", "下载保存路径修改成功")
        else:
            logger.debug("[Setting] 用户取消选择下载保存路径")

    def _onPageNumsChanged(self, *args):
        """每页数量变更处理"""
        value = self.pageNumsComboBox.currentData()
        if value is None:
            return
        qconfig.set(cfg.NumberPerDownloads, value)
        logger.info(f"[Setting] 每页检索数量已修改: {value}")
        self._showSuccessMessage("O(∩_∩)O 修改成功", "每页检索数量修改成功")

    def _onThreadsChanged(self, *args):
        """线程数变更处理"""
        value = self.threadsComboBox.currentData()
        if value is None:
            return
        qconfig.set(cfg.ThreadPerDownloads, value)
        logger.info(f"[Setting] 下载线程数量已修改: {value}")
        self._showSuccessMessage("O(∩_∩)O 修改成功", "线程数量修改成功")

    def _onMaxTriesChanged(self, *args):
        """最大重试次数变更处理"""
        value = self.maxTriesComboBox.currentData()
        if value is None:
            return
        qconfig.set(cfg.MaximumAttempts, value)
        logger.info(f"[Setting] 最大尝试次数已修改: {value}")
        self._showSuccessMessage("O(∩_∩)O 修改成功", "最大尝试次数修改成功")

    def _onHskRefresh(self):
        """刷新HSK Token"""
        logger.info("[Setting] 开始刷新HSK Token...")

        # 获取已保存的凭证
        savedUsername = qconfig.get(cfg.HSKLoginUsername)
        savedPassword = qconfig.get(cfg.HSKLoginPassword)

        # 显示登录对话框，自动填充已保存的凭证
        from app.view.widgets.token_refresh_dialog import TokenRefreshDialog

        dialog = TokenRefreshDialog(
            "HSK登录", savedUsername or "", savedPassword or "", self.window()
        )
        dialog.usernameEdit.setPlaceholderText("请输入HSK账号邮箱")
        dialog.passwordEdit.setPlaceholderText("请输入HSK密码")

        if dialog.exec():
            credentials = dialog.getCredentials()
            username = credentials["username"]
            password = credentials["password"]

            if not username or not password:
                self._showErrorMessage("输入错误", "用户名和密码不能为空")
                return

            # 保存用户名密码到配置
            qconfig.set(cfg.HSKLoginUsername, username)
            qconfig.set(cfg.HSKLoginPassword, password)

            self.hskRefreshButton.setEnabled(False)
            self.hskRefreshButton.setText("刷新中...")

            # 创建并启动线程，传入凭证
            self.hskThread = HskTokenRefreshThread(username, password)
            self.hskThread.finished.connect(self._onHskRefreshFinished)
            self.hskThread.error.connect(self._onHskRefreshError)
            self.hskThread.start()
        else:
            logger.info("[Setting] 用户取消HSK Token刷新")

    def _onHskRefreshFinished(self, token: str):
        """HSK Token刷新完成"""
        self.hskRefreshButton.setEnabled(True)
        self.hskRefreshButton.setText("刷新")

        # 保存Token
        qconfig.set(cfg.HSKLoginToken, token)

        self.hskTokenBadge.setConfigured(True)

        # 发送信号
        signalBus.hskTokenRefreshSignal.emit(token)

        logger.info("[Setting] HSK Token刷新并保存成功")
        self._showSuccessMessage("O(∩_∩)O 刷新成功", "HSK-Token已刷新")

    def _onHskRefreshError(self, error: str):
        """HSK Token刷新错误"""
        self.hskRefreshButton.setEnabled(True)
        self.hskRefreshButton.setText("刷新")

        logger.error(f"[Setting] HSK Token刷新失败: {error}")
        self._showErrorMessage("刷新失败", error)

    def _onGlobalRefresh(self):
        """刷新Global Token"""
        logger.info("[Setting] 开始刷新Global Token...")

        # 获取已保存的凭证
        savedUserId = qconfig.get(cfg.GlobalLoginUsername)
        savedPassword = qconfig.get(cfg.GlobalLoginPassword)

        # 显示登录对话框，自动填充已保存的凭证
        from app.view.widgets.token_refresh_dialog import TokenRefreshDialog

        dialog = TokenRefreshDialog(
            "Global登录", savedUserId or "", savedPassword or "", self.window()
        )
        dialog.usernameEdit.setPlaceholderText("请输入Global UserID")
        dialog.passwordEdit.setPlaceholderText("请输入Global Password")

        if dialog.exec():
            credentials = dialog.getCredentials()
            userId = credentials["username"]
            password = credentials["password"]

            if not userId or not password:
                self._showErrorMessage("输入错误", "UserID和密码不能为空")
                return

            # 保存凭证到配置
            qconfig.set(cfg.GlobalLoginUsername, userId)
            qconfig.set(cfg.GlobalLoginPassword, password)

            self.globalRefreshButton.setEnabled(False)
            self.globalRefreshButton.setText("刷新中...")

            # 创建并启动线程
            self.globalThread = GlobalTokenRefreshThread(userId, password)
            self.globalThread.finished.connect(self._onGlobalRefreshFinished)
            self.globalThread.error.connect(self._onGlobalRefreshError)
            self.globalThread.start()
        else:
            logger.info("[Setting] 用户取消Global Token刷新")

    def _onGlobalRefreshFinished(self, token: str):
        """Global Token刷新完成"""
        self.globalRefreshButton.setEnabled(True)
        self.globalRefreshButton.setText("刷新")

        # 保存Token
        qconfig.set(cfg.GlobalLoginToken, token)

        self.globalTokenBadge.setConfigured(True)

        # 发送信号
        signalBus.globalTokenRefreshSignal.emit(token)

        logger.info("[Setting] Global Token刷新并保存成功")
        self._showSuccessMessage("O(∩_∩)O 刷新成功", "Global-Token已刷新")

    def _onGlobalRefreshError(self, error: str):
        """Global Token刷新错误"""
        self.globalRefreshButton.setEnabled(True)
        self.globalRefreshButton.setText("刷新")

        logger.error(f"[Setting] Global Token刷新失败: {error}")
        self._showErrorMessage("刷新失败", error)


class AiInsightSettingWidget(OverviewGroupCard):
    """AI 解读设置组件（PRD-001 REQ-AI-001）

    与「AI 聊天设置」共用同一套 LLM 配置（API Key / Base URL / 模型 ID），
    本卡只暴露解读独有的设置项:
        - 解读风格（学术 / 通俗 / 简洁）

    注意：API Key / Base URL / 模型 请在「AI 聊天设置」中配置，本卡不重复。
    """

    def __init__(self, parent=None):
        super().__init__("AI 解读设置", FluentIcon.EXPRESSIVE_INPUT_ENTRY, "语料解读风格", parent)

        # ---- 字段 ----
        self.styleCombo = ComboBox()
        styleLabels = {
            "学术": "学术 · 引经据典术语规范",
            "通俗": "通俗 · 清晰自然易理解",
            "简洁": "简洁 · 聚焦核心结论",
        }
        for value, label in styleLabels.items():
            self.styleCombo.addItem(label, userData=value)
        currentStyle = qconfig.get(cfg.AiInsightStyle) or "学术"
        self.styleCombo.setCurrentIndex(max(0, self.styleCombo.findData(currentStyle)))
        self.styleCombo.setFixedSize(184, 32)
        self.styleCombo.currentIndexChanged.connect(
            lambda _index: qconfig.set(
                cfg.AiInsightStyle, self.styleCombo.currentData()
            )
        )

        # ---- 添加设置组 ----
        self.addGroup(
            FluentIcon.PALETTE,
            "解读风格",
            "控制 AI 解读语料时的语气与详细程度",
            self.styleCombo,
        )
        self.statusLabel = self.addInfoBanner(self._summaryText())

        # 字段变化 → 刷新状态条
        self.styleCombo.currentIndexChanged.connect(self._refreshStatus)
        # 监听共用 LLM 配置变化（API Key / 模型 / Base URL 改完时即时刷新）
        cfg.AiApiKey.valueChanged.connect(self._refreshStatus)
        cfg.AiModelChat.valueChanged.connect(self._refreshStatus)
        cfg.AiBaseUrl.valueChanged.connect(self._refreshStatus)

    def _refreshStatus(self, *_args) -> None:
        self.statusLabel.setText(self._summaryText())

    def _summaryText(self) -> str:
        return (
            "ⓘ  AI 解读与 AI 聊天共用同一套 LLM，请到「AI 聊天设置」中配置 "
            "API Key、Base URL 与模型。"
        )


class AiChatSettingWidget(OverviewGroupCard):
    """AI 聊天设置组件

    允许用户配置:
        - API Key (DeepSeek / OpenAI 等 OpenAI 兼容服务)
        - Base URL
        - Chat 模型(自由输入)
        - 多轮上下文轮数
        - 系统提示词

    所有配置项通过 ``qconfig`` 持久化,与 ``cfg`` 中对应键双向同步,
    设置变更后底部状态条实时刷新摘要。
    """

    def __init__(self, parent=None):
        super().__init__("AI 聊天设置", FluentIcon.CHAT, "LLM · DeepSeek", parent)

        # ---- 表单字段 ----
        self.apiKeyEdit = LineEdit()
        self.apiKeyEdit.setPlaceholderText("请输入 API Key(支持 DeepSeek / OpenAI 等)")
        self.apiKeyEdit.setEchoMode(LineEdit.EchoMode.Password)
        self.apiKeyEdit.setText(qconfig.get(cfg.AiApiKey) or "")
        self.apiKeyEdit.textChanged.connect(
            lambda v: qconfig.set(cfg.AiApiKey, v.strip())
        )
        self.apiKeyEdit.setFixedSize(240, 32)
        self.apiKeyVisibilityButton = PushButton("显示", self)
        self.apiKeyVisibilityButton.setIcon(FluentIcon.VIEW)
        self.apiKeyVisibilityButton.setFixedSize(76, 32)
        self.apiKeyVisibilityButton.clicked.connect(self._toggleApiKeyVisibility)

        self.baseUrlEdit = LineEdit()
        self.baseUrlEdit.setPlaceholderText("https://api.deepseek.com")
        self.baseUrlEdit.setText(
            qconfig.get(cfg.AiBaseUrl) or "https://api.deepseek.com"
        )
        self.baseUrlEdit.textChanged.connect(
            lambda v: qconfig.set(cfg.AiBaseUrl, v.strip())
        )
        self.baseUrlEdit.setFixedSize(260, 32)

        # Chat 模型:自由输入框,用户可填任意模型 ID
        self.chatModelEdit = LineEdit()
        self.chatModelEdit.setPlaceholderText("如 deepseek-chat / gpt-4o / qwen-max …")
        self.chatModelEdit.setText(qconfig.get(cfg.AiModelChat) or "deepseek-chat")
        self.chatModelEdit.textChanged.connect(
            lambda v: qconfig.set(cfg.AiModelChat, v.strip())
        )
        self.chatModelEdit.setFixedSize(200, 32)

        self.maxHistoryCombo = ComboBox()
        for n in (5, 10, 20, 50):
            self.maxHistoryCombo.addItem(f"{n} 轮", userData=n)
        historyIndex = self.maxHistoryCombo.findData(
            qconfig.get(cfg.AiMaxHistory) or 10
        )
        self.maxHistoryCombo.setCurrentIndex(max(0, historyIndex))
        self.maxHistoryCombo.setFixedSize(140, 32)
        self.maxHistoryCombo.currentIndexChanged.connect(
            lambda _index: qconfig.set(
                cfg.AiMaxHistory, self.maxHistoryCombo.currentData()
            )
        )

        # 系统提示词:不再用输入框,改为本地文件上传(.txt / .md / .json 等文本文件)
        self.systemPromptFileLabel = CaptionLabel(self._systemPromptText())
        self.systemPromptFileLabel.setFixedSize(297, 32)
        self.systemPromptFileLabel.setToolTip(self._systemPromptText())
        promptPathFont = QFont("Cascadia Mono")
        promptPathFont.setFamilies(
            ["Cascadia Mono", "Consolas", "Microsoft YaHei UI"]
        )
        promptPathFont.setPointSize(9)
        self.systemPromptFileLabel.setFont(promptPathFont)
        self.systemPromptFileLabel.setStyleSheet(
            "color: #4B5563; background: #F7F8FA; border: 1px solid #E5E7EB; "
            "border-radius: 6px; padding: 7px 10px;"
        )
        self.systemPromptFileButton = PushButton("选择提示词")
        self.systemPromptFileButton.setIcon(FluentIcon.DOCUMENT)
        self.systemPromptFileButton.setFixedSize(152, 32)
        self.systemPromptClearButton = PushButton("清除")
        self.systemPromptClearButton.setIcon(FluentIcon.CLOSE)
        self.systemPromptClearButton.setFixedSize(76, 32)
        self.systemPromptFileButton.clicked.connect(self._chooseSystemPromptFile)
        self.systemPromptClearButton.clicked.connect(self._clearSystemPromptFile)

        # ---- 状态展示 ----
        self.statusLabel = CaptionLabel("当前 LLM:")
        self.statusLabel.setStyleSheet(f"color: {_MUTED};")

        # ---- 添加设置组 ----
        self.addGroup(
            FluentIcon.VPN,
            "API Key",
            "用于调用大模型服务的鉴权密钥 · 已启用密码遮罩",
            self._buildApiKeyWidget(),
        )
        self.addGroup(
            FluentIcon.LINK,
            "Base URL",
            "OpenAI 兼容接口的接入地址",
            self.baseUrlEdit,
        )
        self.addGroup(
            FluentIcon.IOT,
            "Chat 模型",
            "用于聊天的模型标识",
            self.chatModelEdit,
        )
        self.addGroup(
            FluentIcon.HISTORY,
            "历史轮数",
            "聊天上下文中保留的最大消息往返数",
            self.maxHistoryCombo,
        )
        systemPromptGroup = self.addGroup(
            FluentIcon.DOCUMENT,
            "系统提示词",
            "作为 system message 注入的自定义提示词文件（.md / .txt）",
            self._buildSystemPromptWidget(),
        )
        systemPromptGroup.contentLabel.setWordWrap(True)
        systemPromptGroup.setMinimumHeight(92)

        # ---- 设计稿中的紧凑状态栏 ----
        self.statusFooter = QFrame(self.view)
        self.statusFooter.setObjectName("aiStatusFooter")
        self.statusFooter.setStyleSheet(
            "#aiStatusFooter { background: #F5F5F5; border-top: 1px solid #E5E5E5; "
            "border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; }"
        )
        self.statusFooter.setFixedHeight(44)
        statusRowLayout = QHBoxLayout(self.statusFooter)
        statusRowLayout.setContentsMargins(24, 10, 24, 10)
        statusRowLayout.setSpacing(8)
        statusIcon = IconWidget(_accentIcon(FluentIcon.SPEED_MEDIUM), self.statusFooter)
        statusIcon.setFixedSize(16, 16)
        statusRowLayout.addWidget(statusIcon)
        statusRowLayout.addWidget(self.statusLabel)
        self.modelPill = QLabel(self.statusFooter)
        self.modelPill.setStyleSheet(
            "QLabel { color: #1F1F1F; background: white; border: 1px solid #E5E5E5; "
            "border-radius: 10px; padding: 2px 8px; }"
        )
        self.historyPrefixLabel = CaptionLabel("·  历史", self.statusFooter)
        self.historyPrefixLabel.setStyleSheet(f"color: {_MUTED};")
        self.historyPill = QLabel(self.statusFooter)
        self.historyPill.setStyleSheet(
            "QLabel { color: #1F1F1F; background: white; border: 1px solid #E5E5E5; "
            "border-radius: 10px; padding: 2px 8px; }"
        )
        statusRowLayout.addWidget(self.modelPill)
        statusRowLayout.addWidget(self.historyPrefixLabel)
        statusRowLayout.addWidget(self.historyPill)
        statusRowLayout.addStretch(1)
        effectiveLabel = CaptionLabel("设置保存后立即生效", self.statusFooter)
        effectiveLabel.setStyleSheet(f"color: {_MUTED};")
        statusRowLayout.addWidget(effectiveLabel)
        self.groupLayout.addWidget(self.statusFooter)

        # 字段变更 → 刷新状态条
        for sig in (
            self.apiKeyEdit.textChanged,
            self.baseUrlEdit.textChanged,
            self.chatModelEdit.textChanged,
            self.maxHistoryCombo.currentIndexChanged,
        ):
            sig.connect(self._refreshStatus)

        self._refreshStatus()

    def _buildApiKeyWidget(self) -> QWidget:
        wrapper = QWidget(self)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.apiKeyEdit)
        layout.addWidget(self.apiKeyVisibilityButton)
        return wrapper

    def _toggleApiKeyVisibility(self) -> None:
        isHidden = self.apiKeyEdit.echoMode() == LineEdit.EchoMode.Password
        self.apiKeyEdit.setEchoMode(
            LineEdit.EchoMode.Normal if isHidden else LineEdit.EchoMode.Password
        )
        self.apiKeyVisibilityButton.setText("隐藏" if isHidden else "显示")
        self.apiKeyVisibilityButton.setIcon(
            FluentIcon.HIDE if isHidden else FluentIcon.VIEW
        )

    def _refreshStatus(self, *_args) -> None:
        chatModel = qconfig.get(cfg.AiModelChat) or "deepseek-chat"
        maxHistory = qconfig.get(cfg.AiMaxHistory) or 10
        self.modelPill.setText(chatModel)
        self.historyPill.setText(f"{maxHistory} 轮")
        self.setHeaderSummary(f"LLM · {self._providerName()}")

    def _providerName(self) -> str:
        baseUrl = (qconfig.get(cfg.AiBaseUrl) or "").lower()
        if "deepseek" in baseUrl:
            return "DeepSeek"
        if "openai" in baseUrl:
            return "OpenAI"
        return "自定义服务"

    def _summaryText(self) -> str:
        chatModel = qconfig.get(cfg.AiModelChat) or "deepseek-chat"
        maxHist = qconfig.get(cfg.AiMaxHistory) or 10
        return f"当前 LLM：{chatModel} · 历史 {maxHist} 轮"

    # ---- 系统提示词(文件上传)----
    def _buildSystemPromptWidget(self) -> QWidget:
        """组装系统提示词控件:状态标签 + 选择/清除按钮"""
        wrapper = QWidget(self)
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.systemPromptFileLabel)
        layout.addWidget(self.systemPromptFileButton)
        layout.addWidget(self.systemPromptClearButton)
        return wrapper

    def _systemPromptText(self) -> str:
        """当前已选择的提示词文件路径(简短描述)"""
        raw = (qconfig.get(cfg.AiSystemPrompt) or "").strip()
        if not raw:
            return "默认提示词"
        if "\n" in raw or not Path(raw).is_file():
            # 早期版本残留的纯文本,提示用户清除或重新选择
            firstLine = raw.splitlines()[0][:60]
            return f"检测到旧版文本提示词(首行:{firstLine}…),建议清除后重新选择文件。"
        return raw

    def _chooseSystemPromptFile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择系统提示词文件",
            "",
            "文本文件 (*.txt *.md *.json *.yaml *.yml);;所有文件 (*)",
        )
        if not path:
            return
        try:
            # 读取一次以校验可访问性(以及提前排除空文件)
            content = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
            if not content:
                InfoBar.warning(
                    title="",
                    content="所选文件为空,将使用默认提示词。",
                    position=InfoBarPosition.TOP,
                    parent=self.window(),
                    duration=2500,
                )
        except OSError as e:
            logger.error(f"[Setting] 读取提示词文件失败: {e}")
            InfoBar.error(
                title="读取失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=3000,
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )
            return
        qconfig.set(cfg.AiSystemPrompt, path)
        self.systemPromptFileLabel.setText(self._systemPromptText())
        self.systemPromptFileLabel.setToolTip(path)
        InfoBar.success(
            title="",
            content=f"已设置提示词文件:{Path(path).name}",
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=2500,
        )

    def _clearSystemPromptFile(self) -> None:
        qconfig.set(cfg.AiSystemPrompt, "")
        self.systemPromptFileLabel.setText(self._systemPromptText())
        self.systemPromptFileLabel.setToolTip(self._systemPromptText())
        InfoBar.success(
            title="",
            content="已清除提示词文件,将使用默认提示词。",
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=2500,
        )




class AboutSettingWidget(OverviewGroupCard):
    """关于软件设置组件"""

    def __init__(self, parent=None):
        # 导入设置信息
        from app.core.utils.setting import VERSION, APP_NAME, YEAR
        super().__init__("关于软件", FluentIcon.INFO, f"{APP_NAME} · Desktop Client", parent)

        # 重新查看引导按钮(2026-07-28 新增)
        # - 点击后会重置 cfg.MainTourShown=False,并在主窗口上重新弹出引导遮罩
        self.retourButton = PrimaryPushButton("重新查看引导", self)
        self.retourButton.setIcon(FluentIcon.PLAY.icon(color=QColor("white")))
        self.retourButton.setFixedHeight(32)
        self.retourButton.clicked.connect(self._restartMainTour)

        self.openBadge = SettingStatusBadge(True, self)
        self.openBadge.setConfigured(True, "内测开放")

        # 获取系统信息
        systemInfo = self._getSystemInfoItems()
        displayVersion = VERSION if str(VERSION).lower().startswith("v") else f"v{VERSION}"

        # 添加版本号组
        self.addGroup(
            FluentIcon.ZIP_FOLDER,
            f"棱溯客户端 {displayVersion}",
            f"{YEAR} · 贵州六棱光界科技工作室",
            self.openBadge,
        )

        # 添加「重新查看引导」组(2026-07-28 新增)
        self.addGroup(
            FluentIcon.LAYOUT,
            "主窗口引导",
            "重新播放首次启动时的功能引导，熟悉主界面布局",
            self.retourButton,
        )

        self.groupWidgets[-1].setSeparatorVisible(True)
        self.systemInfoSection = self._buildSystemInfoSection(systemInfo)
        self.groupLayout.addWidget(self.systemInfoSection)

    def _buildSystemInfoSection(self, infoItems) -> QWidget:
        section = QWidget(self.view)
        sectionLayout = QVBoxLayout(section)
        sectionLayout.setContentsMargins(24, 18, 24, 18)
        sectionLayout.setSpacing(10)

        header = QWidget(section)
        headerLayout = QHBoxLayout(header)
        headerLayout.setContentsMargins(0, 0, 0, 0)
        headerLayout.setSpacing(8)
        icon = IconWidget(_accentIcon(FluentIcon.DEVELOPER_TOOLS), header)
        icon.setFixedSize(16, 16)
        title = BodyLabel("系统信息", header)
        _setChineseUiFont(title, 10, QFont.Weight.DemiBold)
        healthBadge = SettingStatusBadge(True, header)
        healthBadge.setConfigured(True, "健康")
        headerLayout.addWidget(icon)
        headerLayout.addWidget(title)
        headerLayout.addStretch(1)
        headerLayout.addWidget(healthBadge)
        sectionLayout.addWidget(header)

        gridWidget = QWidget(section)
        grid = QGridLayout(gridWidget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(4)
        for index, (key, value) in enumerate(infoItems):
            cell = QFrame(gridWidget)
            cell.setStyleSheet(
                "QFrame { border: none; border-bottom: 1px dashed #E5E5E5; }"
            )
            cellLayout = QHBoxLayout(cell)
            cellLayout.setContentsMargins(0, 6, 0, 6)
            keyLabel = CaptionLabel(key, cell)
            keyLabel.setStyleSheet(f"color: {_MUTED}; border: none;")
            valueLabel = CaptionLabel(value, cell)
            valueLabel.setStyleSheet(f"color: {_TEXT}; border: none;")
            cellLayout.addWidget(keyLabel)
            cellLayout.addStretch(1)
            cellLayout.addWidget(valueLabel)
            grid.addWidget(cell, index // 2, index % 2)
        sectionLayout.addWidget(gridWidget)
        return section

    def _restartMainTour(self):
        """重新启动主窗口引导遮罩(2026-07-28 新增)。

        流程:
            1) 把 cfg.MainTourShown 设为 False(允许下次启动也弹)
            2) 在主窗口上构造并启动 MainTourOverlay
            3) 提示用户引导已开始,可在「跳过引导」中提前结束
        """
        try:
            # 1) 重置持久化标记
            qconfig.set(cfg.MainTourShown, False)
            logger.info("[Setting] 用户点击「重新查看引导」,重置 cfg.MainTourShown")

            # 2) 找到顶层主窗口(向上查找 QWidget 链)
            mainWindow = self.window()
            # window() 在 widget 尚未 show 时可能返回 None,
            # 退而求其次查找 QApplication.activeWindow()
            if mainWindow is None or not isinstance(mainWindow, QWidget):
                try:
                    from PySide6.QtWidgets import QApplication

                    mainWindow = QApplication.activeWindow()
                except Exception:
                    pass
            if mainWindow is None:
                # 最后的兜底:遍历 topLevelWidgets
                try:
                    from PySide6.QtWidgets import QApplication

                    for w in QApplication.topLevelWidgets():
                        if w.isVisible():
                            mainWindow = w
                            break
                except Exception:
                    pass

            if mainWindow is None:
                logger.warning("[Setting] 重启引导失败:找不到顶层主窗口")
                InfoBar.warning(
                    title="无法启动引导",
                    content="请重新打开主窗口后再试。",
                    parent=self,
                    duration=2500,
                    position=InfoBarPosition.TOP,
                )
                return

            # 3) 检查 MainTourOverlay 是否已经存在(避免重复叠加)
            try:
                from app.view.widgets.main_tour_overlay import MainTourOverlay

                existing = mainWindow.findChild(MainTourOverlay)
                if existing is not None:
                    logger.info("[Setting] 已存在引导遮罩,先关闭再重启")
                    existing._completeTour(writeCfg=False)
            except Exception as e:
                logger.warning(f"[Setting] 检查已有引导遮罩失败: {e}")

            # 4) 构造并启动新引导
            try:
                overlay = MainTourOverlay(mainWindow)
                overlay.start()
                logger.info("[Setting] 主窗口引导遮罩已重新弹出")
                InfoBar.success(
                    title="引导已重新启动",
                    content="跟随卡片提示浏览全部主窗口功能。",
                    parent=self,
                    duration=2000,
                    position=InfoBarPosition.TOP,
                )
            except Exception as e:
                logger.warning(f"[Setting] 启动引导遮罩失败: {e}")
                InfoBar.error(
                    title="启动引导失败",
                    content=str(e),
                    parent=self,
                    duration=3000,
                    position=InfoBarPosition.TOP,
                )
        except Exception as e:
            logger.exception(f"[Setting] 重新查看引导失败: {e}")

    def _getSystemInfoItems(self):
        """获取系统信息"""
        import platform
        import psutil

        infoItems = [("系统", f"{platform.system()} {platform.release()}")]

        # CPU信息
        try:
            cpuCount = psutil.cpu_count(logical=False)
            cpuCountLogical = psutil.cpu_count(logical=True)
            infoItems.append(("CPU", f"{cpuCount} 核 / {cpuCountLogical} 线程"))
        except Exception:
            infoItems.append(("CPU", platform.machine()))

        # 内存信息
        try:
            mem = psutil.virtual_memory()
            memGb = mem.total / (1024**3)
            memUsedGb = mem.used / (1024**3)
            infoItems.append(
                ("内存", f"{memUsedGb:.1f} GB / {memGb:.1f} GB ({mem.percent}%)")
            )
        except Exception:
            infoItems.append(("内存", "未知"))

        # 磁盘信息
        try:
            disk = psutil.disk_usage("/")
            diskGb = disk.total / (1024**3)
            diskUsedGb = disk.used / (1024**3)
            infoItems.append(
                ("磁盘", f"{diskUsedGb:.0f} GB / {diskGb:.0f} GB ({disk.percent}%)")
            )
        except Exception:
            infoItems.append(("磁盘", "未知"))

        return infoItems


class AgreementLabelWidget(QWidget):
    """用户协议组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setupLayout()

    def _setupLayout(self):
        """设置布局"""
        hBoxLayout = QHBoxLayout(self)
        hBoxLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 定价协议链接
        self.privacyPolicyLabel = HyperlinkLabel("定价协议", self)
        self.privacyPolicyLabel.setUrl("https://docs.qq.com/pdf/DTnFzeXhjWXBRd3h0")

        # 用户协议链接
        self.userAgreementLabel = HyperlinkLabel("用户协议", self)
        self.userAgreementLabel.setUrl("https://docs.qq.com/pdf/DTkhGeXVsWXBGTWN4")

        # 分隔符
        self.separator = VerticalSeparator(self)
        self.separator.setFixedHeight(15)

        # 添加组件
        hBoxLayout.addWidget(self.privacyPolicyLabel, 0, Qt.AlignmentFlag.AlignCenter)
        hBoxLayout.addSpacing(10)
        hBoxLayout.addWidget(self.separator)
        hBoxLayout.addSpacing(10)
        hBoxLayout.addWidget(self.userAgreementLabel, 0, Qt.AlignmentFlag.AlignCenter)


class SettingInterface(ScrollArea):
    """设置界面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._initScrollWidget()
        self._initComponents()
        self._initWidget()
        self._initLayout()
        self._connectSignals()

    def _initScrollWidget(self):
        """初始化滚动区域"""
        self.scrollWidget = QWidget()
        self.expandLayout = QVBoxLayout(self.scrollWidget)
        self.contentWidget = QWidget(self.scrollWidget)
        self.contentWidget.setFixedWidth(832)
        self.contentLayout = QVBoxLayout(self.contentWidget)
        self.contentLayout.setContentsMargins(0, 0, 0, 0)
        self.contentLayout.setSpacing(32)

    def _initComponents(self):
        """初始化组件"""
        self.heroWidget = QWidget(self.contentWidget)
        heroLayout = QVBoxLayout(self.heroWidget)
        heroLayout.setContentsMargins(0, 24, 0, 10)
        heroLayout.setSpacing(0)
        heroLayout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        heroIconContainer = QWidget(self.heroWidget)
        heroIconContainer.setFixedSize(56, 56)
        heroIconContainer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        heroIconContainer.setStyleSheet(
            f"background: {_ACCENT_SOFT}; border-radius: 28px;"
        )
        heroIconLayout = QHBoxLayout(heroIconContainer)
        heroIconLayout.setContentsMargins(16, 16, 16, 16)
        self.iconLabel = IconWidget(
            _accentIcon(FluentIcon.SETTING), heroIconContainer
        )
        self.iconLabel.setFixedSize(24, 24)
        heroIconLayout.addWidget(self.iconLabel)

        self.titleLabel = BodyLabel("设置", self.heroWidget)
        _setChineseUiFont(self.titleLabel, 22, QFont.Weight.Bold)
        self.titleLabel.setStyleSheet(f"color: {_TEXT};")
        self.titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        from app.core.utils.setting import VERSION

        displayVersion = VERSION if str(VERSION).lower().startswith("v") else f"v{VERSION}"
        self.subtitleLabel = CaptionLabel(
            f"棱溯客户端 {displayVersion} · 个性化你的工作流", self.heroWidget
        )
        _setChineseUiFont(self.subtitleLabel, 10)
        self.subtitleLabel.setStyleSheet(f"color: {_MUTED};")
        self.subtitleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heroLayout.addWidget(heroIconContainer, 0, Qt.AlignmentFlag.AlignHCenter)
        heroLayout.addSpacing(22)
        heroLayout.addWidget(self.titleLabel, 0, Qt.AlignmentFlag.AlignHCenter)
        heroLayout.addSpacing(8)
        heroLayout.addWidget(self.subtitleLabel, 0, Qt.AlignmentFlag.AlignHCenter)
        heroLayout.addSpacing(4)

        # 软件设置组件
        self.softwareSettingWidget = SoftwareSettingWidget(self.scrollWidget)

        # AI 聊天设置组件
        self.aiChatSettingWidget = AiChatSettingWidget(self.scrollWidget)

        # AI 解读设置组件（PRD-001 REQ-AI-001）
        self.aiInsightSettingWidget = AiInsightSettingWidget(self.scrollWidget)

        # 关于设置组件
        self.aboutSettingWidget = AboutSettingWidget(self.scrollWidget)

        # 用户协议组件
        self.agreementLabelWidget = AgreementLabelWidget(self.scrollWidget)

        # 版权信息
        self.infoLabel = BodyLabel(
            "©2026 棱溯 · 贵州六棱光界科技工作室",
            self.scrollWidget,
        )
        self.footerWidget = QWidget(self.contentWidget)
        footerLayout = QVBoxLayout(self.footerWidget)
        footerLayout.setContentsMargins(0, 0, 0, 0)
        footerLayout.setSpacing(8)
        footerLayout.addWidget(self.agreementLabelWidget)
        footerLayout.addWidget(self.infoLabel)

    def _initWidget(self):
        """初始化部件属性"""
        self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setObjectName("SettingInterface")

        self.scrollWidget.setObjectName("scrollWidget")
        self._applyPageTheme()
        qconfig.themeChangedFinished.connect(self._applyPageTheme)
        self.setStyleSheet("background:transparent;border:none;")

        self.infoLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.infoLabel.setStyleSheet("color:gray;font-size:12px;")

    def _applyPageTheme(self) -> None:
        """让设置页画布与其他业务页面使用同一背景令牌。"""
        self.scrollWidget.setStyleSheet(
            f"background:{pageBackgroundColor().name()};border:none;"
        )

    def _initLayout(self):
        """初始化布局"""
        self.expandLayout.setContentsMargins(24, 40, 24, 40)
        self.expandLayout.setSpacing(0)
        self.expandLayout.addWidget(
            self.contentWidget, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
        )

        self.contentLayout.addWidget(self.heroWidget)
        self.contentLayout.addWidget(self.softwareSettingWidget)
        self.contentLayout.addWidget(self.aiChatSettingWidget)
        self.contentLayout.addWidget(self.aiInsightSettingWidget)
        self.contentLayout.addWidget(self.aboutSettingWidget)
        self.contentLayout.addWidget(self.footerWidget)

    def _connectSignals(self):
        """连接信号槽"""
        pass
