# coding: utf-8
"""
搭配分析引擎(对标 AntConc Collocates)

按需求文档 v3 §2.4.5:
    FR-CLB-001 节点词搭配统计
    FR-CLB-002 MI 值
    FR-CLB-003 MI3 值
    FR-CLB-004 T-score
    FR-CLB-005 LogDice
    FR-CLB-006 Z-score
    FR-CLB-007 Delta-P(方向性)
    FR-CLB-008 可配置跨距(L/R 独立)
    FR-CLB-009 跨距位置分布
    FR-CLB-010 搭配网络图(此处仅产出边数据,绘图交给 NetworkWidget)
    FR-CLB-011 搭配词导出(由 widget 层处理)

学术严谨性说明
----------------
本引擎的统计量推导基于 2×2 列联表(Church & Hanks 1990)。
抽样单位是一个合法的“center-context 有向位置对”，即每个中心
token 与其 L/R 跨距内、不跨越指定边界的上下文 token 构成一个观测:

                    共现(w₂)   不共现(¬w₂)   行合计
    节点词(w₁)         O₁₁         R-O₁₁         R
    非节点词(¬w₁)      C-O₁₁       N-R-C+O₁₁   N-R
    列合计              C            N-C           N

其中:
    O = O₁₁   : center 为节点词且 context 为搭配词的位置对数
    R         : center 为节点词的合法上下文位置对数
    C         : context 为搭配词的合法位置对数
    N         : 全部合法有向位置对数

期望频次(零假设:节点词与搭配词独立):
    E = R · C / N

各统计量文献来源:
    MI       = log₂(O/E)              Church & Hanks (1990, CL)
    MI3      = log₂(O³/E)             Hofland & Johansson (1982)
    T-score  = (O - E) / sqrt(O)      Church & Hanks (1990, CL); 等价于
                                       Dunning (1993) 的简化形式
    LogDice  = 14 + log₂(2O/(R+C))    Rychlý (2008, RASLAN)
    Z-score  = (O - E) / sigma_hyp    Dunning (1993, CL eq.7),
              sigma_hyp = sqrt(E · (N-R)/N · (N-C)/(N-1))
              (超几何分布精确方差)
    G²/LL    = 2 · Σ O_ij · ln(O_ij / E_ij)   Dunning (1993, CL eq.8),
              对低频搭配更稳健;阈值 G² ≥ 3.84 对应 p < 0.05 (df=1)
    Delta-P₁ = P(w₂|w₁) - P(w₂|¬w₁)  Ellis (2006, Biber 简化的方向搭配强度)
    Delta-P₂ = P(w₁|w₂) - P(w₁|¬w₂)

设计选择与已知局限
------------------
1. 跨距按"词位"度量(token position),而非字符距离;语料应先经分词
2. 默认在句子边界处切断；文件边界无论用户是否允许跨句都必须切断
3. MI 是关联强度而非显著性检验。MI ≥ 3 仅是 Church & Hanks (1990)
   的经验性强关联阈值，不应解读为 p < 0.05
4. `continuityCorrection` 仅保留为旧调用兼容参数。Yates 修正针对
   Pearson χ²，不应作用于 G²；本引擎始终返回未修正的 G²
5. 当 O = 0 时,该搭配词不出现在结果中(已过滤);当 O > 0 但 E = 0
   (极端稀有的搭配词全频次出现在节点词跨距内),MI / G² 记为 +inf,
   由调用方决定如何呈现

References
----------
    Church, K. W., & Hanks, P. (1990). Word association norms,
        mutual information, and lexicography. CL.
    Dunning, T. (1993). Accurate methods for the statistics of
        surprise and coincidence. CL.
    Ellis, N. C. (2006). Lexical acquisition in the typological
        pattern primings of L2. SSLA.
    Hofland, K., & Johansson, S. (1982). Word frequencies in
        British and American English.
    Mann, H. B. (2012). On the utility of the continuity correction:
        A re-examination of the evidence. (Cited via agronomy
        literature; for Z-score the correction is generally
        discouraged.)
    Rychlý, P. (2008). A lexicographer-friendly association score.
        RASLAN.
    Yates, F. (1934). Contingency tables involving small numbers
        and the χ² test. Supplement to the Journal of the Royal
        Statistical Society.
    Barry, C. (2018). LogDice — A stable association measure for
        collocation extraction (Working notes).
    Gablasova, D., Brezina, V., & McEnery, T. (2017). Collocations
        in corpus linguistics: A decade of research. CL.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.core.utils import logger


# ---------------------------------------------------------------------------
# 列联表数据结构
# ---------------------------------------------------------------------------
@dataclass
class ContingencyTable:
    """2×2 共现列联表(Church & Hanks 1990)

    Attributes:
        O:  节点词-center 与搭配词-context 共现的位置对数
        R:  节点词-center 的合法上下文位置对数
        C:  搭配词作为 context 的合法位置对数
        N:  全部合法有向 center-context 位置对数
    """

    O: int = 0
    R: int = 0
    C: int = 0
    N: int = 0

    def __post_init__(self) -> None:
        """校验列联表边际约束，拒绝通过截断负数掩盖抽样总体错误。"""
        if min(self.O, self.R, self.C, self.N) < 0:
            raise ValueError("列联表频数不能为负数")
        if self.O > self.R or self.O > self.C:
            raise ValueError("共现频数 O 不能超过行或列边际频数")
        if self.R > self.N or self.C > self.N:
            raise ValueError("行或列边际频数不能超过总观测数 N")
        if self.N - self.R - self.C + self.O < 0:
            raise ValueError("列联表的 O22 单元格不能为负数")

    @property
    def E(self) -> float:
        """期望频次(零假设下):
        E = R · C / N
        """
        if self.N <= 0:
            return 0.0
        return (self.R * self.C) / self.N

    @property
    def O11(self) -> int:
        return self.O

    @property
    def O12(self) -> int:
        """节点词跨距内非搭配词(同一行的其他列)"""
        return self.R - self.O

    @property
    def O21(self) -> int:
        """非节点词位置上的搭配词"""
        return self.C - self.O

    @property
    def O22(self) -> int:
        """非节点词位置上的非搭配词"""
        return self.N - self.R - self.C + self.O


# ---------------------------------------------------------------------------
# 搭配强度统计量
# ---------------------------------------------------------------------------
@dataclass
class CollocateEntry:
    """单个搭配词条目(含全部统计量 + 元数据)"""

    collocate: str = ""  # 搭配词
    freq: int = 0  # 共现频次 O

    # 主要强度(FR-CLB-002~007)
    mi: float = 0.0  # Mutual Information
    mi3: float = 0.0  # MI³(Hofland & Johansson 1982)
    tScore: float = 0.0  # T-score(Church & Hanks 1990)
    logDice: float = 0.0  # LogDice(Rychlý 2008)
    zScore: float = 0.0  # Z-score(Dunning 1993, 超几何近似)
    logLikelihood: float = 0.0  # G² / Log-Likelihood ratio (Dunning 1993)
    deltaP1: float = 0.0  # ΔP₁: w₁ → w₂ 方向性
    deltaP2: float = 0.0  # ΔP₂: w₂ → w₁ 方向性

    # 元数据(用于学术报告)
    collocateFreq: int = 0  # C(搭配词作为 context 的位置对数)
    corpusFreq: int = 0  # 搭配词在原语料中的 token 频次
    expectedFreq: float = 0.0  # E = R·C/N(零假设期望)
    meetsMiThreshold: bool = False  # MI 达到经验强关联阈值,不是 p 值


@dataclass
class PositionStat:
    """跨距内某位置的共现统计(FR-CLB-009)"""

    position: int = 0  # 负=左(L),正=右(R),0=节点词
    freq: int = 0  # 该位置上的总共现频次(所有搭配词累计)

    def label(self) -> str:
        if self.position < 0:
            return f"L{abs(self.position)}"
        if self.position > 0:
            return f"R{self.position}"
        return "Node"


@dataclass
class CollocationResult:
    """搭配分析完整结果"""

    nodeWord: str = ""  # 节点词(原始大小写)
    nodeKey: str = ""  # 用于匹配的归一化形式
    nodeFreq: int = 0  # 节点词在原语料中的 token 频次
    leftSpan: int = 5
    rightSpan: int = 5
    totalTokens: int = 0  # 原语料 token 总数
    uniqueTypes: int = 0  # V(类型数)
    contextOpportunityCount: int = 0  # 有效 center-context 位置对总数 N
    nodeOpportunityCount: int = 0  # center 为节点词的位置对数 R

    collocates: List[CollocateEntry] = field(default_factory=list)
    positionDistribution: Dict[int, Counter] = field(default_factory=dict)
    networkEdges: List[Tuple[str, str, float]] = field(default_factory=list)

    # 学术元数据
    strongAssociationCount: int = 0  # MI 达到经验强关联阈值的数量
    miThreshold: float = 3.0  # MI 经验强关联阈值(Church & Hanks 1990)
    continuityCorrection: bool = False  # 实际是否应用连续性修正(始终 False)
    elapsedSeconds: float = 0.0


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------
class CollocationEngine:
    """搭配分析引擎

    全部统计量严格基于列联表(ContingencyTable)推导,
    符合 Church & Hanks (1990) 与 Dunning (1993) 的标准定义。

    用法:
        engine = CollocationEngine()
        result = engine.analyze(
            tokens=tokens,
            nodeWord="学习",
            leftSpan=5,
            rightSpan=5,
            minFreq=2,
            topN=100,
            miThreshold=3.0,
            continuityCorrection=False,
        )
    """

    # 标点/空白字符集合(中英文常见标点 + Unicode 通用类别过滤)
    _PUNCT_SET = frozenset(
        "。，、！？：；…—·\"'`()[]【】《》<>「」『』/\\|,.;:!?\"'`()[]{}<>-_+=*&^%$#@~`"
    )

    def __init__(self):
        pass

    # ============================================================
    # 主入口
    # ============================================================
    def analyze(
        self,
        tokens: List[str],
        nodeWord: str,
        leftSpan: int = 5,
        rightSpan: int = 5,
        minFreq: int = 2,
        topN: int = 100,
        caseSensitive: bool = False,
        excludePunct: bool = True,
        miThreshold: float = 3.0,
        continuityCorrection: bool = False,
        crossSentenceBoundary: bool = False,
        sentenceBoundaryIndices: Optional[List[int]] = None,
        documentBoundaryIndices: Optional[List[int]] = None,
    ) -> CollocationResult:
        """搭配分析

        Args:
            tokens: 已分词列表(如 jieba.cut 结果)
            nodeWord: 节点词
            leftSpan: 左跨距(节点词左侧词位数,默认 L5)
            rightSpan: 右跨距(默认 R5)
            minFreq: 最低共现频次(过滤噪声)
            topN: 返回前 N 个搭配词(按 MI 降序,0 表示不限)
            caseSensitive: 是否区分大小写(英文场景)
            excludePunct: 是否过滤纯标点搭配词
            miThreshold: MI 强关联经验阈值(默认 3.0)
            continuityCorrection: 旧版兼容参数；不会应用到 G²
            crossSentenceBoundary: P1-2 修复 — 是否允许跨句边界取搭配词。
                学术惯例默认 False:搭配关系应受句子边界约束
                (Sinclair 1991, Stubbs 1995)。
            sentenceBoundaryIndices: P1-2 修复 — 句子边界索引列表,
                即「该位置的 token 之前有一个句子结束」。
                若为 None 则按默认行为(全语料内统计,不切断)。
            documentBoundaryIndices: 文件边界索引列表。无论是否允许跨句，
                这些边界都会切断跨距。

        Returns:
            CollocationResult
        """
        import time as _time

        startTime = _time.time()

        if continuityCorrection:
            logger.warning(
                "[CollocationEngine] Yates 修正不适用于 G²，"
                "continuityCorrection 已忽略"
            )

        # 0) 输入校验
        nodeWord = (nodeWord or "").strip()
        if not nodeWord:
            logger.warning("[CollocationEngine] 节点词为空,返回空结果")
            return CollocationResult()

        # 1) 大小写归一化
        if caseSensitive:
            nodeKey = nodeWord
            normalizedTokens = list(tokens)
        else:
            nodeKey = nodeWord.lower()
            normalizedTokens = [t.lower() for t in tokens]

        N = len(normalizedTokens)
        if N == 0:
            logger.warning("[CollocationEngine] 语料为空,返回空结果")
            return CollocationResult(nodeWord=nodeWord, nodeKey=nodeKey)

        # boundarySet[k] 表示「位置 k 的 token 之前存在边界」。
        # 文件边界始终生效；句子边界受用户开关控制。
        boundarySet = {
            idx
            for idx in (documentBoundaryIndices or [])
            if 0 < idx < N
        }
        if not crossSentenceBoundary:
            boundarySet.update(
                idx
                for idx in (sentenceBoundaryIndices or [])
                if 0 < idx < N
            )

        # 2) 全局频次统计
        totalFreq = Counter(normalizedTokens)
        nodeFreq = totalFreq.get(nodeKey, 0)  # R
        V = len(totalFreq)

        if nodeFreq == 0:
            logger.warning(
                f"[CollocationEngine] 节点词「{nodeWord}」"
                f"(key={nodeKey})未出现在语料中"
            )
            return CollocationResult(
                nodeWord=nodeWord,
                nodeKey=nodeKey,
                nodeFreq=0,
                totalTokens=N,
                uniqueTypes=V,
                miThreshold=miThreshold,
                continuityCorrection=False,
                elapsedSeconds=_time.time() - startTime,
            )

        # 3) 在同一抽样总体上统计 2×2 列联表边际。
        # 抽样单位是合法的有向 center-context 位置对。这保证
        # O <= R, O <= C，即使节点窗口重叠也不会产生负单元格。
        collocateFreq: Counter = Counter()
        contextFreq: Counter = Counter()
        positionFreq: Dict[int, Counter] = defaultdict(Counter)
        contextOpportunityCount = 0
        nodeOpportunityCount = 0

        for i, tok in enumerate(normalizedTokens):
            isNode = tok == nodeKey
            for d in range(1, leftSpan + 1):
                j = i - d
                if j < 0:
                    break
                # 从 j 向右到 i 会穿过“j+1 之前”的边界。
                if (j + 1) in boundarySet:
                    break
                w = normalizedTokens[j]
                if excludePunct and self._isPunct(w):
                    continue
                contextOpportunityCount += 1
                contextFreq[w] += 1
                if isNode:
                    nodeOpportunityCount += 1
                    collocateFreq[w] += 1
                    positionFreq[-d][w] += 1
            for d in range(1, rightSpan + 1):
                j = i + d
                if j >= N:
                    break
                # 从 i 向右到 j 会穿过“j 之前”的边界。
                if j in boundarySet:
                    break
                w = normalizedTokens[j]
                if excludePunct and self._isPunct(w):
                    continue
                contextOpportunityCount += 1
                contextFreq[w] += 1
                if isNode:
                    nodeOpportunityCount += 1
                    collocateFreq[w] += 1
                    positionFreq[d][w] += 1

        # 4) 构造列联表 + 计算统计量
        entries: List[CollocateEntry] = []
        for w, O in collocateFreq.items():
            if O < minFreq:
                continue
            C = contextFreq.get(w, 0)
            if C == 0:
                continue
            table = ContingencyTable(
                O=O,
                R=nodeOpportunityCount,
                C=C,
                N=contextOpportunityCount,
            )
            entry = self._computeAll(table, miThreshold, continuityCorrection)
            entry.collocate = w
            entry.corpusFreq = totalFreq.get(w, 0)
            entries.append(entry)

        # 5) 排序:MI 降序(主排序);同 MI 按 freq 降序(辅助,保证稳定)
        entries.sort(key=lambda e: (e.mi, e.freq), reverse=True)
        if topN > 0:
            entries = entries[:topN]

        # 6) MI 达到经验强关联阈值的搭配数量
        strongAssociationCount = sum(1 for e in entries if e.meetsMiThreshold)

        # 7) 网络图边:仅保留 MI > 0 且达到强关联阈值的搭配
        networkEdges = [
            (nodeWord, e.collocate, round(e.mi, 4))
            for e in entries
            if e.mi > 0 and e.meetsMiThreshold
        ]

        return CollocationResult(
            nodeWord=nodeWord,
            nodeKey=nodeKey,
            nodeFreq=nodeFreq,
            leftSpan=leftSpan,
            rightSpan=rightSpan,
            totalTokens=N,
            uniqueTypes=V,
            contextOpportunityCount=contextOpportunityCount,
            nodeOpportunityCount=nodeOpportunityCount,
            collocates=entries,
            positionDistribution={pos: cnt for pos, cnt in positionFreq.items()},
            networkEdges=networkEdges,
            strongAssociationCount=strongAssociationCount,
            miThreshold=miThreshold,
            continuityCorrection=False,
            elapsedSeconds=_time.time() - startTime,
        )

    # ============================================================
    # 列联表 → 全部统计量
    # ============================================================
    def _computeAll(
        self,
        t: ContingencyTable,
        miThreshold: float,
        useCorrection: bool,
    ) -> CollocateEntry:
        """基于列联表 t 计算所有统计量

        所有公式严格按列联表语义实现,与 AntConc 4.x 系列兼容。
        """
        # 仅保留参数以兼容旧调用；Yates 修正不适用于 G²。
        _ = useCorrection
        O, R, C, N = t.O, t.R, t.C, t.N
        E = t.E

        # ---- MI = log₂(O/E) ----
        if O > 0 and E > 0:
            mi = math.log2(O / E)
        elif O > 0 and E == 0:
            # 全频次共现(几乎不可能):MI 视为 +inf 的工程近似
            mi = float("inf")
        else:
            mi = 0.0

        # ---- MI3 = log₂(O³/E) = 3·log₂(O) - log₂(E) ----
        # (Hofland & Johansson 1982)
        if O > 0 and E > 0:
            mi3 = 3.0 * math.log2(O) - math.log2(E)
        else:
            mi3 = 0.0

        # ---- T-score = (O - E) / sqrt(O) ----
        # (Church & Hanks 1990; 当 O=0 时 T=0)
        if O > 0:
            tScore = (O - E) / math.sqrt(O)
        else:
            tScore = 0.0

        # ---- LogDice = 14 + log₂(2·O/(R+C)) ----
        # (Rychlý 2008)
        denom = R + C
        if O > 0 and denom > 0:
            logDice = 14.0 + math.log2((2.0 * O) / denom)
        else:
            logDice = 0.0
        # 工程截断:[0, 14];理论上 LogDice ∈ [0, 14]
        logDice = max(0.0, min(14.0, logDice))

        # ---- Z-score = (O - E) / sqrt(V_O) ----
        # 使用超几何分布方差(Dunning 1993, CL Vol.19 No.1, eq.7):
        #   V_O = E · (N - R) / N · (N - C) / (N - 1)
        # 该方差是超几何分布在零假设下的精确方差,优于 Barry (2018) 的简化形式。
        # Yates 修正不应用于 Z-score。
        if N > 1:
            var = E * (N - R) / N * (N - C) / (N - 1)
            sigma = math.sqrt(max(var, 1e-12))
            zScore = (O - E) / sigma if sigma > 0 else 0.0
        else:
            zScore = 0.0

        # ---- Log-Likelihood Ratio (G² / LL) ----
        # Dunning (1993) 推荐用于低频搭配的显著性检验(优于卡方):
        #   G² = 2 · Σ O_ij · ln(O_ij / E_ij)
        # 四格: O11=O, O12=R-O, O21=C-O, O22=N-R-C+O
        # 对 O_ij = 0 的格子,贡献为 0(0·ln(0/E)=0,工程实现按 0 处理)。
        # 本引擎不对 G² 应用 Yates 修正。
        logLikelihood = 0.0
        if N > 0 and R > 0 and C > 0:
            o11 = O
            o12 = R - O
            o21 = C - O
            o22 = N - R - C + O
            cells = [
                (o11, R * C / N),
                (o12, R * (N - C) / N),
                (o21, (N - R) * C / N),
                (o22, (N - R) * (N - C) / N),
            ]
            for observed, expected in cells:
                if observed <= 0:
                    continue
                if expected <= 0:
                    # 期望为 0 但实际 > 0:LL → +inf
                    logLikelihood = float("inf")
                    break
                logLikelihood += 2.0 * observed * math.log(observed / expected)

        # ---- Delta-P₁ = P(w₂|w₁) - P(w₂|¬w₁) ----
        # (Ellis 2006;Gablasova 2017 推荐作为方向搭配强度)
        # 分母:R(节点词出现位置数);条件空间为节点词出现位置上的搭配词分布
        if R > 0:
            pW2GivenW1 = O / R
        else:
            pW2GivenW1 = 0.0
        if (N - R) > 0:
            pW2GivenNotW1 = (C - O) / (N - R)
        else:
            pW2GivenNotW1 = 0.0
        deltaP1 = pW2GivenW1 - pW2GivenNotW1

        # ---- Delta-P₂ = P(w₁|w₂) - P(w₁|¬w₂) ----
        if C > 0:
            pW1GivenW2 = O / C
        else:
            pW1GivenW2 = 0.0
        if (N - C) > 0:
            pW1GivenNotW2 = (R - O) / (N - C)
        else:
            pW1GivenNotW2 = 0.0
        deltaP2 = pW1GivenW2 - pW1GivenNotW2

        return CollocateEntry(
            collocate="",  # 由调用方填充
            freq=O,
            mi=round(mi, 4) if math.isfinite(mi) else mi,
            mi3=round(mi3, 4),
            tScore=round(tScore, 4),
            logDice=round(logDice, 4),
            zScore=round(zScore, 4),
            logLikelihood=(
                round(logLikelihood, 4)
                if math.isfinite(logLikelihood)
                else logLikelihood
            ),
            deltaP1=round(deltaP1, 4),
            deltaP2=round(deltaP2, 4),
            collocateFreq=C,
            expectedFreq=round(E, 4),
            meetsMiThreshold=(math.isfinite(mi) and mi >= miThreshold),
        )

    # ============================================================
    # 工具:标点判断
    # ============================================================
    @classmethod
    def _isPunct(cls, token: str) -> bool:
        """判断 token 是否为纯标点/空白

        使用显式字符集合 + Unicode 通用类别 P*/Z* 双重判定,
        比单一字符集更稳健(覆盖罕见标点)。
        """
        if not token:
            return True
        for ch in token:
            if ch.isspace():
                continue
            if ch in cls._PUNCT_SET:
                continue
            # Unicode 通用类别:标点 (P*) 或 分隔符 (Z*)
            cat = _unicodedata_category(ch)
            if cat.startswith("P") or cat.startswith("Z") or cat.startswith("C"):
                continue
            return False
        return True


def _unicodedata_category(ch: str) -> str:
    """安全获取 Unicode 通用类别,避免导入 unicodedata 异常时崩溃"""
    try:
        import unicodedata

        return unicodedata.category(ch) or "Cn"
    except Exception:
        return "Cn"
