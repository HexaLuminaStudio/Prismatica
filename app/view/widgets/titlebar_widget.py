from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout
from qfluentwidgets import BodyLabel, CardWidget, ImageLabel
from qframelesswindow import TitleBar

from app.core.models.auth_models import UserTier
from app.core.utils import cfg, qconfig, signalBus
from app.view.widgets.project_switcher_widget import ProjectSwitcher


# 档位 → 标题栏显示文案
_TIER_LABELS = {
    UserTier.GUEST: "公益版",
    UserTier.TRIAL: "体验版",
    UserTier.BETA: "公益版",
    UserTier.BETA_PRO: "内测专业版",
    UserTier.PAID: "正式用户",
}


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
        """更新激活状态显示(2026-08-05 改读 AuthService 云端凭证)"""
        from app.core.services.auth_service import getAuthService

        auth = getAuthService()

        if auth.isAuthenticated():
            # 已激活
            tier = auth.currentTier()
            self.tokenLabel.setText(_TIER_LABELS.get(tier, tier.value))
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

        # PRD-002:项目切换器(插入到 userTokenCard 左侧)
        # 复用 TitleBar 现有 hBoxLayout,在 userTokenCard 之前插入,
        # 借助 addStretch(0) 把项目切换器推到 userTokenCard 左边。
        # 注:由于 userTokenCard 已经设置了 stretch=1 把卡片推到最右,
        # 这里用 insertWidget 在 titleLabel 之后插一个 stretch 槽 + 切换器。
        self.projectSwitcher = ProjectSwitcher(self)
        # 先加一个 stretch 让切换器推到 userTokenCard 之前
        self.hBoxLayout.insertStretch(
            self.hBoxLayout.indexOf(self.userTokenCard),
            0,
        )
        self.hBoxLayout.insertWidget(
            self.hBoxLayout.indexOf(self.userTokenCard),
            self.projectSwitcher,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )

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
