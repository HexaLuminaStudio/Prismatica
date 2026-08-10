# coding: utf-8
"""AI 解读兼容门面。

实际 AI 调用和 Token 结算已经统一下沉到平台 ChatService；本类不再额外预占，
避免一次 AI 解读生成两笔账单。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal

from app.core.utils import logger

from .ai_insight_service import AiInsightService

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
        """平台 AI 洞察主入口。

        Returns: True 表示成功发起；False 表示已有任务或请求未启动。
        """
        if self._inner.isRunning:
            logger.warning("[CloudInsight] 已有进行中的解读,忽略新请求")
            return False

        self._currentType = analysisType
        ok = self._inner.explain(analysisType, data, corpusMeta=corpusMeta)
        return ok

    # ------------------------------------------------------------------
    # 内部信号中继
    # ------------------------------------------------------------------

    def _onStreamFinished(self) -> None:
        self._currentType = None
        self.streamFinished.emit()

    def _onFailed(self, err: str) -> None:
        self._currentType = None
        self.failed.emit(err)

    # ------------------------------------------------------------------
    # 兼容旧 widget 的便捷入口（仍由平台端按真实 Token 计费）
    # ------------------------------------------------------------------

    def explain(self, analysisType: str, data: Dict[str, Any], corpusMeta: Optional[Dict[str, Any]] = None) -> bool:
        """向后兼容入口；新代码请使用 runWithBilling。"""
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
