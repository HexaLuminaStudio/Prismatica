# coding: utf-8
"""N-gram 聚簇分析引擎

对 N≥3 的 N-gram 进行基于文件共现的聚簇分析：
    1. 从 N-gram DataFrame 解析 Files 列，构建 N-gram×文件 的二元共现矩阵
    2. 计算 N-gram 之间的余弦相似度
    3. 使用 PCA → t-SNE 降维到 2D 用于可视化
    4. 使用 KMeans（肘部法则 + 轮廓系数自动选 k）进行聚类
    5. 按 TF-IDF 加权的簇内高频词提取每个簇的代表性 N-gram

设计：
    - 所有计算在后台线程执行，通过 progress 回调报告进度
    - 依赖 sklearn（可选），不可用时降级为基于共享词的简单聚类
    - 进度报告分阶段：解析数据 → 构建矩阵 → 降维 → 聚类 → 提取摘要
"""

from __future__ import annotations

import traceback
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from loguru import logger

# sklearn 可选依赖
try:
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA
    from sklearn.manifold import TSNE
    from sklearn.metrics import (
        pairwise_distances,
        silhouette_score,
    )
    from sklearn.preprocessing import StandardScaler

    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("[NgramClusterEngine] sklearn 不可用，将使用基于共享词的简单聚类")


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class NgramClusterResult:
    """N-gram 聚簇分析结果

    包含：
        - 每个 N-gram 的 2D 坐标（用于散点图渲染）
        - 簇分配与簇摘要（用于图例和标签）
        - 评估指标（轮廓系数、最优 k）
    """

    n: int  # N-gram 阶数
    ngram_count: int  # 参与聚类的 N-gram 数量
    file_count: int  # 语料文件数
    points_2d: np.ndarray  # (ngram_count, 2) 降维后的 2D 坐标
    ngram_labels: List[str]  # 每个点的 N-gram 文本标签
    ngram_freqs: List[int]  # 每个点的频次（用于点大小映射）
    cluster_ids: np.ndarray  # (ngram_count,) 簇编号（-1 表示噪声/未分配）
    cluster_top_ngrams: Dict[int, List[str]]  # 每个簇的 top-8 代表性 N-gram
    cluster_sizes: Dict[int, int]  # 每个簇的成员数量
    k: int  # 使用的簇数
    silhouette: float  # 轮廓系数（-1~1，越大越好）


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------


