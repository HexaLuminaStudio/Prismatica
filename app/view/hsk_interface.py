# coding: utf-8
# Hsk动态作文语料库下载页面

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

from app.core.utils import logger, signalBus
from .widgets.hsk_search_widget import (
    AdvancedSettingCardWidget,
    HskSearchContainer,
    SpecificConditionSearchWidget,
    StringGeneralSearchWidget,
    WordCombinationSearchWidget,
    WrongSentenceSearchWidget,
)


class HskInterface(QWidget):
    """HSK语料库下载界面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("HskInterface")

        # 搜索容器（包含滚动布局）
        self.searchContainer = HskSearchContainer(self)

        # 创建各搜索组件
        self.stringGeneralSearchWidget = StringGeneralSearchWidget()
        self.specialConditionSearchWidget = SpecificConditionSearchWidget()
        self.wordCombinationSearchWidget = WordCombinationSearchWidget()
        self.wrongCombinationSearchWidget = WrongSentenceSearchWidget()
        self.advancedSettingCardWidget = AdvancedSettingCardWidget()

        # 将搜索组件添加到容器
        self.searchContainer.addSearchWidget(self.stringGeneralSearchWidget)
        self.searchContainer.addSearchWidget(self.specialConditionSearchWidget)
        self.searchContainer.addSearchWidget(self.wordCombinationSearchWidget)
        self.searchContainer.addSearchWidget(self.wrongCombinationSearchWidget)

        # 分段控件和按钮
        self.typeSegmentedWidget = SegmentedWidget(self)
        self.runTaskButton = PushButton("申请任务", self)

        self._initWidget()

    def _initWidget(self):
        """初始化部件"""
        self.typeSegmentedWidget.addItem("stringGeneral", "字符串一般检索")
        self.typeSegmentedWidget.addItem("specificCondition", "特定条件检索")
        self.typeSegmentedWidget.addItem("wordCombination", "词语搭配检索")
        self.typeSegmentedWidget.addItem("wrongSentence", "错句检索")
        self.typeSegmentedWidget.setCurrentItem("stringGeneral")
        self.typeSegmentedWidget.currentItemChanged.connect(self._shiftWidget)

        # 设置搜索组件的大小策略
        searchWidgets = [
            self.stringGeneralSearchWidget,
            self.specialConditionSearchWidget,
            self.wordCombinationSearchWidget,
            self.wrongCombinationSearchWidget,
        ]
        for widget in searchWidgets:
            widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            # 默认显示第一个组件，隐藏其他
            if widget != self.stringGeneralSearchWidget:
                widget.hide()

        self.runTaskButton.setFixedSize(130, 40)
        self.runTaskButton.setIcon(":app/icons/Check.svg")
        self.runTaskButton.clicked.connect(self._runTask)

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

        # 按钮行
        btnRow = QHBoxLayout()
        btnRow.addStretch(1)
        btnRow.addWidget(self.runTaskButton)
        vBoxLayout.addLayout(btnRow)

    def _shiftWidget(self, index):
        """切换搜索组件"""
        widgetDict = {
            "stringGeneral": self.stringGeneralSearchWidget,
            "specificCondition": self.specialConditionSearchWidget,
            "wordCombination": self.wordCombinationSearchWidget,
            "wrongSentence": self.wrongCombinationSearchWidget,
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
        logger.info("[HSK] 用户点击申请任务按钮")

        # 获取当前显示的搜索组件
        currentWidget = self._getCurrentSearchWidget()
        if currentWidget is None:
            return

        baseDict = currentWidget.returnValues()

        # 验证输入条件
        isValid, errorType = self._validateInput(baseDict)
        if not isValid:
            logger.warning("[HSK] 输入验证未通过")
            self._showInputError(errorType, currentWidget)
            return

        advanceDict = self.advancedSettingCardWidget.returnValues()
        url = baseDict.get("url")
        baseDict.pop("url")
        infoDict = {"url": url, "payload": {**baseDict, **advanceDict}}

        # 显示加载状态
        logger.debug(f"[HSK] 搜索参数: {infoDict}")
        self.runTaskButton.setEnabled(False)
        self.runTaskButton.setText("处理中...")

        try:
            # 显示下载确认对话框
            from app.view.widgets.download_apply_widget import DownloadApplyWidget

            w = DownloadApplyWidget("Hsk", infoDict, self.window())
            if w.exec():
                # 用户确认，创建下载任务
                from app.core.services import taskManager
                taskId = taskManager.createTask("hskDownload", infoDict)
                logger.info(f"[HSK] 创建下载任务成功, taskId={taskId}")

                # 显示成功提示
                InfoBar.success(
                    "任务已创建",
                    f"HSK下载任务已加入队列",
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

    def _getCurrentSearchWidget(self):
        """获取当前显示的搜索组件"""
        currentIndex = self.typeSegmentedWidget.currentRouteKey()
        widgetDict = {
            "stringGeneral": self.stringGeneralSearchWidget,
            "specificCondition": self.specialConditionSearchWidget,
            "wordCombination": self.wordCombinationSearchWidget,
            "wrongSentence": self.wrongCombinationSearchWidget,
        }
        return widgetDict.get(currentIndex)

    def _validateInput(self, baseDict):
        """验证输入条件，返回 (isValid, errorType)"""
        if "keyword" in baseDict:
            if not baseDict.get("keyword"):
                return (False, "keyword")
        elif (
            "start_word" in baseDict
            or "pre_word" in baseDict
            or "post_word" in baseDict
            or "end_word" in baseDict
        ):
            hasCondition = any(
                [
                    baseDict.get("start_word"),
                    baseDict.get("pre_word"),
                    baseDict.get("post_word"),
                    baseDict.get("end_word"),
                ]
            )
            if not hasCondition:
                return (False, "condition")
        elif "wrong_type" in baseDict:
            pass
        else:
            pass
        return (True, None)

    def _showInputError(self, errorType: str, currentWidget):
        """显示输入错误提示"""
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
            title="（；´д｀）ゞ",
            content=content,
            isClosable=True,
            tailPosition=TeachingTipTailPosition.BOTTOM,
            duration=2000,
            parent=self,
        )
        target.setFocus()
