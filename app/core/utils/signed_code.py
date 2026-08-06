# coding: utf-8
"""统一凭证签名/验签工具

与 license.py 复用同一根 HMAC-SHA256 密钥(LICENSE_SECRET),
保证邀请码 / 体验码 / 充值码的签发与验证逻辑一致、可共用运营工具。

凭证编码格式(与 license.py 完全一致):
    base64( JSON( payloadWithoutSig + {"signature": "<hmac-hex>"}) )

本工具刻意不依赖 license.py,作为独立工具模块存在,
便于在运营 CLI 与客户端之间共享,且不会破坏现有 LicenseManager 的契约。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Type, TypeVar

from app.core.utils import logger
from pydantic import BaseModel

from app.core.models.auth_models import InviteCode, RechargeCode, TrialCode
from app.core.utils.setting import LICENSE_SECRET


T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# 规范化签名(与 license.py 完全一致,确保跨模块签名可互认)
# ---------------------------------------------------------------------------


def _canonicalPayload(payload: dict) -> str:
    """构造签名前的规范化字符串。

    - sort_keys=True: 字段顺序不影响签名
    - ensure_ascii=False: 中文 / 特殊字符正确编码
    - separators=(",", ":"): 紧凑 JSON,无空白
    """
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def signPayload(payload: dict) -> str:
    """计算 HMAC-SHA256 签名(16 进制)。"""
    canonical = _canonicalPayload(payload)
    return hmac.new(
        LICENSE_SECRET.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verifyPayload(payload: dict, signature: str) -> bool:
    """验签(使用 hmac.compare_digest 防御时序攻击)。"""
    if not signature or not isinstance(signature, str):
        return False
    expected = signPayload(payload)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# 编 / 解码
# ---------------------------------------------------------------------------


def encodeSignedModel(model: BaseModel) -> str:
    """将 Pydantic 模型序列化为 base64(签名后)字符串。"""
    payload = model.model_dump(mode="json")
    payload["signature"] = signPayload(payload)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def decodeSignedCode(rawCode: str) -> dict:
    """从 base64 字符串解码为 payload dict(未验签)。

    Raises:
        ValueError: 解码失败或格式非法
    """
    try:
        decoded = base64.b64decode(rawCode).decode("utf-8")
        data = json.loads(decoded)
    except Exception as e:
        raise ValueError(f"凭证格式错误: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("凭证结构非法")
    return data


def parseSignedModel(
    rawCode: str,
    modelCls: Type[T],
) -> tuple[T, str]:
    """解析 + 验签 + 反序列化为 Pydantic 模型。

    Returns:
        (model, signature) 元组

    Raises:
        ValueError: 解码 / 验签 / 反序列化失败
    """
    data = decodeSignedCode(rawCode)
    signature = data.get("signature")
    if not signature:
        raise ValueError("凭证缺少 signature 字段")
    payloadWithoutSig = {k: v for k, v in data.items() if k != "signature"}
    if not verifyPayload(payloadWithoutSig, signature):
        raise ValueError("凭证签名校验失败")
    model = modelCls.model_validate(payloadWithoutSig)
    return model, signature


# ---------------------------------------------------------------------------
# 便捷生成函数(供运营 CLI 与首次启动内嵌使用)
# ---------------------------------------------------------------------------


def makeInviteCode(
    maxUses: int = 1,
    grantedBalance: int = 100,
    grantedDays: int = 30,
    tier: str = "beta",
    expireAt: datetime | None = None,
) -> str:
    """生成一条邀请码字符串。"""
    from app.core.models.auth_models import UserTier

    expire = expireAt or (datetime.utcnow() + __import__("datetime").timedelta(days=14))
    code = _generateCodeBody("INV")
    model = InviteCode(
        code=code,
        maxUses=maxUses,
        grantedBalance=grantedBalance,
        grantedDays=grantedDays,
        tier=UserTier(tier),
        expireAt=expire,
    )
    return encodeSignedModel(model)


def makeTrialCode(
    grantedBalance: int = 20,
    grantedDays: int = 7,
    expireAt: datetime | None = None,
) -> str:
    """生成一条体验码字符串。"""
    from app.core.models.auth_models import UserTier
    from datetime import timedelta

    expire = expireAt or (datetime.utcnow() + timedelta(days=30))
    code = _generateCodeBody("TRY")
    model = TrialCode(
        code=code,
        grantedBalance=grantedBalance,
        grantedDays=grantedDays,
        tier=UserTier.TRIAL,
        expireAt=expire,
    )
    return encodeSignedModel(model)


def makeRechargeCode(
    amount: int,
    expireAt: datetime | None = None,
    note: str = "",
) -> str:
    """生成一条充值码字符串。"""
    from datetime import timedelta

    if amount <= 0:
        raise ValueError("充值面额必须大于 0")
    expire = expireAt or (datetime.utcnow() + timedelta(days=365))
    code = _generateCodeBody("RCH")
    model = RechargeCode(
        code=code,
        amount=amount,
        expireAt=expire,
        note=note,
    )
    return encodeSignedModel(model)


# ---------------------------------------------------------------------------
# 内部:码体生成
# ---------------------------------------------------------------------------


def _generateCodeBody(prefix: str) -> str:
    """生成形如 INV-AB12-CD34-EF56 的码体(排除混淆字符 0/O, 1/I/L)。"""
    import secrets
    import string

    alphabet = "".join(c for c in (string.ascii_uppercase + string.digits) if c not in "0O1IL")
    body1 = "".join(secrets.choice(alphabet) for _ in range(4))
    body2 = "".join(secrets.choice(alphabet) for _ in range(4))
    body3 = "".join(secrets.choice(alphabet) for _ in range(4))
    body4 = "".join(secrets.choice(alphabet) for _ in range(4))
    return f"{prefix}-{body1}-{body2}-{body3}-{body4}"


# ---------------------------------------------------------------------------
# 工具函数:解析用户输入的码为模型
# ---------------------------------------------------------------------------


def tryParseAnyCode(rawCode: str) -> tuple[str, BaseModel]:
    """根据码前缀自动识别类型并解析。

    Returns:
        (类型前缀, Pydantic 模型实例)

    Raises:
        ValueError: 无法识别 / 验签失败
    """
    rawCode = (rawCode or "").strip()
    # 先解码取前缀(因为 base64 字符串可能以 "eyJ" 开头,不是真正的码前缀)
    try:
        data = decodeSignedCode(rawCode)
    except Exception as e:
        raise ValueError(f"凭证格式错误: {e}")
    codeField = data.get("code", "")
    if not codeField:
        raise ValueError("凭证缺少 code 字段")
    prefix = codeField.split("-", 1)[0]

    if prefix == "INV":
        model, _ = parseSignedModel(rawCode, InviteCode)
        return "invite", model
    if prefix == "TRY":
        model, _ = parseSignedModel(rawCode, TrialCode)
        return "trial", model
    if prefix == "RCH":
        model, _ = parseSignedModel(rawCode, RechargeCode)
        return "recharge", model
    raise ValueError(f"未知凭证类型: {prefix}")


__all__ = [
    "encodeSignedModel",
    "decodeSignedCode",
    "parseSignedModel",
    "signPayload",
    "verifyPayload",
    "makeInviteCode",
    "makeTrialCode",
    "makeRechargeCode",
    "tryParseAnyCode",
]