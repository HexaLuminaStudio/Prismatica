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

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import networkx as nx

from app.view.widgets.freq_analyzer.freq_engine import TextSegmenter

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from loguru import logger


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
        coMatrix: Dict[Tuple[str, int], int] = defaultdict(int)
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
        """共现扫描 — 滑动窗口双指针 + Counter(O(K · W))

        设计依据(FR-CON-001 共现矩阵,学术规范参考 Church & Hanks 1990):
            1. K = candidateIndices 长度(命中候选集的 token 位置数)
            2. 对每对 (i, j) i<j 且 j - i <= windowSize,在 [i, j] 区间内
               所有候选词两两配对一次,边权 += 1(无向)
            3. 关键优化:对每个 i,用 left/right 双指针维护 [i, i+W] 区间内的
               候选集,右指针单调不回退 → 总时间 O(K · W)
            4. 内层用 Counter 临时累积配对,批量写回 coMatrix,
               避免 dict 频繁访问

        与原实现的差异:
            - 原实现:外层 K,内层双向各扫 K 次 → O(K²)
            - 新实现:外层 K,内层 right 单调推进 → O(K · W)
            - TopK=80、窗口 ±5 时,扫描次数从约 3200 → 400 量级
        """
        if windowSize <= 0 or not tokens or not candidates:
            return
        # 1. 预过滤:只保留命中候选集的位置(已排序)
        candSet = set(candidates.keys())
        candidateIndices: List[int] = [i for i, t in enumerate(tokens) if t in candSet]
        k = len(candidateIndices)
        if k < 2:
            return

        # 2. 双指针主循环
        #    right 始终指向「j-i <= windowSize 的最大 j+1」
        #    left  在新 i 推进时,从左向右单调推进到「j >= i-windowSize」
        #    对每个 i,扫描 [left, right) 区间(排除 i 自身)内的候选 j,
        #    每对 (i, j) 恰好被计入一次
        right = 0
        left = 0
        for i_idx in range(k):
            i_pos = candidateIndices[i_idx]
            wi = tokens[i_pos]

            # 推进 left 到首个 j 满足 j >= i_pos - windowSize
            # (left 单调不回退,所以这也是 O(K + W) 总量)
            while left < i_idx and candidateIndices[left] < i_pos - windowSize:
                left += 1

            # 推进 right 到首个 j 不满足 j - i_pos <= windowSize
            if right < i_idx + 1:
                right = i_idx + 1
            while right < k and candidateIndices[right] - i_pos <= windowSize:
                right += 1

            # 累加 [left, right) 区间内所有 j != i_idx 的配对
            innerCounter: Counter = Counter()
            for j_idx in range(left, right):
                if j_idx == i_idx:
                    continue
                wj = tokens[candidateIndices[j_idx]]
                if wi == wj:
                    continue
                a, b = (wi, wj) if wi < wj else (wj, wi)
                innerCounter[(a, b)] += 1

            # 批量写回 coMatrix
            for pair, cnt in innerCounter.items():
                coMatrix[pair] = coMatrix.get(pair, 0) + cnt

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
