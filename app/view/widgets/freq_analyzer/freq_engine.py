# coding: utf-8
"""
词频分析核心引擎
对标 AntConc 词频统计功能

功能:
- 中英文混合分词(jieba)
- 词频统计(支持 N-gram)
- 文件范围(range)统计
- Zipf 律计算
- 大小写、词长、停用词过滤
"""

from __future__ import annotations

import os
import re
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import jieba
import pandas as pd
from loguru import logger

from app.view.widgets.freq_analyzer.token_cache import backendModelVersion


# 默认中文停用词表(精简版,可由用户扩展)
DEFAULT_STOPWORDS_ZH = {
    "的",
    "了",
    "和",
    "是",
    "在",
    "就",
    "都",
    "而",
    "及",
    "与",
    "或",
    "一个",
    "没有",
    "我们",
    "你们",
    "他们",
    "它们",
    "这个",
    "那个",
    "这样",
    "那样",
    "什么",
    "怎么",
    "为什么",
    "因为",
    "所以",
    "但是",
    "如果",
    "虽然",
    "然后",
    "现在",
    "可以",
    "应该",
    "需要",
    "已经",
    "还",
    "也",
    "又",
    "再",
    "才",
    "只",
    "就是",
    "不是",
    "只是",
}

# 默认英文停用词(精简版)
DEFAULT_STOPWORDS_EN = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "if",
    "of",
    "at",
    "by",
    "for",
    "with",
    "to",
    "in",
    "on",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "this",
    "that",
    "these",
    "those",
    "it",
    "its",
    "i",
    "you",
    "he",
    "she",
    "we",
    "they",
    "them",
    "their",
}


@dataclass
class CleanRule:
    """文本清洗规则集合（用户自定义）

    所有清洗动作在分词之前对原文执行，作为预处理流水线。

    字段说明:
        removeEnglish: 移除所有英文字母（A-Z, a-z）
        removeDigits: 移除所有数字（0-9）
        removePunct: 移除所有 Unicode 标点（含中文标点）
        removeWhitespace: 合并连续空白为单个空格（默认开启）
        removeSpecialSymbols: 移除特殊符号集合（emoji、数学符号、货币等）
        customRemoveList: 用户自定义移除的字符串列表（按字符串字面量删除）
        customRegexList: 用户自定义正则表达式列表（编译失败时跳过并告警）
        replaceMap: 自定义字符串替换字典 {from: to}
        lowercase: 是否在清洗阶段统一转为小写（与下游 caseSensitive 配合）
    """

    removeEnglish: bool = False
    removeDigits: bool = False
    removePunct: bool = False
    removeWhitespace: bool = True
    removeSpecialSymbols: bool = False
    customRemoveList: List[str] = field(default_factory=list)
    customRegexList: List[str] = field(default_factory=list)
    replaceMap: Dict[str, str] = field(default_factory=dict)
    lowercase: bool = False

    def isEnabled(self) -> bool:
        """判断是否存在任意启用的清洗动作"""
        return any(
            [
                self.removeEnglish,
                self.removeDigits,
                self.removePunct,
                self.removeWhitespace,
                self.removeSpecialSymbols,
                bool(self.customRemoveList),
                bool(self.customRegexList),
                bool(self.replaceMap),
                self.lowercase,
            ]
        )


