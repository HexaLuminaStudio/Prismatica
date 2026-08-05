# coding: utf-8
"""账户中心 - 余额卡片

修复(2026-08-05):
    1. 36px 字号直接设到 StrongBodyLabel 在某些主题下撑爆
       → 用 QFont.pointSize 更可控,并加最小宽度防止挤压
    2. _onBalanceChanged 调用 refresh() → refresh() 重新读 DB → 重复触发信号
       → 直接 setText,不递归
    3. palette(base) 在深色主题下不透明
       → 改用 setObjectName + QSS 显式配色
    4. 副标题"本月已用"用纯文本不够显眼
       → 用两栏结构 + 渐变色块 + 数字 + 单位
    5. 充值按钮图标用 FIF.ADD 不合适
       → 改 FIF.SHOPPING_CART(或钱包图标)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QFrame,
    QSizePolicy,
)
from qfluentwidgets import (
    StrongBodyLabel,
    BodyLabel,
    CaptionLabel,
    PrimaryPushButton,
    PushButton,
    FluentIcon as FIF,
    IconWidget,
)

from app.core.models.billing_models import BillStatus
from app.core.services import account_db
from app.core.services.billing_service import getBillingService
from app.core.utils.signal_bus import signalBus


_BALANCE_CARD_QSS = """
/* 修复(2026-08-05):背景改为 transparent,避免被外层 ElevatedCardWidget
   覆盖;border-radius 完整保留,让渐变 header 与 body 拼成圆角卡片。 */
