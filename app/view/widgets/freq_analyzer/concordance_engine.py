# coding: utf-8
"""
语境分析（Concordance / KWIC）核心引擎

需求文档:
    test/6D-CorpusClient_需求文档_v3.md §2.4.2

功能:
    - 节点词检索（支持单字/多字/正则）
    - 左右语境提取（左右可独立配置宽度）
    - 4 种排序：原始语序 / 左 1 词 / 右 1 词 / 节点词搭配词
    - 二次检索（在当前结果中再次过滤）
    - 随机抽样
    - 上下文扩展（FR-KWC-006）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import jieba

from app.view.widgets.freq_analyzer.freq_engine import TextSegmenter


class SortMode(Enum):
    """索引行排序方式（FR-KWC-003）"""

    ORIGINAL = "original"  # 原始语序
    LEFT_FIRST = "left"  # 按左侧第一个词
    RIGHT_FIRST = "right"  # 按右侧第一个词
    NODE_COLLOCATE = "collocate"  # 按节点词搭配词（左+节点+右）


@dataclass
class KwicHit:
    """单条 KWIC 命中（索引行）

    Fields:
        leftContext: 左侧 token 列表
        node: 节点词 token 列表（用于支持多字节点词）
        rightContext: 右侧 token 列表
        sourceFile: 所在文件名
        tokenIndex: 节点词在整个文件分词流中的起始 token 索引
        nodeLineIndex: 节点词所在原始行号（0-based）；用于限制跨行拼接
    """

    leftContext: List[str] = field(default_factory=list)
    node: List[str] = field(default_factory=list)
    rightContext: List[str] = field(default_factory=list)
    sourceFile: str = ""
    tokenIndex: int = 0
    nodeLineIndex: int = 0

    @property
    def leftText(self) -> str:
        return " ".join(self.leftContext)

    @property
    def nodeText(self) -> str:
        return " ".join(self.node)

    @property
    def rightText(self) -> str:
        return " ".join(self.rightContext)

    @property
    def collocateText(self) -> str:
        """节点词搭配词：左1 + 节点 + 右1，用于排序"""

        parts: List[str] = []
        if self.leftContext:
            parts.append(self.leftContext[-1])
        parts.extend(self.node)
        if self.rightContext:
            parts.append(self.rightContext[0])
        return " ".join(parts)


@dataclass
class ConcordanceResult:
    """KWIC 分析结果"""

    hits: List[KwicHit] = field(default_factory=list)
    totalMatches: int = 0
    searchWord: str = ""
    corpusName: str = ""


class ConcordanceEngine:
    """语境分析（KWIC）引擎"""

    def __init__(
        self,
        useJieba: bool = True,
        caseSensitive: bool = False,
    ):
        """Args:
        useJieba: 是否使用 jieba 中文分词（False 时按汉字单字切分）
        caseSensitive: 是否区分大小写
        """
        self.useJieba = useJieba
        self.caseSensitive = caseSensitive
        self.segmenter = TextSegmenter()

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def search(
        self,
        fileToText: Dict[str, str],
        searchWord: str,
        leftWidth: int = 10,
        rightWidth: int = 10,
        isRegex: bool = False,
        sortMode: SortMode = SortMode.ORIGINAL,
        secondaryWord: Optional[str] = None,
        secondaryRegex: bool = False,
        secondaryOffset: int = 0,
        sampleLimit: int = 100,
        sampleRandom: bool = True,
    ) -> ConcordanceResult:
        """执行 KWIC 检索

        Args:
            fileToText: 文件名 -> 全文
            searchWord: 节点词/检索词
            leftWidth / rightWidth: 上下文宽度（词数）
            isRegex: searchWord 是否按正则解析
            sortMode: 排序方式
            secondaryWord / secondaryRegex: 二次检索词
            secondaryOffset: 二次检索相对节点词的位置
                0 = 仅匹配节点词本身；正数 = 节点词右侧第 N 词；负数 = 左侧第 N 词
            sampleLimit: 抽样上限（默认 100）
            sampleRandom: True=随机抽样，False=取前 N 条

        Returns:
            ConcordanceResult
        """
        if not searchWord:
            return ConcordanceResult(searchWord=searchWord)

        allHits: List[KwicHit] = []
        corpusName = self._joinCorpusName(fileToText)

        for fileName, text in fileToText.items():
            # 按行分句：每个 token 携带其所在行号 (tokenText, lineIndex)
            tokensWithLine = self._tokenizeLines(text or "")
            tokens = [t for t, _ in tokensWithLine]
            normalized = [self._normalize(t) for t in tokens]
            for hit in self._scanFile(
                fileName=fileName,
                tokens=tokens,
                tokensWithLine=tokensWithLine,
                normalized=normalized,
                searchWord=searchWord,
                leftWidth=leftWidth,
                rightWidth=rightWidth,
                isRegex=isRegex,
            ):
                allHits.append(hit)

        # 二次检索（FR-KWC-004）
        if secondaryWord:
            allHits = self._filterSecondary(
                hits=allHits,
                secondaryWord=secondaryWord,
                isRegex=secondaryRegex,
                offset=secondaryOffset,
            )

        totalMatches = len(allHits)

        # 排序（FR-KWC-003）
        allHits = self._sortHits(allHits, sortMode)

        # 抽样（FR-KWC-005）
        if sampleLimit > 0 and len(allHits) > sampleLimit:
            if sampleRandom:
                import random

                allHits = random.sample(allHits, sampleLimit)
            else:
                allHits = allHits[:sampleLimit]

        return ConcordanceResult(
            hits=allHits,
            totalMatches=totalMatches,
            searchWord=searchWord,
            corpusName=corpusName,
        )

    def expandContext(
        self,
        hit: KwicHit,
        fullTokensByFile: Dict[str, List[str]],
        expandWidth: int = 100,
    ) -> Tuple[List[str], List[str]]:
        """扩展上下文（FR-KWC-006）

        Args:
            hit: 选中的索引行
            fullTokensByFile: 文件名 -> 全分词流（或带行号的 (tokens, lineMap) 元组）
            expandWidth: 节点词前后各取多少词

        Returns:
            (expandedLeft, expandedRight) token 列表
        """
        entry = fullTokensByFile.get(hit.sourceFile)
        if isinstance(entry, tuple) and len(entry) == 2:
            tokens, lineMap = entry
        else:
            tokens = entry or []
            lineMap = None

        nodeStart = hit.tokenIndex
        nodeEnd = nodeStart + len(hit.node)
        n = len(tokens)

        # 默认：按 expandWidth 取
        leftStart = max(0, nodeStart - expandWidth)
        rightEnd = min(n, nodeEnd + expandWidth)

        # 行号限制：避免跨行拼接相邻句子
        if lineMap is not None and hit.nodeLineIndex is not None:
            targetLine = hit.nodeLineIndex
            # 左边：向右逼近至同行的 token
            while leftStart < nodeStart and lineMap[leftStart] != targetLine:
                leftStart += 1
            # 右边：向左逼近至同行的 token
            while rightEnd > nodeEnd and lineMap[rightEnd - 1] != targetLine:
                rightEnd -= 1

        return tokens[leftStart:nodeStart], tokens[nodeEnd:rightEnd]

    def buildContextMap(self, text: str) -> Tuple[List[str], List[int]]:
        """同时返回全分词流与每个 token 所在行号，便于 expandContext 按行号裁剪。

        Returns:
            (tokens, lineIndexPerToken)
        """
        pairs = self._tokenizeLines(text or "")
        tokens = [t for t, _ in pairs]
        lineMap = [ln for _, ln in pairs]
        return tokens, lineMap

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        return [
            t
            for t in self.segmenter.tokenize(text, useJieba=self.useJieba)
            if t and t.strip()
        ]

    def _tokenizeLines(self, text: str) -> List[Tuple[str, int]]:
        """按行分句后逐行分词，返回 (token, lineIndex) 列表

        这样扫描时可以根据 lineIndex 限制上下文窗口不跨行，
        避免相邻两行被错误地拼成一句话分析。
        """
        if not text:
            return []
        result: List[Tuple[str, int]] = []
        # 用 splitlines() 同时处理 \n \r\n \r
        lines = text.splitlines()
        for lineIdx, line in enumerate(lines):
            if not line or not line.strip():
                continue
            tokens = [
                t
                for t in self.segmenter.tokenize(line, useJieba=self.useJieba)
                if t and t.strip()
            ]
            for t in tokens:
                result.append((t, lineIdx))
        return result

    @staticmethod
    def _lineStartIndex(
        tokensWithLine: List[Tuple[str, int]],
        nodeStart: int,
        maxWidth: int,
        targetLine: int,
    ) -> int:
        """从 nodeStart 向左扫描，最多取 maxWidth 个 token，但不允许跨行。

        Returns:
            上下文左端 token 索引（闭区间）
        """
        start = nodeStart
        taken = 0
        for k in range(nodeStart - 1, -1, -1):
            if taken >= maxWidth:
                break
            _, ln = tokensWithLine[k]
            if ln != targetLine:
                break
            start = k
            taken += 1
        return start

    @staticmethod
    def _lineEndIndex(
        tokensWithLine: List[Tuple[str, int]],
        nodeEnd: int,
        maxWidth: int,
        targetLine: int,
    ) -> int:
        """从 nodeEnd 向右扫描，最多取 maxWidth 个 token，但不允许跨行。"""
        end = nodeEnd
        taken = 0
        total = len(tokensWithLine)
        for k in range(nodeEnd, total):
            if taken >= maxWidth:
                break
            _, ln = tokensWithLine[k]
            if ln != targetLine:
                break
            end = k + 1
            taken += 1
        return end

    def _normalize(self, token: str) -> str:
        return token if self.caseSensitive else token.lower()

    def _scanFile(
        self,
        fileName: str,
        tokens: List[str],
        tokensWithLine: List[Tuple[str, int]],
        normalized: List[str],
        searchWord: str,
        leftWidth: int,
        rightWidth: int,
        isRegex: bool,
    ):
        """扫描单个文件，产出 KwicHit 生成器

        上下文窗口严格限制在节点词所在行内，避免跨行拼接相邻句子。
        """
        targetNorm = searchWord if self.caseSensitive else searchWord.lower()

        # 预编译正则
        nodeRegex = None
        if isRegex:
            try:
                nodeRegex = re.compile(targetNorm)
            except re.error:
                nodeRegex = None

        # 中文节点词的字面匹配（更准确）
        nodeLiteral = searchWord if self.caseSensitive else searchWord.lower()

        n = len(tokens)
        i = 0
        while i < n:
            # 默认按字面匹配节点词
            nodeTokens: List[str] = []
            nodeLen = 0

            if nodeRegex is not None:
                # 用整 token 串做正则匹配
                if nodeRegex.fullmatch(normalized[i]):
                    nodeTokens = [tokens[i]]
                    nodeLen = 1
            elif self._matchNode(normalized, i, nodeLiteral):
                # 计算节点词在分词流中的长度（用于多字节点词）
                nodeLen = self._nodeLength(
                    tokens=tokens,
                    normalized=normalized,
                    start=i,
                    target=nodeLiteral,
                )
                if nodeLen > 0:
                    nodeTokens = tokens[i : i + nodeLen]

            if nodeLen > 0:
                nodeLineIdx = tokensWithLine[i][1]
                # 上下文窗口只在节点词所在行内取值
                leftStart = self._lineStartIndex(
                    tokensWithLine, i, leftWidth, nodeLineIdx
                )
                rightEnd = self._lineEndIndex(
                    tokensWithLine, i + nodeLen, rightWidth, nodeLineIdx
                )
                yield KwicHit(
                    leftContext=tokens[leftStart:i],
                    node=nodeTokens,
                    rightContext=tokens[i + nodeLen : rightEnd],
                    sourceFile=fileName,
                    tokenIndex=i,
                    nodeLineIndex=nodeLineIdx,
                )
                i += nodeLen
            else:
                i += 1

    def _matchNode(
        self,
        normalized: List[str],
        start: int,
        target: str,
    ) -> bool:
        """判断从 start 开始是否能匹配 target 作为节点词。

        支持多字节点词（例如"机器学习"）。匹配策略：
        1. 优先单 token 精确匹配（jieba 词典中存在该词时）
        2. 否则尝试将若干相邻 token 拼起来做字面比较
        """
        if not target:
            return False
        # 单 token 直接匹配
        if start < len(normalized) and normalized[start] == target:
            return True
        # 多 token 拼接匹配（覆盖跨词情况）
        maxLen = min(len(normalized) - start, max(1, len(target)))
        for length in range(1, maxLen + 1):
            joined = "".join(normalized[start : start + length])
            if joined == target:
                return True
            if len(joined) > len(target):
                break
        return False

    def _nodeLength(
        self,
        tokens: List[str],
        normalized: List[str],
        start: int,
        target: str,
    ) -> int:
        """返回匹配节点词占用的 token 数。

        长度选择规则（贪心）：
        - 优先单 token 精确匹配
        - 否则取拼接等于 target 的最短长度
        """
        if start < len(normalized) and normalized[start] == target:
            return 1
        maxLen = min(len(normalized) - start, max(1, len(target)))
        for length in range(1, maxLen + 1):
            joined = "".join(normalized[start : start + length])
            if joined == target:
                return length
            if len(joined) > len(target):
                break
        return 0

    def _filterSecondary(
        self,
        hits: List[KwicHit],
        secondaryWord: str,
        isRegex: bool,
        offset: int = 0,
    ) -> List[KwicHit]:
        """二次检索：在每条命中行的 left/right/node 范围内再次过滤

        Args:
            offset: 0 表示在节点词本身范围内匹配；正数 N 表示节点词右侧第 N 个词；
                负数 -N 表示左侧第 N 个词
        """
        if not secondaryWord:
            return hits
        word = secondaryWord if self.caseSensitive else secondaryWord.lower()
        regex: Optional[re.Pattern] = None
        if isRegex:
            try:
                regex = re.compile(word)
            except re.error:
                regex = None
        result: List[KwicHit] = []
        for hit in hits:
            if self._hitMatches(hit, word, regex, offset):
                result.append(hit)
        return result

    def _hitMatches(
        self,
        hit: KwicHit,
        word: str,
        regex: Optional[re.Pattern],
        offset: int,
    ) -> bool:
        target = self._tokenAtOffset(hit, offset)
        if target is None:
            return False
        targetNorm = target if self.caseSensitive else target.lower()
        if regex is not None:
            return bool(regex.fullmatch(targetNorm))
        return targetNorm == word

    def _tokenAtOffset(self, hit: KwicHit, offset: int) -> Optional[str]:
        """根据相对节点词的偏移取 token（FR-KWC-004 二次检索定位）

        - offset = 0: 节点词本身（多字节点词则整个拼接）
        - offset > 0: 节点词右侧第 offset 个 token
        - offset < 0: 节点词左侧第 |offset| 个 token
        """
        if offset == 0:
            return "".join(hit.node) if hit.node else None
        if offset > 0:
            if offset - 1 < len(hit.rightContext):
                return hit.rightContext[offset - 1]
            return None
        # offset < 0
        idx = len(hit.leftContext) + offset
        if 0 <= idx < len(hit.leftContext):
            return hit.leftContext[idx]
        return None

    def _sortHits(
        self,
        hits: List[KwicHit],
        sortMode: SortMode,
    ) -> List[KwicHit]:
        if sortMode == SortMode.ORIGINAL or not hits:
            return hits
        if sortMode == SortMode.LEFT_FIRST:
            return sorted(hits, key=lambda h: self._leftSortKey(h))
        if sortMode == SortMode.RIGHT_FIRST:
            return sorted(hits, key=lambda h: self._rightSortKey(h))
        if sortMode == SortMode.NODE_COLLOCATE:
            return sorted(hits, key=lambda h: h.collocateText)
        return hits

    @staticmethod
    def _leftSortKey(hit: KwicHit) -> Tuple[str, int]:
        key = hit.leftContext[-1] if hit.leftContext else ""
        return (key, hit.tokenIndex)

    @staticmethod
    def _rightSortKey(hit: KwicHit) -> Tuple[str, int]:
        key = hit.rightContext[0] if hit.rightContext else ""
        return (key, hit.tokenIndex)

    @staticmethod
    def _joinCorpusName(fileToText: Dict[str, str]) -> str:
        if not fileToText:
            return "(空)"
        if len(fileToText) == 1:
            return next(iter(fileToText.keys()))
        return f"{len(fileToText)} 个文件"