class TextCleaner:
    """文本清洗器：负责在分词前对原始语料执行用户自定义清洗。

    设计要点:
        - 仅做字符/字符串级别的预处理，不做语义替换；
        - 所有正则按需懒编译并缓存，编译失败给出告警而非中断；
        - 流水线顺序固定，避免规则互相干扰。
    """

    # 常用特殊符号集合（emoji / 货币 / 数学 / 控制字符）
    SPECIAL_SYMBOL_PATTERN = re.compile(
        "["
        "\U0001f300-\U0001faff"  # emoji & 表情符号
        "\U00002600-\U000027bf"  # 杂项符号
        "\U0001f000-\U0001f1ff"  # 扑克 / 麻将等
        "\U0001f600-\U0001f64f"  # 表情
        "©®™°±×÷≈≠≤≥∞∑∏√∫"
        "$€£¥¢₽₹₩₺₴"
        "]+",
        flags=re.UNICODE,
    )

    # 所有 Unicode 标点（含中文标点）
    PUNCT_PATTERN = re.compile(r"[\u2000-\u206F\u3000-\u303F\uFF00-\uFFEF]+")

    # 连续空白
    WHITESPACE_PATTERN = re.compile(r"\s+")

    # 英文字母
    ENGLISH_PATTERN = re.compile(r"[A-Za-z]+")

    # 数字
    DIGIT_PATTERN = re.compile(r"\d+")

    def __init__(self, rule: Optional[CleanRule] = None):
        self.rule = rule or CleanRule()
        self._compiledRegex: List[re.Pattern] = []
        self._compileCustomRegex()

    def setRule(self, rule: CleanRule) -> None:
        """替换当前规则并重新编译正则"""
        self.rule = rule
        self._compiledRegex = []
        self._compileCustomRegex()

    def _compileCustomRegex(self) -> None:
        """编译用户自定义正则；失败的项被忽略并打 warning"""
        for pattern in self.rule.customRegexList:
            try:
                self._compiledRegex.append(re.compile(pattern))
            except re.error as e:
                logger.warning(
                    f"[FreqEngine] 自定义正则编译失败，已跳过: {pattern!r} ({e})"
                )

    def clean(self, text: str) -> str:
        """执行清洗流水线"""
        if not text or not self.rule.isEnabled():
            return text or ""

        result = text

        # 1) 自定义字符串替换（先做，便于后续规则复用替换后的内容）
        if self.rule.replaceMap:
            for src, dst in self.rule.replaceMap.items():
                if src:
                    result = result.replace(src, dst)

        # 2) 移除英文
        if self.rule.removeEnglish:
            result = self.ENGLISH_PATTERN.sub(" ", result)

        # 3) 移除数字
        if self.rule.removeDigits:
            result = self.DIGIT_PATTERN.sub(" ", result)

        # 4) 移除标点
        if self.rule.removePunct:
            result = self.PUNCT_PATTERN.sub(" ", result)

        # 5) 移除特殊符号
        if self.rule.removeSpecialSymbols:
            result = self.SPECIAL_SYMBOL_PATTERN.sub(" ", result)

        # 6) 自定义字符串移除（按字面量）
        if self.rule.customRemoveList:
            for token in self.rule.customRemoveList:
                if token:
                    result = result.replace(token, " ")

        # 7) 自定义正则
        for pattern in self._compiledRegex:
            result = pattern.sub(" ", result)

        # 8) 合并空白（默认开启）
        if self.rule.removeWhitespace:
            result = self.WHITESPACE_PATTERN.sub(" ", result).strip()

        # 9) 小写化
        if self.rule.lowercase:
            result = result.lower()

        return result

    def cleanCorpus(self, fileToText: Dict[str, str]) -> Dict[str, str]:
        """对语料库逐文件清洗，返回清洗后的新字典（不修改原对象）"""
        if not self.rule.isEnabled():
            return fileToText
        cleaned: Dict[str, str] = {}
        for name, text in fileToText.items():
            cleaned[name] = self.clean(text or "")
        return cleaned


