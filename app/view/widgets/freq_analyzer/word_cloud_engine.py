# coding: utf-8
"""
词云渲染引擎(基于 wordcloud 库)

按需求文档 v3 §2.5.1:
    FR-WDC-001 基础词云生成(由 wordcloud 库保证)
    FR-WDC-002 自定义形状(矩形/圆形/椭圆 + 用户上传蒙版)
    FR-WDC-003 配色方案(5 种内置 + matplotlib colormap)
    FR-WDC-004 字体设置(中英文混排)
    FR-WDC-005 词云导出(PNG/SVG)

依赖:
    wordcloud  (Andreas Mueller, MIT License)
    numpy
    Pillow
    matplotlib

设计:
    - 完全采用 wordcloud 库的标准 API,利用其工业级碰撞检测
    - 形状通过 numpy 生成的二值掩码提供(矩形/圆形/椭圆)或用户上传图片
    - 配色通过 matplotlib colormap 字符串或自定义 color_func 提供
    - 中文字体通过 font_manager 自动探测系统已安装字体

References
----------
    Mueller, F. (2014). word_cloud: A little word cloud generator in Python.
    https://github.com/amueller/word_cloud
"""

from __future__ import annotations

import base64
import io
import math
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import Colormap
from matplotlib.figure import Figure

try:
    from wordcloud import WordCloud

    _WORDCLOUD_AVAILABLE = True
except ImportError:
    _WORDCLOUD_AVAILABLE = False

try:
    from PIL import Image

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from loguru import logger


# ---------------------------------------------------------------------------
# 形状 / 配色 / 旋转 / 背景枚举
# ---------------------------------------------------------------------------
class CloudShape(Enum):
    """词云形状"""

    RECTANGLE = "rectangle"
    CIRCLE = "circle"
    ELLIPSE = "ellipse"
    HEART = "heart"


class ColorScheme(Enum):
    """词云配色方案(对应 matplotlib colormap)"""

    WARM = "autumn"  # 暖色:红/橙/黄
    COOL = "winter"  # 冷色:蓝/青
    GRADIENT = "viridis"  # 蓝紫渐变(默认 matplotlib colormap)
    ACADEMIC = "cividis"  # 学术风:深蓝/灰
    RANDOM = "hsv"  # 高饱和随机


class RotationMode(Enum):
    """旋转模式"""

    HORIZONTAL_ONLY = "horizontal"
    MOSTLY_HORIZONTAL = "mixed_30"  # 70% 水平 + 30% ±90°
    RANDOM = "random"


class BackgroundColor(Enum):
    """背景色"""

    WHITE = "white"
    BLACK = "black"
    TRANSPARENT = None  # None 在 wordcloud 中表示透明


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class WordCloudConfig:
    """词云配置"""

    width: int = 800
    height: int = 600
    topN: int = 200
    minWordLength: int = 1
    minFreq: int = 2
    shape: CloudShape = CloudShape.CIRCLE
    colorScheme: ColorScheme = ColorScheme.COOL
    background: BackgroundColor = BackgroundColor.WHITE
    minFontSize: int = 12
    maxFontSize: int = 80
    rotationMode: RotationMode = RotationMode.MOSTLY_HORIZONTAL
    fontPath: Optional[str] = None  # 中文字体路径(自动探测时为 None)
    posFilter: Optional[List[str]] = None  # 词性过滤
    # 用户上传的蒙版图(base64 编码或文件路径,可选)
    customMaskPath: Optional[str] = None
    # 词语间距(像素,>=1)
    collocations: bool = False  # 是否合并 bigram


@dataclass
class WordCloudResult:
    """词云渲染结果"""

    # 内部 wordcloud 对象(由 wordcloud 库返回)
    wordCloud: Optional["WordCloud"] = None
    # 输入的词频字典 {word: freq}
    wordFreqs: Dict[str, int] = field(default_factory=dict)
    # 实际放置的词条数(由 wordcloud 库报告)
    placedCount: int = 0
    # 跳过的词条(因 wordcloud 库限制,默认 0)
    skippedCount: int = 0
    width: int = 0
    height: int = 0
    elapsedSeconds: float = 0.0
    totalTokens: int = 0  # 输入语料 token 总数
    # 错误信息(若 wordcloud 不可用)
    errorMessage: Optional[str] = None


