# coding: utf-8
"""全局停用词目录及持久化配置服务。"""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Iterable, List

from app.core.utils import cfg, logger, qconfig


DEFAULT_STOPWORDS_ZH = {
    "的", "了", "和", "是", "在", "就", "都", "而", "及", "与", "或",
    "一个", "没有", "我们", "你们", "他们", "它们", "这个", "那个", "这样",
    "那样", "什么", "怎么", "为什么", "因为", "所以", "但是", "如果", "虽然",
    "然后", "现在", "可以", "应该", "需要", "已经", "还", "也", "又", "再",
    "才", "只", "就是", "不是", "只是",
}

DEFAULT_STOPWORDS_EN = {
    "the", "a", "an", "and", "or", "but", "if", "of", "at", "by", "for",
    "with", "to", "in", "on", "is", "are", "was", "were", "be", "been",
    "being", "this", "that", "these", "those", "it", "its", "i", "you", "he",
    "she", "we", "they", "them", "their",
}


def normalizeStopwords(words: Iterable[str]) -> List[str]:
    """去除空行、注释与重复项，同时保持首次出现顺序。"""
    seen = set()
    normalizedWords: List[str] = []
    for rawWord in words:
        word = str(rawWord or "").strip().replace("\ufeff", "")
        if not word or word.startswith("#") or word in seen:
            continue
        seen.add(word)
        normalizedWords.append(word)
    return normalizedWords


def parseStopwordsFromText(text: str) -> List[str]:
    """从多行文本解析停用词。"""
    return normalizeStopwords((text or "").splitlines())


@functools.lru_cache(maxsize=1)
def _defaultStopwordTuple() -> tuple[str, ...]:
    words = DEFAULT_STOPWORDS_ZH | DEFAULT_STOPWORDS_EN
    return tuple(sorted(words, key=lambda word: (word.isascii(), word.casefold())))


def defaultStopwords() -> List[str]:
    """返回内置中英文停用词表的独立副本。"""
    return list(_defaultStopwordTuple())


def loadStopwordsFromFile(filePath: str) -> List[str]:
    """从 TXT 文件加载停用词，并兼容常见中英文编码。"""
    path = Path(filePath)
    if not filePath or not path.is_file():
        raise FileNotFoundError(f"停用词文件不存在: {filePath}")

    text = None
    for encoding in ("utf-8-sig", "utf-8", "gbk", "utf-16", "latin-1"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = path.read_text(encoding="utf-8", errors="ignore")
    return parseStopwordsFromText(text)


def saveStopwordsToFile(filePath: str, words: Iterable[str]) -> None:
    """将停用词以 UTF-8、每行一个词的格式导出。"""
    normalizedWords = normalizeStopwords(words)
    content = [
        "# 停用词列表 (UTF-8,每行一个词)",
        f"# 共 {len(normalizedWords)} 个",
        *normalizedWords,
    ]
    Path(filePath).write_text("\n".join(content) + "\n", encoding="utf-8")


class StopwordService:
    """提供所有分析页面共用的停用词配置来源。"""

    def isEnabled(self) -> bool:
        return bool(qconfig.get(cfg.analysisStopwordsEnabled))

    def setEnabled(self, isEnabled: bool, save: bool = True) -> None:
        qconfig.set(
            cfg.analysisStopwordsEnabled,
            bool(isEnabled),
            save=save,
        )

    def words(self) -> List[str]:
        rawValue = qconfig.get(cfg.analysisStopwordsJson)
        if rawValue in (None, ""):
            return defaultStopwords()
        if isinstance(rawValue, list):
            return normalizeStopwords(rawValue)
        try:
            decodedValue = json.loads(str(rawValue))
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.warning("[StopwordService] 停用词配置无法解析，已回退内置默认表")
            return defaultStopwords()
        if not isinstance(decodedValue, list):
            logger.warning("[StopwordService] 停用词配置不是数组，已回退内置默认表")
            return defaultStopwords()
        return normalizeStopwords(decodedValue)

    def saveWords(self, words: Iterable[str], save: bool = True) -> List[str]:
        normalizedWords = normalizeStopwords(words)
        qconfig.set(
            cfg.analysisStopwordsJson,
            json.dumps(normalizedWords, ensure_ascii=False),
            save=save,
        )
        return normalizedWords

    def resetWords(self, save: bool = True) -> None:
        qconfig.set(cfg.analysisStopwordsJson, "", save=save)


stopwordService = StopwordService()


__all__ = [
    "DEFAULT_STOPWORDS_EN",
    "DEFAULT_STOPWORDS_ZH",
    "StopwordService",
    "defaultStopwords",
    "loadStopwordsFromFile",
    "normalizeStopwords",
    "parseStopwordsFromText",
    "saveStopwordsToFile",
    "stopwordService",
]
