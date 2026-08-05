# coding: utf-8
"""业务装饰器:把任意 service 方法包装为自动计费

用法:
    from app.core.services.decorators import charged
    from app.core.models.billing_models import ActionType

    class FreqAnalyzerService:
        @charged(ActionType.FREQ_ANALYZE, resourceFrom="corpus.charCount")
        def runAnalysis(self, corpus, options):
            ...

`resourceFrom` 支持两种取值:
    - "arg.<name>"  : 取 args/kwargs 中名为 <name> 的参数
    - "result.<attr>": 取方法返回值的 <attr> 属性
    - 默认 0       : 资源量视为 0(只收基础费)

任何异常路径都会触发自动退款。
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Optional

from loguru import logger

from app.core.models.billing_models import ActionType
from app.core.services.auth_service import getAuthService
from app.core.services.billing_service import (
    InsufficientBalanceError,
    getBillingService,
)


def _extractResource(
    args: tuple,
    kwargs: dict,
    result: Any,
    spec: str,
) -> int:
    """根据 spec 从 args/kwargs/result 中提取资源量。"""
    try:
        if spec.startswith("arg."):
            name = spec[4:]
            if name in kwargs:
                return int(kwargs[name] or 0)
            for arg in args:
                if hasattr(arg, name):
                    return int(getattr(arg, name) or 0)
            return 0
        if spec.startswith("result."):
            attr = spec[7:]
            if hasattr(result, attr):
                return int(getattr(result, attr) or 0)
            if isinstance(result, dict) and attr in result:
                return int(result[attr] or 0)
            return 0
        if spec.startswith("kwarg."):
            name = spec[6:]
            return int(kwargs.get(name, 0) or 0)
        return 0
    except Exception:
        return 0


def charged(
    action: ActionType,
    resourceFrom: str = "0",
    confirmRequired: bool = True,
    confirmHook: Optional[Callable] = None,
) -> Callable:
    """自动计费装饰器。

    Args:
        action:        动作类型
        resourceFrom:  资源量提取规则
        confirmRequired: 是否需要 UI 二次确认(默认 True)
        confirmHook:   自定义确认回调(返回 bool),None 则自动跳过 UI 确认
                       (供单元测试或脚本调用)
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            auth = getAuthService()
            userId = auth.currentUserId()
            if userId is None:
                logger.warning(
                    f"[charged] 未鉴权,跳过计费 action={action.value} func={func.__name__}"
                )
                # 内测期仍允许执行,避免阻断现有业务
                return func(self, *args, **kwargs)

            billing = getBillingService()
            # 1) 预估资源量(执行前用 args 估算)
            preResource = _extractResource(args, kwargs, None, resourceFrom)
            preview = billing.preview(action, preResource, userId)
            logger.debug(
                f"[charged] 预估 user={userId} action={action.value} "
                f"resource={preResource} cost={preview.estimatedCost}"
            )

            # 2) 余额不足直接拒绝(2026-08-05 T7):
            # 之前逻辑已经检查 affordable,但同时也会再走 preauth → 云端预占
            # → 当 balance=0 时还白白走一趟 /v1/billing/preauth 现预占 0 额度,失败再回退,
            # 流程冗余。这次修正语义:affordable=False 直接抛,不再发起云端预占请求。
            if not preview.affordable:
                logger.warning(
                    f"[charged] 余额不足 user={userId} "
                    f"balance={preview.currentBalance} need={preview.estimatedCost}"
                )
                raise InsufficientBalanceError(
                    preview.currentBalance, preview.estimatedCost
                )

            # 3) UI 确认(若启用)
            if confirmRequired and confirmHook is not None:
                try:
                    confirmed = confirmHook(preview)
                except Exception as e:
                    logger.warning(f"[charged] confirmHook 异常,默认放行: {e}")
                    confirmed = True
                if not confirmed:
                    logger.info(f"[charged] 用户取消计费 action={action.value}")
                    return None

            # 4) 预占
            taskId = kwargs.get("taskId", "") or ""
            preauth = billing.frozenPreauth(
                userId=userId,
                action=action,
                resourceUsed=preResource,
                taskId=taskId,
                description=func.__name__,
            )
            if not preauth.success:
                logger.warning(f"[charged] 预占失败: {preauth.message}")
                raise InsufficientBalanceError(preview.currentBalance, preview.estimatedCost)

            # 5) 执行原方法
            try:
                result = func(self, *args, **kwargs)
            except Exception as e:
                logger.exception(
                    f"[charged] 业务执行异常,触发自动退款: {e}"
                )
                try:
                    billing.refund(preauth.billId, reason=f"exception:{type(e).__name__}")
                except Exception as refundErr:
                    logger.error(f"[charged] 自动退款失败: {refundErr}")
                raise

            # 6) 结算(按真实资源量)
            try:
                realResource = _extractResource(args, kwargs, result, resourceFrom)
                billing.settle(preauth.billId, realResource)
            except Exception as e:
                logger.warning(f"[charged] 结算失败,降级为退款: {e}")
                try:
                    billing.refund(preauth.billId, reason=f"settle_failed:{e}")
                except Exception:
                    pass
            return result

        return wrapper

    return decorator


__all__ = ["charged"]