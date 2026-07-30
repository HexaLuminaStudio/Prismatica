# coding: utf-8
# Global中介语语料库下载页面

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    InfoBar,
    InfoBarIcon,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    setFont,
    TeachingTip,
    TeachingTipTailPosition,
)

from app.core.services import batchApplyService
from app.core.utils import logger, signalBus
from .widgets.global_search_widget import (
    GlobalAdvancedSettingCardWidget,
    GlobalSearchContainer,
    SpecificConditionSearchWidget,
    SpeechPartSearchWidget,
    StringGeneralSearchWidget,
    WordCombinationSearchWidget,
)
from .widgets.download_apply_widget import DownloadApplyWidget


class GlobalInterface(QWidget):
    """Global语料库下载界面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("GlobalInterface")

        # 搜索容器（包含滚动布局）
        self.searchContainer = GlobalSearchContainer(self)

        # 创建各搜索组件
        self.stringGeneralSearchWidget = StringGeneralSearchWidget()
        self.specialConditionSearchWidget = SpecificConditionSearchWidget()
        self.wordCombinationSearchWidget = WordCombinationSearchWidget()
        self.speechPartSearchWidget = SpeechPartSearchWidget()
        self.advancedSettingCardWidget = GlobalAdvancedSettingCardWidget()

        # 将搜索组件添加到容器
        self.searchContainer.addSearchWidget(self.stringGeneralSearchWidget)
        self.searchContainer.addSearchWidget(self.specialConditionSearchWidget)
        self.searchContainer.addSearchWidget(self.wordCombinationSearchWidget)
        self.searchContainer.addSearchWidget(self.speechPartSearchWidget)

        # 分段控件和按钮
        self.typeSegmentedWidget = SegmentedWidget(self)
        self.runTaskButton = PushButton("申请任务", self)
        self.batchAddButton = PushButton("+ 添加到清单", self)
        self.batchDownloadButton = PushButton("批量下载 (0)", self)

        self._initWidget()

    def _initWidget(self):
        """初始化部件"""
        self.typeSegmentedWidget.addItem("stringGeneral", "字符串一般检索")
        self.typeSegmentedWidget.addItem("specificCondition", "特定条件检索")
        self.typeSegmentedWidget.addItem("wordCombination", "词语搭配检索")
        self.typeSegmentedWidget.addItem("speechPart", "按词性检索")
        self.typeSegmentedWidget.setCurrentItem("stringGeneral")
        self.typeSegmentedWidget.currentItemChanged.connect(self._shiftWidget)

        # 设置搜索组件的大小策略
        searchWidgets = [
            self.stringGeneralSearchWidget,
            self.specialConditionSearchWidget,
            self.wordCombinationSearchWidget,
            self.speechPartSearchWidget,
        ]
        for widget in searchWidgets:
            widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            # 默认显示第一个组件，隐藏其他
            if widget != self.stringGeneralSearchWidget:
                widget.hide()

        self.runTaskButton.setFixedSize(130, 40)
        self.runTaskButton.setIcon(":app/icons/Check.svg")
        self.runTaskButton.clicked.connect(self._runTask)

        # PRD-003:批量下载按钮(复用 HSK 模式)
        self.batchAddButton.setFixedSize(140, 40)
        self.batchAddButton.setIcon(":app/icons/Check.svg")
        self.batchAddButton.clicked.connect(self._onBatchAddClicked)
        self.batchDownloadButton.setFixedSize(140, 40)
        self.batchDownloadButton.clicked.connect(self._onBatchDownloadClicked)
        self.batchDownloadButton.setEnabled(False)
        # 订阅清单数量变化,更新徽章与启用状态
        batchApplyService.itemsChanged.connect(self._onBatchItemsChanged)
        self._onBatchItemsChanged(batchApplyService.getCount())

        self._initLayout()

    def _initLayout(self):
        """初始化布局"""
        vBoxLayout = QVBoxLayout(self)
        vBoxLayout.setContentsMargins(15, 5, 15, 15)
        vBoxLayout.setSpacing(10)

        # 添加组件到布局
        vBoxLayout.addWidget(self.typeSegmentedWidget)
        vBoxLayout.addWidget(self.searchContainer)
        vBoxLayout.addWidget(self.advancedSettingCardWidget)
        vBoxLayout.addStretch(1)

        # 按钮行:批量入口(左)+ 申请任务(右)
        btnRow = QHBoxLayout()
        btnRow.addWidget(self.batchAddButton)
        btnRow.addWidget(self.batchDownloadButton)
        btnRow.addStretch(1)
        btnRow.addWidget(self.runTaskButton)
        vBoxLayout.addLayout(btnRow)

    def _shiftWidget(self, index):
        """切换搜索组件"""
        widgetDict = {
            "stringGeneral": self.stringGeneralSearchWidget,
            "specificCondition": self.specialConditionSearchWidget,
            "wordCombination": self.wordCombinationSearchWidget,
            "speechPart": self.speechPartSearchWidget,
        }

        # 隐藏所有搜索组件
        for widget in widgetDict.values():
            widget.hide()

        # 显示选中的搜索组件
        if index in widgetDict:
            widget = widgetDict[index]
            widget.show()
            self.searchContainer.getSearchWidgets()

    def _runTask(self):
        """执行任务"""
        logger.info("[Global] 用户点击申请任务按钮")

        # 获取当前显示的搜索组件
        currentWidget = self._getCurrentSearchWidget()
        if currentWidget is None:
            return

        baseDict = currentWidget.returnValues()

        # 验证输入条件
        isValid, errorType = self._validateInput(baseDict)
        if not isValid:
            logger.warning("[Global] 输入验证未通过")
            self._showInputError(errorType, currentWidget)
            return

        advanceDict = self.advancedSettingCardWidget.returnValues()
        constantDict = {"corp_org_id": "", "isDeptCheck": False, "ft": ""}
        url = baseDict.get("url")
        baseDict.pop("url")

        # 处理 ft 参数
        if "ft" in advanceDict:
            constantDict.pop("ft")

        infoDict = {"url": url, "payload": {**baseDict, **advanceDict, **constantDict}}

        logger.debug(f"[Global] 搜索参数: {infoDict}")
        self.runTaskButton.setEnabled(False)
        self.runTaskButton.setText("处理中...")

        try:
            # 显示下载确认对话框
            w = DownloadApplyWidget("Global", infoDict, self.window())
            if w.exec():
                # 用户确认，创建下载任务
                from app.core.services import taskManager

                taskId = taskManager.createTask("globalDownload", infoDict)
                logger.info(f"[Global] 创建下载任务成功, taskId={taskId}")

                # 显示成功提示
                InfoBar.success(
                    "任务已创建",
                    f"Global下载任务已加入队列",
                    Qt.Orientation.Horizontal,
                    True,
                    3000,
                    InfoBarPosition.TOP_RIGHT,
                    self.window(),
                )

        finally:
            # 恢复按钮状态
            self.runTaskButton.setEnabled(True)
            self.runTaskButton.setText("申请任务")

    # ========================================================================
    # PRD-003 批量下载入口(复用 HSK 模式)
    # ========================================================================

    def _buildInfoDict(self) -> dict:
        """根据当前检索条件构造 infoDict(url + payload)"""
        currentWidget = self._getCurrentSearchWidget()
        if currentWidget is None:
            return {}
        baseDict = currentWidget.returnValues()
        isValid, errorType = self._validateInput(baseDict)
        if not isValid:
            self._showInputError(errorType, currentWidget)
            return {}
        advanceDict = self.advancedSettingCardWidget.returnValues()
        constantDict = {"corp_org_id": "", "isDeptCheck": False, "ft": ""}
        url = baseDict.get("url")
        baseDict.pop("url")
        # ft 在 advanceDict 中存在时不使用默认值
        if "ft" in advanceDict:
            constantDict.pop("ft")
        return {"url": url, "payload": {**baseDict, **advanceDict, **constantDict}}

    def _onBatchAddClicked(self) -> None:
        """+ 添加到清单 → 弹出 BatchDownloadDialog(弹窗内可继续添加)"""
        infoDict = self._buildInfoDict()
        if not infoDict:
            return
        logger.debug(f"[Global] 批量添加: {infoDict}")
        from app.view.widgets.batch_download_dialog import BatchDownloadDialog

        dlg = BatchDownloadDialog("Global", infoDict, self.window())
        dlg.exec()

    def _onBatchDownloadClicked(self) -> None:
        """底部"批量下载 (N)"按钮 → 快捷入口:一次性创建 N 个任务"""
        if batchApplyService.getCount() == 0:
            return
        items = batchApplyService.getItems()
        from app.core.services import taskManager

        created = 0
        for item in items:
            try:
                taskId = taskManager.createTask("globalDownload", item.toInfoDict())
                created += 1
                logger.info(f"[Global] 批量创建任务 {taskId}: {item.summary()[:40]}")
            except Exception as e:
                logger.error(f"[Global] createTask 失败: {e}")
        batchApplyService.clearAll()
        if created > 0:
            InfoBar.success(
                title="批量任务已创建",
                content=f"已创建 {created} 个下载任务,请到任务中心查看进度",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            try:
                signalBus.navigateToSubInterface.emit("TaskInterface")
            except Exception as e:
                logger.warning(f"[Global] 跳转 Task 页面失败: {e}")

    def _onBatchItemsChanged(self, count: int) -> None:
        """清单数量变化 → 更新批量下载按钮的徽章与启用状态"""
        self.batchDownloadButton.setText(f"批量下载 ({count})")
        self.batchDownloadButton.setEnabled(count > 0)

    def _getCurrentSearchWidget(self):
        """获取当前显示的搜索组件"""
        currentIndex = self.typeSegmentedWidget.currentRouteKey()
        widgetDict = {
            "stringGeneral": self.stringGeneralSearchWidget,
            "specificCondition": self.specialConditionSearchWidget,
            "wordCombination": self.wordCombinationSearchWidget,
            "speechPart": self.speechPartSearchWidget,
        }
        return widgetDict.get(currentIndex)

    def _validateInput(self, baseDict):
        """验证输入条件，返回 (isValid, errorType)"""
        if "keystr" in baseDict:
            # 字符串一般检索、词语搭配检索、按词性检索
            if not baseDict.get("keystr"):
                return (False, "keyword")
        elif (
            "shou" in baseDict
            or "kaishi" in baseDict
            or "jieshu" in baseDict
            or "wei" in baseDict
        ):
            # 特定条件检索：至少需要填写一个条件
            hasCondition = any(
                [
                    baseDict.get("shou"),
                    baseDict.get("kaishi"),
                    baseDict.get("jieshu"),
                    baseDict.get("wei"),
                ]
            )
            if not hasCondition:
                return (False, "condition")
        return (True, None)

    def _showInputError(self, errorType: str, currentWidget):
        """显示输入错误提示"""
        if errorType == "keyword":
            target = getattr(currentWidget, "keyWord", None) or getattr(
                currentWidget, "speechPartLineEdit", None
            )
            content = "请输入关键字"
        elif errorType == "condition":
            target = (
                getattr(currentWidget, "initialString", None)
                or getattr(currentWidget, "previousWords", None)
                or getattr(currentWidget, "postWord", None)
                or getattr(currentWidget, "tailString", None)
            )
            content = "请至少填写一个检索条件"
        else:
            return

        if target is None:
            return

        TeachingTip.create(
            target=target,
            icon=InfoBarIcon.WARNING,
            title="（；´д｀）ゞ",
            content=content,
            isClosable=True,
            tailPosition=TeachingTipTailPosition.BOTTOM,
            duration=2000,
            parent=self,
        )
        target.setFocus()
