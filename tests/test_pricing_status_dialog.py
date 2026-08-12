"""设置页当前定价状态弹窗。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from app.view.setting_interface import AgreementLabelWidget
from app.view.widgets.pricing_status_dialog import PricingStatusDialog


class _FakePricingCatalog(QObject):
    catalogChanged = Signal(object)
    refreshStarted = Signal()
    refreshFailed = Signal(str)

    def __init__(self, snapshot: dict) -> None:
        super().__init__()
        self._snapshot = dict(snapshot)
        self.isRefreshing = False
        self.lastSyncedAt = datetime.now().astimezone()
        self.refreshCalls = 0

    def snapshot(self) -> dict:
        return dict(self._snapshot)

    def refreshAsync(self) -> None:
        self.refreshCalls += 1


def _catalogSnapshot() -> dict:
    return {
        "version": "pricing-v3",
        "state": "active",
        "source": "published",
        "effectiveAt": "2026-08-12T09:30:00+08:00",
        "refreshAfterSeconds": 30,
        "rules": [
            {
                "featureCode": "analysis_export",
                "displayName": "语料分析导出",
                "billingMode": "fixed",
                "unitName": "次",
                "fixedCost": 5,
                "enabled": True,
            },
            {
                "featureCode": "hsk_download",
                "displayName": "HSK 语料下载",
                "billingMode": "metered",
                "unitName": "千条",
                "unitSize": 1000,
                "perUnitCost": 3,
                "enabled": True,
            },
            {
                "featureCode": "ai_chat",
                "displayName": "AI 聊天",
                "billingMode": "token",
                "unitName": "千 Token",
                "inputTokenCostPer1K": 1,
                "outputTokenCostPer1K": 2,
                "minCost": 1,
                "enabled": True,
            },
        ],
    }


def testSettingsFooterReplacesPricingAgreementWithCurrentPricing(qtbot) -> None:
    footer = AgreementLabelWidget()
    qtbot.addWidget(footer)

    assert footer.pricingStatusLabel.text() == "当前定价"
    assert footer.pricingStatusLabel.getUrl().isEmpty()
    assert footer.userAgreementLabel.text() == "用户协议"


def testPricingStatusDialogShowsCurrentVersionAndAllBillingModes(qtbot) -> None:
    catalog = _FakePricingCatalog(_catalogSnapshot())
    parent = QWidget()
    parent.resize(900, 700)
    qtbot.addWidget(parent)
    dialog = PricingStatusDialog(catalog=catalog, parent=parent)
    qtbot.addWidget(dialog)

    assert dialog.versionLabel.text() == "pricing-v3"
    assert dialog.sourceLabel.text() == "运营发布目录"
    assert dialog.statusBadge.text() == "当前生效"
    assert dialog.ruleCountLabel.text() == "3 项"
    assert [row.priceLabel.text() for row in dialog._ruleRows] == [
        "5 点 / 次",
        "3 点 / 档",
        "输入 1 · 输出 2 点",
    ]
    assert "每 1,000 条为一档" in dialog._ruleRows[1].detailLabel.text()
    assert "最低 1 点" in dialog._ruleRows[2].detailLabel.text()
    assert catalog.refreshCalls == 1


def testPricingStatusDialogKeepsCachedPricesWhenRefreshFails(qtbot) -> None:
    catalog = _FakePricingCatalog(_catalogSnapshot())
    parent = QWidget()
    parent.resize(900, 700)
    qtbot.addWidget(parent)
    dialog = PricingStatusDialog(catalog=catalog, parent=parent)
    qtbot.addWidget(dialog)

    catalog.refreshFailed.emit("模拟网络超时")

    assert dialog.statusBadge.text() == "同步失败"
    assert "上次同步价格" in dialog.statusDetailLabel.text()
    assert dialog.ruleCountLabel.text() == "3 项"
    assert dialog.yesButton.text() == "重新加载"


def testPricingStatusDialogAdaptsToNarrowParent(qtbot) -> None:
    catalog = _FakePricingCatalog(_catalogSnapshot())
    parent = QWidget()
    parent.resize(420, 700)
    qtbot.addWidget(parent)
    dialog = PricingStatusDialog(catalog=catalog, parent=parent)
    qtbot.addWidget(dialog)

    assert dialog.widget.width() == 372
