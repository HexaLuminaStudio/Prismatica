# coding: utf-8
"""
Global搜索组件模块
包含各种Global中介语语料库检索组件
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QVBoxLayout, QSizePolicy, QWidget
from qfluentwidgets import (
    CheckBox,
    ComboBox,
    CompactSpinBox,
    GroupHeaderCardWidget,
    LineEdit,
    ScrollArea,
)

from app.core.utils.constant import tableName, corpType


class _InputWidthPolicyMixin:
    """用于搜索卡片内输入控件的宽度适配."""

    def _setInputWidthPolicy(self, widget: QWidget) -> None:
        widget.setMinimumWidth(160)
        widget.setMaximumWidth(320)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        widget.setMinimumHeight(32)


class GlobalSearchContainer(QWidget):
    """Global搜索容器组件，包含滚动布局"""

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

class StringGeneralSearchWidget(_InputWidthPolicyMixin, GroupHeaderCardWidget):
    """字符串一般检索组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("关键词检索")
        self.setBorderRadius(8)

        self.keyWord = LineEdit(self)
        self.keyWord.setPlaceholderText("输入关键词或短语，例如「学习」")
        self._setInputWidthPolicy(self.keyWord)

        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "关键字",
            "输入关键字搜索",
            self.keyWord,
        )

    def returnValues(self):
        return {
            "url": "https://qqk.blcu.edu.cn/corp/index/getzfcsample",
            "keystr": self.keyWord.text(),
            "tablename": "ylk_zi",
            "txt": "",
        }


class SpecificConditionSearchWidget(_InputWidthPolicyMixin, GroupHeaderCardWidget):
    """特定条件检索组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("上下文条件")
        self.setBorderRadius(8)

        self.typeCombobox = ComboBox(self)
        self.typeCombobox.addItems(tableName.keys())
        self._setInputWidthPolicy(self.typeCombobox)

        self.initialString = LineEdit(self)
        self.initialString.setPlaceholderText("字符串")
        self._setInputWidthPolicy(self.initialString)

        self.previousWords = LineEdit(self)
        self.previousWords.setPlaceholderText("前词")
        self._setInputWidthPolicy(self.previousWords)

        self.compactSpinBox = CompactSpinBox(self)
        self.compactSpinBox.setRange(0, 100)
        self.compactSpinBox.setValue(0)
        self._setInputWidthPolicy(self.compactSpinBox)

        self.postWord = LineEdit(self)
        self.postWord.setPlaceholderText("后词")
        self._setInputWidthPolicy(self.postWord)

        self.tailString = LineEdit(self)
        self.tailString.setPlaceholderText("尾字符串")
        self._setInputWidthPolicy(self.tailString)

        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "语料类型",
            "选择语料类型",
            self.typeCombobox,
        )
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
            "url": "https://qqk.blcu.edu.cn/corp/index/getzfc",
            "shou": self.initialString.text(),
            "kaishi": self.previousWords.text(),
            "num": self.compactSpinBox.value(),
            "jieshu": self.postWord.text(),
            "wei": self.tailString.text(),
            "tablename": tableName[self.typeCombobox.currentText()],
        }


class WordCombinationSearchWidget(_InputWidthPolicyMixin, GroupHeaderCardWidget):
    """词语搭配检索组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("词语搭配")
        self.setBorderRadius(8)

        self.keyWord = LineEdit(self)
        self.keyWord.setPlaceholderText("输入关键词或词语")
        self._setInputWidthPolicy(self.keyWord)

        self.sortTypeCombobox = ComboBox(self)
        self.sortTypeCombobox.addItems(["左", "右"])
        self._setInputWidthPolicy(self.sortTypeCombobox)

        self.compactSpinBox = CompactSpinBox(self)
        self.compactSpinBox.setRange(0, 100)
        self.compactSpinBox.setValue(0)
        self._setInputWidthPolicy(self.compactSpinBox)

        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "关键字或词",
            "输入关键字或词搜索",
            self.keyWord,
        )
        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "排序方向",
            "选择排序方向",
            self.sortTypeCombobox,
        )
        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "检索后字符数",
            "输入检索后字符数",
            self.compactSpinBox,
        )

    def returnValues(self):
        return {
            "url": "https://qqk.blcu.edu.cn/corp/index/get_ciyudapei",
            "keystr": self.keyWord.text(),
            "orderstr": {"右": "r", "左": "l"}[self.sortTypeCombobox.currentText()],
            "showlenght": str(self.compactSpinBox.value()),
            "tag": "cx",
            "txt": "",
        }


