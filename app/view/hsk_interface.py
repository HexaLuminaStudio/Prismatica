# coding: utf-8
# Hsk动态作文语料库下载页面

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from PySide6.QtWidgets import QStackedWidget
from qfluentwidgets import (
    InfoBar,
    InfoBarIcon,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    setFont,
    TeachingTip,
    TeachingTipTailPosition,
)

from app.core.utils import logger, signalBus


class HskInterface(QWidget):
    """HSK语料库下载界面"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("HskInterface")

        self.typeSegmentedWidget = SegmentedWidget(self)

        # 使用StackedWidget
        self.stackedWidget = QStackedWidget(self)

        # 导入各搜索组件
        from .widgets.hsk_search_widget import (
            AdvancedSettingCardWidget,
            SpecificConditionSearchWidget,
            StringGeneralSearchWidget,
            WordCombinationSearchWidget,
            WrongSentenceSearchWidget,
        )

        self.stringGeneralSearchWidget = StringGeneralSearchWidget(self)
        self.specialConditionSearchWidget = SpecificConditionSearchWidget(self)
        self.wordCombinationSearchWidget = WordCombinationSearchWidget(self)
        self.wrongCombinationSearchWidget = WrongSentenceSearchWidget(self)
        self.advancedSettingCardWidget = AdvancedSettingCardWidget(self)

        # 申请任务按钮
        self.runTaskButton = PrimaryPushButton("申请任务", self)

        self._initWidget()

    def _initWidget(self):
        """初始化部件"""
        self.typeSegmentedWidget.addItem("stringGeneral", "字符串一般检索")
        self.typeSegmentedWidget.addItem("specificCondition", "特定条件检索")
        self.typeSegmentedWidget.addItem("wordCombination", "词语搭配检索")
        self.typeSegmentedWidget.addItem("wrongSentence", "错句检索")
        self.typeSegmentedWidget.setCurrentItem("stringGeneral")
        self.typeSegmentedWidget.currentItemChanged.connect(self._shiftWidget)

        # 设置子widget的大小策略
        for widget in [
            self.stringGeneralSearchWidget,
            self.specialConditionSearchWidget,
            self.wordCombinationSearchWidget,
            self.wrongCombinationSearchWidget,
        ]:
            widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # 添加到堆叠窗口
        self.stackedWidget.addWidget(self.stringGeneralSearchWidget)
        self.stackedWidget.addWidget(self.specialConditionSearchWidget)
        self.stackedWidget.addWidget(self.wordCombinationSearchWidget)
        self.stackedWidget.addWidget(self.wrongCombinationSearchWidget)

        self.runTaskButton.setFixedSize(130, 40)
        self.runTaskButton.clicked.connect(self._runTask)
        setFont(self.runTaskButton, 17, QFont.Weight.Normal)

        self._initLayout()

    def _initLayout(self):
        """初始化布局"""
        # 创建主垂直布局
        vBoxLayout = QVBoxLayout(self)
        vBoxLayout.setContentsMargins(10, 5, 10, 25)

        # 创建滚动区域
        scrollArea = ScrollArea()
        scrollArea.setStyleSheet("background-color: transparent;border: none;")
        scrollArea.setWidgetResizable(True)
        scrollArea.setFrameShape(QFrame.NoFrame)
        scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # 创建滚动区域内容widget
        scrollWidget = QWidget()
        scrollWidget.setStyleSheet("background-color: transparent;border: none;")
        scrollLayout = QVBoxLayout(scrollWidget)
        scrollLayout.setContentsMargins(0, 0, 0, 0)
        scrollLayout.setSpacing(10)

        # 添加到滚动布局
        scrollLayout.addWidget(self.typeSegmentedWidget, 0, Qt.AlignmentFlag.AlignTop)
        scrollLayout.addWidget(self.stackedWidget, 0, Qt.AlignmentFlag.AlignTop)
        scrollLayout.addWidget(
            self.advancedSettingCardWidget, 0, Qt.AlignmentFlag.AlignTop
        )
        scrollLayout.addStretch(1)

        # 设置滚动区域
        scrollArea.setWidget(scrollWidget)

        # 添加到主布局
        vBoxLayout.addWidget(scrollArea, 1)

        # 按钮行
        btnRow = QHBoxLayout()
        btnRow.addStretch(1)
        btnRow.addWidget(self.runTaskButton)
        vBoxLayout.addLayout(btnRow)

    def _shiftWidget(self, index):
        """切换搜索组件"""
        widgetDict = {
            "stringGeneral": 0,
            "specificCondition": 1,
            "wordCombination": 2,
            "wrongSentence": 3,
        }

        if index in widgetDict:
            self.stackedWidget.setCurrentIndex(widgetDict[index])
            self.stackedWidget.updateGeometry()
            self.updateGeometry()

    def _runTask(self):
        """执行任务"""
        logger.info("[HSK] 用户点击申请任务按钮")

        # 获取当前搜索组件
        currentWidget = self.stackedWidget.currentWidget()
        baseDict = currentWidget.returnValues()
        logger.debug(f"[HSK] 搜索参数: {baseDict}")

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
        self.runTaskButton.setEnabled(False)
        self.runTaskButton.setText("处理中...")

        try:
            # 发送下载信号
            from PySide6.QtCore import QTimer

            QTimer.singleShot(1000, lambda: signalBus.hskDownloadSignal.emit(infoDict))

            infoBar = InfoBar(
                icon=InfoBarIcon.INFORMATION,
                title="提交成功",
                content="任务已提交至下载队列",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.BOTTOM,
                duration=2000,
                parent=self,
            )
            viewButton = PushButton("查看")
            viewButton.clicked.connect(lambda: signalBus.jumpToTaskSignal.emit())
            infoBar.addWidget(viewButton)
            infoBar.show()
        finally:
            # 恢复按钮状态
            self.runTaskButton.setEnabled(True)
            self.runTaskButton.setText("申请任务")

    def _validateInput(self, baseDict):
        """验证输入条件，返回 (isValid, errorType)"""
        # 根据不同的搜索类型验证输入
        if "keyword" in baseDict:
            # 字符串一般检索和词语搭配检索
            if not baseDict.get("keyword"):
                return (False, "keyword")
        elif (
            "start_word" in baseDict
            or "pre_word" in baseDict
            or "post_word" in baseDict
            or "end_word" in baseDict
        ):
            # 特定条件检索
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
            # 错误句检索
            pass
        else:
            # 其他类型的检索
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
