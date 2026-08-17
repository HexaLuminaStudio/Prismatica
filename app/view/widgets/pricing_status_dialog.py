"""展示当前云端生效价格目录的 Fluent 弹窗。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    MessageBoxBase,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    qconfig,
)

from app.core.services.pricing_catalog import PricingCatalog, getPricingCatalog
from app.view.widgets.prismatica_theme import shellPalette


def _formatEffectiveAt(value: Any) -> str:
    if not value:
        return "随服务启动生效"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except (TypeError, ValueError):
        return str(value)


def _formatSyncedAt(value: datetime | None) -> str:
    if value is None:
        return "本次打开前已缓存"
    return value.astimezone().strftime("%H:%M:%S")


def _countNoun(rule: dict[str, Any]) -> str:
    unitName = str(rule.get("unitName", ""))
    return "篇" if unitName.endswith("篇") else "条"


def _priceText(rule: dict[str, Any]) -> str:
    mode = str(rule.get("billingMode", ""))
    if mode == "fixed":
        unitName = str(rule.get("unitName") or "次")
        return f"{int(rule.get('fixedCost', 0) or 0):,} 点 / {unitName}"
    if mode == "token":
        isNewPricing = int(rule.get("tokenPricingVersion", 1) or 1) >= 2
        inputKey = "inputTokenCostPerUnit" if isNewPricing else "inputTokenCostPer1K"
        outputKey = "outputTokenCostPerUnit" if isNewPricing else "outputTokenCostPer1K"
        inputCost = int(rule.get(inputKey, 0) or 0)
        outputCost = int(rule.get(outputKey, 0) or 0)
        return f"输入 {inputCost:,} · 输出 {outputCost:,} 点"
    if mode == "metered":
        unitCost = int(rule.get("perUnitCost", 0) or 0)
        return f"{unitCost:,} 点 / 档"
    return "计价规则不可用"


def _ruleDetail(rule: dict[str, Any]) -> str:
    mode = str(rule.get("billingMode", ""))
    if mode == "fixed":
        return "每次成功完成后按固定价格结算"
    if mode == "token":
        minimum = int(rule.get("minCost", 0) or 0)
        minimumText = f"，最低 {minimum:,} 点" if minimum > 0 else ""
        if int(rule.get("tokenPricingVersion", 1) or 1) >= 2:
            unitSize = max(1, int(rule.get("unitSize", 1_000_000) or 1_000_000))
            return f"每 {unitSize:,} Token 计价，输入、输出加权合计后向上取整{minimumText}"
        return f"输入、输出分别按每千 Token 向上取整{minimumText}"
    if mode == "metered":
        unitSize = max(1, int(rule.get("unitSize", 1) or 1))
        baseCost = int(rule.get("baseCost", 0) or 0)
        baseText = f"，另含基础费用 {baseCost:,} 点" if baseCost > 0 else ""
        return f"每 {unitSize:,} {_countNoun(rule)}为一档，不足一档按一档计{baseText}"
    return "请刷新后重试"


class _PricingRuleRow(QFrame):
    """单项价格规则，文本与颜色共同表达计费模式。"""

    def __init__(self, rule: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.rule = dict(rule)
        self.setObjectName("pricingRuleRow")
        self.setMinimumHeight(68)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(5)

        headingLayout = QHBoxLayout()
        headingLayout.setSpacing(12)
        self.nameLabel = StrongBodyLabel(str(rule.get("displayName") or "未命名功能"), self)
        self.nameLabel.setWordWrap(True)
        self.nameLabel.setMinimumWidth(0)
        self.priceLabel = StrongBodyLabel(_priceText(rule), self)
        priceFont = self.priceLabel.font()
        priceFont.setWeight(QFont.Weight.DemiBold)
        self.priceLabel.setFont(priceFont)
        self.priceLabel.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        headingLayout.addWidget(self.nameLabel, 1)
        headingLayout.addWidget(self.priceLabel, 0)
        layout.addLayout(headingLayout)

        self.detailLabel = CaptionLabel(_ruleDetail(rule), self)
        self.detailLabel.setWordWrap(True)
        self.detailLabel.setAccessibleName(f"{self.nameLabel.text()}计价说明")
        layout.addWidget(self.detailLabel)

        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)

    def _applyTheme(self, *_args) -> None:
        palette = shellPalette()
        self.setStyleSheet(
            f"QFrame#pricingRuleRow {{ background: {palette.surfaceAlt.name()}; "
            f"border: 1px solid {palette.border.name()}; border-radius: 10px; }}"
        )
        self.nameLabel.setStyleSheet(f"color: {palette.text.name()}; border: none;")
        self.priceLabel.setStyleSheet(f"color: {palette.accentText.name()}; border: none;")
        self.detailLabel.setStyleSheet(f"color: {palette.mutedText.name()}; border: none;")


class PricingStatusDialog(MessageBoxBase):
    """实时展示当前生效版本、同步状态与全部收费规则。"""

    def __init__(
        self,
        catalog: PricingCatalog | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._catalog = catalog or getPricingCatalog()
        self._ruleRows: list[_PricingRuleRow] = []
        self._snapshot: dict[str, Any] = {}

        availableWidth = parent.width() - 48 if parent is not None and parent.width() > 0 else 640
        self.widget.setFixedWidth(max(360, min(660, availableWidth)))
        self.yesButton.setText("刷新价格")
        self.cancelButton.setText("关闭")
        self._buildUi()
        self._connectSignals()

        cached = self._catalog.snapshot()
        if cached:
            self._renderCatalog(cached)
        else:
            self._setStatus("loading", "正在连接", "正在获取当前生效价格…")
        self._requestRefresh()

    def _buildUi(self) -> None:
        self.viewLayout.setContentsMargins(24, 18, 24, 8)
        self.viewLayout.setSpacing(12)

        titleLabel = SubtitleLabel("当前定价", self)
        self.viewLayout.addWidget(titleLabel)
        descriptionLabel = BodyLabel(
            "这里展示当前服务端实际生效的价格。价格调整后会自动同步，进行中的任务仍保留原价格快照。",
            self,
        )
        descriptionLabel.setWordWrap(True)
        self.viewLayout.addWidget(descriptionLabel)

        self.statusFrame = QFrame(self)
        self.statusFrame.setObjectName("pricingStatusFrame")
        statusLayout = QHBoxLayout(self.statusFrame)
        statusLayout.setContentsMargins(14, 10, 14, 10)
        statusLayout.setSpacing(10)
        self.statusBadge = QLabel("正在连接", self.statusFrame)
        self.statusBadge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.statusBadge.setMinimumWidth(78)
        self.statusBadge.setFixedHeight(26)
        self.statusDetailLabel = CaptionLabel("正在获取当前生效价格…", self.statusFrame)
        self.statusDetailLabel.setWordWrap(True)
        statusLayout.addWidget(self.statusBadge, 0)
        statusLayout.addWidget(self.statusDetailLabel, 1)
        self.viewLayout.addWidget(self.statusFrame)

        self.metaFrame = QFrame(self)
        self.metaFrame.setObjectName("pricingMetaFrame")
        metaLayout = QGridLayout(self.metaFrame)
        metaLayout.setContentsMargins(14, 10, 14, 10)
        metaLayout.setHorizontalSpacing(18)
        metaLayout.setVerticalSpacing(7)
        self.versionLabel = CaptionLabel("—", self.metaFrame)
        self.sourceLabel = CaptionLabel("—", self.metaFrame)
        self.effectiveAtLabel = CaptionLabel("—", self.metaFrame)
        self.syncedAtLabel = CaptionLabel("—", self.metaFrame)
        metaItems = (
            ("生效版本", self.versionLabel),
            ("价格来源", self.sourceLabel),
            ("生效时间", self.effectiveAtLabel),
            ("最近同步", self.syncedAtLabel),
        )
        self.metaKeyLabels = []
        for index, (key, valueLabel) in enumerate(metaItems):
            keyLabel = CaptionLabel(key, self.metaFrame)
            keyLabel.setAccessibleName(f"{key}标签")
            valueLabel.setWordWrap(True)
            valueLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.metaKeyLabels.append(keyLabel)
            metaLayout.addWidget(keyLabel, index, 0)
            metaLayout.addWidget(valueLabel, index, 1)
        self.viewLayout.addWidget(self.metaFrame)

        rulesHeadingLayout = QHBoxLayout()
        rulesHeadingLayout.setSpacing(8)
        rulesHeading = StrongBodyLabel("当前收费项目", self)
        self.ruleCountLabel = CaptionLabel("0 项", self)
        rulesHeadingLayout.addWidget(rulesHeading)
        rulesHeadingLayout.addStretch(1)
        rulesHeadingLayout.addWidget(self.ruleCountLabel)
        self.viewLayout.addLayout(rulesHeadingLayout)

        self.rulesScrollArea = ScrollArea(self)
        self.rulesScrollArea.setWidgetResizable(True)
        self.rulesScrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rulesScrollArea.setFixedHeight(330)
        self.rulesWidget = QWidget(self.rulesScrollArea)
        self.rulesLayout = QVBoxLayout(self.rulesWidget)
        self.rulesLayout.setContentsMargins(0, 0, 4, 0)
        self.rulesLayout.setSpacing(8)
        self.rulesLayout.addStretch(1)
        self.rulesScrollArea.setWidget(self.rulesWidget)
        self.viewLayout.addWidget(self.rulesScrollArea)

        self.noticeLabel = CaptionLabel(
            "实际收费以前置报价与预授权时锁定的价格快照为准；失败或取消会按流程释放预授权。",
            self,
        )
        self.noticeLabel.setWordWrap(True)
        self.viewLayout.addWidget(self.noticeLabel)
        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)

    def _connectSignals(self) -> None:
        self._catalog.refreshStarted.connect(self._onRefreshStarted)
        self._catalog.catalogChanged.connect(self._renderCatalog)
        self._catalog.refreshFailed.connect(self._onRefreshFailed)

    def _requestRefresh(self) -> None:
        if self._catalog.isRefreshing:
            self._onRefreshStarted()
            return
        self._catalog.refreshAsync()

    def _onRefreshStarted(self) -> None:
        if self._snapshot:
            self._setStatus("loading", "正在核对", "继续显示上次同步价格，正在核对云端版本…")
        else:
            self._setStatus("loading", "正在连接", "正在获取当前生效价格…")
        self.yesButton.setEnabled(False)
        self.yesButton.setText("刷新中…")

    def _renderCatalog(self, catalog: dict[str, Any]) -> None:
        self._snapshot = dict(catalog)
        rules = list(catalog.get("rules") or [])
        self.versionLabel.setText(str(catalog.get("version") or "—"))
        self.sourceLabel.setText("运营发布目录" if catalog.get("source") == "published" else "服务端基准目录")
        self.effectiveAtLabel.setText(_formatEffectiveAt(catalog.get("effectiveAt")))
        self.syncedAtLabel.setText(_formatSyncedAt(self._catalog.lastSyncedAt))
        self.ruleCountLabel.setText(f"{len(rules)} 项")
        self._replaceRuleRows(rules)
        self._setStatus("active", "当前生效", "已与定价服务同步")
        self.yesButton.setEnabled(True)
        self.yesButton.setText("刷新价格")

    def _replaceRuleRows(self, rules: list[dict[str, Any]]) -> None:
        for row in self._ruleRows:
            self.rulesLayout.removeWidget(row)
            row.deleteLater()
        self._ruleRows.clear()
        for rule in rules:
            row = _PricingRuleRow(rule, self.rulesWidget)
            self._ruleRows.append(row)
            self.rulesLayout.insertWidget(self.rulesLayout.count() - 1, row)

    def _onRefreshFailed(self, _message: str) -> None:
        if self._snapshot:
            self._setStatus("stale", "同步失败", "当前显示上次同步价格，软件会自动重试")
        else:
            self._setStatus("error", "暂不可用", "无法连接定价服务，请检查网络后重试")
        self.yesButton.setEnabled(True)
        self.yesButton.setText("重新加载")

    def _setStatus(self, state: str, badgeText: str, detailText: str) -> None:
        self._statusState = state
        self.statusBadge.setText(badgeText)
        self.statusDetailLabel.setText(detailText)
        self._applyStatusTheme()

    def _applyTheme(self, *_args) -> None:
        palette = shellPalette()
        self.statusFrame.setStyleSheet(
            f"QFrame#pricingStatusFrame {{ background: {palette.surface.name()}; "
            f"border: 1px solid {palette.border.name()}; border-radius: 10px; }}"
        )
        self.metaFrame.setStyleSheet(
            f"QFrame#pricingMetaFrame {{ background: {palette.surfaceAlt.name()}; border: none; border-radius: 10px; }}"
        )
        for label in self.metaKeyLabels:
            label.setStyleSheet(f"color: {palette.mutedText.name()}; border: none;")
        for label in (
            self.versionLabel,
            self.sourceLabel,
            self.effectiveAtLabel,
            self.syncedAtLabel,
        ):
            label.setStyleSheet(f"color: {palette.text.name()}; border: none;")
        self.noticeLabel.setStyleSheet(f"color: {palette.mutedText.name()};")
        self.rulesScrollArea.setStyleSheet(
            "QScrollArea { background: transparent; border: none; } "
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self.rulesScrollArea.viewport().setStyleSheet("background: transparent;")
        self.rulesWidget.setStyleSheet("background: transparent;")
        self._applyStatusTheme()

    def _applyStatusTheme(self) -> None:
        if not hasattr(self, "statusBadge"):
            return
        palette = shellPalette()
        colors = {
            "loading": (palette.accentText, palette.accentSurface),
            "active": (palette.successText, palette.successSurface),
            "stale": (palette.warningText, palette.warningSurface),
            "error": (palette.dangerText, palette.dangerSurface),
        }
        foreground, background = colors.get(getattr(self, "_statusState", "loading"), colors["loading"])
        self.statusBadge.setStyleSheet(
            f"color: {foreground.name()}; background: {background.name()}; "
            f"border: 1px solid {foreground.name()}; border-radius: 13px; padding: 0 9px;"
        )
        self.statusDetailLabel.setStyleSheet(f"color: {palette.text.name()}; border: none;")

    def validate(self) -> bool:
        """主按钮仅刷新价格，不关闭状态弹窗。"""
        self._requestRefresh()
        return False


__all__ = ["PricingStatusDialog"]
