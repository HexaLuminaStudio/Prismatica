# coding: utf-8
"""费用预估弹窗

每次扣费动作执行前弹出,展示:
    - 动作名称 / 资源量 / 预估费用
    - 当前余额 / 扣后余额
    - 阶梯明细(可展开)
    - [取消] [确认扣费]

设计要点(2026-08-05 修复):
    1. MessageBoxBase 的 self.view 已在 __init__ 中创建,不能重新赋值为新的 QWidget
       否则 buttonGroup/view 失去联系,导致按钮不可见或位置错误
    2. 直接对 self.view 的 layout() 添加控件,或把现有 layout 重新组织
    3. 阶梯明细改用 ExpandGroup 折叠,避免一行长串
    4. 余额不足时 yesButton 禁用并给红色提示
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget, QSizePolicy
from qfluentwidgets import (
    MessageBoxBase,
    SubtitleLabel,
    BodyLabel,
    CaptionLabel,
    StrongBodyLabel,
    PushButton,
    PrimaryPushButton,
    FluentIcon as FIF,
    ExpandGroupSettingCard,
    SettingCardGroup,
)

from app.core.models.billing_models import CostPreview


def _resolveBodyWidget(dlg: MessageBoxBase) -> QWidget:
    """MessageBoxBase 在新版有 self.widget + viewLayout,旧版有 self.view。

    这里统一返回用于 add 控件的容器。
    """
    return getattr(dlg, "widget", None) or getattr(dlg, "view", None)


class CostPreviewDialog(MessageBoxBase):
    """费用预估确认弹窗"""

    def __init__(self, preview: CostPreview, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)
        self._preview = preview
        self._confirmed = False

        # 标题
        self.titleLabel = SubtitleLabel(
            f"{preview.displayName} — 费用预估", self
        )

        # 主信息:左右对照(资源量 / 单价)
        infoRow = QHBoxLayout()
        infoRow.setSpacing(24)
        infoRow.addWidget(self._makeInfoBlock("资源量", f"{preview.resourceUsed:,} {preview.unitName}"))
        infoRow.addWidget(self._makeInfoBlock("预估费用", f"{preview.estimatedCost} 币", accent=True))
        infoRow.addStretch(1)

        # 余额对照(当前 → 扣后)
        balanceText = (
            f"当前余额: {preview.currentBalance} 币    →    "
            f"扣后余额: {preview.balanceAfter} 币"
        )
        self.balanceLabel = BodyLabel(balanceText, self)

        # 警告(余额不足)
        self.warnLabel = StrongBodyLabel("", self)
        if not preview.affordable:
            gap = preview.estimatedCost - preview.currentBalance
            self.warnLabel.setText(f"余额不足,还差 {gap} 币。请先充值。")
            self.warnLabel.setStyleSheet("color: #d83b3b; font-size: 14px;")

        # 阶梯明细(可展开)
        self.tierCard = self._makeTierCard(preview)

        # 装配 view(直接使用父类提供的 view,而不是新建)
        # 优先用 viewLayout(新版本),没有则用 body.layout()(旧版本)
        body = _resolveBodyWidget(self)
        viewLayout = getattr(self, "viewLayout", None)
        if viewLayout is None:
            viewLayout = body.layout()
        viewLayout.setContentsMargins(24, 24, 24, 24)
        viewLayout.setSpacing(10)

        viewLayout.addWidget(self.titleLabel)
        viewLayout.addSpacing(4)
        viewLayout.addLayout(infoRow)
        viewLayout.addWidget(self.balanceLabel)
        if not preview.affordable:
            viewLayout.addWidget(self.warnLabel)
        viewLayout.addWidget(self.tierCard)
        viewLayout.addStretch(1)

        # 按钮文案与禁用
        self.yesButton.setText("确认扣费")
        self.cancelButton.setText("取消")
        if not preview.affordable:
            self.yesButton.setEnabled(False)
        self.buttonGroup.setMinimumWidth(280)

        # 调整 view 尺寸(高度自适应阶梯)
        body.setMinimumWidth(440)
        body.setMinimumHeight(280)

    # ---------- 内部构造 ----------
    def _makeInfoBlock(self, title: str, value: str, accent: bool = False) -> QWidget:
        """构造一对 (标题, 值) 的纵向 block。"""
        w = QWidget(self)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        cap = CaptionLabel(title, w)
        strong = StrongBodyLabel(value, w)
        if accent:
            strong.setStyleSheet("font-size: 22px; color: #1a73e8;")
        else:
            strong.setStyleSheet("font-size: 18px;")
        v.addWidget(cap)
        v.addWidget(strong)
        return w

    def _makeTierCard(self, preview: CostPreview) -> QWidget:
        """阶梯明细卡片(可折叠)。"""
        group = ExpandGroupSettingCard(
            FIF.INFO, "阶梯明细", "当前动作的智慧计价规则", self
        )
        if preview.tierBreakdown:
            for tier in preview.tierBreakdown:
                line = BodyLabel(
                    f"资源量 ≤ {tier['upTo']:,} 时,单价倍率 {tier['rate']}x",
                    group,
                )
                line.setStyleSheet("padding: 2px 0;")
                group.addWidget(line)
        else:
            group.addWidget(BodyLabel("(固定单价,无阶梯)", group))
        group.expand()  # 默认展开,便于用户查看
        return group

    # ---------- 状态 ----------
    def accept(self) -> None:  # type: ignore[override]
        self._confirmed = True
        super().accept()

    def isConfirmed(self) -> bool:
        return self._confirmed


def confirmCost(preview: CostPreview, parent: Optional[QWidget] = None) -> bool:
    """便捷函数:展示弹窗并返回是否确认。"""
    dialog = CostPreviewDialog(preview, parent=parent)
    dialog.exec()
    return dialog.isConfirmed()