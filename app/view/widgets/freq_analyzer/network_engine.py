# coding: utf-8
"""
词语共现网络分析引擎

需求文档:
    test/6D-CorpusClient_需求文档_v3.md §2.5.2 共现网络图

功能:
    - FR-CON-001 共现矩阵构建:基于滑动窗口(±N 词)
    - FR-CON-002 网络图渲染:力导向布局(Fruchterman-Reingold)
    - FR-CON-004 节点大小映射词频,边粗细映射共现频次
    - FR-CON-005 社区发现着色:NetworkX 内置 greedy modularity 算法
    - FR-CON-006 筛选与过滤:按最小共现频次、最小词频过滤

设计:
    - 与 FreqAnalyzerWidget / ConcordanceWidget 共用 TextSegmenter
    - 复用 CorpusStore 的 effectiveTexts(),保证清洗/分词结果一致
    - 关键词优先(可选):仅保留包含关键词的共现关系

学术依据与已知局限
------------------
本引擎采用**绝对共现频次**作为边权重(weight = O_{ij}),
符合 AntConc 与 Sketch Engine 的默认行为。
更严格的可选权重方案:
    - PMI (Pointwise Mutual Information):
        weight = log₂ P(w_i, w_j) / (P(w_i) · P(w_j))
    - NPMI (Bouma 2009):
        归一化到 [-1, +1],更利于跨语料比较
    - Jaccard:
        weight = |w_i ∩ w_j| / |w_i ∪ w_j|
    - LogDice:
        weight = 14 + log₂(2 · O / (f_i + f_j))
当前默认使用频次权重,与 Gephi 等工具直接兼容;如需上述
归一化权重,可在调用方对 network.graph 重写 edge 属性。

社区发现采用 Clauset-Newman-Moore 贪心模块度优化
(NetworkX greedy_modularity_communities),复杂度近似 O(n log² n),
适用于 n < 10⁴ 的网络;更大网络建议改用 Louvain(Blondel et al. 2008)。

References:
    Fruchterman, T. M. J., & Reingold, E. M. (1991). Graph drawing
        by force-directed placement. Software: Practice and Experience.
    Clauset, A., Newman, M. E. J., & Moore, C. (2004). Finding
        community structure in very large networks. Phys. Rev. E.
    Bouma, G. (2009). Normalized (pointwise) mutual information in
        collocation extraction. GSCL.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

import networkx as nx

from app.view.widgets.freq_analyzer.freq_engine import TextSegmenter


# =====================================================================
# 边权归一化方案(FR-CON-008 P0-fix 2026-07-20)
# =====================================================================
# 学术依据:
#   - 绝对共现频次(Frequency)受高频词偏置严重,功能词会形成 hub
#   - PMI (Church & Hanks 1990): log₂ P(x,y) / (P(x)·P(y))
#   - NPMI (Bouma 2009): 归一化到 [-1, +1],更利于跨语料比较
#   - Dice 系数: 2·O / (f_i + f_j),对称
#   - LogDice (Rychlý 2008): 14 + log₂(2·O / (f_i + f_j)),
#     值域近似 [0, 14],符合功能词筛除经验值
#   - Jaccard: O / (f_i + f_j - O),对称
# =====================================================================
class EdgeWeight(Enum):
    """边权计算方法"""

    FREQUENCY = "frequency"  # 绝对共现频次 O_{ij}(默认)
    PMI = "pmi"  # Pointwise Mutual Information
    NPMI = "npmi"  # Normalized PMI(Bouma 2009)
    DICE = "dice"  # Dice 系数
    LOG_DICE = "log_dice"  # LogDice(Rychlý 2008)
    JACCARD = "jaccard"  # Jaccard 相似系数


# 各方案对应的最小阈值(低于此值视为无效边)
_EDGE_MIN_WEIGHT: Dict[EdgeWeight, float] = {
    EdgeWeight.FREQUENCY: 1.0,
    EdgeWeight.PMI: 0.0,  # PMI 可能为负,理论上 0 即随机水平
    EdgeWeight.NPMI: -1.0,
    EdgeWeight.DICE: 0.0,
    EdgeWeight.LOG_DICE: 0.0,
    EdgeWeight.JACCARD: 0.0,
}

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from app.core.utils import logger


@dataclass
class NetworkBuildParams:
    """网络构建参数(FR-CON-001/006/008)"""

    windowSize: int = 5  # 滑动窗口半径(±N 候选词),默认 ±5
    minWordFreq: int = 2  # 最小词频阈值(过滤低频词)
    minCoFreq: int = 2  # 最小共现频次阈值(过滤弱关联)
    keepTopK: int = 80  # 仅保留频率最高的 K 个节点,避免图过大
    useJieba: bool = True  # 是否启用 jieba 分词
    caseSensitive: bool = False  # 是否区分大小写
    stopwords: Optional[set] = None  # 停用词集合
    keyword: str = ""  # 关键词过滤:仅保留与该词共现的边
    enableCommunity: bool = True  # 是否启用社区发现着色
    # FR-CON-010 P0-fix 2026-07-20:统一过滤模式(关键词 + 词性组合合一)
    # 支持以下语法:
    #   "学习"              → 仅保留含"学习"的边(向后兼容)
    #   "V 都 V 了"          → 仅保留符合该结构的边(向后兼容)
    #   "学习:V 都 V 了"     → "学习"出现在 "V 都 V 了" 结构内
    #   "学习,工作"          → 含"学习"或"工作"任一(OR)
    #   "学习:N 的 N"        → "学习"在 "N 的 N" 结构内
    # 空字符串表示不过滤
    filterExpr: str = ""
    # FR-CON-008 P0-fix 2026-07-20:边权归一化方案
    # - FREQUENCY(默认):O_{ij},与 Gephi/AntConc 兼容
    # - PMI/NPMI/DICE/LOG_DICE/JACCARD:归一化权重,降低高频词偏置
    edgeWeight: EdgeWeight = EdgeWeight.FREQUENCY

    def __post_init__(self):
        if self.windowSize < 1:
            self.windowSize = 1
        if self.minWordFreq < 1:
            self.minWordFreq = 1
        if self.minCoFreq < 1:
            self.minCoFreq = 1
        if self.keepTopK < 1:
            self.keepTopK = 1
        # 兼容传入字符串的 edgeWeight
        if isinstance(self.edgeWeight, str):
            try:
                self.edgeWeight = EdgeWeight(self.edgeWeight)
            except ValueError:
                logger.warning(
                    f"[NetworkBuildParams] 未知 edgeWeight={self.edgeWeight},"
                    "回退到 FREQUENCY"
                )
                self.edgeWeight = EdgeWeight.FREQUENCY


@dataclass
class CooccurrenceEdge:
    """单条共现边(用于导出 GEXF/GraphML)"""

    source: str
    target: str
    weight: int


@dataclass
class CooccurrenceNetwork:
    """共现网络图构建结果

    Fields:
        graph:       NetworkX Graph (无向图)
        nodeFreq:    节点 -> 词频
        communities: 节点 -> 社区 id(int, 0-based),仅在 enableCommunity 时填充
        params:      实际使用的构建参数
        totalTokens: 参与统计的总 token 数
    """

    graph: "nx.Graph" = field(default_factory=nx.Graph)
    nodeFreq: Dict[str, int] = field(default_factory=dict)
    communities: Dict[str, int] = field(default_factory=dict)
    params: NetworkBuildParams = field(default_factory=NetworkBuildParams)
    totalTokens: int = 0

    @property
    def nodeCount(self) -> int:
        return self.graph.number_of_nodes()

    @property
    def edgeCount(self) -> int:
        return self.graph.number_of_edges()

    def edges(self) -> List[CooccurrenceEdge]:
        """返回所有边(按权重降序),供导出使用"""
        return [
            CooccurrenceEdge(source=u, target=v, weight=int(d.get("weight", 0)))
            for u, v, d in self.graph.edges(data=True)
        ]


class CooccurrenceEngine:
    """词语共现网络分析引擎"""

    def __init__(
        self,
        useJieba: bool = True,
        caseSensitive: bool = False,
        tokenCache=None,
    ):
        self.useJieba = useJieba
        self.caseSensitive = caseSensitive
        self.segmenter = TextSegmenter(tokenCache=tokenCache)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def build(
        self,
        fileToText: Dict[str, str],
        params: Optional[NetworkBuildParams] = None,
        progressCallback: Optional[Callable[[str], None]] = None,
    ) -> CooccurrenceNetwork:
        """根据多文件语料构建共现网络

        Args:
            fileToText: 文件名 -> 清洗后全文(由 CorpusStore.effectiveTexts() 提供)
            params: 构建参数,None 时使用默认
            progressCallback: P1-3 修复 — 阶段回调函数。接收阶段名(str),
                UI/Worker 可在主线程或 worker 线程中调用以更新进度提示。

        Returns:
            CooccurrenceNetwork
        """
        if params is None:
            params = NetworkBuildParams(
                useJieba=self.useJieba, caseSensitive=self.caseSensitive
            )
        else:
            params.useJieba = params.useJieba and self.useJieba

        network = CooccurrenceNetwork(params=params)

        # 1) 分词 + 词频统计
        if progressCallback:
            progressCallback("分词与统计词频...")
        wordCounter: Counter = Counter()
        tokenStreams: List[List[str]] = []
        totalFiles = len(fileToText)
        for fileIdx, text in enumerate(fileToText.values(), start=1):
            tokens = self._tokenize(text)
            tokenStreams.append(tokens)
            wordCounter.update(tokens)
            if (
                progressCallback
                and totalFiles > 0
                and fileIdx % max(1, totalFiles // 10) == 0
            ):
                pct = int(40 * fileIdx / totalFiles)
                progressCallback(f"分词 {fileIdx}/{totalFiles} ({pct}%)")

        network.totalTokens = sum(wordCounter.values())

        # 2) 应用停用词 + 词频过滤,确定参与网络构建的「候选节点集」
        if progressCallback:
            progressCallback("筛选候选节点...")
        candidates = self._filterCandidates(wordCounter, params)

        if not candidates:
            logger.warning("[CooccurrenceEngine] 没有满足最低词频的候选词")
            return network

        # 3) 在 token 流上滑动窗口,统计共现
        if progressCallback:
            progressCallback(f"扫描共现窗口 (候选词 {len(candidates)} 个)...")
        coMatrix: Dict[Tuple[str, str], int] = defaultdict(int)
        totalStreams = len(tokenStreams)
        for streamIdx, tokens in enumerate(tokenStreams, start=1):
            self._scanCooccurrence(tokens, candidates, params.windowSize, coMatrix)
            if (
                progressCallback
                and totalStreams > 0
                and streamIdx % max(1, totalStreams // 5) == 0
            ):
                pct = 50 + int(35 * streamIdx / totalStreams)
                progressCallback(f"共现扫描 {streamIdx}/{totalStreams} ({pct}%)")

        # 3.5) FR-CON-010 P0-fix 2026-07-20:统一过滤模式(关键词 + 词性结构)
        # 解析 filterExpr,对每个匹配区间内的候选词两两配对,加入共现矩阵
        unifiedFilter = None
        try:
            from app.view.widgets.freq_analyzer.pos_pattern import (
                NetworkFilter,
                tokenizeForPos,
            )

            unifiedFilter = NetworkFilter(params.filterExpr)
        except Exception as e:
            logger.warning(f"[CooccurrenceEngine] filterExpr 解析失败: {e}")

        if unifiedFilter and not unifiedFilter.isEmpty():
            if progressCallback:
                progressCallback(f"应用过滤模式: {params.filterExpr}")
            # 处理所有 POS 结构子句(对每个子句执行一次扫描)
            patternCount = 0
            for clause in unifiedFilter.clauses:
                if not clause.hasPosPattern:
                    continue
                pattern = clause.posPattern
                keywordsInClause = clause.keywords  # 可能为空(纯 POS 子句)
                # 是否需要限定关键词(combined 子句)
                needKw = bool(keywordsInClause)
                matchedKwByEdge: Dict[Tuple[str, str], int] = defaultdict(int)
                for streamIdx, text in enumerate(fileToText.values()):
                    posTokens = tokenizeForPos(text, useJieba=params.useJieba)
                    if not posTokens:
                        continue
                    matches = pattern.match(posTokens)
                    for match in matches:
                        # 在匹配区间内的所有候选词(去重)
                        wordsInMatch = [
                            w for w, _ in match.matched if w in set(candidates.keys())
                        ]
                        seen = set()
                        uniqueWords = []
                        for w in wordsInMatch:
                            if w not in seen:
                                seen.add(w)
                                uniqueWords.append(w)
                        if not uniqueWords:
                            continue
                        # 若子句带关键词,要求至少一个候选词命中关键词
                        if needKw:
                            kwSet = {
                                kw if params.caseSensitive else kw.lower()
                                for kw in keywordsInClause
                            }
                            if not any(w in kwSet for w in uniqueWords):
                                continue
                        # 两两配对
                        for i in range(len(uniqueWords)):
                            for j in range(i + 1, len(uniqueWords)):
                                a, b = uniqueWords[i], uniqueWords[j]
                                key = (a, b) if a < b else (b, a)
                                coMatrix[key] += 1
                                matchedKwByEdge[key] += 1
                        patternCount += 1
                logger.info(
                    f"[CooccurrenceEngine] 子句 {clause.raw!r} 匹配 {patternCount} 次"
                )

        # 4) 应用关键词过滤(若指定)与共现频次阈值
        candidateSet = set(candidates)
        edgesToAdd: List[Tuple[str, str, int]] = []
        # 从统一过滤中提取纯关键词部分(向后兼容 keyword 字段)
        if unifiedFilter and not unifiedFilter.isEmpty():
            filterKeywords = unifiedFilter.keywords()
            filterKeywordsLower = [
                k if params.caseSensitive else k.lower() for k in filterKeywords
            ]
        else:
            filterKeywords = []
            filterKeywordsLower = []
        for (a, b), w in coMatrix.items():
            if w < params.minCoFreq:
                continue
            if filterKeywordsLower:
                # 任一关键词命中(a 或 b 任一)即保留
                if not any(
                    kw in (a, b) or kw in (a.lower(), b.lower())
                    for kw in filterKeywordsLower
                ):
                    continue
            elif params.keyword:
                # 向后兼容旧版 keyword 字段
                kw = params.keyword if params.caseSensitive else params.keyword.lower()
                if kw and kw not in (a, b) and kw not in (a.lower(), b.lower()):
                    continue
            # 节点必须在候选集中(防御性)
            if a not in candidateSet or b not in candidateSet:
                continue
            edgesToAdd.append((a, b, w))

        # 5) 构图(FR-CON-008 P0-fix 2026-07-20:边权归一化)
        graph = nx.Graph()
        for word, freq in candidates.items():
            graph.add_node(word, freq=freq)
        if params.edgeWeight == EdgeWeight.FREQUENCY:
            # 默认:绝对共现频次作为边权
            for a, b, w in edgesToAdd:
                graph.add_edge(a, b, weight=float(w), rawCount=int(w))
        else:
            # 归一化权重(PMI/NPMI/Dice/LogDice/Jaccard)
            normalized = self._normalizeEdgeWeights(
                coMatrix, candidates, params.edgeWeight
            )
            minW = _EDGE_MIN_WEIGHT.get(params.edgeWeight, 0.0)
            for a, b, w in edgesToAdd:
                wNorm = normalized.get((a, b), 0.0)
                if wNorm < minW:
                    continue
                graph.add_edge(a, b, weight=float(wNorm), rawCount=int(w))
        network.graph = graph
        network.nodeFreq = dict(candidates)

        # 6) 社区发现(FR-CON-005)
        if params.enableCommunity and graph.number_of_nodes() > 0:
            network.communities = self._detectCommunities(graph)

        logger.info(
            f"[CooccurrenceEngine] 构建完成: "
            f"节点={graph.number_of_nodes()} 边={graph.number_of_edges()} "
            f"窗口=±{params.windowSize} 最小词频={params.minWordFreq} "
            f"最小共现={params.minCoFreq}"
        )
        return network

    def computeLayout(
        self,
        graph: "nx.Graph",
        seed: int = 42,
        iterations: int = 200,
    ) -> Dict[str, Tuple[float, float]]:
        """计算 Fruchterman-Reingold 力导向布局(FR-CON-002)

        Args:
            graph: 输入图(应带有 weight 属性)
            seed: 随机种子,保证可复现
            iterations: 迭代次数

        Returns:
            {node: (x, y)} 位置字典
        """
        if graph.number_of_nodes() == 0:
            return {}
        try:
            pos = nx.spring_layout(
                graph,
                weight="weight",
                seed=seed,
                iterations=iterations,
                k=None,
            )
        except Exception as e:
            logger.warning(f"[CooccurrenceEngine] spring_layout 失败,改用 random: {e}")
            pos = nx.random_layout(graph, seed=seed)
        # 归一化到 [0, 1]
        if pos:
            xs = [p[0] for p in pos.values()]
            ys = [p[1] for p in pos.values()]
            minX, maxX = min(xs), max(xs)
            minY, maxY = min(ys), max(ys)
            dx = maxX - minX or 1.0
            dy = maxY - minY or 1.0
            pos = {n: ((p[0] - minX) / dx, (p[1] - minY) / dy) for n, p in pos.items()}
        return pos

    def exportGexf(self, network: CooccurrenceNetwork) -> str:
        """导出 GEXF(FR-CON-007)

        FR-CON-008 P0-fix 2026-07-20:
            - weight 字段使用归一化后权重(float),便于 Gephi 按权重着色
            - rawCount 字段保留原始共现频次(int),便于二次分析
        """
        g = network.graph.copy()
        # 写入节点/边属性以便 Gephi 识别
        for n, d in g.nodes(data=True):
            d["label"] = n
            d["frequency"] = int(d.get("freq", 0))
        for u, v, d in g.edges(data=True):
            w = d.get("weight", 0)
            d["weight"] = float(w) if isinstance(w, (int, float)) else 0.0
            d["rawCount"] = int(d.get("rawCount", 0))
        # 社区 -> 颜色编码
        for n, cid in network.communities.items():
            if n in g.nodes:
                g.nodes[n]["community"] = int(cid)
        return "\n".join(nx.generate_gexf(g))

    def exportGraphML(self, network: CooccurrenceNetwork) -> str:
        """导出 GraphML(FR-CON-007)

        FR-CON-008 P0-fix 2026-07-20:
            - 保留原始 weight(rawCount)与归一化 weight 两个字段
            - GraphML 默认类型推断可能将 float 转 double,需显式转换
        """
        g = network.graph.copy()
        for n, d in g.nodes(data=True):
            d["frequency"] = int(d.get("freq", 0))
        for u, v, d in g.edges(data=True):
            w = d.get("weight", 0)
            d["weight"] = float(w) if isinstance(w, (int, float)) else 0.0
            d["rawCount"] = int(d.get("rawCount", 0))
        for n, cid in network.communities.items():
            if n in g.nodes:
                g.nodes[n]["community"] = int(cid)
        from io import BytesIO

        buf = BytesIO()
        nx.write_graphml(g, buf)
        return buf.getvalue().decode("utf-8")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        tokens = self.segmenter.tokenize(text, useJieba=self.useJieba)
        if not self.caseSensitive:
            tokens = [t.lower() for t in tokens]
        return [t for t in tokens if t and t.strip()]

    def _filterCandidates(
        self,
        wordCounter: Counter,
        params: NetworkBuildParams,
    ) -> Dict[str, int]:
        """按停用词 + 词频阈值 + TopK 过滤,得到参与构图的高频词集合"""
        stopwords = params.stopwords or set()
        filtered = {
            w: c
            for w, c in wordCounter.items()
            if c >= params.minWordFreq and w not in stopwords
        }
        if not filtered:
            return {}
        # 仅保留频率最高的 K 个,避免图爆炸
        if len(filtered) > params.keepTopK:
            top = sorted(filtered.items(), key=lambda x: x[1], reverse=True)[
                : params.keepTopK
            ]
            filtered = dict(top)
        return filtered

    def _scanCooccurrence(
        self,
        tokens: List[str],
        candidates: Dict[str, int],
        windowSize: int,
        coMatrix: Dict[Tuple[str, str], int],
    ) -> None:
        """共现扫描 — 按原始 token 距离枚举无向位置对(O(K · W))

        设计依据(FR-CON-001 共现矩阵,学术规范参考 Church & Hanks 1990):

        窗口语义:
            ``windowSize=N`` 表示两个候选词在原始 token 流中的位置差不超过 N。
            停用词、低频词等非候选 token 仍占据窗口位置,不能在筛选后被折叠。

        滑动窗口主循环:
            1. K = candidateIndices 长度(命中候选集的 token 位置数)
            2. 仅枚举 j > i,保证每个实际无向位置对恰好计数一次
            3. 当原始位置差大于 windowSize 时停止向右扫描

        学术依据:
            - Church, K. W., & Hanks, P. (1990). Word association norms,
              mutual information, and lexicography. ACL.
            - Evert, S. (2008). Corpora and collocations. Corpus Linguistics.
        """
        if windowSize <= 0 or not tokens or not candidates:
            return
        # 1. 预过滤:只保留命中候选集的位置(已排序)
        candSet = set(candidates.keys())
        candidateIndices: List[int] = [i for i, t in enumerate(tokens) if t in candSet]
        k = len(candidateIndices)
        if k < 2:
            return

        # 2. 只向右枚举后继候选位置。原始 token 位置有序,超过窗口即可停止。
        for iIdx in range(k - 1):
            iPosition = candidateIndices[iIdx]
            wi = tokens[iPosition]
            for jIdx in range(iIdx + 1, k):
                jPosition = candidateIndices[jIdx]
                if jPosition - iPosition > windowSize:
                    break
                wj = tokens[jPosition]
                # 同词位置对不生成自环,但不同词的位置对只计一次。
                if wi == wj:
                    continue
                a, b = (wi, wj) if wi < wj else (wj, wi)
                coMatrix[(a, b)] = coMatrix.get((a, b), 0) + 1

    def _normalizeEdgeWeights(
        self,
        coMatrix: Dict[Tuple[str, str], int],
        candidates: Dict[str, int],
        method: EdgeWeight,
    ) -> Dict[Tuple[str, str], float]:
        """边权归一化(FR-CON-008 P0-fix 2026-07-20)

        Args:
            coMatrix: 原始共现矩阵 {(a, b): O_{ij}}
            candidates: 候选词及其原始 token 频次(仅用于校验节点存在)
            method: 归一化方案

        Returns:
            {(a, b): normalized_weight}
        """
        result: Dict[Tuple[str, str], float] = {}

        if method == EdgeWeight.FREQUENCY:
            # 直接返回频次
            return {pair: float(cnt) for pair, cnt in coMatrix.items()}

        # 抽样单位是“候选词无向位置对事件”。对每个词,边际频数是
        # 包含该词的事件数；这使 O<=f_a,f_b,并让 PMI/NPMI/Dice/
        # LogDice/Jaccard 全部使用同一机会空间。
        totalPairEvents = sum(coMatrix.values())
        if totalPairEvents <= 0:
            return {}
        eventMarginals: Counter = Counter()
        for (a, b), eventCount in coMatrix.items():
            eventMarginals[a] += eventCount
            eventMarginals[b] += eventCount

        for (a, b), o_ij in coMatrix.items():
            if a not in candidates or b not in candidates:
                continue
            f_a = eventMarginals.get(a, 0)
            f_b = eventMarginals.get(b, 0)
            if f_a == 0 or f_b == 0 or o_ij == 0:
                continue

            if method == EdgeWeight.PMI:
                # PMI = log₂ P(a,b) / (P(a)·P(b))
                #     = log₂ (O_{ij}/E) / ((f_a/E)·(f_b/E))
                #     = log₂ (O_{ij}·E) / (f_a·f_b)
                denom = f_a * f_b
                if denom <= 0:
                    continue
                w = math.log2((o_ij * totalPairEvents) / denom)
            elif method == EdgeWeight.NPMI:
                # NPMI = PMI / -log₂ P(a,b) (Bouma 2009)
                #     归一化到 [-1, +1]
                p_ab = o_ij / totalPairEvents
                denom = (f_a * f_b) / (totalPairEvents**2)
                if p_ab <= 0 or denom <= 0:
                    continue
                pmi = math.log2(p_ab / denom)
                h_xy = -math.log2(p_ab) if p_ab > 0 else 0.0
                if h_xy <= 0:
                    continue
                w = pmi / h_xy
            elif method == EdgeWeight.DICE:
                # Dice = 2·O_{ij} / (f_a + f_b)
                w = (2.0 * o_ij) / (f_a + f_b)
            elif method == EdgeWeight.LOG_DICE:
                # LogDice = 14 + log₂(2·O_{ij} / (f_a + f_b))
                # (Rychlý 2008,值域近似 [0, 14])
                denom = (f_a + f_b) / 2.0
                if denom <= 0:
                    continue
                w = 14.0 + math.log2(o_ij / denom)
            elif method == EdgeWeight.JACCARD:
                # Jaccard = O_{ij} / (f_a + f_b - O_{ij})
                denom = f_a + f_b - o_ij
                if denom <= 0:
                    continue
                w = o_ij / denom
            else:
                # 未知方法,fallback 到 FREQUENCY
                w = float(o_ij)

            # PMI/LogDice 可能产生 -inf / NaN,跳过
            if math.isnan(w) or math.isinf(w):
                continue
            result[(a, b)] = w

        return result

    def _detectCommunities(self, graph: "nx.Graph") -> Dict[str, int]:
        """社区发现(FR-CON-005)

        使用 NetworkX 内置的 greedy_modularity_communities 算法
        (Clauset-Newman-Moore 贪心模块度优化),无需依赖 python-louvain。

        P0-fix 2026-07-20【大网退化处理】:
            对 n > 5000 的大图,贪心模块度算法接近 O(n log² n),
            实测 5000 节点需数十秒。改进策略:
            - n <= 2000:使用 NetworkX 内置 greedy_modularity(精确)
            - n > 2000:  使用 louvain_communities(若可用,O(n log n))
                          或退化为连通分量(避免阻塞 UI)
        """
        n = graph.number_of_nodes()
        if n < 2 or graph.number_of_edges() < 1:
            return {n: 0 for n in graph.nodes}

        # 大图优先尝试 Louvain(若 python-louvain 可用)
        if n > 2000:
            try:
                from networkx.algorithms.community import louvain_communities

                comms = list(louvain_communities(graph, weight="weight", seed=42))
                logger.info(f"[CooccurrenceEngine] 大图(n={n})使用 Louvain 社区发现")
                result: Dict[str, int] = {}
                for cid, group in enumerate(comms):
                    for nd in group:
                        result[nd] = cid
                return result
            except ImportError:
                logger.warning(
                    f"[CooccurrenceEngine] 大图(n={n})未安装 python-louvain,"
                    "退化为连通分量着色"
                )
                return self._fallbackConnectedComponents(graph)

        # 中小图:使用精确贪心模块度
        try:
            from networkx.algorithms.community import greedy_modularity_communities

            comms = list(greedy_modularity_communities(graph, weight="weight"))
        except Exception as e:
            logger.warning(f"[CooccurrenceEngine] 社区发现失败: {e}")
            return {n: 0 for n in graph.nodes}

        result: Dict[str, int] = {}
        for cid, group in enumerate(comms):
            for n in group:
                result[n] = cid
        return result

    def _fallbackConnectedComponents(self, graph: "nx.Graph") -> Dict[str, int]:
        """社区发现 fallback:连通分量着色

        对大图或无 Louvain 环境,使用连通分量作为社区代理。
        每个连通分量分配一个 cid,确保仍能产生视觉差异。
        """
        result: Dict[str, int] = {}
        for cid, component in enumerate(nx.connected_components(graph)):
            for n in component:
                result[n] = cid
        return result


# 社区调色板(FR-CON-005):颜色循环分配,超过自动取模
COMMUNITY_COLORS: List[str] = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
    "#393b79",
    "#637939",
    "#8c6d31",
    "#843c39",
    "#7b4173",
    "#3182bd",
    "#31a354",
    "#756bb1",
    "#636363",
    "#e6550d",
]


def colorForCommunity(cid: int) -> str:
    """根据社区 id 取颜色"""
    if cid < 0:
        return "#999999"
    return COMMUNITY_COLORS[cid % len(COMMUNITY_COLORS)]
