# coding: utf-8
"""句法依存分析引擎 — FR-DEP-001 / FR-DEP-002 / FR-DEP-005

设计目标:
    - 抽象接口 DependencyParser,可替换后端
    - 默认由桌面端直连 HanLP,另提供显式的规则演示后端
    - 输出统一格式(DepToken 列表),与具体后端解耦
    - 支持 CoNLL-U 导出(FR-DEP-005)

CoNLL-U 格式(标准):
    # ID  FORM  LEMMA  UPOS  XPOS  FEATS  HEAD  DEPREL  DEPS  MISC
    1    我    _     _     r     _       2    SBV     _     _
    2    爱    _     _     v     _       0    ROOT    _     _

学术严谨性说明
--------------
本模块提供两个后端:
    1. HanLPDependencyParser — 由桌面端直接调用 HanLP RESTful API。
       本适配器默认请求 ``tok/fine``、``dep`` 与 ``pos/ctb`` 任务,并保留
       HanLP 返回的依存标签原貌。
       实际标注体系、模型版本和分词粒度必须随研究结果一并报告;不能仅凭
       “HanLP”名称推定为 UD,也不能宣称可不经人工核验直接用于发表。
    2. RuleBasedDependencyParser — 基于 jieba 分词 + 启发式规则
       的**降级方案**,无任何学术发表的依存分析后端作为支撑。
       其规则仅覆盖常见汉语模式(的/地/得、介词、副词等),
       **不可用于学术研究**;仅供教学演示或在没有外部依赖时使用。
       严格学术场景应使用经过目标领域评测的模型并进行人工抽样核验。

References:
    Nivre, J., et al. (2016). Universal Dependencies v1: A
        multilingual treebank collection. LREC.
    HanLP 2.x Documentation. RESTful APIs and Data Format.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from app.core.utils import logger


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class DepToken:
    """依存分析中的一个词语节点

    字段遵循 CoNLL-U 标准:
        id:       1-based 序号(0 表示虚拟 ROOT)
        form:     词语表面形式
        lemma:    词元(可选,后端不支持时为 form)
        pos:      词性标注(粗粒度)
        head:     父节点 id(0 = ROOT)
        deprel:   依存关系类型(如 SBV/VOB/ATT)
    """

    id: int
    form: str
    pos: str = ""
    head: int = 0
    deprel: str = "ROOT"
    lemma: str = ""

    def __post_init__(self) -> None:
        if not self.lemma:
            self.lemma = self.form


@dataclass
class DependencyParse:
    """一个句子的依存分析结果"""

    tokens: List[DepToken] = field(default_factory=list)
    text: str = ""
    backend: str = ""  # 实际使用的后端名
    provider: str = ""
    endpoint: str = ""
    language: str = ""
    tasks: List[str] = field(default_factory=list)
    modelVersion: str = ""
    labelScheme: str = "backend-native"

    @property
    def root(self) -> Optional[DepToken]:
        for t in self.tokens:
            if t.head == 0:
                return t
        return self.tokens[0] if self.tokens else None

    @property
    def edges(self) -> List[Tuple[int, int, str]]:
        """返回所有边: (head_id, dependent_id, deprel)"""
        return [(t.head, t.id, t.deprel) for t in self.tokens if t.head != t.id]


# ---------------------------------------------------------------------------
# 抽象接口
# ---------------------------------------------------------------------------
class DependencyParser(ABC):
    """依存分析器抽象接口"""

    name: str = "base"

    @abstractmethod
    def parse(self, sentence: str) -> DependencyParse:
        """分析单个句子"""

    def isAvailable(self) -> bool:
        """后端是否可用(库已安装)"""
        return True

    def describe(self) -> str:
        return f"{self.name} 依存分析器"


# ---------------------------------------------------------------------------
# Fallback: 规则式简易依存分析(始终可用)
# ---------------------------------------------------------------------------
class RuleBasedDependencyParser(DependencyParser):
    """基于规则的简易依存分析 — 不依赖任何外部库

    策略:
        - 使用 jieba.posseg 分词 + 词性标注(项目已有依赖)
        - 启发式确定 ROOT:句中第一个动词(若有),否则第一个名词,否则第一个词
        - 启发式建立依存:
            * 标点 → PUNCT(deprel)
            * 助词/的/地/得 → 右邻词的 ATT(定中) 等
            * 介词 → 右邻 SBV/ADV 等
            * 数词+量词 → 组合
            * 副词 → 最近动词 ADV(状中)
            * 名词链(连续名词) → ATT 或 COO
            * 默认剩余 → 全部挂在 ROOT 上,deprel 为 COO 或 HED
    """

    name = "rule"

    # 句末标点符号(单独成 token 时用 PUNCT)
    PUNCT_CHARS = set("。,.;!?！？,;:,()（）[]【】《》\"'「」『』")
    # 介词(支配 SBV/ADV)
    PREPOSITIONS = {"在", "从", "向", "对", "和", "与", "给", "以", "由", "为"}
    # 结构助词(从属于前面最近的实词)
    STRUCT_PARTICLES = {"的", "地", "得"}
    # 动词常见词性
    VERB_POS = {"v", "vd", "vn"}
    # 名词常见词性
    NOUN_POS = {"n", "nr", "ns", "nt", "nx", "nz"}
    # 形容词
    ADJ_POS = {"a", "ad", "an"}
    # 副词
    ADV_POS = {"d"}

    def __init__(self):
        # 延迟导入 jieba
        try:
            import jieba.posseg as pseg  # noqa

            self._pseg = pseg
            self._available = True
        except ImportError:
            self._pseg = None
            self._available = False

    def isAvailable(self) -> bool:
        return self._available

    def describe(self) -> str:
        return "规则式依存分析(无外部依赖,基于 jieba 分词 + 启发式规则)"

    def parse(self, sentence: str) -> DependencyParse:
        sentence = (sentence or "").strip()
        if not sentence:
            return DependencyParse(text=sentence, backend=self.name)

        # 1) 分词 + 词性
        if self._pseg is None:
            # jieba 不可用 → 单字切分
            rawTokens = [(c, "n") for c in sentence if c.strip()]
        else:
            rawTokens = [(w.word, w.flag) for w in self._pseg.cut(sentence)]

        # 2) 过滤空白 token
        rawTokens = [(w, t) for w, t in rawTokens if w and w.strip()]
        if not rawTokens:
            return DependencyParse(text=sentence, backend=self.name)

        # 3) 为每个 token 分配 1-based id
        tokens: List[DepToken] = []
        for idx, (form, pos) in enumerate(rawTokens, start=1):
            tokens.append(DepToken(id=idx, form=form, pos=pos, lemma=form))

        # 4) 启发式建立依存
        rootId = self._findRoot(tokens)
        self._linkDependencies(tokens, rootId)

        # 5) 防御性校验:head 引用合法性 + root 自挂修复
        self._sanitizeHeads(tokens, rootId)

        return DependencyParse(tokens=tokens, text=sentence, backend=self.name)

    def _sanitizeHeads(self, tokens: List[DepToken], rootId: int) -> None:
        """校验并修复 head 引用

        - root 节点自身: head 应为 0,deprel 应为 ROOT
        - 其它节点: head 必须在 validIds 内,且不能等于自身 id
        """
        validIds = {t.id for t in tokens}
        for tok in tokens:
            # ROOT 节点修复
            if tok.id == rootId:
                tok.head = 0
                if not tok.deprel or tok.deprel != "ROOT":
                    tok.deprel = "ROOT"
                continue
            # 其它节点:head 必须是有效 id,且不能指向自己
            if tok.head == 0:
                # 0 只给 ROOT;非 ROOT 节点不应 head=0 → fallback 到 rootId
                tok.head = rootId
                if not tok.deprel:
                    tok.deprel = "DE"
            elif tok.head not in validIds or tok.head == tok.id:
                logger.debug(
                    f"[RuleDepParser] token id={tok.id} ({tok.form!r}) "
                    f"head={tok.head} 非法,fallback 到 rootId={rootId}"
                )
                tok.head = rootId
                if not tok.deprel:
                    tok.deprel = "DE"

    def _findRoot(self, tokens: List[DepToken]) -> int:
        """找 ROOT:第一个动词优先;否则第一个名词;否则第一个词"""
        for t in tokens:
            if t.pos in self.VERB_POS:
                return t.id
        for t in tokens:
            if t.pos in self.NOUN_POS:
                return t.id
        return tokens[0].id

    def _linkDependencies(self, tokens: List[DepToken], rootId: int) -> None:
        """为每个 token 设置 head 与 deprel"""
        for i, tok in enumerate(tokens):
            if tok.form in self.PUNCT_CHARS:
                # 标点单独处理:PUNCT 挂在前一个实词上(若存在)
                if i > 0:
                    tok.head = tokens[i - 1].id
                    tok.deprel = "PUNCT"
                else:
                    tok.head = rootId
                    tok.deprel = "PUNCT"
            elif tok.form in self.STRUCT_PARTICLES:
                # 结构助词「的/地/得」挂在前一个实词上,deprel=M(标记/助词)
                if i > 0:
                    tok.head = tokens[i - 1].id
                    tok.deprel = "MT"
                else:
                    tok.head = rootId
                    tok.deprel = "MT"
            elif tok.form in self.PREPOSITIONS:
                # 介词挂在前一个实词上,右邻词 SBV/ADV
                if i > 0:
                    tok.head = tokens[i - 1].id
                    tok.deprel = "ADV"
                else:
                    tok.head = rootId
                    tok.deprel = "ADV"
            elif tok.pos in self.ADV_POS:
                # 副词 → 右边最近动词 ADV
                vId = self._nearestVerb(tokens, i, direction=1)
                if vId is not None:
                    tok.head = vId
                    tok.deprel = "ADV"
                else:
                    tok.head = rootId
                    tok.deprel = "ADV"
            elif tok.pos in self.ADJ_POS:
                # 形容词 → 右边最近名词 ATT,否则最近动词 CMP(补语)
                nId = self._nearestPos(tokens, i, direction=1, allowed=self.NOUN_POS)
                if nId is not None:
                    tok.head = nId
                    tok.deprel = "ATT"
                else:
                    vId = self._nearestPos(
                        tokens, i, direction=-1, allowed=self.VERB_POS
                    )
                    if vId is not None:
                        tok.head = vId
                        tok.deprel = "CMP"
                    else:
                        tok.head = rootId
                        tok.deprel = "ATT"
            elif tok.pos in self.NOUN_POS:
                # 名词 → 左边最近动词 VOB,否则 ROOT HED,否则左邻名词 ATT
                vId = self._nearestPos(tokens, i, direction=-1, allowed=self.VERB_POS)
                if vId is not None:
                    tok.head = vId
                    tok.deprel = "VOB"
                    continue
                nId = self._nearestPos(tokens, i, direction=-1, allowed=self.NOUN_POS)
                if nId is not None and i > 0 and tokens[i - 1].pos in self.NOUN_POS:
                    tok.head = nId
                    tok.deprel = "ATT"
                else:
                    tok.head = rootId
                    tok.deprel = "HED"
            elif tok.pos in self.VERB_POS:
                # 动词:ROOT 自身,其它动词挂 ROOT
                if tok.id == rootId:
                    tok.head = 0
                    tok.deprel = "ROOT"
                else:
                    tok.head = rootId
                    tok.deprel = "COO"
            else:
                # 其它(代词/数词/量词等)→ 左边最近实词
                leftId = self._nearestContentWord(tokens, i, direction=-1)
                if leftId is not None:
                    tok.head = leftId
                    tok.deprel = "DE"
                else:
                    tok.head = rootId
                    tok.deprel = "DE"

    def _nearestVerb(
        self, tokens: List[DepToken], i: int, direction: int
    ) -> Optional[int]:
        return self._nearestPos(tokens, i, direction, self.VERB_POS)

    def _nearestPos(
        self,
        tokens: List[DepToken],
        i: int,
        direction: int,
        allowed: set,
    ) -> Optional[int]:
        j = i + direction
        while 0 <= j < len(tokens):
            if tokens[j].pos in allowed:
                return tokens[j].id
            j += direction
        return None

    def _nearestContentWord(
        self, tokens: List[DepToken], i: int, direction: int
    ) -> Optional[int]:
        """最近实词(排除标点/助词/介词)"""
        skip = self.PUNCT_CHARS | self.STRUCT_PARTICLES | self.PREPOSITIONS
        j = i + direction
        while 0 <= j < len(tokens):
            t = tokens[j]
            if t.form not in skip and t.pos not in ("w", "p", "u", "c"):
                return t.id
            j += direction
        return None


# ---------------------------------------------------------------------------
# HanLP RESTful 直连适配器(凭据按产品决策硬编码)
# ---------------------------------------------------------------------------
class HanLPDependencyParser(DependencyParser):
    """桌面端直接调用 HanLP RESTful API。"""

    name = "hanlp"
    HANLP_AUTH = "MTA4MzRAYmJzLmhhbmxwLmNvbTprN0NMTnhXWk92ajBmRmdL"
    HANLP_API_URL = "https://hanlp.hankcs.com/api"
    HANLP_LANGUAGE = "zh"
    HANLP_TASKS = ("tok/fine", "pos/ctb", "dep")

    def __init__(self) -> None:
        """使用代码内固定配置初始化 HanLP 客户端。"""
        self._client = None
        self._lastError: Optional[str] = None
        self._metadata: Dict[str, Any] = {
            "provider": "HanLP RESTful",
            "endpoint": self.HANLP_API_URL,
            "language": self.HANLP_LANGUAGE,
            "tasks": list(self.HANLP_TASKS),
            "modelVersion": "",
            "labelScheme": "backend-native",
        }
        try:
            from hanlp_restful import HanLPClient

            self._client = HanLPClient(
                url=self.HANLP_API_URL,
                auth=self.HANLP_AUTH,
                language=self.HANLP_LANGUAGE,
                timeout=60,
            )
        except ImportError:
            self._lastError = "hanlp_restful 未安装"
        except Exception as error:
            self._lastError = str(error)
            logger.warning(f"[HanLPDepParser] 客户端初始化失败:{error}")

    def isAvailable(self) -> bool:
        return self._client is not None

    def describe(self) -> str:
        return f"HanLP RESTful 直连({self.HANLP_API_URL})"

    def getLastError(self) -> Optional[str]:
        """返回最近一次的初始化/解析错误信息(用于 UI 提示)"""
        return self._lastError

    def clearLastError(self) -> None:
        self._lastError = None

    def parse(self, sentence: str) -> DependencyParse:
        """通过桌面端内置凭据直连 HanLP 分析单句。"""
        sentence = (sentence or "").strip()
        if not sentence:
            return DependencyParse(text=sentence, backend=self.name)
        if self._client is None:
            raise RuntimeError(self._lastError or "HanLP 客户端不可用")
        try:
            document = self._client(sentence, tasks=list(self.HANLP_TASKS))
            tokens, posTags, dependencies = self._extractOutput(document)
        except Exception as error:
            self._lastError = str(error)
            raise
        self._lastError = None
        parsedTokens = [
            DepToken(
                id=index + 1,
                form=str(form),
                lemma=str(form),
                pos=str(posTags[index]) if index < len(posTags) else "",
                head=int(dependencies[index][0]),
                deprel=str(dependencies[index][1]),
            )
            for index, form in enumerate(tokens)
        ]
        self._validateTree(parsedTokens)
        return DependencyParse(
            text=sentence,
            backend=self.name,
            provider=self._metadata["provider"],
            endpoint=self._metadata["endpoint"],
            language=self._metadata["language"],
            tasks=list(self._metadata["tasks"]),
            modelVersion=self._metadata["modelVersion"],
            labelScheme=self._metadata["labelScheme"],
            tokens=parsedTokens,
        )

    @staticmethod
    def _extractOutput(document: Any) -> Tuple[List[Any], List[Any], List[Any]]:
        data = document.to_dict() if hasattr(document, "to_dict") else document
        if not isinstance(data, dict):
            raise ValueError("HanLP 返回格式无效")

        def firstField(prefix: str) -> Any:
            for key, value in data.items():
                if key == prefix or key.startswith(f"{prefix}/"):
                    return value
            return None

        def firstSentence(value: Any) -> List[Any]:
            if not isinstance(value, list):
                return []
            if value and isinstance(value[0], list):
                return list(value[0])
            return list(value)

        tokens = firstSentence(firstField("tok"))
        posTags = firstSentence(firstField("pos"))
        dependencies = firstSentence(firstField("dep"))
        if not tokens or len(tokens) != len(dependencies):
            raise ValueError("HanLP 分词与依存结果长度不一致")
        if any(
            not isinstance(item, (list, tuple)) or len(item) < 2
            for item in dependencies
        ):
            raise ValueError("HanLP 依存节点结构无效")
        return tokens, posTags, dependencies

    @staticmethod
    def _validateTree(tokens: List[DepToken]) -> None:
        tokenCount = len(tokens)
        if sum(token.head == 0 for token in tokens) != 1:
            raise ValueError("HanLP 依存树应有且仅有一个 ROOT")
        headById = {token.id: token.head for token in tokens}
        for token in tokens:
            if token.head < 0 or token.head > tokenCount or token.head == token.id:
                raise ValueError("HanLP 依存树包含无效中心词索引")
            visited: set[int] = set()
            currentId = token.id
            while currentId != 0:
                if currentId in visited:
                    raise ValueError("HanLP 依存树包含环")
                visited.add(currentId)
                currentId = headById[currentId]

    def metadata(self) -> Dict[str, Any]:
        return dict(self._metadata)


# ---------------------------------------------------------------------------
# 句子切分工具
# ---------------------------------------------------------------------------
_SENTENCE_RE = re.compile(r"[^。！？.!?]+[。！？.!?]?")


def splitSentences(text: str) -> List[str]:
    """粗粒度句子切分(中英文)

    Returns:
        句子列表(已 strip)
    """
    if not text:
        return []
    raw = _SENTENCE_RE.findall(text)
    return [s.strip() for s in raw if s and s.strip()]


# ---------------------------------------------------------------------------
# CoNLL-U 序列化(FR-DEP-005)
# ---------------------------------------------------------------------------
def toConllU(parse: DependencyParse) -> str:
    """将 DependencyParse 序列化为 10 列 CoNLL-U 容器格式。

    CoNLL-U 标准(每行 10 字段,Tab 分隔):
        ID FORM LEMMA UPOS XPOS FEATS HEAD DEPREL DEPS MISC

    当前后端返回 CTB/jieba 词性与原始依存标签，未经 UD 映射验证。
    因此词性写入 XPOS，UPOS 留空；不把该导出宣称为 UD 树库。
    """
    lines = [
        f"# text = {parse.text}",
        f"# backend = {parse.backend}",
        f"# label_scheme = {parse.labelScheme}; UPOS unavailable",
    ]
    optionalMetadata = (
        ("provider", parse.provider),
        ("endpoint", parse.endpoint),
        ("language", parse.language),
        ("tasks", ",".join(parse.tasks)),
        ("model_version", parse.modelVersion or "未报告"),
    )
    lines.extend(f"# {key} = {value}" for key, value in optionalMetadata if value)
    for tok in parse.tokens:
        fields = [
            str(tok.id),
            tok.form,
            tok.lemma or "_",
            "_",  # UPOS: 未做经验证的 UD 映射
            tok.pos or "_",  # XPOS: 后端原生词性
            "_",  # FEATS
            str(tok.head),
            tok.deprel,
            "_",  # DEPS
            "_",  # MISC
        ]
        lines.append("\t".join(fields))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 引擎工厂
# ---------------------------------------------------------------------------
def getAvailableParsers() -> List[DependencyParser]:
    """返回所有可用的依存分析器(优先级降序)

    HanLP > RuleBasedFallback
    """
    parsers: List[DependencyParser] = []
    try:
        h = HanLPDependencyParser()
        if h.isAvailable():
            parsers.append(h)
    except Exception:
        pass

    # 始终附带 fallback(确保有可用结果)
    parsers.append(RuleBasedDependencyParser())
    return parsers


def getDefaultParser() -> DependencyParser:
    """返回当前最佳可用依存分析器"""
    parsers = getAvailableParsers()
    return parsers[0]
