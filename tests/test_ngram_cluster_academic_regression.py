import numpy as np
import pandas as pd

from app.core.services.insight_prompts import buildNgramClusterPrompt
from app.view.widgets.freq_analyzer.ngram_cluster_engine import NgramClusterEngine


def testFileDistributionFeaturesAreNonNegativeAndUnitNormalized():
    matrix = np.array([[1, 0, 1], [1, 1, 0], [0, 1, 0]], dtype=np.int8)

    features = NgramClusterEngine._buildFileDistributionFeatures(matrix)

    assert np.all(features >= 0)
    assert np.allclose(np.linalg.norm(features, axis=1), 1.0)


def testNgramPromptDoesNotTurnFileClustersIntoSemanticThemes():
    prompt = buildNgramClusterPrompt(
        {
            "n": 3,
            "ngramCount": 10,
            "clusterCount": 2,
            "k": 2,
            "silhouette": 0.4,
            "featureMethod": "file-idf cosine",
            "embeddingMethod": "PCA + t-SNE (visualization only)",
            "clusters": [],
        },
        {"corpusName": "test"},
    )["user"]

    assert "不要把文件共现簇直接命名为语义主题" in prompt
    assert "不得解释簇间全局距离" in prompt


def testRowsWithoutFileEvidenceAreRemovedFromLabelsAndFeatures():
    engine = NgramClusterEngine()
    frame = pd.DataFrame(
        {
            "Ngram": ["a b c", "d e f", "g h i"],
            "Freq": [10, 9, 8],
            "Files": ["one.txt", "", "two.txt"],
        }
    )

    result = engine.analyze(frame, n=3, minNgramFreq=1, maxClusters=2)

    assert result is not None
    assert result.ngram_count == 2
    assert result.ngram_labels == ["a b c", "g h i"]
    assert len(result.cluster_ids) == 2
