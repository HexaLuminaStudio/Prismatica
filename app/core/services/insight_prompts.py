# coding: utf-8
"""
AI 解读 Prompt 模板库（PRD-001 REQ-AI-001）

集中存放 3 类分析（词频 / 共现网络 / KWIC）的解读 Prompt，以及
对应的「数据 → 文本」汇总逻辑。

设计原则：
    - 三可原则：可溯源（指明数据来自哪一栏）、可证伪（每个论断附 [数据: ...]）、
                 可拒绝（数据不足时主动声明）。
    - 单类 Prompt 构造 < 50ms（DataFrame 用 head + to_dict，不做序列化）。
    - 强约束：context 长度超过阈值时自动降采样，并在 user_prompt 末尾追加
              「数据已精简到 Top-N」提示。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 风格化 system prompt 片段
# ---------------------------------------------------------------------------
_STYLE_TONE = {
    "学术": (
        "你是中文学术语料语言学专家。请用学术风格写一段 200-300 字的解读，"
        "逻辑严密、用词克制，避免「非常」「十分」等口语化副词。"
    ),
    "通俗": (
        "你是中文语料分析顾问。请用通俗易懂的语言写一段 200-300 字的解读，"
        "面向非专业读者，避免堆砌术语，必要时举一两个具体例子。"
    ),
    "简洁": (
        "你是一名精炼的语料分析助手。请用 150 字以内的简短段落给出最关键的 3 点结论，"
        "保留必要的 [数据: ...] 引用。"
    ),
}


def _stylePrefix(style: str) -> str:
    """根据风格返回 system prompt 前缀"""
    return _STYLE_TONE.get(style, _STYLE_TONE["学术"])


# ---------------------------------------------------------------------------
# 通用 system prompt（共用的「三可原则」约束）
# ---------------------------------------------------------------------------
_BASE_CONSTRAINTS = """
格式硬性要求：
1. 每个明确的论断必须紧跟 [数据: ...] 引用，引用内容来自下方输入；
2. 若数据不足以支撑某结论，请明确写出「基于现有数据无法判断」；
3. 引用必须来自输入，不要自造数字；
4. 全文使用中文，可用 Markdown 加粗（**xxx**）突出关键术语。
"""


def _buildSystemPrompt(style: str) -> str:
    """根据风格生成完整 system prompt"""
    return f"{_stylePrefix(style)}\n{_BASE_CONSTRAINTS}"


# ---------------------------------------------------------------------------
# Token 粗估（中文 1 字符 ≈ 1.5 token，英文词 ≈ 1 token）
# ---------------------------------------------------------------------------
def _approxTokenCount(text: str) -> int:
    """粗估 token 数（MVP 阶段用字符数 * 1.5 估算）"""
    return int(len(text) * 1.5)


# ---------------------------------------------------------------------------
# 词频 Prompt
# ---------------------------------------------------------------------------
def buildFreqPrompt(
    freqRows: List[Dict[str, Any]],
    corpusMeta: Dict[str, Any],
    style: str = "学术",
) -> Dict[str, str]:
    """构造词频分析的解读 Prompt

    Args:
        freqRows: 词频 top 行（已转 dict），每个元素含 Rank/Word/Freq/Range/Pct/Pmw
        corpusMeta: 语料元信息（corpusName / fileCount / totalChars / tokenCount）
        style: 风格（学术 / 通俗 / 简洁）

    Returns:
        {"system": ..., "user": ...}
    """
    system = _buildSystemPrompt(style)

    # 截断保底：最多 50 行
    rows = freqRows[:50]
    truncated = len(freqRows) > 50

    topByPct = sorted(rows, key=lambda r: r.get("Pct", 0) or 0, reverse=True)[:10]

    userParts: List[str] = []
    _totalChars = corpusMeta.get("totalChars", "?")
    if isinstance(_totalChars, int):
        _totalCharsStr = f"{_totalChars:,}"
    else:
        _totalCharsStr = str(_totalChars)
    userParts.append(
        f"用户基于语料「{corpusMeta.get('corpusName', '未命名')}」"
        f"（{corpusMeta.get('fileCount', '?')} 个文档，"
        f"约 {_totalCharsStr} 字符，"
        f"词种 {corpusMeta.get('tokenCount', '?')}）"
        f"生成了词频统计。"
    )
    userParts.append("【Top 50 高频词（Rank / Word / Freq / Pct）】")
    for r in rows:
        userParts.append(
            f"  {r.get('Rank', '?')}. {r.get('Word', '?')} "
            f"频次={r.get('Freq', '?')} 占比={r.get('Pct', '?')}%"
        )

    userParts.append("")
    userParts.append("【占比 Top 10 词】")
    for r in topByPct:
        userParts.append(f"  - {r.get('Word', '?')} {r.get('Pct', '?')}%")

    userParts.append("")
    if truncated:
        userParts.append(f"注：输入已精简到 Top 50（原始 {len(freqRows)} 条）。")

    userParts.append(
        "请按以下结构解读：\n"
        "1. 高频词反映该语料什么主题 / 语体特征？\n"
        "2. 是否有异常高频或异常低频词值得关注？\n"
        "3. 与该类语料的「预期词频分布」相比，有何偏差？"
    )

    return {"system": system, "user": "\n".join(userParts)}


# ---------------------------------------------------------------------------
# 共现网络 Prompt
# ---------------------------------------------------------------------------
def buildNetworkPrompt(
    networkSummary: Dict[str, Any],
    corpusMeta: Dict[str, Any],
    style: str = "学术",
) -> Dict[str, str]:
    """构造共现网络分析的解读 Prompt

    Args:
        networkSummary: 网络摘要字典，字段：
            - nodeCount: 节点数
            - edgeCount: 边数
            - communityCount: 社区数
            - topHubs: [{node, degree, freq}] top-10
            - topEdges: [{src, dst, weight}] top-5
            - windowSize: 滑动窗口
            - metric: 边权指标名
        corpusMeta: 语料元信息
        style: 风格

    Returns:
        {"system": ..., "user": ...}
    """
    system = _buildSystemPrompt(style)

    userParts: List[str] = []
    userParts.append(
        f"用户基于语料「{corpusMeta.get('corpusName', '未命名')}」"
        f"（{corpusMeta.get('fileCount', '?')} 个文档）"
        f"构建了共现网络（窗口={networkSummary.get('windowSize', '?')}，"
        f"边权={networkSummary.get('metric', '?')}）。"
    )
    userParts.append(
        f"网络规模：{networkSummary.get('nodeCount', '?')} 个节点、"
        f"{networkSummary.get('edgeCount', '?')} 条边、"
        f"识别出 {networkSummary.get('communityCount', '?')} 个社区。"
    )

    userParts.append("")
    userParts.append("【高连接度 Top 10 节点（hub）】")
    for h in networkSummary.get("topHubs", [])[:10]:
        userParts.append(
            f"  - {h.get('node', '?')} 度={h.get('degree', '?')} "
            f"频次={h.get('freq', '?')}"
        )

    userParts.append("")
    userParts.append("【强边 Top 5】")
    for e in networkSummary.get("topEdges", [])[:5]:
        userParts.append(
            f"  - {e.get('src', '?')} ↔ {e.get('dst', '?')} "
            f"权重={e.get('weight', '?')}"
        )

    userParts.append("")
    userParts.append(
        "请按以下结构解读：\n"
        "1. 该网络呈现什么样的词形共现结构？\n"
        "2. 哪些边 / 节点可能是研究的关键发现？\n"
        "3. 是否存在异常密集的子图？这只是待回到原文验证的共现线索，"
        "不得仅据网络直接判定语义类别、构式或固定搭配。"
    )

    return {"system": system, "user": "\n".join(userParts)}


# ---------------------------------------------------------------------------
# KWIC Prompt
# ---------------------------------------------------------------------------
def buildKwicPrompt(
    hits: List[Dict[str, Any]],
    query: str,
    corpusMeta: Dict[str, Any],
    topContextLeft: List[Dict[str, Any]],
    topContextRight: List[Dict[str, Any]],
    style: str = "学术",
) -> Dict[str, str]:
    """构造 KWIC 检索结果的解读 Prompt

    Args:
        hits: KWIC 命中行（已转 dict），元素含 leftText / nodeText / rightText
        query: 检索关键词
        corpusMeta: 语料元信息
        topContextLeft: 左侧高频词 Top-3（含 word, count）
        topContextRight: 右侧高频词 Top-3（含 word, count）
        style: 风格

    Returns:
        {"system": ..., "user": ...}
    """
    system = _buildSystemPrompt(style)

    sampleHits = hits[:10] if hits else []
    truncated = len(hits) > 10 if hits else False

    userParts: List[str] = []
    userParts.append(
        f"用户对语料「{corpusMeta.get('corpusName', '未命名')}」"
        f"执行关键词检索：query = 「{query}」，"
        f"共 {len(hits) if hits else 0} 条命中。"
    )

    userParts.append("")
    userParts.append("【典型 KWIC 样本（前 10 条）】")
    for i, h in enumerate(sampleHits, 1):
        # 兼容 dataclass(KwicHit) 与 dict:优先 .get,降级 getattr
        if isinstance(h, dict):
            left = h.get("leftText", "") or ""
            node = h.get("nodeText", query) or query
            right = h.get("rightText", "") or ""
        else:
            left = getattr(h, "leftText", "") or ""
            node = getattr(h, "nodeText", query) or query
            right = getattr(h, "rightText", "") or ""
        userParts.append(f"  {i}. …{left} 【{node}】 {right}…")

    userParts.append("")
    if topContextLeft:
        userParts.append("【左搭配高频词 Top 3】")
        for w in topContextLeft[:3]:
            userParts.append(
                f"  - {w.get('word', '?')} (出现 {w.get('count', '?')} 次)"
            )

    if topContextRight:
        userParts.append("")
        userParts.append("【右搭配高频词 Top 3】")
        for w in topContextRight[:3]:
            userParts.append(
                f"  - {w.get('word', '?')} (出现 {w.get('count', '?')} 次)"
            )

    userParts.append("")
    if truncated:
        userParts.append(f"注：KWIC 样本已精简到前 10 条（原始 {len(hits)} 条）。")

    userParts.append(
        "请按以下结构解读：\n"
        "1. 关键词「{query}」在语料中主要出现在哪些语境？\n"
        "2. 左 / 右搭配高频词揭示了什么用法偏好？\n"
        "3. 是否存在特殊的构式或固定搭配？".format(query=query)
    )

    return {"system": system, "user": "\n".join(userParts)}


# ---------------------------------------------------------------------------
# 词频数据汇总（取自现有的 unigramDf）
# ---------------------------------------------------------------------------
def summarizeFreqData(df: Any, maxRows: int = 50) -> List[Dict[str, Any]]:
    """从 pandas DataFrame 提取 top-N 词频行

    Args:
        df: 词频 DataFrame（列: Rank, Word, Freq, Range, Pct, Pmw）
        maxRows: 取前 N 行

    Returns:
        转 dict 列表
    """
    if df is None:
        return []
    try:
        head = df.head(maxRows)
        return head.to_dict(orient="records")
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 共现网络数据汇总
# ---------------------------------------------------------------------------
def summarizeNetworkData(
    network: Any,
    windowSize: Any = None,
    metric: str = "LogDice",
    topHubN: int = 10,
    topEdgeN: int = 5,
) -> Dict[str, Any]:
    """从 CooccurrenceNetwork 提取摘要

    Args:
        network: CooccurrenceNetwork 对象（含 .graph / .communities）
        windowSize: 滑动窗口大小（来自 UI 参数）
        metric: 边权指标名
        topHubN: 取多少 hub
        topEdgeN: 取多少强边

    Returns:
        摘要 dict
    """
    summary: Dict[str, Any] = {
        "nodeCount": 0,
        "edgeCount": 0,
        "communityCount": 0,
        "topHubs": [],
        "topEdges": [],
        "windowSize": windowSize,
        "metric": metric,
    }
    if network is None:
        return summary
    graph = getattr(network, "graph", None)
    if graph is None:
        return summary

    summary["nodeCount"] = graph.number_of_nodes()
    summary["edgeCount"] = graph.number_of_edges()

    # 社区数
    communities = getattr(network, "communities", {}) or {}
    summary["communityCount"] = len(set(communities.values())) if communities else 0

    # hub 节点：按 degree 排序
    try:
        degreeSeq = sorted(graph.degree(), key=lambda x: x[1], reverse=True)[:topHubN]
        for node, degree in degreeSeq:
            freq = 0
            try:
                freq = graph.nodes[node].get("freq", 0) or 0
            except Exception:
                freq = 0
            summary["topHubs"].append({"node": node, "degree": degree, "freq": freq})
    except Exception:
        pass

    # 强边：按 weight 排序
    try:
        sortedEdges = sorted(
            graph.edges(data=True),
            key=lambda e: e[2].get("weight", 0) or 0,
            reverse=True,
        )[:topEdgeN]
        for src, dst, data in sortedEdges:
            summary["topEdges"].append(
                {
                    "src": src,
                    "dst": dst,
                    "weight": float(data.get("weight", 0) or 0),
                }
            )
    except Exception:
        pass

    return summary


# ---------------------------------------------------------------------------
# KWIC 数据汇总
# ---------------------------------------------------------------------------
def _extractTextTokens(text: str) -> List[str]:
    """极简分词：按空白与非中文字符切分（仅做搭配统计）"""
    if not text:
        return []
    # 中文按字切，英文按词切
    tokens: List[str] = []
    buf: List[str] = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            if buf:
                tokens.append("".join(buf))
                buf = []
            tokens.append(ch)
        elif ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                tokens.append("".join(buf))
                buf = []
    if buf:
        tokens.append("".join(buf))
    return [t for t in tokens if t]


def summarizeKwicData(
    hits: Any,
    query: str,
    topN: int = 3,
    sampleN: int = 10,
) -> Dict[str, Any]:
    """从 KWIC 命中列表提取摘要

    Args:
        hits: ConcordanceResult.hits 或 list[KwicHit]
        query: 检索词
        topN: 左右搭配高频词取 N 个
        sampleN: 样本 KWIC 取 N 条

    Returns:
        {"sampleHits": [...], "topLeft": [...], "topRight": [...], "total": N}
    """
    if hits is None:
        return {"sampleHits": [], "topLeft": [], "topRight": [], "total": 0}

    # 兼容 dataclass / dict
    items: List[Dict[str, Any]] = []
    try:
        for h in hits:
            if isinstance(h, dict):
                items.append(h)
            else:
                items.append(
                    {
                        "leftText": getattr(h, "leftText", "") or "",
                        "nodeText": getattr(h, "nodeText", query) or query,
                        "rightText": getattr(h, "rightText", "") or "",
                    }
                )
    except TypeError:
        return {"sampleHits": [], "topLeft": [], "topRight": [], "total": 0}

    total = len(items)
    sample = items[:sampleN]

    # 高频左 / 右搭配
    from collections import Counter

    leftCounter: Counter = Counter()
    rightCounter: Counter = Counter()
    for it in items:
        for tok in _extractTextTokens(it.get("leftText", "")):
            if tok == query:
                continue
            leftCounter[tok] += 1
        for tok in _extractTextTokens(it.get("rightText", "")):
            if tok == query:
                continue
            rightCounter[tok] += 1

    topLeft = [{"word": w, "count": c} for w, c in leftCounter.most_common(topN)]
    topRight = [{"word": w, "count": c} for w, c in rightCounter.most_common(topN)]

    return {
        "sampleHits": sample,
        "topLeft": topLeft,
        "topRight": topRight,
        "total": total,
    }


# ---------------------------------------------------------------------------
# 入口：按类型构造 Prompt
# ---------------------------------------------------------------------------
def buildPrompt(
    analysisType: str,
    data: Dict[str, Any],
    style: str = "学术",
) -> Dict[str, str]:
    """统一 Prompt 构造入口

    Args:
        analysisType: "freq" | "network" | "kwic"
        data: 取决于分析类型，约定字段：
            - freq: {"rows": [...], "corpusMeta": {...}}
            - network: {"summary": {...}, "corpusMeta": {...}}
            - kwic: {"hits": [...], "query": str, "topLeft": [...], "topRight": [...], "corpusMeta": {...}}
        style: 风格

    Returns:
        {"system": ..., "user": ...}
    """
    analysisType = (analysisType or "").lower()
    corpusMeta = data.get("corpusMeta") or {}

    if analysisType == "freq":
        return buildFreqPrompt(
            freqRows=data.get("rows") or [],
            corpusMeta=corpusMeta,
            style=style,
        )
    if analysisType == "network":
        return buildNetworkPrompt(
            networkSummary=data.get("summary") or {},
            corpusMeta=corpusMeta,
            style=style,
        )
    if analysisType == "kwic":
        return buildKwicPrompt(
            hits=data.get("hits") or [],
            query=data.get("query") or "",
            corpusMeta=corpusMeta,
            topContextLeft=data.get("topLeft") or [],
            topContextRight=data.get("topRight") or [],
            style=style,
        )
    if analysisType == "collocation":
        return buildCollocationPrompt(
            collocSummary=data.get("summary") or {},
            corpusMeta=corpusMeta,
            style=style,
        )
    if analysisType == "construction":
        return buildConstructionPrompt(
            constructionSummary=data.get("summary") or {},
            corpusMeta=corpusMeta,
            style=style,
        )
    if analysisType == "dependency":
        return buildDependencyPrompt(
            dependencySummary=data.get("summary") or {},
            corpusMeta=corpusMeta,
            style=style,
        )
    if analysisType == "keyword_list":
        return buildKeywordListPrompt(
            keywordSummary=data.get("summary") or {},
            corpusMeta=corpusMeta,
            style=style,
        )
    if analysisType == "ngram_cluster":
        return buildNgramClusterPrompt(
            clusterSummary=data.get("summary") or {},
            corpusMeta=corpusMeta,
            style=style,
        )
    if analysisType == "sentiment":
        return buildSentimentPrompt(
            sentimentSummary=data.get("summary") or {},
            corpusMeta=corpusMeta,
            style=style,
        )
    if analysisType == "word_cloud":
        return buildWordCloudPrompt(
            cloudSummary=data.get("summary") or {},
            corpusMeta=corpusMeta,
            style=style,
        )
    if analysisType == "word_analysis":
        return buildWordAnalysisPrompt(
            analysisSummary=data.get("summary") or {},
            corpusMeta=corpusMeta,
            style=style,
        )

    # fallback
    return {
        "system": _buildSystemPrompt(style),
        "user": "（未识别的分析类型，无法生成解读。data=" + str(data)[:200] + "）",
    }


# ===========================================================================
# 搭配 (Collocation) Prompt + Summarizer
# ===========================================================================
def summarizeCollocationData(result: Any, topN: int = 20) -> Dict[str, Any]:
    """从 CollocationResult 提取摘要"""
    if result is None:
        return {"collocateCount": 0}
    collocates = getattr(result, "collocates", []) or []
    items = []
    for c in collocates[:topN]:
        items.append(
            {
                "collocate": getattr(c, "collocate", ""),
                "freq": getattr(c, "freq", 0),
                "mi": round(getattr(c, "mi", 0.0) or 0.0, 2),
                "tScore": round(getattr(c, "tScore", 0.0) or 0.0, 2),
                "logDice": round(getattr(c, "logDice", 0.0) or 0.0, 2),
                "meetsMiThreshold": getattr(c, "meetsMiThreshold", False),
            }
        )
    return {
        "nodeWord": getattr(result, "nodeWord", ""),
        "nodeFreq": getattr(result, "nodeFreq", 0),
        "leftSpan": getattr(result, "leftSpan", 5),
        "rightSpan": getattr(result, "rightSpan", 5),
        "totalTokens": getattr(result, "totalTokens", 0),
        "collocateCount": len(collocates),
        "strongAssociationCount": getattr(result, "strongAssociationCount", 0),
        "elapsedSeconds": getattr(result, "elapsedSeconds", 0.0),
        "items": items,
    }


def buildCollocationPrompt(
    collocSummary: Dict[str, Any],
    corpusMeta: Dict[str, Any],
    style: str = "学术",
) -> Dict[str, str]:
    system = _buildSystemPrompt(style)
    parts: List[str] = []
    parts.append(
        f"用户对语料「{corpusMeta.get('corpusName', '未命名')}」"
        f"（{corpusMeta.get('fileCount', '?')} 个文档）"
        f"执行节点词「{collocSummary.get('nodeWord', '?')}」的搭配分析"
        f"（L{collocSummary.get('leftSpan', '?')}/R{collocSummary.get('rightSpan', '?')} 跨距，"
        f"节点频次 R={collocSummary.get('nodeFreq', '?')}）。"
    )
    parts.append(
        f"共发现 {collocSummary.get('collocateCount', 0)} 个搭配词，"
        f"其中达到 MI 强关联阈值的搭配 "
        f"{collocSummary.get('strongAssociationCount', 0)} 个。"
    )
    parts.append("")
    parts.append("【Top 20 搭配词（按 MI 降序）】")
    for it in collocSummary.get("items", [])[:20]:
        strengthFlag = "★" if it.get("meetsMiThreshold") else " "
        parts.append(
            f"  {strengthFlag} {it.get('collocate', '?')}  "
            f"O={it.get('freq', '?')}  MI={it.get('mi', '?')}  "
            f"T={it.get('tScore', '?')}  LogDice={it.get('logDice', '?')}"
        )
    parts.append("")
    parts.append(
        "请按以下结构解读：\n"
        "1. 这些搭配词揭示了节点词的哪些典型语境 / 用法偏好？\n"
        "2. 达到 MI 强关联阈值的搭配（带★）中是否有反常或意外的组合？\n"
        "3. 对语料主题或语体特征有何推断？\n"
        "注意:MI 是关联强度,不是显著性检验的 p 值。"
    )
    return {"system": system, "user": "\n".join(parts)}


# ===========================================================================
# 构式 (Construction) Prompt + Summarizer
# ===========================================================================
def summarizeConstructionData(result: Any, topSlotN: int = 10) -> Dict[str, Any]:
    if result is None:
        return {"slotCount": 0}
    slots = getattr(result, "slotEntries", []) or []
    internalPairs = getattr(result, "internalPairs", []) or []
    items = []
    for s in slots[:topSlotN]:
        items.append(
            {
                "slotLabel": getattr(s, "slotLabel", ""),
                "posTag": getattr(s, "posTag", ""),
                "word": getattr(s, "word", ""),
                "freq": getattr(s, "freq", 0),
                "mi": round(getattr(s, "mi", 0.0) or 0.0, 2),
                "meetsMiThreshold": getattr(s, "meetsMiThreshold", False),
            }
        )
    return {
        "pattern": getattr(result, "patternRaw", ""),
        "freq": getattr(result, "constructionFreq", 0),
        "matchCount": getattr(result, "matchCount", 0),
        "overallInferenceAvailable": getattr(
            result, "overallInferenceAvailable", False
        ),
        "overallInferenceNote": getattr(result, "overallInferenceNote", ""),
        "slotCount": len(slots),
        "internalPairCount": len(internalPairs),
        "items": items,
    }


def buildConstructionPrompt(
    constructionSummary: Dict[str, Any],
    corpusMeta: Dict[str, Any],
    style: str = "学术",
) -> Dict[str, str]:
    system = _buildSystemPrompt(style)
    parts: List[str] = []
    parts.append(
        f"用户对语料「{corpusMeta.get('corpusName', '未命名')}」"
        f"执行构式「{constructionSummary.get('pattern', '?')}」的填充词分析。"
    )
    parts.append(
        f"构式频次={constructionSummary.get('freq', 0)},"
        f"匹配 {constructionSummary.get('matchCount', 0)} 个区间。"
    )
    parts.append(
        "未提供独立参考概率或构式机会空间,因此不报告构式整体 G²/p 值,"
        "也不得把构式频次解释为统计显著性。"
    )
    parts.append("")
    parts.append("【Top 10 slot 填充词】")
    for it in constructionSummary.get("items", [])[:10]:
        strengthFlag = "★" if it.get("meetsMiThreshold") else " "
        parts.append(
            f"  {strengthFlag} [{it.get('slotLabel', '?')}/{it.get('posTag', '?')}] "
            f"{it.get('word', '?')}  freq={it.get('freq', '?')}  "
            f"MI={it.get('mi', '?')}"
        )
    parts.append("")
    parts.append(
        "请按以下结构解读：\n"
        "1. 构式「{pattern}」的主要填充词反映了什么语义偏好？\n"
        "2. 各 slot 之间的搭配是否揭示了固定搭配倾向？\n"
        "3. 达到 MI 强关联阈值的填充词与构式整体语义是否一致？"
        "（MI 是效应强度,不是 p 值。）".format(
            pattern=constructionSummary.get("pattern", "?")
        )
    )
    return {"system": system, "user": "\n".join(parts)}


# ===========================================================================
# 依存 (Dependency) Prompt + Summarizer
# ===========================================================================
def summarizeDependencyData(
    parses: Any, topRelN: int = 10, topHeadN: int = 10
) -> Dict[str, Any]:
    if not parses:
        return {"relationCount": 0, "sentenceCount": 0}
    from collections import Counter

    relCounter: Counter = Counter()
    headCounter: Counter = Counter()
    deprelWordCounter: Counter = Counter()
    samples = []
    for p in (parses or [])[:5]:
        text = getattr(p, "text", "") or ""
        tokens = getattr(p, "tokens", []) or []
        for t in tokens:
            if t.head == t.id or t.head == 0:
                continue
            relCounter[t.deprel] += 1
            headCounter[t.form] += 1
            deprelWordCounter[(t.deprel, t.form)] += 1
        if text:
            samples.append(text[:120])

    return {
        "backends": sorted(
            {
                str(getattr(p, "backend", "") or "unknown")
                for p in (parses or [])
            }
        ),
        "sentenceCount": len(parses or []),
        "backendDetails": sorted(
            {
                (
                    str(getattr(p, "provider", "") or "未报告"),
                    str(getattr(p, "endpoint", "") or "未报告"),
                    str(getattr(p, "language", "") or "未报告"),
                    ",".join(getattr(p, "tasks", []) or []) or "未报告",
                    str(getattr(p, "modelVersion", "") or "未报告"),
                    str(getattr(p, "labelScheme", "") or "未报告"),
                )
                for p in (parses or [])
            }
        ),
        "relationCount": sum(relCounter.values()),
        "topRelations": [
            {"rel": r, "count": c} for r, c in relCounter.most_common(topRelN)
        ],
        "topHeads": [
            {"word": w, "count": c} for w, c in headCounter.most_common(topHeadN)
        ],
        "samples": samples,
    }


def buildDependencyPrompt(
    dependencySummary: Dict[str, Any],
    corpusMeta: Dict[str, Any],
    style: str = "学术",
) -> Dict[str, str]:
    system = _buildSystemPrompt(style)
    parts: List[str] = []
    parts.append(
        f"用户对语料「{corpusMeta.get('corpusName', '未命名')}」"
        f"的 {dependencySummary.get('sentenceCount', 0)} 个句子执行了依存句法分析,"
        f"共得到 {dependencySummary.get('relationCount', 0)} 条依存边。"
    )
    backends = ", ".join(dependencySummary.get("backends", [])) or "未知"
    parts.append(
        f"实际后端={backends}。必须按后端原始标签体系解释;若为 rule,只能作为教学演示,"
        "不得据此推断语料的句法复杂度或用于论文定量结论。"
    )
    for provider, endpoint, language, tasks, modelVersion, labelScheme in (
        dependencySummary.get("backendDetails", [])
    ):
        parts.append(
            "复现元数据:"
            f"provider={provider}, endpoint={endpoint}, language={language}, "
            f"tasks={tasks}, modelVersion={modelVersion}, labelScheme={labelScheme}。"
        )
    parts.append(
        "若模型版本为“未报告”,必须将其列为复现限制,不得臆测具体模型或标注体系。"
    )
    parts.append("")
    parts.append("【高频依存关系 Top 10】")
    for r in dependencySummary.get("topRelations", [])[:10]:
        parts.append(f"  - {r.get('rel', '?')}: {r.get('count', '?')} 次")
    parts.append("")
    parts.append("【高频核心词 Top 10（head）】")
    for h in dependencySummary.get("topHeads", [])[:10]:
        parts.append(f"  - {h.get('word', '?')}: {h.get('count', '?')} 次")
    if dependencySummary.get("samples"):
        parts.append("")
        parts.append("【典型样本句（前 5 条）】")
        for i, s in enumerate(dependencySummary["samples"], 1):
            parts.append(f"  {i}. {s}")
    parts.append("")
    parts.append(
        "请按以下结构解读：\n"
        "1. 该语料的句法结构呈现什么倾向（简单句 / 复合句 / 主从结构）？\n"
        "2. 高频核心词反映了什么样的论述焦点？\n"
        "3. 高频依存关系在当前后端标签体系中的描述性分布如何？不得在没有参照组、"
        "标注准确率与人工核验的情况下判断占比是否“合理”。"
    )
    return {"system": system, "user": "\n".join(parts)}


# ===========================================================================
# 关键词列表 (Keyword List) Prompt + Summarizer
# ===========================================================================
def summarizeKeywordListData(result: Any, topN: int = 20) -> Dict[str, Any]:
    if result is None:
        return {"keywordCount": 0}
    df = getattr(result, "df", None)
    if df is None:
        return {"keywordCount": 0}
    rows = []
    try:
        head = df.head(topN)
        records = head.to_dict(orient="records")
        for r in records:
            rows.append(
                {
                    "keyword": r.get("Keyword", ""),
                    "obsFreq": r.get("ObsFreq", 0),
                    "refFreq": r.get("RefFreq", 0),
                    "ll": round(float(r.get("LL", 0) or 0), 2),
                    "logRatio": round(float(r.get("LogRatio", 0) or 0), 2),
                    "adjustedP": round(float(r.get("AdjustedP", 1) or 1), 6),
                    "direction": r.get("Direction", ""),
                    "isKey": bool(r.get("IsKey", False)),
                }
            )
    except Exception:
        rows = []
    return {
        "observedName": getattr(result, "observedName", ""),
        "referenceName": getattr(result, "referenceName", ""),
        "observedTokens": getattr(result, "observedTokens", 0),
        "referenceTokens": getattr(result, "referenceTokens", 0),
        "keywordCount": getattr(result, "testedHypotheses", len(df)),
        "significantCount": getattr(result, "significantCount", 0),
        "significanceLevel": getattr(result, "significanceLevel", 0.0),
        "familyWiseAlpha": getattr(result, "familyWiseAlpha", 0.01),
        "testedHypotheses": getattr(result, "testedHypotheses", 0),
        "items": rows,
    }


def buildKeywordListPrompt(
    keywordSummary: Dict[str, Any],
    corpusMeta: Dict[str, Any],
    style: str = "学术",
) -> Dict[str, str]:
    system = _buildSystemPrompt(style)
    parts: List[str] = []
    parts.append(
        f"用户对语料「{corpusMeta.get('corpusName', '未命名')}」"
        f"（{keywordSummary.get('observedTokens', 0):,} tokens）与参照语料"
        f"「{keywordSummary.get('referenceName', '?')}」"
        f"（{keywordSummary.get('referenceTokens', 0):,} tokens）"
        f"做了关键性比较分析。"
    )
    parts.append(
        f"共识别 {keywordSummary.get('keywordCount', 0)} 个候选词，"
        f"其中通过 Holm 校正的关键词 "
        f"{keywordSummary.get('significantCount', 0)} 个（完整检验族 "
        f"{keywordSummary.get('testedHypotheses', 0)}，FWER α="
        f"{keywordSummary.get('familyWiseAlpha', '?')}）。"
    )
    parts.append("")
    parts.append("【Top 20 关键词】")
    for it in keywordSummary.get("items", [])[:20]:
        flag = "★" if it.get("isKey") else " "
        parts.append(
            f"  {flag} {it.get('keyword', '?')}  "
            f"obs={it.get('obsFreq', '?')}  ref={it.get('refFreq', '?')}  "
            f"G²={it.get('ll', '?')}  LogRatio={it.get('logRatio', '?')}  "
            f"Holm-p={it.get('adjustedP', '?')}  {it.get('direction', '')}"
        )
    parts.append("")
    parts.append(
        "请按以下结构解读：\n"
        "1. 这些关键词揭示了观察语料的哪些独有特征？\n"
        "2. 高 LogRatio 的词在两个语料中的差异有何含义？\n"
        "3. 与参照语料相比,观察语料的语体 / 主题有何倾向？\n"
        "只将 Holm 校正后的结果称为显著；G² 表示统计证据，"
        "LogRatio 表示效应方向与大小，不要将显著性等同于实质重要性。"
    )
    return {"system": system, "user": "\n".join(parts)}


# ===========================================================================
# N-gram 聚类 (Ngram Cluster) Prompt + Summarizer
# ===========================================================================
def summarizeNgramClusterData(
    result: Any, topClusterN: int = 5, topNgramPerCluster: int = 8
) -> Dict[str, Any]:
    if result is None:
        return {"clusterCount": 0}
    clusterIds = getattr(result, "cluster_ids", []) or []
    clusterSizes = getattr(result, "cluster_sizes", {}) or {}
    clusterTop = getattr(result, "cluster_top_ngrams", {}) or {}
    clusters = []
    # 取最大 size 的 top N 簇
    if clusterSizes:
        sortedClusters = sorted(clusterSizes.items(), key=lambda x: x[1], reverse=True)[
            :topClusterN
        ]
        for cid, size in sortedClusters:
            clusters.append(
                {
                    "id": int(cid),
                    "size": int(size),
                    "topNgrams": list(clusterTop.get(cid, []))[:topNgramPerCluster],
                }
            )
    return {
        "n": getattr(result, "n", 0),
        "ngramCount": getattr(result, "ngram_count", 0),
        "k": getattr(result, "k", 0),
        "silhouette": round(getattr(result, "silhouette", 0.0) or 0.0, 3),
        "featureMethod": getattr(result, "feature_method", "file-idf cosine"),
        "embeddingMethod": getattr(
            result, "embedding_method", "PCA + t-SNE (visualization only)"
        ),
        "isFallback": bool(getattr(result, "is_fallback", False)),
        "clusterCount": len(clusterSizes),
        "clusters": clusters,
    }


def buildNgramClusterPrompt(
    clusterSummary: Dict[str, Any],
    corpusMeta: Dict[str, Any],
    style: str = "学术",
) -> Dict[str, str]:
    system = _buildSystemPrompt(style)
    parts: List[str] = []
    parts.append(
        f"用户对语料「{corpusMeta.get('corpusName', '未命名')}」"
        f"执行了 {clusterSummary.get('n', '?')}-gram 聚类分析,"
        f"共 {clusterSummary.get('ngramCount', 0)} 个 N-gram 参与,"
        f"聚为 {clusterSummary.get('clusterCount', 0)} 个簇"
        f"（k={clusterSummary.get('k', '?')}），"
        f"余弦距离轮廓系数 = {clusterSummary.get('silhouette', '?')}。"
    )
    parts.append(
        f"特征={clusterSummary.get('featureMethod', '?')};"
        f"二维嵌入={clusterSummary.get('embeddingMethod', '?')}。"
        "t-SNE 坐标只用于邻域可视化,不得解释簇间全局距离、方向或面积。"
    )
    if clusterSummary.get("isFallback"):
        parts.append("当前为退化/单簇结果,不得据此命名主题或评价聚类质量。")
    parts.append("")
    parts.append("【Top 5 簇的代表性 N-gram】")
    for c in clusterSummary.get("clusters", []):
        ngrams = ", ".join(c.get("topNgrams", [])[:8])
        parts.append(f"  - 簇 {c.get('id')}（{c.get('size')} 个成员）: {ngrams}")
    parts.append("")
    parts.append(
        "请按以下结构解读：\n"
        "1. 各簇有哪些共享的文件分布模式？不要把文件共现簇直接命名为语义主题。\n"
        "2. 簇间的 N-gram 是否有重叠（暗示边界模糊）？\n"
        "3. 结合余弦轮廓系数描述分离度;不要使用未经校准的好/坏阈值。"
    )
    return {"system": system, "user": "\n".join(parts)}


# ===========================================================================
# 情感 (Sentiment) Prompt + Summarizer
# ===========================================================================
def summarizeSentimentData(result: Any, topSampleN: int = 5) -> Dict[str, Any]:
    if result is None:
        return {"docCount": 0}
    docs = getattr(result, "documents", []) or []
    polarityCounter = {"positive": 0, "negative": 0, "neutral": 0}
    scores = []
    samplePositive = []
    sampleNegative = []
    for d in docs:
        pol = (
            getattr(d.polarity, "value", "neutral")
            if hasattr(d, "polarity")
            else "neutral"
        )
        polarityCounter[pol] = polarityCounter.get(pol, 0) + 1
        score = getattr(d, "score", 0.0)
        scores.append(score)
        # 取 1-2 个正/负代表样本
        if pol == "positive" and len(samplePositive) < topSampleN:
            txt = (getattr(d, "text", "") or "")[:100]
            samplePositive.append({"file": d.fileName, "score": score, "text": txt})
        if pol == "negative" and len(sampleNegative) < topSampleN:
            txt = (getattr(d, "text", "") or "")[:100]
            sampleNegative.append({"file": d.fileName, "score": score, "text": txt})

    avgScore = sum(scores) / len(scores) if scores else 0.0
    return {
        "docCount": len(docs),
        "positiveCount": getattr(result, "positiveCount", polarityCounter["positive"]),
        "negativeCount": getattr(result, "negativeCount", polarityCounter["negative"]),
        "neutralCount": getattr(result, "neutralCount", polarityCounter["neutral"]),
        "avgScore": round(avgScore, 3),
        "samplePositive": samplePositive,
        "sampleNegative": sampleNegative,
    }


def buildSentimentPrompt(
    sentimentSummary: Dict[str, Any],
    corpusMeta: Dict[str, Any],
    style: str = "学术",
) -> Dict[str, str]:
    system = _buildSystemPrompt(style)
    parts: List[str] = []
    parts.append(
        f"用户对语料「{corpusMeta.get('corpusName', '未命名')}」"
        f"的 {sentimentSummary.get('docCount', 0)} 个文档执行了情感分析。"
    )
    parts.append(
        f"情感分布：正面 {sentimentSummary.get('positiveCount', 0)} 篇、"
        f"负面 {sentimentSummary.get('negativeCount', 0)} 篇、"
        f"中性 {sentimentSummary.get('neutralCount', 0)} 篇,"
        f"平均分 = {sentimentSummary.get('avgScore', '?')}（-1~+1）。"
    )
    if sentimentSummary.get("samplePositive"):
        parts.append("")
        parts.append("【正面样本】")
        for s in sentimentSummary["samplePositive"][:3]:
            parts.append(
                f"  - {s.get('file', '?')} (score={s.get('score'):.2f}): {s.get('text', '')}"
            )
    if sentimentSummary.get("sampleNegative"):
        parts.append("")
        parts.append("【负面样本】")
        for s in sentimentSummary["sampleNegative"][:3]:
            parts.append(
                f"  - {s.get('file', '?')} (score={s.get('score'):.2f}): {s.get('text', '')}"
            )
    parts.append("")
    parts.append(
        "请按以下结构解读：\n"
        "1. 语料整体情感倾向如何?是否以中性 / 正面 / 负面为主？\n"
        "2. 极端正 / 负样本揭示了哪些主题或表达？\n"
        "3. 情感分布是否提示语料类型（如评论 / 报道 / 论述）？"
    )
    return {"system": system, "user": "\n".join(parts)}


# ===========================================================================
# 词云 (Word Cloud) Prompt + Summarizer
# ===========================================================================
def summarizeWordCloudData(result: Any, topN: int = 30) -> Dict[str, Any]:
    if result is None:
        return {"wordCount": 0}
    wordFreqs = getattr(result, "wordFreqs", {}) or {}
    items = sorted(wordFreqs.items(), key=lambda x: x[1], reverse=True)[:topN]
    return {
        "wordCount": len(wordFreqs),
        "placedCount": getattr(result, "placedCount", 0),
        "totalTokens": getattr(result, "totalTokens", 0),
        "items": [{"word": w, "freq": f} for w, f in items],
    }


def buildWordCloudPrompt(
    cloudSummary: Dict[str, Any],
    corpusMeta: Dict[str, Any],
    style: str = "学术",
) -> Dict[str, str]:
    system = _buildSystemPrompt(style)
    parts: List[str] = []
    parts.append(
        f"用户对语料「{corpusMeta.get('corpusName', '未命名')}」"
        f"（{cloudSummary.get('totalTokens', 0):,} tokens）生成了词云。"
    )
    parts.append(
        f"词云共收录 {cloudSummary.get('wordCount', 0)} 个不同词，"
        f"实际放置 {cloudSummary.get('placedCount', 0)} 个。"
    )
    parts.append("")
    parts.append("【Top 30 高频词】")
    for it in cloudSummary.get("items", [])[:30]:
        parts.append(f"  - {it.get('word', '?')}: {it.get('freq', '?')}")
    parts.append("")
    parts.append(
        "请按以下结构解读：\n"
        "1. 词云整体呈现的主题 / 语体倾向是什么？\n"
        "2. 高频词的分布是否提示了高频概念或专有领域？\n"
        "3. 与该语料的预期词频分布相比,有无异常？"
    )
    return {"system": system, "user": "\n".join(parts)}


# ===========================================================================
# 词语分析 (Word Analysis) Prompt + Summarizer
# ===========================================================================
def summarizeWordAnalysisData(result: Any, topN: int = 30) -> Dict[str, Any]:
    """从 WordMetrics 提取摘要(词汇丰富度 / 高频词 / 词性分布)"""
    if result is None:
        return {"totalTokens": 0, "totalTypes": 0}
    posDist = getattr(result, "posDistribution", {}) or {}
    topPos = sorted(posDist.items(), key=lambda x: x[1], reverse=True)[:10]
    highFreq = getattr(result, "highFreqWords", []) or []
    items = []
    for w in highFreq[:topN]:
        items.append(
            {
                "rank": getattr(w, "rank", 0),
                "word": getattr(w, "word", ""),
                "freq": getattr(w, "freq", 0),
                "freqPct": round((getattr(w, "freqPct", 0.0) or 0.0) * 100, 2),
            }
        )
    # 词性 → 实词率参考(辅助判断语体)
    contentPos = {"n", "v", "a", "d", "nr", "ns", "nt", "vn", "vd"}
    contentTokens = sum(
        cnt for pos, cnt in posDist.items() if pos.lower() in contentPos
    )
    totalPos = sum(posDist.values())
    return {
        "totalTokens": getattr(result, "totalTokens", 0),
        "totalTypes": getattr(result, "totalTypes", 0),
        "fileCount": getattr(result, "fileCount", 0),
        "density": round((getattr(result, "density", 0.0) or 0.0) * 100, 2),
        "avgLength": round(getattr(result, "avgLength", 0.0) or 0.0, 2),
        "ttr": round((getattr(result, "ttr", 0.0) or 0.0) * 100, 2),
        "guiraud": round(getattr(result, "guiraud", 0.0) or 0.0, 2),
        "mattr": round((getattr(result, "mattr", 0.0) or 0.0) * 100, 2),
        "mtld": round(getattr(result, "mtld", 0.0) or 0.0, 2),
        "coverageAt50": getattr(result, "coverageAt50", 0),
        "coverageAt80": getattr(result, "coverageAt80", 0),
        "coverageAt90": getattr(result, "coverageAt90", 0),
        "topPos": [{"pos": p, "count": c} for p, c in topPos],
        "contentTokenRatio": (
            round(contentTokens / totalPos * 100, 1) if totalPos > 0 else 0.0
        ),
        "topWords": items,
        "elapsedSeconds": getattr(result, "elapsedSeconds", 0.0),
    }


def buildWordAnalysisPrompt(
    analysisSummary: Dict[str, Any],
    corpusMeta: Dict[str, Any],
    style: str = "学术",
) -> Dict[str, str]:
    system = _buildSystemPrompt(style)
    parts: List[str] = []
    parts.append(
        f"用户对语料「{corpusMeta.get('corpusName', '未命名')}」"
        f"（{analysisSummary.get('fileCount', '?')} 个文档）"
        f"执行了词语分析。"
    )
    parts.append(
        f"语料含 {analysisSummary.get('totalTokens', 0):,} tokens、"
        f"{analysisSummary.get('totalTypes', 0):,} types,"
        f"平均词长 {analysisSummary.get('avgLength', '?')} 字,"
        f"实词比 {analysisSummary.get('density', '?')}%。"
    )
    parts.append(
        f"词汇丰富度:TTR={analysisSummary.get('ttr', '?')}% 、"
        f"Guiraud={analysisSummary.get('guiraud', '?')} 、"
        f"MATTR={analysisSummary.get('mattr', '?')}% 、"
        f"MTLD={analysisSummary.get('mtld', '?')} 。"
    )
    parts.append(
        f"高频词覆盖率:50% 覆盖率需要 {analysisSummary.get('coverageAt50', '?')} 个词、"
        f"80% 需要 {analysisSummary.get('coverageAt80', '?')} 个词、"
        f"90% 需要 {analysisSummary.get('coverageAt90', '?')} 个词。"
    )
    parts.append("")
    parts.append("【词性分布 Top 10】")
    for p in analysisSummary.get("topPos", []):
        parts.append(f"  - {p.get('pos', '?')}: {p.get('count', '?')} 个")
    parts.append("")
    parts.append("【Top 30 高频词】")
    for w in analysisSummary.get("topWords", [])[:30]:
        parts.append(
            f"  {w.get('rank', '?')}. {w.get('word', '?')} "
            f"freq={w.get('freq', '?')} ({w.get('freqPct', '?')}%)"
        )
    parts.append("")
    parts.append(
        "请按以下结构解读：\n"
        "1. 该语料的词汇丰富度水平如何(对比同类型语料参考范围)?\n"
        "2. 高频词覆盖率反映该语料的词汇多样性有什么特点?\n"
        "3. 词性分布揭示了语料的语体倾向(口语/书面/学术/叙事)?\n"
        "4. 平均词长与高频词分布有无异常?"
    )
    return {"system": system, "user": "\n".join(parts)}