# ---------------------------------------------------------------------------
# 字体探测
# ---------------------------------------------------------------------------
def _availableCjkFonts() -> List[str]:
    """返回系统中可用的中文字体列表(按优先级)

    注意:
        不再回退到 DejaVu Sans——它是 matplotlib 默认 Latin-only 字体,
        缺少 CJK 字形,在词云中显示中文时会变成「豆腐块」(□)。
    """
    fonts: List[str] = []
    candidates = [
        "Microsoft YaHei",
        "微软雅黑",
        "SimHei",
        "黑体",
        "Source Han Sans CN",
        "Noto Sans CJK SC",
        "PingFang SC",
        "Hiragino Sans GB",
        "WenQuanYi Micro Hei",
        "Arial Unicode MS",
        "Noto Sans",  # 兜底:Noto Sans Latin + 部分 CJK 支持
    ]
    try:
        available = {f.name for f in fm.fontManager.ttflist}
        for c in candidates:
            if c in available:
                fonts.append(c)
    except Exception:
        pass
    if not fonts:
        # 没有任何候选字体可用,返回空列表。
        # 调用方应通过 _resolveFontPath 的返回值 None 感知,
        # 由 wordcloud 库自行决定使用其内置默认字体。
        logger.warning(
            "[WordCloud] 系统未安装任何已知的 CJK 字体," "词云中的中文可能无法正常显示"
        )
    return fonts


