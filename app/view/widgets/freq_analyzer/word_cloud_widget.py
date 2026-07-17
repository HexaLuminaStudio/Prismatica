# coding: utf-8
"""
词语云图面板(对标 wordcloud,纯 matplotlib 实现)

按需求文档 v3 §2.5.1:
    FR-WDC-001 基础词云生成
    FR-WDC-002 自定义形状(矩形/圆形/椭圆)
    FR-WDC-003 配色方案(5 种内置)
    FR-WDC-004 字体设置(中英文混排)
    FR-WDC-005 词云导出(PNG/SVG)

UI 风格与其他子页面保持一致:
    - 外边距 20/20/20/20
    - SubtitleLabel 标题
    - CardWidget 16/12 内边距
    - CaptionLabel 提示 11px
    - 与 word_analysis_widget / collocation_widget 同款布局
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
)

import matplotlib

matplotlib.use("QtAgg", force=False)
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.backends.backend_qtagg import (  # noqa: E402
    FigureCanvasQTAgg as FigureCanvas,
)

from app.view.widgets.freq_analyzer.freq_engine import TextSegmenter
from app.view.widgets.freq_analyzer.token_cache import TokenCache
from app.view.widgets.freq_analyzer.ui_helpers import _showInfoBar
from app.view.widgets.freq_analyzer.word_analysis_engine import (
    DEFAULT_CONTENT_POS,
    WordAnalysisEngine,
)
from app.view.widgets.freq_analyzer.word_cloud_engine import (
    BackgroundColor,
    CloudShape,
    ColorScheme,
    RotationMode,
    WordCloudConfig,
    WordCloudEngine,
    WordCloudResult,
    _availableCjkFonts,
    _WORDCLOUD_AVAILABLE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 后台渲染线程
# ---------------------------------------------------------------------------
class WordCloudWorker(QThread):
    """词云渲染后台线程"""

    progress = Signal(int, str)
    finished = Signal(object)  # WordCloudResult
    failed = Signal(str)

    def __init__(
        self,
        corpusStore,
        segmenter: TextSegmenter,
        config: WordCloudConfig,
        includePos: bool = False,
        posTags: Optional[List[str]] = None,
    ):
        super().__init__()
        self._corpusStore = corpusStore
        self._segmenter = segmenter
        self._config = config
        self._includePos = includePos
        self._posTags = posTags
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            self.progress.emit(5, "正在加载语料...")
            fileToText = self._corpusStore.effectiveTexts()
            fileNames = list(fileToText.keys())
            n = len(fileNames)
            if n == 0:
                self.failed.emit("语料库为空")
                return

            self.progress.emit(10, f"共 {n} 个文件,开始分词...")

            from app.view.widgets.freq_analyzer.token_cache import (
                backendModelVersion,
            )

            tokenCache: Optional[TokenCache] = (
                self._corpusStore.tokenCache()
                if hasattr(self._corpusStore, "tokenCache")
                else None
            )
            modelVer = backendModelVersion("jieba")

            allTokens: List[str] = []
            allPosTags: List[str] = []  # 与 allTokens 一一对应
            # 是否需要词性:勾选了词性过滤时计算
            needPos = bool(self._config.posFilter)
            for idx, name in enumerate(fileNames, start=1):
                if self._cancel:
                    return
                text = fileToText.get(name, "")
                if not text:
                    continue
                if tokenCache is not None:
                    tokens = tokenCache.getOrCompute(
                        text=text,
                        backendName="jieba",
                        modelVersion=modelVer,
                        computeFn=lambda t: self._segmenter._jiebaCut(t),
                    )
                else:
                    tokens = self._segmenter._jiebaCut(text)
                allTokens.extend(tokens)
                # 词性标注(按需)
                if needPos:
                    from app.view.widgets.freq_analyzer.freq_engine import (
                        posTagBatch,
                    )

                    posResults = posTagBatch([text])
                    if posResults and posResults[0]:
                        # 长度对齐:posTagBatch 返回 token 级 (word, tag) 列表
                        for word, _tag in posResults[0]:
                            allPosTags.append(_tag)
                        # 若 posTags 与 tokens 长度不一致,补齐
                        if len(allPosTags) < len(allTokens):
                            allPosTags.extend(
                                ["x"] * (len(allTokens) - len(allPosTags))
                            )

                pct = 10 + int(60 * idx / n)
                self.progress.emit(pct, f"分词 {idx}/{n}")

            if self._cancel:
                return

            self.progress.emit(75, "正在统计词频...")
            # 复用 WordAnalysisEngine 计算高频词(若勾选了词性过滤,会按 posFilter 过滤)
            engine = WordAnalysisEngine()
            metrics = engine.analyze(
                tokens=allTokens,
                posTags=allPosTags if allPosTags else None,
                topN=self._config.topN,
                minWordLength=self._config.minWordLength,
                minFreq=self._config.minFreq,
                posFilter=self._config.posFilter,
            )
            wordFreqs: List[Tuple[str, int]] = [
                (e.word, e.freq) for e in metrics.highFreqWords
            ]

            if self._cancel:
                return

            self.progress.emit(85, "正在渲染词云...")
            wcEngine = WordCloudEngine()
            result = wcEngine.render(wordFreqs, self._config)

            self.progress.emit(100, "完成!")
            self.finished.emit(result)

        except Exception as e:
            import traceback

            logger.exception(f"[WordCloudWorker] 失败: {e}")
            self.failed.emit(f"{e}\n{traceback.format_exc()}")


# ---------------------------------------------------------------------------
# 主面板
# ---------------------------------------------------------------------------
class WordCloudWidget(QWidget):
    """词语云图主面板"""

    def __init__(self, parent: Optional[QWidget] = None, corpusStore=None):
        super().__init__(parent=parent)
        self._corpusStore = corpusStore
        self._worker: Optional[WordCloudWorker] = None
        self._result: Optional[WordCloudResult] = None
        self._lastFigure: Optional[Figure] = None
        # 词性过滤复选框(预先初始化,避免 CheckBox 导入失败时 _gatherPosFilter 报错)
        self._posCheckBoxes: Dict[str, "qfluentwidgets.CheckBox"] = {}

        # 分词器
        tokenCache = corpusStore.tokenCache() if corpusStore is not None else None
        self._segmenter = TextSegmenter(tokenCache=tokenCache)

        self._initUi()

        if corpusStore is not None:
            self._bindCorpusStore(corpusStore)

    # ------------------------------------------------------------------
    # 语料库绑定
    # ------------------------------------------------------------------
    def setCorpusStore(self, store):
        self._corpusStore = store
        tokenCache = store.tokenCache() if store is not None else None
        self._segmenter = TextSegmenter(tokenCache=tokenCache)
        self._updateCorpusInfo()

    def _bindCorpusStore(self, store):
        if hasattr(store, "filesAdded"):
            store.filesAdded.connect(lambda *_: self._updateCorpusInfo())
        if hasattr(store, "filesRemoved"):
            store.filesRemoved.connect(lambda *_: self._updateCorpusInfo())
        if hasattr(store, "cleanRuleChanged"):
            store.cleanRuleChanged.connect(lambda: self._updateCorpusInfo())
        self._updateCorpusInfo()

    def _updateCorpusInfo(self):
        if self._corpusStore is None:
            self.statusLabel.setText("未加载语料库")
            self.runBtn.setEnabled(False)
            return
        n = self._corpusStore.fileCount()
        chars = self._corpusStore.totalChars()
        self.statusLabel.setText(f"语料库: {n} 个文件,共 {chars:,} 字符")
        self.runBtn.setEnabled(n > 0)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _initUi(self) -> None:
        # 外层布局(与其他子页面一致)
        outerLayout = QVBoxLayout(self)
        outerLayout.setContentsMargins(20, 20, 20, 20)
        outerLayout.setSpacing(12)

        # 标题
        titleLabel = SubtitleLabel("词语云图", self)
        outerLayout.addWidget(titleLabel)

        # 说明
        hint = CaptionLabel(
            "基于词频生成可视化词云图(纯 matplotlib 实现,无外部 wordcloud 依赖);"
            "支持矩形/圆形/椭圆形状、5 种配色方案、中英文字体自动混排。",
            self,
        )
        hint.setStyleSheet("color: #888; font-size: 12px;")
        hint.setWordWrap(True)
        outerLayout.addWidget(hint)

        # 滚动容器
        self._scrollArea = ScrollArea(self)
        self._scrollArea.setWidgetResizable(True)
        self._scrollArea.setStyleSheet("border: none; background: transparent;")
        outerLayout.addWidget(self._scrollArea, 1)

        self._contentWidget = QWidget(self._scrollArea)
        self._contentWidget.setObjectName("wordCloudContent")
        root = QVBoxLayout(self._contentWidget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        self._scrollArea.setWidget(self._contentWidget)

        # 参数卡片
        root.addWidget(self._buildParamCard())

        # 状态 / 摘要
        self._statusRow = self._buildStatusRow()
        root.addWidget(self._statusRow)

        # 词云画布
        root.addWidget(self._buildCloudCard(), 1)

    def _buildParamCard(self) -> CardWidget:
        """参数配置卡片"""
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(StrongBodyLabel("1. 生成参数", card))

        # ---- 第 1 行: Top-N / 最小词长 / 最低频次 ----
        row1 = QHBoxLayout()
        row1.setSpacing(16)
        row1.addWidget(BodyLabel("Top-N:", card))
        self.topNSpin = SpinBox(card)
        self.topNSpin.setRange(20, 500)
        self.topNSpin.setValue(150)
        row1.addWidget(self.topNSpin)

        row1.addWidget(BodyLabel("最小词长:", card))
        self.minLenSpin = SpinBox(card)
        self.minLenSpin.setRange(1, 10)
        self.minLenSpin.setValue(2)
        row1.addWidget(self.minLenSpin)

        row1.addWidget(BodyLabel("最低频次:", card))
        self.minFreqSpin = SpinBox(card)
        self.minFreqSpin.setRange(1, 100)
        self.minFreqSpin.setValue(2)
        row1.addWidget(self.minFreqSpin)

        row1.addStretch(1)
        layout.addLayout(row1)

        # ---- 第 2 行: 形状 / 配色 / 背景 ----
        row2 = QHBoxLayout()
        row2.setSpacing(16)
        row2.addWidget(BodyLabel("形状:", card))
        self.shapeCombo = ComboBox(card)
        for s in CloudShape:
            label = {
                CloudShape.RECTANGLE: "矩形",
                CloudShape.CIRCLE: "圆形",
                CloudShape.ELLIPSE: "椭圆",
                CloudShape.HEART: "爱心",
            }[s]
            self.shapeCombo.addItem(label, userData=s)
        self.shapeCombo.setCurrentIndex(1)  # 默认圆形
        row2.addWidget(self.shapeCombo)

        row2.addWidget(BodyLabel("配色:", card))
        self.colorCombo = ComboBox(card)
        for c in ColorScheme:
            label = {
                ColorScheme.WARM: "暖色",
                ColorScheme.COOL: "冷色",
                ColorScheme.GRADIENT: "蓝紫渐变",
                ColorScheme.ACADEMIC: "学术风",
                ColorScheme.RANDOM: "随机",
            }[c]
            self.colorCombo.addItem(label, userData=c)
        self.colorCombo.setCurrentIndex(1)  # 默认冷色
        row2.addWidget(self.colorCombo)

        row2.addWidget(BodyLabel("背景:", card))
        self.bgCombo = ComboBox(card)
        for b in BackgroundColor:
            label = {
                BackgroundColor.WHITE: "白色",
                BackgroundColor.BLACK: "黑色",
                BackgroundColor.TRANSPARENT: "透明",
            }[b]
            self.bgCombo.addItem(label, userData=b)
        self.bgCombo.setCurrentIndex(0)
        row2.addWidget(self.bgCombo)

        row2.addStretch(1)
        layout.addLayout(row2)

        # ---- 第 3 行: 字号范围 / 旋转模式 / 字体 ----
        row3 = QHBoxLayout()
        row3.setSpacing(16)
        row3.addWidget(BodyLabel("字号范围:", card))
        self.minFontSpin = SpinBox(card)
        self.minFontSpin.setRange(8, 40)
        self.minFontSpin.setValue(14)
        row3.addWidget(self.minFontSpin)
        row3.addWidget(BodyLabel("~", card))
        self.maxFontSpin = SpinBox(card)
        self.maxFontSpin.setRange(20, 200)
        self.maxFontSpin.setValue(80)
        row3.addWidget(self.maxFontSpin)

        row3.addWidget(BodyLabel("旋转:", card))
        self.rotCombo = ComboBox(card)
        for r in RotationMode:
            label = {
                RotationMode.HORIZONTAL_ONLY: "仅水平",
                RotationMode.MOSTLY_HORIZONTAL: "70% 水平",
                RotationMode.RANDOM: "随机",
            }[r]
            self.rotCombo.addItem(label, userData=r)
        self.rotCombo.setCurrentIndex(1)
        row3.addWidget(self.rotCombo)

        row3.addWidget(BodyLabel("字体:", card))
        self.fontCombo = ComboBox(card)
        cjkFonts = _availableCjkFonts()
        for f in cjkFonts:
            self.fontCombo.addItem(f)
        if cjkFonts:
            self.fontCombo.setCurrentIndex(0)
        row3.addWidget(self.fontCombo, 1)

        layout.addLayout(row3)

        # ---- 第 4 行: 词性过滤(可选) ----
        row4 = QHBoxLayout()
        row4.setSpacing(16)
        row4.addWidget(BodyLabel("词性过滤:", card))
        try:
            from qfluentwidgets import CheckBox

            commonPos = [
                ("n", "名词"),
                ("v", "动词"),
                ("a", "形容词"),
                ("d", "副词"),
                ("r", "代词"),
            ]
            for tag, label in commonPos:
                cb = CheckBox(label, card)
                cb.setProperty("posTag", tag)
                self._posCheckBoxes[tag] = cb
                row4.addWidget(cb)
        except ImportError:
            # 即使 CheckBox 不可用,_posCheckBoxes 已为空 dict,_gatherPosFilter 安全
            logger.warning("[WordCloudWidget] CheckBox 不可用,词性过滤已禁用")
        row4.addStretch(1)
        layout.addLayout(row4)

        # ---- 操作按钮 ----
        rowBtn = QHBoxLayout()
        self.runBtn = PrimaryPushButton("生成词云", card)
        self.runBtn.setIcon(FIF.PLAY)
        self.runBtn.clicked.connect(self._onRunClicked)
        rowBtn.addWidget(self.runBtn)

        self.cancelBtn = PushButton("取消", card)
        self.cancelBtn.setIcon(FIF.CLOSE)
        self.cancelBtn.clicked.connect(self._onCancelClicked)
        self.cancelBtn.setEnabled(False)
        rowBtn.addWidget(self.cancelBtn)

        self.exportPngBtn = PushButton("导出 PNG", card)
        self.exportPngBtn.setIcon(FIF.SAVE)
        self.exportPngBtn.clicked.connect(lambda: self._export("png"))
        self.exportPngBtn.setEnabled(False)
        rowBtn.addWidget(self.exportPngBtn)

        self.exportSvgBtn = PushButton("导出 SVG", card)
        self.exportSvgBtn.setIcon(FIF.SAVE)
        self.exportSvgBtn.clicked.connect(lambda: self._export("svg"))
        self.exportSvgBtn.setEnabled(False)
        rowBtn.addWidget(self.exportSvgBtn)

        rowBtn.addStretch(1)
        layout.addLayout(rowBtn)

        return card

    def _buildStatusRow(self) -> CardWidget:
        """状态 / 摘要行"""
        card = CardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 8, 16, 8)

        self.statusLabel = CaptionLabel("就绪", card)
        self.statusLabel.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.statusLabel)

        self.summaryLabel = CaptionLabel("", card)
        self.summaryLabel.setStyleSheet("color: #1890ff; font-size: 11px;")
        layout.addStretch(1)
        layout.addWidget(self.summaryLabel)

        return card

    def _buildCloudCard(self) -> CardWidget:
        """词云画布卡片"""
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(StrongBodyLabel("2. 词云预览", card))

        # 画布
        self._figure = Figure(figsize=(8, 6), dpi=100, facecolor="white")
        self._ax = self._figure.add_subplot(111)
        self._ax.set_xlim(0, 800)
        self._ax.set_ylim(0, 600)
        self._ax.set_aspect("equal")
        self._ax.axis("off")
        self._ax.set_facecolor("white")
        self._ax.text(
            0.5,
            0.5,
            "等待生成...",
            ha="center",
            va="center",
            transform=self._ax.transAxes,
            color="#999",
            fontsize=14,
        )

        self._canvas = FigureCanvas(self._figure)
        layout.addWidget(self._canvas, 1)

        return card

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _gatherPosFilter(self) -> Optional[List[str]]:
        """收集已勾选的词性"""
        tags = [tag for tag, cb in self._posCheckBoxes.items() if cb.isChecked()]
        return tags if tags else None

    def _gatherConfig(self) -> WordCloudConfig:
        """从 UI 收集 WordCloudConfig"""
        return WordCloudConfig(
            width=800,
            height=600,
            topN=self.topNSpin.value(),
            minWordLength=self.minLenSpin.value(),
            minFreq=self.minFreqSpin.value(),
            shape=self.shapeCombo.currentData(),
            colorScheme=self.colorCombo.currentData(),
            background=self.bgCombo.currentData(),
            minFontSize=self.minFontSpin.value(),
            maxFontSize=self.maxFontSpin.value(),
            rotationMode=self.rotCombo.currentData(),
            fontPath=self.fontCombo.currentText(),
            posFilter=self._gatherPosFilter(),
        )

    def _onRunClicked(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if self._corpusStore is None or self._corpusStore.fileCount() == 0:
            _showInfoBar("warning", "无法生成", "请先在「语料导入」中加载语料", self)
            return

        config = self._gatherConfig()

        self.runBtn.setEnabled(False)
        self.cancelBtn.setEnabled(True)
        self.exportPngBtn.setEnabled(False)
        self.exportSvgBtn.setEnabled(False)
        self.statusLabel.setText("生成中...")
        self.summaryLabel.setText("")

        self._worker = WordCloudWorker(
            corpusStore=self._corpusStore,
            segmenter=self._segmenter,
            config=config,
        )
        self._worker.progress.connect(self._onProgress)
        self._worker.finished.connect(self._onFinished)
        self._worker.failed.connect(self._onFailed)
        self._worker.start()

    def _onCancelClicked(self):
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(2000)
        self._resetUi()

    def _onProgress(self, pct: int, msg: str):
        self.statusLabel.setText(f"[{pct}%] {msg}")

    def _onFailed(self, err: str):
        self._resetUi()
        _showInfoBar("error", "生成失败", err[:200], self, duration=4000)

    def _onFinished(self, result: WordCloudResult):
        if result.errorMessage:
            # 引擎返回的错误(例如 wordcloud 未安装)
            self._resetUi()
            _showInfoBar(
                "error",
                "生成失败",
                result.errorMessage[:300],
                self,
                duration=5000,
            )
            self.statusLabel.setText("失败")
            return
        self._result = result
        self._resetUi()
        self._renderCloud(result)
        placedN = result.placedCount
        skippedN = result.skippedCount
        self.summaryLabel.setText(
            f"已放置 <b>{placedN}</b> 个词"
            + (f" / 跳过 {skippedN}" if skippedN > 0 else "")
            + f" · 耗时 {result.elapsedSeconds:.2f}s"
        )
        self.exportPngBtn.setEnabled(True)
        self.exportSvgBtn.setEnabled(True)
        _showInfoBar(
            "success",
            "生成完成",
            f"成功放置 {placedN} 个词,耗时 {result.elapsedSeconds:.2f}s",
            self,
            duration=2500,
        )

    def _resetUi(self):
        self.runBtn.setEnabled(True)
        self.cancelBtn.setEnabled(False)

    def _renderCloud(self, result: WordCloudResult):
        """渲染词云到 Figure(基于 wordcloud 库输出)"""
        if result is None or result.wordCloud is None:
            return

        # 直接使用 wordcloud 库渲染好的图像数组(已含碰撞检测与布局)
        imgArray = result.wordCloud.to_array()

        # 处理背景
        bgData = self.bgCombo.currentData()
        isTransparent = bgData == BackgroundColor.TRANSPARENT
        bgColor = "none" if isTransparent else bgData.value or "white"

        self._figure.patch.set_facecolor(bgColor)
        self._ax.clear()
        self._ax.axis("off")
        self._ax.set_facecolor(bgColor)
        self._ax.imshow(imgArray, interpolation="bilinear")
        self._ax.set_xlim(0, result.width)
        self._ax.set_ylim(result.height, 0)
        self._canvas.draw()

        # 独立 Figure 用于导出(高分辨率)
        self._lastFigure = self._buildExportFigure(result)

    def _buildExportFigure(self, result: WordCloudResult) -> Figure:
        """构建独立 Figure 用于导出(高分辨率)"""
        config = self._gatherConfig()
        engine = WordCloudEngine()
        return engine.renderToFigure(result, config)

    def _export(self, fmt: str):
        """导出 PNG 或 SVG"""
        if self._result is None:
            return
        ext = ".png" if fmt == "png" else ".svg"
        filterStr = "PNG Files (*.png)" if fmt == "png" else "SVG Files (*.svg)"
        path, _ = QFileDialog.getSaveFileName(
            self,
            f"导出词云图({fmt.upper()})",
            f"word_cloud.{ext}",
            filterStr,
        )
        if not path:
            return
        if not path.lower().endswith(ext):
            path += ext

        try:
            # 直接调用 wordcloud 库的 to_file,矢量/位图质量最佳
            engine = WordCloudEngine()
            ok = engine.saveResult(self._result, path, fmt=fmt)
            if not ok:
                # 退化:走 matplotlib 路径
                if self._lastFigure is None:
                    self._lastFigure = self._buildExportFigure(self._result)
                if fmt == "png":
                    self._lastFigure.savefig(
                        path,
                        dpi=300,
                        bbox_inches="tight",
                        facecolor=self._lastFigure.get_facecolor(),
                    )
                else:
                    self._lastFigure.savefig(
                        path,
                        format="svg",
                        bbox_inches="tight",
                        facecolor=self._lastFigure.get_facecolor(),
                    )
            _showInfoBar("success", "导出成功", f"已保存:{path}", self, duration=2500)
        except Exception as e:
            logger.exception(f"[WordCloudWidget] 导出失败: {e}")
            _showInfoBar("error", "导出失败", str(e), self, duration=3500)

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(2000)
        super().closeEvent(event)