class SpeechPartSearchWidget(_InputWidthPolicyMixin, GroupHeaderCardWidget):
    """按词性检索组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("按词性检索")
        self.setBorderRadius(8)

        self.speechPartLineEdit = LineEdit(self)
        self.speechPartLineEdit.setPlaceholderText("输入词性代码，例如 n、v、a")
        self._setInputWidthPolicy(self.speechPartLineEdit)

        self.addGroup(
            QIcon(":app/icons/Write.svg"),
            "词性代码",
            "输入词性代码",
            self.speechPartLineEdit,
        )

    def returnValues(self):
        return {
            "url": "https://qqk.blcu.edu.cn/corp/index/getcx",
            "keystr": self.speechPartLineEdit.text(),
            "tablename": "ylk_zi",
            "tag": "cx",
        }


class GlobalAdvancedSettingCardWidget(_InputWidthPolicyMixin, GroupHeaderCardWidget):
    """Global高级设置组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("高级筛选")

        self.enableCheckBox = CheckBox("启用高级筛选", self)
        self.enableCheckBox.setChecked(False)
        self.enableCheckBox.stateChanged.connect(self._enabelAdvancedSetting)
        self.headerLayout.addWidget(self.enableCheckBox, 0, Qt.AlignmentFlag.AlignRight)

        self.corpTypeCombobox = ComboBox(self)
        self.corpTypeCombobox.addItems(corpType.keys())
        self._setInputWidthPolicy(self.corpTypeCombobox)

        self.nativeLanguage = LineEdit(self)
        self.nativeLanguage.setPlaceholderText("输入母语")
        self._setInputWidthPolicy(self.nativeLanguage)

        self.nationality = LineEdit(self)
        self.nationality.setPlaceholderText("输入国籍")
        self._setInputWidthPolicy(self.nationality)

        self.hskGrade = ComboBox(self)
        self._setInputWidthPolicy(self.hskGrade)
        self.hskGrade.addItems(
            [
                "不限",
                "未参加",
                "未得证书",
                "一级",
                "二级",
                "三级",
                "四级",
                "五级",
                "六级",
            ]
        )

        self.chineseLevel = ComboBox(self)
        self._setInputWidthPolicy(self.chineseLevel)
        self.chineseLevel.addItems(["不限", "初级", "中级", "高级"])

        self.addGroup(
            QIcon(":app/icons/Thread.svg"),
            "中介语熟语料类型",
            "选择类型",
            self.corpTypeCombobox,
        )
        self.addGroup(
            QIcon(":app/icons/EssayTitle.svg"),
            "母语",
            "填写母语",
            self.nativeLanguage,
        )
        self.addGroup(
            QIcon(":app/icons/Public.svg"),
            "国籍",
            "填写国籍",
            self.nationality,
        )
        self.addGroup(
            QIcon(":app/icons/Level.svg"),
            "HSK等级",
            "选择HSK等级",
            self.hskGrade,
        )
        self.addGroup(
            QIcon(":app/icons/Level.svg"),
            "汉语水平",
            "选择汉语水平",
            self.chineseLevel,
        )

        for i in range(5):
            self.groupWidgets[i].setEnabled(False)
            self.groupWidgets[i].setVisible(False)

    def _enabelAdvancedSetting(self, state):
        isEnabled = state == 2
        for i in range(5):
            self.groupWidgets[i].setEnabled(isEnabled)
            self.groupWidgets[i].setVisible(isEnabled)

    def returnValues(self):
        if self.enableCheckBox.isChecked():
            dicts = {}
            if self.nativeLanguage.text():
                dicts["mothertongue"] = self.nativeLanguage.text()
            if self.hskGrade.currentText() != "不限":
                dicts["shkgrade"] = self.hskGrade.currentText()
            if self.chineseLevel.currentText() != "不限":
                dicts["ext1"] = self.chineseLevel.currentText()
            if self.nationality.text():
                dicts["authornationality"] = self.nationality.text()
            dicts["ft"] = corpType[self.corpTypeCombobox.currentText()]
            return dicts
        else:
            return {}