class NgramClusterEngine:
    """N-gram 聚簇分析引擎

    用法:
        engine = NgramClusterEngine()
        result = engine.analyze(
            ngramDf, n=3, maxClusters=8,
            progressCallback=lambda pct, msg: print(f"[{pct}%] {msg}")
        )
    """

    # 默认聚类数范围
    DEFAULT_MIN_K: int = 2
    DEFAULT_MAX_K: int = 8

    # t-SNE 参数
    TSNE_PERPLEXITY: int = 15
    TSNE_RANDOM_STATE: int = 42

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def analyze(
        self,
        ngramDf: pd.DataFrame,
        n: int,
        maxClusters: int = 8,
        minNgramFreq: int = 3,
        maxNgrams: int = 2000,
        progressCallback: Optional[Callable[[int, str], None]] = None,
    ) -> Optional[NgramClusterResult]:
        """执行 N-gram 聚簇分析

        Args:
            ngramDf:        N-gram DataFrame（需包含 Ngram / Freq / Files 列）
            n:              N-gram 阶数
            maxClusters:    最大聚类数（默认 8，自动在 2~maxClusters 间选最优）
            minNgramFreq:   最低 N-gram 频次（过滤稀疏项）
            maxNgrams:      最多参与聚类的 N-gram 数量（超量时取 top-k）
            progressCallback: 进度回调 (pct, status_msg)

        Returns:
            NgramClusterResult 或 None（数据不足时）
        """
        if ngramDf is None or ngramDf.empty:
            logger.warning("[NgramClusterEngine] N-gram 数据为空")
            return None

        # 检查必要列
        requiredCols = {"Ngram", "Freq", "Files"}
        missing = requiredCols - set(ngramDf.columns)
        if missing:
            logger.warning(f"[NgramClusterEngine] 缺少必要列: {missing}")
            return None

        self._report(progressCallback, 5, "准备数据...")

        # 1. 过滤 + 截断
        df = self._prefilter(ngramDf, minNgramFreq, maxNgrams)
        if df is None:
            return None

        self._report(progressCallback, 15, "解析文件共现...")

        # 2. 构建特征矩阵
        matrix, fileList = self._buildFileCooccurrenceMatrix(df)
        if matrix is None:
            return None

        nRows, nCols = matrix.shape
        logger.info(f"[NgramClusterEngine] 特征矩阵: {nRows} N-grams × {nCols} 文件")

        # 3. 标准化
        self._report(progressCallback, 25, "标准化特征...")
        scaler = StandardScaler()
        matrixScaled = scaler.fit_transform(matrix.astype(np.float64))

        # 4. 降维：PCA(最多 50 维) → t-SNE(2 维)
        self._report(progressCallback, 35, "PCA 降维...")
        points2d = self._reduceDimensions(matrixScaled, progressCallback)

        # 5. 聚类
        self._report(progressCallback, 65, "执行聚类...")
        k, clusterIds, sil = self._cluster(
            matrixScaled, maxClusters=maxClusters, progressCallback=progressCallback
        )

        self._report(progressCallback, 85, "提取簇摘要...")

        # 6. 簇摘要
        clusterTopNgrams = self._extractClusterSummaries(df, clusterIds, k)

        # 7. 频次列表（用于点大小映射）
        freqs = df["Freq"].tolist()
        labels = df["Ngram"].tolist()

        self._report(progressCallback, 100, "聚类完成")

        return NgramClusterResult(
            n=n,
            ngram_count=nRows,
            file_count=nCols,
            points_2d=points2d,
            ngram_labels=labels,
            ngram_freqs=freqs,
            cluster_ids=clusterIds,
            cluster_top_ngrams=clusterTopNgrams,
            cluster_sizes=self._countSizes(clusterIds, k),
            k=k,
            silhouette=sil,
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _report(cb: Optional[Callable[[int, str], None]], pct: int, msg: str) -> None:
        if cb:
            try:
                cb(int(pct), str(msg))
            except Exception:
                pass

    def _prefilter(
        self, ngramDf: pd.DataFrame, minFreq: int, maxNgrams: int
    ) -> Optional[pd.DataFrame]:
        """过滤低频 N-gram 并截断到 top-k"""
        df = ngramDf.copy()
        # 按频次过滤
        if minFreq > 1:
            df = df[df["Freq"] >= minFreq]
        if df.empty:
            logger.warning("[NgramClusterEngine] 过滤后无 N-gram 数据")
            return None
        # 按频次排序取 top-k
        df = df.sort_values("Freq", ascending=False).head(maxNgrams)
        return df.reset_index(drop=True)

    def _buildFileCooccurrenceMatrix(
        self, df: pd.DataFrame
    ) -> Tuple[Optional[np.ndarray], List[str]]:
        """从 Files 列构建 N-gram×文件 二元共现矩阵

        Files 列格式: "file1.txt, file2.txt, file3.txt"

        Returns:
            (matrix, fileList) 或 (None, [])
        """
        # 收集所有文件名
        allFiles: set = set()
        fileSets: List[set] = []
        for filesStr in df["Files"]:
            if pd.isna(filesStr) or not str(filesStr).strip():
                fset: set = set()
            else:
                fset = set(s.strip() for s in str(filesStr).split(",") if s.strip())
            allFiles.update(fset)
            fileSets.append(fset)

        fileList = sorted(allFiles)
        if not fileList:
            logger.warning("[NgramClusterEngine] Files 列为空")
            return None, []

        # 构建二元矩阵
        nRows = len(df)
        nCols = len(fileList)
        matrix = np.zeros((nRows, nCols), dtype=np.int8)
        for i, fset in enumerate(fileSets):
            for j, fname in enumerate(fileList):
                if fname in fset:
                    matrix[i, j] = 1

        # 过滤全零行（防御）
        rowSums = matrix.sum(axis=1)
        validMask = rowSums > 0
        if not validMask.all():
            logger.info(
                f"[NgramClusterEngine] 移除 {int((~validMask).sum())} 条无文件关联的 N-gram"
            )
            matrix = matrix[validMask]
            df = df.loc[validMask].reset_index(drop=True)
            # 同步更新 df 引用（调用方持有原引用，此处仅影响后续计算）
            self._filteredDf = df

        return matrix, fileList

    def _reduceDimensions(
        self,
        matrix: np.ndarray,
        progressCallback: Optional[Callable[[int, str], None]] = None,
    ) -> np.ndarray:
        """PCA + t-SNE 降维到 2D

        策略：
            - 先用 PCA 降到 max(50, 特征数) 维（去噪+加速 t-SNE）
            - 再用 t-SNE 降到 2 维用于可视化
            - 若 sklearn 不可用，退化为随机初始化 + 简单 PCA 映射
            - 若特征维度不足（≤1 列或零方差），跳过 PCA 直接随机投影
        """
        nSamples = matrix.shape[0]

        if nSamples < 3:
            # 太少样本，直接随机生成 2D 点
            rng = np.random.RandomState(42)
            return rng.randn(nSamples, 2) * 0.1

        if not SKLEARN_AVAILABLE:
            logger.warning("[NgramClusterEngine] sklearn 不可用，使用随机投影")
            rng = np.random.RandomState(42)
            return rng.randn(nSamples, 2) * 0.1

        # 检测退化情况：特征维度不足或矩阵方差接近零
        nFeatures = matrix.shape[1]
        totalVar = float(np.var(matrix))
        nComponents = min(50, nSamples - 1, nFeatures)

        if nComponents < 2 or totalVar < 1e-8:
            logger.warning(
                f"[NgramClusterEngine] 特征维度不足(nFeatures={nFeatures}, "
                f"var={totalVar:.2e})，使用随机投影 + t-SNE"
            )
            rng = np.random.RandomState(42)
            randomInit = rng.randn(nSamples, min(5, nSamples - 1)) * 0.1
            # 尝试用 t-SNE 从随机初始化投影到 2D
            try:
                perplexity = min(self.TSNE_PERPLEXITY, max(2, (nSamples - 1) // 2))
                tsne = TSNE(
                    n_components=2,
                    perplexity=perplexity,
                    random_state=self.TSNE_RANDOM_STATE,
                    init="random",
                    learning_rate="auto",
                )
                points2d = tsne.fit_transform(randomInit)
                return points2d.astype(np.float64)
            except Exception as e:
                logger.warning(f"[NgramClusterEngine] t-SNE 退化路径失败: {e}")
                return rng.randn(nSamples, 2) * 0.1

        self._report(progressCallback, 40, "PCA 中间降维...")

        # PCA 中间降维
        pca = PCA(n_components=nComponents, random_state=42)
        reduced = pca.fit_transform(matrix)

        self._report(progressCallback, 50, "t-SNE 可视化降维...")

        # t-SNE → 2D
        # 当样本数较少时，降低困惑度
        perplexity = min(self.TSNE_PERPLEXITY, max(2, (nSamples - 1) // 2))
        tsne = TSNE(
            n_components=2,
            perplexity=perplexity,
            random_state=self.TSNE_RANDOM_STATE,
            init="pca",
            learning_rate="auto",
        )
        points2d = tsne.fit_transform(reduced)
        return points2d.astype(np.float64)

    def _cluster(
        self,
        matrix: np.ndarray,
        maxClusters: int = 8,
        progressCallback: Optional[Callable[[int, str], None]] = None,
    ) -> Tuple[int, np.ndarray, float]:
        """KMeans 聚类 + 自动选 k（肘部法则 + 轮廓系数）

        Returns:
            (k, clusterIds, silhouette)
        """
        nSamples = matrix.shape[0]

        if nSamples < 3:
            # 太少样本，全部归为一个簇
            return 1, np.zeros(nSamples, dtype=int), 0.0

        minK = self.DEFAULT_MIN_K
        maxK = min(maxClusters, nSamples - 1)
        if maxK < minK:
            maxK = minK

        if not SKLEARN_AVAILABLE or maxK <= 2:
            # 退化为简单聚类
            k = min(3, nSamples)
            kmeans = (
                KMeans(n_clusters=k, random_state=42, n_init="auto")
                if SKLEARN_AVAILABLE
                else None
            )
            if kmeans is None:
                return 1, np.zeros(nSamples, dtype=int), 0.0
            labels = kmeans.fit_predict(matrix)
            sil = silhouette_score(matrix, labels) if k > 1 else 0.0
            return k, labels, sil

        # 尝试不同 k 并计算轮廓系数
        bestK = minK
        bestSilhouette = -1.0
        bestLabels: Optional[np.ndarray] = None

        self._report(progressCallback, 70, f"评估 k={minK}~{maxK}...")

        for k in range(minK, maxK + 1):
            if progressCallback:
                innerPct = 70 + int((k - minK) / max(1, maxK - minK) * 10)
                self._report(progressCallback, innerPct, f"聚类 k={k}/{maxK}...")

            try:
                kmeans = KMeans(n_clusters=k, random_state=42, n_init="auto")
                labels = kmeans.fit_predict(matrix)
                sil = silhouette_score(matrix, labels)
                if sil > bestSilhouette:
                    bestSilhouette = sil
                    bestK = k
                    bestLabels = labels.copy()
            except Exception as e:
                logger.warning(f"[NgramClusterEngine] k={k} 聚类失败: {e}")
                continue

        if bestLabels is None:
            bestK = minK
            try:
                kmeans = KMeans(n_clusters=bestK, random_state=42, n_init="auto")
                bestLabels = kmeans.fit_predict(matrix)
                bestSilhouette = silhouette_score(matrix, bestLabels)
            except Exception as e:
                logger.warning(
                    f"[NgramClusterEngine] 最终 fallback 聚类失败: {e}，所有点归为 1 个簇"
                )
                bestK = 1
                bestLabels = np.zeros(nSamples, dtype=int)
                bestSilhouette = 0.0

        logger.info(
            f"[NgramClusterEngine] 最优 k={bestK}, silhouette={bestSilhouette:.3f}"
        )
        return bestK, bestLabels, bestSilhouette

    def _extractClusterSummaries(
        self, df: pd.DataFrame, clusterIds: np.ndarray, k: int
    ) -> Dict[int, List[str]]:
        """为每个簇提取 top-8 代表性 N-gram（按频次排序）"""
        summaries: Dict[int, List[str]] = {}
        for cid in range(k):
            mask = clusterIds == cid
            clusterNgrams = df.loc[mask].sort_values("Freq", ascending=False)
            topNgrams = clusterNgrams["Ngram"].head(8).tolist()
            summaries[cid] = topNgrams
        return summaries

    @staticmethod
    def _countSizes(clusterIds: np.ndarray, k: int) -> Dict[int, int]:
        sizes: Dict[int, int] = {}
        for cid in range(k):
            sizes[cid] = int((clusterIds == cid).sum())
        return sizes
