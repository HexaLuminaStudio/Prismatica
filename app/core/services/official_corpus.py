"""Prismatica 云端官方语料账号 Token 客户端。"""

from __future__ import annotations

from typing import Literal

from .cloud_api import CloudApi, CloudApiError, getCloudApi

OfficialCorpusProvider = Literal["hsk", "global"]


def requestOfficialCorpusToken(
    provider: OfficialCorpusProvider,
    api: CloudApi | None = None,
) -> str:
    """请求云端代登录，只接收签发 Token，不接触官方账号密码。"""
    cloudApi = api or getCloudApi()
    payload = cloudApi.post(
        "/v1/resources/official-token",
        body={"provider": provider},
        withAuth=False,
        timeout=35.0,
    )
    if not isinstance(payload, dict):
        raise CloudApiError("BAD_RESPONSE", "官方账号服务响应格式无效")
    returnedProvider = str(payload.get("provider", "")).strip()
    token = str(payload.get("token", "")).strip()
    if returnedProvider != provider or not token:
        raise CloudApiError("BAD_RESPONSE", "官方账号服务未返回有效 Token")
    return token


__all__ = ["OfficialCorpusProvider", "requestOfficialCorpusToken"]
