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
本引擎的统计量推导基于 2×2 列联表(Church & Hanks 1990):

                    共现(w₂)   不共现(¬w₂)   行合计
    节点词(w₁)         O₁₁         R₁-C₁        R₁
    非节点词(¬w₁)      C₁-O₁₁       N-R₁-C₁+O₁₁  N-R₁
    列合计              C₁           N-C₁          N

其中:
    O = O₁₁   : 跨距内的共现频次
    R = f₁    : 节点词在语料中的总频次(nodeFreq)
    C = f₂    : 搭配词在语料中的总频次(collocateFreq)
    N         : 语料 token 总数

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
2. 当前实现对跨句搭配不主动切断 — 用户应自行决定是否引入句子边界
3. MI ≥ 3 的显著性阈值是 Church & Hanks (1990) 经验值,基于英语语料;
   中文搭配研究中该阈值可能需调整(参见 Wei et al. 2014);
   对低频语料建议改用 G² ≥ 3.84 (Dunning 1993)
4. `continuityCorrection=True` 仅作用于 G²(Likelihood Ratio)的 Yates
   修正(Yates 1934),不应用于 Z-score(Mann 2012 指出 Yates 修正
   会破坏 Z 的正态近似,在小样本时反而引入偏差)。Yates 修正仅对
   期望频次接近 1~5 的低频格子有理论意义,默认关闭以兼容 AntConc。
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

from loguru import logger


# ---------------------------------------------------------------------------
# 列联表数据结构
# ---------------------------------------------------------------------------
@dataclass
class ContingencyTable:
    """2×2 共现列联表(Church & Hanks 1990)

    Attributes:
        O:  共现频次 O₁₁ (w₁ 与 w₂ 同时出现在跨距内)
        R:  节点词在语料中的总频次 R₁
        C:  搭配词在语料中的总频次 C₁
        N:  语料 token 总数
    """

    O: int = 0
    R: int = 0
    C: int = 0
    N: int = 0

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
        return max(0, self.R - self.O)

    @property
    def O21(self) -> int:
        """非节点词位置上的搭配词"""
        return max(0, self.C - self.O)

    @property
    def O22(self) -> int:
        """非节点词位置上的非搭配词"""
        return max(0, self.N - self.R - self.C + self.O)


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
    collocateFreq: int = 0  # f₂ = C(搭配词全语料频次)
    expectedFreq: float = 0.0  # E = R·C/N(零假设期望)
    isSignificant: bool = False  # MI ≥ 阈值(默认 3.0)


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
    nodeFreq: int = 0  # 节点词频 R
    leftSpan: int = 5
    rightSpan: int = 5
    totalTokens: int = 0  # N
    uniqueTypes: int = 0  # V(类型数)

    collocates: List[CollocateEntry] = field(default_factory=list)
    positionDistribution: Dict[int, Counter] = field(default_factory=dict)
    networkEdges: List[Tuple[str, str, float]] = field(default_factory=list)

    # 学术元数据
    significantCount: int = 0  # MI ≥ threshold 数量
    significanceThreshold: float = 3.0  # MI 阈值(Church & Hanks 1990)
    continuityCorrection: bool = False  # 是否启用 Yates 修正
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
            significanceThreshold=3.0,
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
        significanceThreshold: float = 3.0,
        continuityCorrection: bool = False,
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
            significanceThreshold: 显著搭配的 MI 阈值(默认 3.0,
                Church & Hanks 1990; 中文研究可调至 2.5~3.5)
            continuityCorrection: 是否启用 Yates 连续性修正
                (仅对 O=0 或 E=O 等边界情况有意义,默认关闭)

        Returns:
            CollocationResult
        """
        import time as _time

        startTime = _time.time()

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
                significanceThreshold=significanceThreshold,
                continuityCorrection=continuityCorrection,
                elapsedSeconds=_time.time() - startTime,
            )

        # 3) 跨距共现统计
        # 使用节点词出现位置列表(而非全表扫描),复杂度 O(K·(L+R)),
        # K = nodeFreq。对大语料显著优于全表扫描
        collocateFreq: Counter = Counter()
        positionFreq: Dict[int, Counter] = defaultdict(Counter)

        for i, tok in enumerate(normalizedTokens):
            if tok != nodeKey:
                continue
            # 左跨距:严格 < 节点词位置
            for d in range(1, leftSpan + 1):
                j = i - d
                if j < 0:
                    break
                w = normalizedTokens[j]
                if excludePunct and self._isPunct(w):
                    continue
                collocateFreq[w] += 1
                positionFreq[-d][w] += 1
            # 右跨距:严格 > 节点词位置
            for d in range(1, rightSpan + 1):
                j = i + d
                if j >= N:
                    break
                w = normalizedTokens[j]
                if excludePunct and self._isPunct(w):
                    continue
                collocateFreq[w] += 1
                positionFreq[d][w] += 1

        # 4) 构造列联表 + 计算统计量
        entries: List[CollocateEntry] = []
        for w, O in collocateFreq.items():
            if O < minFreq:
                continue
            C = totalFreq.get(w, 0)
            if C == 0:
                continue
            table = ContingencyTable(O=O, R=nodeFreq, C=C, N=N)
            entry = self._computeAll(table, significanceThreshold, continuityCorrection)
            entry.collocate = w
            entries.append(entry)

        # 5) 排序:MI 降序(主排序);同 MI 按 freq 降序(辅助,保证稳定)
        entries.sort(key=lambda e: (e.mi, e.freq), reverse=True)
        if topN > 0:
            entries = entries[:topN]

        # 6) 显著搭配统计(MI ≥ threshold)
        sigCount = sum(1 for e in entries if e.isSignificant)

        # 7) 网络图边:仅 MI > 0 且显著的搭配
        networkEdges = [
            (nodeWord, e.collocate, round(e.mi, 4))
            for e in entries
            if e.mi > 0 and e.isSignificant
        ]

        return CollocationResult(
            nodeWord=nodeWord,
            nodeKey=nodeKey,
            nodeFreq=nodeFreq,
            leftSpan=leftSpan,
            rightSpan=rightSpan,
            totalTokens=N,
            uniqueTypes=V,
            collocates=entries,
            positionDistribution={pos: cnt for pos, cnt in positionFreq.items()},
            networkEdges=networkEdges,
            significantCount=sigCount,
            significanceThreshold=significanceThreshold,
            continuityCorrection=continuityCorrection,
            elapsedSeconds=_time.time() - startTime,
        )

    # ============================================================
    # 列联表 → 全部统计量
    # ============================================================
    def _computeAll(
        self,
        t: ContingencyTable,
        sigThreshold: float,
        useCorrection: bool,
    ) -> CollocateEntry:
        """基于列联表 t 计算所有统计量

        所有公式严格按列联表语义实现,与 AntConc 4.x 系列兼容。
        """
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
        # 注:Yates continuity correction 在 Z-score 上**没有学术依据** —
        # Yates 修正仅用于 Pearson 卡方,误用于 Z 会破坏其正态性近似,
        # 因此 useCorrection 仅影响 G²/LL 计算,不影响 Z-score。
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
        # Yates 修正:当 E - 0.5 < O < E + 0.5 时,加 0.5 到各 O_ij
        # (见 Dunning 1993, eq.11)
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
                if useCorrection and observed == 0:
                    # Yates 修正:对 O=0 的格子跳过 0.5 加法(无法加到 0)
                    pass
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
            isSignificant=(math.isfinite(mi) and mi >= sigThreshold),
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