def _resolveFontPath(fontName: Optional[str]) -> Optional[str]:
    """解析字体文件路径(返回 .ttf/.otf 文件路径)

    Args:
        fontName: 字体名(如 "Microsoft YaHei"),None 表示自动选择第一个
            系统中可用的 CJK 字体。

    Returns:
        字体文件路径(.ttf/.otf),若未找到则返回 None。
        调用方(wordcloud 库)在收到 None 时会使用其内置默认字体。
    """
    target = fontName
    if target is None:
        cjk = _availableCjkFonts()
        if not cjk:
            # 没有可用 CJK 字体;让 wordcloud 库使用其内置默认(英文)字体
            return None
        target = cjk[0]
    try:
        for f in fm.fontManager.ttflist:
            if f.name == target:
                return f.fname
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 形状掩码(原生 numpy 实现,无需上传蒙版图)
# ---------------------------------------------------------------------------
def _buildShapeMask(
    width: int,
    height: int,
    shape: CloudShape,
) -> np.ndarray:
    """生成形状掩码:0 = 可放置区域,255 = masked out(不可绘制)

    返回 dtype=uint8 的 2D 数组 (height, width),wordcloud 库要求此格式。

    重要 - wordcloud 库的 mask 语义(与大多数人的直觉相反):
        wordcloud 1.9.x 库中,_get_bolean_mask 返回 `mask == 255` 的 bool 数组,
        而 IntegralOccupancyMap 将 `255 * boolean_mask` 累加成积分图,
        词条放置查询条件为 `area == 0`(矩形内积分 = 0,即矩形全在 mask==255 处)。

        因此:
            - mask == 255 (白色) → _get_bolean_mask 返回 True → 积分图累加 →
              矩形 area > 0 → 不作为候选位置 → 实际被「mask out」
            - mask == 0 (黑色)   → _get_bolean_mask 返回 False → 积分图保持 →
              矩形 area == 0 → 作为候选位置 → 实际「可绘制」

        简言之:wordcloud 的 mask 语义是 **黑色(0)=可绘制, 白色(255)=mask out**,
        与通常的 PIL mask 习惯相反,务必小心!

    注意:
        词条 to_array() 输出 HxW RGB 数组,行索引 = y,列索引 = x。
        因此 mask 的 axis=0 对应 y,axis=1 对应 x。
        须用 meshgrid 显式构造 2D 坐标,避免 np.ogrid 广播方向相反导致形状错乱。

    Args:
        width, height: 画布尺寸(像素)
        shape: 形状

    Returns:
        ndarray (height, width) dtype=uint8,0=可绘制,255=mask out
    """
    # 默认全部 255(masked out)
    mask = np.ones((height, width), dtype=np.uint8) * 255

    # 显式构造 2D 坐标矩阵,shape=(H, W),与图像数组完全对齐
    yy, xx = np.mgrid[:height, :width]  # yy:(H,W),xx:(H,W)

    if shape == CloudShape.RECTANGLE:
        # 留 3% 内边距的矩形区域设为 0(可绘制)
        mH = int(height * 0.03)
        mW = int(width * 0.03)
        mask[mH : height - mH, mW : width - mW] = 0

    elif shape == CloudShape.CIRCLE:
        cx, cy = width / 2, height / 2  # cx 对应列(x 方向),cy 对应行(y 方向)
        r = min(width, height) / 2 * 0.95
        # |(xx-cx)² + (yy-cy)²|² ≤ r²  为可绘制区域 → 设为 0
        inCircle = (xx - cx) ** 2 + (yy - cy) ** 2 <= r**2
        mask[inCircle] = 0

    elif shape == CloudShape.ELLIPSE:
        cx, cy = width / 2, height / 2
        rx = width / 2 * 0.95
        ry = height / 2 * 0.92
        # 椭圆标准方程:((x-cx)/rx)² + ((y-cy)/ry)² ≤ 1
        inEllipse = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1.0
        mask[inEllipse] = 0

    elif shape == CloudShape.HEART:
        # 经典心形参数曲线(尖角朝下、两瓣朝上):
        #     x(t) = 16 sin³(t)
        #     y(t) = 13 cos(t) - 5 cos(2t) - 2 cos(3t) - cos(4t)
        # 范围约: x ∈ [-16, 16], y ∈ [-17, +12]
        # 多边形填充后即得「♥」形状
        if _PIL_AVAILABLE:
            from PIL import Image, ImageDraw

            # 生成曲线采样点
            t = np.linspace(0, 2 * np.pi, 600)
            hx = 16 * np.sin(t) ** 3
            hy = 13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)
            # 缩放至画布(整体占据约 85% 短边)
            scale = min(width, height) / 38.0 * 0.85
            ptsX = (width / 2 + hx * scale).astype(np.int32)
            ptsY = (height / 2 - hy * scale).astype(np.int32)  # Y 翻转
            # PIL polygon 填充(白色=masked out,黑色=可绘制)
            img = Image.new("L", (width, height), 255)
            ImageDraw.Draw(img).polygon(list(zip(ptsX.tolist(), ptsY.tolist())), fill=0)
            mask = np.array(img)
        else:
            # PIL 不可用时回退:用「经典隐式公式 + 顶部凹槽切除 + 缩放」
            # 这一方案精度低于参数曲线,但保证总有输出
            cx, cy = width / 2, height / 2
            scale = min(width, height) / 2 * 0.85
            u = (xx - cx) / scale
            v = (cy - yy) / scale  # Y 翻转:画布 +y → v 减小
            f = (u * u + v * v - 1) ** 3 - (u * u) * (v * v * v)
            # 经典公式在爱心范围内全 ≤ 0(包括顶部凹槽,数学上非空洞)
            # 凹槽切除:删除 v > 0.4 且 |u| < 0.4 的中央顶部区域
            inHeart = (f <= 0) & ~((v > 0.4) & (np.abs(u) < 0.4))
            mask[inHeart] = 0

    return mask


def _loadMaskFromImage(imagePath: str) -> Optional[np.ndarray]:
    """从图片文件加载蒙版

    注意:wordcloud 库语义为「白色 = mask out」,因此:
        - 图片中亮(灰度 > 128)区域 → 255 → masked out
        - 图片中暗(灰度 ≤ 128)区域 → 0 → 可绘制
    用户上传蒙版图时应确保「想显示词条的形状」为黑色(0)、「不绘制」为白色(255)。
    """
    if not _PIL_AVAILABLE:
        logger.warning("[WordCloud] PIL 不可用,无法加载蒙版图片")
        return None
    try:
        img = Image.open(imagePath).convert("L")  # 灰度
        mask = np.array(img)
        # 二值化:亮区域 = mask out(255),暗区域 = 可绘制(0)
        mask = (mask > 128).astype(np.uint8) * 255
        return mask
    except Exception as e:
        logger.error(f"[WordCloud] 加载蒙版失败: {e}")
        return None


