# coding: utf-8
"""受保护 HSK 作文数据库资源清单客户端。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from .cloud_api import CloudApi, CloudApiError, getCloudApi

REQUIRED_RESOURCE_KEYS = {"hskCorpus", "hskLocalCorpus"}


@dataclass(frozen=True)
class CloudResourceManifest:
    """后端签发的单个短期资源下载清单。"""

    resourceKey: str
    downloadUrl: str
    sha256: str
    version: str


class CloudResource:
    """通过当前云端会话获取订阅授权后的短期下载地址。"""

    def __init__(self, api: Optional[CloudApi] = None) -> None:
        self._api = api or getCloudApi()

    def bootstrap(self) -> list[CloudResourceManifest]:
        """获取并严格校验后端资源清单。"""
        if not self._api.isLoggedIn():
            raise CloudApiError("UNAUTHORIZED", "请先登录 Prismatica 账号")
        payload = self._api.post(
            "/v1/resources/bootstrap",
            body={},
            timeout=15.0,
        )
        rawResources = payload.get("resources") if isinstance(payload, dict) else None
        if not isinstance(rawResources, list):
            raise CloudApiError("BAD_RESPONSE", "资源清单格式无效")

        manifests = []
        seenKeys = set()
        for item in rawResources:
            if not isinstance(item, dict):
                raise CloudApiError("BAD_RESPONSE", "资源清单包含无效条目")
            resourceKey = str(item.get("resourceKey", "")).strip()
            downloadUrl = str(item.get("downloadUrl", "")).strip()
            sha256 = str(item.get("sha256", "")).strip().lower()
            version = str(item.get("version", "")).strip()
            parsedUrl = urlparse(downloadUrl)
            if resourceKey not in REQUIRED_RESOURCE_KEYS or resourceKey in seenKeys:
                raise CloudApiError("BAD_RESPONSE", "资源清单标识无效")
            if parsedUrl.scheme not in {"http", "https"} or not parsedUrl.netloc:
                raise CloudApiError("BAD_RESPONSE", "资源下载地址无效")
            if not re.fullmatch(r"[a-f0-9]{64}", sha256):
                raise CloudApiError("BAD_RESPONSE", "资源完整性摘要无效")
            if not version:
                raise CloudApiError("BAD_RESPONSE", "资源版本无效")
            seenKeys.add(resourceKey)
            manifests.append(
                CloudResourceManifest(
                    resourceKey=resourceKey,
                    downloadUrl=downloadUrl,
                    sha256=sha256,
                    version=version,
                )
            )

        if seenKeys != REQUIRED_RESOURCE_KEYS:
            raise CloudApiError("BAD_RESPONSE", "资源清单不完整")
        return manifests


_singleton: CloudResource | None = None


def getCloudResource() -> CloudResource:
    global _singleton
    if _singleton is None:
        _singleton = CloudResource()
    return _singleton


__all__ = [
    "CloudResource",
    "CloudResourceManifest",
    "getCloudResource",
]
