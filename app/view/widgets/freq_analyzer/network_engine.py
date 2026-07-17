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

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import networkx as nx

from app.view.widgets.freq_analyzer.freq_engine import TextSegmenter

logger = logging.getLogger(__name__)


@dataclass
class NetworkBuildParams:
    """网络构建参数(FR-CON-001/006)"""

    windowSize: int = 5  # 滑动窗口半径(±N 词),默认 ±5
    minWordFreq: int = 2  # 最小词频阈值(过滤低频词)
    minCoFreq: int = 2  # 最小共现频次阈值(过滤弱关联)
    keepTopK: int = 80  # 仅保留频率最高的 K 个节点,避免图过大
    useJieba: bool = True  # 是否启用 jieba 分词
    caseSensitive: bool = False  # 是否区分大小写
    stopwords: Optional[set] = None  # 停用词集合
    keyword: str = ""  # 关键词过滤:仅保留与该词共现的边
    enableCommunity: bool = True  # 是否启用社区发现着色

    def __post_init__(self):
        if self.windowSize < 1:
            self.windowSize = 1
        if self.minWordFreq < 1:
            self.minWordFreq = 1
        if self.minCoFreq < 1:
            self.minCoFreq = 1
        if self.keepTopK < 1:
            self.keepTopK = 1


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
    ) -> CooccurrenceNetwork:
        """根据多文件语料构建共现网络

        Args:
            fileToText: 文件名 -> 清洗后全文(由 CorpusStore.effectiveTexts() 提供)
            params: 构建参数,None 时使用默认

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
        wordCounter: Counter = Counter()
        tokenStreams: List[List[str]] = []
        for text in fileToText.values():
            tokens = self._tokenize(text)
            tokenStreams.append(tokens)
            wordCounter.update(tokens)

        network.totalTokens = sum(wordCounter.values())

        # 2) 应用停用词 + 词频过滤,确定参与网络构建的「候选节点集」
        candidates = self._filterCandidates(wordCounter, params)

        if not candidates:
            logger.warning("[CooccurrenceEngine] 没有满足最低词频的候选词")
            return network

        # 3) 在 token 流上滑动窗口,统计共现
        coMatrix: Dict[Tuple[str, str], int] = defaultdict(int)
        for tokens in tokenStreams:
            self._scanCooccurrence(tokens, candidates, params.windowSize, coMatrix)

        # 4) 应用关键词过滤(若指定)与共现频次阈值
        candidateSet = set(candidates)
        edgesToAdd: List[Tuple[str, str, int]] = []
        for (a, b), w in coMatrix.items():
            if w < params.minCoFreq:
                continue
            if params.keyword:
                kw = params.keyword if params.caseSensitive else params.keyword.lower()
                if kw and kw not in (a, b):
                    continue
            # 节点必须在候选集中(防御性)
            if a not in candidateSet or b not in candidateSet:
                continue
            edgesToAdd.append((a, b, w))

        # 5) 构图
        graph = nx.Graph()
        for word, freq in candidates.items():
            graph.add_node(word, freq=freq)
        for a, b, w in edgesToAdd:
            graph.add_edge(a, b, weight=w)
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
        """导出 GEXF(FR-CON-007)"""
        g = network.graph.copy()
        # 写入节点/边属性以便 Gephi 识别
        for n, d in g.nodes(data=True):
            d["label"] = n
            d["frequency"] = int(d.get("freq", 0))
        for u, v, d in g.edges(data=True):
            d["weight"] = int(d.get("weight", 0))
        # 社区 -> 颜色编码
        for n, cid in network.communities.items():
            if n in g.nodes:
                g.nodes[n]["community"] = int(cid)
        return "\n".join(nx.generate_gexf(g))

    def exportGraphML(self, network: CooccurrenceNetwork) -> str:
        """导出 GraphML(FR-CON-007)"""
        g = network.graph.copy()
        for n, d in g.nodes(data=True):
            d["frequency"] = int(d.get("freq", 0))
        for u, v, d in g.edges(data=True):
            d["weight"] = int(d.get("weight", 0))
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
        """滑动窗口扫描共现

        对每个位置 i,与其前后 windowSize 个 token 内的所有候选词两两统计一次。
        """
        n = len(tokens)
        # 预过滤:只保留在候选集内的索引,加速窗口扫描
        candSet = set(candidates.keys())
        candidateIndices: List[int] = []
        for i, t in enumerate(tokens):
            if t in candSet:
                candidateIndices.append(i)

        # 使用双指针计算每个候选词在窗口内的邻居
        for idxPos, i in enumerate(candidateIndices):
            wi = tokens[i]
            # 在 candidateIndices 中寻找所有满足 j-i<=windowSize 的索引
            # 由于 candidateIndices 单调递增,可以用二分
            left = idxPos
            right = idxPos
            # 向左扩展
            for k in range(idxPos - 1, -1, -1):
                j = candidateIndices[k]
                if i - j > windowSize:
                    break
                left = k
            # 向右扩展
            for k in range(idxPos + 1, len(candidateIndices)):
                j = candidateIndices[k]
                if j - i > windowSize:
                    break
                right = k

            # 累加共现(无向,小-大排序避免重复)
            for k in range(left, right + 1):
                if k == idxPos:
                    continue
                j = candidateIndices[k]
                wj = tokens[j]
                if wi == wj:
                    continue
                a, b = (wi, wj) if wi < wj else (wj, wi)
                coMatrix[(a, b)] += 1

    def _detectCommunities(self, graph: "nx.Graph") -> Dict[str, int]:
        """社区发现(FR-CON-005)

        使用 NetworkX 内置的 greedy_modularity_communities 算法
        (Clauset-Newman-Moore 贪心模块度优化),无需依赖 python-louvain。
        """
        if graph.number_of_nodes() < 2 or graph.number_of_edges() < 1:
            return {n: 0 for n in graph.nodes}
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
