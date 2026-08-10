# coding: utf-8
"""
AI 解读服务门面（PRD-001 REQ-AI-001）

包装 ChatService，对全部分析类型提供统一的解读入口。
复用现有 ChatService 的 LLM 调用能力；通过 prompt 参数注入
按分析类型 + 风格动态构造的 system prompt。

流式输出通过信号 textReceived(str, int) / streamFinished() / failed(str) 推送，
与 ChatService 保持一致，UI 侧只需额外切换 Prompt 即可在 Chat 与 Insight 间复用。

设计：
    - 单一通用入口 explain(analysis_type, data, corpus_meta)，
      各 widget 只需按 protocol 提供 (type, data) 元组
    - type 与 data schema 的约束下沉到 insight_prompts.buildPrompt
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from app.core.utils import cfg, logger, qconfig

from .chat_service import ChatService
from .insight_prompts import (
    buildPrompt,
    summarizeCollocationData,
    summarizeConstructionData,
    summarizeDependencyData,
    summarizeFreqData,
    summarizeKeywordListData,
    summarizeKwicData,
    summarizeNetworkData,
    summarizeNgramClusterData,
    summarizeSentimentData,
    summarizeWordAnalysisData,
    summarizeWordCloudData,
)


# 模块级 analysis_type 常量（便于外部避免拼写错误 + 类型提示）
TYPE_FREQ = "freq"
TYPE_NETWORK = "network"
TYPE_KWIC = "kwic"
TYPE_COLLOCATION = "collocation"
TYPE_CONSTRUCTION = "construction"
TYPE_DEPENDENCY = "dependency"
TYPE_KEYWORD_LIST = "keyword_list"
TYPE_NGRAM_CLUSTER = "ngram_cluster"
TYPE_SENTIMENT = "sentiment"
TYPE_WORD_CLOUD = "word_cloud"
TYPE_WORD_ANALYSIS = "word_analysis"

# 所有支持的类型（供遍历检查）
SUPPORTED_TYPES = (
    TYPE_FREQ,
    TYPE_NETWORK,
    TYPE_KWIC,
    TYPE_COLLOCATION,
    TYPE_CONSTRUCTION,
    TYPE_DEPENDENCY,
    TYPE_KEYWORD_LIST,
    TYPE_NGRAM_CLUSTER,
    TYPE_SENTIMENT,
    TYPE_WORD_CLOUD,
    TYPE_WORD_ANALYSIS,
)


class AiInsightService(QObject):
    """AI 解读服务门面

    Signals:
        textReceived(str, int): 流式 token 增量
        streamFinished(): 本次解读正常结束
        failed(str): 解读失败（含 API Key 未配置 / LLM 异常）
    """

    textReceived = Signal(str, int)
    streamFinished = Signal()
    failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        # 复用 ChatService 的 LLM 流式调用
        self._chat = ChatService(self)
        self._chat.textReceived.connect(self.textReceived)
        self._chat.streamFinished.connect(self._onChatFinished)
        self._chat.failed.connect(self._onChatFailed)

        # 当前解读类型（用于在 finished / failed 时识别上下文）
        self._currentType: Optional[str] = None

    # ------------------------------------------------------------------
    # 公共 API：通用入口
    # ------------------------------------------------------------------
    def explain(
        self,
        analysisType: str,
        data: Dict[str, Any],
        corpusMeta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """发起一次 AI 解读(统一入口)

        Args:
            analysisType: 分析类型（见 SUPPORTED_TYPES）
            data: 取决于分析类型，约定字段见 insight_prompts.buildPrompt
            corpusMeta: 语料元信息,自动注入到 data["corpusMeta"]

        Returns:
            True 表示成功发起;False 表示被守卫挡掉(已在跑 / 数据为空 / 类型未知)
        """
        if self._chat.isRunning:
            logger.warning("[AiInsightService] 已有进行中的解读，忽略新请求")
            return False

        # 注入 corpusMeta
        if corpusMeta:
            data = dict(data or {})
            data["corpusMeta"] = corpusMeta

        # 计算 summary(供 buildPrompt 与 _guardData 共用)
        summary = self._summarizeForType(analysisType, data)
        if summary is not None:
            data = dict(data)
            data["summary"] = summary

        # 触发数据守卫（summarizeXxx 返回 None / empty 时拒绝）
        guard = self._guardData(analysisType, data)
        if not guard.ok:
            logger.info(
                f"[AiInsightService] 数据守卫拒绝解读, "
                f"type={analysisType}, reason={guard.reason}"
            )
            self.failed.emit(guard.reason)
            return False

        style = qconfig.get(cfg.AiInsightStyle) or "学术"
        prompts = buildPrompt(analysisType, data, style=style)
        self._currentType = analysisType
        logger.info(
            f"[AiInsightService] 发起解读, type={analysisType}, style={style}"
        )
        self._chat.ask(
            message=prompts["user"],
            prompt=prompts["system"],
            fileText="",
            featureCode="ai_insight",
        )
        return True

    @staticmethod
    def _summarizeForType(
        analysisType: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """按类型对 data 做汇总,返回 summary dict;不可汇总则 None"""
        try:
            if analysisType == TYPE_COLLOCATION:
                return summarizeCollocationData(data.get("result"))
            if analysisType == TYPE_CONSTRUCTION:
                return summarizeConstructionData(data.get("result"))
            if analysisType == TYPE_DEPENDENCY:
                # 依存数据是 List[DependencyParse]
                return summarizeDependencyData(data.get("result"))
            if analysisType == TYPE_KEYWORD_LIST:
                return summarizeKeywordListData(data.get("result"))
            if analysisType == TYPE_NGRAM_CLUSTER:
                return summarizeNgramClusterData(data.get("result"))
            if analysisType == TYPE_SENTIMENT:
                return summarizeSentimentData(data.get("result"))
            if analysisType == TYPE_WORD_CLOUD:
                return summarizeWordCloudData(data.get("result"))
            if analysisType == TYPE_WORD_ANALYSIS:
                return summarizeWordAnalysisData(data.get("result"))
        except Exception as e:
            logger.warning(f"[AiInsightService] 汇总 {analysisType} 数据失败: {e}")
        return None

    # ------------------------------------------------------------------
    # 数据守卫：按类型检查 data 是否可解读
    # ------------------------------------------------------------------
    @staticmethod
    def _guardData(analysisType: str, data: Dict[str, Any]) -> "GuardResult":
        """返回 (ok, reason)"""
        if analysisType not in SUPPORTED_TYPES:
            return GuardResult(False, f"未知的分析类型: {analysisType}")

        if analysisType == TYPE_FREQ:
            rows = _summarizeRows(data.get("rows"), maxRows=50)
            if not rows:
                return GuardResult(False, "没有可解读的词频数据。")
        elif analysisType == TYPE_NETWORK:
            summary = summarizeNetworkData(
                data.get("network"),
                windowSize=data.get("windowSize"),
                metric=data.get("metric", "LogDice"),
            )
            if summary.get("nodeCount", 0) == 0:
                return GuardResult(False, "网络为空，无可解读的数据。")
        elif analysisType == TYPE_KWIC:
            kwicSum = summarizeKwicData(
                data.get("hits"), query=data.get("query", "") or "", topN=3, sampleN=10
            )
            if kwicSum.get("total", 0) == 0:
                return GuardResult(False, "没有可解读的 KWIC 命中。")
        elif analysisType == TYPE_COLLOCATION:
            colSum = summarizeCollocationData(data.get("result"))
            if colSum.get("collocateCount", 0) == 0:
                return GuardResult(False, "没有可解读的搭配结果。")
        elif analysisType == TYPE_CONSTRUCTION:
            conSum = summarizeConstructionData(data.get("result"))
            if conSum.get("slotCount", 0) == 0:
                return GuardResult(False, "没有可解读的构式结果。")
        elif analysisType == TYPE_DEPENDENCY:
            depSum = summarizeDependencyData(data.get("result"))
            if depSum.get("relationCount", 0) == 0:
                return GuardResult(False, "没有可解读的依存关系。")
        elif analysisType == TYPE_KEYWORD_LIST:
            kwSum = summarizeKeywordListData(data.get("result"))
            if kwSum.get("keywordCount", 0) == 0:
                return GuardResult(False, "没有可解读的关键词列表。")
        elif analysisType == TYPE_NGRAM_CLUSTER:
            ngSum = summarizeNgramClusterData(data.get("result"))
            if ngSum.get("clusterCount", 0) == 0:
                return GuardResult(False, "没有可解读的 N-gram 聚类。")
        elif analysisType == TYPE_SENTIMENT:
            sentSum = summarizeSentimentData(data.get("result"))
            if sentSum.get("docCount", 0) == 0:
                return GuardResult(False, "没有可解读的情感分析结果。")
        elif analysisType == TYPE_WORD_CLOUD:
            wcSum = summarizeWordCloudData(data.get("result"))
            if wcSum.get("wordCount", 0) == 0:
                return GuardResult(False, "没有可解读的词云数据。")
        elif analysisType == TYPE_WORD_ANALYSIS:
            waSum = summarizeWordAnalysisData(data.get("result"))
            if waSum.get("totalTokens", 0) == 0:
                return GuardResult(False, "没有可解读的词语分析结果。")

        return GuardResult(True, "")

    # ------------------------------------------------------------------
    # 旧接口保留(向下兼容 3 个已存在的 widget)
    # ------------------------------------------------------------------
    def explainFreq(self, df: Any, corpusMeta: Dict[str, Any]) -> bool:
        """发起词频分析解读(兼容旧调用)"""
        return self.explain(TYPE_FREQ, {"rows": df}, corpusMeta=corpusMeta)

    def explainNetwork(
        self,
        network: Any,
        params: Dict[str, Any],
        corpusMeta: Dict[str, Any],
    ) -> bool:
        """发起共现网络分析解读(兼容旧调用)"""
        # 先汇总成 summary,buildPrompt 期望 data["summary"]
        summary = summarizeNetworkData(
            network,
            windowSize=params.get("windowSize"),
            metric=params.get("metric", "LogDice"),
        )
        return self.explain(
            TYPE_NETWORK,
            {
                "summary": summary,
                "windowSize": params.get("windowSize"),
                "metric": params.get("metric", "LogDice"),
            },
            corpusMeta=corpusMeta,
        )

    def explainKwic(
        self,
        hits: Any,
        query: str,
        corpusMeta: Dict[str, Any],
    ) -> bool:
        """发起 KWIC 检索结果解读(兼容旧调用)"""
        # 预先汇总左 / 右搭配高频词,buildPrompt 期望 data["topLeft"] / data["topRight"]
        summary = summarizeKwicData(hits, query=query, topN=3, sampleN=10)
        return self.explain(
            TYPE_KWIC,
            {
                "hits": summary["sampleHits"],
                "query": query,
                "topLeft": summary["topLeft"],
                "topRight": summary["topRight"],
            },
            corpusMeta=corpusMeta,
        )

    # ------------------------------------------------------------------
    # 中断 / 状态查询
    # ------------------------------------------------------------------
    def stop(self) -> None:
        """请求中断当前解读（UI 主动调用，如关闭 drawer）"""
        if self._chat.isRunning:
            logger.info("[AiInsightService] 用户主动中断解读")
            self._chat.stop()
            # 重置当前类型上下文,避免后续 finished 信号误触发
            self._currentType = None

    @property
    def isRunning(self) -> bool:
        return self._chat.isRunning

    @property
    def responseText(self) -> str:
        return self._chat.responseText

    @property
    def tokenUsage(self) -> int:
        return self._chat.tokenUsage

    # ------------------------------------------------------------------
    # 内部信号中继
    # ------------------------------------------------------------------
    def _onChatFinished(self) -> None:
        logger.info(
            f"[AiInsightService] 解读完成, type={self._currentType}, "
            f"responseChars={len(self._chat.responseText)}, "
            f"tokens={self._chat.tokenUsage}"
        )
        self._currentType = None
        self.streamFinished.emit()

    def _onChatFailed(self, err: str) -> None:
        logger.error(f"[AiInsightService] 解读失败, type={self._currentType}: {err}")
        # 包装错误文案，让用户更易理解
        msg = err
        if not msg or "API Key" in msg:
            msg = "AI 解读失败：" + (err or "未知错误")
        self._currentType = None
        self.failed.emit(msg)


class GuardResult:
    """_guardData 的轻量返回类型"""

    __slots__ = ("ok", "reason")

    def __init__(self, ok: bool, reason: str = ""):
        self.ok = ok
        self.reason = reason


def _summarizeRows(rows: Any, maxRows: int = 50) -> List[Dict[str, Any]]:
    """把 freq 词频行规整成 list[dict]。

    兼容三种入参:
        - pandas DataFrame（自带 .head / .to_dict）
        - list / tuple（widget 已转换过的记录列表）
        - None / 其他类型（视为空）

    取前 maxRows 行(列表切片 / DataFrame.head),失败返回 []。
    """
    if rows is None:
        return []
    # pandas DataFrame
    try:
        import pandas as _pd  # noqa: PLC0415

        if isinstance(rows, _pd.DataFrame):
            if rows.empty:
                return []
            return rows.head(maxRows).to_dict(orient="records")
    except Exception:
        pass
    # list / tuple of dict
    if isinstance(rows, (list, tuple)):
        out: List[Dict[str, Any]] = []
        for item in list(rows)[:maxRows]:
            if isinstance(item, dict):
                out.append(item)
            else:
                # 非 dict 行(如 NamedTuple / dataclass)转 dict
                try:
                    out.append(
                        dict(item) if hasattr(item, "__dict__") else {"value": item}
                    )
                except Exception:
                    continue
        return out
    return []
