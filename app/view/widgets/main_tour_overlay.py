# coding: utf-8
"""
主窗口首次进入引导遮罩（Tour Overlay）

设计目标:
    用户首次进入主窗口（非「启动期引导」）时，在主窗口之上展示一个
    半透明遮罩，将除了「关键控件」以外的位置全部用黑幕盖住，并在
    关键控件旁边展示一个带文字说明的提示卡片，引导用户认识主窗口
    的关键区域。

核心特性:
    - 多步骤:覆盖导航栏 / 顶栏 / 多个 subInterface 模块,引导用户逐步认识
      整个主窗口的功能分布。
    - 主动切页:每一步会自动调用 mainWindow.switchTo(...) 切到对应模块,
      让用户在「实际看到该模块」的同时看到说明。
    - 智能摆位:按 prefer 方向(right / bottom / left / top)放置卡片,
      空间不够自动尝试下一方向,最终居中。
    - 跟随 resize:主窗口大小变化时卡片重定位。
    - 控件点击穿透透传:除卡片自身外,其余被遮罩覆盖的位置鼠标事件转发
      给对应控件,用户能在引导期内正常与高亮控件交互(例如切换导航项)。
    - 跳过持久化:用户点完成 / 跳过 / ✕ 均会写 cfg.MainTourShown=True。

多步步骤(2026-07-28 扩展版):
    1. 欢迎页 + 整体导航栏
    2. 顶栏项目切换器
    3. HSK 下载模块
    4. 全球中介语料下载模块
    5. 偏误统计模块
    6. 语料分析模块
    7. AI 聊天模块
    8. 项目管理模块
    9. 任务管理 + 设置入口
    10. 完成页
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, CaptionLabel, PushButton, TransparentPushButton

from app.core.utils import cfg, logger, qconfig
from app.core.utils.setting import INTERNAL_TEST_MODE
from app.view.widgets.prismatica_theme import shellPalette


# 主题色：与主窗口 setThemeColor("#00b09c") 对齐
_THEME_COLOR = QColor("#00b09c")
_MASK_COLOR = QColor(0, 0, 0, 178)  # ~70% 透明黑
_HIGHLIGHT_BORDER_COLOR = QColor("#00b09c")
_HIGHLIGHT_BORDER_WIDTH = 2
_HIGHLIGHT_PADDING = 8
_HIGHLIGHT_RADIUS = 10

_CARD_BG_COLOR = QColor(255, 255, 255)
_CARD_TEXT_COLOR = QColor(40, 40, 40)
_CARD_SHADOW_COLOR = QColor(0, 0, 0, 90)
_CARD_RADIUS = 12
_CARD_PADDING_X = 20
_CARD_PADDING_Y = 16
_CARD_MAX_WIDTH = 340

# 提示卡与高亮矩形的安全间距
_CARD_GAP = 18


@dataclass
class TourStep:
    """单步引导配置。

    Attributes:
        title: 卡片标题(粗体)
        body: 卡片正文说明
        targetResolver: 可调用对象,签名 (mainWindow) -> QWidget 或 None,
                        用于动态解析高亮目标控件。
        switchTo: 可选,在展示该步骤前要切换到的 subInterface(主窗口对象名)。
        prefer: 卡片相对高亮矩形的首选方向 ("right"/"bottom"/"left"/"top")。
        interactive: True 表示允许用户点击/操作高亮控件(默认 False,
                     防止误触主窗口其它功能)。设为 True 时,遮罩会把鼠标
                     事件转发到目标控件,而不是吞掉。
    """

    title: str
    body: str
    targetResolver: Callable[[QWidget], Optional[QWidget]]
    switchTo: Optional[str] = None
    prefer: str = "right"
    interactive: bool = False


class _MouseForwarder(QObject):
    """把指定 QWidget 之外区域的鼠标事件转发到另一个 QWidget。

    用于:TourOverlay 覆盖整个主窗口但希望「非高亮区」依然可点击底层控件。
    - 默认所有事件 swallow 掉,避免用户在遮罩期误操作其它模块
    - 对 `targetWidget` 区域:把鼠标事件 translate 到目标坐标后 sendEvent
    - 对 stepCard:不拦截(卡片自己有事件处理)
    """

    def __init__(
        self,
        overlay: QWidget,
        targetGetter: Callable[[], Optional[QWidget]],
        excludeWidgets: List[QWidget],
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._overlay = overlay
        self._targetGetter = targetGetter
        self._excludeWidgets = excludeWidgets

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # 只过滤 overlay 上的鼠标按下/释放/移动/双击
        if obj is not self._overlay:
            return False
        if event.type() not in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.MouseMove,
            QEvent.Type.Wheel,
        ):
            return False

        # 排除卡片:卡片自己处理事件
        for ex in self._excludeWidgets:
            if ex is None:
                continue
            try:
                if ex.isVisible() and ex.geometry().contains(event.pos()):
                    return False
            except Exception:
                continue

        target = self._targetGetter()
        if target is None:
            # 没目标:吞掉所有事件,什么都不做
            return True

        # 把事件转发到目标:先 translate 到目标坐标
        translated = QMouseEvent(
            event.type(),
            target.mapFrom(self._overlay, event.pos()),
            event.globalPosition(),
            event.button(),
            event.buttons(),
            event.modifiers(),
        )
        QApplication.sendEvent(target, translated)
        return True


class MainTourOverlay(QWidget):
    """主窗口引导遮罩。

    使用方法:
        overlay = MainTourOverlay(mainWindow)
        overlay.start()
    """

    def __init__(self, mainWindow: QWidget):
        super().__init__(mainWindow)
        self._mainWindow = mainWindow
        self._steps: List[TourStep] = []
        self._currentIndex: int = 0
        self._currentTarget: Optional[QWidget] = None
        self._currentTargetRect: Optional[QRect] = None

        # 覆盖层属性
        self.setWindowFlags(Qt.WindowType.Widget | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        # ---- 顶部提示卡 ----
        self._card = QWidget(self)
        self._card.setObjectName("tourCard")
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 8)
        shadow.setColor(_CARD_SHADOW_COLOR)
        self._card.setGraphicsEffect(shadow)

        # 卡片内部布局
        cardLayout = QVBoxLayout(self._card)
        cardLayout.setContentsMargins(
            _CARD_PADDING_X, _CARD_PADDING_Y, _CARD_PADDING_X, _CARD_PADDING_Y
        )
        cardLayout.setSpacing(8)

        # 顶部一行:图标 + 标题
        headerRow = QHBoxLayout()
        headerRow.setContentsMargins(0, 0, 0, 0)
        headerRow.setSpacing(8)

        # 步骤序号徽标
        self._stepBadge = BodyLabel("")
        badgeFont = QFont()
        badgeFont.setPointSize(11)
        badgeFont.setBold(True)
        self._stepBadge.setFont(badgeFont)
        self._stepBadge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stepBadge.setFixedSize(24, 24)
        self._stepBadge.setStyleSheet(
            f"background-color: rgb{_THEME_COLOR.red(), _THEME_COLOR.green(), _THEME_COLOR.blue()};"
            "color: white; border-radius: 12px;"
        )
        headerRow.addWidget(self._stepBadge, 0, Qt.AlignmentFlag.AlignVCenter)

        self._titleLabel = BodyLabel("")
        titleFont = QFont()
        titleFont.setPointSize(14)
        titleFont.setBold(True)
        self._titleLabel.setFont(titleFont)
        self._titleLabel.setWordWrap(True)
        headerRow.addWidget(self._titleLabel, 1, Qt.AlignmentFlag.AlignVCenter)

        cardLayout.addLayout(headerRow)

        # 正文
        self._bodyLabel = CaptionLabel("")
        self._bodyLabel.setWordWrap(True)
        self._bodyLabel.setMinimumWidth(0)
        cardLayout.addWidget(self._bodyLabel)

        # 分隔细线
        self._separator = QWidget(self._card)
        self._separator.setFixedHeight(1)
        cardLayout.addSpacing(4)
        cardLayout.addWidget(self._separator)
        cardLayout.addSpacing(4)

        # 操作行(上一步 / 下一步)
        actionRow = QHBoxLayout()
        actionRow.setContentsMargins(0, 0, 0, 0)
        actionRow.setSpacing(8)

        self._skipButton = TransparentPushButton("跳过引导")
        self._skipButton.clicked.connect(self._onSkipClicked)
        actionRow.addWidget(self._skipButton)

        actionRow.addStretch(1)

        self._prevButton = PushButton("← 上一步")
        self._prevButton.clicked.connect(self._onPrevClicked)
        actionRow.addWidget(self._prevButton)

        self._nextButton = PushButton("下一步 →")
        self._nextButton.clicked.connect(self._onNextClicked)
        actionRow.addWidget(self._nextButton)

        cardLayout.addLayout(actionRow)

        # 步骤指示 (1 / N)
        self._stepIndicator = CaptionLabel("")
        self._stepIndicator.setAlignment(Qt.AlignmentFlag.AlignRight)
        cardLayout.addWidget(self._stepIndicator)

        # 关闭按钮 (✕) — 浮在卡片右上角
        self._closeButton = TransparentPushButton("✕")
        self._closeButton.setFixedSize(28, 28)
        self._closeButton.clicked.connect(self._onSkipClicked)
        self._closeButton.setParent(self._card)
        self._closeButton.raise_()

        # ---- 鼠标事件转发器(透传到高亮控件)----
        self._mouseForwarder = _MouseForwarder(
            overlay=self,
            targetGetter=lambda: self._currentTarget,
            excludeWidgets=[self._card, self._closeButton],
        )
        self.installEventFilter(self._mouseForwarder)

        # 构建步骤定义
        self._buildSteps()
        self._applyTheme()
        qconfig.themeChangedFinished.connect(self._applyTheme)

    def _applyTheme(self, *_args) -> None:
        palette = shellPalette()
        self._card.setStyleSheet(
            f"#tourCard {{ background-color: {palette.surface.name()}; "
            f"border: 1px solid {palette.border.name()}; "
            f"border-radius: {_CARD_RADIUS}px; }}"
        )
        self._titleLabel.setStyleSheet(f"color: {palette.text.name()};")
        self._bodyLabel.setStyleSheet(f"color: {palette.mutedText.name()};")
        self._separator.setStyleSheet(
            f"background-color: {palette.border.name()};"
        )
        self._stepIndicator.setStyleSheet(
            f"color: {palette.mutedText.name()};"
        )
        self._closeButton.setStyleSheet(
            f"color: {palette.mutedText.name()}; font-size: 14px;"
        )
        self._nextButton.setStyleSheet(
            f"QPushButton {{ background-color: {_THEME_COLOR.name()}; color: white; "
            "border: none; border-radius: 4px; padding: 6px 14px; }}"
            "QPushButton:hover { background-color: #008F7F; }"
            f"QPushButton:disabled {{ background-color: {palette.surfaceAlt.name()}; "
            f"color: {palette.mutedText.name()}; }}"
        )


    # ------------------------------------------------------------------
    # 步骤定义
    # ------------------------------------------------------------------
    def _buildSteps(self) -> None:
        """定义多页引导步骤,覆盖主窗口全部关键功能区。"""

        def _nav():
            return getattr(self._mainWindow, "navigationInterface", None)

        def _stacked():
            return getattr(self._mainWindow, "stackedWidget", None)

        def _switcher():
            tb = getattr(self._mainWindow, "titleBar", None)
            return getattr(tb, "projectSwitcher", None) if tb else None

        def _navItem(routeKey: str):
            """通过 routeKey 拿导航栏按钮 widget。"""
            try:
                nav = _nav()
                if nav is None:
                    return None
                w = nav.widget(routeKey)
                return w if w is not None else None
            except Exception:
                return None

        def _currentPage():
            """当前 stackedWidget 页面(用于 step 内切到目标页后再次定位)。"""
            try:
                sw = _stacked()
                if sw is not None:
                    return sw.currentWidget()
            except Exception:
                pass
            return None

        self._steps = [
            # ---- 1. 欢迎 ----
            TourStep(
                title="欢迎使用 Prismatica 棱溯",
                body=(
                    "接下来会用 8 个步骤带你认识主界面。"
                    "每一步会高亮一个区域,并在旁边显示说明文字。"
                    "你随时可以点击「跳过引导」直接结束。"
                ),
                targetResolver=_nav,
                prefer="right",
            ),
            # ---- 2. 顶栏项目切换器 ----
            TourStep(
                title="顶栏:项目切换器",
                body=(
                    "在这里可以快速切换已创建的研究项目。"
                    "下拉菜单包含「项目管理 / 新建项目」入口。"
                ),
                targetResolver=_switcher,
                prefer="bottom",
            ),
            # ---- 3. HSK 下载模块 ----
            TourStep(
                title="① HSK 语料下载",
                body=(
                    "下载 HSK 全级别作文 / 文本语料,支持批量与索引。"
                    "首次使用需在「设置」中配置 HSK 账号或 Token。"
                ),
                targetResolver=lambda _mw=None: _navItem("HskInterface"),
                switchTo="HskInterface",
                prefer="right",
                interactive=True,
            ),
            # ---- 4. 全球中介语料下载 ----
            TourStep(
                title="② 全球中介语料下载",
                body=(
                    "对接全球中介语料库,支持检索与批量下载," "适合做跨语言偏误研究。"
                ),
                targetResolver=lambda _mw=None: _navItem("GlobalInterface"),
                switchTo="GlobalInterface",
                prefer="right",
                interactive=True,
            ),
            # ---- 5. 偏误统计 ----
            TourStep(
                title="③ 偏误统计",
                body=(
                    "基于中介语料库的偏误频次 / 类型 / 分布统计," "辅助教学与教材编写。"
                ),
                targetResolver=lambda _mw=None: _navItem("BiasInterface"),
                switchTo="BiasInterface",
                prefer="right",
                interactive=True,
            ),
            # ---- 6. 语料分析(词频/搭配/索引等) ----
            TourStep(
                title="④ 语料分析",
                body=(
                    "内置词频 / 搭配 / 索引 / 共现网络 / 依存 / 词云"
                    "等多维度分析能力,大多数操作可在右侧面板直接完成。"
                ),
                targetResolver=lambda _mw=None: _navItem("freqAnalyzerInterface"),
                switchTo="freqAnalyzerInterface",
                prefer="right",
                interactive=True,
            ),
            # ---- 7. AI 聊天 ----
            TourStep(
                title="⑤ AI 聊天助手",
                body=(
                    "内置 AI 助手,可用于解释语料、撰写研究笔记等。"
                    "需要先在「设置 → AI 聊天设置」配置 API Key。"
                ),
                targetResolver=lambda _mw=None: _navItem("chatInterface"),
                switchTo="chatInterface",
                prefer="right",
                interactive=True,
            ),
            # ---- 8. 项目管理 ----
            TourStep(
                title="⑥ 项目管理",
                body=(
                    "把每次研究封装为「项目」,独立管理资源池与研究成果。"
                    "在仪表盘可一键跳转到对应分析模块。"
                ),
                targetResolver=lambda _mw=None: _navItem("projectInterface"),
                switchTo="projectInterface",
                prefer="right",
                interactive=True,
            ),
            # ---- 9. 底部入口:任务 / 设置 ----
            TourStep(
                title="任务管理 + 设置",
                body=(
                    "底部导航的「任务管理」用于查看下载进度与历史记录;"
                    "「设置」用于配置下载路径、Token、分析规则与主题等。"
                ),
                targetResolver=lambda _mw=None: _navItem("TaskInterface")
                or _navItem("SettingInterface"),
                switchTo="SettingInterface",
                prefer="right",
                interactive=True,
            ),
            # ---- 10. 完成 ----
            TourStep(
                title="开始你的研究之旅",
                body=(
                    "以上就是主窗口的全部关键区域。"
                    "可以随时在「设置 → 关于软件」中重新查看引导。"
                ),
                targetResolver=_currentPage,
                prefer="bottom",
            ),
        ]
        if INTERNAL_TEST_MODE:
            self._steps = [
                step for step in self._steps if step.switchTo != "chatInterface"
            ]
            self._steps[0].body = (
                f"接下来会用 {len(self._steps)} 个步骤带你认识主界面。"
                "每一步会高亮一个区域,并在旁边显示说明文字。"
                "你随时可以点击「跳过引导」直接结束。"
            )

    # ------------------------------------------------------------------
    # 启动 / 步骤推进
    # ------------------------------------------------------------------
    def start(self) -> None:
        """开始引导(非阻塞)。"""
        if not self._steps:
            logger.warning("[MainTour] 无可用步骤,跳过引导")
            return
        self._currentIndex = 0
        self._showStep()
        self.show()
        self.raise_()

    def _showStep(self) -> None:
        """渲染当前步骤。"""
        if not (0 <= self._currentIndex < len(self._steps)):
            return

        step = self._steps[self._currentIndex]

        # 1) 主动切到目标 subInterface(若指定)
        if step.switchTo:
            self._switchToInterface(step.switchTo)

        # 2) 解析目标控件
        target = step.targetResolver()
        if target is None:
            logger.warning(
                f"[MainTour] 步骤 {self._currentIndex + 1} 目标控件未找到,降级"
            )
            target = getattr(self._mainWindow, "navigationInterface", None)

        self._currentTarget = target

        # 3) 计算高亮矩形(映射到 overlay 坐标系)
        self._currentTargetRect = self._computeTargetRect(target)

        # 4) 更新文案
        self._titleLabel.setText(step.title)
        self._bodyLabel.setText(step.body)
        self._stepBadge.setText(str(self._currentIndex + 1))
        self._stepIndicator.setText(
            f"第 {self._currentIndex + 1} / {len(self._steps)} 步"
        )

        # 5) 控制按钮状态
        self._prevButton.setEnabled(self._currentIndex > 0)
        isLast = self._currentIndex >= len(self._steps) - 1
        self._nextButton.setText("完成 ✓" if isLast else "下一步 →")

        # 6) 让 overlay 覆盖整个主窗口客户区
        self._fitToMainWindow()

        # 7) 摆放卡片
        self._positionCard(step.prefer)

        # 8) 卡片置顶 + 关闭按钮定位
        self._card.raise_()
        self._closeButton.raise_()

        # 9) 强制重绘
        self.update()

    def _switchToInterface(self, objectName: str) -> None:
        """切换主窗口 stackedWidget 到指定 objectName 的页面。"""
        try:
            sw = getattr(self._mainWindow, "stackedWidget", None)
            if sw is None:
                return
            for i in range(sw.count()):
                w = sw.widget(i)
                if w is not None and w.objectName() == objectName:
                    sw.setCurrentWidget(w)
                    logger.debug(f"[MainTour] 已切到 {objectName}")
                    # 等待一帧让布局生效
                    QApplication.processEvents()
                    return
            logger.debug(f"[MainTour] 未找到页面 {objectName}")
        except Exception as e:
            logger.warning(f"[MainTour] 切换页面失败: {e}")

    def _computeTargetRect(self, target: Optional[QWidget]) -> Optional[QRect]:
        """把目标控件映射到 overlay 坐标系下的矩形(overlay = mainWindow 客户区)。"""
        if target is None:
            return None
        try:
            # overlay 与 mainWindow 同坐标系(overlay 几何=mainWindow.rect)
            rectInTarget = QRect(0, 0, target.width(), target.height())
            topLeft = target.mapTo(self._mainWindow, rectInTarget.topLeft())
            return QRect(topLeft, rectInTarget.size())
        except Exception:
            return None

    def _fitToMainWindow(self) -> None:
        """让 overlay 覆盖主窗口的整个客户区。"""
        clientRect = self._mainWindow.rect()
        if self.geometry() != clientRect:
            self.setGeometry(clientRect)

    def _positionCard(self, prefer: str) -> None:
        """根据高亮矩形 + 首选方向智能摆放卡片。"""
        self._card.adjustSize()
        self._card.setFixedWidth(min(self._card.width(), _CARD_MAX_WIDTH))
        self._card.adjustSize()
        cardW = self._card.width()
        cardH = self._card.height()
        cardSize = QSize(cardW, cardH)

        highlight = self._currentTargetRect
        parentSize = self.size()

        if highlight is None:
            self._centerCard()
            return

        # 去重保序的尝试方向序列
        seen = set()
        ordered: List[str] = []
        for c in [prefer, "right", "bottom", "left", "top"]:
            if c not in seen:
                seen.add(c)
                ordered.append(c)

        for direction in ordered:
            pt = self._computeCardPos(highlight, cardSize, parentSize, direction)
            if pt is not None:
                self._card.move(pt)
                self._closeButton.move(cardW - 32, 4)
                return

        self._centerCard()

    def _computeCardPos(
        self,
        highlight: QRect,
        cardSize: QSize,
        parentSize: QSize,
        direction: str,
    ) -> Optional[QPoint]:
        gap = _CARD_GAP
        x, y = 0, 0

        if direction == "right":
            x = highlight.right() + gap
            y = highlight.center().y() - cardSize.height() // 2
        elif direction == "left":
            x = highlight.left() - cardSize.width() - gap
            y = highlight.center().y() - cardSize.height() // 2
        elif direction == "bottom":
            x = highlight.center().x() - cardSize.width() // 2
            y = highlight.bottom() + gap
        elif direction == "top":
            x = highlight.center().x() - cardSize.width() // 2
            y = highlight.top() - cardSize.height() - gap
        else:
            return None

        x = max(8, min(x, parentSize.width() - cardSize.width() - 8))
        y = max(8, min(y, parentSize.height() - cardSize.height() - 8))

        if x + cardSize.width() <= 16 or y + cardSize.height() <= 16:
            return None
        if x >= parentSize.width() - 16 or y >= parentSize.height() - 16:
            return None
        return QPoint(x, y)

    def _centerCard(self) -> None:
        self._card.adjustSize()
        self._card.setFixedWidth(min(self._card.width(), _CARD_MAX_WIDTH))
        self._card.adjustSize()
        x = (self.width() - self._card.width()) // 2
        y = (self.height() - self._card.height()) // 2
        self._card.move(x, y)
        self._closeButton.move(self._card.width() - 32, 4)

    # ------------------------------------------------------------------
    # 按钮回调
    # ------------------------------------------------------------------
    def _onPrevClicked(self) -> None:
        if self._currentIndex <= 0:
            return
        self._currentIndex -= 1
        self._showStep()

    def _onNextClicked(self) -> None:
        if self._currentIndex >= len(self._steps) - 1:
            self._completeTour(writeCfg=True)
            return
        self._currentIndex += 1
        self._showStep()

    def _onSkipClicked(self) -> None:
        self._completeTour(writeCfg=True)

    def _completeTour(self, writeCfg: bool) -> None:
        logger.info(
            f"[MainTour] 引导结束 (writeCfg={writeCfg}, "
            f"step={self._currentIndex + 1}/{len(self._steps)})"
        )
        if writeCfg:
            try:
                qconfig.set(cfg.MainTourShown, True)
            except Exception as e:
                logger.warning(f"[MainTour] 写入 MainTourShown 失败: {e}")
        self.hide()
        self.deleteLater()

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1) 全幅遮罩
        path = QPainterPath()
        path.addRect(QRectF(0, 0, self.width(), self.height()))

        # 2) 减去高亮矩形(挖洞)
        if self._currentTargetRect is not None:
            padded = self._currentTargetRect.adjusted(
                -_HIGHLIGHT_PADDING,
                -_HIGHLIGHT_PADDING,
                _HIGHLIGHT_PADDING,
                _HIGHLIGHT_PADDING,
            )
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(padded), _HIGHLIGHT_RADIUS, _HIGHLIGHT_RADIUS)
            path = path.subtracted(hole)

        painter.fillPath(path, _MASK_COLOR)

        # 3) 绘制高亮边框
        if self._currentTargetRect is not None:
            padded = self._currentTargetRect.adjusted(
                -_HIGHLIGHT_PADDING,
                -_HIGHLIGHT_PADDING,
                _HIGHLIGHT_PADDING,
                _HIGHLIGHT_PADDING,
            )
            pen = QPen(_HIGHLIGHT_BORDER_COLOR)
            pen.setWidth(_HIGHLIGHT_BORDER_WIDTH)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(padded), _HIGHLIGHT_RADIUS, _HIGHLIGHT_RADIUS
            )

        painter.end()

    # ------------------------------------------------------------------
    # 跟随主窗口变化
    # ------------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._steps and 0 <= self._currentIndex < len(self._steps):
            self._positionCard(self._steps[self._currentIndex].prefer)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fitToMainWindow()
        if self._steps and 0 <= self._currentIndex < len(self._steps):
            self._positionCard(self._steps[self._currentIndex].prefer)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        # 主窗口移动时,overlay 跟随
        self._fitToMainWindow()
