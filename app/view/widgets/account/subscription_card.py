# coding: utf-8
"""P0-A 桌面端 订阅卡片(单条)。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, StrongBodyLabel, SubtitleLabel

from app.core.utils import logger


class SubscriptionCard(QFrame):
    """显示单条订阅:planCode / status / 周期 / 进度条。"""

    def __init__(self, sub: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._sub = sub
        self.setStyleSheet(
            "QFrame { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 10px; }"
        )
        self.setMinimumHeight(96)
        self._buildUi()

    def _buildUi(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        # 第一行:planCode + status 徽标
        row1 = QHBoxLayout()
        plan = str(self._sub.get("planCode", "—"))
        status = str(self._sub.get("status", "active"))
        row1.addWidget(SubtitleLabel(plan))
        row1.addStretch(1)
        statusLabel = CaptionLabel(f"  {status.upper()}  ")
        colorMap = {
            "active": "#10b981",
            "expired": "#9ca3af",
            "canceled": "#f59e0b",
            "past_due": "#ef4444",
        }
        statusLabel.setStyleSheet(
            f"color: white; background: {colorMap.get(status, '#6b7280')}; "
            f"padding: 2px 8px; border-radius: 8px;"
        )
        row1.addWidget(statusLabel)
        layout.addLayout(row1)

        # 第二行:周期
        start = self._sub.get("currentPeriodStart", "")
        end = self._sub.get("currentPeriodEnd", "")
        expiresAt = self._sub.get("expiresAt", "")
        row2 = CaptionLabel(
            f"周期:{start[:10] if isinstance(start, str) else start} → {end[:10] if isinstance(end, str) else end}  ·  到期:{expiresAt[:10] if isinstance(expiresAt, str) else expiresAt}"
        )
        layout.addWidget(row2)

        # 第三行:quota / 自动续费
        quota = int(self._sub.get("monthlyQuota", 0) or 0)
        auto = bool(self._sub.get("autoRenew", False))
        row3 = CaptionLabel(f"周期额度:{quota} 积分  ·  自动续费:{'是' if auto else '否'}")
        layout.addWidget(row3)


__all__ = ["SubscriptionCard"]