# ---------------------------------------------------------------------------
# 颜色函数(支持 ColorScheme → matplotlib colormap)
# ---------------------------------------------------------------------------
def _buildColorFunc(scheme: ColorScheme) -> Optional[Callable]:
    """根据配色方案构造 wordcloud 库的 color_func

    颜色稳定性说明:
        使用 zlib.crc32 作为哈希函数(而非 Python 内置 hash())——
        后者在每次进程启动时随机种子(PYTHONHASHSEED 默认为 random),
        会导致同一词在不同进程/会话中得到不同颜色,破坏**学术可复现性**。
        zlib.crc32 是确定性的,跨进程、跨平台结果一致。

    Returns:
        callable(word, font_size, position, orientation, font_path, random_state)
            → str (HEX 颜色,如 "#1890ff")
        或 None(使用默认 colormap)
    """
    cmapName = scheme.value
    try:
        cmap = plt.get_cmap(cmapName)
    except Exception:
        cmap = plt.get_cmap("viridis")

    def colorFunc(
        word: str,
        font_size: int,
        position,
        orientation,
        font_path: str,
        random_state,
    ):
        # 使用确定性哈希(crc32)替代 Python 内置 hash(),保证跨进程同色
        import zlib

        h = (zlib.crc32(word.encode("utf-8")) & 0xFFFFFFFF) / 0xFFFFFFFF
        rgba = cmap(h)
        return "#{:02x}{:02x}{:02x}".format(
            int(rgba[0] * 255), int(rgba[1] * 255), int(rgba[2] * 255)
        )

    return colorFunc


def _buildPreferHorizontal(mode: RotationMode) -> float:
    """根据旋转模式返回 wordcloud 的 prefer_horizontal 参数

    wordcloud 参数语义:prefer_horizontal ∈ [0, 1]
        1.0 = 全部水平
        0.0 = 全部竖直/随机
        0.7 ≈ 70% 水平 + 30% 旋转
    """
    if mode == RotationMode.HORIZONTAL_ONLY:
        return 1.0
    elif mode == RotationMode.MOSTLY_HORIZONTAL:
        return 0.7
    else:
        return 0.3


