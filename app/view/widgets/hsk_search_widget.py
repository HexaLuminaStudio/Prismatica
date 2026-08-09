# coding: utf-8
"""
HSK搜索组件模块
包含各种HSK语料库检索组件
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import (
    CheckBox,
    ComboBox,
    CompactSpinBox,
    GroupHeaderCardWidget,
    LineEdit,
    ScrollArea,
)


class HskSearchContainer(QWidget):
    """HSK搜索容器组件，包含滚动布局"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._initLayout()

    def _initLayout(self):
        """初始化滚动布局"""
        vBoxLayout = QVBoxLayout(self)
        vBoxLayout.setContentsMargins(0, 0, 0, 0)
        vBoxLayout.setSpacing(0)

        # 创建滚动区域
        scrollArea = ScrollArea(self)
        scrollArea.setStyleSheet("background:transparent;border:none;")
        scrollArea.setWidgetResizable(True)
        scrollArea.setFrameShape(QFrame.NoFrame)
        scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # 创建滚动内容widget
        scrollWidget = QWidget()
        scrollLayout = QVBoxLayout(scrollWidget)
        scrollLayout.setContentsMargins(0, 0, 0, 0)
        scrollLayout.setSpacing(0)

        # 搜索组件容器
        self.searchWidget = QWidget()
        self.searchLayout = QVBoxLayout(self.searchWidget)
        self.searchLayout.setContentsMargins(0, 0, 0, 0)
        self.searchLayout.setSpacing(10)

        scrollLayout.addWidget(self.searchWidget)
        scrollArea.setWidget(scrollWidget)
        vBoxLayout.addWidget(scrollArea)

    def addSearchWidget(self, widget):
        """添加搜索组件"""
        self.searchLayout.addWidget(widget)

    def getSearchWidgets(self):
        """获取所有搜索组件"""
        return [
            self.searchLayout.itemAt(i).widget()
            for i in range(self.searchLayout.count())
        ]


class StringGeneralSearchWidget(GroupHeaderCardWidget):
    """字符串一般检索组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("字符串一般检索")
        self.setBorderRadius(8)

        self.keyWord = LineEdit(self)
        self.keyWord.setPlaceholderText("输入关键字")
        self.keyWord.setFixedWidth(200)

        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "关键字",
            "输入关键字搜索",
            self.keyWord,
        )

    def returnValues(self):
        return {
            "url": "https://hsk.blcu.edu.cn/api/v1/sentence/search/keyword",
            "keyword": self.keyWord.text(),
        }


class SpecificConditionSearchWidget(GroupHeaderCardWidget):
    """特定条件检索组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("特定条件检索")
        self.setBorderRadius(8)

        self.initialString = LineEdit(self)
        self.initialString.setPlaceholderText("字符串")
        self.initialString.setFixedWidth(200)

        self.previousWords = LineEdit(self)
        self.previousWords.setPlaceholderText("前词")
        self.previousWords.setFixedWidth(200)

        self.compactSpinBox = CompactSpinBox(self)
        self.compactSpinBox.setRange(0, 100)
        self.compactSpinBox.setValue(0)
        self.compactSpinBox.setFixedWidth(200)

        self.postWord = LineEdit(self)
        self.postWord.setPlaceholderText("后词")
        self.postWord.setFixedWidth(200)

        self.tailString = LineEdit(self)
        self.tailString.setPlaceholderText("尾字符串")
        self.tailString.setFixedWidth(200)

        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "首字符串",
            "输入首字符串",
            self.initialString,
        )
        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "前词",
            "输入前词",
            self.previousWords,
        )
        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "距离",
            "输入距离",
            self.compactSpinBox,
        )
        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "后词",
            "输入后词",
            self.postWord,
        )
        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "尾字符串",
            "输入尾字符串",
            self.tailString,
        )

    def returnValues(self):
        return {
            "url": "https://hsk.blcu.edu.cn/api/v1/sentence/search/terms",
            "start_word": self.initialString.text(),
            "pre_word": self.previousWords.text(),
            "word_distance": self.compactSpinBox.value(),
            "post_word": self.postWord.text(),
            "end_word": self.tailString.text(),
        }


