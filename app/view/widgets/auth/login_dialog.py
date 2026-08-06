# coding: utf-8
"""启动门 - 内测激活码兑换对话框

修复(2026-08-05)「黑边 + 显示不全」:
    原实现继承 qfluentwidgets.MessageBoxBase → MaskDialogBase → QDialog,
    存在三个根本问题:
        1. setGeometry(0, 0, parent.width(), parent.height()) 依赖 parent
           → 启动时 splash 已 hide / 未稳定时 dialog 几何错乱
        2. widget 高度由 buttonGroup(81) + viewLayout 内容撑开,setMinimumHeight(320)
           不足以容纳全部内容 → 显示不全 / 底部按钮溢出
        3. MaskDialogBase 的 windowMask 透明遮罩 + WA_TranslucentBackground
           配合不佳,在某些主题下产生 OS 边框 → 「黑边」

    仿照 SplashWindow 重新设计:
        - 直接继承 QWidget(独立窗口),不再继承 MaskDialogBase
        - WindowFlags: FramelessWindowHint | WindowStaysOnTopHint | Tool | Dialog
        - WA_TranslucentBackground + 内嵌 _card 子 widget
        - QGraphicsDropShadowEffect 直接挂在 _card 上
        - 固定尺寸 540×560,不依赖 parent
        - 模态用 QEventLoop.exec() 实现(参考 GuideWindow.exec())

历史修复(保留):
    - 三个 Tab 独立 _CodeForm,不再共用 LineEdit
    - reentryMode 支持账户页重新激活
    - 重复激活时显示「立即注销并重试」按钮

2026-08-06 精简:
    - 内测版只允许「内测激活码」一种凭证,删除「邀请码」「体验码」两个 Tab
    - 移除 Pivot,只显示一个 _CodeForm
    - 窗口高度相应下调
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QEventLoop
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QGraphicsDropShadowEffect,
)
from qfluentwidgets import (
    SubtitleLabel,
    BodyLabel,
    CaptionLabel,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
)

from app.core.services.auth_service import getAuthService


# 主题色:与 SplashWindow 保持一致
THEME_COLOR = "#00b09c"
SHADOW_COLOR = QColor(0, 0, 0, 60)


class _CodeForm(QWidget):
    """单个 Tab 的表单:凭证输入 + 可选昵称 + 状态提示"""

    def __init__(
        self,
        placeholder: str,
        showName: bool,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(6)

        v.addWidget(CaptionLabel("凭证:", self))
        self.codeEdit = LineEdit(self)
        self.codeEdit.setPlaceholderText(placeholder)
        self.codeEdit.setClearButtonEnabled(True)
        v.addWidget(self.codeEdit)

        self.nameEdit: Optional[LineEdit] = None
        if showName:
            v.addWidget(CaptionLabel("昵称(可选):", self))
            self.nameEdit = LineEdit(self)
            self.nameEdit.setPlaceholderText("内测用户")
            v.addWidget(self.nameEdit)

        # 错误/状态提示
        self.statusLabel = CaptionLabel("", self)
        self.statusLabel.setStyleSheet("color: #888;")
        v.addWidget(self.statusLabel)

        v.addStretch(1)

    def clear(self) -> None:
        self.codeEdit.clear()
        if self.nameEdit is not None:
            self.nameEdit.clear()
        self.statusLabel.setText("")


class LoginDialog(QWidget):
    """启动门:输入内测激活码(2026-08-06 精简后只剩激活码 Tab)

    - 独立 QWidget,无遮罩
    - 固定 540×420 尺寸(去掉了 Pivot + 两个 Tab,整体高度下调)
    - exec() 用 QEventLoop 阻塞
    - reject()/accept() 关闭 event loop
    """

    # 固定尺寸(像素)
    WINDOW_WIDTH = 540
    WINDOW_HEIGHT = 420

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        reentryMode: bool = False,
    ):
        super().__init__(parent)
        self._success = False
        self._reentryMode = reentryMode
        self._loop: Optional[QEventLoop] = None
        self._result: int = 0  # 0=reject, 1=accept

        # ----- 窗口标志与背景(仿 Splash) -----
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        # 固定尺寸,不让父类 / layout 拉伸
        self.setFixedSize(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)

        # ----- 外层布局(用于容纳 _card 与其外边距,留出阴影空间) -----
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(0)

        # ----- 内容卡片(仿 SplashWindow._card) -----
        self._card = QFrame(self)
        self._card.setObjectName("loginCard")
        self._card.setStyleSheet(
            f"#loginCard {{"
            f"background-color: white;"
            f"border-radius: 12px;"
            f"border: 1px solid #e6e6e6;"
            f"}}"
        )
        outer.addWidget(self._card)

        # 阴影挂在 _card 上(关键,避免 OS 黑边)
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 8)
        shadow.setColor(SHADOW_COLOR)
        self._card.setGraphicsEffect(shadow)

        # ----- card 内部布局 -----
        cardLayout = QVBoxLayout(self._card)
        cardLayout.setContentsMargins(28, 24, 28, 20)
        cardLayout.setSpacing(10)

        # ---- 顶部 header ----
        self.titleLabel = SubtitleLabel("激活 Prismatica 内测版", self._card)
        titleFont = self.titleLabel.font()
        titleFont.setPointSize(16)
        titleFont.setBold(True)
        self.titleLabel.setFont(titleFont)
        self.titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.titleLabel.setStyleSheet("color: #202020;")
        cardLayout.addWidget(self.titleLabel)

        self.hintLabel = BodyLabel(
            "请输入运营下发的内测激活码,粘贴后点击下方「激活」即可。",
            self._card,
        )
        self.hintLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hintLabel.setStyleSheet("color: #888;")
        self.hintLabel.setWordWrap(True)
        cardLayout.addWidget(self.hintLabel)

        # ---- 2026-08-06:删除 Pivot + 邀请码/体验码 Tab,只保留激活码 ----
        self.activationForm = _CodeForm(
            "粘贴激活码", showName=True, parent=self._card
        )
        cardLayout.addWidget(self.activationForm, 1)

        # ---- 重复激活时显示的内联按钮(默认隐藏) ----
        self.reactivateRow = QWidget(self._card)
        rrLayout = QHBoxLayout(self.reactivateRow)
        rrLayout.setContentsMargins(0, 4, 0, 0)
        rrLayout.setSpacing(8)
        rrLayout.addStretch(1)
        self.reactivateInlineBtn = PushButton(
            "立即注销并重试", self.reactivateRow
        )
        self.reactivateInlineBtn.clicked.connect(self._onInlineReactivate)
        rrLayout.addWidget(self.reactivateInlineBtn)
        self.reactivateRow.hide()
        cardLayout.addWidget(self.reactivateRow)

        # ---- 底部按钮行(替代 buttonGroup) ----
        buttonRow = QHBoxLayout()
        buttonRow.setSpacing(12)
        buttonRow.setContentsMargins(0, 8, 0, 0)

        self.cancelButton = PushButton(
            "退出程序" if not reentryMode else "关闭", self._card
        )
        self.cancelButton.clicked.connect(self.reject)
        buttonRow.addWidget(self.cancelButton, 1)

        self.yesButton = PrimaryPushButton("激活", self._card)
        self.yesButton.clicked.connect(self._onActivate)
        buttonRow.addWidget(self.yesButton, 1)

        cardLayout.addLayout(buttonRow)

    def _routeKeyToIndex(self, key: Optional[str]) -> int:
        """保留兼容接口(2026-08-06 删除 Pivot 后,_activeForm 已不再使用)。

        旧调用方若传 key,仍返回 0(invite 的旧值)以避免崩溃;
        新代码应直接调用 _activeForm()。
        """
        return 0

    # ============================================================
    # 模态执行(用 QEventLoop)
    # ============================================================

    def exec(self) -> int:
        """阻塞弹出,直到用户激活/退出。返回 DialogCode(0=reject, 1=accept)。"""
        self.show()
        self.raise_()
        self.activateWindow()
        self._loop = QEventLoop(self)
        self._loop.exec()
        return self._result

    def accept(self) -> None:
        self._result = 1
        self._success = True
        self._close_loop()

    def reject(self) -> None:
        self._result = 0
        self._success = False
        self._close_loop()

    def _close_loop(self) -> None:
        """退出 event loop 后隐藏 dialog,避免「弹窗残留」bug。

        修复(2026-08-05):之前只 quit event loop,dialog 仍 visible,
        导致重新激活后弹窗不消失。
        """
        if self._loop is not None:
            self._loop.quit()
        # 主动 hide —— exec() 返回后 dialog 立即从屏幕上消失。
        # hide 是非破坏性操作,调用方仍可访问 isSuccess() / currentRouteKey() 等。
        self.hide()

    def isSuccess(self) -> bool:
        return self._success

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # 用户点窗口右上角 X 时等同 reject
        self._result = 0
        self._success = False
        self._close_loop()
        super().closeEvent(event)

    # ============================================================
    # 槽
    # ============================================================

    def _onTabChanged(self, index: int) -> None:
        """保留兼容接口(2026-08-06):现仅剩激活码 Tab,不再根据 index 切换文案。"""
        # 旧行为:Pivot 切换时刷新 hintLabel / 按钮文案
        # 新行为:只有一个 Tab,hintLabel 在 __init__ 中已写定,无需再改
        del index  # 显式标记未使用
        self.yesButton.setText("激活")

    def _activeForm(self) -> _CodeForm:
        """返回当前激活的表单。2026-08-06 精简后只剩 activationForm。"""
        return self.activationForm

    def _onActivate(self) -> None:
        form = self._activeForm()
        code = form.codeEdit.text().strip()
        if not code:
            form.codeEdit.setError(True)
            form.codeEdit.setFocus()
            InfoBar.warning(
                title="请输入凭证",
                content="凭证不能为空",
                parent=self,
                duration=2500,
                position=InfoBarPosition.TOP,
            )
            return
        form.codeEdit.setError(False)
        self.reactivateRow.hide()

        displayName = (
            (form.nameEdit.text().strip() if form.nameEdit else "") or "内测用户"
        )
        auth = getAuthService()
        result = auth.redeemCode(code, displayName=displayName)
        if result.success:
            self._success = True
            InfoBar.success(
                title="激活成功",
                content=result.message,
                parent=self,
                duration=3000,
                position=InfoBarPosition.TOP,
            )
            self.accept()
        else:
            form.codeEdit.setError(True)
            if getattr(result, "code", "") == "ALREADY_AUTHENTICATED":
                self.reactivateRow.show()
            InfoBar.error(
                title="激活失败",
                content=result.message,
                parent=self,
                duration=3500,
                position=InfoBarPosition.TOP,
            )

    def _onInlineReactivate(self) -> None:
        from app.core.services.auth_service import getAuthService

        auth = getAuthService()
        auth.deactivate()
        self.reactivateRow.hide()
        InfoBar.success(
            title="已注销",
            content="请重新点击「激活」以使用新凭证",
            parent=self,
            duration=2000,
            position=InfoBarPosition.TOP,
        )