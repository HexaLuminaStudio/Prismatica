# coding: utf-8
"""
设置界面模块
提供软件设置、关于信息、激活码管理和用户协议等功能
"""

from PySide6.QtCore import Qt
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from typing import List, Tuple
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
)

from app.core.services import HskTokenRefreshThread, GlobalTokenRefreshThread
from app.core.utils import cfg, qconfig, logger, signalBus


class SoftwareSettingWidget(GroupHeaderCardWidget):
    """软件设置组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("下载功能设置")
        logger.info("[Setting] SoftwareSettingWidget 初始化")

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


class LicenseDialog(MessageBoxBase):
    """激活码管理弹窗(完整 UI)

    P1-fix 2026-07-18:把激活管理从「设置页面里塞一整个 GroupHeaderCardWidget」
    改为「弹窗」。设置页面仅保留一行状态入口,点击后弹出本对话框。
    弹窗内容(从原 LicenseSettingWidget 迁移):
        - 设备码显示 + 复制按钮
        - 激活码输入 + 激活按钮(仅正式版)
        - 内测版功能列表(仅内测版)
        - 当前模式 / 激活状态

    行为:
        - 用 dialog.exec() 模态显示
        - 关闭后,刷新设置页的状态入口显示
    """

    def __init__(self, parent=None, isBeta: bool = False):
        # P0-fix 2026-07-18:MessageBoxBase.__init__ 会访问 parent.width/height,
        # parent=None 时抛 AttributeError。这里回退到 activeWindow 或临时 widget。
        if parent is None:
            from PySide6.QtWidgets import QApplication, QWidget

            app = QApplication.instance()
            if app is not None:
                parent = app.activeWindow()
            if parent is None:
                parent = QWidget()
                parent.resize(800, 600)
                import sys as _sys

                _sys.modules.setdefault("__license_dialog_fallback_parent__", parent)
        super().__init__(parent=parent)
        self._isBeta = isBeta
        self._buildUi()
        self._wireEvents()
        self._loadDeviceId()
        self._updateActivationStatus()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _buildUi(self):
        """构建弹窗 UI"""
        # 自定义标题
        self.titleLabel = BodyLabel("激活码管理", self)
        self.titleLabel.setStyleSheet(
            "font-size: 16px; font-weight: 600; color: #0078D4;"
        )
        self.viewLayout.addWidget(self.titleLabel)

        # ---- 设备码组 ----
        deviceIdLayout = QHBoxLayout()
        deviceIdLayout.setContentsMargins(0, 4, 0, 4)
        deviceIdLayout.setSpacing(8)

        self.deviceIdLabel = BodyLabel("正在获取设备码...", self)
        self.deviceIdLabel.setMinimumWidth(360)

        self.copyDeviceIdButton = PushButton("复制设备码", self)
        self.copyDeviceIdButton.setIcon(":app/icons/Copy.svg")
        self.copyDeviceIdButton.clicked.connect(self._copyDeviceId)

        deviceIdLayout.addWidget(self.deviceIdLabel, 1)
        deviceIdLayout.addWidget(self.copyDeviceIdButton)

        deviceIdWidget = QWidget(self)
        deviceIdWidget.setLayout(deviceIdLayout)
        self.viewLayout.addWidget(deviceIdWidget)

        # 设备码说明
        deviceHint = CaptionLabel("请将此设备码提供给管理员以获取激活码", self)
        deviceHint.setStyleSheet("color: #888; font-size: 11px;")
        self.viewLayout.addWidget(deviceHint)

        # ---- 激活码组(仅正式版) / 内测版功能组(仅内测版) ----
        if not self._isBeta:
            self._buildActivationGroup()
        else:
            self._buildBetaFeaturesGroup()

        # ---- 状态组 ----
        self.statusLabel = BodyLabel("", self)
        self.statusLabel.setWordWrap(True)
        self.viewLayout.addWidget(self.statusLabel)

        # ---- 重写 yes/cancel 按钮(只保留关闭按钮)----
        # MessageBoxBase 自带 yes/no 按钮,这里重新设置文案
        self.yesButton.setText("关闭")
        self.yesButton.setIcon(":app/icons/Check.svg")
        self.cancelButton.setVisible(False)  # 隐藏「取消」按钮
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self.accept)

    def _buildActivationGroup(self):
        """构建激活码输入组(正式版)"""
        activationLayout = QHBoxLayout()
        activationLayout.setContentsMargins(0, 4, 0, 4)
        activationLayout.setSpacing(8)

        self.activationCodeLineEdit = LineEdit(self)
        self.activationCodeLineEdit.setMinimumWidth(360)
        self.activationCodeLineEdit.setPlaceholderText("请输入激活码")
        self.activationCodeLineEdit.textChanged.connect(self._onActivationCodeChanged)

        self.activateButton = PushButton("激活", self)
        self.activateButton.setIcon(":app/icons/Check.svg")
        self.activateButton.clicked.connect(self._activateLicense)
        self.activateButton.setEnabled(False)

        activationLayout.addWidget(self.activationCodeLineEdit, 1)
        activationLayout.addWidget(self.activateButton)

        activationWidget = QWidget(self)
        activationWidget.setLayout(activationLayout)
        self.viewLayout.addWidget(activationWidget)

        # 激活码说明
        activationHint = CaptionLabel("输入激活码以解锁高级功能", self)
        activationHint.setStyleSheet("color: #888; font-size: 11px;")
        self.viewLayout.addWidget(activationHint)

    def _buildBetaFeaturesGroup(self):
        """构建内测版功能列表组(内测版)"""
        from app.core.utils.setting import BETA_FEATURES

        featureContainer = QWidget(self)
        featureLayout = QVBoxLayout(featureContainer)
        featureLayout.setContentsMargins(0, 4, 0, 4)
        featureLayout.setSpacing(8)

        # 内测版说明(顶)
        introLabel = BodyLabel("内测期间所有功能均可免费使用,无需激活码", self)
        introLabel.setStyleSheet("color: #c97a00; font-weight: 600; font-size: 12px;")
        self.viewLayout.addWidget(introLabel)

        for iconKey, title, desc in BETA_FEATURES:
            row = QHBoxLayout()
            row.setSpacing(12)

            iconWidget = QSvgWidget(f":app/icons/{iconKey}", featureContainer)
            iconWidget.setFixedSize(20, 20)
            row.addWidget(iconWidget, 0, Qt.AlignmentFlag.AlignVCenter)

            textCol = QVBoxLayout()
            textCol.setSpacing(0)
            titleLabel = BodyLabel(title, featureContainer)
            titleLabel.setStyleSheet(
                "font-weight: 600; color: #0078D4; font-size: 13px;"
            )
            descLabel = CaptionLabel(desc, featureContainer)
            descLabel.setStyleSheet("color: #888; font-size: 11px;")
            descLabel.setWordWrap(True)
            textCol.addWidget(titleLabel)
            textCol.addWidget(descLabel)

            row.addLayout(textCol, 1)
            featureLayout.addLayout(row)

        featureContainer.setLayout(featureLayout)
        self.viewLayout.addWidget(featureContainer)

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _wireEvents(self):
        """绑定事件(预留,目前 _buildUi 已直接绑定)"""
        pass

    def _loadDeviceId(self):
        """加载设备码"""
        try:
            from app.core.utils import generateOrLoadDeviceId

            deviceId = generateOrLoadDeviceId()
            formattedId = "-".join(
                [deviceId[i : i + 8] for i in range(0, len(deviceId), 8)]
            )
            self.deviceIdLabel.setText(formattedId)
            logger.info(f"[Setting] 设备码加载成功: {deviceId[:16]}...")
        except Exception as e:
            logger.error(f"[Setting] 设备码加载失败: {e}")
            self.deviceIdLabel.setText("设备码获取失败，请重试")

    def _copyDeviceId(self):
        """复制设备码到剪贴板"""
        from PySide6.QtWidgets import QApplication

        deviceId = self.deviceIdLabel.text().replace("-", "")
        clipboard = QApplication.clipboard()
        clipboard.setText(deviceId)
        logger.info("[Setting] 设备码已复制到剪贴板")
        self._showInfoBar("success", "O(∩_∩)O 复制成功", "设备码已复制到剪贴板")

    def _onActivationCodeChanged(self, code: str):
        """激活码变更时切换激活按钮可用状态"""
        isValid = len(code.strip()) >= 16
        if hasattr(self, "activateButton") and self.activateButton is not None:
            self.activateButton.setEnabled(isValid)

    def _activateLicense(self):
        """激活许可证

        P1-fix(2026-07-18):license.activate() 返回值改为 dict,这里根据
        result["success"] 与 result["message"] 给出准确提示,避免笼统的
        「数据保存失败」掩盖了真实原因(磁盘权限、加密失败等)。
        """
        activationCode = self.activationCodeLineEdit.text().strip()

        if not activationCode:
            self._showInfoBar("error", "激活失败", "请输入激活码")
            return

        logger.info(f"[Setting] 尝试激活,激活码: {activationCode[:8]}...")

        deviceId = self.deviceIdLabel.text().replace("-", "")

        from app.core.utils.license import getLicenseManager

        licenseManager = getLicenseManager()

        result = licenseManager.verifyActivationCode(activationCode, deviceId)
        if not result["success"]:
            logger.warning(f"[Setting] 激活失败: {result['message']}")
            self._showInfoBar("error", "激活失败", result["message"])
            return

        activateResult = licenseManager.activate(activationCode, deviceId)
        if not activateResult.get("success"):
            logger.error(
                f"[Setting] 激活失败: {activateResult.get('message', '未知错误')}"
            )
            self._showInfoBar(
                "error",
                "激活失败",
                activateResult.get("message", "数据保存失败，请重试"),
            )
            return

        logger.info("[Setting] 激活成功")
        self._showInfoBar("success", "O(∩_∩)O 激活成功", "高级功能已解锁")
        self._updateActivationStatus()
        signalBus.activationStatusChanged.emit(True)
        self.activationCodeLineEdit.clear()

    def _updateActivationStatus(self):
        """更新激活状态显示"""
        from app.core.utils.license import getLicenseManager

        licenseManager = getLicenseManager()

        if self._isBeta:
            status = licenseManager.ensureBetaTimelock()
            daysRemaining = status.get("daysRemaining", -1)
            deadline = status.get("deadline", "")

            if status.get("status") == "activated":
                statusText = "正式版已激活"
            elif daysRemaining < 0:
                statusText = "内测版 | 首次启动,所有功能可免费使用"
            elif daysRemaining == 0:
                statusText = f"内测期已结束(截止 {deadline})"
            else:
                statusText = f"内测版 | 剩余 {daysRemaining} 天 | 截止 {deadline}"

            self.statusLabel.setText(statusText)
            self.statusLabel.setStyleSheet(
                "color: #c97a00; font-size: 13px; padding: 4px 0;"
            )
            logger.info(f"[Setting] 当前状态(内测): {statusText}")
            return

        # 正式版
        if licenseManager.isActivated():
            userType = licenseManager.getUserType()
            expiryDate = licenseManager.getExpiryDate()
            daysRemaining = licenseManager.getDaysRemaining()
            statusText = f"{userType} | 有效期至 {expiryDate} | 剩余 {daysRemaining} 天"
            self.statusLabel.setText(statusText)
            self.statusLabel.setStyleSheet(
                "color: #2c8a4a; font-size: 13px; padding: 4px 0;"
            )
            # 禁用激活输入(激活成功后)
            if (
                hasattr(self, "activationCodeLineEdit")
                and self.activationCodeLineEdit is not None
            ):
                self.activationCodeLineEdit.setEnabled(False)
            if hasattr(self, "activateButton") and self.activateButton is not None:
                self.activateButton.setEnabled(False)
                self.activateButton.setText("已激活")
            logger.info(f"[Setting] 当前状态: {statusText}")
        else:
            self.statusLabel.setText("未激活")
            self.statusLabel.setStyleSheet(
                "color: #888; font-size: 13px; padding: 4px 0;"
            )
            logger.info("[Setting] 当前状态: 未激活")

    def _showInfoBar(self, level: str, title: str, content: str):
        """显示 InfoBar(成功/错误)"""
        showFn = getattr(InfoBar, level, None)
        if showFn is None:
            return
        showFn(
            title,
            content,
            Qt.Orientation.Horizontal,
            True,
            2500,
            InfoBarPosition.TOP_RIGHT,
            self.window(),
        )


class LicenseSettingWidget(GroupHeaderCardWidget):
    """激活码管理设置入口(仅一行)

    P1-fix 2026-07-18:把原来占据整页的「激活码管理」卡片
    (设备码 + 激活输入 + 状态 + 内测版功能列表)压缩为一行,
    用户点「管理激活码」按钮弹出 LicenseDialog 完整 UI。

    设置页结构:
        [图标] 激活码管理  状态:xxx               [管理激活码 →]
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("激活码管理")
        logger.info("[Setting] LicenseSettingWidget 初始化")
        self._isBeta = self._detectBeta()
        self._buildOneRowEntry()

    def _detectBeta(self) -> bool:
        """判断当前是否为内测版"""
        try:
            from app.core.utils.setting import IS_BETA

            return bool(IS_BETA)
        except Exception as e:
            logger.warning(f"[Setting] 检测 IS_BETA 失败: {e}")
            return False

    def _buildOneRowEntry(self):
        """构建一行入口(图标 + 状态 + 按钮)"""
        from app.core.utils.license import getLicenseManager

        rowLayout = QHBoxLayout()
        rowLayout.setContentsMargins(0, 0, 0, 0)
        rowLayout.setSpacing(12)

        # 状态文本(单行摘要,详细状态在弹窗内)
        statusText, statusColor = self._summaryStatus()
        self.statusLabel = BodyLabel(statusText, self)
        self.statusLabel.setStyleSheet(f"color: {statusColor}; font-size: 13px;")
        self.statusLabel.setMinimumWidth(360)

        rowLayout.addWidget(self.statusLabel, 1)

        # 「管理激活码」按钮
        self.manageButton = PushButton("管理激活码 →", self)
        self.manageButton.setIcon(":app/icons/Setting.svg")
        self.manageButton.clicked.connect(self._openLicenseDialog)

        rowLayout.addWidget(self.manageButton)

        rowWidget = QWidget(self)
        rowWidget.setLayout(rowLayout)

        self.addGroup(
            ":app/icons/Advance.svg",
            "激活码",
            "查看 / 管理激活状态、复制设备码、输入激活码",
            rowWidget,
        )

    def _summaryStatus(self) -> tuple[str, str]:
        """返回单行状态摘要与颜色

        Returns:
            (status_text, color_hex)
        """
        try:
            from app.core.utils.license import getLicenseManager

            licenseManager = getLicenseManager()

            if self._isBeta:
                status = licenseManager.ensureBetaTimelock()
                daysRemaining = status.get("daysRemaining", -1)
                deadline = status.get("deadline", "")
                if daysRemaining < 0:
                    return (
                        "内测版 | 首次启动,所有功能可免费使用",
                        "#c97a00",
                    )
                if daysRemaining == 0:
                    return (
                        f"内测期已结束(截止 {deadline})",
                        "#b00",
                    )
                return (
                    f"内测版 | 剩余 {daysRemaining} 天 | 截止 {deadline}",
                    "#c97a00",
                )

            # 正式版
            if licenseManager.isActivated():
                userType = licenseManager.getUserType()
                expiryDate = licenseManager.getExpiryDate()
                daysRemaining = licenseManager.getDaysRemaining()
                return (
                    f"{userType} | 有效期至 {expiryDate} | 剩余 {daysRemaining} 天",
                    "#2c8a4a",
                )
            return ("未激活", "#888")
        except Exception as e:
            logger.error(f"[Setting] 读取激活状态失败: {e}")
            return ("状态读取失败", "#b00")

    def _openLicenseDialog(self):
        """打开激活码管理弹窗"""
        try:
            dialog = LicenseDialog(parent=self.window(), isBeta=self._isBeta)
            dialog.setWindowTitle("Prismatica - 激活码管理")
            dialog.exec()
        except Exception as e:
            logger.exception(f"[Setting] 打开激活码弹窗失败: {e}")
        else:
            # 关闭弹窗后刷新设置页状态摘要(可能激活 / 反激活)
            self._refreshSummary()

    def _refreshSummary(self):
        """刷新设置页状态摘要文本

        弹窗关闭后(可能是激活成功 / 输入了错误码),调用此方法
        让设置页的单行状态文字同步更新。
        """
        statusText, statusColor = self._summaryStatus()
        self.statusLabel.setText(statusText)
        self.statusLabel.setStyleSheet(f"color: {statusColor}; font-size: 13px;")

    def _showSuccessMessage(self, title: str, content: str):
        """显示成功提示(兼容旧 API)"""
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
        """显示错误提示(兼容旧 API)"""
        InfoBar.error(
            title,
            content,
            Qt.Orientation.Horizontal,
            True,
            3000,
            InfoBarPosition.TOP_RIGHT,
            self.window(),
        )


