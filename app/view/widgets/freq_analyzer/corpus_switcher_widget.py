# coding: utf-8
"""语料库切换器 UI 组件

需求文档:
    test/6D-CorpusClient_需求文档_v3.md §2.3.1.2 语料库管理

功能:
    - 下拉选择当前活动语料库
    - 「新建」:弹窗输入名称 → 自动注册并切换
    - 「删除」:删除当前语料库(默认语料库不可删)
    - 「打开上次」:一键回到上次使用的语料库
    - 「选择路径...」:导入/绑定已有的 db 文件(高级)
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon,
    LineEdit,
    MessageBox,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
)

from app.view.widgets.freq_analyzer.corpus_manager import CorpusInfo, CorpusManager
from app.view.widgets.freq_analyzer.ui_helpers import _showInfoBar

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from app.core.utils import logger
from app.view.widgets.prismatica_theme import setThemeRole


# ---------------------------------------------------------------------------
# 新建语料库弹窗
# ---------------------------------------------------------------------------
class NewCorpusDialog(MessageBoxBase):
    """新建语料库对话框:输入名称 + 可选描述"""

    def __init__(
        self,
        existingNames: List[str],
        defaultPath: str,
        parent=None,
    ):
        super().__init__(parent)
        self._existingNames = set(existingNames)
        self._defaultPath = defaultPath

        titleLabel = SubtitleLabel("新建语料库", self)
        titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.viewLayout.addWidget(titleLabel, 0, Qt.AlignmentFlag.AlignCenter)
        self.viewLayout.addSpacing(8)

        # 名称
        nameWrap = QVBoxLayout()
        nameLabel = BodyLabel("语料库名称 *", self)
        self.nameEdit = LineEdit(self)
        self.nameEdit.setPlaceholderText(
            "例如:HSK-A级 / 学术汉语 / 新闻语料 (1-32 字符)"
        )
        self.nameErrLabel = CaptionLabel("", self)
        setThemeRole(self.nameErrLabel, "danger", "font-size: 11px;")
        self.nameErrLabel.setVisible(False)
        nameWrap.addWidget(nameLabel)
        nameWrap.addWidget(self.nameEdit)
        nameWrap.addWidget(self.nameErrLabel)
        self.viewLayout.addLayout(nameWrap)
        self.viewLayout.addSpacing(8)

        # 描述
        descWrap = QVBoxLayout()
        descLabel = BodyLabel("描述(可选)", self)
        self.descEdit = LineEdit(self)
        self.descEdit.setPlaceholderText("例如:北京语言大学 HSK 动态作文语料 A 级")
        descWrap.addWidget(descLabel)
        descWrap.addWidget(self.descEdit)
        self.viewLayout.addLayout(descWrap)
        self.viewLayout.addSpacing(8)

        # 保存路径提示
        pathHint = CaptionLabel(f"数据库文件将保存至: {defaultPath}", self)
        setThemeRole(pathHint, "muted", "font-size: 11px;")
        pathHint.setWordWrap(True)
        self.viewLayout.addWidget(pathHint)
        self.viewLayout.addSpacing(4)

        # 按钮
        self.yesButton.setText("创建")
        self.cancelButton.setText("取消")
        self.widget.setFixedWidth(440)

    def getName(self) -> str:
        return self.nameEdit.text().strip()

    def getDescription(self) -> str:
        return self.descEdit.text().strip()

    def validate(self) -> bool:
        """qfluentwidgets.MessageBoxBase 的校验钩子

        返回 True 表示通过(允许关闭);返回 False 表示校验失败,弹窗保持打开
        并在底部显示错误信息。
        """
        name = self.getName()
        if not name:
            self.showError("名称不能为空")
            return False
        if len(name) > 32:
            self.showError("名称不能超过 32 个字符")
            return False
        if any(c in name for c in ("/", "\\", ":")):
            self.showError("名称不能包含路径分隔符")
            return False
        if name in self._existingNames:
            self.showError(f"名称已存在: {name}")
            return False
        # 校验通过:清除之前的错误提示
        self.nameErrLabel.setVisible(False)
        return True

    def showError(self, msg: str) -> None:
        self.nameErrLabel.setText(msg)
        self.nameErrLabel.setVisible(True)


# ---------------------------------------------------------------------------
# 主切换器组件
# ---------------------------------------------------------------------------
class CorpusSwitcherWidget(CardWidget):
    """语料库切换器 - 卡片 UI,放在 CorpusImportWidget 顶部

    Signals:
        activeCorpusChanged(int)  活动语料库切换(由 CorpusManager 直接发出)
    """

    def __init__(
        self,
        manager: CorpusManager,
        parent=None,
    ):
        super().__init__(parent)
        self._manager = manager
        self._buildUi()
        self._refreshUi()

        # 监听注册表变更
        self._manager.registryChanged.connect(self._refreshUi)
        self._manager.activeCorpusChanged.connect(lambda _id: self._refreshUi())
        self._manager.statsUpdated.connect(lambda _id: self._refreshUi())

    # ---------------- UI ----------------
    def _buildUi(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        title = StrongBodyLabel("语料库", self)
        layout.addWidget(title)

        row1 = QHBoxLayout()
        row1.setSpacing(8)

        row1.addWidget(BodyLabel("当前:", self))
        self.corpusCombo = ComboBox(self)
        self.corpusCombo.setMinimumWidth(220)
        self.corpusCombo.currentIndexChanged.connect(self._onComboChanged)
        row1.addWidget(self.corpusCombo, 1)

        self.newBtn = PushButton("新建", self)
        self.newBtn.setIcon(FluentIcon.ADD)
        self.newBtn.clicked.connect(self._onNewClicked)
        row1.addWidget(self.newBtn)

        self.deleteBtn = PushButton("删除", self)
        self.deleteBtn.setIcon(FluentIcon.DELETE)
        self.deleteBtn.clicked.connect(self._onDeleteClicked)
        row1.addWidget(self.deleteBtn)

        self.lastBtn = PushButton("打开上次", self)
        self.lastBtn.setIcon(FluentIcon.SYNC)
        self.lastBtn.clicked.connect(self._onActivateLastClicked)
        row1.addWidget(self.lastBtn)

        self.importBtn = PushButton("绑定外部库...", self)
        self.importBtn.setIcon(FluentIcon.FOLDER)
        self.importBtn.clicked.connect(self._onImportExternalClicked)
        row1.addWidget(self.importBtn)

        layout.addLayout(row1)

        self.infoLabel = CaptionLabel("", self)
        setThemeRole(self.infoLabel, "muted", "font-size: 11px;")
        self.infoLabel.setWordWrap(True)
        layout.addWidget(self.infoLabel)

    # ---------------- 数据刷新 ----------------
    def _refreshUi(self):
        items: List[CorpusInfo] = self._manager.listAll()
        activeId = self._manager._activeId
        lastId = self._manager._lastId

        # 重新填充下拉框
        self.corpusCombo.blockSignals(True)
        self.corpusCombo.clear()
        idxActive = 0
        for i, info in enumerate(items):
            stats = self._quickStats(info.dbPath)
            label = f"{info.name}  ·  {stats}  ({info.description or '无描述'})"
            self.corpusCombo.addItem(label, userData=info.id)
            if info.id == activeId:
                idxActive = i
        if items:
            self.corpusCombo.setCurrentIndex(idxActive)
        self.corpusCombo.blockSignals(False)

        # 按钮状态
        active = self._manager.activeCorpus()
        self.deleteBtn.setEnabled(active is not None and active.name != "default")
        self.lastBtn.setEnabled(lastId > 0 and lastId != activeId)

        # 摘要
        if active is None:
            self.infoLabel.setText("尚未选择语料库")
        else:
            stats = self._quickStats(active.dbPath)
            lastName = ""
            if lastId and lastId != activeId:
                last = self._manager.registry.getById(lastId)
                if last:
                    lastName = f"  ·  上次: {last.name}"
            self.infoLabel.setText(
                f"当前: {active.name}  ·  {stats}  ·  路径: {active.dbPath}{lastName}"
            )

    @staticmethod
    def _quickStats(dbPath: str) -> str:
        """从 db 文件快速读取 (file_count, total_chars)"""
        if not dbPath or not os.path.exists(dbPath):
            return "0 个文件 / 0 字符"
        try:
            import sqlite3

            conn = sqlite3.connect(dbPath)
            try:
                cur = conn.execute("SELECT COUNT(*) FROM documents")
                files = int(cur.fetchone()[0] or 0)
                cur = conn.execute("SELECT COALESCE(SUM(char_count), 0) FROM documents")
                chars = int(cur.fetchone()[0] or 0)
                return f"{files} 个文件 / {chars:,} 字符"
            finally:
                conn.close()
        except Exception:
            return "0 个文件 / 0 字符"

    # ---------------- 事件 ----------------
    def _onComboChanged(self, idx: int):
        if idx < 0:
            return
        cid = self.corpusCombo.itemData(idx)
        if cid is None:
            return
        try:
            if cid != self._manager._activeId:
                self._manager.setActive(int(cid))
        except Exception as e:
            logger.error(f"[CorpusSwitcher] 切换失败: {e}")
            _showInfoBar("error", "切换失败", str(e), self, duration=3000)
            self._refreshUi()

    def _onNewClicked(self):
        existing = [info.name for info in self._manager.listAll()]
        from app.view.widgets.freq_analyzer.corpus_manager import CORPORA_DIR

        dlg = NewCorpusDialog(
            existingNames=existing,
            defaultPath=str(CORPORA_DIR / "<新名称>.db"),
            parent=self.window(),
        )
        if not dlg.exec():
            return
        name = dlg.getName()
        desc = dlg.getDescription()
        try:
            self._manager.createCorpus(name=name, description=desc)
            _showInfoBar(
                "success",
                "已创建",
                f"语料库「{name}」已创建并切换",
                self,
                duration=2500,
            )
        except Exception as e:
            logger.error(f"[CorpusSwitcher] 新建失败: {e}")
            _showInfoBar("error", "创建失败", str(e), self, duration=3000)

    def _onDeleteClicked(self):
        active = self._manager.activeCorpus()
        if active is None:
            return
        if active.name == "default":
            _showInfoBar(
                "warning",
                "提示",
                "默认语料库不可删除(可使用「清空」按钮移除文件)",
                self,
                duration=2500,
            )
            return

        # 二级确认(符合 NFR-USA-003 删除前确认弹窗)
        msg = MessageBox(
            "删除语料库",
            f"确定删除「{active.name}」?\n\n"
            f"路径: {active.dbPath}\n\n"
            "请选择删除方式:",
            self.window(),
        )
        msg.yesButton.setText("仅注销(保留文件)")
        msg.cancelButton.setText("取消")
        # 加一个彻底删除按钮(替代默认 buttonGroup)
        from qfluentwidgets import PrimaryPushButton

        purgeBtn = PrimaryPushButton("彻底删除文件", msg)
        purgeBtn.clicked.connect(msg.accept)
        msg.buttonLayout.addWidget(purgeBtn)

        choice = msg.exec()
        if not choice:
            return

        # 区分 yes / purge:y
        # 由于 PrimaryPushButton 也调用 accept,这里只能用当前激活按钮文字判断
        # 但 MessageBox 默认 yesButton 走 accept,purgeBtn 也走 accept,无法区分。
        # 简化处理:第一次确认 → 仅注销;若用户希望彻底删除,需要二次确认。
        # 通过点击 yesButton 文字判断更可靠,这里采用「二次弹窗」简化:
        try:
            self._manager.deleteCorpus(active.id, deleteDbFile=False)
            _showInfoBar(
                "success",
                "已注销",
                f"「{active.name}」已从列表移除,数据库文件保留",
                self,
                duration=2500,
            )
            # 询问是否彻底删除
            followUp = MessageBox(
                "是否彻底删除文件?",
                f"数据库文件仍然存在于:\n{active.dbPath}\n\n是否一并删除(不可恢复)?",
                self.window(),
            )
            followUp.yesButton.setText("删除文件")
            followUp.cancelButton.setText("保留")
            if followUp.exec():
                try:
                    for p in (
                        active.dbPath,
                        active.dbPath + "-shm",
                        active.dbPath + "-wal",
                    ):
                        if os.path.exists(p):
                            os.remove(p)
                    _showInfoBar(
                        "success",
                        "已删除",
                        "数据库文件已彻底删除",
                        self,
                        duration=2000,
                    )
                except Exception as e:
                    _showInfoBar(
                        "error",
                        "删除失败",
                        str(e),
                        self,
                        duration=3000,
                    )
        except Exception as e:
            logger.error(f"[CorpusSwitcher] 删除失败: {e}")
            _showInfoBar("error", "删除失败", str(e), self, duration=3000)

    def _onActivateLastClicked(self):
        info = self._manager.activateLast()
        if info is None:
            _showInfoBar(
                "warning", "提示", "没有可用的「上次」语料库", self, duration=2000
            )
            return
        _showInfoBar(
            "info",
            "已切换",
            f"已切换回「{info.name}」",
            self,
            duration=2000,
        )

    def _onImportExternalClicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择要绑定的 SQLite 语料库文件",
            "",
            "SQLite Files (*.db *.sqlite *.sqlite3);;All Files (*)",
        )
        if not path:
            return
        # 默认名 = 文件名(去后缀)
        baseName = os.path.splitext(os.path.basename(path))[0] or "imported"
        # 名称冲突时追加数字
        existing = {info.name for info in self._manager.listAll()}
        newName = baseName
        i = 1
        while newName in existing:
            i += 1
            newName = f"{baseName}_{i}"

        try:
            info = self._manager.createCorpus(
                name=newName,
                dbPath=path,
                description=f"从外部导入: {path}",
            )
            _showInfoBar(
                "success",
                "已绑定",
                f"已绑定外部语料库「{newName}」并切换",
                self,
                duration=2500,
            )
        except Exception as e:
            logger.error(f"[CorpusSwitcher] 绑定外部库失败: {e}")
            _showInfoBar("error", "绑定失败", str(e), self, duration=3000)
