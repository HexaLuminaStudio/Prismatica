# coding: utf-8
"""
批量下载弹窗(PRD-003)

弹窗结构:
    ┌─ 批量下载任务 ────────────────────────────┐
    │ [当前检索条件预览]                            │
    │   - 来源:Hsk                                │
    │   - 参数: ...                               │
    │   - 预计条数:1234                            │
    │                                            │
    │ [+ 添加到清单]                              │
    │                                            │
    │ ── 已添加任务 (N) ──────────────────       │
    │ #1 学中文/日本 1234条  [×]                 │
    │ #2 ...                                     │
    │                                            │
    │              [清空清单]   [取消] [批量下载(N)] │
    └────────────────────────────────────────────┘

交互:
    - 用户在 HskInterface 配好条件 → 点 "+ 添加到清单" → 弹本弹窗
    - 弹窗内查总数 → 用户点 "+ 添加到清单" 按钮把当前项加入 BatchApplyService
    - 用户改条件、再点 "+ 添加到清单" → 弹窗内清单保留
    - 用户点 "批量下载 (N)" → 一次性 createTask N 个 → 跳到 Task 页面
"""

from typing import Any, Dict, Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QFrame
from qfluentwidgets import (
    MessageBoxBase,
    SubtitleLabel,
    BodyLabel,
    StrongBodyLabel,
    CaptionLabel,
    PrimaryPushButton,
    PushButton,
    TransparentToolButton,
    FluentIcon,
    CardWidget,
    ScrollArea,
    HorizontalSeparator,
)

from app.core.services import batchApplyService
from app.core.utils import logger, signalBus


