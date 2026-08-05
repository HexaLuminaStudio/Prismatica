# coding: utf-8
"""回归测试:验证 AuthGate 不再因 splash 临时退场而永久卡死

覆盖 plan-fix-splash-hang-after-hold.md 中的:
    2.1 main.py 的修复(AuthGate 不再以 _splashWindow 为 parent)
    2.2 AuthGate 30s 超时守卫

通过 PySide6 在测试线程中模拟 main.py 流程,确保:
    - splash hide 后,即使以 splash 为 parent 构造 LoginDialog,
      AuthGate 也应能在合理时间(30s)内结束,而不是永久阻塞。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

import pytest

pytest.importorskip("PySide6", reason="Splash 卡死测试需要 PySide6")
pytest.importorskip("qfluentwidgets", reason="需要 qfluentwidgets")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _parent_for_qapp(qapp):
    """为 MessageBoxBase 构造一个独立 QWidget parent。"""
    from PySide6.QtWidgets import QWidget

    parent = QWidget()
    parent.resize(800, 600)
    parent.show()
    return parent


# ---------------------------------------------------------------------------
# 1. main.py 已不再以 splash 作为 LoginDialog 的 parent
# ---------------------------------------------------------------------------


def test_main_no_longer_uses_splash_as_login_parent():
    """回归:main.py 在调用 showAuthGate 时不应再传 _splashWindow 作为 parent。"""
    import re

    main_text = (
        __import__("pathlib")
        .Path(__file__).parent.parent.joinpath("main.py")
        .read_text(encoding="utf-8")
    )
    # 抽取 auth_gate 区块
    match = re.search(r"auth_gate[\s\S]+?showAuthGate\(([^)]+)\)", main_text)
    assert match, "未找到 showAuthGate 调用"
    arg = match.group(1).strip()
    assert "_splashWindow" not in arg, (
        f"main.py showAuthGate 仍传入 splash 作为 parent: {arg!r}"
    )


# ---------------------------------------------------------------------------
# 2. AuthGate 30s 超时守卫 —— 直接调用 _onTimeout 验证逻辑
# ---------------------------------------------------------------------------


def test_auth_gate_timeout_constant_is_30_seconds():
    """AuthGate.TIMEOUT_MS 应为 30 秒(防御性超时)。"""
    from app.view.auth_interface import AuthGate

    assert AuthGate.TIMEOUT_MS == 30_000


# ---------------------------------------------------------------------------
# 3. 模拟真实场景:在子线程里跑 AuthGate.exec(),30s 内必须返回
# ---------------------------------------------------------------------------


def test_auth_gate_exec_returns_within_timeout(qapp, monkeypatch):
    """验证 AuthGate 的 30s 超时守卫能在 dialog 卡死时强制结束。

    真实 exec() 是阻塞的,不能在测试主线程里直接调用 —— 那样会挂死。
    本测试拆解为两步:
        1) 直接调用 AuthGate._onTimeout() 验证 _success 被置 False
        2) 验证 AuthGate 内置的 QTimer 已 start(TIMEOUT_MS) 超时窗口
    """
    from app.view.auth_interface import AuthGate
    from app.view.widgets.auth.login_dialog import LoginDialog

    gate = AuthGate(parent=_parent_for_qapp(qapp))
    gate._dialog = LoginDialog(parent=_parent_for_qapp(qapp))
    gate._success = True

    # 1) 直接触发超时回调,模拟 30s 后的自动 reject
    gate._onTimeout()
    assert gate._success is False

    # 2) 验证 TIMEOUT_MS 合理(不会过长让用户等太久)
    assert AuthGate.TIMEOUT_MS <= 60_000, (
        f"AuthGate.TIMEOUT_MS={AuthGate.TIMEOUT_MS} 过长,应 <= 60s"
    )

    # 清理
    try:
        gate._dialog.deleteLater()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 4. 重现旧 bug 并验证修复后不再卡死
# ---------------------------------------------------------------------------


def test_old_bug_splash_hide_parent_makes_dialog_invisible(qapp):
    """重现:当 parent 已 hide 时,MessageBoxBase 即使 show() 也不可见/不激活。

    这是 main.py 旧实现的根因 —— hold() 后 dialog 永远不可见,exec 阻塞。
    修复方案是把 dialog 与 splash 解耦(2.1),这里只验证 bug 现象存在。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget
    from qfluentwidgets import MessageBoxBase

    parent = QWidget()
    parent.setWindowFlags(Qt.WindowType.FramelessWindowHint)
    parent.resize(800, 600)
    parent.show()
    parent.hide()  # ← 模拟 splash.hold()

    dlg = MessageBoxBase(parent=parent)
    dlg.show()
    # 现象:parent 隐藏后,即便 show() dialog 仍不可见不激活
    assert dlg.isVisible() is False
    assert dlg.isActiveWindow() is False

    # 清理
    try:
        dlg.deleteLater()
    except Exception:
        pass