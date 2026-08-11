# coding: utf-8
"""
P0-A 桌面端 FeatureGate:高级功能扣费闭环的统一入口。

业务规则:
    - requireFeature(featureCode, estimatedCost=0)  → 返回 GateResult
        - 未登录:reason='login_required',UI 弹登录窗
        - 余额不足:reason='insufficient_balance',UI 弹充值窗
        - 离线/网络:reason='offline',UI 提示
        - 通过:reason='ok',返回 (preauthResp, settleFn, refundFn)

UI 用法:
    result = featureGate.requireFeature('ai_insight', resourceUsed=textLen)
    if not result.ok:
        featureGate.handleBlockReason(result, parent)
        return
    preauth = result.context['preauth']
    try:
        llmReply = call_llm(...)
        featureGate.settle(preauth['billId'], realCost=calc_real_cost(...))
    except Exception:
        featureGate.refund(preauth['billId'])
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from app.core.utils import logger, signalBus

from .cloud_api import CloudApiError, getCloudApi
from .cloud_auth import getCloudAuth
from .cloud_billing import getCloudBilling


@dataclass
class GateResult:
    ok: bool
    reason: str = ""  # ok / login_required / insufficient_balance / offline / blocked
    message: str = ""
    context: Dict[str, Any] = field(default_factory=dict)


class FeatureGate(QObject):
    """高级功能入口(由 AI 洞察 / 跨设备同步等调用)。"""

    featureBlocked = Signal(str, str)  # reason, message — UI 收到后弹窗

    # 资源量估算函数(可选覆盖);key=featureCode,value=Callable[widget, params] -> int
    _resourceEstimators: Dict[str, Callable[..., int]] = {}

    def registerResourceEstimator(self, featureCode: str, fn: Callable[..., int]) -> None:
        self._resourceEstimators[featureCode] = fn

    def estimateResource(self, featureCode: str, *args, **kwargs) -> int:
        fn = self._resourceEstimators.get(featureCode)
        if fn is None:
            return 0
        try:
            return int(fn(*args, **kwargs))
        except Exception:
            logger.exception(f"[FeatureGate] 估算 {featureCode} 资源量失败")
            return 0

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def requireFeature(
        self,
        featureCode: str,
        *,
        estimatedCost: int = 0,
        resourceUsed: int = 0,
        taskId: str = "",
        description: str = "",
        idempotencyKey: str | None = None,
    ) -> GateResult:
        """主入口:检查登录 + 余额 → preauth → 返回 settle / refund 闭包。"""
        auth = getCloudAuth()
        api = getCloudApi()
        operationId = idempotencyKey or str(uuid.uuid4())

        if not auth._api.isLoggedIn():
            return GateResult(ok=False, reason="login_required", message="请先登录账号")

        # 在线检测(快速 ping);失败 → offline
        try:
            api.get("/v1/account/me", timeout=3.0)
        except CloudApiError as exc:
            if exc.code == "NETWORK_ERROR":
                return GateResult(ok=False, reason="offline", message="网络异常,请检查后重试")
            # 401 已被自动 refresh 处理,如果还失败说明 token 已失效
            if exc.code in ("UNAUTHORIZED", "TOKEN_REVOKED"):
                return GateResult(ok=False, reason="login_required", message="登录已过期,请重新登录")
            # 其他错误(5xx 等)按 offline 处理,避免阻塞
            logger.warning(f"[FeatureGate] /me 探活失败: {exc}")

        # 估算资源(若调用方未指定)
        if resourceUsed == 0:
            resourceUsed = self.estimateResource(featureCode)

        # 1) estimate
        try:
            preview = getCloudBilling().estimate(featureCode, resourceUsed)
        except CloudApiError as exc:
            return GateResult(ok=False, reason="offline", message=f"计价失败: {exc.message}")

        actualCost = int(preview.get("estimatedCost", estimatedCost) or estimatedCost)
        if not preview.get("affordable", True):
            return GateResult(
                ok=False,
                reason="insufficient_balance",
                message="余额不足,无法继续",
                context={"required": actualCost, "current": preview.get("currentBalance", 0)},
            )

        # 2) preauth
        try:
            preauthResp = getCloudBilling().preauth(
                featureCode,
                resourceUsed,
                taskId=taskId,
                description=description or featureCode,
                idempotencyKey=operationId,
            )
        except CloudApiError as exc:
            if exc.code == "INSUFFICIENT_BALANCE":
                return GateResult(
                    ok=False,
                    reason="insufficient_balance",
                    message=exc.message,
                    context={"required": exc.details.get("required", 0)},
                )
            return GateResult(ok=False, reason="offline", message=f"预占失败: {exc.message}")

        billId = preauthResp.get("billId")
        if not billId:
            return GateResult(ok=False, reason="offline", message="预占失败:无 billId")
        # estimate 与 preauth 之间可能恰逢价格发布；以真正锁定的预占金额为准。
        actualCost = int(preauthResp.get("estimatedCost", actualCost) or actualCost)

        # 3) 通知 UI 余额变了
        try:
            signalBus.balanceChanged.emit(int(preauthResp.get("balanceAfter", 0)))
        except Exception:
            pass

        # 4) 返回 GateResult,UI 拿到 preauth / settle / refund 闭包
        def _settle(realCost: int, realResource: int = 0) -> Dict[str, Any]:
            try:
                result = getCloudBilling().settle(billId, int(realCost), int(realResource))
                # 触发刷新
                try:
                    refreshed = getCloudApi().get("/v1/account/me", timeout=5.0)
                    if isinstance(refreshed, dict):
                        signalBus.balanceChanged.emit(int(refreshed.get("balance", 0) or 0))
                except Exception:
                    pass
                return result
            except CloudApiError as exc:
                logger.warning(f"[FeatureGate] settle 失败:{exc}; 自动 refund")
                try:
                    getCloudBilling().refund(billId)
                except Exception:
                    logger.exception("[FeatureGate] 失败后 refund 也失败")
                raise

        def _refund() -> Dict[str, Any]:
            try:
                return getCloudBilling().refund(billId)
            finally:
                try:
                    refreshed = getCloudApi().get("/v1/account/me", timeout=5.0)
                    if isinstance(refreshed, dict):
                        signalBus.balanceChanged.emit(int(refreshed.get("balance", 0) or 0))
                except Exception:
                    pass

        return GateResult(
            ok=True,
            reason="ok",
            message="ok",
            context={
                "preauth": preauthResp,
                "settle": _settle,
                "refund": _refund,
                "featureCode": featureCode,
                "estimatedCost": actualCost,
                "resourceUsed": resourceUsed,
                "operationId": operationId,
            },
        )

    # ------------------------------------------------------------------
    # 阻断原因 → UI 弹窗
    # ------------------------------------------------------------------

    def handleBlockReason(self, result: GateResult, parent: Optional[QWidget] = None) -> None:
        """UI 可统一调本方法把阻断原因翻译为用户弹窗。"""
        try:
            from qfluentwidgets import MessageBox
        except Exception:
            MessageBox = None  # type: ignore[assignment]

        title = ""
        body = result.message or ""
        if result.reason == "login_required":
            title = "需要登录"
        elif result.reason == "insufficient_balance":
            title = "余额不足"
        elif result.reason == "offline":
            title = "网络异常"
        else:
            title = "无法继续"

        # 触发全局信号,主窗口 / 抽屉 / 头像红点可以订阅
        try:
            self.featureBlocked.emit(result.reason, body)
        except Exception:
            pass

        if MessageBox is not None:
            try:
                box = MessageBox(title, body, parent)
                if result.reason == "login_required":
                    box.yesButton.setText("去登录")
                    box.cancelButton.setText("取消")
                elif result.reason == "insufficient_balance":
                    box.yesButton.setText("去充值")
                    box.cancelButton.setText("取消")
                else:
                    box.yesButton.setText("好")
                    box.cancelButton.hide()
                box.exec()
            except Exception:
                logger.exception("[FeatureGate] 弹窗失败")


_singleton: FeatureGate | None = None


def getFeatureGate() -> FeatureGate:
    global _singleton
    if _singleton is None:
        _singleton = FeatureGate()
    return _singleton


__all__ = [
    "GateResult",
    "FeatureGate",
    "getFeatureGate",
]
