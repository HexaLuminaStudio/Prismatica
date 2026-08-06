# coding: utf-8
"""
设置界面模块
提供软件设置、关于信息、激活码管理和用户协议等功能
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    GroupHeaderCardWidget,
    HyperlinkLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBoxBase,
    PushButton,
    ScrollArea,
    VerticalSeparator,
    HyperlinkButton,
)

from app.core.services import HskTokenRefreshThread, GlobalTokenRefreshThread
from app.core.utils import cfg, qconfig, logger, signalBus


class SoftwareSettingWidget(GroupHeaderCardWidget):
    """软件设置组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("下载功能设置")

        # 下载保存路径按钮
        self.downloadPathButton = PushButton("选择保存路径", self)
        self.downloadPathButton.clicked.connect(self._selectDownloadPath)

        # 每页数量选择
        self.pageNumsComboBox = ComboBox(self)
        self.pageNumsComboBox.addItems(["10", "20", "50", "100"])
        self.pageNumsComboBox.setCurrentText(str(qconfig.get(cfg.NumberPerDownloads)))
        self.pageNumsComboBox.currentTextChanged.connect(self._onPageNumsChanged)

        # 下载线程数选择
        self.threadsComboBox = ComboBox(self)
        self.threadsComboBox.addItems([str(i) for i in range(1, 7)])
        self.threadsComboBox.setCurrentText(str(qconfig.get(cfg.ThreadPerDownloads)))
        self.threadsComboBox.currentTextChanged.connect(self._onThreadsChanged)

        # 最大重试次数选择
        self.maxTriesComboBox = ComboBox(self)
        self.maxTriesComboBox.addItems([str(i) for i in range(1, 11)])
        self.maxTriesComboBox.setCurrentText(str(qconfig.get(cfg.MaximumAttempts)))
        self.maxTriesComboBox.currentTextChanged.connect(self._onMaxTriesChanged)

        # HSK Token刷新按钮
        self.hskRefreshButton = PushButton("刷新", self)
        self.hskRefreshButton.setIcon(":app/icons/Refresh.svg")
        self.hskRefreshButton.clicked.connect(self._onHskRefresh)

        # Global Token刷新按钮
        self.globalRefreshButton = PushButton("刷新", self)
        self.globalRefreshButton.setIcon(":app/icons/Refresh.svg")
        self.globalRefreshButton.clicked.connect(self._onGlobalRefresh)

        # 添加设置组
        self._addSettingGroups()

        # 更新标题显示
        self.groupWidgets[0].setTitle(qconfig.get(cfg.DownloadSavePath))

    def _addSettingGroups(self):
        """添加设置组"""
        self.addGroup(
            ":app/icons/SavePath.svg",
            qconfig.get(cfg.DownloadSavePath),
            "选择下载保存路径",
            self.downloadPathButton,
        )
        self.addGroup(
            ":app/icons/Number.svg",
            "设置每页检索数量",
            "设置数量(建议数值:100)",
            self.pageNumsComboBox,
        )
        self.addGroup(
            ":app/icons/Thread.svg",
            "设置下载线程数量",
            "设置线程(建议数值:5)",
            self.threadsComboBox,
        )
        self.addGroup(
            ":app/icons/MaxTries.svg",
            "设置最大尝试次数",
            "设置次数(建议数值:3)",
            self.maxTriesComboBox,
        )
        self.addGroup(
            ":app/icons/Hsk.svg",
            "设置HSK-Token",
            qconfig.get(cfg.HSKLoginToken)[:100],
            self.hskRefreshButton,
        )
        self.addGroup(
            ":app/icons/Global.svg",
            "设置Global-Token",
            qconfig.get(cfg.GlobalLoginToken),
            self.globalRefreshButton,
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
            self.groupWidgets[0].setTitle(folderPath)
            logger.info(f"[Setting] 下载保存路径已修改: {folderPath}")
            self._showSuccessMessage("O(∩_∩)O 修改成功", "下载保存路径修改成功")
        else:
            logger.debug("[Setting] 用户取消选择下载保存路径")

    def _onPageNumsChanged(self, *args):
        """每页数量变更处理"""
        value = int(self.pageNumsComboBox.currentText())
        qconfig.set(cfg.NumberPerDownloads, value)
        logger.info(f"[Setting] 每页检索数量已修改: {value}")
        self._showSuccessMessage("O(∩_∩)O 修改成功", "每页检索数量修改成功")

    def _onThreadsChanged(self, *args):
        """线程数变更处理"""
        value = int(self.threadsComboBox.currentText())
        qconfig.set(cfg.ThreadPerDownloads, value)
        logger.info(f"[Setting] 下载线程数量已修改: {value}")
        self._showSuccessMessage("O(∩_∩)O 修改成功", "线程数量修改成功")

    def _onMaxTriesChanged(self, *args):
        """最大重试次数变更处理"""
        value = int(self.maxTriesComboBox.currentText())
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

        # 更新显示
        if len(self.groupWidgets) >= 5:
            # 更新groupWidget[4]的内容描述
            self.groupWidgets[4].setContent(token[:100])

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

        # 更新显示
        if len(self.groupWidgets) >= 6:
            self.groupWidgets[5].setContent(token)

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


class AiInsightSettingWidget(GroupHeaderCardWidget):
    """AI 解读设置组件（PRD-001 REQ-AI-001）

    与「AI 聊天设置」共用同一套 LLM 配置（API Key / Base URL / 模型 ID），
    本卡只暴露解读独有的设置项:
        - 解读风格（学术 / 通俗 / 简洁）

    注意：API Key / Base URL / 模型 请在「AI 聊天设置」中配置，本卡不重复。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("AI 解读设置")

        # ---- 字段 ----
        self.styleCombo = ComboBox()
        for s in ("学术", "通俗", "简洁"):
            self.styleCombo.addItem(s)
        currentStyle = qconfig.get(cfg.AiInsightStyle) or "学术"
        if currentStyle in ("学术", "通俗", "简洁"):
            self.styleCombo.setCurrentText(currentStyle)
        self.styleCombo.currentTextChanged.connect(
            lambda v: qconfig.set(cfg.AiInsightStyle, v)
        )

        # ---- 状态展示 ----
        self.statusLabel = CaptionLabel(self._summaryText())
        self.statusLabel.setStyleSheet("color: #888;")

        # ---- 添加设置组 ----
        self.addGroup(
            ":app/icons/Write.svg",
            "解读风格",
            "默认解读风格，生成 Prompt 时注入。",
            self.styleCombo,
        )
        self.addGroup(
            ":app/icons/SystemInfo.svg",
            "当前 LLM 配置",
            "AI 解读与 AI 聊天共用同一套 LLM，请到「AI 聊天设置」中配置。",
            self.statusLabel,
        )

        # 字段变化 → 刷新状态条
        self.styleCombo.currentTextChanged.connect(self._refreshStatus)
        # 监听共用 LLM 配置变化（API Key / 模型 / Base URL 改完时即时刷新）
        cfg.AiApiKey.valueChanged.connect(self._refreshStatus)
        cfg.AiModelChat.valueChanged.connect(self._refreshStatus)
        cfg.AiBaseUrl.valueChanged.connect(self._refreshStatus)

    def _refreshStatus(self, *_args) -> None:
        self.statusLabel.setText(self._summaryText())

    def _summaryText(self) -> str:
        apiKey = qconfig.get(cfg.AiApiKey) or ""
        if not apiKey:
            return "未配置 API Key(请到「AI 聊天设置」中填写)"
        model = qconfig.get(cfg.AiModelChat) or "deepseek-chat"
        style = qconfig.get(cfg.AiInsightStyle) or "学术"
        return f"Chat 模型: {model}  ·  解读风格: {style}"


class AiChatSettingWidget(GroupHeaderCardWidget):
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
        super().__init__(parent)
        self.setTitle("AI 聊天设置")

        # ---- 表单字段 ----
        self.apiKeyEdit = LineEdit()
        self.apiKeyEdit.setPlaceholderText("请输入 API Key(支持 DeepSeek / OpenAI 等)")
        self.apiKeyEdit.setEchoMode(LineEdit.EchoMode.Password)
        self.apiKeyEdit.setText(qconfig.get(cfg.AiApiKey) or "")
        self.apiKeyEdit.textChanged.connect(
            lambda v: qconfig.set(cfg.AiApiKey, v.strip())
        )

        self.baseUrlEdit = LineEdit()
        self.baseUrlEdit.setPlaceholderText("https://api.deepseek.com")
        self.baseUrlEdit.setText(
            qconfig.get(cfg.AiBaseUrl) or "https://api.deepseek.com"
        )
        self.baseUrlEdit.textChanged.connect(
            lambda v: qconfig.set(cfg.AiBaseUrl, v.strip())
        )

        # Chat 模型:自由输入框,用户可填任意模型 ID
        self.chatModelEdit = LineEdit()
        self.chatModelEdit.setPlaceholderText("如 deepseek-chat / gpt-4o / qwen-max …")
        self.chatModelEdit.setText(qconfig.get(cfg.AiModelChat) or "deepseek-chat")
        self.chatModelEdit.textChanged.connect(
            lambda v: qconfig.set(cfg.AiModelChat, v.strip())
        )

        self.maxHistoryCombo = ComboBox()
        for n in (5, 10, 20, 50):
            self.maxHistoryCombo.addItem(str(n))
        self.maxHistoryCombo.setCurrentText(str(qconfig.get(cfg.AiMaxHistory) or 10))
        self.maxHistoryCombo.currentTextChanged.connect(
            lambda v: qconfig.set(cfg.AiMaxHistory, int(v))
        )

        # 系统提示词:不再用输入框,改为本地文件上传(.txt / .md / .json 等文本文件)
        self.systemPromptFileLabel = CaptionLabel(self._systemPromptText())
        self.systemPromptFileLabel.setWordWrap(True)
        self.systemPromptFileLabel.setStyleSheet("color: #888;")
        self.systemPromptFileButton = PushButton("选择提示词文件…")
        self.systemPromptClearButton = PushButton("清除")
        self.systemPromptFileButton.clicked.connect(self._chooseSystemPromptFile)
        self.systemPromptClearButton.clicked.connect(self._clearSystemPromptFile)

        # ---- 状态展示 ----
        self.statusLabel = CaptionLabel(self._summaryText())
        self.statusLabel.setStyleSheet("color: #888;")

        # ---- 添加设置组 ----
        self.addGroup(
            ":app/icons/Setting.svg",
            "API Key",
            "用于调用大模型 API,请妥善保管。留空则「AI 聊天」页无法发送。",
            self.apiKeyEdit,
        )
        self.addGroup(
            ":app/icons/Information.svg",
            "API Base URL",
            "支持任意 OpenAI 兼容服务,默认 DeepSeek。",
            self.baseUrlEdit,
        )
        self.addGroup(
            ":app/icons/Chat.svg",
            "Chat 模型",
            "普通对话使用的模型 ID,需与 API 提供方一致。",
            self.chatModelEdit,
        )
        self.addGroup(
            ":app/icons/Status.svg",
            "历史轮数",
            "保留最近多少轮对话作为上下文,数值越大越费 token。",
            self.maxHistoryCombo,
        )
        self.addGroup(
            ":app/icons/Write.svg",
            "系统提示词",
            "所有对话开头的 system 消息,用于设定角色与风格。上传本地文本文件作为提示词内容。",
            self._buildSystemPromptWidget(),
        )

        # ---- 状态组(底部)----
        statusRow = QWidget(self)
        statusRowLayout = QHBoxLayout(statusRow)
        statusRowLayout.setContentsMargins(0, 4, 0, 0)
        statusRowLayout.addWidget(self.statusLabel)
        statusRowLayout.addStretch(1)
        self.addGroup(
            ":app/icons/SystemInfo.svg",
            "当前状态",
            "",
            statusRow,
        )

        # 字段变更 → 刷新状态条
        for sig in (
            self.apiKeyEdit.textChanged,
            self.chatModelEdit.textChanged,
            self.maxHistoryCombo.currentTextChanged,
        ):
            sig.connect(self._refreshStatus)

    def _refreshStatus(self, *_args) -> None:
        self.statusLabel.setText(self._summaryText())

    def _summaryText(self) -> str:
        if not qconfig.get(cfg.AiApiKey):
            return "未配置 API Key"
        chatModel = qconfig.get(cfg.AiModelChat) or "deepseek-chat"
        maxHist = qconfig.get(cfg.AiMaxHistory) or 10
        return f"Chat 模型: {chatModel}  ·  历史: {maxHist} 轮"

    # ---- 系统提示词(文件上传)----
    def _buildSystemPromptWidget(self) -> QWidget:
        """组装系统提示词控件:状态标签 + 选择/清除按钮"""
        wrapper = QWidget(self)
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.systemPromptFileLabel)

        btnRow = QWidget(wrapper)
        btnLayout = QHBoxLayout(btnRow)
        btnLayout.setContentsMargins(0, 0, 0, 0)
        btnLayout.setSpacing(8)
        btnLayout.addWidget(self.systemPromptFileButton)
        btnLayout.addWidget(self.systemPromptClearButton)
        btnLayout.addStretch(1)
        layout.addWidget(btnRow)
        return wrapper

    def _systemPromptText(self) -> str:
        """当前已选择的提示词文件路径(简短描述)"""
        raw = (qconfig.get(cfg.AiSystemPrompt) or "").strip()
        if not raw:
            return "未选择提示词文件,将使用默认提示词。"
        if "\n" in raw or not Path(raw).is_file():
            # 早期版本残留的纯文本,提示用户清除或重新选择
            firstLine = raw.splitlines()[0][:60]
            return f"检测到旧版文本提示词(首行:{firstLine}…),建议清除后重新选择文件。"
        return f"已选择:{raw}"

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
        InfoBar.success(
            title="",
            content="已清除提示词文件,将使用默认提示词。",
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=2500,
        )




class AboutSettingWidget(GroupHeaderCardWidget):
    """关于软件设置组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("关于软件")

        # 导入设置信息
        from app.core.utils.setting import VERSION, APP_NAME, YEAR, AUTHOR

        # 重新查看引导按钮(2026-07-28 新增)
        # - 点击后会重置 cfg.MainTourShown=False,并在主窗口上重新弹出引导遮罩
        self.retourButton = PushButton("重新查看引导", self)
        self.retourButton.clicked.connect(self._restartMainTour)

        # 获取系统信息
        systemInfo = self._getSystemInfo()

        # 添加版本号组
        self.addGroup(
            ":app/icons/Information.svg",
            "版本号",
            f"{APP_NAME} {VERSION} | {YEAR} - {AUTHOR}",
            QWidget(),
        )

        # 添加「重新查看引导」组(2026-07-28 新增)
        self.addGroup(
            ":app/icons/Hsk.svg",  # 复用现有图标资源,引导无专属图标
            "主窗口引导",
            "重新展示首次进入主窗口时的引导遮罩",
            self.retourButton,
        )

        # 系统信息组
        emptyWidget = QWidget(self)
        emptyWidget.setFixedWidth(1)
        self.addGroup(
            ":app/icons/SystemInfo.svg",
            "系统信息",
            systemInfo,
            emptyWidget,
        )

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

    def _getSystemInfo(self) -> str:
        """获取系统信息"""
        import platform
        import psutil

        infoParts = []

        # 操作系统
        infoParts.append(f"系统: {platform.system()} {platform.release()}")
        # CPU架构
        infoParts.append(f"架构: {platform.machine()}")

        # CPU信息
        try:
            cpuCount = psutil.cpu_count(logical=False)
            cpuCountLogical = psutil.cpu_count(logical=True)
            infoParts.append(f"CPU: {cpuCount}核{cpuCountLogical}线程")

            cpuFreq = psutil.cpu_freq()
            if cpuFreq:
                infoParts.append(f"频率: {cpuFreq.current:.0f}MHz")
        except Exception:
            pass

        # 内存信息
        try:
            mem = psutil.virtual_memory()
            memGb = mem.total / (1024**3)
            memUsedGb = mem.used / (1024**3)
            infoParts.append(f"内存: {memUsedGb:.1f}GB/{memGb:.1f}GB ({mem.percent}%)")
        except Exception:
            pass

        # 磁盘信息
        try:
            disk = psutil.disk_usage("/")
            diskGb = disk.total / (1024**3)
            diskUsedGb = disk.used / (1024**3)
            infoParts.append(
                f"磁盘: {diskUsedGb:.1f}GB/{diskGb:.1f}GB ({disk.percent}%)"
            )
        except Exception:
            pass

        return " | ".join(infoParts)


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

    def _initComponents(self):
        """初始化组件"""
        # 设置图标
        self.iconLabel = QSvgWidget(":app/icons/Setting.svg", self)
        self.iconLabel.setFixedSize(50, 50)

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
            " ©2026 棱溯 \n 贵州六棱光界科技工作室",
            self.scrollWidget,
        )

    def _initWidget(self):
        """初始化部件属性"""
        self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setObjectName("SettingInterface")

        self.scrollWidget.setObjectName("scrollWidget")
        self.scrollWidget.setStyleSheet("background:transparent;border:none;")
        self.setStyleSheet("background:transparent;border:none;")

        self.infoLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.infoLabel.setStyleSheet("color:gray;font-size:12px;")

    def _initLayout(self):
        """初始化布局"""
        self.expandLayout.addWidget(
            self.iconLabel, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
        )
        self.expandLayout.addSpacing(20)
        self.expandLayout.addWidget(
            self.softwareSettingWidget, 0, Qt.AlignmentFlag.AlignTop
        )
        self.expandLayout.addSpacing(20)
        self.expandLayout.addWidget(
            self.aiChatSettingWidget, 0, Qt.AlignmentFlag.AlignTop
        )
        self.expandLayout.addSpacing(20)
        self.expandLayout.addWidget(
            self.aiInsightSettingWidget, 0, Qt.AlignmentFlag.AlignTop
        )
        self.expandLayout.addSpacing(20)
        self.expandLayout.addWidget(
            self.aboutSettingWidget, 0, Qt.AlignmentFlag.AlignTop
        )
        self.expandLayout.addSpacing(20)
        self.expandLayout.addWidget(
            self.agreementLabelWidget, 1, Qt.AlignmentFlag.AlignBottom
        )
        self.expandLayout.addSpacing(5)
        self.expandLayout.addWidget(self.infoLabel, 0, Qt.AlignmentFlag.AlignBottom)

    def _connectSignals(self):
        """连接信号槽"""
        pass