class TextSegmenter:
    """文本分词器(支持中英文混合)

    中文使用 jieba 分词,英文按单词切分。
    集成 TokenCache 以加速重复分词:
        - 同一文本只分词一次
        - 结果缓存到 SQLite,跨会话持久化

    单字模式(useJieba=False):强制按汉字单字切分。
    """

    # 中文字符范围
    CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
    # 英文单词
    ENGLISH_PATTERN = re.compile(r"[a-zA-Z]+(?:'[a-zA-Z]+)?")

    # 后端常量
    BACKEND_NAME = "jieba"

    def __init__(
        self,
        userDictPath: Optional[str] = None,
        tokenCache=None,
    ):
        """初始化分词器

        Args:
            userDictPath: 用户自定义词典路径(jieba 格式)
            tokenCache: TokenCache 实例,用于缓存分词结果(可选)
        """
        self._userDictPath = userDictPath
        self._tokenCache = tokenCache
        if userDictPath and os.path.exists(userDictPath):
            try:
                jieba.load_userdict(userDictPath)
                logger.info(f"[FreqEngine] 加载用户词典: {userDictPath}")
            except Exception as e:
                logger.warning(f"[FreqEngine] 用户词典加载失败: {e}")

    def setTokenCache(self, tokenCache):
        """设置或替换 token cache

        通常在 CorpusStore 创建后调用,用于共享 cache 实例
        """
        self._tokenCache = tokenCache

    def tokenize(self, text: str, useJieba: bool = True) -> List[str]:
        """分词

        Args:
            text: 原始文本
            useJieba: 兼容旧参数
                      - True  时使用 jieba 分词中文
                      - False 时强制按汉字单字切分(更轻量,用于特殊场景)

        Returns:
            词列表
        """
        if not text:
            return []

        tokens: List[str] = []
        # 按中英文块切分
        lastEnd = 0
        for match in self.CHINESE_PATTERN.finditer(text):
            start, end = match.span()
            # 处理中文字符块之前的英文片段
            if start > lastEnd:
                englishPart = text[lastEnd:start]
                tokens.extend(self._tokenizeEnglish(englishPart))
            chinesePart = text[start:end]
            if useJieba:
                # jieba 分词(带 cache)
                tokens.extend(self._segmentChinese(chinesePart))
            else:
                tokens.extend(list(chinesePart))  # 单字切分
            lastEnd = end

        # 收尾
        if lastEnd < len(text):
            tokens.extend(self._tokenizeEnglish(text[lastEnd:]))

        return [t.strip() for t in tokens if t and t.strip()]

    @staticmethod
    def _tokenizeEnglish(text: str) -> List[str]:
        """英文按单词切分"""
        return TextSegmenter.ENGLISH_PATTERN.findall(text)

    def _segmentChinese(self, text: str) -> List[str]:
        """使用 jieba 分词中文(支持 token cache)

        若设置了 tokenCache,优先从 cache 读取,避免重复分词。
        分词失败时降级到单字切分。
        """
        if not text:
            return []
        try:
            # 尝试从 token cache 获取
            if self._tokenCache is not None:
                modelVer = backendModelVersion(self.BACKEND_NAME)
                tokens = self._tokenCache.getOrCompute(
                    text=text,
                    backendName=self.BACKEND_NAME,
                    modelVersion=modelVer,
                    computeFn=lambda t: self._jiebaCut(t),
                )
                return tokens

            # 无 cache:直接调用
            return self._jiebaCut(text)
        except Exception as e:
            logger.error(f"[TextSegmenter] jieba 分词失败,降级到单字切分: {e}")
            return [c for c in text if c.strip()]

    @staticmethod
    def _jiebaCut(text: str) -> List[str]:
        """调用 jieba 切分中文"""
        import jieba

        return [t for t in jieba.cut(text) if t and t.strip()]

    def currentBackendName(self) -> str:
        """获取当前后端名称(供 UI 显示)"""
        return self.BACKEND_NAME

    @property
    def userDictPath(self) -> Optional[str]:
        return self._userDictPath