# ---------------------------------------------------------------------------
# 主引擎
# ---------------------------------------------------------------------------
class WordCloudEngine:
    """词云渲染引擎(基于 wordcloud 库)"""

    def __init__(self):
        if not _WORDCLOUD_AVAILABLE:
            logger.warning(
                "[WordCloudEngine] wordcloud 库未安装," "请运行:pip install wordcloud"
            )

    def render(
        self,
        wordFreqs: List[Tuple[str, int]],
        config: WordCloudConfig,
    ) -> WordCloudResult:
        """渲染词云

        Args:
            wordFreqs: [(word, freq), ...] 已按频次降序(顺序不强制)
            config: 词云配置

        Returns:
            WordCloudResult
        """
        import time as _time

        t0 = _time.time()

        if not _WORDCLOUD_AVAILABLE:
            return WordCloudResult(
                width=config.width,
                height=config.height,
                errorMessage="wordcloud 库未安装,请运行:pip install wordcloud",
                elapsedSeconds=_time.time() - t0,
            )

        # 1) 过滤 + 排序
        filtered = [
            (w, f)
            for w, f in wordFreqs
            if len(w.strip()) >= config.minWordLength and f >= config.minFreq
        ]
        # 转 dict(支持 wordcloud 库)
        freqDict: Dict[str, int] = dict(filtered[: config.topN])

        if not freqDict:
            logger.warning("[WordCloud] 过滤后无词条")
            return WordCloudResult(
                width=config.width,
                height=config.height,
                errorMessage="过滤后无词条,请降低过滤条件",
                elapsedSeconds=_time.time() - t0,
            )

        # 2) 字体解析
        fontPath = _resolveFontPath(config.fontPath)
        if fontPath is None:
            logger.warning("[WordCloud] 未找到可用字体,wordcloud 可能使用默认字体")

        # 3) 形状掩码
        mask: Optional[np.ndarray] = None
        if config.customMaskPath and os.path.exists(config.customMaskPath):
            mask = _loadMaskFromImage(config.customMaskPath)
        if mask is None:
            mask = _buildShapeMask(config.width, config.height, config.shape)

        # 4) 配色函数
        colorFunc = _buildColorFunc(config.colorScheme)

        # 5) 旋转偏好
        preferHorizontal = _buildPreferHorizontal(config.rotationMode)

        # 6) 构造 WordCloud 对象
        try:
            wc = WordCloud(
                width=config.width,
                height=config.height,
                font_path=fontPath,
                mask=mask,
                background_color=(
                    config.background.value
                    if config.background != BackgroundColor.TRANSPARENT
                    else None
                ),
                max_words=config.topN,
                min_font_size=config.minFontSize,
                max_font_size=config.maxFontSize,
                prefer_horizontal=preferHorizontal,
                color_func=colorFunc,
                relative_scaling=0.6,  # 频率→字号的缩放因子,默认 0.5
                collocations=config.collocations,
                random_state=42,  # 保证可复现
                contour_width=0,  # 不绘制描边
                mode="RGB",
            )
            # 7) 生成
            wc.generate_from_frequencies(freqDict)

            placedCount = len(wc.words_)
            skippedCount = len(freqDict) - placedCount

            return WordCloudResult(
                wordCloud=wc,
                wordFreqs=freqDict,
                placedCount=placedCount,
                skippedCount=max(0, skippedCount),
                width=config.width,
                height=config.height,
                elapsedSeconds=_time.time() - t0,
                totalTokens=sum(freqDict.values()),
            )

        except Exception as e:
            import traceback

            logger.exception(f"[WordCloudEngine] 渲染失败: {e}")
            return WordCloudResult(
                width=config.width,
                height=config.height,
                errorMessage=f"{e}\n{traceback.format_exc()}",
                elapsedSeconds=_time.time() - t0,
            )

    def renderToFigure(
        self, result: WordCloudResult, config: WordCloudConfig
    ) -> Figure:
        """将 WordCloudResult 渲染为 matplotlib Figure

        使用 wordcloud 库自带的 to_image() 方法,确保颜色、布局一致。

        Args:
            result: render() 返回结果
            config: 词云配置(用于背景色等)

        Returns:
            matplotlib Figure
        """
        dpi = 100
        fig = Figure(
            figsize=(config.width / dpi, config.height / dpi),
            dpi=dpi,
            facecolor=(
                config.background.value
                if config.background != BackgroundColor.TRANSPARENT
                else "#ffffff"
            ),
        )
        ax = fig.add_subplot(111)
        ax.axis("off")

        if result.wordCloud is not None:
            # wordcloud 库自带的 to_array() 返回 numpy 数组
            imgArray = result.wordCloud.to_array()
            ax.imshow(imgArray, interpolation="bilinear")
            ax.set_xlim(0, config.width)
            ax.set_ylim(config.height, 0)  # 翻转 Y 方向,与图像一致

        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        return fig

    def saveResult(
        self,
        result: WordCloudResult,
        path: str,
        fmt: str = "png",
    ) -> bool:
        """直接保存 wordcloud 库的输出(高质量)

        Args:
            result: render() 结果
            path: 保存路径
            fmt: png | svg

        Returns:
            bool 是否成功

        Notes:
            wordcloud 库本身不支持 SVG 导出,SVG 通过 to_array() + matplotlib
            Figure.savefig(format='svg') 间接生成。
        """
        if result.wordCloud is None:
            return False
        try:
            if fmt == "svg":
                # wordcloud 不支持 SVG,改走 matplotlib 路径
                fig = self.renderToFigure(
                    result,
                    WordCloudConfig(
                        width=result.width,
                        height=result.height,
                        background=BackgroundColor.WHITE,
                    ),
                )
                fig.savefig(path, format="svg", bbox_inches="tight")
                return True
            else:
                # PNG 高分辨率
                result.wordCloud.to_file(path)
                return True
        except Exception as e:
            logger.error(f"[WordCloudEngine] 保存失败: {e}")
            return False
