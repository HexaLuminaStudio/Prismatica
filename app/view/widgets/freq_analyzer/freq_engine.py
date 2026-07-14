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
    """文本分词器(支持中英文混合)"""

    # 中文字符范围
    CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
    # 英文单词
    ENGLISH_PATTERN = re.compile(r"[a-zA-Z]+(?:'[a-zA-Z]+)?")

    def __init__(self, userDictPath: Optional[str] = None):
        """初始化分词器

        Args:
            userDictPath: 用户自定义词典路径(jieba 格式)
        """
        if userDictPath and os.path.exists(userDictPath):
            try:
                jieba.load_userdict(userDictPath)
                logger.info(f"[FreqEngine] 加载用户词典: {userDictPath}")
            except Exception as e:
                logger.warning(f"[FreqEngine] 用户词典加载失败: {e}")

    def tokenize(self, text: str, useJieba: bool = True) -> List[str]:
        """分词

        Args:
            text: 原始文本
            useJieba: 是否对中文使用 jieba 精确分词。
                      False 时按汉字单字切分（更轻量）。

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
                tokens.extend(jieba.cut(chinesePart))
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

    def setCleanRule(self, rule: CleanRule) -> None:
        """运行时更新清洗规则（重新编译正则缓存）"""
        self.cleaner.setRule(rule)

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
        self, texts: List[str], sources: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """分析多段文本

        Args:
            texts: 文本列表(每条 = 一段语料,可对应一文件或一行)
            sources: 每个文本对应的来源标识(如文件名),用于 Range 统计

        Returns:
            DataFrame: Rank / Word / Freq / Range / Files / Pct
        """
        # 1. 分词
        tokenLists: List[List[str]] = []
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

        # 4. 拼装结果
        total = sum(globalCounter.values())
        rows = []
        for rank, (word, freq) in enumerate(
            sorted(globalCounter.items(), key=lambda x: (-x[1], x[0])), 1
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
            df["Zipf"] = df["Freq"] * df["Rank"]  # Zipf 律参考值
        return df

    def analyzeCorpus(self, fileToText: Dict[str, str]) -> pd.DataFrame:
        """分析语料库(多文件)

        Args:
            fileToText: 文件名 -> 全文

        Returns:
            词频 DataFrame
        """
        cleaned = self._precleanCorpus(fileToText)
        fileNames = list(cleaned.keys())
        texts = [cleaned[f] for f in fileNames]
        return self.analyzeTexts(texts, sources=fileNames)

    def computeZipf(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算 Zipf 律参考列: ZipfFreq = Rank * Freq

        Args:
            df: analyzeCorpus 输出

        Returns:
            加上 Zipf / LogRank / LogFreq 列的 DataFrame
        """
        if df.empty:
            return df
        df = df.copy()
        df["LogRank"] = df["Rank"].apply(lambda r: math.log10(r) if r > 0 else 0)
        df["LogFreq"] = df["Freq"].apply(lambda f: math.log10(f) if f > 0 else 0)
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
        df = pd.read_excel(filePath, engine="openpyxl", header=headerRow, dtype=str, nrows=0)
    except Exception as e1:
        try:
            df = pd.read_excel(filePath, header=headerRow, dtype=str, nrows=0)
        except Exception as e2:
            raise ValueError(
                f"无法读取 Excel 文件 {filePath}：{e1}; fallback: {e2}"
            )
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