class FrequencyAnalyzer:
    """词频分析器"""

    def __init__(
        self,
        minLength: int = 1,
        maxLength: int = 100,
        caseSensitive: bool = False,
        excludeNumbers: bool = True,
        excludePunctuation: bool = True,
        useStopwords: bool = False,
        stopwords: Optional[set] = None,
        useJieba: bool = True,
        userDictPath: Optional[str] = None,
        cleanRule: Optional[CleanRule] = None,
        posTags: Optional[Set[str]] = None,
        posEnabled: bool = False,
        tokenCache=None,
    ):
        """
        Args:
            minLength: 词最短长度
            maxLength: 词最长长度
            caseSensitive: 是否区分大小写
            excludeNumbers: 是否排除纯数字
            excludePunctuation: 是否排除纯标点
            useStopwords: 是否过滤停用词
            stopwords: 停用词集合,None 时使用默认
            useJieba: 是否使用 jieba 中文分词
            userDictPath: 用户词典路径
            cleanRule: 文本清洗规则（None 时不启用清洗）
            posTags: 仅保留指定词性标签的集合,如 {"n","v","a"};None 表示不过滤
            posEnabled: 是否启用词性过滤(必须与 posTags 配合使用)
        """
        self.minLength = minLength
        self.maxLength = maxLength
        self.caseSensitive = caseSensitive
        self.excludeNumbers = excludeNumbers
        self.excludePunctuation = excludeNumbers  # alias
        self.useStopwords = useStopwords
        self.stopwords = (
            stopwords
            if stopwords is not None
            else (DEFAULT_STOPWORDS_ZH | DEFAULT_STOPWORDS_EN)
        )
        self.segmenter = TextSegmenter(userDictPath)
        self.useJieba = useJieba
        self.cleaner = TextCleaner(cleanRule or CleanRule())
        # 词性过滤配置
        self.posEnabled: bool = bool(posEnabled and posTags)
        self.posTags: Set[str] = set(posTags) if posTags else set()

    def setCleanRule(self, rule: CleanRule) -> None:
        """运行时更新清洗规则（重新编译正则缓存）"""
        self.cleaner.setRule(rule)

    def setStopwords(self, stopwords: Optional[set]) -> None:
        """运行时替换停用词集合。

        Args:
            stopwords: 新的停用词集合；传 None 表示使用默认中英文停用词。
        """
        if stopwords is None:
            self.stopwords = DEFAULT_STOPWORDS_ZH | DEFAULT_STOPWORDS_EN
        else:
            # 拷贝一份以避免外部 mutate
            self.stopwords = set(stopwords)

    def getStopwords(self) -> set:
        """返回当前停用词集合的拷贝（外部不可直接修改内部状态）。"""
        return set(self.stopwords)

    def setPosTags(
        self, posTags: Optional[Set[str]], enabled: Optional[bool] = None
    ) -> None:
        """运行时更新词性过滤配置。

        Args:
            posTags: 仅保留的词性标签集合;None 或空表示关闭过滤
            enabled: 显式开关;若为 None 则根据 posTags 是否非空自动判断
        """
        newSet = set(posTags) if posTags else set()
        self.posTags = newSet
        if enabled is None:
            self.posEnabled = bool(newSet)
        else:
            self.posEnabled = bool(enabled and newSet)

    def getPosTags(self) -> Set[str]:
        """返回当前生效的词性标签集合(空集合表示未启用)。"""
        return set(self.posTags)

    def _precleanCorpus(self, fileToText: Dict[str, str]) -> Dict[str, str]:
        """对语料库执行清洗预处理"""
        return self.cleaner.cleanCorpus(fileToText)

    def _normalize(self, token: str) -> str:
        if not self.caseSensitive:
            token = token.lower()
        return token

    def _isValidToken(self, token: str) -> bool:
        """检查是否满足所有过滤条件"""
        if not token:
            return False
        if len(token) < self.minLength or len(token) > self.maxLength:
            return False
        if self.excludeNumbers and token.isdigit():
            return False
        if not token.isalnum():
            return False
        if self.useStopwords and token.lower() in self.stopwords:
            return False
        return True

    def analyzeTexts(
        self,
        texts: List[str],
        sources: Optional[List[str]] = None,
        minFreq: int = 1,
    ) -> pd.DataFrame:
        """分析多段文本

        Args:
            texts: 文本列表(每条 = 一段语料,可对应一文件或一行)
            sources: 每个文本对应的来源标识(如文件名),用于 Range 统计
            minFreq: 最低频次阈值;只保留出现次数 >= minFreq 的词,默认 1(=不过滤)

        Returns:
            DataFrame: Rank / Word / Freq / Range / Files / Pct
        """
        minFreq = max(1, int(minFreq))  # 至少为 1

        # 1. 分词
        # 若启用词性过滤,改用 posTagBatch 同时获取 word 与 tag,
        # 仅保留 posTags 命中的 token;
        # 否则沿用原 segmenter.tokenize 流程(更快)
        tokenLists: List[List[str]] = []
        if self.posEnabled and self.posTags:
            posResults = posTagBatch(texts)
            for tagged in posResults:
                kept: List[str] = []
                for word, tag in tagged:
                    if not word or not tag:
                        continue
                    if tag not in self.posTags:
                        continue
                    normalized = self._normalize(word)
                    if not self._isValidToken(normalized):
                        continue
                    kept.append(normalized)
                tokenLists.append(kept)
        else:
            for text in texts:
                tokens = self.segmenter.tokenize(text or "", useJieba=self.useJieba)
                normalized = [
                    self._normalize(t)
                    for t in tokens
                    if self._isValidToken(self._normalize(t))
                ]
                tokenLists.append(normalized)

        # 2. 全局词频
        globalCounter: Counter = Counter()
        for tokens in tokenLists:
            globalCounter.update(tokens)

        # 3. Range(词出现在多少 source 中)
        sourceCount = len(texts)
        wordToSources: Dict[str, set] = defaultdict(set)
        if sources and len(sources) == len(tokenLists):
            for tokens, src in zip(tokenLists, sources):
                for t in set(tokens):
                    wordToSources[t].add(src)

        # 4. 按 minFreq 过滤 + 拼装结果
        # 过滤后重新计算 total,以保证 Pct 仍以"过滤后语料"为分母
        if minFreq > 1:
            filteredCounter = Counter(
                {w: f for w, f in globalCounter.items() if f >= minFreq}
            )
        else:
            filteredCounter = globalCounter
        total = sum(filteredCounter.values())

        rows = []
        for rank, (word, freq) in enumerate(
            sorted(filteredCounter.items(), key=lambda x: (-x[1], x[0])), 1
        ):
            sources_ = wordToSources.get(word, set())
            rangeVal = len(sources_)
            rows.append(
                {
                    "Rank": rank,
                    "Word": word,
                    "Freq": freq,
                    "Range": rangeVal,
                    "Files": ", ".join(sorted(sources_)) if sources else "",
                    "Pct": (freq / total * 100) if total > 0 else 0.0,
                }
            )
        df = pd.DataFrame(rows)
        if not df.empty:
                # Zipf 律参考值(Rank × Freq):
                #   严格 Zipf 律 freq ∝ 1/Rank^α(α≈1.0),即 log Freq ≈ C - α·log Rank;
                #   若 α=1,则 Rank × Freq ≈ const(常数 C)。此处直接给出该乘积,
                #   作为「是否符合 Zipf 律」的快速诊断指标 ——
                #   若该列近似常数,说明语料接近理想 Zipf 分布。
                #   严格的 α 估计需对 (log Rank, log Freq) 做线性回归,
                #   见 computeZipf()。
                df["Zipf"] = df["Freq"] * df["Rank"]
        return df

    def analyzeCorpus(
        self,
        fileToText: Dict[str, str],
        minFreq: int = 1,
    ) -> pd.DataFrame:
        """分析语料库(多文件)

        Args:
            fileToText: 文件名 -> 全文
            minFreq: 最低频次阈值,只保留出现次数 >= minFreq 的词,默认 1(=不过滤)

        Returns:
            词频 DataFrame
        """
        cleaned = self._precleanCorpus(fileToText)
        fileNames = list(cleaned.keys())
        texts = [cleaned[f] for f in fileNames]
        return self.analyzeTexts(texts, sources=fileNames, minFreq=minFreq)

    def computeZipf(self, df: pd.DataFrame) -> pd.DataFrame:
            """计算 Zipf 律参考列

            严格 Zipf 律(Zipf 1935 / 1949):
                Freq ∝ Rank^(-α),即 log₁₀ Freq = log₁₀ C - α · log₁₀ Rank
            其中 α≈1.0 为理想 Zipf 分布(英语/汉语语料经验值 0.9~1.2,
            Powers 1998 "Applications and explanations of Zipf's law")。

            本方法输出:
                LogRank: log₁₀(Rank)
                LogFreq: log₁₀(Freq)
                ZipfAlpha: 对 (LogRank, LogFreq) 做 OLS 线性回归的斜率取负
                    —— 即 α 的最小二乘估计;若 |α-1| 较小则语料符合 Zipf 律
                R2: 拟合优度(R²),越接近 1 表示越符合 Zipf 律

            Args:
                df: analyzeCorpus 输出

            Returns:
                加上 LogRank / LogFreq / ZipfAlpha / R2 列的 DataFrame
            """
            if df.empty:
                return df
            df = df.copy()
            df["LogRank"] = df["Rank"].apply(lambda r: math.log10(r) if r > 0 else 0)
            df["LogFreq"] = df["Freq"].apply(lambda f: math.log10(f) if f > 0 else 0)
            # OLS 拟合 log₁₀ Freq = b - α · log₁₀ Rank
            try:
                valid = df[(df["LogRank"] > 0) & (df["LogFreq"] > 0)]
                if len(valid) >= 2:
                    xs = valid["LogRank"].values
                    ys = valid["LogFreq"].values
                    n = len(xs)
                    meanX = xs.mean()
                    meanY = ys.mean()
                    num = ((xs - meanX) * (ys - meanY)).sum()
                    den = ((xs - meanX) ** 2).sum()
                    if den > 0:
                        slope = num / den  # 斜率 = -α
                        alpha = -slope
                        # R² = 1 - SS_res/SS_tot
                        ssTot = ((ys - meanY) ** 2).sum()
                        intercept = meanY - slope * meanX
                        ssRes = ((ys - (slope * xs + intercept)) ** 2).sum()
                        r2 = 1 - ssRes / ssTot if ssTot > 0 else 0.0
                        df["ZipfAlpha"] = alpha
                        df["ZipfR2"] = r2
                    else:
                        df["ZipfAlpha"] = float("nan")
                        df["ZipfR2"] = float("nan")
                else:
                    df["ZipfAlpha"] = float("nan")
                    df["ZipfR2"] = float("nan")
            except Exception:
                df["ZipfAlpha"] = float("nan")
                df["ZipfR2"] = float("nan")
            return df

    def generateNgrams(self, tokens: List[str], n: int = 2) -> List[Tuple[str, ...]]:
        """生成 N-gram

        Args:
            tokens: 分词结果
            n: n-gram 阶数

        Returns:
            n-gram 元组列表
        """
        if len(tokens) < n:
            return []
        return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]

    def analyzeNgrams(
        self,
        fileToText: Dict[str, str],
        n: int = 2,
    ) -> pd.DataFrame:
        """N-gram 频率统计

        Args:
            fileToText: 文件名 -> 全文
            n: 2=bigram, 3=trigram

        Returns:
            DataFrame: Rank / Ngram / Freq / Range / Pct
        """
        globalCounter: Counter = Counter()
        ngramToSources: Dict[Tuple[str, ...], set] = defaultdict(set)

        cleaned = self._precleanCorpus(fileToText)
        for fileName, text in cleaned.items():
            tokens = self.segmenter.tokenize(text or "", useJieba=self.useJieba)
            normalized = [
                self._normalize(t)
                for t in tokens
                if self._isValidToken(self._normalize(t))
            ]
            ngrams = self.generateNgrams(normalized, n=n)
            globalCounter.update(ngrams)
            for ng in set(ngrams):
                ngramToSources[ng].add(fileName)

        total = sum(globalCounter.values())
        rows = []
        for rank, (ng, freq) in enumerate(
            sorted(globalCounter.items(), key=lambda x: (-x[1], x[0])), 1
        ):
            sources_ = ngramToSources[ng]
            rows.append(
                {
                    "Rank": rank,
                    "Ngram": " ".join(ng),
                    "Freq": freq,
                    "Range": len(sources_),
                    "Files": ", ".join(sorted(sources_)),
                    "Pct": (freq / total * 100) if total > 0 else 0.0,
                }
            )
        return pd.DataFrame(rows)


