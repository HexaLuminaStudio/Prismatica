# coding: utf-8
"""HSK 动态作文语料库下载工作台。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon,
    InfoBar,
    InfoBarIcon,
    InfoBarPosition,
    TeachingTip,
    TeachingTipTailPosition,
)

from app.core.services import batchApplyService
from app.core.utils import logger, signalBus
from app.view.widgets.download_workbench import DownloadMode, DownloadTaskWorkbench
from app.view.widgets.hsk_search_widget import (
    AdvancedSettingCardWidget,
    SpecificConditionSearchWidget,
    StringGeneralSearchWidget,
    WordCombinationSearchWidget,
    WrongSentenceSearchWidget,
)

HSK_DOWNLOAD_TASK_TYPE = "hskDownload"


class HskInterface(QWidget):
    """HSK 语料库下载界面。"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("HskInterface")

        self.stringGeneralSearchWidget = StringGeneralSearchWidget()
        self.specialConditionSearchWidget = SpecificConditionSearchWidget()
        self.wordCombinationSearchWidget = WordCombinationSearchWidget()
        self.wrongCombinationSearchWidget = WrongSentenceSearchWidget()
        self.advancedSettingCardWidget = AdvancedSettingCardWidget()

        self.workbench = DownloadTaskWorkbench(
            title="HSK 语料下载",
            subtitle="组合检索条件，核对任务摘要后创建远程下载任务。",
            sourceName="HSK 动态作文语料库",
            sourceCaption="创建任务前会查询预计命中数量，并由任务中心持续跟踪下载。",
            pageIcon=FluentIcon.CLOUD_DOWNLOAD,
            modes=[
                DownloadMode(
                    "stringGeneral",
                    "关键词检索",
                    "按词或短语定位作文句子",
                    FluentIcon.SEARCH,
                ),
                DownloadMode(
                    "specificCondition",
                    "上下文条件",
                    "组合首词、前后词与距离",
                    FluentIcon.DOCUMENT,
                ),
                DownloadMode(
                    "wordCombination",
                    "句法搭配",
                    "按关键词和句法关系检索",
                    FluentIcon.LINK,
                ),
                DownloadMode(
                    "wrongSentence",
                    "错句类型",
                    "按预设偏误句式筛选",
                    FluentIcon.TAG,
                ),
            ],
            parent=self,
        )

        # 保留既有公开属性，避免其他页面或测试因工作台重构失效。
        self.typeSegmentedWidget = self.workbench.typeSegmentedWidget
        self.searchContainer = self.workbench.searchStack
        self.runTaskButton = self.workbench.runTaskButton
        self.batchAddButton = self.workbench.batchAddButton
        self.batchDownloadButton = self.workbench.batchDownloadButton

        self._initWidget()

    def _initWidget(self) -> None:
        """挂载真实表单并连接既有业务操作。"""
        self.workbench.addSearchWidget(
            "stringGeneral", self.stringGeneralSearchWidget
        )
        self.workbench.addSearchWidget(
            "specificCondition", self.specialConditionSearchWidget
        )
        self.workbench.addSearchWidget(
            "wordCombination", self.wordCombinationSearchWidget
        )
        self.workbench.addSearchWidget(
            "wrongSentence", self.wrongCombinationSearchWidget
        )
        self.workbench.setAdvancedWidget(self.advancedSettingCardWidget)

        self.workbench.modeChanged.connect(self._shiftWidget)
        self.workbench.summaryRefreshRequested.connect(self._refreshTaskSummary)
        self.runTaskButton.clicked.connect(self._runTask)
        self.batchAddButton.clicked.connect(self._onBatchAddClicked)
        self.batchDownloadButton.clicked.connect(self._onBatchDownloadClicked)
        batchApplyService.itemsChanged.connect(self._onBatchItemsChanged)

        self._initLayout()
        self.workbench.setCurrentMode("stringGeneral")
        self._onBatchItemsChanged(batchApplyService.getCount())
        self._refreshTaskSummary()

    def _initLayout(self) -> None:
        """初始化页面布局。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.workbench)

    def _shiftWidget(self, routeKey: str) -> None:
        """响应工作台检索方式变化。"""
        if self.workbench.currentRouteKey() != routeKey:
            self.workbench.setCurrentMode(routeKey)
        self._refreshTaskSummary()

    def _refreshTaskSummary(self, *_args) -> None:
        """把当前控件值转换为用户可读的任务摘要。"""
        routeKey = self.workbench.currentRouteKey()
        entries = []
        if routeKey == "stringGeneral":
            keyword = self.stringGeneralSearchWidget.keyWord.text().strip()
            if keyword:
                entries.append(("关键词", keyword))
        elif routeKey == "specificCondition":
            values = (
                ("首字符串", self.specialConditionSearchWidget.initialString.text()),
                ("前词", self.specialConditionSearchWidget.previousWords.text()),
                ("后词", self.specialConditionSearchWidget.postWord.text()),
                ("尾字符串", self.specialConditionSearchWidget.tailString.text()),
            )
            entries.extend(
                (label, value.strip()) for label, value in values if value.strip()
            )
            if entries:
                entries.append(
                    ("词距", str(self.specialConditionSearchWidget.compactSpinBox.value()))
                )
        elif routeKey == "wordCombination":
            keyword = self.wordCombinationSearchWidget.keyWord.text().strip()
            if keyword:
                entries.append(("关键词", keyword))
            entries.append(
                (
                    "句法结构",
                    self.wordCombinationSearchWidget.relationshipCombobox.currentText(),
                )
            )
        elif routeKey == "wrongSentence":
            entries.append(
                (
                    "错句类型",
                    self.wrongCombinationSearchWidget.sentencePatternComBobox.currentText(),
                )
            )

        if self.advancedSettingCardWidget.enableCheckBox.isChecked():
            if self.advancedSettingCardWidget.essayTitle.currentText() != "不限":
                entries.append(
                    ("作文题目", self.advancedSettingCardWidget.essayTitle.currentText())
                )
            if self.advancedSettingCardWidget.certificateLevel.currentText() != "不限":
                entries.append(
                    (
                        "作文等级",
                        self.advancedSettingCardWidget.certificateLevel.currentText(),
                    )
                )
            nationality = self.advancedSettingCardWidget.nationality.text().strip()
            if nationality:
                entries.append(("国籍", nationality))
        self.workbench.setSummary(entries)

    def _runTask(self) -> None:
        """验证当前条件并创建单个下载任务。"""
        logger.info("[HSK] 用户点击创建下载任务按钮")
        currentWidget = self._getCurrentSearchWidget()
        if currentWidget is None:
            return

        baseDict = currentWidget.returnValues()
        isValid, errorType = self._validateInput(baseDict)
        if not isValid:
            logger.warning("[HSK] 输入验证未通过")
            self._showInputError(errorType, currentWidget)
            return

        advanceDict = self.advancedSettingCardWidget.returnValues()
        url = baseDict.pop("url", None)
        infoDict = {"url": url, "payload": {**baseDict, **advanceDict}}
        logger.debug(f"[HSK] 搜索参数: {infoDict}")
        self.workbench.setBusy(True)

        try:
            from app.view.widgets.download_apply_widget import DownloadApplyWidget

            dialog = DownloadApplyWidget("Hsk", infoDict, self.window())
            if dialog.exec():
                from app.core.services import taskManager

                taskId = taskManager.createTask(HSK_DOWNLOAD_TASK_TYPE, infoDict)
                logger.info(f"[HSK] 创建下载任务成功, taskId={taskId}")
                InfoBar.success(
                    "任务已创建",
                    "HSK 下载任务已加入队列",
                    Qt.Orientation.Horizontal,
                    True,
                    3000,
                    InfoBarPosition.TOP_RIGHT,
                    self.window(),
                )
        finally:
            self.workbench.setBusy(False)

    def _buildInfoDict(self) -> dict:
        """根据当前检索条件构造任务参数。"""
        currentWidget = self._getCurrentSearchWidget()
        if currentWidget is None:
            return {}
        baseDict = currentWidget.returnValues()
        isValid, errorType = self._validateInput(baseDict)
        if not isValid:
            self._showInputError(errorType, currentWidget)
            return {}
        advanceDict = self.advancedSettingCardWidget.returnValues()
        url = baseDict.pop("url", None)
        return {"url": url, "payload": {**baseDict, **advanceDict}}

    def _onBatchAddClicked(self) -> None:
        """把当前条件加入 HSK 批量清单。"""
        infoDict = self._buildInfoDict()
        if not infoDict:
            return
        logger.debug(f"[HSK] 批量添加: {infoDict}")
        from app.view.widgets.batch_download_dialog import BatchDownloadDialog

        dialog = BatchDownloadDialog("Hsk", infoDict, self.window())
        dialog.exec()

    def _onBatchDownloadClicked(self) -> None:
        """仅提交 HSK 类型的批量下载任务。"""
        if batchApplyService.getCount(HSK_DOWNLOAD_TASK_TYPE) == 0:
            return
        items = batchApplyService.getItems(HSK_DOWNLOAD_TASK_TYPE)
        from app.core.services import taskManager

        created = 0
        for item in items:
            try:
                taskId = taskManager.createTask(item.taskType, item.toInfoDict())
                created += 1
                logger.info(f"[HSK] 批量创建任务 {taskId}: {item.summary()[:40]}")
            except Exception as exc:
                logger.error(f"[HSK] createTask 失败: {exc}")
        batchApplyService.clearAll(HSK_DOWNLOAD_TASK_TYPE)
        if created > 0:
            InfoBar.success(
                title="批量任务已创建",
                content=f"已创建 {created} 个下载任务，请到任务中心查看进度",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=3500,
                position=InfoBarPosition.TOP_RIGHT,
                parent=self.window(),
            )
            try:
                signalBus.navigateToSubInterface.emit("TaskInterface")
            except Exception as exc:
                logger.warning(f"[HSK] 跳转 Task 页面失败: {exc}")

    def _onBatchItemsChanged(self, _count: int) -> None:
        """刷新当前来源的批量清单状态。"""
        count = batchApplyService.getCount(HSK_DOWNLOAD_TASK_TYPE)
        self.workbench.setBatchCount(count)

    def _getCurrentSearchWidget(self):
        """获取当前检索方式对应的真实表单。"""
        widgetDict = {
            "stringGeneral": self.stringGeneralSearchWidget,
            "specificCondition": self.specialConditionSearchWidget,
            "wordCombination": self.wordCombinationSearchWidget,
            "wrongSentence": self.wrongCombinationSearchWidget,
        }
        return widgetDict.get(self.workbench.currentRouteKey())

    def _validateInput(self, baseDict):
        """验证输入条件，返回 (isValid, errorType)。"""
        if "keyword" in baseDict:
            if not baseDict.get("keyword"):
                return (False, "keyword")
        elif any(
            key in baseDict
            for key in ("start_word", "pre_word", "post_word", "end_word")
        ):
            if not any(
                baseDict.get(key)
                for key in ("start_word", "pre_word", "post_word", "end_word")
            ):
                return (False, "condition")
        return (True, None)

    def _showInputError(self, errorType: str, currentWidget) -> None:
        """在对应输入控件附近显示中文错误提示。"""
        if errorType == "keyword":
            target = getattr(currentWidget, "keyWord", None)
            content = "请输入关键字"
        elif errorType == "condition":
            target = (
                getattr(currentWidget, "initialString", None)
                or getattr(currentWidget, "previousWords", None)
                or getattr(currentWidget, "postWord", None)
            )
            content = "请至少填写一个检索条件"
        else:
            return
        if target is None:
            return

        TeachingTip.create(
            target=target,
            icon=InfoBarIcon.WARNING,
            title="检索条件不完整",
            content=content,
            isClosable=True,
            tailPosition=TeachingTipTailPosition.BOTTOM,
            duration=2000,
            parent=self,
        )
        target.setFocus()