class SponsorSettingWidget(GroupHeaderCardWidget):
    """赞助名单组件

    展示本项目所有赞助人的昵称与邮箱。
    数据为内部写死（开发者后续可手动更新 _SPONSORS 列表），
    以"昵称<邮箱>"格式每行展示一位赞
    静态占位数据：
        - 使用示例昵称与示例邮箱，方便开发者按相同格式直接替换为真实数据
    """

    _SPONSORS: List[Tuple[str, str]] = [
        ("🙉", "192****613@qq.com"),
        ("小红薯69B5B903", "224****626@qq.com"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("赞助名单")
        logger.info(
            f"[Setting] SponsorSettingWidget 初始化,共 {len(self._sponsors())} 位赞助人"
        )

        # 列表显示控件:多行只读文本,展示「昵称<邮箱>」
        self.sponsorListLabel = BodyLabel(self._formatSponsorList(), self)
        self.sponsorListLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.sponsorListLabel.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 13px;"
            "line-height: 1.7;"
        )
        self.sponsorListLabel.setWordWrap(True)

        # 添加设置组
        self._addSettingGroups()

    def _formatSponsorList(self) -> str:
        """格式化赞助人列表为多行文本「昵称<邮箱>」。"""
        sponsors = self._sponsors()
        if not sponsors:
            return "（暂无赞助记录）"
        lines = []
        for nickname, email in sponsors:
            # 行内使用全角尖括号以视觉上更友好,且避免与邮箱中的 @/特殊字符冲突
            lines.append(f"{nickname} <{email}>")
        return "\n".join(lines)

    @classmethod
    def _sponsors(cls) -> List[Tuple[str, str]]:
        """获取赞助人列表(子类可重写此方法自定义数据源)。"""
        return cls._SPONSORS

    def _addSettingGroups(self):
        """添加设置组"""
        # 顶部说明
        self.addGroup(
            ":app/icons/Advance.svg",
            "感谢以下赞助者",
            "本软件由以下用户慷慨赞助支持",
            QWidget(),
        )
        # 列表组
        self.addGroup(
            ":app/icons/Contact.svg",
            f"赞助人名单（共 {len(self._sponsors())} 位）",
            "点击文本可复制单行内容",
            self.sponsorListLabel,
        )


class AboutSettingWidget(GroupHeaderCardWidget):
    """关于软件设置组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("关于软件")

        # 导入设置信息
        from app.core.utils.setting import VERSION, APP_NAME, YEAR, AUTHOR

        # 软件反馈按钮
        self.feedbackButton = PushButton("提交反馈", self)
        self.feedbackButton.clicked.connect(self._openFeedback)

        # 获取系统信息
        systemInfo = self._getSystemInfo()

        # 添加版本号组
        self.addGroup(
            ":app/icons/Information.svg",
            "版本号",
            f"{APP_NAME} {VERSION} | {YEAR} - {AUTHOR}",
            QWidget(),
        )

        # 添加软件反馈组
        self.addGroup(
            ":app/icons/Feedback.svg",
            "软件反馈",
            "遇到问题或有建议？点击提交反馈",
            self.feedbackButton,
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

    def _openFeedback(self):
        """打开反馈页面"""
        # P0-fix:authKey 是 QQ 加群链接的密钥,硬编码在源码里任何人翻代码即可
        # 拿到,可能被滥用刷群。改为从环境变量 / 配置文件读取,源码只留占位。
        import os
        import webbrowser

        qqAuthKey = os.environ.get(
            "PRISMATICA_QQ_AUTH_KEY", "PLACEHOLDER_REPLACE_VIA_ENV"
        )
        webbrowser.open(
            "https://qm.qq.com/cgi-bin/qm/qr?k=kpUC2epMMuLEO90kx-BB6VcJJrKqfhyT"
            f"&jump_from=webapi&authKey={qqAuthKey}"
        )

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

        # 激活码设置组件
        self.licenseSettingWidget = LicenseSettingWidget(self.scrollWidget)

        # 关于设置组件
        self.aboutSettingWidget = AboutSettingWidget(self.scrollWidget)

        # 赞助名单组件（页面下部分）
        self.sponsorSettingWidget = SponsorSettingWidget(self.scrollWidget)

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
            self.licenseSettingWidget, 0, Qt.AlignmentFlag.AlignTop
        )
        self.expandLayout.addSpacing(20)
        self.expandLayout.addWidget(
            self.aboutSettingWidget, 0, Qt.AlignmentFlag.AlignTop
        )
        self.expandLayout.addSpacing(20)
        self.expandLayout.addWidget(
            self.sponsorSettingWidget, 0, Qt.AlignmentFlag.AlignTop
        )
        self.expandLayout.addWidget(
            self.agreementLabelWidget, 1, Qt.AlignmentFlag.AlignBottom
        )
        self.expandLayout.addSpacing(5)
        self.expandLayout.addWidget(self.infoLabel, 0, Qt.AlignmentFlag.AlignBottom)

    def _connectSignals(self):
        """连接信号槽"""
        pass
