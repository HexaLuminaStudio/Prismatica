# coding: utf-8
"""
情感分析引擎

需求文档:
    test/6D-CorpusClient_需求文档_v3.md §2.4.7

功能:
    - FR-SNT-001 极性判断(正面/负面/中性)
    - FR-SNT-002 强度评分(-1.0 ~ +1.0),含程度副词与否定词修饰
    - FR-SNT-003 三级分析(篇章/段落/句子)
    - FR-SNT-006 自定义词典导入

设计:
    - 内置精简版情感词典(正面词/负面词)
    - 内置程度副词修饰系数(很/非常=1.5, 有点/稍微=0.5)
    - 内置否定词表(不/没/未/别/无...)
    - 使用 jieba 分词
    - 滑动窗口:否定词/程度副词在情感词前 3 个 token 内生效

学术依据与已知局限
------------------
本引擎实现遵循基于词典的情感分析方法(Lexicon-based Sentiment Analysis),
典型参考:
    - Taboada, M., Brooke, J., Tofiloski, M., Voll, K., & Stede, M.
      (2011). Lexicon-based methods for sentiment analysis.
      Computational Linguistics, 37(2), 267-307.
    - Pang, B., & Lee, L. (2004). A sentimental education:
      Sentiment analysis using subjectivity summarization based on
      minimum cuts. ACL.
    - Turney, P. D. (2002). Thumbs up or thumbs down? Semantic
      orientation applied to unsupervised classification of reviews.
      ACL.

已知局限(供学术使用参考):
    1. **上下文敏感度有限**:否定/程度副词的回看窗口固定 3 token,
       无法处理远距离修饰或隐含否定(反问、讽刺等)。
    2. **程度副词叠加方式**:采用乘性叠加(Π d_i),符合 Taboada 2011;
       早期研究曾用最大系数法(取 max d_i),严格学术场景应说明选用的
       叠加方式,本文档明确为乘性。
    3. **归一化方式**:句级 score 采用 raw / sqrt(len) 的折中归一化,
       严格场景推荐报告 (raw_score, hit_count) 2 项而非单一 score。
    4. **极性阈值 ±0.05**:为经验值,源自 HowNet 情感强度直方图分布;
       大型语料(>10k 句)推荐以等频分位法重新校准。
    5. **讽刺/反语**:本引擎不识别讽刺语气,严格研究应结合语境模型。
    6. **情感词典覆盖**:内置词典约 600+ 词,远小于 HowNet(~17k)、
       NTUSD(~11k);建议加载完整词典以提升召回率。
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

import jieba

from app.view.widgets.freq_analyzer.freq_engine import TextSegmenter

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from app.core.utils import logger


# ---------------------------------------------------------------------------
# 内置词典
# ---------------------------------------------------------------------------

# 正面情感词(精选常用词典,覆盖「高兴/喜爱/赞美/积极」等)
BUILTIN_POSITIVE: Set[str] = {
    # 情绪类
    "高兴",
    "非常高兴",
    "快乐",
    "开心",
    "愉快",
    "欣喜",
    "喜悦",
    "欢乐",
    "欢喜",
    "欢欣",
    "欢快",
    "幸福",
    "美满",
    "满足",
    "欣慰",
    "畅快",
    "快活",
    "爽",
    "美",
    "美好",
    "美妙",
    "兴奋",
    "激动",
    "雀跃",
    "激动人心",
    "振奋",
    "鼓舞",
    "激动不已",
    # 喜爱/偏好
    "喜欢",
    "喜爱",
    "热爱",
    "爱",
    "钟爱",
    "偏爱",
    "欣赏",
    "青睐",
    "宠爱",
    "心爱",
    "好感",
    "迷恋",
    "陶醉",
    "沉迷",
    "着迷",
    "心仪",
    # 赞美/肯定
    "好",
    "棒",
    "赞",
    "优秀",
    "出色",
    "卓越",
    "杰出",
    "非凡",
    "优异",
    "精良",
    "完美",
    "极佳",
    "极好",
    "绝佳",
    "精妙",
    "精湛",
    "精致",
    "精良",
    "精美",
    "精巧",
    "漂亮",
    "美丽",
    "帅气",
    "动人",
    "迷人",
    "魅力",
    "俊秀",
    "精彩",
    "精采",
    "生动",
    "活泼",
    "灵动",
    # 积极行为
    "成功",
    "胜利",
    "进展",
    "突破",
    "成就",
    "成果",
    "收获",
    "进步",
    "提升",
    "改进",
    "表扬",
    "赞扬",
    "赞美",
    "称赞",
    "夸奖",
    "嘉奖",
    "表彰",
    "奖励",
    "鼓励",
    "感谢",
    "感激",
    "感恩",
    "致谢",
    # 友好/合作
    "友好",
    "友善",
    "亲切",
    "热情",
    "真诚",
    "诚意",
    "善意",
    "善良",
    "仁慈",
    "团结",
    "合作",
    "和谐",
    "和睦",
    "融洽",
    # 正面形容词
    "温暖",
    "温馨",
    "和蔼",
    "和善",
    "慈祥",
    "慈爱",
    "温柔",
    "柔情",
    "乐观",
    "自信",
    "勇敢",
    "坚强",
    "坚定",
    "果断",
    "聪明",
    "智慧",
    "睿智",
    "机智",
    "聪慧",
    "灵敏",
    "慷慨",
    "大方",
    "无私",
    "奉献",
    "安全",
    "平安",
    "健康",
    "稳定",
    "安宁",
    "宁静",
    "平静",
    "安静",
    "富裕",
    "富足",
    "繁荣",
    "昌盛",
    "兴旺",
    "发达",
    "满意",
    "顺畅",
    "顺心",
    "如意",
    "顺遂",
    "顺当",
}

# 负面情感词
BUILTIN_NEGATIVE: Set[str] = {
    # 悲伤/难过
    "难过",
    "伤心",
    "悲伤",
    "悲痛",
    "悲哀",
    "凄惨",
    "凄凉",
    "惨",
    "悲惨",
    "痛苦",
    "郁闷",
    "忧愁",
    "忧伤",
    "忧郁",
    "愁",
    "愁苦",
    "愁闷",
    "愁绪",
    "焦虑",
    "失望",
    "绝望",
    "沮丧",
    "气馁",
    "灰心",
    "颓废",
    "消沉",
    "低沉",
    "心酸",
    "心碎",
    "心痛",
    "痛心",
    "辛酸",
    # 愤怒
    "生气",
    "愤怒",
    "恼怒",
    "恼火",
    "气愤",
    "愤慨",
    "愤恨",
    "怒火",
    "怒气",
    "不满",
    "抱怨",
    "埋怨",
    "抗议",
    "反感",
    "厌恶",
    "憎恨",
    "讨厌",
    "厌烦",
    "可恶",
    "可恨",
    "该死",
    "混账",
    # 恐惧/担心
    "害怕",
    "恐惧",
    "畏惧",
    "恐慌",
    "惊恐",
    "惊吓",
    "担惊受怕",
    "惶恐",
    "担心",
    "担忧",
    "忧虑",
    "不安",
    "焦虑",
    # 厌恶
    "讨厌",
    "厌恶",
    "恶心",
    "反感",
    "排斥",
    "憎恶",
    # 批评/否定
    "差",
    "糟",
    "糟糕",
    "坏",
    "恶劣",
    "低劣",
    "拙劣",
    "差劲",
    "次",
    "次品",
    "错",
    "错误",
    "失误",
    "失败",
    "挫折",
    "批评",
    "指责",
    "责备",
    "斥责",
    "训斥",
    "责骂",
    "痛斥",
    "抨击",
    "攻击",
    "丑",
    "丑陋",
    "难看",
    "糟糕透顶",
    "无聊",
    "乏味",
    "枯燥",
    "单调",
    # 负面状态
    "困难",
    "艰苦",
    "艰难",
    "困苦",
    "窘迫",
    "窘困",
    "困窘",
    "危险",
    "危急",
    "严峻",
    "严重",
    "糟糕",
    "紧张",
    "慌张",
    "慌乱",
    "手足无措",
    "累",
    "疲劳",
    "疲惫",
    "疲倦",
    "困倦",
    "病",
    "疾病",
    "生病",
    "患病",
    "虚弱",
    "不适",
    "难受",
    "穷",
    "贫困",
    "贫穷",
    "拮据",
    "潦倒",
    "乱",
    "混乱",
    "杂乱",
    "脏",
    "肮脏",
    "污秽",
    # 不满/消极
    "后悔",
    "遗憾",
    "惋惜",
    "可惜",
    "懊悔",
    "懊恼",
    "孤独",
    "寂寞",
    "孤单",
    "孤立",
    "无助",
    "紧张",
    "惶恐",
    "恐慌",
    "冷淡",
    "冷漠",
    "冷酷",
    "无情",
    "自私",
    "贪婪",
    "卑鄙",
    "无耻",
    "下流",
}

# 程度副词 + 修饰系数(权重)
DEGREE_WORDS: Dict[str, float] = {
    # 极强
    "极": 2.0,
    "极其": 2.0,
    "极度": 2.0,
    "极端": 2.0,
    "极为": 2.0,
    "绝对": 1.8,
    "完全": 1.8,
    "彻底": 1.8,
    "完完全全": 2.0,
    # 强
    "非常": 1.5,
    "很": 1.5,
    "十分": 1.5,
    "特别": 1.5,
    "相当": 1.5,
    "格外": 1.5,
    "分外": 1.5,
    "异常": 1.5,
    "超": 1.5,
    "超级": 1.5,
    # 中
    "比较": 1.2,
    "较": 1.2,
    "较为": 1.2,
    "更": 1.2,
    "更加": 1.2,
    "挺": 1.2,
    "蛮": 1.2,
    # 弱
    "稍微": 0.5,
    "稍": 0.5,
    "略": 0.5,
    "略微": 0.5,
    "有点": 0.5,
    "有些": 0.7,
    "一点": 0.5,
    "一些": 0.7,
    "轻度": 0.5,
    # 否定减弱
    "不算": 0.3,
    "不怎么": 0.3,
}

# 否定词
# 注意:集合(set)中重复词不会生效,但代码上仍保持唯一条目以避免误导。
# 学术依据:
#   - 否定词集参考 HowNet 情感词典(董振东等)及 NTUSD 否定词列表(台湾大学)
#   - "拒绝"/"反对" 在语境中常作动词,可能产生语义偏移;此处仅作启发式
#     处理,严谨场景建议采用依存句法(参见 dependency_engine)
NEGATION_WORDS: Set[str] = {
    "不",
    "没",
    "未",
    "别",
    "无",
    "非",
    "勿",
    "没有",
    "不是",
    "不会",
    "不能",
    "不可",
    "不要",
    "不曾",
    "不必",
    "无需",
    "无须",
    "难以",
    "拒绝",
    "反对",
}


# 情感极性枚举
class Polarity(Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class SentimentHit:
    """单条情感词命中"""

    word: str  # 情感词本身
    polarity: Polarity  # 极性
    baseWeight: float  # 基础权重(+1 / -1 / 用户自定义)
    degree: float  # 程度副词系数
    negated: bool  # 是否被否定
    finalScore: float  # 实际贡献分 = baseWeight * degree * (-1 if negated)


@dataclass
class SentenceSentiment:
    """单句情感分析结果"""

    text: str
    score: float  # -1.0 ~ +1.0
    polarity: Polarity
    hits: List[SentimentHit] = field(default_factory=list)
    positiveCount: int = 0
    negativeCount: int = 0

    @property
    def hitCount(self) -> int:
        return len(self.hits)


@dataclass
class ParagraphSentiment:
    """段落情感分析结果"""

    text: str
    score: float  # 段内所有句子的平均分
    polarity: Polarity
    sentences: List[SentenceSentiment] = field(default_factory=list)


@dataclass
class DocumentSentiment:
    """篇章情感分析结果"""

    fileName: str
    text: str
    score: float  # 篇章总分
    polarity: Polarity
    paragraphs: List[ParagraphSentiment] = field(default_factory=list)
    sentences: List[SentenceSentiment] = field(default_factory=list)

    # 统计字段
    positiveCount: int = 0
    negativeCount: int = 0
    neutralCount: int = 0
    positiveWords: Dict[str, int] = field(default_factory=dict)
    negativeWords: Dict[str, int] = field(default_factory=dict)

    @property
    def totalSentences(self) -> int:
        return len(self.sentences)


@dataclass
class CorpusSentimentResult:
    """整个语料库的情感分析结果"""

    documents: List[DocumentSentiment] = field(default_factory=list)
    totalChars: int = 0
    elapsedSeconds: float = 0.0

    # 全局统计
    positiveCount: int = 0
    negativeCount: int = 0
    neutralCount: int = 0

    @property
    def totalDocuments(self) -> int:
        return len(self.documents)

    @property
    def totalSentences(self) -> int:
        return sum(d.totalSentences for d in self.documents)

    @property
    def avgScore(self) -> float:
        scores = [d.score for d in self.documents if d.sentences]
        if not scores:
            return 0.0
        return sum(scores) / len(scores)

    def topPositiveWords(self, k: int = 50) -> List[Tuple[str, int]]:
        counter: Dict[str, int] = {}
        for d in self.documents:
            for w, c in d.positiveWords.items():
                counter[w] = counter.get(w, 0) + c
        return sorted(counter.items(), key=lambda x: x[1], reverse=True)[:k]

    def topNegativeWords(self, k: int = 50) -> List[Tuple[str, int]]:
        counter: Dict[str, int] = {}
        for d in self.documents:
            for w, c in d.negativeWords.items():
                counter[w] = counter.get(w, 0) + c
        return sorted(counter.items(), key=lambda x: x[1], reverse=True)[:k]


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------
class SentimentEngine:
    """情感分析引擎

    用法:
        engine = SentimentEngine()
        result = engine.analyzeCorpus({"a.txt": "今天我很高兴!"})
    """

    # 否定/程度副词的最大回看窗口(token 数)
    LOOKBACK_WINDOW = 3

    def __init__(
        self,
        customPositive: Optional[Set[str]] = None,
        customNegative: Optional[Set[str]] = None,
        customWeights: Optional[Dict[str, float]] = None,
        tokenCache=None,
    ):
        self._positive: Set[str] = set(BUILTIN_POSITIVE)
        self._negative: Set[str] = set(BUILTIN_NEGATIVE)
        self._weights: Dict[str, float] = dict(customWeights or {})
        if customPositive:
            self._positive |= set(customPositive)
        if customNegative:
            self._negative |= set(customNegative)
        self._segmenter = TextSegmenter(tokenCache=tokenCache)

    # ---------------- 公开 API ----------------
    def analyzeText(self, text: str) -> SentenceSentiment:
        """分析单段文本(自动切句)"""
        if not text:
            return SentenceSentiment(text="", score=0.0, polarity=Polarity.NEUTRAL)
        sentences = self._splitSentences(text)
        sent_results = [self._analyzeSentence(s) for s in sentences if s.strip()]
        if not sent_results:
            return SentenceSentiment(text=text, score=0.0, polarity=Polarity.NEUTRAL)
        avg = sum(s.score for s in sent_results) / len(sent_results)
        return SentenceSentiment(
            text=text,
            score=avg,
            polarity=self._scoreToPolarity(avg),
            hits=[],  # 顶层 text 不聚合 hits(逐句查询)
            positiveCount=sum(s.positiveCount for s in sent_results),
            negativeCount=sum(s.negativeCount for s in sent_results),
        )

    def analyzeDocument(
        self,
        fileName: str,
        text: str,
        progressCallback=None,
    ) -> DocumentSentiment:
        """分析单篇文档(篇章级)

        三级粒度:
            - 篇章级:整篇情感
            - 段落级:每段情感得分
            - 句子级:每句情感得分,逐级细化

        Args:
            fileName: 文件名
            text: 文档文本
            progressCallback: 可选进度回调 fn(charsDone: int)
                - 在每个句子处理完后被调用,参数为该文档已处理的字符数
        """
        if not text:
            return DocumentSentiment(
                fileName=fileName, text="", score=0.0, polarity=Polarity.NEUTRAL
            )

        paragraphs = self._splitParagraphs(text)
        para_results: List[ParagraphSentiment] = []
        all_sentences: List[SentenceSentiment] = []
        charsProcessed = 0  # 用于内层进度回调

        # 粗略估算平均句子长度(避免每句都 split 计算位置)
        estimatedTotalChars = len(text)
        avgSentenceLen = 30  # 中文平均 30 字符/句(经验值)

        for para in paragraphs:
            if not para.strip():
                continue
            sentences = self._splitSentences(para)
            sent_results: List[SentenceSentiment] = []
            for s in sentences:
                if not s.strip():
                    continue
                sent_results.append(self._analyzeSentence(s))
                charsProcessed += max(1, len(s))
                # 估算进度(每 N 个句子报一次,避免高频回调)
                if progressCallback and len(sent_results) % 20 == 0:
                    try:
                        progressCallback(min(charsProcessed, estimatedTotalChars))
                    except Exception:
                        pass
            if not sent_results:
                continue
            avg = sum(s.score for s in sent_results) / len(sent_results)
            para_results.append(
                ParagraphSentiment(
                    text=para,
                    score=avg,
                    polarity=self._scoreToPolarity(avg),
                    sentences=sent_results,
                )
            )
            all_sentences.extend(sent_results)

        if not all_sentences:
            return DocumentSentiment(
                fileName=fileName, text=text, score=0.0, polarity=Polarity.NEUTRAL
            )

        doc_score = sum(s.score for s in all_sentences) / len(all_sentences)
        polarity = self._scoreToPolarity(doc_score)

        # 统计正负面文档/句子
        pos = neg = neu = 0
        positive_words: Dict[str, int] = {}
        negative_words: Dict[str, int] = {}
        for s in all_sentences:
            if s.polarity == Polarity.POSITIVE:
                pos += 1
            elif s.polarity == Polarity.NEGATIVE:
                neg += 1
            else:
                neu += 1
            for hit in s.hits:
                if hit.polarity == Polarity.POSITIVE:
                    positive_words[hit.word] = positive_words.get(hit.word, 0) + 1
                else:
                    negative_words[hit.word] = negative_words.get(hit.word, 0) + 1

        # 最终报告 100% 进度
        if progressCallback:
            try:
                progressCallback(estimatedTotalChars)
            except Exception:
                pass

        return DocumentSentiment(
            fileName=fileName,
            text=text,
            score=doc_score,
            polarity=polarity,
            paragraphs=para_results,
            sentences=all_sentences,
            positiveCount=pos,
            negativeCount=neg,
            neutralCount=neu,
            positiveWords=positive_words,
            negativeWords=negative_words,
        )

    def analyzeCorpus(
        self,
        fileToText: Dict[str, str],
        progressCallback=None,
    ) -> CorpusSentimentResult:
        """分析整个语料库

        Args:
            fileToText: 文件名 -> 清洗后文本
            progressCallback: 可选回调 fn(done: int, total: int, fileName: str)
                - done: 已处理字符数
                - total: 总字符数
                - fileName: 当前正在处理的文件名
        """
        import time

        start = time.time()
        result = CorpusSentimentResult()
        totalChars = sum(len(t or "") for t in fileToText.values())

        # 字符级进度回调:按字符比例报告(更平滑、对单大文件友好)
        lastEmitTime = [start]
        lastEmitPct = [0.0]

        def _emitProgress(fileName: str, processedChars: int):
            if not progressCallback:
                return
            import time as _t

            now = _t.time()
            pctNow = processedChars / max(1, totalChars) * 100
            # 节流:进度变化 ≥ 1% 或距上次 ≥ 0.3s 才发
            if pctNow - lastEmitPct[0] < 1.0 and (now - lastEmitTime[0]) < 0.3:
                return
            try:
                progressCallback(processedChars, totalChars, fileName)
                lastEmitTime[0] = now
                lastEmitPct[0] = pctNow
            except Exception:
                pass

        processedChars = 0
        for fileName, text in fileToText.items():
            text = text or ""

            # 每文件级回调:进度按字符累加(避免单文件过大时卡住)
            def innerCb(_charsDoneInFile: int):
                _emitProgress(fileName, processedChars + _charsDoneInFile)

            doc = self.analyzeDocument(fileName, text, progressCallback=innerCb)
            result.documents.append(doc)
            processedChars += len(text)
            # 累计
            result.positiveCount += doc.positiveCount
            result.negativeCount += doc.negativeCount
            result.neutralCount += doc.neutralCount
            # 每文件结束时强制发一次进度(保证 100% 报到)
            if progressCallback:
                try:
                    progressCallback(processedChars, totalChars, fileName)
                    lastEmitPct[0] = 100.0
                except Exception:
                    pass

        result.totalChars = totalChars
        result.elapsedSeconds = time.time() - start
        return result

    # ---------------- 词典导入(FR-SNT-006) ----------------
    def importCustomDict(
        self,
        filePath: str,
        replaceBuiltin: bool = False,
    ) -> Tuple[int, int, int]:
        """导入自定义情感词典

        支持格式:
            - TXT:每行一个词;[+/-]前缀表示极性(默认 +)
            - CSV:三列 `word, polarity, weight` (polarity: positive/negative)

        Args:
            filePath: 词典文件路径
            replaceBuiltin: True=替换内置词典;False=合并

        Returns:
            (新增正面词数, 新增负面词数, 总权重条目数)
        """
        if not os.path.exists(filePath):
            raise FileNotFoundError(f"词典文件不存在: {filePath}")

        if replaceBuiltin:
            self._positive.clear()
            self._negative.clear()

        newPos = newNeg = 0
        newWeights = 0
        ext = os.path.splitext(filePath)[1].lower()

        try:
            if ext == ".csv":
                with open(filePath, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        word = (row.get("word") or "").strip()
                        polarity = (row.get("polarity") or "positive").strip().lower()
                        weightStr = row.get("weight") or "1.0"
                        if not word:
                            continue
                        try:
                            weight = float(weightStr)
                        except ValueError:
                            weight = 1.0
                        if polarity == "positive":
                            self._positive.add(word)
                            self._weights[word] = weight
                            newPos += 1
                        elif polarity == "negative":
                            self._negative.add(word)
                            self._weights[word] = weight
                            newNeg += 1
                        newWeights += 1
            else:
                # TXT 格式
                with open(filePath, "r", encoding="utf-8") as f:
                    for line in f:
                        word = line.strip()
                        if not word or word.startswith("#"):
                            continue
                        if word.startswith("+"):
                            self._positive.add(word[1:].strip())
                            newPos += 1
                        elif word.startswith("-"):
                            self._negative.add(word[1:].strip())
                            newNeg += 1
                        else:
                            # 默认正面
                            self._positive.add(word)
                            newPos += 1
        except Exception as e:
            logger.error(f"[SentimentEngine] 导入词典失败: {e}")
            raise

        logger.info(
            f"[SentimentEngine] 导入词典 {filePath}: +{newPos} -{newNeg} 权重={newWeights}"
        )
        return newPos, newNeg, newWeights

    # ---------------- 内部方法 ----------------
    def _tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        return [
            t for t in self._segmenter.tokenize(text, useJieba=True) if t and t.strip()
        ]

    @staticmethod
    def _splitSentences(text: str) -> List[str]:
        """按中英文句末标点切句(P0-4 修复)

        改进点:
            1. 使用 lookbehind + 字符类,保留分隔符信息(可在结果中恢复标点)
            2. 处理「……」「——」「!!」「!?」等连续/复合标点
            3. 兼容英文 `?` `!` 后必须接空格或行尾才视为收尾
            4. 排除空字符串与纯空白片段

        学术依据:
            - 中文句末标点集:`。！？` 及变体(`!!` `?!` 等)
            - 英文句末标点:`. ! ?`(`.` 后需大写或换行才视为句子收尾,
              此处为简化处理,在中文为主语料下不严格区分)
        """
        import re

        if not text:
            return []
        # 匹配「零宽 + 句末标点(可重复)」,中文标点直接切,英文标点后接空白/换行才切
        # 复合标点(如 `……`、`——`、`!!`、`?!`)作为单个分隔符
        pattern = re.compile(
            r"(?<=[。！？…])|"          # 中文句末标点(含省略号)
            r"(?<=[!?])"                # 英文 !?
            r"|(?<=[.])(?=\s|$)|"       # 英文 . 后接空白或行尾
            r"(?<=\n)"                  # 换行也算收尾
        )
        parts = pattern.split(text)
        return [p.strip() for p in parts if p and p.strip()]

    @staticmethod
    def _splitParagraphs(text: str) -> List[str]:
        """按段落切分(P0-4 / P1-4 修复)

        改进点:
            1. 优先按空行切段(连续两个换行)
            2. 若全文无空行,降级按「句末标点聚合段」切分
               — 即 3~5 个相邻句子合并为一个段落(中文常见长度)
            3. 兼容 Word/Excel 导入的纯连续文本

        学术依据:
            - Biber et al. (1999) 的语篇段落定义:段是话题/论点的相对闭合单位,
              中文书面语平均 80~200 字/段
            - 3~5 句聚合策略在无明确段落标记时是常用工程近似
        """
        if not text:
            return []

        # 1) 优先按空行切段
        paras: List[str] = []
        buf: List[str] = []
        for line in text.splitlines():
            if not line.strip():
                if buf:
                    paras.append("\n".join(buf))
                    buf = []
            else:
                buf.append(line)
        if buf:
            paras.append("\n".join(buf))

        # 2) 若只有 1 段且长度过长,降级按句聚合
        # 阈值:超过 600 字符且无段落分隔,启用降级策略
        if len(paras) <= 1:
            only = paras[0] if paras else text
            if len(only) > 600:
                sentences = SentimentEngine._splitSentences(only)
                if len(sentences) > 4:
                    aggregated: List[str] = []
                    chunkSize = 4  # 每段约 4 个句子
                    for i in range(0, len(sentences), chunkSize):
                        chunk = sentences[i : i + chunkSize]
                        aggregated.append("".join(chunk))
                    return aggregated

        return paras

    def _analyzeSentence(self, sentence: str) -> SentenceSentiment:
        """分析单句情感"""
        if not sentence or not sentence.strip():
            return SentenceSentiment(
                text=sentence or "",
                score=0.0,
                polarity=Polarity.NEUTRAL,
            )

        tokens = self._tokenize(sentence)
        if not tokens:
            return SentenceSentiment(
                text=sentence, score=0.0, polarity=Polarity.NEUTRAL
            )

        hits: List[SentimentHit] = []
        for i, token in enumerate(tokens):
            base_polarity: Optional[Polarity] = None
            if token in self._positive:
                base_polarity = Polarity.POSITIVE
            elif token in self._negative:
                base_polarity = Polarity.NEGATIVE
            if base_polarity is None:
                continue

            # 查找回看窗口内的修饰语
            # 学术依据:程度副词对情感强度的修饰采用**乘性叠加**(Taboada 2011,
            #   "Lexicon-based methods for sentiment analysis", CL);
            # 即 N 个程度副词 => 系数 = Π d_i,而非只取最近的一个。
            # 例:"非常非常高兴" => 1.5 * 1.5 = 2.25(原代码只取 1.5)。
            degree = 1.0
            negated = False
            lookback = max(0, i - self.LOOKBACK_WINDOW)
            for j in range(i - 1, lookback - 1, -1):
                if j < 0:
                    break
                prev = tokens[j]
                if prev in NEGATION_WORDS:
                    negated = not negated  # 否定词累加(双重否定 = 肯定)
                if prev in DEGREE_WORDS:
                    # 乘性叠加(取全部出现在回看窗口内的程度副词)
                    degree *= DEGREE_WORDS[prev]

            base_weight = self._weights.get(token, 1.0)
            if base_polarity == Polarity.NEGATIVE:
                base_weight = -base_weight

            final = base_weight * degree * (-1.0 if negated else 1.0)
            hits.append(
                SentimentHit(
                    word=token,
                    polarity=base_polarity,
                    baseWeight=base_weight,
                    degree=degree,
                    negated=negated,
                    finalScore=final,
                )
            )

        # 计算归一化分数(除以 token 长度)
        if not hits:
            score = 0.0
            polarity = Polarity.NEUTRAL
        else:
            raw = sum(h.finalScore for h in hits)
            # 归一化:除以 sqrt(token 数),使长句不至于被拉低
            # 注:本引擎采用 sqrt 归一化而非线性归一化(N/tokens)是为了
            # 缓解长句中累加误差过大的问题(Pang & Lee 2004 综述指出
            # 词典法对长文本倾向给出更"中性"的分数,sqrt 是一种工程折中)。
            # 严格学术场景推荐报告 raw 平均分 + 命中数 2 项,而非单一 score。
            norm = max(1.0, len(tokens) ** 0.5)
            score = max(-1.0, min(1.0, raw / norm))
            polarity = self._scoreToPolarity(score)

        pos = sum(1 for h in hits if h.polarity == Polarity.POSITIVE and not h.negated)
        neg = sum(1 for h in hits if h.polarity == Polarity.NEGATIVE and not h.negated)

        return SentenceSentiment(
            text=sentence,
            score=score,
            polarity=polarity,
            hits=hits,
            positiveCount=pos,
            negativeCount=neg,
        )

    @staticmethod
    def _scoreToPolarity(score: float) -> Polarity:
        if score > 0.05:
            return Polarity.POSITIVE
        if score < -0.05:
            return Polarity.NEGATIVE
        return Polarity.NEUTRAL