from app.core.utils import syntacticRelationshipList


class WordCombinationSearchWidget(GroupHeaderCardWidget):
    """词语搭配检索组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("词语搭配检索")
        self.setBorderRadius(8)

        self.keyWord = LineEdit(self)
        self.keyWord.setPlaceholderText("输入关键字")
        self.keyWord.setFixedWidth(200)

        self.relationshipCombobox = ComboBox(self)
        self.relationshipCombobox.addItems(syntacticRelationshipList)
        self.relationshipCombobox.setFixedWidth(200)

        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "关键字或词",
            "输入关键字或词搜索",
            self.keyWord,
        )
        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "句法结构",
            "选择句法结构",
            self.relationshipCombobox,
        )

    def returnValues(self):
        return {
            "url": "https://hsk.blcu.edu.cn/api/v1/sentence/search/pairs",
            "keyword": self.keyWord.text(),
            "depType": self.relationshipCombobox.currentText(),
        }


from app.core.utils import wrongSentencePattern


class WrongSentenceSearchWidget(GroupHeaderCardWidget):
    """错误句检索组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("错误句检索")
        self.setBorderRadius(8)

        self.sentencePatternComBobox = ComboBox(self)
        self.sentencePatternComBobox.addItems(wrongSentencePattern.keys())
        self.sentencePatternComBobox.setFixedWidth(200)

        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "句式",
            "选择句式",
            self.sentencePatternComBobox,
        )

    def returnValues(self):
        return {
            "url": "https://hsk.blcu.edu.cn/api/v1/sentence/search/wrongju",
            "wrong_type": wrongSentencePattern[
                self.sentencePatternComBobox.currentText()
            ],
        }


from app.core.utils import hskEssayList, hskCountryDict


class AdvancedSettingCardWidget(GroupHeaderCardWidget):
    """高级设置组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("检索高级设置")

        self.enableCheckBox = CheckBox("使用高级筛选条件", self)
        self.enableCheckBox.setChecked(False)
        self.enableCheckBox.stateChanged.connect(self._enabelAdvancedSetting)
        self.headerLayout.addWidget(self.enableCheckBox, 0, Qt.AlignmentFlag.AlignRight)

        self.essayTitle = ComboBox(self)
        self.essayTitle.setFixedWidth(200)

        self.essayTitle.addItems(hskEssayList)

        self.certificateLevel = ComboBox(self)
        self.certificateLevel.setFixedWidth(200)
        self.certificateLevel.addItems(["不限", "A", "B", "C", "未参加"])

        self.nationality = LineEdit(self)
        self.nationality.setPlaceholderText("输入国家中文名,如「日本」")
        self.nationality.setFixedWidth(200)

        self.addGroup(
            QIcon(":app/icons/EssayTitle.svg"),
            "作文题目",
            "选择作文题目",
            self.essayTitle,
        )
        self.addGroup(
            QIcon(":app/icons/Level.svg"),
            "作文等级",
            "选择作文等级",
            self.certificateLevel,
        )
        self.addGroup(
            QIcon(":app/icons/Public.svg"),
            "国籍",
            "输入国籍(精确匹配国家中文名,留空表示不限)",
            self.nationality,
        )
        for i in range(3):
            self.groupWidgets[i].setEnabled(False)

    def _enabelAdvancedSetting(self, state):
        if state == 2:
            for i in range(3):
                self.groupWidgets[i].setEnabled(True)
        else:
            for i in range(3):
                self.groupWidgets[i].setEnabled(False)

    def returnValues(self):
        if self.enableCheckBox.isChecked():
            dicts = {}
            if self.essayTitle.currentText() != "不限":
                dicts["title"] = self.essayTitle.currentText()
            if self.certificateLevel.currentText() != "不限":
                dicts["level"] = self.certificateLevel.currentText()
            nationName = self.nationality.text().strip()
            if nationName and nationName != "不限":
                # 精确匹配国家中文名,未命中视为不限
                nationCode = hskCountryDict.get(nationName, "")
                if nationCode:
                    dicts["nation"] = nationCode
            return dicts
        else:
            return {}
