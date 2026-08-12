"""Keyword List(主题词/Keyness)分析引擎

需求文档:
    - AntConc Keyword List 功能对标
    - 论文写作 / 语料库语言学的核心方法(参照 vs 观察语料对比)

核心概念:
    - 参照语料库 (Reference Corpus): 用户指定,通常规模较大,
      代表「通用语言/普通语料」的分布
    - 观察语料库 (Observed Corpus):  当前活动语料,
      代表用户研究对象的分布
    - Keyness 指标: 衡量「该词在观察语料中是否过度出现」

支持的显著度指标(对标 AntConc):
    - Log-Likelihood (LL / G2): Dunning 1993,业界事实标准,推荐使用
    - Log-Ratio:    Hardie 2014,直观(对数比值),低频词稳定
    - %DIFF:        简单百分比差异,直观易懂

输入/输出:
    - 输入: 两个语料库(CorpusStore 实例)+ 过滤参数
    - 输出: KeywordListResult,DataFrame 列:
        Rank | Keyword | ObsFreq | RefFreq | ObsRate(per 10k) | RefRate(per 10k)
              | LL | LogRatio | PctDiff | RawP | AdjustedP | Direction | IsKey

参考:
    - Dunning, T. (1993). Accurate methods for the statistics of surprise
      and coincidence. Computational Linguistics, 19(1), 61-74.
    - Hardie, A. (2014). Log Ratio: An Informal Introduction.
      CAS Showcase Talk.
    - Rayson, P., & Garside, R. (2000). Comparing corpora using frequency
      profiling. In CL2000.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# P0-A2 fix 2026-07-18:改用统一的 loguru logger
from app.core.utils import logger


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


# χ²(1) 单次检验临界值；界面中用它们反推 Holm 校正的家族错误率。
LL_THRESHOLD_P001 = 6.634897  # α = 0.01
LL_THRESHOLD_P005 = 3.841459  # α = 0.05
LL_THRESHOLD_P001_LARGE = 10.827566  # α = 0.001


@dataclass
class KeywordListResult:
    """Keyword List 分析结果。

    Attributes:
        df:                  主题词表 DataFrame
                              (列:Rank/Keyword/ObsFreq/RefFreq/
                               ObsRate/RefRate/LL/LogRatio/PctDiff/IsKey)
        observedName:        观察语料库名(展示用)
        referenceName:       参照语料库名(展示用)
        observedTokens:      观察语料总 token 数
        referenceTokens:     参照语料总 token 数
        elapsedSeconds:      计算耗时
        significanceLevel:   用于换算 FWER alpha 的χ²(1) 临界值
        significantCount:    完整检验族中 IsKey=True 的词数
        method:              算法描述
    """

    df: pd.DataFrame
    observedName: str
    referenceName: str
    observedTokens: int
    referenceTokens: int
    elapsedSeconds: float = 0.0
    significanceLevel: float = LL_THRESHOLD_P001
    significantCount: int = 0
    familyWiseAlpha: float = 0.01
    testedHypotheses: int = 0
    method: str = "G² (Dunning 1993) + Holm FWER"


# ---------------------------------------------------------------------------
# 核心算法
# ---------------------------------------------------------------------------


def computeLogLikelihood(
    a: int,
    b: int,
    nA: int,
    nB: int,
) -> float:
    """计算 2×2 列联表的 Log-Likelihood (G2) 显著度。

    公式(Dunning 1993):
        E1 = nA * (a+b) / (nA+nB)
        E2 = nB * (a+b) / (nA+nB)
        LL = 2 * (a * log(a/E1) + b * log(b/E2))

    其中:
        a = 词在观察语料中的频次
        b = 词在参照语料中的频次
        nA = 观察语料总 token 数
        nB = 参照语料总 token 数
        E1, E2 = 期望频次

    Args:
        a:  词在观察语料中的频次
        b:  词在参照语料中的频次
        nA: 观察语料总 token 数
        nB: 参照语料总 token 数

    Returns:
        float: G2 值(>=0)。越大表示该词在观察语料中越显著地过度出现。
    """
    # 0/0 边界:当词只出现在一个语料中时,LL 仍然有定义(对 0 取极限 = 0)
    # 但 0 * log(0/0) → 0 * (-inf) 需要返回 0
    if a == 0 and b == 0:
        return 0.0
    if a + b == 0:
        return 0.0
    if nA + nB == 0:
        return 0.0

    total = a + b
    nATotal = nA + nB

    # 期望频次(避免除以 0)
    e1 = nA * total / nATotal if nATotal > 0 else 0.0
    e2 = nB * total / nATotal if nATotal > 0 else 0.0

    ll = 0.0
    # a * log(a/E1)
    if a > 0 and e1 > 0:
        ll += a * math.log(a / e1)
    # b * log(b/E2)
    if b > 0 and e2 > 0:
        ll += b * math.log(b / e2)

    return 2.0 * ll


def computeLogRatio(
    a: int,
    b: int,
    nA: int,
    nB: int,
    smoothing: float = 0.0,
) -> float:
    """计算 Log-Ratio(Hardie 2014)。

    公式:
        rate_A = (a + smoothing) / nA
        rate_B = (b + smoothing) / nB
        LR = log2(rate_A / rate_B)

    平滑处理(smoothing=0.5 时常用 Laplace smoothing):
        - 当 b=0 时,LR 为正无穷(无界);实际中加上小常数避免 inf
        - 当 a=0 时,LR 为负无穷

    Args:
        a:        词在观察语料中的频次
        b:        词在参照语料中的频次
        nA:       观察语料总 token 数
        nB:       参照语料总 token 数
        smoothing: 加性平滑常数(默认 0,即纯公式;
                            实际计算 per-10k 时用 rate 即可)

    Returns:
        float: log2(rate_A / rate_B)。正值表示该词在观察语料更频繁。
    """
    if nA == 0 or nB == 0:
        return 0.0

    rateA = (a + smoothing) / nA
    rateB = (b + smoothing) / nB

    if rateB <= 0:
        # 参照语料无该词 → +inf
        return float("inf") if rateA > 0 else 0.0
    if rateA <= 0:
        return float("-inf")

    return math.log2(rateA / rateB)


def computePctDiff(
    a: int,
    b: int,
    nA: int,
    nB: int,
) -> float:
    """计算百分比差异(直观版 keyness 指标)。

    公式:
        rate_A = a / nA * 10000   (per 10000 token)
        rate_B = b / nB * 10000
        PctDiff = (rate_A - rate_B) / rate_B * 100  (单位:%)

    Returns:
        float: 百分比差异。0 = 两语料中频率相同;
                >0 = 观察语料中更频繁; <0 = 参照语料中更频繁。
    """
    if nA == 0 or nB == 0:
        return 0.0
    if b == 0:
        return float("inf") if a > 0 else 0.0
    rateA = a / nA
    rateB = b / nB
    return (rateA - rateB) / rateB * 100.0


def significanceAlphaFromLlThreshold(significanceLevel: float) -> float:
    """将 G² 阈值换算为自由度 1 的χ²上尾概率。"""
    return math.erfc(math.sqrt(max(float(significanceLevel), 0.0) / 2.0))


def adjustPValuesHolm(rawPValues: np.ndarray) -> np.ndarray:
    """Holm step-down 校正，控制完整候选词族的 FWER。"""
    values = np.asarray(rawPValues, dtype=float)
    count = len(values)
    if count == 0:
        return values.copy()
    order = np.argsort(values, kind="stable")
    sortedValues = values[order]
    adjustedSorted = np.maximum.accumulate(
        np.minimum(sortedValues * (count - np.arange(count)), 1.0)
    )
    adjusted = np.empty(count, dtype=float)
    adjusted[order] = adjustedSorted
    return adjusted


# ---------------------------------------------------------------------------
# 主入口:对比两个 CorpusStore 的词频
# ---------------------------------------------------------------------------


def _counterToDict(counter: Dict[str, int]) -> Dict[str, int]:
    """Counter/dict 转普通 dict,避免 hash 类型差异。"""
    return dict(counter) if counter else {}


def analyzeKeywordList(
    observedTokens: List[str],
    referenceTokens: List[str],
    observedName: str = "观察语料",
    referenceName: str = "参照语料",
    minFreq: int = 2,
    topN: int = 500,
    significanceLevel: float = LL_THRESHOLD_P001,
    method: str = "G² (Dunning 1993) + Holm FWER",
    smoothing: float = 0.5,
) -> Optional[KeywordListResult]:
    """对比观察语料与参照语料,找出 Keyness 显著的主题词。

    Args:
        observedTokens:   观察语料的分词结果(已分词)
        referenceTokens:  参照语料的分词结果
        observedName:     观察语料名(展示用)
        referenceName:    参照语料名(展示用)
        minFreq:          最低频次阈值(观察语料中至少出现 minFreq 次才纳入)
        topN:             返回 top-N 个关键词(按 LL 降序)
        significanceLevel: LL 阈值(默认 6.6349 = p < 0.01)
        method:           算法描述(展示用)
        smoothing:        Log-Ratio 的加性平滑常数(默认 0.5 Laplace)

    Returns:
        KeywordListResult 或 None(输入为空时)

    Note:
        - 输入已是分词结果(token list),不做二次分词(由调用方负责)
        - 计算复杂度 O(n + m + k·|vocab|),其中 k=3(LL/Log-Ratio/%DIFF)
        - 大语料(参照语料 100万+ token)耗时通常 < 5 秒
    """
    import time as _time

    startTime = _time.perf_counter()
    if not observedTokens or not referenceTokens:
        return None

    # 1) 统计词频(Counter 在大列表上比 dict 更快)
    from collections import Counter

    obsCounter = Counter(observedTokens)
    refCounter = Counter(referenceTokens)

    nA = len(observedTokens)
    nB = len(referenceTokens)

    # 2) 候选词族对称定义：任一语料中达到 minFreq 即纳入。
    #    先在完整候选族上校正，再做 topN 展示截断。
    candidateWords = {
        word
        for word in set(obsCounter) | set(refCounter)
        if max(obsCounter.get(word, 0), refCounter.get(word, 0)) >= minFreq
    }

    # 3) 对每个候选词计算显著度指标(向量化预分配)
    wordList = list(candidateWords)
    n = len(wordList)
    if n == 0:
        return None

    obsFreqs = np.array(
        [obsCounter.get(w, 0) for w in wordList], dtype=np.int64
    )
    refFreqs = np.array(
        [refCounter.get(w, 0) for w in wordList], dtype=np.int64
    )

    # Log-Likelihood(向量化版本,避免逐词调用 math.log)
    total = obsFreqs + refFreqs  # a + b
    grandTotal = nA + nB  # nA + nB
    # 期望频次
    e1 = nA * total / grandTotal
    e2 = nB * total / grandTotal

    # 各项分别计算(避免 log(0))
    # LL = 2 * [a*log(a/e1) + b*log(b/e2)]
    # 处理 0 值: a*log(a/e1), a=0 时为 0 * (-inf) = 0
    with np.errstate(divide="ignore", invalid="ignore"):
        llTermA = np.where(
            (obsFreqs > 0) & (e1 > 0),
            obsFreqs * np.log(obsFreqs / e1),
            0.0,
        )
        llTermB = np.where(
            (refFreqs > 0) & (e2 > 0),
            refFreqs * np.log(refFreqs / e2),
            0.0,
        )
    llVals = 2.0 * (llTermA + llTermB)
    llVals = np.maximum(llVals, 0.0)  # LL 永远 >= 0
    rawPValues = np.array(
        [math.erfc(math.sqrt(value / 2.0)) for value in llVals], dtype=float
    )
    adjustedPValues = adjustPValuesHolm(rawPValues)
    familyWiseAlpha = significanceAlphaFromLlThreshold(significanceLevel)

    # Log-Ratio (向量化)
    rateA = (obsFreqs + smoothing) / nA
    rateB = (refFreqs + smoothing) / nB
    with np.errstate(divide="ignore", invalid="ignore"):
        logRatio = np.log2(rateA / rateB)
    # inf / -inf 处理:rateB=0 时为 +inf; rateA=0 时为 -inf
    logRatio = np.where(rateB == 0, np.where(rateA > 0, np.inf, 0.0), logRatio)
    logRatio = np.where(rateA == 0, np.where(rateB > 0, -np.inf, 0.0), logRatio)

    # %DIFF
    pctDiff = np.where(
        refFreqs == 0,
        np.where(obsFreqs > 0, np.inf, 0.0),
        (obsFreqs / nA - refFreqs / nB) / (refFreqs / nB) * 100.0,
    )

    # ObsRate / RefRate (per 10k)
    obsRate = obsFreqs / nA * 10000.0
    refRate = refFreqs / nB * 10000.0

    # 4) 构造 DataFrame
    df = pd.DataFrame(
        {
            "Keyword": wordList,
            "ObsFreq": obsFreqs,
            "RefFreq": refFreqs,
            "ObsRate": obsRate,
            "RefRate": refRate,
            "LL": llVals,
            "LogRatio": logRatio,
            "PctDiff": pctDiff,
            "RawP": rawPValues,
            "AdjustedP": adjustedPValues,
            "Direction": np.select(
                [obsRate > refRate, obsRate < refRate],
                ["观察语料过度使用", "参照语料过度使用"],
                default="归一化频率相同",
            ),
            "IsKey": adjustedPValues <= familyWiseAlpha,
        }
    )

    # 5) 排序:LL 降序;正 keyness 优先(LL 大的整体靠前)
    df = df.sort_values(["LL"], ascending=[False]).reset_index(drop=True)

    totalSignificantCount = int(df["IsKey"].sum())

    # 6) 取 top-N（不改变已经完成的多重校正）
    if topN > 0 and len(df) > topN:
        df = df.head(topN).reset_index(drop=True)

    # 7) Rank
    df.insert(0, "Rank", df.index + 1)

    elapsed = _time.perf_counter() - startTime
    significantCount = totalSignificantCount

    logger.info(
        f"[KeywordList] 完成: 观察={observedName}({nA} tokens) vs "
        f"参照={referenceName}({nB} tokens) | "
        f"候选词 {n} 个,显著 {significantCount} 个 | 耗时 {elapsed:.2f}s"
    )

    return KeywordListResult(
        df=df,
        observedName=observedName,
        referenceName=referenceName,
        observedTokens=nA,
        referenceTokens=nB,
        elapsedSeconds=elapsed,
        significanceLevel=significanceLevel,
        significantCount=significantCount,
        familyWiseAlpha=familyWiseAlpha,
        testedHypotheses=n,
        method=method,
    )


# ---------------------------------------------------------------------------
# 工具:把任意 corpus 转换为 token list(走 CorpusStore.effectiveTexts)
# ---------------------------------------------------------------------------


def tokenizeCorpusStore(
    corpusStore: Any,
    segmenter: Any,
    minLength: int = 1,
    maxLength: int = 50,
    caseSensitive: bool = False,
    useStopwords: bool = False,
    excludeNumbers: bool = True,
) -> List[str]:
    """把 CorpusStore 转为 token list(走 cache 加速)。

    Args:
        corpusStore: CorpusStore 实例
        segmenter:   TextSegmenter 实例(共享 tokenCache)
        其他参数:    与 FrequencyAnalyzer 一致

    Returns:
        List[str]: 所有文件的 token 列表(已应用过滤规则)
    """
    if corpusStore is None:
        return []

    try:
        from app.view.widgets.freq_analyzer.freq_engine import FrequencyAnalyzer
    except Exception:
        # 兜底:仅做按字符切分(无法使用 jieba)
        return _fallbackTokenize(corpusStore)

    analyzer = FrequencyAnalyzer(
        minLength=minLength,
        maxLength=maxLength,
        caseSensitive=caseSensitive,
        excludeNumbers=excludeNumbers,
        useStopwords=useStopwords,
        useJieba=True,
        tokenCache=(
            corpusStore.tokenCache() if hasattr(corpusStore, "tokenCache") else None
        ),
    )

    tokens: List[str] = []
    fileToText = corpusStore.effectiveTexts()
    for fileName, text in fileToText.items():
        if not text:
            continue
        try:
            fileTokens = analyzer._segmentFile(text)
        except Exception:
            fileTokens = []
        tokens.extend(fileTokens)
    return tokens


def _fallbackTokenize(corpusStore: Any) -> List[str]:
    """降级方案:按非空白字符切分。"""
    tokens: List[str] = []
    fileToText = corpusStore.effectiveTexts()
    for text in fileToText.values():
        if text:
            tokens.extend(t for t in text.split() if t)
    return tokens
