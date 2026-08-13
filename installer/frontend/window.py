# coding: utf-8
"""与 Prismatica 桌面端一致的 Fluent 安装窗口。"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import QFileInfo, QProcess, QTimer, Qt
from PySide6.QtGui import QCloseEvent, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    FluentIcon,
    IconWidget,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SubtitleLabel,
    TitleLabel,
    isDarkTheme,
    qconfig,
)
from qframelesswindow import FramelessWindow, StandardTitleBar

from installer.frontend.core import (
    APP_EXE_NAME,
    APP_NAME,
    APP_VERSION,
    InstallOptions,
    InstallWorker,
    buildInstallerArguments,
    defaultInstallDir,
    readProgressState,
)


ACCENT = "#00B09C"
ACCENT_DARK = "#56D6C5"
LIGHT_WINDOW = "#EEF3F6"
LIGHT_CONTENT = "#F6F8FA"
LIGHT_SURFACE = "#FFFFFF"
LIGHT_BORDER = "#D6DEE3"
LIGHT_TEXT = "#20262C"
LIGHT_MUTED = "#596873"
DARK_WINDOW = "#181B1E"
DARK_CONTENT = "#202428"
DARK_SURFACE = "#24292D"
DARK_BORDER = "#343B40"
DARK_TEXT = "#F3F6F7"
DARK_MUTED = "#AEB9BF"


class StepRow(QFrame):
    """左侧步骤导航中的单行状态。"""

    def __init__(self, number: int, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("installerStep")
        self._numberLabel = QLabel(str(number))
        self._numberLabel.setObjectName("installerStepNumber")
        self._numberLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._numberLabel.setFixedSize(28, 28)
        self._titleLabel = QLabel(title)
        self._titleLabel.setObjectName("installerStepTitle")
        self._titleLabel.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Medium))

        rowLayout = QHBoxLayout(self)
        rowLayout.setContentsMargins(0, 0, 0, 0)
        rowLayout.setSpacing(12)
        rowLayout.addWidget(self._numberLabel)
        rowLayout.addWidget(self._titleLabel)
        rowLayout.addStretch(1)
        self.setFixedHeight(42)

    def setState(self, state: str, *, isDark: bool) -> None:
        activeColor = ACCENT_DARK if isDark else "#007C70"
        mutedColor = DARK_MUTED if isDark else LIGHT_MUTED
        borderColor = DARK_BORDER if isDark else LIGHT_BORDER
        if state == "active":
            numberStyle = f"color: white; background: {activeColor}; border: none;"
            titleColor = DARK_TEXT if isDark else LIGHT_TEXT
        elif state == "done":
            numberStyle = f"color: {activeColor}; background: transparent; border: 1px solid {activeColor};"
            titleColor = activeColor
        else:
            numberStyle = f"color: {mutedColor}; background: transparent; border: 1px solid {borderColor};"
            titleColor = mutedColor
        self._numberLabel.setStyleSheet(
            f"QLabel#installerStepNumber {{ {numberStyle} border-radius: 14px; font-weight: 600; }}"
        )
        self._titleLabel.setStyleSheet(
            f"QLabel#installerStepTitle {{ color: {titleColor}; background: transparent; }}"
        )


class InstallerWindow(FramelessWindow):
    """Prismatica 单窗口安装流程。"""

    WELCOME_PAGE = 0
    LICENSE_PAGE = 1
    OPTIONS_PAGE = 2
    INSTALL_PAGE = 3
    FINISH_PAGE = 4

    def __init__(
        self,
        backendPath: Path,
        logoPath: Path,
        licensePath: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._backendPath = Path(backendPath)
        self._logoPath = Path(logoPath)
        self._licensePath = Path(licensePath)
        self._worker: InstallWorker | None = None
        self._isInstalling = False
        self._installFailed = False
        self._progressPath: Path | None = None
        self._logPath: Path | None = None
        self._lastProgressPercent = 0
        self._installedDir = defaultInstallDir()

        self.setWindowTitle(f"安装 {APP_NAME}")
        self.setWindowIcon(QIcon(str(self._logoPath)))
        installerFont = QFont("Microsoft YaHei UI", 9)
        installerFont.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"])
        self.setFont(installerFont)
        self.setFixedSize(940, 640)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._buildUi()
        self._connectSignals()
        self._applyTheme()
        self._showPage(self.WELCOME_PAGE)

    def _buildUi(self) -> None:
        titleBar = StandardTitleBar(self)
        titleBar.setFixedHeight(48)
        self.setTitleBar(titleBar)

        rootLayout = QVBoxLayout(self)
        rootLayout.setContentsMargins(0, 48, 0, 0)
        rootLayout.setSpacing(0)

        self._shell = QFrame()
        self._shell.setObjectName("installerShell")
        shellLayout = QHBoxLayout(self._shell)
        shellLayout.setContentsMargins(0, 0, 0, 0)
        shellLayout.setSpacing(0)
        rootLayout.addWidget(self._shell)

        self._buildRail(shellLayout)
        self._buildContent(shellLayout)

    def _buildRail(self, shellLayout: QHBoxLayout) -> None:
        self._rail = QFrame()
        self._rail.setObjectName("installerRail")
        self._rail.setFixedWidth(254)
        railLayout = QVBoxLayout(self._rail)
        railLayout.setContentsMargins(28, 30, 24, 26)
        railLayout.setSpacing(0)

        logoLabel = QLabel()
        logoLabel.setObjectName("installerLogo")
        logoLabel.setFixedSize(76, 76)
        logoLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logoPixmap = QPixmap(str(self._logoPath))
        logoLabel.setPixmap(
            logoPixmap.scaled(
                68,
                68,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        railLayout.addWidget(logoLabel)
        railLayout.addSpacing(16)

        productLabel = QLabel(APP_NAME)
        productLabel.setObjectName("installerProduct")
        productLabel.setFont(QFont("Segoe UI", 18, QFont.Weight.DemiBold))
        railLayout.addWidget(productLabel)
        railLayout.addSpacing(4)
        versionLabel = CaptionLabel(f"桌面端 · {APP_VERSION}")
        versionLabel.setObjectName("installerVersion")
        railLayout.addWidget(versionLabel)
        railLayout.addSpacing(38)

        stepTitles = ("欢迎", "许可协议", "安装设置", "正在安装", "完成")
        self._stepRows = [StepRow(index + 1, title) for index, title in enumerate(stepTitles)]
        for stepRow in self._stepRows:
            railLayout.addWidget(stepRow)
            railLayout.addSpacing(8)
        railLayout.addStretch(1)

        footerLabel = CaptionLabel("Hexalumina Studio")
        footerLabel.setObjectName("installerRailFooter")
        railLayout.addWidget(footerLabel)
        shellLayout.addWidget(self._rail)

    def _buildContent(self, shellLayout: QHBoxLayout) -> None:
        self._content = QFrame()
        self._content.setObjectName("installerContent")
        contentLayout = QVBoxLayout(self._content)
        contentLayout.setContentsMargins(48, 38, 42, 30)
        contentLayout.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._createWelcomePage())
        self._stack.addWidget(self._createLicensePage())
        self._stack.addWidget(self._createOptionsPage())
        self._stack.addWidget(self._createInstallPage())
        self._stack.addWidget(self._createFinishPage())
        contentLayout.addWidget(self._stack, 1)

        self._buttonBar = QFrame()
        self._buttonBar.setObjectName("installerButtonBar")
        buttonLayout = QHBoxLayout(self._buttonBar)
        buttonLayout.setContentsMargins(0, 22, 0, 0)
        buttonLayout.setSpacing(10)
        self._cancelButton = PushButton("取消")
        self._backButton = PushButton("上一步")
        self._nextButton = PrimaryPushButton("继续")
        for button in (self._cancelButton, self._backButton, self._nextButton):
            button.setFixedHeight(38)
            button.setMinimumWidth(96)
        buttonLayout.addWidget(self._cancelButton)
        buttonLayout.addStretch(1)
        buttonLayout.addWidget(self._backButton)
        buttonLayout.addWidget(self._nextButton)
        contentLayout.addWidget(self._buttonBar)
        shellLayout.addWidget(self._content, 1)

    def _createPage(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("installerPage")
        pageLayout = QVBoxLayout(page)
        pageLayout.setContentsMargins(0, 0, 0, 0)
        pageLayout.setSpacing(0)
        titleLabel = TitleLabel(title)
        titleLabel.setObjectName("installerPageTitle")
        subtitleLabel = BodyLabel(subtitle)
        subtitleLabel.setObjectName("installerPageSubtitle")
        subtitleLabel.setWordWrap(True)
        pageLayout.addWidget(titleLabel)
        pageLayout.addSpacing(8)
        pageLayout.addWidget(subtitleLabel)
        pageLayout.addSpacing(30)
        return page, pageLayout

    def _createWelcomePage(self) -> QWidget:
        page, pageLayout = self._createPage(
            "安装 Prismatica",
            "为中文语料研究准备一个安静、可靠的桌面工作空间。",
        )
        heroRow = QHBoxLayout()
        heroRow.setSpacing(30)
        heroTextLayout = QVBoxLayout()
        heroTextLayout.setSpacing(0)
        heroTitle = SubtitleLabel("准备开始")
        heroTitle.setObjectName("installerSectionTitle")
        heroDescription = BodyLabel(
            "安装向导会将 Prismatica 写入你选择的位置，并配置需要的快捷方式和项目文件关联。"
        )
        heroDescription.setWordWrap(True)
        heroDescription.setObjectName("installerBody")
        heroMeta = CaptionLabel("Windows 64 位 · 可升级覆盖 · 可完整卸载")
        heroMeta.setObjectName("installerMeta")
        heroTextLayout.addWidget(heroTitle)
        heroTextLayout.addSpacing(12)
        heroTextLayout.addWidget(heroDescription)
        heroTextLayout.addSpacing(18)
        heroTextLayout.addWidget(heroMeta)
        heroTextLayout.addStretch(1)

        heroLogo = QLabel()
        heroLogo.setFixedSize(190, 190)
        heroLogo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logoPixmap = QPixmap(str(self._logoPath))
        heroLogo.setPixmap(
            logoPixmap.scaled(
                178,
                178,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        heroRow.addLayout(heroTextLayout, 1)
        heroRow.addWidget(heroLogo)
        pageLayout.addLayout(heroRow, 1)
        return page

    def _createLicensePage(self) -> QWidget:
        page, pageLayout = self._createPage(
            "许可协议",
            "请在安装前阅读以下条款。只有确认同意后，安装才能继续。",
        )
        self._licenseEdit = QPlainTextEdit()
        self._licenseEdit.setObjectName("installerLicense")
        self._licenseEdit.setReadOnly(True)
        self._licenseEdit.setPlainText(self._readLicense())
        self._licenseEdit.setFont(QFont("Microsoft YaHei UI", 9))
        pageLayout.addWidget(self._licenseEdit, 1)
        pageLayout.addSpacing(16)
        self._licenseCheck = CheckBox("我已阅读并同意许可协议")
        self._licenseCheck.setObjectName("installerLicenseCheck")
        pageLayout.addWidget(self._licenseCheck)
        return page

    def _createOptionsPage(self) -> QWidget:
        page, pageLayout = self._createPage(
            "安装设置",
            "选择安装位置和系统集成选项。这些设置都可以在以后调整。",
        )
        pathLabel = SubtitleLabel("安装位置")
        pathLabel.setObjectName("installerSectionTitle")
        pageLayout.addWidget(pathLabel)
        pageLayout.addSpacing(10)
        pathRow = QHBoxLayout()
        pathRow.setSpacing(10)
        self._pathEdit = LineEdit()
        self._pathEdit.setText(str(defaultInstallDir()))
        self._pathEdit.setClearButtonEnabled(False)
        self._pathEdit.setMinimumHeight(38)
        self._browseButton = PushButton("浏览", self, FluentIcon.FOLDER)
        self._browseButton.setFixedHeight(38)
        self._browseButton.setMinimumWidth(94)
        pathRow.addWidget(self._pathEdit, 1)
        pathRow.addWidget(self._browseButton)
        pageLayout.addLayout(pathRow)
        pageLayout.addSpacing(8)
        self._diskSpaceLabel = CaptionLabel()
        self._diskSpaceLabel.setObjectName("installerMeta")
        pageLayout.addWidget(self._diskSpaceLabel)
        pageLayout.addSpacing(28)

        integrationLabel = SubtitleLabel("系统集成")
        integrationLabel.setObjectName("installerSectionTitle")
        pageLayout.addWidget(integrationLabel)
        pageLayout.addSpacing(14)
        self._desktopCheck = CheckBox("在桌面创建 Prismatica 快捷方式")
        self._fileAssocCheck = CheckBox("使用 Prismatica 打开 .prf 项目文件")
        self._fileAssocCheck.setChecked(True)
        pageLayout.addWidget(self._desktopCheck)
        pageLayout.addSpacing(12)
        pageLayout.addWidget(self._fileAssocCheck)
        pageLayout.addStretch(1)
        self._updateDiskSpace()
        return page

    def _createInstallPage(self) -> QWidget:
        page, pageLayout = self._createPage(
            "正在安装 Prismatica",
            "请保持此窗口开启。Windows 可能会请求管理员授权。",
        )
        pageLayout.addStretch(1)
        progressIcon = IconWidget(FluentIcon.DOWNLOAD)
        progressIcon.setFixedSize(54, 54)
        pageLayout.addWidget(progressIcon, 0, Qt.AlignmentFlag.AlignHCenter)
        pageLayout.addSpacing(22)
        self._progressStatusLabel = SubtitleLabel("正在等待开始")
        self._progressStatusLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pageLayout.addWidget(self._progressStatusLabel)
        pageLayout.addSpacing(18)
        self._progressBar = ProgressBar()
        self._progressBar.setRange(0, 100)
        self._progressBar.setValue(0)
        pageLayout.addWidget(self._progressBar)
        pageLayout.addSpacing(12)
        self._progressDetailLabel = CaptionLabel("准备安装文件")
        self._progressDetailLabel.setObjectName("installerMeta")
        self._progressDetailLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progressDetailLabel.setWordWrap(True)
        pageLayout.addWidget(self._progressDetailLabel)
        pageLayout.addStretch(2)
        return page

    def _createFinishPage(self) -> QWidget:
        page, pageLayout = self._createPage(
            "Prismatica 已准备就绪",
            "安装已成功完成。现在可以进入你的中文语料研究工作空间。",
        )
        pageLayout.addStretch(1)
        successIcon = IconWidget(FluentIcon.ACCEPT)
        successIcon.setFixedSize(64, 64)
        pageLayout.addWidget(successIcon, 0, Qt.AlignmentFlag.AlignHCenter)
        pageLayout.addSpacing(22)
        successLabel = SubtitleLabel("安装完成")
        successLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pageLayout.addWidget(successLabel)
        pageLayout.addSpacing(28)
        self._launchCheck = CheckBox("完成后启动 Prismatica")
        self._launchCheck.setChecked(True)
        pageLayout.addWidget(self._launchCheck, 0, Qt.AlignmentFlag.AlignHCenter)
        pageLayout.addStretch(2)
        return page

    def _connectSignals(self) -> None:
        self._cancelButton.clicked.connect(self.close)
        self._backButton.clicked.connect(self._goBack)
        self._nextButton.clicked.connect(self._goNext)
        self._licenseCheck.toggled.connect(self._updateNavigation)
        self._browseButton.clicked.connect(self._browseInstallDir)
        self._pathEdit.textChanged.connect(self._updateDiskSpace)
        qconfig.themeChangedFinished.connect(self._applyTheme)

        self._progressTimer = QTimer(self)
        self._progressTimer.setInterval(120)
        self._progressTimer.timeout.connect(self._pollProgress)

    def _readLicense(self) -> str:
        try:
            return self._licensePath.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return "许可协议暂时无法读取。请重新获取完整安装包。"

    def _showPage(self, pageIndex: int) -> None:
        self._stack.setCurrentIndex(pageIndex)
        isDark = isDarkTheme()
        for index, stepRow in enumerate(self._stepRows):
            state = "active" if index == pageIndex else "done" if index < pageIndex else "pending"
            stepRow.setState(state, isDark=isDark)
        self._updateNavigation()

    def _updateNavigation(self) -> None:
        pageIndex = self._stack.currentIndex()
        self._backButton.setVisible(pageIndex not in (self.WELCOME_PAGE, self.INSTALL_PAGE, self.FINISH_PAGE))
        self._cancelButton.setVisible(pageIndex != self.FINISH_PAGE)
        self._cancelButton.setEnabled(not self._isInstalling)
        self._nextButton.setVisible(pageIndex != self.INSTALL_PAGE or self._installFailed)
        self._nextButton.setEnabled(True)

        if pageIndex == self.WELCOME_PAGE:
            self._nextButton.setText("开始")
        elif pageIndex == self.LICENSE_PAGE:
            self._nextButton.setText("继续")
            self._nextButton.setEnabled(self._licenseCheck.isChecked())
        elif pageIndex == self.OPTIONS_PAGE:
            self._nextButton.setText("开始安装")
        elif pageIndex == self.INSTALL_PAGE:
            self._nextButton.setText("重试")
        else:
            self._nextButton.setText("完成")

    def _goBack(self) -> None:
        pageIndex = self._stack.currentIndex()
        if pageIndex in (self.LICENSE_PAGE, self.OPTIONS_PAGE):
            self._showPage(pageIndex - 1)

    def _goNext(self) -> None:
        pageIndex = self._stack.currentIndex()
        if pageIndex == self.WELCOME_PAGE:
            self._showPage(self.LICENSE_PAGE)
        elif pageIndex == self.LICENSE_PAGE and self._licenseCheck.isChecked():
            self._showPage(self.OPTIONS_PAGE)
        elif pageIndex == self.OPTIONS_PAGE:
            self._startInstall()
        elif pageIndex == self.INSTALL_PAGE and self._installFailed:
            self._startInstall()
        elif pageIndex == self.FINISH_PAGE:
            self._finish()

    def _browseInstallDir(self) -> None:
        selectedDir = QFileDialog.getExistingDirectory(
            self,
            "选择 Prismatica 安装位置",
            self._pathEdit.text().strip(),
        )
        if selectedDir:
            self._pathEdit.setText(str(Path(selectedDir) / APP_NAME))

    def _updateDiskSpace(self) -> None:
        installText = self._pathEdit.text().strip() if hasattr(self, "_pathEdit") else ""
        if not installText:
            self._diskSpaceLabel.setText("请输入安装位置")
            return
        installPath = Path(installText)
        probePath = installPath
        while not probePath.exists() and probePath.parent != probePath:
            probePath = probePath.parent
        try:
            freeBytes = shutil.disk_usage(probePath).free
            freeGiB = freeBytes / (1024**3)
            self._diskSpaceLabel.setText(f"目标磁盘可用空间约 {freeGiB:.1f} GB")
        except OSError:
            self._diskSpaceLabel.setText("无法读取目标磁盘空间")

    def _validateOptions(self) -> InstallOptions | None:
        installText = self._pathEdit.text().strip()
        if not installText:
            InfoBar.error(
                title="无法开始安装",
                content="请选择有效的安装位置。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3500,
            )
            return None
        installPath = Path(installText)
        if QFileInfo(str(installPath)).isRelative():
            InfoBar.error(
                title="安装位置无效",
                content="请使用完整的 Windows 路径。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3500,
            )
            return None
        return InstallOptions(
            installDir=installPath,
            createDesktopIcon=self._desktopCheck.isChecked(),
            associateProjectFiles=self._fileAssocCheck.isChecked(),
        )

    def _startInstall(self) -> None:
        options = self._validateOptions()
        if options is None:
            return
        if not self._backendPath.is_file():
            InfoBar.error(
                title="安装核心缺失",
                content="安装包不完整，请重新下载 Prismatica 安装程序。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=-1,
            )
            return

        sessionId = uuid.uuid4().hex
        tempDir = Path(tempfile.gettempdir()) / "PrismaticaInstaller"
        tempDir.mkdir(parents=True, exist_ok=True)
        self._progressPath = tempDir / f"{sessionId}.progress"
        self._logPath = tempDir / f"{sessionId}.log"
        self._installedDir = options.installDir
        arguments = buildInstallerArguments(options, self._progressPath, self._logPath)

        self._installFailed = False
        self._isInstalling = True
        self._lastProgressPercent = 1
        self._progressBar.setValue(1)
        self._progressStatusLabel.setText("等待管理员授权")
        self._progressDetailLabel.setText("请在 Windows 提示中选择“是”以继续")
        self._showPage(self.INSTALL_PAGE)

        self._worker = InstallWorker(self._backendPath, arguments, self)
        self._worker.processStarted.connect(self._onProcessStarted)
        self._worker.processFinished.connect(self._onProcessFinished)
        self._worker.processFailed.connect(self._onProcessFailed)
        self._worker.start()

    def _onProcessStarted(self) -> None:
        self._progressTimer.start()

    def _pollProgress(self) -> None:
        if self._progressPath is None or not self._progressPath.is_file():
            return
        progressState = readProgressState(self._progressPath)
        if progressState is None:
            return
        percent, statusText = progressState
        if percent < self._lastProgressPercent:
            return
        self._lastProgressPercent = percent
        self._progressBar.setValue(percent)
        self._progressStatusLabel.setText(statusText)
        self._progressDetailLabel.setText(f"安装进度 {percent}%")

    def _onProcessFinished(self, exitCode: int) -> None:
        self._progressTimer.stop()
        self._isInstalling = False
        if exitCode == 0:
            self._lastProgressPercent = 100
            self._progressBar.setValue(100)
            self._progressStatusLabel.setText("安装完成")
            self._progressDetailLabel.setText("Prismatica 已成功写入系统")
            QTimer.singleShot(350, lambda: self._showPage(self.FINISH_PAGE))
            return
        self._showInstallError(f"安装核心返回错误代码 {exitCode}")

    def _onProcessFailed(self, message: str) -> None:
        self._progressTimer.stop()
        self._isInstalling = False
        self._showInstallError(message)

    def _showInstallError(self, message: str) -> None:
        self._installFailed = True
        self._progressStatusLabel.setText("安装未完成")
        logHint = f"\n日志：{self._logPath}" if self._logPath else ""
        self._progressDetailLabel.setText(f"{message}{logHint}")
        self._progressBar.setValue(0)
        self._updateNavigation()
        InfoBar.error(
            title="安装失败",
            content="请检查提示后重试；已有程序数据不会被删除。",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=-1,
        )

    def _finish(self) -> None:
        if self._launchCheck.isChecked():
            executablePath = self._installedDir / APP_EXE_NAME
            if executablePath.is_file():
                QProcess.startDetached(str(executablePath), [], str(self._installedDir))
        self.close()

    def _applyTheme(self) -> None:
        isDark = isDarkTheme()
        windowColor = DARK_WINDOW if isDark else LIGHT_WINDOW
        contentColor = DARK_CONTENT if isDark else LIGHT_CONTENT
        surfaceColor = DARK_SURFACE if isDark else LIGHT_SURFACE
        borderColor = DARK_BORDER if isDark else LIGHT_BORDER
        textColor = DARK_TEXT if isDark else LIGHT_TEXT
        mutedColor = DARK_MUTED if isDark else LIGHT_MUTED
        accentColor = ACCENT_DARK if isDark else "#007C70"
        railColor = "#1B1E21" if isDark else "#F7F9FA"

        self.setStyleSheet(
            f"""
            InstallerWindow {{ background: {windowColor}; }}
            QFrame#installerShell {{ background: {contentColor}; border: none; }}
            QFrame#installerRail {{
                background: {railColor};
                border-right: 1px solid {borderColor};
            }}
            QFrame#installerContent {{ background: {contentColor}; border: none; }}
            QWidget#installerPage {{ background: transparent; border: none; }}
            QLabel {{ color: {textColor}; background: transparent; border: none; }}
            QLabel#installerProduct {{ color: {textColor}; }}
            QLabel#installerVersion, QLabel#installerRailFooter,
            QLabel#installerPageSubtitle, QLabel#installerMeta {{ color: {mutedColor}; }}
            QLabel#installerPageTitle {{ color: {textColor}; font-size: 27px; font-weight: 600; }}
            QLabel#installerSectionTitle {{ color: {textColor}; font-weight: 600; }}
            QLabel#installerBody {{ color: {textColor}; font-size: 14px; line-height: 1.5; }}
            QPlainTextEdit#installerLicense {{
                color: {textColor};
                background: {surfaceColor};
                border: 1px solid {borderColor};
                border-radius: 10px;
                padding: 12px;
                selection-background-color: {accentColor};
            }}
            QFrame#installerButtonBar {{
                background: transparent;
                border-top: 1px solid {borderColor};
            }}
            """
        )
        for index, stepRow in enumerate(self._stepRows):
            pageIndex = self._stack.currentIndex()
            state = "active" if index == pageIndex else "done" if index < pageIndex else "pending"
            stepRow.setState(state, isDark=isDark)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._isInstalling:
            event.ignore()
            InfoBar.warning(
                title="安装正在进行",
                content="为避免程序文件损坏，请等待安装完成后再关闭窗口。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3500,
            )
            return
        super().closeEvent(event)


__all__ = ["InstallerWindow", "StepRow"]
