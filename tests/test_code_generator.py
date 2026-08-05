# coding: utf-8
"""凭证生成器 UI 测试

覆盖内容:
    - signed_code 三种码生成函数能跑通
    - 充值码 amount<=0 抛 ValueError
    - 生成 -> tryParseAnyCode 类型一致
    - CodeGeneratorWindow 可直接构造(qapp 存在时)
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6", reason="UI 测试需要 PySide6")
pytest.importorskip("qfluentwidgets", reason="UI 测试需要 qfluentwidgets")


# ---------------------------------------------------------------------------
# signed_code 接口冒烟(不需要 UI)
# ---------------------------------------------------------------------------


def test_make_invite_code_via_signed_code():
    from app.core.utils.signed_code import makeInviteCode, tryParseAnyCode

    raw = makeInviteCode(
        grantedBalance=123, grantedDays=7, tier="beta"
    )
    assert isinstance(raw, str)
    assert len(raw) > 50  # base64 编码后长度
    kind, model = tryParseAnyCode(raw)
    assert kind == "invite"
    assert model.grantedBalance == 123
    assert model.grantedDays == 7
    assert model.tier.value == "beta"


def test_make_trial_code_via_signed_code():
    from app.core.utils.signed_code import makeTrialCode, tryParseAnyCode

    raw = makeTrialCode(grantedBalance=20, grantedDays=7)
    kind, model = tryParseAnyCode(raw)
    assert kind == "trial"
    assert model.grantedBalance == 20
    assert model.grantedDays == 7
    assert model.tier.value == "trial"


def test_make_recharge_code_via_signed_code():
    from app.core.utils.signed_code import makeRechargeCode, tryParseAnyCode

    raw = makeRechargeCode(amount=50, note="测试")
    kind, model = tryParseAnyCode(raw)
    assert kind == "recharge"
    assert model.amount == 50
    assert model.note == "测试"


def test_make_recharge_code_rejects_zero_amount():
    from app.core.utils.signed_code import makeRechargeCode

    with pytest.raises(ValueError):
        makeRechargeCode(amount=0)
    with pytest.raises(ValueError):
        makeRechargeCode(amount=-1)


def test_tryParseAnyCode_round_trip():
    """生成 -> 解析 -> 字段完全一致。"""
    from app.core.utils.signed_code import (
        makeInviteCode,
        makeRechargeCode,
        makeTrialCode,
        tryParseAnyCode,
    )

    for raw, expected in [
        (makeInviteCode(grantedBalance=50, grantedDays=14), "invite"),
        (makeTrialCode(grantedBalance=20, grantedDays=7), "trial"),
        (makeRechargeCode(amount=100), "recharge"),
    ]:
        kind, _ = tryParseAnyCode(raw)
        assert kind == expected


def test_tryParseAnyCode_invalid_input():
    from app.core.utils.signed_code import tryParseAnyCode

    with pytest.raises(ValueError):
        tryParseAnyCode("not-a-code")
    with pytest.raises(ValueError):
        tryParseAnyCode("")


def test_decode_signed_code_returns_payload():
    from app.core.utils.signed_code import decodeSignedCode, makeInviteCode

    raw = makeInviteCode(grantedBalance=88, grantedDays=15)
    data = decodeSignedCode(raw)
    assert isinstance(data, dict)
    assert "signature" in data
    assert data["grantedBalance"] == 88
    assert data["grantedDays"] == 15


# ---------------------------------------------------------------------------
# UI 构造测试
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_window_can_be_constructed(qapp):
    from tools.code_generator_window import CodeGeneratorWindow

    w = CodeGeneratorWindow()
    try:
        assert w.windowTitle() == "Prismatica 凭证生成器"
        # 必须有所有核心控件
        assert hasattr(w, "kindPivot")
        assert hasattr(w, "countSpin")
        assert hasattr(w, "balanceSpin")
        assert hasattr(w, "daysSpin")
        assert hasattr(w, "noteEdit")
        assert hasattr(w, "tierCombo")
        assert hasattr(w, "generateBtn")
        assert hasattr(w, "verifyInput")
        assert hasattr(w, "verifyBtn")
        assert hasattr(w, "resultTable")
        assert hasattr(w, "copyAllBtn")
        assert hasattr(w, "exportBtn")
        assert hasattr(w, "clearBtn")
        assert hasattr(w, "verifyOutput")
    finally:
        w.deleteLater()


def test_window_default_kind_is_invite(qapp):
    from tools.code_generator_window import CodeGeneratorWindow

    w = CodeGeneratorWindow()
    try:
        assert w._currentKind == CodeGeneratorWindow.KIND_INVITE
        assert w.kindPivot.currentRouteKey() == "invite"
    finally:
        w.deleteLater()


def test_window_pivot_switch_changes_current_kind(qapp):
    from tools.code_generator_window import CodeGeneratorWindow

    w = CodeGeneratorWindow()
    try:
        w.kindPivot.setCurrentItem("trial")
        # signal 是 currentItemChanged,需要在事件循环里触发;
        # 这里直接调 _onKindChanged 模拟
        w._onKindChanged()
        assert w._currentKind == CodeGeneratorWindow.KIND_TRIAL

        w.kindPivot.setCurrentItem("recharge")
        w._onKindChanged()
        assert w._currentKind == CodeGeneratorWindow.KIND_RECHARGE
        # RCH 模式下 noteEdit 应可用
        assert w.noteEdit.isEnabled() is True
        # days 应被禁用
        assert w.daysSpin.isEnabled() is False
    finally:
        w.deleteLater()


def test_window_generate_adds_rows(qapp):
    from tools.code_generator_window import CodeGeneratorWindow

    w = CodeGeneratorWindow()
    try:
        w.countSpin.setValue(3)
        w._onGenerate()
        assert w.resultTable.rowCount() == 3
        assert len(w._generatedRows) == 3
        # 每行都有码
        for r in w._generatedRows:
            assert r["kind"] == "invite"
            assert r["code"].startswith("eyJ")  # base64 of JSON starts with eyJ
    finally:
        w.deleteLater()


def test_window_clear_empties_table(qapp):
    from tools.code_generator_window import CodeGeneratorWindow

    w = CodeGeneratorWindow()
    try:
        w._onGenerate()
        assert w.resultTable.rowCount() > 0
        w._onClear()
        assert w.resultTable.rowCount() == 0
        assert len(w._generatedRows) == 0
    finally:
        w.deleteLater()


def test_window_verify_decodes_payload(qapp):
    from app.core.utils.signed_code import makeInviteCode
    from tools.code_generator_window import CodeGeneratorWindow

    raw = makeInviteCode(grantedBalance=77, grantedDays=11, tier="beta")
    w = CodeGeneratorWindow()
    try:
        w.verifyInput.setPlainText(raw)
        w._onVerify()
        output = w.verifyOutput.toPlainText()
        # 应包含 payload 与 "grantedBalance": 77
        assert "invite" in output
        assert "grantedBalance" in output
        assert "77" in output
    finally:
        w.deleteLater()


def test_window_verify_invalid_shows_error(qapp):
    from tools.code_generator_window import CodeGeneratorWindow

    w = CodeGeneratorWindow()
    try:
        w.verifyInput.setPlainText("not-a-real-code")
        w._onVerify()
        output = w.verifyOutput.toPlainText()
        assert "❌" in output
        assert "错误" in output or "凭证" in output
    finally:
        w.deleteLater()


def test_window_export_writes_file(qapp, tmp_path):
    from tools.code_generator_window import CodeGeneratorWindow
    from PySide6.QtWidgets import QFileDialog

    w = CodeGeneratorWindow()
    try:
        w.countSpin.setValue(2)
        w._onGenerate()
        out_path = tmp_path / "exported.txt"

        # monkey-patch QFileDialog.getSaveFileName
        import tools.code_generator_window as mod

        original = mod.QFileDialog.getSaveFileName

        def fake_save(*args, **kwargs):
            return str(out_path), "Text Files (*.txt)"

        mod.QFileDialog.getSaveFileName = staticmethod(fake_save)
        try:
            w._onExport()
        finally:
            mod.QFileDialog.getSaveFileName = original

        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        # 包含 INV header 与生成的码
        assert "INV" in content
        assert "# Prismatica 凭证导出" in content
        # 至少 2 条码
        assert content.count("eyJ") >= 2
    finally:
        w.deleteLater()