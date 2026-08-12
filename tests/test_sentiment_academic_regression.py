from app.view.widgets.freq_analyzer.sentiment_engine import (
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
