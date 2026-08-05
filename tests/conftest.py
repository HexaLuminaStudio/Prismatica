# coding: utf-8
"""pytest 全局 fixture

所有测试使用临时 DATA_DIR,避免污染用户数据。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch, tmp_path: Path):
    """将 DATA_DIR / CONFIG_FOLDER 重定向到临时目录。

    自动应用于所有测试,确保不读写真实 <INSTALL_DIR>/datas。
    """
    testRoot = tmp_path / "prismatica_test"
    dataDir = testRoot / "datas"
    configDir = testRoot / "config"
    logDir = testRoot / "logs"
    for d in (dataDir, configDir, logDir):
        d.mkdir(parents=True, exist_ok=True)

    # 先重置所有单例,再 monkeypatch 路径,再重置单例(确保新路径生效)
    import app.core.services.auth_service as auth_mod
    import app.core.services.billing_service as billing_mod
    import app.core.services.pricing_service as pricing_mod

    auth_mod._authServiceInstance = None
    billing_mod._billingInstance = None
    pricing_mod.PricingService._instance = None

    # Monkey patch 路径(必须在单例构造前)
    import app.core.services.account_db as account_db_mod
    import app.core.services.auth_service as auth_service_mod
    monkeypatch.setattr("app.core.utils.setting.DATA_FOLDER", dataDir)
    monkeypatch.setattr("app.core.utils.setting.CONFIG_FOLDER", configDir)
    monkeypatch.setattr("app.core.utils.setting.LOG_FOLDER", logDir)
    monkeypatch.setattr(account_db_mod, "ACCOUNT_DB", dataDir / "account.db")
    monkeypatch.setattr(
        "app.core.services.pricing_service.PRICING_FILE", dataDir / "pricing.json"
    )
    monkeypatch.setattr(auth_service_mod, "LICENSE_FILE", configDir / "license.enc")

    # 强制重新初始化 schema(到 monkeypatched 路径)
    from app.core.services.account_db import initSchema
    initSchema()

    yield testRoot
    # 清理(autouse 结束后 tmp_path 自动删除,这里不必手动)