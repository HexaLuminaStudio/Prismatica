from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout
from qfluentwidgets import BodyLabel, CardWidget, ImageLabel
from qframelesswindow import TitleBar

from app.core.utils import cfg, qconfig, signalBus


class UserStatusWidget(CardWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.parent().height() - 10)
        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hBoxLayout.setContentsMargins(10, 2, 10, 2)

        # 未激活图标
        self.publicImageLabel = ImageLabel(self)
        self.publicImageLabel.setImage(QPixmap(":app/icons/Public.svg"))
        self.publicImageLabel.scaledToHeight(20)

        # 激活后图标
        self.advanceImageLabel = ImageLabel(self)
        self.advanceImageLabel.setImage(QPixmap(":app/icons/Advance.svg"))
        self.advanceImageLabel.scaledToHeight(20)
        self.advanceImageLabel.setVisible(False)

        self.tokenLabel = BodyLabel("公益版", self)

        self.hBoxLayout.addWidget(self.publicImageLabel)
        self.hBoxLayout.addWidget(self.advanceImageLabel)
        self.hBoxLayout.addWidget(self.tokenLabel)

        # 初始化时检查激活状态
        self._updateActivationStatus()

        # 连接激活状态变更信号
        signalBus.activationStatusChanged.connect(self._onActivationStatusChanged)

    def _updateActivationStatus(self):
        """更新激活状态显示"""
        from app.core.utils.license import getLicenseManager

        licenseManager = getLicenseManager()

        if licenseManager.isActivated():
            # 已激活
            userType = licenseManager.getUserType()
            self.tokenLabel.setText(userType)
            self.publicImageLabel.setVisible(False)
            self.advanceImageLabel.setVisible(True)
        else:
            # 未激活
            self.tokenLabel.setText("公益版")
            self.publicImageLabel.setVisible(True)
            self.advanceImageLabel.setVisible(False)

    def refreshStatus(self):
        """刷新激活状态（供外部调用）"""
        self._updateActivationStatus()

    def _onActivationStatusChanged(self, isActivated: bool):
        """处理激活状态变更信号"""
        self._updateActivationStatus()


class CustomTitleBar(TitleBar):
    """Title bar with icon and title"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.hBoxLayout.removeWidget(self.minBtn)
        self.hBoxLayout.removeWidget(self.maxBtn)
        self.hBoxLayout.removeWidget(self.closeBtn)

        # add window icon
        self.iconLabel = QLabel(self)
        self.iconLabel.setFixedSize(20, 20)
        self.hBoxLayout.insertSpacing(0, 15)
        self.hBoxLayout.insertWidget(
            1,
            self.iconLabel,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.window().windowIconChanged.connect(self.setIcon)

        # add title label
        self.titleLabel = QLabel(self)
        self.hBoxLayout.insertWidget(
            2,
            self.titleLabel,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.hBoxLayout.insertSpacing(2, 10)
        self.titleLabel.setObjectName("titleLabel")
        self.window().windowTitleChanged.connect(self.setTitle)

        self.userTokenCard = UserStatusWidget(self)
        self.hBoxLayout.addWidget(
            self.userTokenCard,
            1,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
        )
        self.hBoxLayout.addSpacing(50)

        self.vBoxLayout = QVBoxLayout()
        self.buttonLayout = QHBoxLayout()
        self.buttonLayout.setSpacing(0)
        self.buttonLayout.setContentsMargins(0, 0, 0, 0)
        self.buttonLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.buttonLayout.addWidget(self.minBtn)
        self.buttonLayout.addWidget(self.maxBtn)
        self.buttonLayout.addWidget(self.closeBtn)
        self.vBoxLayout.addLayout(self.buttonLayout)
        self.vBoxLayout.addStretch(1)
        self.hBoxLayout.addLayout(self.vBoxLayout, 0)

    def setTitle(self, title):
        self.titleLabel.setText(title)
        self.titleLabel.adjustSize()

    def setIcon(self, icon):
        self.iconLabel.setPixmap(QIcon(icon).pixmap(20, 20))
