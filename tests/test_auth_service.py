# coding: utf-8
"""AuthService 密钥稳定性测试(2026-08-05 C1 修复点)。

覆盖:
    - 同一目录两次构造实例 → 派生密钥一致(身份不再因硬件特征/进程重启丢失)
    - 不同目录 → 密钥不同(隔离)
    - 密钥文件缺失时自动持久化生成
"""

from __future__ import annotations

from app.core.services.auth_service import AuthService


def test_derive_key_stable_across_instances(tmp_path):
    """同目录两次构造 → 返回同一密钥(修复「刷新后即消失」)。"""
    licFile = tmp_path / "license.enc"
    key1 = AuthService(licenseFile=licFile)._deriveKey()
    key2 = AuthService(licenseFile=licFile)._deriveKey()
    assert key1 == key2
    assert len(key1) == 32
    # 密钥已持久化到 .license-key
    assert (tmp_path / ".license-key").exists()


def test_derive_key_isolated_between_dirs(tmp_path):
    """不同配置目录 → 密钥不同(互不影响)。"""
    keyA = AuthService(licenseFile=tmp_path / "a" / "license.enc")._deriveKey()
    keyB = AuthService(licenseFile=tmp_path / "b" / "license.enc")._deriveKey()
    assert keyA != keyB
    assert keyA == AuthService(licenseFile=tmp_path / "a" / "license.enc")._deriveKey()