def loadExcelColumn(filePath: str, column: str = None, headerRow: int = 0) -> str:
    """从 Excel 读取指定列并合并为单文本

    Args:
        filePath: Excel 文件路径
        column: 列名,None 时取第一列
        headerRow: 表头行索引

    Returns:
        拼接后的纯文本(行间用换行)

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 文件不是有效的 Excel 格式
    """
    import os as _os

    if not _os.path.exists(filePath):
        raise FileNotFoundError(f"文件不存在：{filePath}")

    # 规范化路径(支持 .xIsx / .Xlsx 等大小写变体)
    base, ext = _os.path.splitext(filePath)
    if ext.lower() in (".xlsx", ".xls"):
        # 大小写归一化:统一用 .xlsx 重试
        normalized = base + ".xlsx"
        if not _os.path.exists(normalized) and ext != ".xlsx":
            # 尝试用原始大小写读取,read_excel 会处理
            pass
    try:
        df = pd.read_excel(filePath, engine="openpyxl", header=headerRow, dtype=str)
    except Exception as e1:
        try:
            df = pd.read_excel(filePath, header=headerRow, dtype=str)
        except Exception as e2:
            raise ValueError(
                f"无法读取 Excel 文件：{_os.path.basename(filePath)}\n"
                f"引擎 openpyxl 失败：{e1}\n"
                f"默认引擎失败：{e2}"
            ) from e2

    if df.empty:
        return ""

    if column is None or column not in df.columns:
        column = df.columns[0]

    series = df[column].astype(str).fillna("")
    return "\n".join(line for line in series if line and line != "nan")


