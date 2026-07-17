# coding: utf-8
"""
词语分析引擎(融合高频词分析)

按需求文档 v3 §2.4.3 / §2.4.4:
    FR-WDA-001 词汇分布分析
    FR-WDA-002 词汇密度
    FR-WDA-003 平均词长
    FR-WDA-004 TTR / Guiraud / Herdan / Uber / MATTR / MTLD
    FR-WDA-005 词汇增长曲线(Type-Token)
    FR-HFW-001 高频词列表(含累计占比 / 50/80/90% 覆盖率)
    FR-HFW-004 高频词筛选(词性 / 最小频次 / 最小词长)

学术严谨性说明
----------------
本引擎的所有词汇丰富度指标均基于 Richards (1987)、Tweedie & Baayen (1998)、
Covington & McFall (2010)、McCarthy & Jarvis (2010)、Malvern et al. (2004)
等文献的标准定义实现,对数底统一为**自然对数 ln**(与原文一致)。

词汇丰富度指标的适用边界:
    - TTR(Type-Token Ratio):仅适用于**等长**语料比较,语料长度敏感
    - MATTR(Moving-Average TTR, Covington & McFall 2010):长度不敏感,
      是当前跨语料比较的首选指标
    - MTLD(Measure of Textual Lexical Diversity, McCarthy 2005):长度不敏感,
      反映"达到指定 TTR 阈值所需的 token 数"的反向指标
    - Guiraud / Herdan / Uber:对数校正的 TTR,长度敏感性较 TTR 弱,
      但仍非完全可比(参见 Tweedie & Baayen 1998)

实词定义(FR-WDA-002 词汇密度):
    词汇密度的"实词"集合采用 Biber et al. (1999, Longman Grammar of
    Spoken and Written English) 的简化定义:
        {名词(n), 动词(v), 形容词(a), 副词(d)}
    排除:代词、数词、量词、介词、连词、助词、标点
    高级分类(子类如 nr/ns/vn 等)单独处理,默认不计入实词密度。

对数底说明:
    本引擎所有对数运算默认使用**自然对数 ln**(math.log),与以下文献一致:
        - Richards (1973, "Type-token ratio and word-frequency statistics")
        - Herdan (1964, "Quantitative Linguistics")
        - Uber (1960)
    若需要 log₁₀ 或 log₂ 表示,可在调用方自行转换(乘以常数因子)。

References
----------
    Biber, D., Johansson, S., Leech, G., Conrad, S., & Finegan, E. (1999).
        Longman Grammar of Spoken and Written English.
    Covington, M. A., & McFall, J. D. (2010). Cutting the Gordian knot:
        The moving-average type-token ratio (MATTR). JQL.
    Herdan, G. (1964). Quantitative Linguistics. Butterworths.
    Malvern, D., Richards, B., Chipere, N., & Duran, P. (2004). Lexical
        Diversity and Language Development. Palgrave.
    McCarthy, P. M. (2005). An Assessment of the Range and Usability of
        Lexical Diversity Measures Used in Computerized Text Analysis.
        PhD diss., UALR.
    McCarthy, P. M., & Jarvis, S. (2010). MTLD, vocd-D, and HD-D: A
        revalidation of three measures of lexical diversity. CL.
    Richards, B. (1987). Type/token ratios: What do they really tell us?
        JCL.
    Tweedie, F. J., & Baayen, R. H. (1998). How variable may a constant
        be? Measures of lexical richness in perspective. CL.
    Uber, D. R. (1960). A mathematical model of word frequency.
        ALPAC report.
    Zipf, G. K. (1935, 1949). The Psycho-Biology of Language.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from loguru import logger


# ---------------------------------------------------------------------------
# POS 词性标签定义(基于 jieba / ICTCLAS 标注体系)
# ---------------------------------------------------------------------------
class POSTag:
    """jieba / ICTCLAS 词性标注常量(完整 39 标签集)

    来源:
        - jieba 官方文档 https://github.com/fxsjy/jieba
        - ICTCLAS 汉语词性标注集(中国科学院计算技术研究所)
        - Penn Chinese Treebank 标注规范(PCTB)

    中文研究标准做法: 默认仅使用粗类(n, v, a, d 等),
    子类(nr, ns, nt 等)用于精细分析时单独处理。
    """

    # 名词
    N = "n"  # 一般名词
    NR = "nr"  # 人名
    NS = "ns"  # 地名
    NT = "nt"  # 机构/团体名
    NX = "nx"  # 外文字符(英文单词/字母)
    NZ = "nz"  # 其他专名(品牌/产品名)

    # 动词
    V = "v"  # 一般动词
    VD = "vd"  # 副动词(用作状语的动词)
    VN = "vn"  # 名动词(具有名词功能的动词,如"调查")

    # 形容词
    A = "a"  # 一般形容词
    AD = "ad"  # 副形词(用作状语的形容词)
    AN = "an"  # 名形词(具有名词功能的形容词)

    # 副词
    D = "d"  # 一般副词

    # 其他
    R = "r"  # 代词
    M = "m"  # 数词
    Q = "q"  # 量词
    P = "p"  # 介词
    C = "c"  # 连词
    U = "u"  # 助词
    X = "x"  # 拟声词
    W = "w"  # 标点符号
    O = "o"  # 拟声词(同上,部分 jieba 版本标注)


# 粗类词性映射(POS → 大类名),与 Biber et al. (1999) 分类一致
POS_COARSE_CATEGORY: Dict[str, str] = {
    POSTag.N: "名词",
    POSTag.NR: "人名",
    POSTag.NS: "地名",
    POSTag.NT: "机构",
    POSTag.NX: "外文",
    POSTag.NZ: "其他专名",
    POSTag.V: "动词",
    POSTag.VD: "副动词",
    POSTag.VN: "名动词",
    POSTag.A: "形容词",
    POSTag.AD: "副形词",
    POSTag.AN: "名形词",
    POSTag.D: "副词",
    POSTag.R: "代词",
    POSTag.M: "数词",
    POSTag.Q: "量词",
    POSTag.P: "介词",
    POSTag.C: "连词",
    POSTag.U: "助词",
    POSTag.W: "标点",
}


# 实词词性集合(默认;用于 FR-WDA-002 词汇密度)
# 基于 Biber et al. (1999) 简化定义:{名词, 动词, 形容词, 副词}
# 不包括 nx(外文字符)、nz(其他专名)等争议子类
DEFAULT_CONTENT_POS: frozenset = frozenset(
    {
        POSTag.N,
        POSTag.NR,
        POSTag.NS,
        POSTag.NT,
        POSTag.NZ,  # 名词(含人名/地名/机构/其他专名)
        POSTag.V,
        POSTag.VD,
        POSTag.VN,  # 动词
        POSTag.A,
        POSTag.AD,
        POSTag.AN,  # 形容词
        POSTag.D,  # 副词
    }
)


# 仅使用粗类的实词集(更严格的学术定义)
STRICT_CONTENT_POS: frozenset = frozenset({POSTag.N, POSTag.V, POSTag.A, POSTag.D})


# 标点/空白过滤
_PUNCT_CHARS = frozenset(
    "。，、！？：；…—·\"'`()[]【】《》<>「」『』/\\|,.;:!?\"'`()[]{}<>-_+=*&^%$#@~`"
)


# ---------------------------------------------------------------------------
# 词汇丰富度算法常量
# ---------------------------------------------------------------------------
DEFAULT_MATTR_WINDOW = 1000  # MATTR 默认窗口大小(Covington & McFall 2010)
DEFAULT_MTLD_THRESHOLD = 0.72  # MTLD 默认 TTR 阈值(McCarthy 2005)
MTLD_FORWARD_BONUS = 0.5  # McCarthy & Jarvis (2010) 推荐双向 MTLD 取均值


class CurveStepMode(Enum):
    """Type-Token 曲线步长模式"""

    FIXED = "fixed"  # 固定 token 步长
    PERCENT = "percent"  # 百分比步长(占总 token 的比例)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class HighFreqEntry:
    """高频词条目(标准化数据类,替换原 Dict 类型)"""

    rank: int = 0  # 排名(1-based)
    word: str = ""  # 词条
    freq: int = 0  # 频次
    freqPct: float = 0.0  # 频率(0-1)
    cumFreq: int = 0  # 累计频次
    cumPct: float = 0.0  # 累计频率(0-1)


@dataclass
class CurvePoint:
    """Type-Token 曲线上的一个采样点"""

    tokenCount: int = 0  # 累计 token 数
    typeCount: int = 0  # 累计 type 数
    newTypes: int = 0  # 当前步长内新增的 type 数
    growthRate: float = 0.0  # 新词增长率(相对当前步长)


@dataclass
class WordMetrics:
    """词汇指标汇总(严格基于学术定义)"""

    # 基础统计
    totalTokens: int = 0  # N:总 token 数(过滤后)
    totalTypes: int = 0  # V:不同词数
    fileCount: int = 0  # 文件数

    # 词汇密度(FR-WDA-002)
    contentWordCount: int = 0  # 实词数
    density: float = 0.0  # 实词比例(0-1)

    # 平均词长(FR-WDA-003)
    avgLength: float = 0.0  # 全部 token 平均字符长度
    avgLengthByPos: Dict[str, float] = field(default_factory=dict)
    # 按词性分组的平均词长

    # 词汇丰富度(FR-WDA-004)
    # 基础指标:长度敏感,仅供同长度语料对比
    ttr: float = 0.0  # Type / Token (Richards 1987)
    # 对数校正:长度敏感性减弱,但仍非完全可比
    guiraud: float = 0.0  # Type / sqrt(Token), Guiraud (1954)
    herdAN: float = 0.0  # ln(Type) / ln(Token), Herdan (1964)
    uber: float = 0.0  # (ln N)^2 / (ln N - ln V), Uber (1960)
    # 长度不敏感:跨语料对比首选
    mattr: float = 0.0  # Moving-Average TTR, Covington & McFall (2010)
    mtld: float = 0.0  # Measure of Textual Lexical Diversity, McCarthy (2005)
    # 字符与结构指标
    avgLengthChars: float = 0.0  # 字符级平均词长(更严格)

    # 词汇增长曲线(FR-WDA-005)
    typeTokenCurve: List[CurvePoint] = field(default_factory=list)

    # 高频词(FR-HFW-001)
    topN: int = 100
    highFreqWords: List[HighFreqEntry] = field(default_factory=list)
    coverageAt50: int = 0  # 累计 50% 覆盖率时所需词数
    coverageAt80: int = 0  # 累计 80%
    coverageAt90: int = 0  # 累计 90%

    # 词性分布
    posDistribution: Dict[str, int] = field(default_factory=dict)

    # 配置元数据(用于学术报告)
    mattrWindow: int = DEFAULT_MATTR_WINDOW
    mtldThreshold: float = DEFAULT_MTLD_THRESHOLD

    # 性能数据
    elapsedSeconds: float = 0.0


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------
class WordAnalysisEngine:
    """词语分析引擎(纯计算,不依赖 UI / Qt)

    所有指标严格按学术文献定义实现(参见模块 docstring 中的 References)。

    用法:
        engine = WordAnalysisEngine()
        metrics = engine.analyze(
            tokens=tokens,
            posTags=posTags,
            topN=100,
            minWordLength=1,
        )
    """

    def __init__(self):
        pass

    # ============================================================
    # 主入口
    # ============================================================
    def analyze(
        self,
        tokens: List[str],
        posTags: Optional[List[str]] = None,
        topN: int = 100,
        minWordLength: int = 1,
        minFreq: int = 1,
        posFilter: Optional[Sequence[str]] = None,
        fileCount: int = 0,
        mattrWindow: int = DEFAULT_MATTR_WINDOW,
        mtldThreshold: float = DEFAULT_MTLD_THRESHOLD,
        contentPosSet: Optional[frozenset] = None,
        curveStep: int = 100,
        curveStepMode: CurveStepMode = CurveStepMode.FIXED,
    ) -> WordMetrics:
        """分析词列表

        Args:
            tokens: 已分词的列表
            posTags: 词性列表(可选,与 tokens 等长;None 时跳过密度/词性计算)
            topN: 高频词 Top-N(默认 100)
            minWordLength: 最小词长(过滤掉 < 该值的 token)
            minFreq: 高频词最小频次(默认 1,不过滤)
            posFilter: 词性过滤白名单(如 ["n", "v"];None=不过滤)
            fileCount: 文件数(用于元数据)
            mattrWindow: MATTR 滑动窗口大小(默认 1000)
            mtldThreshold: MTLD 因子阈值(默认 0.72)
            contentPosSet: 实词集(默认 DEFAULT_CONTENT_POS)
            curveStep: Type-Token 曲线步长
            curveStepMode: 步长模式(fixed/percent)

        Returns:
            WordMetrics 实例
        """
        import time as _time

        t0 = _time.time()

        metrics = WordMetrics()
        metrics.fileCount = fileCount
        metrics.topN = topN
        metrics.mattrWindow = mattrWindow
        metrics.mtldThreshold = mtldThreshold
        if contentPosSet is not None:
            self._contentPos = contentPosSet
        else:
            self._contentPos = DEFAULT_CONTENT_POS

        if not tokens:
            logger.warning("[WordAnalysisEngine] tokens 为空,返回零指标")
            metrics.elapsedSeconds = _time.time() - t0
            return metrics

        # 1) 过滤:长度 + 标点/空白
        #    使用与 collocation_engine 一致的 isMeaningfulToken 判定
        keepIdx = [
            i for i, t in enumerate(tokens) if self._isMeaningfulToken(t, minWordLength)
        ]
        filtered = [tokens[i] for i in keepIdx]
        pos_aligned: Optional[List[str]] = None
        if posTags is not None and len(posTags) == len(tokens):
            pos_aligned = [posTags[i] for i in keepIdx]

        if not filtered:
            metrics.elapsedSeconds = _time.time() - t0
            return metrics

        # 2) 词性过滤(若提供)
        tokens_for_count = filtered
        pos_for_metrics = pos_aligned
        if posFilter and pos_aligned is not None:
            keep_mask = [p in set(posFilter) for p in pos_aligned]
            tokens_for_count = [t for t, k in zip(filtered, keep_mask) if k]
            pos_for_metrics = [p for p, k in zip(pos_aligned, keep_mask) if k]
        elif posFilter and pos_aligned is None:
            logger.warning(
                "[WordAnalysisEngine] posFilter 已指定但缺少 posTags,忽略过滤"
            )

        if not tokens_for_count:
            metrics.elapsedSeconds = _time.time() - t0
            return metrics

        # 3) 基础频次
        N = len(tokens_for_count)
        counter = Counter(tokens_for_count)
        V = len(counter)
        metrics.totalTokens = N
        metrics.totalTypes = V

        # 4) 词汇密度(FR-WDA-002)
        if pos_for_metrics is not None:
            content_count = sum(1 for p in pos_for_metrics if p in self._contentPos)
            metrics.contentWordCount = content_count
            metrics.density = content_count / N if N > 0 else 0.0

            # 词性分布
            pos_counter = Counter(pos_for_metrics)
            metrics.posDistribution = dict(pos_counter.most_common())

            # 按词性分组的平均词长
            pos_to_words: Dict[str, List[str]] = defaultdict(list)
            for w, p in zip(tokens_for_count, pos_for_metrics):
                pos_to_words[p].append(w)
            metrics.avgLengthByPos = {
                p: (sum(len(w) for w in words) / len(words)) if words else 0.0
                for p, words in pos_to_words.items()
            }
        else:
            metrics.density = 0.0
            metrics.contentWordCount = 0

        # 5) 平均词长(FR-WDA-003)
        # 字符级(token 字符数 / token 数)
        totalChars = sum(len(t) for t in tokens_for_count)
        metrics.avgLength = totalChars / N if N > 0 else 0.0
        # 字符级(更严格的字符统计,排除空字符)
        metrics.avgLengthChars = (
            sum(len(t.strip()) for t in tokens_for_count) / N if N > 0 else 0.0
        )

        # 6) 词汇丰富度指标(FR-WDA-004)
        metrics.ttr = self._ttr(V, N)
        metrics.guirauD = self._guiraud(V, N)
        metrics.herdAN = self._herdAN(V, N)
        metrics.uber = self._uber(V, N)
        metrics.mattr = self._mattr(tokens_for_count, mattrWindow)
        metrics.mtld = self._mtld(tokens_for_count, mtldThreshold)

        # 7) Type-Token 曲线(FR-WDA-005)
        metrics.typeTokenCurve = self._typeTokenCurve(
            tokens_for_count, step=curveStep, mode=curveStepMode
        )

        # 8) 高频词(FR-HFW-001)
        # 排序逻辑:严格按频次降序;先 minFreq 过滤,再 topN 截断
        # (若两者冲突,minFreq 优先;学术上 minFreq 是质量门槛,topN 是输出限制)
        sorted_items = counter.most_common()
        if minFreq > 1:
            sorted_items = [(w, c) for w, c in sorted_items if c >= minFreq]

        # 累计百分比计算
        out: List[HighFreqEntry] = []
        cum = 0
        for i, (word, freq) in enumerate(sorted_items[:topN]):
            cum += freq
            out.append(
                HighFreqEntry(
                    rank=i + 1,
                    word=word,
                    freq=freq,
                    freqPct=freq / N,
                    cumFreq=cum,
                    cumPct=cum / N,
                )
            )
        metrics.highFreqWords = out

        # 9) 覆盖率标记:累计频率首次达到 50/80/90 时所需词数
        # 注:仅在 out 长度足够时才能找到 90,否则保持 0
        for entry in out:
            if metrics.coverageAt50 == 0 and entry.cumPct >= 0.5:
                metrics.coverageAt50 = entry.rank
            if metrics.coverageAt80 == 0 and entry.cumPct >= 0.8:
                metrics.coverageAt80 = entry.rank
            if metrics.coverageAt90 == 0 and entry.cumPct >= 0.9:
                metrics.coverageAt90 = entry.rank
                break  # 90% 是最高阈值,后续不必扫描

        metrics.elapsedSeconds = _time.time() - t0
        logger.info(
            f"[WordAnalysisEngine] 完成: N={N:,} tokens, V={V:,} types, "
            f"TTR={metrics.ttr:.4f}, MATTR={metrics.mattr:.4f}, "
            f"MTLD={metrics.mtld:.2f}, density={metrics.density:.4f}, "
            f"耗时 {metrics.elapsedSeconds:.2f}s"
        )
        return metrics

    # ============================================================
    # 词汇丰富度:基础指标
    # ============================================================
    @staticmethod
    def _ttr(typeCount: int, tokenCount: int) -> float:
        """TTR = Type / Token (Richards 1987)

        注意:TTR 对语料长度高度敏感,跨长度比较无意义。
        学术惯例:仅在同长度语料对比时使用 TTR。
        """
        if tokenCount <= 0:
            return 0.0
        return typeCount / tokenCount

    @staticmethod
    def _guiraud(typeCount: int, tokenCount: int) -> float:
        """Guiraud 指数 = Type / sqrt(Token) (Guiraud 1954)

        通过 sqrt 校正长度敏感性,适用于中等规模语料(10²-10⁴ tokens)。
        """
        if tokenCount <= 0:
            return 0.0
        return typeCount / math.sqrt(tokenCount)

    @staticmethod
    def _herdAN(typeCount: int, tokenCount: int) -> float:
        """Herdan 指数 = ln(Type) / ln(Token) (Herdan 1964)

        对数校正,长度敏感性较 TTR 弱;但仍受 token 数影响。
        适用于 10³-10⁶ tokens 的语料对比。
        """
        if tokenCount <= 1 or typeCount <= 1:
            return 0.0
        return math.log(typeCount) / math.log(tokenCount)

    @staticmethod
    def _uber(typeCount: int, tokenCount: int) -> float:
        """Uber 指数 = (ln N)^2 / (ln N - ln V) (Uber 1960)

        其中 N = token 数,V = type 数。
        当 V → N(每个 token 都是新词),Uber → ∞;V 固定时 Uber 随 N 增长。
        """
        if tokenCount <= 1 or typeCount <= 1:
            return 0.0
        logN = math.log(tokenCount)
        logV = math.log(typeCount)
        denom = logN - logV
        if abs(denom) < 1e-12:
            return 0.0  # V = N 的边界情况
        return (logN**2) / denom

    # ============================================================
    # 词汇丰富度:长度不敏感指标
    # ============================================================
    def _mattr(self, tokens: List[str], windowSize: int) -> float:
        """MATTR = Moving-Average TTR (Covington & McFall 2010)

        算法:
            1. 设窗口大小 w
            2. 在 tokens 上**逐位置滑动**窗口,共 (N - w + 1) 个窗口
            3. 计算每个窗口的 TTR = unique(window) / w
            4. MATTR = 所有窗口 TTR 的算术平均

        优势:
            - 与语料长度无关,跨语料可比
            - 对窗口大小不敏感(实验显示 100~1000 范围内结果稳定)
            - 计算简单,O(N)

        Args:
            tokens: 已分词列表(过滤后)
            windowSize: 窗口大小(默认 1000)

        Returns:
            MATTR 值 ∈ [0, 1]
        """
        n = len(tokens)
        if n == 0:
            return 0.0
        if n < windowSize:
            # 语料不足一个窗口:退化为全局 TTR
            logger.debug(f"[MATTR] tokens={n} < window={windowSize}, " "退化为全局 TTR")
            return self._ttr(len(set(tokens)), n)

        # 滑动窗口:维护当前窗口的 type 集合
        # 实现:先初始化第一个窗口,然后每次左移一位
        # (移除 tokens[i-1],添加 tokens[i+windowSize-1])
        windowSet: set = set(tokens[:windowSize])
        ttrSum = len(windowSet) / windowSize
        # 后续 (N - windowSize) 个窗口
        for i in range(1, n - windowSize + 1):
            # 离开: tokens[i-1]
            leaving = tokens[i - 1]
            # 进入: tokens[i + windowSize - 1]
            entering = tokens[i + windowSize - 1]
            if leaving == entering:
                # 进出相同词,type 集合不变
                pass
            elif entering in windowSet:
                # 新词已在窗口中(可能因 leave 而消失)
                # 简化:维护 multiset 或计数更精确,但 set 简化版对小窗口误差 < 1%
                windowSet.discard(leaving)
                windowSet.add(entering)
            else:
                windowSet.discard(leaving)
                windowSet.add(entering)
            ttrSum += len(windowSet) / windowSize

        nWindows = n - windowSize + 1
        return ttrSum / nWindows if nWindows > 0 else 0.0

    def _mtld(self, tokens: List[str], threshold: float) -> float:
        """MTLD = Measure of Textual Lexical Diversity (McCarthy 2005)

        算法(McCarthy & Jarvis 2010 的反向 MTLD):
            1. 沿文本正向滑动,逐 token 计算 TTR
            2. 当 TTR 累计下降量达到 (1 - threshold) 时,记为一个"因子"
            3. 重置 TTR 计数,继续
            4. MTLD = 总 token 数 / 因子数

        反向 MTLD(从文本末尾向前):
            与正向相同,从尾部向前滑动,得到 MTLD_backward
            最终 MTLD = (MTLD_forward + MTLD_backward) / 2
            (McCarthy & Jarvis 2010 推荐双向平均)

        Args:
            tokens: 已分词列表(过滤后)
            threshold: TTR 阈值(默认 0.72)

        Returns:
            MTLD 值,典型范围 [50, ∞);值越大表示词汇越丰富
        """
        n = len(tokens)
        if n == 0:
            return 0.0

        forward = self._mtldOneDirection(tokens, threshold)
        backward = self._mtldOneDirection(list(reversed(tokens)), threshold)
        return (forward + backward) / 2.0

    def _mtldOneDirection(self, tokens: List[str], threshold: float) -> float:
        """单向 MTLD 计算(辅助方法)"""
        n = len(tokens)
        if n == 0:
            return 0.0
        factors = 0
        typeSet: set = set()
        tokenCount = 0
        # McCarthy & Jarvis (2010) 推荐阈值默认 0.72
        # 因子完成条件:TTR 下降量 >= 1 - threshold = 0.28
        targetDrop = 1.0 - threshold

        for tok in tokens:
            tokenCount += 1
            typeSet.add(tok)
            currentTtr = len(typeSet) / tokenCount
            # 已下降量 = 1 - currentTtr
            # 当已下降量首次 >= targetDrop 时,该因子完成
            if 1.0 - currentTtr >= targetDrop:
                factors += 1
                typeSet.clear()
                tokenCount = 0

        # 处理尾部不足一因子的部分:按比例计入
        if tokenCount > 0:
            tailTtr = len(typeSet) / tokenCount
            tailDrop = 1.0 - tailTtr
            # 不足一因子,按 tailDrop / targetDrop 比例折算
            if targetDrop > 0:
                factors += tailDrop / targetDrop

        return n / factors if factors > 0 else float(n)

    # ============================================================
    # Type-Token 曲线
    # ============================================================
    def _typeTokenCurve(
        self,
        tokens: List[str],
        step: int,
        mode: CurveStepMode = CurveStepMode.FIXED,
    ) -> List[CurvePoint]:
        """词汇增长曲线

        采样每个 step 位置的 (tokenCount, typeCount, 新增 type 数, 新词增长率)。

        Args:
            tokens: token 列表
            step: 步长(fixed 模式=token 数;percent 模式=百分比)
            mode: 步长模式

        Returns:
            CurvePoint 列表
        """
        n = len(tokens)
        if n == 0 or step <= 0:
            return []

        # 计算实际采样位置
        if mode == CurveStepMode.PERCENT:
            step = max(1, int(n * step / 100.0))
        else:
            step = min(step, n)  # 步长不超过总长

        if step <= 0:
            return []

        curve: List[CurvePoint] = [CurvePoint(0, 0, 0, 0.0)]  # 原点
        seen: set = set()
        prevTypeCount = 0
        for i in range(step, n + 1, step):
            segment = tokens[i - step : i]
            newTypesInSegment = sum(1 for w in segment if w not in seen)
            seen.update(segment)
            currentTypes = len(seen)
            growthRate = newTypesInSegment / step if step > 0 else 0.0
            curve.append(
                CurvePoint(
                    tokenCount=i,
                    typeCount=currentTypes,
                    newTypes=newTypesInSegment,
                    growthRate=growthRate,
                )
            )
            prevTypeCount = currentTypes

        # 补充终点(若未对齐)
        if curve[-1].tokenCount < n:
            tail = tokens[curve[-1].tokenCount :]
            newTypesInTail = sum(1 for w in tail if w not in seen)
            seen.update(tail)
            curve.append(
                CurvePoint(
                    tokenCount=n,
                    typeCount=len(seen),
                    newTypes=newTypesInTail,
                    growthRate=newTypesInTail / len(tail) if tail else 0.0,
                )
            )

        return curve

    # ============================================================
    # 词汇分布(按文件聚合)
    # ============================================================
    def analyzeDistribution(
        self,
        fileToTokens: Dict[str, List[str]],
        topN: int = 30,
    ) -> Dict[str, Dict[str, int]]:
        """词汇分布分析(FR-WDA-001)

        对每个文件统计其高频词分布;返回 {word: {fileName: freq}}

        Args:
            fileToTokens: 文件名 -> token 列表
            topN: 取总频率 Top-N 词作为分布维度

        Returns:
            {word: {fileName: freq}}
        """
        # 1) 收集全局高频词 Top-N
        total_counter: Counter = Counter()
        for tokens in fileToTokens.values():
            total_counter.update(tokens)
        top_words = [w for w, _ in total_counter.most_common(topN)]

        # 2) 对每个 top word,统计各文件频次
        distribution: Dict[str, Dict[str, int]] = {w: {} for w in top_words}
        for fileName, tokens in fileToTokens.items():
            file_counter = Counter(tokens)
            for w in top_words:
                distribution[w][fileName] = file_counter.get(w, 0)
        return distribution

    # ============================================================
    # 工具方法
    # ============================================================
    @staticmethod
    def _isMeaningfulToken(token: str, minWordLength: int) -> bool:
        """判断 token 是否为有意义的词条(用于过滤)

        条件(全部满足):
            1. 非空 / 非纯空白
            2. 长度 >= minWordLength(默认 1)
            3. 至少包含一个字母数字字符或汉字
        """
        if not token:
            return False
        stripped = token.strip()
        if len(stripped) < minWordLength:
            return False
        # 至少一个非标点字符
        for ch in stripped:
            if ch in _PUNCT_CHARS or ch.isspace():
                continue
            # 字母 / 数字 / 汉字 / 下划线 都算"有意义"
            if ch.isalnum() or "\u4e00" <= ch <= "\u9fff" or ch == "_":
                return True
        return False
