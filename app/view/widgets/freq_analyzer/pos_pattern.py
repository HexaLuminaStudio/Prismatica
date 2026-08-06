# coding: utf-8
"""POS 词性组合模式匹配器(FR-CON-009 P0-fix 2026-07-20)

需求:
    - 在共现网络图中支持「词性组合过滤」,例如:
        * "V 都 V 了" → <动词>都<动词>了  (如「走都走了」、「看都看了」)
        * "V N V"     → <动词><名词><动词>  (如「读书读」、「吃饭吃」)
        * "N 的 N"    → <名词>的<名词>      (如「我的书」、「老师的家」)
        * "V O"       → <动词><任意词>      (动宾结构)

设计:
    - 简单 DSL 解析,支持词性占位符与字面词混合
    - 编译为 token 序列,在线性 token 流上做 KMP 风格匹配
    - 匹配成功后,从匹配区间内的所有候选词两两配对,加入共现矩阵

学术依据:
    - Chomsky, N. (1957). Syntactic Structures. 短语结构规则。
    - Xue, N., & Xia, F. (2005). 汉语短语结构的自动识别。
    - Evert, S. (2008). Corpora and collocations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence, Tuple

from app.core.utils import logger


# ---------------------------------------------------------------------------
# 词性占位符 → jieba tag 集合
# ---------------------------------------------------------------------------
# 基于 ICTCLAS / jieba 标注体系,定义粗类占位符
POS_PLACEHOLDER_TO_JIEBA: dict = {
    "V": {"v", "vd", "vn"},  # 动词(含副动词/名动词)
    "N": {"n", "nr", "ns", "nt", "nx", "nz"},  # 名词(含人名/地名/机构/外文/其他专名)
    "A": {"a", "ad", "an"},  # 形容词
    "D": {"d"},  # 副词
    "R": {"r"},  # 代词
    "P": {"p", "c"},  # 介词 / 连词(归并为功能词)
    "M": {"m"},  # 数词
    "Q": {"q"},  # 量词
    "U": {"u", "uj", "ul", "uv", "uz"},  # 助词
    "W": {"w"},  # 标点
    "O": None,  # 任意词(通配符,匹配成功不消耗 POS 约束)
}

# 简写 → 词性占位符(英文别名)
POS_ALIAS: dict = {
    "VERB": "V",
    "NOUN": "N",
    "ADJ": "A",
    "ADV": "D",
    "PRON": "R",
    "PREP": "P",
    "NUM": "M",
    "QUAN": "Q",
    "AUX": "U",
    "PUNCT": "W",
    "ANY": "O",
}


class PatternTokenType(Enum):
    """模式 token 类型"""

    PLACEHOLDER = "placeholder"  # <V> / <N> 等(必须匹配指定词性)
    WILDCARD = "wildcard"  # *(任意一个词)
    GREEDY = "greedy"  # *+(任意 ≥0 个词)
    LITERAL = "literal"  # 字面词(如「都」、「了」)
    ALT = "alt"  # [词1|词2|...]字面可选


@dataclass
class PatternToken:
    """模式编译后的单个 token"""

    type: PatternTokenType
    value: object  # PLACEHOLDER: set[str]|None | LITERAL: str | ALT: list[str] | WILDCARD/GREEDY: None


@dataclass
class PatternMatch:
    """一次匹配结果"""

    startIdx: int  # 在 token 流中的起始位置(含)
    endIdx: int  # 在 token 流中的结束位置(含)
    matched: List[Tuple[str, str]]  # [(word, pos_tag), ...] 匹配区间内的词与词性

    def coversIndex(self, idx: int) -> bool:
        """判断 token 索引 idx 是否落在本次匹配区间内"""
        return self.startIdx <= idx <= self.endIdx


# ---------------------------------------------------------------------------
# 词性组合模式解析器
# ---------------------------------------------------------------------------
class PosPattern:
    """词性组合模式(编译后)

    支持的语法:
        <V>              单个动词
        都               字面词
        <V> 都 <V> 了    混合
        *                任意一个词(不消耗长度约束)
        *+               任意 ≥0 个词(贪婪跳过)
        [都|也|还]       字面可选列表

    Examples:
        >>> p = PosPattern("<V> 都 <V> 了")
        >>> p.match(tokens=[("走","v"),("都","d"),("走","v"),("了","u")])
        PatternMatch(...)
    """

    # 模式字符串解析正则
    # 优先级: <*> | *+ | * | [..|..]
    _TOKEN_REGEX = re.compile(
        r"<\s*(?P<placeholder>[A-Za-z]+)\s*>"  # 1: <X> 词性占位符
        r"|(?P<greedy>\*\+)"  # 2: *+ 贪婪
        r"|(?P<star>\*)"  # 3: * 单个通配
        r"|\[(?P<alt>[^\[\]]+)\]"  # 4: [a|b|c] 字面可选
    )

    def __init__(self, patternStr: str):
        self.raw: str = patternStr.strip()
        self.tokens: List[PatternToken] = []
        self._parse(self.raw)

    def _parse(self, s: str) -> None:
        """解析模式字符串 → token 列表

        策略:
            1. 仅在当前位置可能为特殊 token 时(<、[、* 开头)调用正则匹配
            2. 单字符 / 单字母且匹配已知 POS 占位符表 → 自动识别为 PLACEHOLDER
              (例:"V 都 V 了" 中 "V" 自动识别为动词占位符)
            3. 否则作为 LITERAL 词
        """
        if not s:
            raise ValueError("PosPattern: 空模式")

        # 已知 POS 占位符(大写字母)+ 英文别名
        KNOWN_POS = set(POS_PLACEHOLDER_TO_JIEBA.keys()) | set(POS_ALIAS.keys())

        cursor = 0
        n = len(s)
        while cursor < n:
            ch = s[cursor]
            # 跳过空白
            if ch.isspace():
                cursor += 1
                continue

            # 1) 特殊 token(<X>、*+、*、[..|..])
            m = None
            if ch in "<[*":
                m = self._TOKEN_REGEX.match(s, cursor)
                if m is not None and m.start() != cursor:
                    m = None

            if m:
                placeholder = m.group("placeholder")
                greedy = m.group("greedy")
                star = m.group("star")
                alt = m.group("alt")
                if placeholder:
                    tag = POS_ALIAS.get(placeholder.upper(), placeholder.upper())
                    if tag not in POS_PLACEHOLDER_TO_JIEBA and tag != "O":
                        logger.warning(f"[PosPattern] 未知占位符 <{placeholder}>")
                    self.tokens.append(
                        PatternToken(
                            PatternTokenType.PLACEHOLDER,
                            POS_PLACEHOLDER_TO_JIEBA.get(tag),
                        )
                    )
                elif greedy:
                    self.tokens.append(PatternToken(PatternTokenType.GREEDY, None))
                elif star:
                    self.tokens.append(PatternToken(PatternTokenType.WILDCARD, None))
                elif alt:
                    choices = [c.strip() for c in alt.split("|") if c.strip()]
                    self.tokens.append(PatternToken(PatternTokenType.ALT, choices))
                cursor = m.end()
                continue

            # 2) 单字符 POS 占位符简写(中文 1 字符 或 英文 1~4 字母)
            #    例如 "V 都 V 了" → V 自动识别为占位符
            start = cursor
            while cursor < n:
                c = s[cursor]
                if c.isspace() or c in "<[*":
                    break
                cursor += 1
            literalText = s[start:cursor]
            if literalText:
                upper = literalText.upper()
                if upper in KNOWN_POS:
                    tag = (
                        upper if upper in POS_PLACEHOLDER_TO_JIEBA else POS_ALIAS[upper]
                    )
                    self.tokens.append(
                        PatternToken(
                            PatternTokenType.PLACEHOLDER,
                            POS_PLACEHOLDER_TO_JIEBA.get(tag),
                        )
                    )
                else:
                    self.tokens.append(
                        PatternToken(PatternTokenType.LITERAL, literalText)
                    )
            if cursor == start:
                cursor += 1

        if not self.tokens:
            raise ValueError(f"PosPattern: 无法解析 '{s}'")

    def match(
        self,
        tokens: Sequence[Tuple[str, str]],
    ) -> List[PatternMatch]:
        """在线性 token 流上做模式匹配

        Args:
            tokens: [(word, pos_tag), ...] 词性标注序列

        Returns:
            所有非重叠匹配结果列表
        """
        results: List[PatternMatch] = []
        if not tokens or not self.tokens:
            return results

        n = len(tokens)
        # 非贪婪:从每个起始位置尝试匹配,匹配成功后继续从 end+1 搜索
        pos = 0
        while pos < n:
            m = self._tryMatchAt(tokens, pos)
            if m is None:
                pos += 1
                continue
            results.append(m)
            # 跳过匹配区间,避免重叠
            pos = m.endIdx + 1
        return results

    def _tryMatchAt(
        self,
        tokens: Sequence[Tuple[str, str]],
        start: int,
    ) -> Optional[PatternMatch]:
        """从 start 位置尝试匹配,返回第一次成功的匹配区间

        核心算法:对每个模式 token,顺序在 token 流上推进指针。
        PLACEHOLDER / LITERAL / ALT:严格消耗 1 个 token
        WILDCARD:消耗 1 个 token,不约束内容
        GREEDY:消耗 0..N 个 token,直到后续 PLACEHOLDER/LITERAL 匹配成功
        """
        n = len(tokens)
        cursor = start
        for i, pat in enumerate(self.tokens):
            if cursor >= n:
                # 流耗尽
                if pat.type == PatternTokenType.GREEDY:
                    # 贪婪通配符允许末尾为 0 个
                    continue
                return None

            if pat.type == PatternTokenType.PLACEHOLDER:
                allowed = pat.value  # set[str] or None
                word, tag = tokens[cursor]
                if allowed is None or tag.lower() in {t.lower() for t in allowed}:
                    cursor += 1
                else:
                    return None

            elif pat.type == PatternTokenType.LITERAL:
                word, _ = tokens[cursor]
                if word == pat.value:
                    cursor += 1
                else:
                    return None

            elif pat.type == PatternTokenType.ALT:
                word, _ = tokens[cursor]
                if word in pat.value:
                    cursor += 1
                else:
                    return None

            elif pat.type == PatternTokenType.WILDCARD:
                # 任意一个词
                cursor += 1

            elif pat.type == PatternTokenType.GREEDY:
                # 贪婪:尝试跳过 0..N 个 token,使后续 token 匹配
                # 简单实现:找到下一个能匹配后续 pattern 的位置
                if i == len(self.tokens) - 1:
                    # 末尾的 *+ 吞掉所有剩余 token
                    cursor = n
                else:
                    nextPat = self.tokens[i + 1]
                    # 从 cursor 开始找首个满足 nextPat 的位置
                    found = None
                    for j in range(cursor, n):
                        if self._matchOne(nextPat, tokens[j]):
                            found = j
                            break
                    if found is None:
                        # 后续 pattern 在剩余流中找不到匹配,整个失败
                        return None
                    cursor = found  # 让外层循环推进到 found

            else:
                # 未知 token 类型
                return None

        if cursor <= start:
            return None
        return PatternMatch(
            startIdx=start,
            endIdx=cursor - 1,
            matched=list(tokens[start:cursor]),
        )

    @staticmethod
    def _matchOne(pat: PatternToken, item: Tuple[str, str]) -> bool:
        """判断单个 token 是否匹配单个 pattern token"""
        word, tag = item
        if pat.type == PatternTokenType.PLACEHOLDER:
            allowed = pat.value
            return allowed is None or tag.lower() in {t.lower() for t in allowed}
        if pat.type == PatternTokenType.LITERAL:
            return word == pat.value
        if pat.type == PatternTokenType.ALT:
            return word in pat.value
        if pat.type in (PatternTokenType.WILDCARD, PatternTokenType.GREEDY):
            return True
        return False

    def __repr__(self) -> str:
        return f"PosPattern({self.raw!r}, tokens={len(self.tokens)})"


# ---------------------------------------------------------------------------
# 统一过滤模式(FR-CON-010 P0-fix 2026-07-20)
# ---------------------------------------------------------------------------
# 需求:把「关键词过滤」与「词性组合过滤」合并为统一的过滤表达式,
#      用户可在一个输入框中表达复合筛选条件。
#
# 支持的语法(按优先级解析):
#   1. 包含多个由 ',' 分隔的候选表达式(OR 关系)
#      "学习,工作" → 任一关键词命中即保留
#      "学习,V 都 V 了" → 关键词 OR 词性结构
#   2. 单个候选表达式内,使用 ':' 连接子条件(AND 关系)
#      "学习:V 都 V 了" → 关键词「学习」出现在「V 都 V 了」结构中
#   3. 子条件可以是:纯关键词、词性占位符、POS 结构
#   4. 纯字面词(无特殊符号)→ 关键词过滤(向后兼容)
#   5. 含 POS 占位符(<X> 或单字母)→ POS 结构
#
# Examples:
#   "学习"              → 仅保留含"学习"的边(向后兼容)
#   "V 都 V 了"          → 仅保留符合该结构的边内候选(向后兼容)
#   "学习:V 都 V 了"     → "学习"出现在 "V 都 V 了" 结构内
#   "学习,工作"          → 含"学习"或"工作"任一
#   "学习:N 的 N"        → "学习"在 "N 的 N" 结构内
class NetworkFilter:
    """网络过滤模式(关键词 + 词性组合 的统一表达式)

    Attributes:
        raw: 原始表达式字符串
        clauses: [NetworkFilterClause, ...] OR 关系子句列表
    """

    def __init__(self, exprStr: str):
        self.raw: str = (exprStr or "").strip()
        self.clauses: List["NetworkFilterClause"] = []
        if not self.raw:
            return
        self._parse()

    def _parse(self) -> None:
        # 顶层 ',' 分隔(OR)
        parts = [p.strip() for p in self.raw.split(",") if p.strip()]
        for part in parts:
            self.clauses.append(NetworkFilterClause(part))

    @property
    def hasPosPattern(self) -> bool:
        return any(clause.hasPosPattern for clause in self.clauses)

    def keywords(self) -> List[str]:
        """提取所有子句中的关键词(去重小写归一)"""
        result: List[str] = []
        for cl in self.clauses:
            for kw in cl.keywords:
                if kw not in result:
                    result.append(kw)
        return result

    def posPatterns(self) -> List[PosPattern]:
        """提取所有子句中的 POS 结构"""
        return [cl.posPattern for cl in self.clauses if cl.hasPosPattern]

    def isEmpty(self) -> bool:
        return not self.clauses

    def __repr__(self) -> str:
        return f"NetworkFilter({self.raw!r}, clauses={len(self.clauses)})"


class NetworkFilterClause:
    """过滤子句:一个关键词 + 一个/多个 POS 结构的组合(AND 关系)

    Attributes:
        raw: 子句字符串
        keywords: [str, ...] 关键词列表(去小写,无 POS 时即为原字符串)
        posPattern: PosPattern 或 None(若存在 POS 结构)
        combined: bool,True = 关键词 AND 结构;False = 纯关键词 / 纯结构
    """

    def __init__(self, exprStr: str):
        self.raw: str = exprStr.strip()
        self.keywords: List[str] = []
        self.posPattern: Optional[PosPattern] = None
        self._parse()

    def _parse(self) -> None:
        # ':' 分隔关键词与 POS 结构(AND)
        if ":" in self.raw:
            kwPart, _, posPart = self.raw.partition(":")
            kwPart = kwPart.strip()
            posPart = posPart.strip()
            if kwPart:
                self.keywords.extend(t.strip() for t in kwPart.split(",") if t.strip())
            if posPart:
                try:
                    self.posPattern = PosPattern(posPart)
                except Exception:
                    self.posPattern = None
            return

        # 无 ':' 时:尝试识别为 POS 结构(若含占位符或可见 POS 字符)
        # 否则视为关键词列表(',' 分隔)
        if self._looksLikePosPattern(self.raw):
            try:
                self.posPattern = PosPattern(self.raw)
                return
            except Exception:
                pass
        # 关键词列表
        for kw in self.raw.split(","):
            k = kw.strip()
            if k:
                self.keywords.append(k)

    @staticmethod
    def _looksLikePosPattern(s: str) -> bool:
        """判定字符串是否像 POS 模式(含占位符或通配符)"""
        if not s:
            return False
        # 含 <>、*、[] 一定为 POS 模式
        if "<" in s or ">" in s or "*" in s or "[" in s:
            return True
        # 多 token 形式(由空格分隔)
        tokens = s.split()
        if len(tokens) < 2:
            return False
        # 情况 1:全部 token 都属于 POS 占位符(如 "V N V")
        if all(t in POS_PLACEHOLDER_TO_JIEBA for t in tokens):
            return True
        # 情况 2:含至少一个 POS 占位符,且其他是字面词(如 "V 都 V 了")
        hasPosToken = any(t in POS_PLACEHOLDER_TO_JIEBA for t in tokens)
        if hasPosToken:
            # 其余 token 必须是「字面」(不为分隔符、非空)
            return True
        return False

    @property
    def hasPosPattern(self) -> bool:
        return self.posPattern is not None

    @property
    def isPosOnly(self) -> bool:
        return self.posPattern is not None and not self.keywords

    @property
    def isKwOnly(self) -> bool:
        return self.posPattern is None and bool(self.keywords)

    @property
    def isCombined(self) -> bool:
        return self.posPattern is not None and bool(self.keywords)

    def __repr__(self) -> str:
        return f"NetworkFilterClause({self.raw!r})"


def tokenizeForPos(text: str, useJieba: bool = True) -> List[Tuple[str, str]]:
    """对文本做分词 + 词性标注,返回 [(word, tag), ...]

    Args:
        text: 输入文本
        useJieba: 是否使用 jieba.posseg

    Returns:
        [(词, 词性)] 列表
    """
    if not text:
        return []
    if not useJieba:
        # 不使用 jieba: 退化为字符级 + 标 "x" 词性
        return [(ch, "x") for ch in text if ch.strip()]

    try:
        import jieba.posseg as pseg

        return [(w.word, w.flag) for w in pseg.cut(text) if w.word.strip()]
    except Exception as e:
        logger.warning(f"[tokenizeForPos] jieba.posseg 失败: {e},退化为字符级")
        return [(ch, "x") for ch in text if ch.strip()]


def posDistribution(tokens: Sequence[Tuple[str, str]]) -> dict:
    """统计词性分布

    Returns:
        {词性粗类: 频次}
    """
    from collections import Counter

    counter: Counter = Counter()
    for _, tag in tokens:
        # 将细粒度 tag 归到粗类
        first = tag[0].upper() if tag else "O"
        mapping = {
            "N": "N",
            "V": "V",
            "A": "A",
            "D": "D",
            "R": "R",
            "P": "P",
            "M": "M",
            "Q": "Q",
            "U": "U",
            "W": "W",
        }
        coarse = mapping.get(first, "O")
        counter[coarse] += 1
    return dict(counter)