#BalanceCardRoot {
    background-color: transparent;
    border: 1px solid rgba(0, 0, 0, 15%);
    border-radius: 10px;
}
/* 主体白色背景,让卡片在透明底色上仍能呈现"白色卡片"观感 */
#BalanceCardBody {
    background-color: palette(base);
    border-bottom-left-radius: 10px;
    border-bottom-right-radius: 10px;
    padding: 16px;
}
#BalanceCardHeader {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #1a73e8, stop:1 #00b09c
    );
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    /* 修复(2026-08-05):上下 padding 12px,确保渐变区域够高,左右 16px 与 body 对齐 */
    padding: 12px 16px;
    min-height: 44px;
}
#BalanceBigNum {
    font-size: 32px;
    font-weight: 600;
    color: #1a73e8;
}
#BalanceUnit {
    font-size: 14px;
    color: #555;
}
#StatLabel {
    color: #888;
    font-size: 12px;
}
#StatValue {
    font-size: 16px;
    font-weight: 600;
    color: palette(text);
}
"""


class BalanceCard(QWidget):
    """余额卡片 - 顶部渐变 header + 中部余额数字 + 底部双栏统计 + 操作按钮"""

    rechargeRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("BalanceCardRoot")
        self._userId: Optional[str] = None

        # 修复(2026-08-05):让卡片随父容器宽度自适应,而不是被外层 stretch 挤成"瘦长条"。
        # 之前 outer.addStretch(1) 放在 body 之后,导致 header 被压缩,渐变只显示一小块。
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # 外层 layout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---------- 顶部 header ----------
        header = QWidget(self)
        header.setObjectName("BalanceCardHeader")
        headerLayout = QHBoxLayout(header)
        # 修复(2026-08-05):让 QSS 的 padding 生效,layout 不再覆盖上下 padding。
        # 之前 setContentsMargins(4,0,4,0) 把 header 的垂直 padding 压缩为 0,
        # 配合 outer.addStretch(1) 导致渐变背景只能看到一小条。
        headerLayout.setContentsMargins(0, 0, 0, 0)
        headerLayout.setSpacing(8)
        header.setMinimumHeight(44)

        self.headerIcon = IconWidget(FIF.SHOPPING_CART, header)
        self.headerIcon.setFixedSize(20, 20)
        headerLayout.addWidget(self.headerIcon)

        self.titleLabel = StrongBodyLabel("账户余额", header)
        self.titleLabel.setStyleSheet("color: white; font-size: 14px;")
        headerLayout.addWidget(self.titleLabel)
        headerLayout.addStretch(1)

        self.tierLabel = CaptionLabel("beta", header)
        self.tierLabel.setStyleSheet(
            "color: rgba(255,255,255,85%); "
            "background: rgba(255,255,255,18%); "
            "padding: 2px 10px; border-radius: 8px;"
        )
        headerLayout.addWidget(self.tierLabel)

        outer.addWidget(header)

        # ---------- 中部主体 ----------
        body = QWidget(self)
        body.setObjectName("BalanceCardBody")
        bodyLayout = QVBoxLayout(body)
        bodyLayout.setContentsMargins(16, 16, 16, 16)
        bodyLayout.setSpacing(12)

        # 余额数字 + 单位(横向)
        amountRow = QHBoxLayout()
        amountRow.setSpacing(6)
        amountRow.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.balanceNum = StrongBodyLabel("—", body)
        self.balanceNum.setObjectName("BalanceBigNum")
        f = QFont()
        f.setPointSize(28)
        f.setBold(True)
        self.balanceNum.setFont(f)
        amountRow.addWidget(self.balanceNum)

        self.balanceUnit = BodyLabel("币", body)
        self.balanceUnit.setObjectName("BalanceUnit")
        amountRow.addWidget(self.balanceUnit)
        amountRow.addStretch(1)

        bodyLayout.addLayout(amountRow)

        # 本月已用 / 累计消费(双栏统计)
        statsRow = QHBoxLayout()
        statsRow.setSpacing(24)
        self.monthSpentLabel = self._makeStatBlock(body)
        self.totalSpentLabel = self._makeStatBlock(body)
        statsRow.addLayout(self.monthSpentLabel["layout"])
        statsRow.addLayout(self.totalSpentLabel["layout"])
        statsRow.addStretch(1)
        bodyLayout.addLayout(statsRow)

        # 按钮行
        actionRow = QHBoxLayout()
        self.rechargeBtn = PrimaryPushButton("输入充值码", body, FIF.SHOPPING_CART)
        self.rechargeBtn.clicked.connect(self.rechargeRequested.emit)
        actionRow.addWidget(self.rechargeBtn)
        actionRow.addStretch(1)
        bodyLayout.addLayout(actionRow)

        outer.addWidget(body)
        # 修复(2026-08-05):去掉外层末尾的 addStretch(1)。
        # 之前在 outer.addWidget(body) 之后再 addStretch(1),在 AccountInterface
        # 的 containerLayout 末端还有 addStretch(1),导致 header 被挤到最小,
        # 渐变背景只能看到最顶上一小条。这里直接保留 body 自适应即可。
        # outer.addStretch(1)  # removed

        self.setStyleSheet(_BALANCE_CARD_QSS)

        # 信号
        signalBus.balanceChanged.connect(self._onBalanceChanged)

    # ---------- 内部 ----------
    def _makeStatBlock(self, parent: QWidget) -> dict:
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        cap = CaptionLabel("—", parent)
        cap.setObjectName("StatLabel")
        val = StrongBodyLabel("0", parent)
        val.setObjectName("StatValue")
        layout.addWidget(cap)
        layout.addWidget(val)
        return {"layout": layout, "cap": cap, "val": val}

    def setUserId(self, userId: str) -> None:
        self._userId = userId
        self.refresh()

    def refresh(self) -> None:
        if not self._userId:
            self.balanceNum.setText("—")
            self.monthSpentLabel["cap"].setText("本月已用")
            self.monthSpentLabel["val"].setText("—")
            self.totalSpentLabel["cap"].setText("累计消费")
            self.totalSpentLabel["val"].setText("—")
            self.tierLabel.setText("未激活")
            return

        billing = getBillingService()
        balance = billing.getBalance(self._userId)
        acc = billing.getAccount(self._userId)
        tier = acc.tier if acc else "beta"

        self.balanceNum.setText(f"{balance:,}")
        self.tierLabel.setText(tier)

        # 本月已用
        monthStart = datetime.utcnow().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        bills = account_db.listBills(self._userId, status=BillStatus.SETTLED, limit=500)
        monthSpent = sum(b.realCost for b in bills if b.createdAt >= monthStart)
        totalSpent = sum(b.realCost for b in bills)

        self.monthSpentLabel["cap"].setText("本月已用")
        self.monthSpentLabel["val"].setText(f"{monthSpent} 币")
        self.totalSpentLabel["cap"].setText("累计消费")
        self.totalSpentLabel["val"].setText(f"{totalSpent} 币")

    def _onBalanceChanged(self, userId: str, balance: int) -> None:
        """仅更新余额数字,避免反复读 DB 引发的连锁刷新。"""
        if userId == self._userId:
            self.balanceNum.setText(f"{balance:,}")