def listExcelColumns(filePath: str, headerRow: int = 0) -> List[str]:
    """读取 Excel 文件的列名列表

    Args:
        filePath: Excel 文件路径
        headerRow: 表头行索引

    Returns:
        列名列表（按表中出现的顺序）
    """
    import os as _os

    if not _os.path.exists(filePath):
        raise FileNotFoundError(f"文件不存在：{filePath}")
    try:
        df = pd.read_excel(
            filePath, engine="openpyxl", header=headerRow, dtype=str, nrows=0
        )
    except Exception as e1:
        try:
            df = pd.read_excel(filePath, header=headerRow, dtype=str, nrows=0)
        except Exception as e2:
            raise ValueError(f"无法读取 Excel 文件 {filePath}：{e1}; fallback: {e2}")
    return [str(c) for c in df.columns]


def loadTextFile(filePath: str) -> str:
    """读取纯文本文件"""
    encodings = ["utf-8", "gbk", "utf-16", "latin-1"]
    for enc in encodings:
        try:
            with open(filePath, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    # fallback
    with open(filePath, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def loadStopwordsFromFile(filePath: str) -> List[str]:
    """从 TXT 文件加载停用词列表,每行一个词。

    行为:
        - 自动识别 UTF-8 / UTF-8 with BOM / GBK / UTF-16 / Latin-1 编码
        - 跳过空行
        - 去除每行首尾空白
        - 跳过 `#` 开头的注释行
        - 内部去重(保持首次出现顺序)
        - 大小写:不强制转换,保留原始大小写(由 FrequencyAnalyzer 在比较时归一化)

    Args:
        filePath: 停用词文件路径
    Returns:
        去重后的停用词字符串列表
    """
    if not filePath or not os.path.exists(filePath):
        raise FileNotFoundError(f"停用词文件不存在: {filePath}")

    encodings = ["utf-8-sig", "utf-8", "gbk", "utf-16", "latin-1"]
    text: Optional[str] = None
    lastErr: Optional[Exception] = None
    for enc in encodings:
        try:
            with open(filePath, "r", encoding=enc) as f:
                text = f.read()
            break
        except UnicodeDecodeError as e:
            lastErr = e
            continue
    if text is None:
        # 兜底:忽略错误
        with open(filePath, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

    seen: set = set()
    result: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 去除 BOM 等不可见字符
        line = line.replace("\ufeff", "")
        if line and line not in seen:
            seen.add(line)
            result.append(line)
    return result


def parseStopwordsFromText(text: str) -> List[str]:
    """从文本字符串解析停用词(用于弹窗内可编辑文本框)。"""
    seen: set = set()
    result: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip().replace("\ufeff", "")
        if not line or line.startswith("#"):
            continue
        if line not in seen:
            seen.add(line)
            result.append(line)
    return result


def defaultStopwords() -> List[str]:
    """返回合并后的默认中英文停用词列表(按集合迭代顺序,无重复)。"""
    seen: set = set()
    result: List[str] = []
    for w in DEFAULT_STOPWORDS_ZH | DEFAULT_STOPWORDS_EN:
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result


def saveStopwordsToFile(filePath: str, words: List[str]) -> None:
    """把停用词列表写回 TXT 文件,每行一个词,UTF-8 编码。"""
    seen: set = set()
    with open(filePath, "w", encoding="utf-8") as f:
        f.write("# 停用词列表 (UTF-8,每行一个词)\n")
        f.write(f"# 共 {len(words)} 个\n")
        for w in words:
            if not w or w in seen:
                continue
            seen.add(w)
            f.write(w + "\n")


# ===========================================================================
# 词性标注 (POS Tagging) 模块
# ===========================================================================
# 使用 jieba.posseg 进行词性标注
import jieba.posseg as _jieba_posseg  # jieba.posseg 始终可用

# 中文常见词性集合（按 ICTCLAS/863 词性标注集）
# 来源: jieba.posseg 共用的标注体系
POS_CATEGORIES: Dict[str, Dict[str, str]] = {
    # key: 选项内部代号; value: {tag, label_zh, description}
    "n": {
        "tags": ("n", "nr", "ns", "nt", "nx", "nz"),
        "label_zh": "名词",
        "description": "noun - 包括普通名词/人名/地名/机构名/其他专名",
    },
    "v": {
        "tags": ("v", "vd", "vn"),
        "label_zh": "动词",
        "description": "verb - 包括普通动词/动副词/名动词",
    },
    "a": {
        "tags": ("a", "ad", "an"),
        "label_zh": "形容词",
        "description": "adjective - 包括形容词/副形词/名形词",
    },
    "d": {
        "tags": ("d",),
        "label_zh": "副词",
        "description": "adverb",
    },
    "r": {
        "tags": ("r", "rr"),
        "label_zh": "代词",
        "description": "pronoun - 包括人称代词/指示代词",
    },
    "m": {
        "tags": ("m", "q"),
        "label_zh": "数词/量词",
        "description": "numeral / quantifier",
    },
    "p": {
        "tags": ("p", "pba", "pbei"),
        "label_zh": "介词",
        "description": "preposition - 包括介词/把/被",
    },
    "c": {
        "tags": ("c", "cc"),
        "label_zh": "连词",
        "description": "conjunction - 包括并列连词/从属连词",
    },
    "u": {
        "tags": ("u", "uz", "ug", "uj"),
        "label_zh": "助词",
        "description": "auxiliary - 的/地/得/了/着/过等",
    },
    "w": {
        "tags": ("w",),
        "label_zh": "标点",
        "description": "punctuation",
    },
}


def posTagCategories() -> List[Dict[str, str]]:
    """返回词性选项列表,供 UI 渲染多选框。

    Returns:
        [{"key": "n", "label": "名词", "description": "..."}, ...]
    """
    return [
        {
            "key": key,
            "label": info["label_zh"],
            "description": info["description"],
        }
        for key, info in POS_CATEGORIES.items()
    ]


def _posTagForCategory(categoryKey: str) -> Tuple[str, ...]:
    """获取某词性类别对应的 ICTCLAS 短标签集合。"""
    return POS_CATEGORIES.get(categoryKey, {}).get("tags", ())


def posTagsFilter(
    enabledCategories: Optional[List[str]],
) -> Optional[Set[str]]:
    """根据用户勾选的词性类别,生成统一的短标签集合。

    Args:
        enabledCategories: 用户在 UI 中勾选的类别 key 列表,如 ["n", "v"]。
                           None 或空列表表示「不过滤」(返回 None)。
    Returns:
        短标签集合,如 {"n","nr","ns","nt","v","vd"};若不过滤返回 None。
    """
    if not enabledCategories:
        return None
    result: Set[str] = set()
    for key in enabledCategories:
        result.update(_posTagForCategory(key))
    return result or None


def _tagWithJieba(text: str) -> List[Tuple[str, str]]:
    """使用 jieba.posseg 进行词性标注。"""
    return [(w.word, w.flag) for w in _jieba_posseg.cut(text or "")]


def posTag(text: str) -> List[Tuple[str, str]]:
    """对文本进行词性标注,返回 (词, 词性) 列表。

    使用 jieba.posseg 进行标注。

    Args:
        text: 输入文本(任意长度)
    Returns:
        List of (word, pos_tag);例:[("我", "r"), ("爱", "v"), ("中国", "ns")]
    """
    if not text:
        return []
    return _tagWithJieba(text)


def posTagBatch(texts: List[str]) -> List[List[Tuple[str, str]]]:
    """批量词性标注。

    Args:
        texts: 文本列表
    Returns:
        每个文本对应的 [(词, 词性), ...] 列表
    """
    if not texts:
        return []
    return [_tagWithJieba(t) for t in texts]


def availablePosBackend() -> str:
    """返回当前可用的 POS 后端名称('jieba.posseg')。"""
    return "jieba.posseg"


def loadDocxFile(filePath: str) -> str:
    """读取 Word .docx 文件的纯文本内容（包含所有段落与表格，按文档顺序拼接）。

    要求：pip install python-docx

    行为:
        - 段落之间用换行符 \\n 分隔
        - 表格单元格之间用制表符 \\t、单元格之间用换行符 \\n
        - 段落与表格按文档 body 中的顺序交错保留
    """
    try:
        from docx import Document  # type: ignore
    except ImportError as e:
        raise ImportError(
            "读取 .docx 文件需要安装 python-docx（pip install python-docx）"
        ) from e

    doc = Document(filePath)
    chunks: List[str] = []

    # 遍历 body 中的所有块（段落 + 表格），按文档顺序拼接
    try:
        from docx.oxml.ns import qn  # type: ignore

        body = doc.element.body
        for child in body.iterchildren():
            tag = child.tag
            if tag == qn("w:p"):
                # 段落：提取每个 <w:t> 的纯文本
                text = "".join(t.text or "" for t in child.iter(qn("w:t")))
                chunks.append(text)
            elif tag == qn("w:tbl"):
                # 表格：每行用 \\n 拼接，单元格之间用 \\t 分隔
                rows_text: List[str] = []
                for row in child.iter(qn("w:tr")):
                    cells_text: List[str] = []
                    for cell in row.iter(qn("w:tc")):
                        cell_text = "".join(t.text or "" for t in cell.iter(qn("w:t")))
                        cells_text.append(cell_text)
                    rows_text.append("\t".join(cells_text))
                chunks.append("\n".join(rows_text))
    except Exception:
        # fallback：只取段落（忽略表格）
        for para in doc.paragraphs:
            chunks.append(para.text or "")

    text = "\n".join(chunks).strip()
    if not text:
        raise ValueError(
            "该 .docx 文件未包含可提取的文本（可能为图片型 PDF / 扫描件 / 加密文档）"
        )
    return text
