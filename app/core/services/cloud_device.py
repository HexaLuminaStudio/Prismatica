# coding: utf-8
"""本机设备 ID 持久化(云端用户多设备登录追踪)

deviceId 是 UUID-like 字符串(32hex),持久化到 <CONFIG_FOLDER>/device.id,
云端用它做:
    - user_devices 表的主键
    - 多设备登录追踪
    - API 限速的 key
"""

from __future__ import annotations

import secrets
from pathlib import Path

from app.core.utils.setting import CONFIG_FOLDER


DEVICE_ID_FILE: Path = CONFIG_FOLDER / "device.id"


def getOrCreateDeviceId() -> str:
    """获取或生成本机 deviceId(32hex)。

    首次启动会写入 <CONFIG_FOLDER>/device.id,后续启动直接读。
    文件不存在/读失败 → 重新生成并覆盖。
    """
    try:
        if DEVICE_ID_FILE.exists():
            data = DEVICE_ID_FILE.read_text(encoding="utf-8").strip()
            if data and len(data) >= 16:
                return data
    except Exception:
        pass
    # 重新生成
    did = secrets.token_hex(16)
    try:
        DEVICE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_ID_FILE.write_text(did, encoding="utf-8")
    except Exception:
        # 即使写失败也返回内存 ID(避免阻塞业务)
        pass
    return did