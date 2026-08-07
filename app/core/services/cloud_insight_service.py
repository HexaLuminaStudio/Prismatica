# coding: utf-8
"""
P0-A 桌面端 CloudInsightService:M12「高级功能接入扣费闭环」。

把 ai_insight_service 的 explain() 调用包在 FeatureGate 之后:
    1. UI / widget 调 `insightService.runWithBilling(type, data, corpusMeta)`
    2. FeatureGate.requireFeature('ai_insight', resourceUsed=textLen) →
        失败:emit failed(reason) 并弹窗
        成功:preauth → 调 ai_insight_service.explain
            - 成功 → settle(realCost)
            - 失败 → refund

继承 / 复用 AiInsightService 的 textReceived / streamFinished / failed 信号。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal

from app.core.utils import logger, signalBus

from .ai_insight_service import AiInsightService
from .feature_gate import GateResult, getFeatureGate

# 计费特征码(对应后端 pricing_service 的 actionType)
FEATURE_AI_INSIGHT = "ai_insight"


class CloudInsightService(QObject):
    """带扣费闭环的 AI 洞察门面。"""

    # 复用 AiInsightService 的流式信号
    textReceived = Signal(str, int)
    streamFinished = Signal()
    failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._inner = AiInsightService(self)
        # 中继信号
        self._inner.textReceived.connect(self.textReceived)
        self._inner.streamFinished.connect(self._onStreamFinished)
        self._inner.failed.connect(self._onFailed)

        # 扣费上下文(由 requireFeature 设置,settle / refund 时使用)
        self._currentGate = None
        self._currentType: Optional[str] = None

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def runWithBilling(
        self,
        analysisType: str,
        data: Dict[str, Any],
        corpusMeta: Optional[Dict[str, Any]] = None,
        *,
        parentWidget=None,
    ) -> bool:
        """带扣费的 AI 洞察(主入口)。

        Returns: True 表示成功发起;False 表示被 gate 挡掉(emit failed)。
        """
        if self._inner.isRunning:
            logger.warning("[CloudInsight] 已有进行中的解读,忽略新请求")
            return False

        # 估算资源量(用 data 文本长度近似)
        resourceUsed = _estimateInsightResource(analysisType, data)

        # 1) 扣费 gate
        gate = getFeatureGate()
        result: GateResult = gate.requireFeature(
            FEATURE_AI_INSIGHT,
            resourceUsed=resourceUsed,
            taskId=f"ai:{analysisType}",
            description=f"AI 洞察 · {analysisType}",
        )
        if not result.ok:
            logger.info(
                f"[CloudInsight] gate 拒绝: reason={result.reason} message={result.message}"
            )
            # 弹窗(由 widget parent 提供;没有 parent 时只发信号)
            try:
                gate.handleBlockReason(result, parentWidget)
            except Exception:
                logger.exception("[CloudInsight] handleBlockReason 失败")
            self.failed.emit(result.message or f"无法发起: {result.reason}")
            return False

        self._currentGate = result
        self._currentType = analysisType

        # 2) 真正调 LLM
        ok = self._inner.explain(analysisType, data, corpusMeta=corpusMeta)
        if not ok:
            # AiInsightService 自己已经 emit failed;但我们也 refund(若 preauth 写过)
            self._refundSafe()
        return ok

    # ------------------------------------------------------------------
    # 内部信号中继 + 自动 settle
    # ------------------------------------------------------------------

    def _onStreamFinished(self) -> None:
        # LLM 完成 → settle(实际消耗 = max(1, 估算);简化:用 estimated 即可)
        if self._currentGate is None:
            self.streamFinished.emit()
            return
        try:
            estimatedCost = int(self._currentGate.context.get("estimatedCost", 1))
            resourceUsed = int(self._currentGate.context.get("resourceUsed", 0))
            settleFn = self._currentGate.context.get("settle")
            if settleFn is not None:
                settleFn(estimatedCost, resourceUsed)
        except Exception:
            logger.exception("[CloudInsight] 自动 settle 失败,转 refund")
            self._refundSafe()
        finally:
            self._currentGate = None
            self._currentType = None
            self.streamFinished.emit()

    def _onFailed(self, err: str) -> None:
        # LLM 失败 → 立即 refund
        self._refundSafe()
        self.failed.emit(err)

    def _refundSafe(self) -> None:
        if self._currentGate is None:
            return
        try:
            refundFn = self._currentGate.context.get("refund")
            if refundFn is not None:
                refundFn()
        except Exception:
            logger.exception("[CloudInsight] refund 失败")
        finally:
            self._currentGate = None
            self._currentType = None

    # ------------------------------------------------------------------
    # 兼容旧 widget 的便捷入口(直接走 LLM,不扣费)
    # ------------------------------------------------------------------

    def explain(self, analysisType: str, data: Dict[str, Any], corpusMeta: Optional[Dict[str, Any]] = None) -> bool:
        """无扣费的解读(向后兼容);新代码请用 runWithBilling。"""
        return self._inner.explain(analysisType, data, corpusMeta)

    def stop(self) -> None:
        self._inner.stop()

    @property
    def isRunning(self) -> bool:
        return self._inner.isRunning

    @property
    def responseText(self) -> str:
        return self._inner.responseText

    @property
    def tokenUsage(self) -> int:
        return self._inner.tokenUsage


def _estimateInsightResource(analysisType: str, data: Dict[str, Any]) -> int:
    """估算 AI 洞察的资源量(用于计费)。

    简单规则:取 data 中所有字符串字段的总长度,按 1000 字符为 1 个单位。
    """
    totalChars = 0

    def _walk(value: Any) -> None:
        nonlocal totalChars
        if isinstance(value, str):
            totalChars += len(value)
        elif isinstance(value, dict):
            for v in value.values():
                _walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                _walk(v)

    _walk(data or {})
    # 向上取整到 1 千字
    return max(1, (totalChars + 999) // 1000)


_singleton: CloudInsightService | None = None


def getCloudInsightService() -> CloudInsightService:
    global _singleton
    if _singleton is None:
        _singleton = CloudInsightService()
    return _singleton


__all__ = [
    "CloudInsightService",
    "FEATURE_AI_INSIGHT",
    "getCloudInsightService",
]
