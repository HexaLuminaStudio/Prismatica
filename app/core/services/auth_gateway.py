# coding: utf-8
"""AuthGateway(2026-08-05 T2 新增)

把 AuthService 中云端相关的逻辑拆分出来,只负责:
    - 调 CloudApi 完成 redeem / refresh / logout
    - 把云端响应解析成 RedeemResult(供 UI 显示)
    - 失败统一抛 CloudApiError / 网关错误,由调用方决定 UI 文案

不负责:
    - 持久化 license.enc(由 AuthService 负责)
    - import account_db(强云端决策下,账户只在云端)

调用方约定:
    - 业务侧(LoginDialog / account_interface / billing_service 等)统一通过本类做云端调用,
      不再直接 import cloud_api / cloud_cache。
    - token 持久化由 AuthService 持有 TokenStore 接口实现(避免循环 import)。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

from app.core.models.auth_models import (
    AuthMode,
    License,
    RedeemResult,
    UserTier,
)
from app.core.services.cloud_api import (
    CloudApi,
    CloudApiError,
    getCloudApi,
)
from app.core.utils.signal_bus import signalBus


class AuthGateway:
    """用户鉴权编排门面(强云端,不再写本地 SQLite)。

    单例入口由 getAuthGateway() 提供。
    """

    def __init__(self, api: Optional[CloudApi] = None):
        self._api = api or getCloudApi()

    # ============================================================
    # 公开 API
    # ============================================================

    def redeem(self, rawCode: str, displayName: Optional[str] = None) -> RedeemResult:
        """统一兑换入口(INV/TRY/RCH 全部交给云端)。

        云端返回失败 → 返回 RedeemResult(success=False, code=..., message=...)
        网络异常 → 返回 RedeemResult(success=False, code=NETWORK_ERROR, message=...)
        """
        rawCode = (rawCode or "").strip()
        if not rawCode:
            return RedeemResult(success=False, code="INVALID", message="请输入凭证")

        try:
            data = self._api.redeem(
                code=rawCode,
                displayName=displayName or "内测用户",
            )
        except CloudApiError as e:
            logger.warning(f"[AuthGateway] redeem CloudApiError: {e.code} {e.message}")
            return RedeemResult(
                success=False,
                code=e.code or "INVALID",
                message=e.message or "兑换失败",
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[AuthGateway] redeem 异常: {e}")
            return RedeemResult(
                success=False,
                code="NETWORK_ERROR",
                message=f"云端不可达: {e}",
            )

        return _buildRedeemResult(data)

    def refreshSession(self) -> bool:
        """主动调用 refresh(目前由 CloudApi._request 内部自动处理,这里预留)。"""
        rt = self._getRefreshToken()
        if not rt:
            return False
        try:
            self._api.refresh(rt)
            return True
        except CloudApiError as e:
            logger.warning(f"[AuthGateway] refreshSession 失败: {e.code} {e.message}")
            return False
        except Exception:  # noqa: BLE001
            return False

    def logout(self) -> None:
        """best-effort 注销(失败也清理本地)。"""
        try:
            self._api.logout()
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                signalBus.activationStatusChanged.emit(False)
            except Exception:  # noqa: BLE001
                pass

    # ============================================================
    # 内部
    # ============================================================

    def _getRefreshToken(self) -> str:
        try:
            from app.core.services.auth_service import getAuthService

            return getAuthService().getRefreshToken() or ""
        except Exception:  # noqa: BLE001
            return ""


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _buildRedeemResult(data: dict) -> RedeemResult:
    """把云端 redeem 响应(对齐后端 PRD §5.2 RedeemResponse)构造 RedeemResult。"""
    mode = (data or {}).get("mode")  # invite / trial / recharge
    userDict = (data or {}).get("user") or {}
    balanceDict = (data or {}).get("balance") or {}
    granted = int(balanceDict.get("balance", 0) or 0)

    if not userDict or not userDict.get("userId"):
        return RedeemResult(
            success=False,
            code="INVALID",
            message="云端响应缺少 user 信息",
        )

    userId = str(userDict["userId"])
    displayName = str(userDict.get("displayName") or "内测用户")
    tier = str(userDict.get("tier", "beta"))
    activatedAt = datetime.utcnow()
    # 云端权威:优先用云端返回的真实 expireAt;缺失才兜底(默认 365 天,等 /me 同步替换)
    serverExpire = userDict.get("expireAt")
    expireAt = activatedAt + timedelta(days=365)
    if serverExpire:
        try:
            parsed = datetime.fromisoformat(str(serverExpire).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            expireAt = parsed
        except Exception:  # noqa: BLE001
            pass

    authMode = {
        "invite": AuthMode.INVITE_CODE,
        "trial": AuthMode.TRIAL_CODE,
        "recharge": AuthMode.INVITE_CODE,  # 充值模式 user 已有凭证,标记为 INVITE
        "activation": AuthMode.ACTIVATION_CODE,
    }.get(mode, AuthMode.INVITE_CODE)

    try:
        userTier = UserTier(tier)
    except Exception:
        userTier = UserTier.BETA

    lic = License(
        licenseId=f"lic_{_shortId()}",
        userId=userId,
        displayName=displayName,
        authMode=authMode,
        tier=userTier,
        activatedAt=activatedAt,
        expireAt=expireAt,
        deviceFingerprint="",
        grantedBalance=granted,
        payloadJson="",
    )

    msg = {
        "invite": f"激活成功!已赠送 {granted} 币",
        "trial": f"体验激活成功!已赠送 {granted} 币",
        "recharge": f"充值成功 +{granted} 币",
        "activation": "激活成功",
    }.get(mode, "兑换成功")

    return RedeemResult(
        success=True,
        code="OK",
        message=msg,
        license=lic,
        grantedBalance=granted,
        expireAt=expireAt,
        userId=userId,
    )


def _shortId() -> str:
    """12hex 短 ID(用于 licenseId 占位)。"""
    import uuid

    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------


_gatewayInstance: Optional[AuthGateway] = None


def getAuthGateway() -> AuthGateway:
    """获取 AuthGateway 全局单例。"""
    global _gatewayInstance
    if _gatewayInstance is None:
        _gatewayInstance = AuthGateway()
    return _gatewayInstance


def resetAuthGatewayForTesting() -> None:
    """测试钩子:重置单例。"""
    global _gatewayInstance
    _gatewayInstance = None


__all__ = ["AuthGateway", "getAuthGateway", "resetAuthGatewayForTesting"]