class BatchDownloadDialog(MessageBoxBase):
    """批量下载申请弹窗"""

    def __init__(
        self,
        downloadType: Literal["Hsk", "Global"],
        infoDict: Dict[str, Any],
        parent=None,
    ) -> None:
        super().__init__(parent=parent)
        self.downloadType = downloadType
        self.infoDict = infoDict

        # 当前预览的预计条数(由 _onPreviewFinished 写入)
        self._currentTotal: int = 0
        self._previewLoaded: bool = False

        self._initTitle()
        self._initPreviewSection()
        self._initActionRow()
        self._initListSection()
        self._initFooter()
        self._connectService()

        # 启动当前条件预览(查总数)
        self._startPreview()

    # ========================================================================
    # UI 初始化
    # ========================================================================

    def _initTitle(self) -> None:
        self.titleLabel = SubtitleLabel("批量下载任务", self)
        self.titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.viewLayout.addWidget(self.titleLabel, 0, Qt.AlignmentFlag.AlignCenter)

    def _initPreviewSection(self) -> None:
        """上半区:复用 DownloadApplyWidget 的展示组件预览当前检索条件"""
        self.previewCard = CardWidget(self)
        self.previewLayout = QVBoxLayout(self.previewCard)
        self.previewLayout.setContentsMargins(10, 10, 10, 10)
        self.previewLayout.setSpacing(8)

        self.previewTitleLabel = StrongBodyLabel("当前检索条件预览", self.previewCard)
        self.previewLayout.addWidget(self.previewTitleLabel)

        # 复用 InfoItem / ParamDisplay 展示来源 + 参数 + 预计条数
        from app.view.widgets.download_apply_widget import (
            InfoItem,
            ParamDisplay,
            formatParams,
        )

        self.sourceItem = InfoItem(
            f":app/icons/{self.downloadType}.svg",
            "下载来源：",
            self.downloadType,
            self.previewCard,
        )
        self.previewLayout.addWidget(self.sourceItem)

        self.paramsLabel = BodyLabel("下载参数：", self.previewCard)
        self.paramsLabel.setStyleSheet("color: #666; font-weight: bold;")
        self.previewLayout.addWidget(self.paramsLabel)

        self.paramsDisplay = ParamDisplay(
            self.infoDict.get("payload", {}), self.previewCard
        )
        self.previewLayout.addWidget(self.paramsDisplay)

        self.totalItem = InfoItem(
            ":app/icons/Number.svg", "预计条数：", "查询中...", self.previewCard
        )
        self.previewLayout.addWidget(self.totalItem)

        self.viewLayout.addWidget(self.previewCard)

    def _initActionRow(self) -> None:
        """中间:「+ 添加到清单」按钮"""
        self.addToListButton = PrimaryPushButton("+ 添加到清单", self)
        self.addToListButton.setIcon(FluentIcon.ADD)
        self.addToListButton.setFixedHeight(36)
        self.addToListButton.clicked.connect(self._onAddToListClicked)
        # 预览未加载完成前不允许添加
        self.addToListButton.setEnabled(False)
        self.viewLayout.addWidget(self.addToListButton)

    def _initListSection(self) -> None:
        """下半区:已添加的清单列表"""
        self.listHeaderLabel = StrongBodyLabel("已添加任务 (0)", self)
        self.viewLayout.addWidget(self.listHeaderLabel)

        # 滚动列表(清单多了可以滚)
        self.listScrollArea = ScrollArea(self)
        self.listScrollArea.setStyleSheet("background:transparent;border:none;")
        self.listScrollArea.setWidgetResizable(True)
        self.listScrollArea.setMinimumHeight(140)
        self.listScrollArea.setMaximumHeight(220)

        self.listContainer = QFrame(self)
        self.listLayout = QVBoxLayout(self.listContainer)
        self.listLayout.setContentsMargins(0, 0, 0, 0)
        self.listLayout.setSpacing(6)
        self.listLayout.addStretch(1)

        self.listScrollArea.setWidget(self.listContainer)
        self.viewLayout.addWidget(self.listScrollArea)

    def _initFooter(self) -> None:
        """底部:清空/取消/批量下载按钮"""
        # 自定义底部:先放一个清空按钮(放弹窗底部 buttonGroup 上方)
        footerLayout = QHBoxLayout()
        footerLayout.setContentsMargins(0, 0, 0, 0)
        self.clearListButton = PushButton("清空清单", self)
        self.clearListButton.clicked.connect(self._onClearListClicked)
        footerLayout.addWidget(self.clearListButton)
        footerLayout.addStretch(1)
        self.viewLayout.addLayout(footerLayout)

        # 调整 MessageBoxBase 自带的底部按钮
        self.yesButton.setText("批量下载 (0)")
        self.yesButton.setEnabled(False)
        self.cancelButton.setText("取消")
        self.widget.setFixedWidth(560)

        # MessageBoxBase 默认会把 yesButton.clicked 接到 accept() 上,
        # 这里断开后重连到 _onAccept,以便执行批量任务创建逻辑
        try:
            self.yesButton.clicked.disconnect()
        except Exception:
            pass
        self.yesButton.clicked.connect(self._onAccept)

    # ========================================================================
    # 信号连接
    # ========================================================================

    def _connectService(self) -> None:
        batchApplyService.itemsChanged.connect(self._onItemsChanged)
        self._renderList()

    # ========================================================================
    # 当前条件预览
    # ========================================================================

    def _startPreview(self) -> None:
        """启动 GetTotalWorker 查询当前检索条件预计条数"""
        from app.core.services import GetTotalWorker, GlobalGetTotalWorker

        if self.downloadType == "Hsk":
            self.previewWorker = GetTotalWorker(self.infoDict)
        else:
            self.previewWorker = GlobalGetTotalWorker(self.infoDict)
        self.previewWorker.finished.connect(self._onPreviewFinished)
        self.previewWorker.failed.connect(self._onPreviewFailed)
        self.previewWorker.start()

    def _onPreviewFinished(self, total: int) -> None:
        self._currentTotal = total
        self._previewLoaded = True
        if total > 0:
            self.totalItem.updateValue(f"{total} 条")
        else:
            self.totalItem.updateValue("未找到数据")
        # 允许添加(即使 0 条也允许,用户可能就是要下载 0 条)
        self.addToListButton.setEnabled(True)
        self._cleanupPreviewWorker()

    def _onPreviewFailed(self, errorMsg: str) -> None:
        self._currentTotal = 0
        self._previewLoaded = True
        self.totalItem.updateValue(f"查询失败:{errorMsg[:40]}")
        # 失败时仍允许添加(用户可能已知)
        self.addToListButton.setEnabled(True)
        self._cleanupPreviewWorker()

    def _cleanupPreviewWorker(self) -> None:
        worker = getattr(self, "previewWorker", None)
        if worker is None:
            return
        try:
            stopFn = getattr(worker, "stop", None)
            if callable(stopFn):
                try:
                    stopFn()
                except Exception:
                    pass
            try:
                worker.wait(500)
            except Exception:
                pass
            try:
                worker.finished.disconnect(self._onPreviewFinished)
            except Exception:
                pass
            try:
                worker.failed.disconnect(self._onPreviewFailed)
            except Exception:
                pass
            try:
                worker.deleteLater()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"[BatchDownloadDialog] 清理 preview worker 异常: {e}")
        self.previewWorker = None

    # ========================================================================
    # 清单操作
    # ========================================================================

    def _onAddToListClicked(self) -> None:
        if not self._previewLoaded:
            return
        url = self.infoDict.get("url", "")
        payload = dict(self.infoDict.get("payload", {}))
        ok = batchApplyService.addItem(url, payload, self._currentTotal)
        if not ok:
            # 重复:警告 InfoBar
            from qfluentwidgets import InfoBar, InfoBarIcon, InfoBarPosition

            InfoBar.warning(
                title="重复",
                content="已存在相同检索条件的任务",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=2500,
                position=InfoBarPosition.TOP,
                parent=self.window(),
            )

    def _onRemoveItemClicked(self, index: int) -> None:
        batchApplyService.removeItem(index)

    def _onClearListClicked(self) -> None:
        batchApplyService.clearAll()

    def _onItemsChanged(self, count: int) -> None:
        self._renderList()
        # 更新底部按钮文本与启用状态
        self.yesButton.setText(f"批量下载 ({count})")
        self.yesButton.setEnabled(count > 0)
        self.listHeaderLabel.setText(f"已添加任务 ({count})")

    def _renderList(self) -> None:
        """刷新清单列表 UI"""
        # 清空旧项(保留末尾 stretch)
        while self.listLayout.count() > 1:
            child = self.listLayout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()

        items = batchApplyService.getItems()
        for index, item in enumerate(items):
            row = self._makeListRow(index, item)
            self.listLayout.insertWidget(self.listLayout.count() - 1, row)

    def _makeListRow(self, index: int, item) -> QFrame:
        """构造清单列表中的一行"""
        row = QFrame(self.listContainer)
        row.setObjectName(f"BatchListRow_{index}")
        rowLayout = QHBoxLayout(row)
        rowLayout.setContentsMargins(10, 6, 6, 6)
        rowLayout.setSpacing(8)

        # 序号
        indexLabel = StrongBodyLabel(f"#{index + 1}", row)
        indexLabel.setFixedWidth(32)
        indexLabel.setStyleSheet("color: #00b09c;")
        rowLayout.addWidget(indexLabel)

        # 摘要 + 条数(垂直)
        textBox = QVBoxLayout()
        textBox.setSpacing(2)
        summaryLabel = BodyLabel(item.summary() or "(无参数)", row)
        summaryLabel.setWordWrap(True)
        textBox.addWidget(summaryLabel)
        countCaption = CaptionLabel(f"预计 {item.total} 条", row)
        countCaption.setStyleSheet("color: #888;")
        textBox.addWidget(countCaption)
        textWrap = QFrame(row)
        textWrap.setLayout(textBox)
        rowLayout.addWidget(textWrap, 1)

        # 删除按钮
        removeBtn = TransparentToolButton(FluentIcon.CLOSE, row)
        removeBtn.setFixedSize(28, 28)
        removeBtn.setToolTip("从清单移除")
        removeBtn.clicked.connect(lambda _checked=False, i=index: self._onRemoveItemClicked(i))
        rowLayout.addWidget(removeBtn)

        # 分隔线(非首行)
        if index > 0:
            sep = HorizontalSeparator(self.listContainer)
            self.listLayout.insertWidget(self.listLayout.count() - 1, sep)

        return row

    # ========================================================================
    # 提交
    # ========================================================================

    def _onAccept(self) -> None:
        """点击"批量下载 (N)" → 一次性创建 N 个任务 → 跳转 Task 页面"""
        items = batchApplyService.getItems()
        if not items:
            return
        from app.core.services import taskManager

        # 根据弹窗来源选择对应的 taskType(PRD-003 bug-fix)
        if self.downloadType == "Hsk":
            taskType = "hskDownload"
        else:
            taskType = "globalDownload"

        created = 0
        for item in items:
            try:
                taskId = taskManager.createTask(taskType, item.toInfoDict())
                created += 1
                logger.info(
                    f"[BatchDownloadDialog] 创建任务 {taskId}: {item.summary()[:40]}"
                )
            except Exception as e:
                logger.error(f"[BatchDownloadDialog] createTask 失败: {e}")

        # 清空清单
        batchApplyService.clearAll()

        # InfoBar 提示(在父窗口上,弹窗已 accept 后会被销毁)
        if created > 0:
            from qfluentwidgets import InfoBar, InfoBarPosition

            targetWindow = self.window() if self.parent() is None else self.parent().window()
            try:
                InfoBar.success(
                    title="批量任务已创建",
                    content=f"已创建 {created} 个下载任务,请到任务中心查看进度",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    duration=3500,
                    position=InfoBarPosition.TOP,
                    parent=targetWindow,
                )
            except Exception:
                pass
            # 跳转 Task 页面
            try:
                signalBus.navigateToSubInterface.emit("TaskInterface")
            except Exception as e:
                logger.warning(f"[BatchDownloadDialog] 跳转 Task 页面失败: {e}")

        # 关闭弹窗(yesButton 默认 accept 连接已在 _initFooter 断开)
        self.close()

    # ========================================================================
    # 关闭时清理
    # ========================================================================

    def closeEvent(self, event) -> None:
        try:
            batchApplyService.itemsChanged.disconnect(self._onItemsChanged)
        except Exception:
            pass
        self._cleanupPreviewWorker()
        super().closeEvent(event)
