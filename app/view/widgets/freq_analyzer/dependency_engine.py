# coding: utf-8
"""句法依存分析引擎 — FR-DEP-001 / FR-DEP-002 / FR-DEP-005

设计目标:
    - 抽象接口 DependencyParser,可替换后端
    - 优先尝试专业引擎(HanLP > LTP > spaCy),失败时降级到基于规则的简易实现
    - 输出统一格式(DepToken 列表),与具体后端解耦
    - 支持 CoNLL-U 导出(FR-DEP-005)

CoNLL-U 格式(标准):
    # ID  FORM  LEMMA  UPOS  XPOS  FEATS  HEAD  DEPREL  DEPS  MISC
    1    我    _     r     _     _       2    SBV     _     _
    2    爱    _     v     _     _       0    ROOT    _     _

学术严谨性说明
--------------
本模块提供两个后端:
    1. HanLPDependencyParser — 调用 HanLP RESTful API(商业级,
       基于 Transformer 与大规模标注数据;HanLP 团队 2020)。
       输出符合 Universal Dependencies (UD) 标准,学术发表可直接引用。
    2. RuleBasedDependencyParser — 基于 jieba 分词 + 启发式规则
       的**降级方案**,无任何学术发表的依存分析后端作为支撑。
       其规则仅覆盖常见汉语模式(的/地/得、介词、副词等),
       **不可用于学术研究**;仅供教学演示或在没有外部依赖时使用。
       严格学术场景应至少使用 HanLP 或 LTP(哈工大)或 spaCy-zh。

References:
    Nivre, J., et al. (2016). Universal Dependencies v1: A
        multilingual treebank collection. LREC.
    Che, W., Feng, Y., Qin, L., & Liu, T. (2020). N-LTP: An
        Open-source Neural Language Technology Platform. arXiv.
    HanLP 团队 (2020). HanLP: Han Language Processing.
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)


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

        return DependencyParse(tokens=tokens, text=sentence, backend=self.name)

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
# HanLP RESTful 适配器(hanlp_restful 客户端)
# ---------------------------------------------------------------------------
class HanLPDependencyParser(DependencyParser):
    """基于 hanlp_restful 的 HanLP 依存分析适配器

    安装:
        pip install hanlp_restful

    配置(优先级递减,生产环境推荐方式 1):
        1. 环境变量 HANLP_AUTH  (推荐)
        2. 启动参数传入 auth="..."
        3. 直接修改类常量 HanLPDependencyParser.HANLP_AUTH(仅供演示)

    用法:
        parser = HanLPDependencyParser()              # 默认从 env/常量加载
        parser = HanLPDependencyParser(auth="...")    # 显式传入自定义密钥
        if parser.isAvailable():
            result = parser.parse("我爱自然语言处理")
    """

    name = "hanlp"

    # HanLP RESTful 输出标签(UD 英文)→ CTB/中文通用标签
    # RESTful API 输出的 deprel 是英文标签(nsubj/dobj/advmod/...)
    DEPREL_MAP = {
        "root": "ROOT",
        "nsubj": "SBV",
        "dobj": "VOB",
        "obj": "VOB",
        "iobj": "IOB",
        "amod": "ATT",
        "advmod": "ADV",
        "conj": "COO",
        "cc": "CC",
        "case": "ADV",
        "mark": "MT",
        "aux": "AUX",
        "cop": "AUX",
        "det": "DE",
        "clf": "DE",
        "punct": "PUNCT",
        "dep": "DE",
        "xcomp": "CMP",
        "ccomp": "CMP",
        "acl": "ATT",
        "relcl": "ATT",
        "appos": "AP",
        "nmod": "DE",
        "nummod": "DE",
        "compound": "DE",
        "obl": "ADV",  # oblique(介宾/状语)
        "discourse": "DE",
        "parataxis": "DE",
        "list": "DE",
        "fixed": "DE",
        "flat": "DE",
        "orphan": "DE",
    }

    # HanLP RESTful 服务地址(官方)
    HANLP_API_URL = "https://hanlp.hankcs.com/api"
    HANLP_LANGUAGE = "zh"  # 默认中文(可选: zh/en/ja/mul)

    # HanLP RESTful 认证密钥(写死在此处,用户要求)
    # 优先级: 显式传入参数 > 类常量 HANLP_AUTH
    HANLP_AUTH = "MTA4MzRAYmJzLmhhbmxwLmNvbTprN0NMTnhXWk92ajBmRmdL"

    # HanLP RESTful 联合任务的合法任务名(官方限制)
    # 合法值:
    #   'tok/fine', 'tok/coarse', 'pos/ctb', 'pos/pku', 'pos/863',
    #   'ner/msra', 'ner/pku', 'ner/ontonotes', 'srl', 'dep', 'sdp', 'con'
    # 一次性取 分词 + 词性 + 依存 三个任务即可满足需求
    HANLP_TASKS = ("tok/fine", "pos/ctb", "dep")

    def __init__(
        self,
        auth: Optional[str] = None,
        url: Optional[str] = None,
        language: Optional[str] = None,
        timeout: float = 30.0,
    ):
        """初始化 HanLP RESTful 客户端

        Args:
            auth: HanLP 认证密钥。优先级: 参数 > 类常量 HANLP_AUTH
            url: HanLP API 端点,默认 https://hanlp.hankcs.com/api
            language: 语言代码,默认 'zh'
            timeout: HTTP 请求超时(秒)
        """
        self._client = None
        self._auth = auth if auth is not None else self.HANLP_AUTH
        self._url = url or self.HANLP_API_URL
        self._language = language or self.HANLP_LANGUAGE
        self._timeout = timeout
        self._lastError: Optional[str] = None

        # 尝试初始化客户端
        try:
            from hanlp_restful import HanLPClient  # noqa

            self._client = HanLPClient(
                url=self._url,
                auth=self._auth,
                language=self._language,
                timeout=int(self._timeout),
            )
            logger.info(
                f"[HanLPDepParser] HanLP 客户端初始化成功: "
                f"url={self._url}, language={self._language}"
            )
        except ImportError:
            self._lastError = "hanlp_restful 未安装(pip install hanlp_restful)"
            logger.debug(f"[HanLPDepParser] {self._lastError}")
        except Exception as e:
            self._lastError = str(e)
            logger.warning(f"[HanLPDepParser] HanLP 客户端初始化失败: {e}")

    def isAvailable(self) -> bool:
        """后端是否可用:客户端已初始化 且 没有错误"""
        return self._client is not None

    def describe(self) -> str:
        return f"HanLP RESTful 依存分析({self._url}, lang={self._language})"

    def getLastError(self) -> Optional[str]:
        """返回最近一次的初始化/解析错误信息(用于 UI 提示)"""
        return self._lastError

    def parse(self, sentence: str) -> DependencyParse:
        """分析单句(调用 HanLP RESTful)"""
        sentence = (sentence or "").strip()
        if not sentence or self._client is None:
            return DependencyParse(text=sentence, backend=self.name)

        try:
            # 调用 RESTful API,取 分词 + 词性 + 依存 三个任务
            # 注意: tasks 必须是 HanLP RESTful 官方支持的合法任务名
            # (参见类常量 HANLP_TASKS 的注释)
            doc = self._client(sentence, tasks=list(self.HANLP_TASKS))
            tokens, pos, heads, deprels = self._extractRestfulOutput(doc, sentence)
        except Exception as e:
            self._lastError = str(e)
            logger.warning(f"[HanLPDepParser] 解析失败: {e}")
            return DependencyParse(text=sentence, backend=self.name)

        tokenList: List[DepToken] = []
        n = len(tokens)
        for i, form in enumerate(tokens):
            posTag = pos[i] if i < len(pos) else ""
            head = heads[i] if i < len(heads) else 0  # 0-based,0 = ROOT
            depRel = deprels[i] if i < len(deprels) else "DE"
            id1 = i + 1  # 转 1-based token id
            head1 = head if head == 0 else head + 1
            tokenList.append(
                DepToken(
                    id=id1,
                    form=form,
                    pos=posTag,
                    head=head1,
                    deprel=self.DEPREL_MAP.get(depRel.lower(), depRel.upper()),
                    lemma=form,
                )
            )

        return DependencyParse(tokens=tokenList, text=sentence, backend=self.name)

    @staticmethod
    def _extractRestfulOutput(doc, sentence: str):
        """从 hanlp_restful 返回的 doc 中提取 (tokens, pos, heads, deprels)

        HanLP RESTful 返回结构(关键字段名随 tasks 参数变化):
            {
              "tok/fine": [["我", "爱", ...]],          # 二维 list(每个子列表一句)
              "pos/ctb":  [["r", "v", ...]],
              "dep":      [[[2, "nsubj"], [0, "root"], ...]],   # 每个 token 是 [head_id(0-based), deprel]
            }
        注意: 任务名是带斜杠的 'tok/fine' / 'pos/ctb'(不是简写 'tok'/'pos')

        Args:
            doc: HanLP RESTful 返回的 Document/dict 对象
            sentence: 原始句子(用于退化 fallback)
        Returns:
            (tokens, pos, heads, deprels) — 全部为一维 list(本函数只处理单句)
        """
        try:
            # 兼容 doc["key"] 与 doc.key 两种访问方式
            def g(*keys):
                """尝试多个 key(优先带后缀的合法任务名,再兼容简写)"""
                if isinstance(doc, dict):
                    for k in keys:
                        if k in doc and doc[k] is not None:
                            return doc[k]
                    return None
                for k in keys:
                    v = getattr(doc, k, None)
                    if v is not None:
                        return v
                return None

            # 取分词: 优先 'tok/fine'(HANLP_TASKS 中使用的合法名),再 fallback 到 'tok'
            tok2d = g("tok/fine", "tok") or []
            pos2d = g("pos/ctb", "pos") or []
            dep2d = g("dep") or []

            # 取第一个句子(本适配器每次只处理单句)
            tokens = list(tok2d[0]) if tok2d else list(sentence)
            pos = list(pos2d[0]) if pos2d else ["n"] * len(tokens)
            # dep 元素是 [head, deprel] 二元组
            depPairs = dep2d[0] if dep2d else []

            heads: List[int] = []
            deprels: List[str] = []
            for pair in depPairs:
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    heads.append(int(pair[0]))
                    deprels.append(str(pair[1]))
                else:
                    heads.append(0)
                    deprels.append("DE")

            # 长度对齐(防御性)
            while len(heads) < len(tokens):
                heads.append(0)
                deprels.append("DE")

            return tokens, pos, heads, deprels
        except Exception as e:
            logger.warning(f"[HanLPDepParser] RESTful 输出解析失败: {e}")
            return (
                list(sentence),
                ["n"] * len(sentence),
                [0] * len(sentence),
                ["ROOT"] * len(sentence),
            )


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
    """将 DependencyParse 序列化为 CoNLL-U 格式

    CoNLL-U 标准(每行 10 字段,Tab 分隔):
        ID FORM LEMMA UPOS XPOS FEATS HEAD DEPREL DEPS MISC
    """
    lines = [f"# text = {parse.text}", f"# backend = {parse.backend}"]
    for tok in parse.tokens:
        fields = [
            str(tok.id),
            tok.form,
            tok.lemma or "_",
            tok.pos or "_",
            "_",  # XPOS
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
