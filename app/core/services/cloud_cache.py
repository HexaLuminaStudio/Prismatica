# coding: utf-8
"""云端响应本地缓存(只读)

本期用户决策:不允许离线降级,业务强云端。
本模块仅在 5xx/网络异常时给 UI 一个历史快照兜底,
扣费/充值等写操作仍要求云端可用。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from app.core.utils import logger

from app.core.utils.setting import CONFIG_FOLDER


CACHE_DIR: Path = CONFIG_FOLDER / "cache"
USER_CACHE: Path = CACHE_DIR / "user.json"
BILLS_CACHE: Path = CACHE_DIR / "bills.json"


def _jsonDefault(obj: Any) -> Any:
    """JSON 默认编码器:支持 datetime / date / 带 isoformat 的对象"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _ensure() -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"[CloudCache] 创建缓存目录失败: {e}")


def writeUser(user: dict) -> None:
    _ensure()
    try:
        USER_CACHE.write_text(
            json.dumps(user, ensure_ascii=False, indent=2, default=_jsonDefault),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"[CloudCache] 写 user.json 失败: {e}")


def readUser() -> Optional[dict]:
    if not USER_CACHE.exists():
        return None
    try:
        return json.loads(USER_CACHE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[CloudCache] 读 user.json 失败: {e}")
        return None


def writeBills(bills: list[dict]) -> None:
    _ensure()
    try:
        BILLS_CACHE.write_text(
            json.dumps(bills, ensure_ascii=False, indent=2, default=_jsonDefault),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"[CloudCache] 写 bills.json 失败: {e}")


def readBills() -> list[dict]:
    if not BILLS_CACHE.exists():
        return []
    try:
        return json.loads(BILLS_CACHE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[CloudCache] 读 bills.json 失败: {e}")
        return []


def clear() -> None:
    for p in (USER_CACHE, BILLS_CACHE):
        try:
            if p.exists():
                p.unlink()
        except Exception as e:
            logger.warning(f"[CloudCache] 删除 {p.name} 失败: {e}")