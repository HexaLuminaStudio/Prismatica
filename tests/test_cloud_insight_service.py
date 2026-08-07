"""P0-A CloudInsightService 烟雾测试(不真发 HTTP,只测 gate 拒绝路径)。"""
from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)


def test_cloud_insight_service_refuses_when_not_logged_in(monkeypatch) -> None:
    """未登录时,runWithBilling 应该 emit failed 并弹 gate。"""
    from app.core.services import (
        CloudInsightService,
        getCloudAuth,
    )
    from app.core.services.feature_gate import GateResult

    # 清空本地 session
    auth = getCloudAuth()
    auth._api.clearSession()
    monkeypatch.setattr(
        "app.core.services.feature_gate.getCloudApi",
        lambda: auth._api,
    )

    svc = CloudInsightService()
    failedMessages = []
    svc.failed.connect(lambda m: failedMessages.append(m))

    # gate 拒绝(未登录)
    result = svc.runWithBilling("freq", {"rows": "x" * 100}, corpusMeta=None)
    assert result is False
    assert any("登录" in m or "未登录" in m.lower() or "无法发起" in m for m in failedMessages)


def test_estimate_insight_resource_basic() -> None:
    from app.core.services.cloud_insight_service import _estimateInsightResource

    # 0 字符 → 至少 1
    assert _estimateInsightResource("freq", {}) == 1
    # 100 字符 → 1
    assert _estimateInsightResource("freq", {"x": "a" * 100}) == 1
    # 1500 字符 → 2 (向上取整)
    assert _estimateInsightResource("freq", {"x": "a" * 1500}) == 2
    # 嵌套
    assert _estimateInsightResource("freq", {"a": {"b": "c" * 3000}}) == 3
