# coding: utf-8
"""鉴权相关数据模型

与现有 license.py 的 HMAC 激活码体系**共存**,本模块定义的内测期新增凭证:
    - 邀请码(InviteCode)         :一次性,大规模开放内测
    - 体验码(TrialCode)           :邀请码子集,短时体验
    - 充值码(RechargeCode)        :一次性,余额充值
    - License(本地凭证)           :激活成功后的本地凭据

凭证格式约定(全部采用 base64url(JSON(payload) + "." + HMAC-SHA256(payload)))
    与 license.py 完全一致,密钥亦复用 LICENSE_SECRET,
    保证运营 CLI 与客户端共用同一根密钥。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AuthMode(str, Enum):
    """鉴权模式"""

    BETA_TIMELOCK = "beta_timelock"            # 现有内测时间锁
    ACTIVATION_CODE = "activation_code"         # 现有激活码(license.py)
    INVITE_CODE = "invite_code"                 # 新增邀请码
    TRIAL_CODE = "trial_code"                   # 新增体验码
    ONLINE_ACCOUNT = "online_account"           # 预留 RC+ 在线账户


class UserTier(str, Enum):
    """用户档位(决定默认余额、有效期、权限)"""

    GUEST = "guest"
    TRIAL = "trial"
    BETA = "beta"
    BETA_PRO = "beta_pro"
    PAID = "paid"


class InviteCode(BaseModel):
    """邀请码内容(运营签发)"""

    code: str = Field(..., description="邀请码明文,INV-XXXX-XXXX-XXXX")
    maxUses: int = Field(default=1, description="最大使用次数,内测期固定 1")
    grantedBalance: int = Field(default=100, description="成功激活后赠送的余额(币)")
    grantedDays: int = Field(default=30, description="激活后账户有效期(天)")
    tier: UserTier = Field(default=UserTier.BETA)
    expireAt: datetime = Field(..., description="邀请码过期时间")
    issuedAt: datetime = Field(default_factory=datetime.utcnow)
    version: int = 1


class TrialCode(BaseModel):
    """体验码(邀请码的子集,权限更少、时长更短)"""

    code: str = Field(..., description="体验码明文,TRY-XXXX-XXXX-XXXX")
    maxUses: int = 1
    grantedBalance: int = Field(default=20)
    grantedDays: int = Field(default=7)
    tier: UserTier = UserTier.TRIAL
    expireAt: datetime
    issuedAt: datetime = Field(default_factory=datetime.utcnow)
    version: int = 1


class RechargeCode(BaseModel):
    """充值码内容(运营签发)"""

    code: str = Field(..., description="充值码明文,RCH-XXXX-XXXX-XXXX")
    amount: int = Field(..., ge=1, description="充值面额(币)")
    expireAt: datetime
    issuedAt: datetime = Field(default_factory=datetime.utcnow)
    note: str = Field(default="", description="运营备注")
    version: int = 1


class License(BaseModel):
    """激活成功后写入本地的凭证(AES-GCM 加密存于 config/license.enc)

    与 license.py 的 activationData 字段**互不冲突**:
        - 老字段(betaLock / deviceCode / validityPeriod / userType)由 LicenseManager 维护
        - 本模型字段(userId / displayName / authMode / grantedBalance 等)由 AuthService 维护
        - 两者写入同一文件的不同 namespace,互不覆盖
    """

    licenseId: str = Field(..., description="本机许可证 UUID")
    userId: str = Field(..., description="用户唯一标识")
    displayName: str = Field(..., description="用户显示名")
    authMode: AuthMode = Field(..., description="鉴权模式")
    tier: UserTier = Field(..., description="用户档位")
    activatedAt: datetime = Field(default_factory=datetime.utcnow)
    expireAt: datetime = Field(..., description="凭证到期时间")
    deviceFingerprint: str = Field(default="", description="本机设备指纹(本期不强制校验)")
    grantedBalance: int = Field(default=0, description="激活时赠送的初始余额")
    payloadJson: str = Field(default="", description="原始凭证内容,用于追溯")


class Device(BaseModel):
    """设备指纹(对外展示用)"""

    fingerprint: str = Field(..., description="机器码 sha256 摘要(64 hex)")
    platform: str
    hostname: str
    featureSummary: dict = Field(default_factory=dict, description="关键特征摘要")


class RedeemResult(BaseModel):
    """凭证兑换结果(统一返回)"""

    success: bool
    message: str
    # 机器可读错误码:OK / ALREADY_AUTHENTICATED / EXPIRED / INVALID
    code: str = "OK"
    license: Optional[License] = None
    grantedBalance: int = 0
    expireAt: Optional[datetime] = None
    userId: Optional[str] = None


__all__ = [
    "AuthMode",
    "UserTier",
    "InviteCode",
    "TrialCode",
    "RechargeCode",
    "License",
    "Device",
    "RedeemResult",
]