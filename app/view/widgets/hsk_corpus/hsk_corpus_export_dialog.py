# coding: utf-8
"""
HSK 检索结果导出对话框(PRD-005 增强版)
=======================================

把"目录选择 + 格式选择 + 范围选择"合并到一个对话框,
实时显示「可导 N 篇 / 无篇 M 篇 / 合计 X 篇」预览,
一次点击完成所有配置。

UI 布局:
    ┌─────────────────────────────────────┐
    │ 导出 X 篇命中作文(标题:范围选项)      │
    ├─────────────────────────────────────┤
    │ 保存目录:                            │
    │ [ /path/to/dir        ] [ 选择... ]  │
    │                                      │
    │ 输出格式:                            │
    │ ( ) txt 纯文本   (•) docx Word 文档  │
    │                                      │
    │ 导出范围:                            │
    │ (•) 仅含篇目(默认)  ( ) 全量(含篇目+无篇) │
    │                                      │
    │ 预览:                                │
    │   命中 739 篇 · 可导 376 篇 · 跳过 363 篇 │
    │   ⚠ local db 缺 9 篇 zwhao(已剔除)  │
    ├─────────────────────────────────────┤
    │             [取消]    [开始导出]      │
    └─────────────────────────────────────┘

设计原则:
    - 单 modal 而非三连弹(目录 → 格式 → 范围)
    - 实时预览:用户切格式/范围时立刻看到「会导多少篇」
    - 校验完整:目录存在性 + 命中数 + local db 状态
    - 用户按"开始导出"才会启动 Worker,所有参数一次传齐
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    RadioButton,
    StrongBodyLabel,
)

from app.core.services import HSK_ESSAY_EXPORT_FEATURE, getPricingCatalog
from app.view.widgets.prismatica_theme import setThemeRole, shellPalette


class HskCorpusExportOptionsDialog(MessageBoxBase):
    """一体化导出配置对话框。"""

    # 用户确认后,value 字段填充:
    #   outputDir / fileFormat / skipMissingTitle / zwhaoList / preview
    value: Dict[str, Any]

    def __init__(
        self,
        zwhaoList: List[str],
        localAvailable: bool,
        parent=None,
    ) -> None:
        super().__init__(parent=parent)
        self.zwhaoList = list(zwhaoList)
        self.localAvailable = localAvailable

        # 默认值
        self._outputDir: str = str(Path.home() / "Documents")
        self._fileFormat: str = "docx"  # 默认 docx(更通用,Word/WPS 都能开)
        self._mergeMode: bool = True  # 默认合并到单文件(用户原话"写入同一个文件")
        # 不再用「是否含 Title」过滤 — 作文母号才是唯一标识,
        # 只要 zwhao 在 local db 就导出。无 Title 的作文元信息里写「(未提取到篇目)」。

        # 预览统计(动态更新)
        self._previewWithTitle = 0
        self._previewNoTitle = 0
        self._previewMissingInLocal = 0
        self._previewTotal = len(self.zwhaoList)
        self.quotedCost: int | None = None

        # 控件
        self._dirEdit: Optional[QLineEdit] = None
        self._dirChooseBtn: Optional[PushButton] = None
        self._txtRadio: Optional[RadioButton] = None
        self._docxRadio: Optional[RadioButton] = None
        self._mergeRadio: Optional[RadioButton] = None
        self._splitRadio: Optional[RadioButton] = None
        self._mergeNameEdit: Optional[QLineEdit] = None
        self._previewLabel: Optional[BodyLabel] = None
        self._warningLabel: Optional[CaptionLabel] = None
        self._priceLabel: Optional[BodyLabel] = None

        self._buildUi()
        getPricingCatalog().catalogChanged.connect(self._onCatalogChanged)
        self._computePreview()
        self._updatePreviewLabel()
        self._updatePriceLabel()

        # 文案
        self.yesButton.setText("开始导出")
        self.cancelButton.setText("取消")
        self.buttonGroup.setFixedHeight(81)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _buildUi(self) -> None:
        # ---- 顶部提示 ----
        titleLabel = StrongBodyLabel(
            f"导出 {self._previewTotal:,} 篇命中作文", self.widget
        )
        titleLabel.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.viewLayout.addWidget(titleLabel)

        descLabel = CaptionLabel(
            "从本地镜像库 hsk_corpus_local.db 提取原文并保存。",
            self.widget,
        )
        setThemeRole(descLabel, "muted")
        self.viewLayout.addWidget(descLabel)

        self.viewLayout.addWidget(self._makeSeparator())

        # ---- 1. 保存目录 ----
        self.viewLayout.addWidget(self._buildLabel("保存目录"))
        dirRow = QHBoxLayout()
        dirRow.setSpacing(8)
        self._dirEdit = QLineEdit(self._outputDir, self.widget)
        self._dirEdit.setPlaceholderText("选择或粘贴目录路径")
        self._dirEdit.setReadOnly(True)
        self._dirEdit.setMinimumWidth(380)
        dirRow.addWidget(self._dirEdit, 1)

        self._dirChooseBtn = PushButton("选择...", self.widget)
        self._dirChooseBtn.clicked.connect(self._onChooseDirClicked)
        dirRow.addWidget(self._dirChooseBtn, 0)
        self.viewLayout.addLayout(dirRow)

        self.viewLayout.addWidget(self._makeSeparator())

        # ---- 2. 输出格式 ----
        self.viewLayout.addWidget(self._buildLabel("输出格式"))
        fmtRow = QHBoxLayout()
        fmtRow.setSpacing(24)
        self._txtRadio = RadioButton("txt  纯文本", self.widget)
        self._docxRadio = RadioButton("docx  Word 文档", self.widget)
        fmtGroup = QButtonGroup(self.widget)
        fmtGroup.addButton(self._txtRadio)
        fmtGroup.addButton(self._docxRadio)
        # 默认 docx
        if self._fileFormat == "txt":
            self._txtRadio.setChecked(True)
        else:
            self._docxRadio.setChecked(True)
        self._txtRadio.toggled.connect(self._onFormatChanged)
        self._docxRadio.toggled.connect(self._onFormatChanged)
        fmtRow.addWidget(self._txtRadio)
        fmtRow.addWidget(self._docxRadio)
        fmtRow.addStretch(1)
        self.viewLayout.addLayout(fmtRow)

        self.viewLayout.addWidget(self._makeSeparator())

        # ---- 4. 文件组织方式 ----
        self.viewLayout.addWidget(self._buildLabel("文件组织"))
        orgRow = QHBoxLayout()
        orgRow.setSpacing(24)
        self._mergeRadio = RadioButton("合并到单文件", self.widget)
        self._splitRadio = RadioButton("每篇独立文件", self.widget)
        orgGroup = QButtonGroup(self.widget)
        orgGroup.addButton(self._mergeRadio)
        orgGroup.addButton(self._splitRadio)
        # 默认合并(用户原话"写入同一个文件")
        if self._mergeMode:
            self._mergeRadio.setChecked(True)
        else:
            self._splitRadio.setChecked(True)
        self._mergeRadio.toggled.connect(self._onOrgChanged)
        self._splitRadio.toggled.connect(self._onOrgChanged)
        orgRow.addWidget(self._mergeRadio)
        orgRow.addWidget(self._splitRadio)
        orgRow.addStretch(1)
        self.viewLayout.addLayout(orgRow)

        # 合并文件名(只在合并模式下可编辑)
        mergeNameRow = QHBoxLayout()
        mergeNameRow.setSpacing(8)
        mergeNameLabel = CaptionLabel("合并文件名:", self.widget)
        setThemeRole(mergeNameLabel, "muted")
        mergeNameLabel.setFixedWidth(80)
        mergeNameRow.addWidget(mergeNameLabel)

        self._mergeNameEdit = QLineEdit("hsk_export", self.widget)
        self._mergeNameEdit.setPlaceholderText("不带扩展名,如 hsk_export")
        self._mergeNameEdit.setMaximumWidth(280)
        mergeNameRow.addWidget(self._mergeNameEdit)
        mergeNameRow.addStretch(1)
        self.viewLayout.addLayout(mergeNameRow)

        self.viewLayout.addWidget(self._makeSeparator())

        # ---- 5. 预览 ----
        self.viewLayout.addWidget(self._buildLabel("预览"))
        self._previewLabel = BodyLabel("", self.widget)
        setThemeRole(
            self._previewLabel,
            "accent",
            "font-size: 13px; font-weight: 600;",
        )
        self.viewLayout.addWidget(self._previewLabel)

        self._warningLabel = CaptionLabel("", self.widget)
        setThemeRole(self._warningLabel, "danger")
        self._warningLabel.setVisible(False)
        self.viewLayout.addWidget(self._warningLabel)

        self._priceLabel = BodyLabel("", self.widget)
        self._priceLabel.setWordWrap(True)
        self.viewLayout.addWidget(self._priceLabel)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _makeSeparator() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setStyleSheet(f"color: {shellPalette().border.name()};")
        sep.setFixedHeight(1)
        return sep

    @staticmethod
    def _buildLabel(text: str) -> QLabel:
        lbl = CaptionLabel(text)
        setThemeRole(lbl, "text", "font-weight: 600;")
        return lbl

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------
    def _onChooseDirClicked(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "选择导出目录", self._outputDir
        )
        if directory:
            self._outputDir = directory
            if self._dirEdit:
                self._dirEdit.setText(directory)

    def _onFormatChanged(self) -> None:
        if self._txtRadio and self._txtRadio.isChecked():
            self._fileFormat = "txt"
        elif self._docxRadio and self._docxRadio.isChecked():
            self._fileFormat = "docx"
        # 格式变化不影响预览数字,无需重新计算

    def _onOrgChanged(self) -> None:
        if self._mergeRadio and self._mergeRadio.isChecked():
            self._mergeMode = True
            if self._mergeNameEdit:
                self._mergeNameEdit.setEnabled(True)
        elif self._splitRadio and self._splitRadio.isChecked():
            self._mergeMode = False
            if self._mergeNameEdit:
                self._mergeNameEdit.setEnabled(False)
        self._updatePreviewLabel()

    def _getMergeFileName(self) -> str:
        """获取合并文件名(去除扩展名与非法字符)。"""
        if not self._mergeNameEdit:
            return "hsk_export"
        name = self._mergeNameEdit.text().strip() or "hsk_export"
        # 去除扩展名(避免用户写 .docx 时重复)
        for ext in (".txt", ".docx"):
            if name.lower().endswith(ext):
                name = name[: -len(ext)]
        # 清洗非法字符
        import re as _re
        name = _re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', name)
        return name or "hsk_export"

    # ------------------------------------------------------------------
    # 预览
    # ------------------------------------------------------------------
    def _computePreview(self) -> None:
        """根据 zwhao 列表 + local db 状态计算预览数字。"""
        if not self.localAvailable or not self.zwhaoList:
            self._previewWithTitle = 0
            self._previewNoTitle = 0
            self._previewMissingInLocal = 0
            return

        # 流式查 local db,统计 hasTitle
        from app.core.services.hsk_local_corpus_service import (
            hskLocalCorpusService,
        )

        records = hskLocalCorpusService.fetchRecordsByZwhaoList(
            self.zwhaoList
        )
        found_zwhao = {r["zwhao"] for r in records}
        self._previewMissingInLocal = len(self.zwhaoList) - len(found_zwhao)
        self._previewWithTitle = sum(
            1 for r in records if r.get("hasTitle", False)
        )
        self._previewNoTitle = len(found_zwhao) - self._previewWithTitle

    def _updatePreviewLabel(self) -> None:
        """刷新预览文字 + 警告。

        设计原则:作文母号(zwhao)是唯一标识,只要在 local db 就导出。
        所以预览显示「将导出 = 命中数 - local db 缺失数」。
        """
        if not self._previewLabel:
            return

        if not self.localAvailable:
            self._previewLabel.setText(
                "⚠ 本地镜像库不可用,无法导出"
            )
            setThemeRole(self._previewLabel, "danger")
            if self._warningLabel:
                self._warningLabel.setText(
                    "请检查 datas/corpora/hsk_corpus_local.db 是否存在"
                )
                self._warningLabel.setVisible(True)
            return

        if not self.zwhaoList:
            self._previewLabel.setText("⚠ 当前筛选条件下无作文可导出")
            setThemeRole(self._previewLabel, "danger")
            return

        will_export = (
            self._previewWithTitle + self._previewNoTitle
        )
        will_skip_missing = self._previewMissingInLocal
        no_title = self._previewNoTitle  # 仅作为提示,不影响导出

        if self._mergeMode:
            file_unit = "1 个文件"
        else:
            file_unit = f"{will_export:,} 个文件"

        text = (
            f"命中 {self._previewTotal:,} 篇 · "
            f"将导出 {will_export:,} 篇 → {file_unit}"
        )
        if no_title > 0:
            text += f" · 其中 {no_title:,} 篇无篇目(正常)"
        if will_skip_missing > 0:
            text += f" · 缺 {will_skip_missing} 篇(local db 无)"
        self._previewLabel.setText(text)
        setThemeRole(self._previewLabel, "accent", "font-weight: 600;")

        # 警告:全部缺失
        if will_export == 0 and will_skip_missing > 0:
            if self._warningLabel:
                self._warningLabel.setText(
                    "提示:命中的作文母号在本地镜像库中均不存在。"
                )
                self._warningLabel.setVisible(True)

    def _onCatalogChanged(self, _catalog: Dict[str, Any]) -> None:
        self._updatePriceLabel()

    def _updatePriceLabel(self) -> None:
        if self._priceLabel is None:
            return
        willExport = self._previewWithTitle + self._previewNoTitle
        if willExport <= 0:
            self.quotedCost = None
            self._priceLabel.setText("当前没有可计费的作文")
            self.yesButton.setEnabled(False)
            return
        catalog = getPricingCatalog()
        cost = catalog.meteredCost(HSK_ESSAY_EXPORT_FEATURE, willExport)
        if cost is None:
            self.quotedCost = None
            self._priceLabel.setText("预计费用：价格同步中...")
            self.yesButton.setEnabled(False)
            catalog.refreshAsync()
            return
        rule = catalog.rule(HSK_ESSAY_EXPORT_FEATURE)
        unitSize = int(rule.get("unitSize", 100) or 100)
        perUnitCost = int(rule.get("perUnitCost", 0) or 0)
        self.quotedCost = int(cost)
        self._priceLabel.setText(
            f"预计费用：{cost} 点 · 每 {unitSize:,} 篇 {perUnitCost} 点，"
            "不足一档按一档计；开始后锁定当前价格"
        )
        setThemeRole(self._priceLabel, "accent", "font-weight: 600;")
        self.yesButton.setText(f"开始导出（{cost} 点）")
        self.yesButton.setEnabled(True)

    # ------------------------------------------------------------------
    # 校验 / 接受
    # ------------------------------------------------------------------
    def validate(self) -> bool:
        """点「开始导出」前的最终校验。"""
        if not self.localAvailable:
            return False
        if not self.zwhaoList:
            return False
        if not self._outputDir:
            return False
        if self.quotedCost is None:
            return False
        outPath = Path(self._outputDir)
        # 目录可写校验:尝试创建
        try:
            outPath.mkdir(parents=True, exist_ok=True)
        except Exception:
            return False
        return True

    # 重写 accept:把结果填到 value
    def accept(self) -> None:
        if not self.validate():
            return
        self.value = {
            "outputDir": self._outputDir,
            "fileFormat": self._fileFormat,
            "mergeMode": self._mergeMode,
            "mergeFileName": self._getMergeFileName(),
            "zwhaoList": self.zwhaoList,
            "previewTotal": self._previewTotal,
            "previewWithTitle": self._previewWithTitle,
            "previewNoTitle": self._previewNoTitle,
            "previewMissingInLocal": self._previewMissingInLocal,
            "billedEssayCount": self._previewWithTitle + self._previewNoTitle,
            "quotedCost": self.quotedCost,
        }
        super().accept()

    def closeEvent(self, event) -> None:
        try:
            getPricingCatalog().catalogChanged.disconnect(self._onCatalogChanged)
        except Exception:
            pass
        super().closeEvent(event)
