# coding: utf-8
"""
构式搭配强度分析引擎(Construction Collocation Strength)

按需求:
    - 与 Collocates 区分:支持多词构式节点(如 "V 都 V 了"、"N 的 N" 等)
    - 输入形式:POS Pattern 表达式(复用 app.view.widgets.freq_analyzer.pos_pattern)
    - 输出指标:
        * 构式频次 (Construction Frequency)
        * 内部贴合度 (Internal Association, IA):构式内各 slot 间的 MI
        * 跨距贴合度 (Span Association):构式整体与周围词的 MI
        * MI / LogDice / Z-score / T-score / Delta-P(复用列联表计算)
        * 构式频次的描述性统计

    注意:没有独立参考概率或构式机会空间时,构式整体 G²/p 值不可识别。
    MI 仅作为关联强度,不能直接解释为统计显著性。

学术依据:
    - Stefanowitsch, A., & Gries, S. Th. (2003). Collostructions:
      Investigating the interaction between words and constructions.
      International Journal of Corpus Linguistics.
    - Gries, S. Th., & Stefanowitsch, A. (2004). Extending collostructional
      analysis: A corpus-based perspective on 'alternations'.
      International Journal of Corpus Linguistics.
    - Hilpert, M. (2008). Corpus Linguistics: Methods, Theory and Practice.

设计选择:
    1. 构式匹配复用 PosPattern.match,得到所有非重叠匹配区间
    2. 构式内每个 PLACEHOLDER slot 的填充词,统计为该 slot 的实例
    3. 跨距贴合度:对每个构式实例,统计其左右 ±span 内的高频搭配词及其强度
    4. 内部贴合度:对构式内每对 slot(i,j),计算其填充词的逐对 MI / Dice
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from app.core.utils import logger

from app.view.widgets.freq_analyzer.collocation_engine import (
    ContingencyTable,
    CollocationEngine,
)
from app.view.widgets.freq_analyzer.pos_pattern import (
    PatternMatch,
    PatternToken,
    PatternTokenType,
    PosPattern,
)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class ConstructionSlotEntry:
    """构式单个 slot 的填充词统计"""

    slotIndex: int = 0  # slot 在构式中的位置(0-based,仅 PLACEHOLDER)
    slotLabel: str = ""  # 如 "V0", "N1" 等
    posTag: str = ""  # 该 slot 的目标词性(V / N / ...)
    word: str = ""  # 该 slot 的实际填充词
    freq: int = 0  # 频次(该词在该 slot 上的次数)
    wordFreqInCorpus: int = 0  # 该词在整个语料中的频次
    prob: float = 0.0  # P(word | slot)
    mi: float = 0.0  # 该词与该 slot 的 MI
    logDice: float = 0.0  # LogDice
    zScore: float = 0.0  # Z-score
    meetsMiThreshold: bool = False  # MI 展示阈值,不是显著性检验


@dataclass
class CollocateEntry:
    """构式跨距内的一个搭配词"""

    collocate: str = ""
    freq: int = 0  # 共现频次 O
    posTag: str = ""  # 搭配词的词性(用于展示)
    mi: float = 0.0
    logDice: float = 0.0
    tScore: float = 0.0
    zScore: float = 0.0
    deltaP: float = 0.0
    collocateFreq: int = 0
    expectedFreq: float = 0.0
    meetsMiThreshold: bool = False


@dataclass
class InternalSlotPair:
    """构式内两个 slot 之间的内部贴合度"""

    slotA: int = 0
    slotB: int = 0
    labelA: str = ""
    labelB: str = ""
    pairFreq: int = 0  # (slotA 词, slotB 词) 共同出现的次数
    expectedFreq: float = 0.0
    mi: float = 0.0
    logDice: float = 0.0
    zScore: float = 0.0
    meetsMiThreshold: bool = False


@dataclass
class ConstructionResult:
    """构式分析完整结果"""

    patternRaw: str = ""  # 用户输入的原始模式
    constructionFreq: int = 0  # 构式总频次 O_c
    matchCount: int = 0  # 构式匹配区间数(可能含跨距内重复)
    totalTokens: int = 0  # 语料 token 总数 N
    uniqueTypes: int = 0  # 语料 type 数 V

    # 构式每个 slot 的填充词统计
    slotEntries: List[ConstructionSlotEntry] = field(default_factory=list)

    # 构式内部 slot 对的贴合度
    internalPairs: List[InternalSlotPair] = field(default_factory=list)

    # 跨距搭配词(构式整体作为节点)
    collocates: List[CollocateEntry] = field(default_factory=list)

    # 没有独立基线时,构式整体的 G²/p 值不可识别。
    overallInferenceAvailable: bool = False
    overallInferenceNote: str = (
        "未设置独立参考概率或构式机会空间,不计算构式整体 G²/p 值。"
    )

    # 元数据
    leftSpan: int = 3
    rightSpan: int = 3
    slotMiThreshold: float = 3.0  # MI 关联强度展示阈值,不是 p 值阈值
    elapsedSeconds: float = 0.0


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------
class ConstructionEngine:
    """构式搭配强度分析引擎"""

    def __init__(self):
        self._collEngine = CollocationEngine()

    # ============================================================
    # 主入口
    # ============================================================
    def analyze(
        self,
        tokens: List[str],
        posTags: List[str],
        patternStr: str,
        leftSpan: int = 3,
        rightSpan: int = 3,
        minFreq: int = 2,
        topN: int = 100,
        slotMiThreshold: float = 3.0,
    ) -> ConstructionResult:
        """构式搭配强度分析

        Args:
            tokens: 已分词的 token 列表
            posTags: 与 tokens 等长的词性标签列表
            patternStr: POS Pattern 表达式,如 "<V> 都 <V> 了"
            leftSpan: 左跨距(构式整体作为节点的跨距统计)
            rightSpan: 右跨距
            minFreq: 最低共现频次
            topN: 搭配词 Top-N
            slotMiThreshold: slot 词 MI 关联强度展示阈值,不表示统计显著性

        Returns:
            ConstructionResult
        """
        import time as _time

        startTime = _time.time()

        # 0) 输入校验
        if not tokens or len(tokens) != len(posTags):
            logger.warning("[ConstructionEngine] 输入为空或 token/POS 长度不一致")
            return ConstructionResult(patternRaw=patternStr)

        if not patternStr or not patternStr.strip():
            logger.warning("[ConstructionEngine] 模式为空")
            return ConstructionResult(patternRaw=patternStr)

        # 1) 编译模式
        try:
            pattern = PosPattern(patternStr)
        except Exception as e:
            logger.warning(f"[ConstructionEngine] 模式解析失败: {e}")
            return ConstructionResult(patternRaw=patternStr)

        # 2) 全局频次统计
        N = len(tokens)
        totalFreq: Counter = Counter(tokens)
        V = len(totalFreq)

        # 3) 匹配构式
        tokenPosPairs = list(zip(tokens, posTags))
        matches: List[PatternMatch] = pattern.match(tokenPosPairs)
        matchCount = len(matches)
        constructionFreq = matchCount  # 构式总频次 O_c

        result = ConstructionResult(
            patternRaw=patternStr,
            constructionFreq=constructionFreq,
            matchCount=matchCount,
            totalTokens=N,
            uniqueTypes=V,
            leftSpan=leftSpan,
            rightSpan=rightSpan,
            slotMiThreshold=slotMiThreshold,
        )

        if matchCount == 0:
            logger.warning(f"[ConstructionEngine] 模式「{patternStr}」未匹配到任何区间")
            result.elapsedSeconds = _time.time() - startTime
            return result

        # 4) 识别 PLACEHOLDER slots(按出现顺序编号)
        #    例: "<V> 都 <V> 了" → slots: [V0, V1]
        slotTokens: List[Tuple[int, PatternToken]] = []  # (pattern token index, token)
        slotIndexCounter = 0
        for idx, ptoken in enumerate(pattern.tokens):
            if ptoken.type == PatternTokenType.PLACEHOLDER:
                slotTokens.append((idx, ptoken))
                slotIndexCounter += 1

        # 5) 抽取每个匹配区间内对应 slot 位置的填充词
        #    slotFills[slotPosition][word] = freq
        slotFills: List[Counter] = [Counter() for _ in slotTokens]
        for m in matches:
            # 模式 token 数 vs 实际匹配 token 数必须一致(PosPattern 设计保证)
            nPat = len(pattern.tokens)
            if (m.endIdx - m.startIdx + 1) != nPat:
                # 防御:跳过异常区间
                continue
            for slotIdx, (patIdx, _ptoken) in enumerate(slotTokens):
                word = m.matched[patIdx][0]
                if not word:
                    continue
                slotFills[slotIdx][word] += 1

        # 6) 不对构式整体做 G²。若用样本自身的 constructionFreq / N
        #    定义零假设期望,则 E 恒等于 O,G² 恒为 0,不能形成推断检验。

        # 7) 每个 slot 的填充词 MI / LogDice / Z-score
        for slotIdx, (patIdx, ptoken) in enumerate(slotTokens):
            slotPos = ptoken.value  # set[str] | None
            posLabel = (
                _resolvePosLabel(ptoken) or "ANY"
            )
            slotLabel = f"{posLabel}{slotIdx}"

            slotTotal = sum(slotFills[slotIdx].values())
            if slotTotal == 0:
                continue

            for word, O_word in slotFills[slotIdx].items():
                if O_word < minFreq:
                    continue
                # 该词在语料中的总频次
                wordCorpusFreq = totalFreq.get(word, 0)
                if wordCorpusFreq == 0:
                    continue
                # 列联表:slot 位置 vs word
                # R = slotTotal, C = wordCorpusFreq, N = N, O = O_word
                table = ContingencyTable(
                    O=O_word, R=slotTotal, C=wordCorpusFreq, N=N
                )
                entry = ConstructionSlotEntry(
                    slotIndex=slotIdx,
                    slotLabel=slotLabel,
                    posTag=posLabel,
                    word=word,
                    freq=O_word,
                    wordFreqInCorpus=wordCorpusFreq,
                    prob=(O_word / slotTotal) if slotTotal > 0 else 0.0,
                )

                E = table.E
                if O_word > 0 and E > 0:
                    entry.mi = round(math.log2(O_word / E), 4)
                if O_word > 0:
                    denom = slotTotal + wordCorpusFreq
                    if denom > 0:
                        entry.logDice = round(
                            14.0 + math.log2((2.0 * O_word) / denom), 4
                        )
                        entry.logDice = max(0.0, min(14.0, entry.logDice))
                # Z-score
                if N > 1:
                    var = E * (N - slotTotal) / N * (N - wordCorpusFreq) / (N - 1)
                    sigma = math.sqrt(max(var, 1e-12))
                    if sigma > 0:
                        entry.zScore = round((O_word - E) / sigma, 4)
                entry.meetsMiThreshold = (
                    math.isfinite(entry.mi) and entry.mi >= slotMiThreshold
                )
                result.slotEntries.append(entry)

        # 8) 构式内部 slot 对的贴合度(每对 PLACEHOLDER 之间)
        for i in range(len(slotTokens)):
            for j in range(i + 1, len(slotTokens)):
                pairCounter: Counter = Counter()
                for m in matches:
                    if (m.endIdx - m.startIdx + 1) != len(pattern.tokens):
                        continue
                    wA = m.matched[slotTokens[i][0]][0]
                    wB = m.matched[slotTokens[j][0]][0]
                    if not wA or not wB:
                        continue
                    pairCounter[(wA, wB)] += 1

                pairTotal = sum(pairCounter.values())
                if pairTotal == 0:
                    continue

                posLabelA = _resolvePosLabel(slotTokens[i][1]) or "ANY"
                posLabelB = _resolvePosLabel(slotTokens[j][1]) or "ANY"
                labelA = f"{posLabelA}{i}"
                labelB = f"{posLabelB}{j}"

                # 每个构式实例提供一个 (slotA, slotB) 观测。边际频次必须来自
                # 同一 slot-pair 机会总体,不能与全语料 token 频次混用。
                slotATotal = sum(slotFills[i].values())
                slotBTotal = sum(slotFills[j].values())
                if slotATotal == 0 or slotBTotal == 0:
                    continue

                # 收集按 (词A, 词B) 联合频次
                # 联合频次已在 pairCounter 中
                # 但对全局 MI,我们用「对水平(slotA 出现,slotB 出现)是否同现」
                # 工程实现:对每对具体的 (wA, wB),计算 wA 与 wB 在语料中的共现 MI
                # 这是 collexeme 分析的标准做法
                for (wA, wB), O_pair in pairCounter.items():
                    if O_pair < minFreq:
                        continue
                    fA = slotFills[i].get(wA, 0)
                    fB = slotFills[j].get(wB, 0)
                    if fA == 0 or fB == 0:
                        continue
                    table = ContingencyTable(
                        O=O_pair, R=fA, C=fB, N=pairTotal
                    )
                    E_pair = table.E
                    if E_pair <= 0:
                        continue
                    pairEntry = InternalSlotPair(
                        slotA=i,
                        slotB=j,
                        labelA=labelA,
                        labelB=labelB,
                        pairFreq=O_pair,
                        expectedFreq=round(E_pair, 4),
                    )
                    pairEntry.mi = round(math.log2(O_pair / E_pair), 4)
                    denom = fA + fB
                    if denom > 0:
                        pairEntry.logDice = round(
                            14.0 + math.log2((2.0 * O_pair) / denom), 4
                        )
                        pairEntry.logDice = max(0.0, min(14.0, pairEntry.logDice))
                    if pairTotal > 1:
                        var = (
                            E_pair
                            * (pairTotal - fA)
                            / pairTotal
                            * (pairTotal - fB)
                            / (pairTotal - 1)
                        )
                        sigma = math.sqrt(max(var, 1e-12))
                        if sigma > 0:
                            pairEntry.zScore = round(
                                (O_pair - E_pair) / sigma, 4
                            )
                    pairEntry.meetsMiThreshold = (
                        math.isfinite(pairEntry.mi)
                        and pairEntry.mi >= slotMiThreshold
                    )
                    result.internalPairs.append(pairEntry)

        # 9) 跨距搭配:把构式整体视为"节点",统计跨距内词的 MI 等
        #    复用 CollocationEngine 的窗口共现统计
        if constructionFreq > 0 and (leftSpan > 0 or rightSpan > 0):
            # 构造一个虚拟"节点词":构式匹配起点的归一化标识
            nodeKey = f"__CONSTR__{patternStr}__"
            # 在 token 流上以每个 match 的 startIdx 作为节点位置
            # 把节点位置 token 替换为 nodeKey,其余保持不变
            syntheticTokens: List[str] = list(tokens)
            for m in matches:
                if m.startIdx < 0 or m.startIdx >= N:
                    continue
                syntheticTokens[m.startIdx] = nodeKey

            # 调用 CollocationEngine.analyze
            collResult = self._collEngine.analyze(
                tokens=syntheticTokens,
                nodeWord=nodeKey,
                leftSpan=leftSpan,
                rightSpan=rightSpan,
                minFreq=minFreq,
                topN=topN,
                caseSensitive=True,  # synthetic token 已带特殊前缀,无需小写
                miThreshold=slotMiThreshold,
            )
            # 转写到 CollocateEntry
            for ce in collResult.collocates:
                # 过滤掉节点自身的 key(理论不会出现)
                if ce.collocate == nodeKey:
                    continue
                entry = CollocateEntry(
                    collocate=ce.collocate,
                    freq=ce.freq,
                    posTag="",
                    mi=ce.mi,
                    logDice=ce.logDice,
                    tScore=ce.tScore,
                    zScore=ce.zScore,
                    deltaP=ce.deltaP1,  # 用 Delta-P₁ 作为主方向性指标
                    collocateFreq=ce.collocateFreq,
                    expectedFreq=ce.expectedFreq,
                    meetsMiThreshold=ce.meetsMiThreshold,
                )
                # 尝试获取搭配词的词性(取该词首次出现的 tag)
                for tok, tag in zip(tokens, posTags):
                    if tok == ce.collocate:
                        entry.posTag = tag
                        break
                result.collocates.append(entry)

        result.elapsedSeconds = _time.time() - startTime
        logger.info(
            f"[ConstructionEngine] 构式「{patternStr}」:"
            f"匹配 {matchCount} 次,slot 条目 {len(result.slotEntries)},"
            f"内部 slot 对 {len(result.internalPairs)},"
            f"跨距搭配 {len(result.collocates)},"
            f"耗时 {result.elapsedSeconds:.2f}s"
        )
        return result


def _resolvePosLabel(ptoken: PatternToken) -> Optional[str]:
    """将 PatternToken 转为可见的词性标签(V / N / ...)"""
    if ptoken.type != PatternTokenType.PLACEHOLDER:
        return None
    allowed = ptoken.value  # set[str] | None
    if not allowed:
        return "ANY"
    # 取集合中第一个元素作为标签(粗粒度足够用于 UI 展示)
    tag = sorted(allowed)[0].upper()
    return tag
