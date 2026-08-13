"""启动入口不得检查、鉴权或下载数据库资源。"""

from __future__ import annotations

from pathlib import Path


def testMainStartsLoaderWithoutResourcePreparation() -> None:
    mainSource = (Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8"
    )

    assert "StartupDatabaseService" not in mainSource
    assert "StartupResourcePreparationThread" not in mainSource
    assert "_startResourcePreparation" not in mainSource
    assert "_splashLoader.start()" in mainSource
    assert "启动页不检查、不鉴权也不下载数据库资源" in mainSource
