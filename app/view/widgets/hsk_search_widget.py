# coding: utf-8

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from qfluentwidgets import (
    CheckBox,
    ComboBox,
    CompactSpinBox,
    GroupHeaderCardWidget,
    LineEdit,
    ScrollArea,
)


class StringGeneralSearchWidget(GroupHeaderCardWidget):

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
            QIcon(":app/icons/Font.svg"),
            "前词",
            "输入前词",
            self.previousWords,
        )
        self.addGroup(
            QIcon(":app/icons/Link.svg"),
            "距离",
            "输入距离",
            self.compactSpinBox,
        )
        self.addGroup(
            QIcon(":app/icons/Font.svg"),
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
            QIcon(":app/icons/Font.svg"),
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("错误句检索")
        self.setBorderRadius(8)

        self.sentencePatternComBobox = ComboBox(self)
        self.sentencePatternComBobox.addItems(wrongSentencePattern.keys())
        self.sentencePatternComBobox.setFixedWidth(200)

        self.addGroup(
            QIcon(":app/icons/Font.svg"),
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

        self.nationality = ComboBox(self)
        self.nationality.setFixedWidth(200)

        # 生成只包含国家名称的列表
        country_list = list(hskCountryDict.keys())
        self.nationality.addItems(country_list)

        self.addGroup(
            QIcon(":app/icons/Essay.svg"),
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
            QIcon(":app/icons/Nation.svg"),
            "国籍",
            "选择国籍",
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
            if self.nationality.currentText() != "不限":
                dicts["nation"] = hskCountryDict[self.nationality.currentText()]
            return dicts
        else:
            return {}


# ─── 批量下载组件 ───────────────────────────────────────────────

from loguru import logger
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon,
    PrimaryPushButton,
    PrimaryToolButton,
    ProgressBar,
    StrongBodyLabel,
)
from qfluentwidgetspro import SlideAniStackedWidget


class HSKBatchItemCard(CardWidget):
    """批量下载列表中的单个任务卡片"""

    removed = Signal(int)  # 发送索引，通知父组件移除

    def __init__(self, index: int, info_dict: dict, parent=None):
        super().__init__(parent)
        self.index = index
        self.info_dict = info_dict
        logger.info(
            f"[BatchItem.__init__] index={index}, info_dict.keys={list(info_dict.keys())}, payload={info_dict.get('payload')}"
        )
        self._build_ui()

    def _build_ui(self):
        self.setFixedHeight(52)
        self.hBox = QHBoxLayout(self)
        self.hBox.setContentsMargins(12, 0, 8, 0)
        self.hBox.setSpacing(10)

        # 序号标签
        self.indexLabel = BodyLabel(f"#{self.index + 1}", self)
        self.indexLabel.setFixedWidth(30)
        self.hBox.addWidget(
            self.indexLabel,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        # 关键词信息（兼容 HSK 和全球中介语的所有搜索字段）
        payload = self.info_dict.get("payload", {})
        logger.info(f"[BatchItem._build_ui] payload={payload}, type={type(payload)}")

        # 依次尝试各字段，优先级从高到低
        keyword = None
        for _field in (
            "keystr",
            "shou",
            "kaishi",
            "jieshu",
            "wei",
            "mothertongue",
            "authornationality",
            "keyword",
            "wrong_type",
            "start_word",
        ):
            val = payload.get(_field)
            if val and str(val).strip():
                keyword = str(val)
                logger.info(f"[BatchItem] 匹配字段 {_field}={keyword}")
                break

        if not keyword:
            keyword = "未填写关键字"
            logger.info("[BatchItem] 无关键字，使用 fallback")

        self.keywordLabel = StrongBodyLabel(keyword, self)
        logger.info(f"[BatchItem] keywordLabel 显示文本: {keyword}")
        self.keywordLabel.setToolTip(str(payload))
        self.hBox.addWidget(
            self.keywordLabel,
            1,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        # 高级设置标签
        advance = self.info_dict.get("advance", {})
        if advance:
            self.hBox.addWidget(
                BodyLabel(f"含高级筛选", self),
                0,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )

        # 移除按钮
        self.removeBtn = PrimaryToolButton(FluentIcon.DELETE, self)
        self.removeBtn.setFixedSize(28, 28)
        self.removeBtn.setToolTip("移除")
        self.removeBtn.clicked.connect(lambda: self.removed.emit(self.index))
        self.hBox.addWidget(
            self.removeBtn,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )


class HSKBatchDownloadWidget(QWidget):
    """
    批量下载管理组件：
    - 显示已添加的搜索条件列表
    - 显示选中数量统计
    - 提供批量下载按钮
    """

    batchDownloadRequest = Signal(list)  # 发送批量下载请求
    countChanged = Signal(int)  # 任务数量变化信号，通知界面更新按钮状态

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []  # 存储 {index, info_dict}
        self._next_index = 0
        self._setup_ui()

    def _setup_ui(self):
        self.vBox = QVBoxLayout(self)
        self.vBox.setContentsMargins(0, 0, 0, 0)
        self.vBox.setSpacing(6)

        # 头部：标题 + 数量 + 全部清除
        header = QHBoxLayout()
        header.setSpacing(8)

        self.titleLabel = StrongBodyLabel("批量下载列表", self)
        self.countLabel = BodyLabel("(0 个任务)", self)
        self.countLabel.setStyleSheet("color: #888; font-size: 12px;")

        from qfluentwidgets import ToolButton

        self.clearAllBtn = ToolButton(FluentIcon.DELETE, self)
        self.clearAllBtn.setToolTip("清空全部")
        self.clearAllBtn.clicked.connect(self._clear_all)

        header.addWidget(self.titleLabel)
        header.addWidget(self.countLabel)
        header.addStretch(1)
        header.addWidget(self.clearAllBtn)

        self.vBox.addLayout(header)

        # 列表区域（可滚动）
        scroll = ScrollArea(self)
        scroll.setStyleSheet("background:transparent; border:none;")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 初始至少显示3个任务项（每项52px + 间距6px * 2 ≈ 180px），超出后滚动
        scroll.setMinimumHeight(200)

        self.listWidget = QWidget()
        self.listVBox = QVBoxLayout(self.listWidget)
        self.listVBox.setContentsMargins(0, 0, 0, 0)
        self.listVBox.setSpacing(6)
        self.listVBox.addStretch(1)

        scroll.setWidget(self.listWidget)
        self.vBox.addWidget(scroll, 1)

        # 底部操作栏（下载按钮已移至 HskInterface 按钮行，此处仅留空白占位）
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.setContentsMargins(0, 4, 0, 0)  # 仅留少量上边距
        self.vBox.addLayout(footer)

    def add_item(self, info_dict: dict, advance_dict: dict = None):
        """添加一个搜索条件到批量列表（兼容 HSK 扁平结构和 Global 嵌套结构）"""
        # 如果 info_dict 已包含 "payload" 键（Global 传入），直接使用
        # 否则（H SK 传入）提取除 url 外的所有字段作为 payload
        if "payload" in info_dict:
            item_info = {
                "url": info_dict.get("url"),
                "payload": info_dict["payload"],
            }
        else:
            item_info = {
                "url": info_dict.get("url"),
                "payload": {k: v for k, v in info_dict.items() if k != "url"},
            }
        if advance_dict:
            item_info["advance"] = advance_dict
            item_info["payload"].update(advance_dict)

        # 检查是否已存在相同条件
        for existing in self.items:
            if existing["info_dict"]["url"] == item_info["url"] and existing[
                "info_dict"
            ].get("payload") == item_info.get("payload"):
                logger.debug("[Batch] 跳过重复条件")
                return

        card = HSKBatchItemCard(self._next_index, item_info, self.listWidget)
        logger.info(f"[Batch] 创建卡片, item_info.payload={item_info.get('payload')}")
        card.removed.connect(self._remove_item)
        self.listVBox.insertWidget(len(self.items), card)

        self.items.append({"index": self._next_index, "info_dict": item_info})
        self._next_index += 1
        self._update_count()

        logger.info(f"[Batch] 添加任务 #{self._next_index}: {item_info.get('payload')}")

    def _update_count(self):
        count = len(self.items)
        self.countLabel.setText(f"({count} 个任务)")
        self.countChanged.emit(count)

    def _remove_item(self, index: int):
        """移除指定索引的任务卡片"""
        # 找到并移除卡片
        for i, item in enumerate(self.items):
            if item["index"] == index:
                self.items.pop(i)
                break

        # 重建列表（重新编号）
        self._rebuild_list()
        self._update_count()

    def _rebuild_list(self):
        """重建卡片列表，更新显示序号，保持 _next_index 全局递增"""
        # 清除现有卡片
        while self.listVBox.count() > 1:
            child = self.listVBox.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # 重建：保持 _next_index 不变（它代表全局下一个序号）
        # 只更新 item["index"] 和显示序号标签
        for i, item in enumerate(self.items):
            card = HSKBatchItemCard(i, item["info_dict"], self.listWidget)
            card.removed.connect(self._remove_item)
            self.listVBox.insertWidget(i, card)
            item["index"] = i

        # 新增任务时序号从 len(items) 继续递增
        self._next_index = len(self.items)

    def _clear_all(self):
        """清空所有任务"""
        while self.items:
            self.items.pop()
        while self.listVBox.count() > 1:
            child = self.listVBox.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._next_index = 0
        self._update_count()
        logger.info("[Batch] 清空批量下载列表")

    def _download_all(self):
        """触发批量下载"""
        if not self.items:
            return

        info_list = [item["info_dict"] for item in self.items]
        logger.info(f"[Batch] 开始批量下载，共 {len(info_list)} 个任务")
        self.batchDownloadRequest.emit(info_list)

    def clear(self):
        """清空列表（外部调用）"""
        self._clear_all()
