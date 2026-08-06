# coding: utf-8
"""云端 API 配置(2026-08-05 T6 多环境)

通过 profile("prod"/"dev"/"staging")切换 base URL,优先级:
    1. 用户在「设置 → 云端」手动改的 cloudBaseUrl(qconfig)
    2. profile 默认值(由环境变量 PRISMATICA_CLOUD_PROFILE 选择)
    3. 内置默认 prod
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from app.core.utils import cfg
from app.core.utils.config import qconfig


# 各 profile 的默认 base URL(2026-08-05 后端实际部署)
PROD_BASE_URL = "https://api.prismatica.app"
DEV_BASE_URL = "http://103.236.55.211:8000"
STAGING_BASE_URL = "http://103.236.55.211:8000/staging"

# 当前激活 profile(运行时由环境变量 PRISMATICA_CLOUD_PROFILE 注入)
# 2026-08-06:本地联调阶段默认 dev,运营打包发布时再切回 prod。
DEFAULT_BASE_URL = DEV_BASE_URL


def getDefaultProfile() -> Literal["prod", "dev", "staging"]:
    """从环境变量解析 profile(未设置 → dev,2026-08-06 本地联调阶段)。"""
    raw = (os.environ.get("PRISMATICA_CLOUD_PROFILE") or "").strip().lower()
    if raw in ("dev", "staging", "prod"):
        return raw  # type: ignore[return-value]
    return "dev"


def getBaseUrlForProfile(profile: Literal["prod", "dev", "staging"]) -> str:
    """profile → 默认 base URL(不含用户运行时改动)。"""
    if profile == "dev":
        return DEV_BASE_URL
    if profile == "staging":
        return STAGING_BASE_URL
    return PROD_BASE_URL


@dataclass
class CloudConfig:
    """云端 API 配置。"""

    baseUrl: str = DEFAULT_BASE_URL
    timeoutSec: float = 10.0
    retryTimes: int = 1
    retryBackoffSec: float = 1.0


_cloudConfigInstance: CloudConfig | None = None


def getCloudConfig() -> CloudConfig:
    """获取云端配置(单例 + 实时从 qconfig 读 baseUrl)。"""
    global _cloudConfigInstance
    if _cloudConfigInstance is None:
        profile = getDefaultProfile()
        _cloudConfigInstance = CloudConfig(baseUrl=getBaseUrlForProfile(profile))
    # 每次都从 qconfig 读最新 baseUrl,允许运行时切换
    try:
        userOverride = qconfig.get(cfg.cloudBaseUrl)
        if userOverride:
            _cloudConfigInstance.baseUrl = str(userOverride).rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    return _cloudConfigInstance


def resetCloudConfigForTesting() -> None:
    """仅供测试:重置单例。"""
    global _cloudConfigInstance
    _cloudConfigInstance = None


__all__ = [
    "CloudConfig",
    "PROD_BASE_URL",
    "DEV_BASE_URL",
    "STAGING_BASE_URL",
    "getDefaultProfile",
    "getBaseUrlForProfile",
    "getCloudConfig",
    "resetCloudConfigForTesting",
]
