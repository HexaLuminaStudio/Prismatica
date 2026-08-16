from app.core.services.insight_prompts import (
    buildSentimentPrompt,
    summarizeSentimentData,
)
from app.view.widgets.freq_analyzer.sentiment_engine import (
    CorpusSentimentResult,
    DocumentSentiment,
    Polarity,
    SentenceSentiment,
    SentimentEngine,
    SentimentHit,
)


def _sentence(score: float, hits: int) -> SentenceSentiment:
    hitList = [
        SentimentHit("word", Polarity.POSITIVE, 1.0, 1.0, False, 1.0)
        for _ in range(hits)
    ]
    return SentenceSentiment("sentence", score, Polarity.POSITIVE, hitList)


def testParagraphsAreNeverInventedFromFixedSentenceChunks():
    text = "Sentence. " * 120

    assert SentimentEngine._splitParagraphs(text) == [text]


def testExplicitBlankLinesRemainParagraphBoundaries():
    assert SentimentEngine._splitParagraphs("First.\n\nSecond.") == ["First.", "Second."]


def testDocumentScoreWeightsSentenceEvidenceInsteadOfSentenceCount():
    score = SentimentEngine._aggregateSentenceScores(
        [_sentence(1.0, 3), _sentence(-1.0, 1), _sentence(0.0, 0)]
    )

    assert score == 0.5


def testSentimentPromptSeparatesSentenceAndDocumentCounts() -> None:
    sentences = [_sentence(0.8, 1), _sentence(0.6, 1)]
    document = DocumentSentiment(
        fileName="demo.txt",
        text="good. great.",
        score=0.7,
        polarity=Polarity.POSITIVE,
        sentences=sentences,
        positiveCount=2,
    )
    result = CorpusSentimentResult(
        documents=[document],
        positiveCount=2,
    )

    summary = summarizeSentimentData(result)
    prompt = buildSentimentPrompt(summary, {"corpusName": "demo"})["user"]

    assert summary["positiveSentenceCount"] == 2
    assert summary["positiveDocumentCount"] == 1
    assert "正面 2 句" in prompt
    assert "正面 1 篇" in prompt
    assert "正面 2 篇" not in prompt
