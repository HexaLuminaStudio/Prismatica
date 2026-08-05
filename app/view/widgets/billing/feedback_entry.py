# coding: utf-8
"""内测反馈入口

修复(2026-08-05):
    1. logger_msg 变量未定义 → 改为正常 logger 调用
    2. self.view 重新赋值与 MessageBoxBase 冲突 → 直接装配 self.view
    3. 缺少主题色 / 描述为空时给焦点
    4. zip 文件名统一时间戳便于排序
"""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from qfluentwidgets import (
    MessageBoxBase, SubtitleLabel, BodyLabel, CaptionLabel,
    PlainTextEdit, ComboBox, PrimaryPushButton, PushButton,
    FluentIcon as FIF,
    InfoBar, InfoBarPosition,
)
from loguru import logger

from app.core.services.auth_service import getAuthService
from app.core.utils.setting import LOG_FOLDER


FEEDBACK_DIR: Path = Path(LOG_FOLDER).parent / "datas" / "feedback"


def _resolveBodyWidget(dlg: MessageBoxBase) -> QWidget:
    return getattr(dlg, "widget", None) or getattr(dlg, "view", None)


class FeedbackDialog(MessageBoxBase):
    """内测反馈弹窗"""

    CATEGORIES = ["Bug", "功能建议", "性能", "体验", "其它"]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent=parent)

        # 标题
        self.titleLabel = SubtitleLabel("提交内测反馈", self)
        self.hintLabel = CaptionLabel(
            "反馈会写入本地 <INSTALL_DIR>/datas/feedback/。"
            "内测期内请将生成的 zip 发给运营。",
            self,
        )
        self.hintLabel.setWordWrap(True)

        # 分类
        self.categoryLabel = CaptionLabel("分类:", self)
        self.categoryCombo = ComboBox(self)
        self.categoryCombo.addItems(self.CATEGORIES)

        # 描述
        self.descLabel = CaptionLabel("描述:", self)
        self.descEdit = PlainTextEdit(self)
        self.descEdit.setPlaceholderText(
            "请详细描述您遇到的问题或建议…\n\n"
            "• 如果是 Bug:触发步骤 / 预期 vs 实际 / 截图\n"
            "• 如果是建议:动机 / 替代方案"
        )

        # 装配 view
        layout = getattr(self, "viewLayout", None) or _resolveBodyWidget(self).layout()
        layout.setContentsMargins(24, 24, 24, 16)
        layout.setSpacing(8)
        layout.addWidget(self.titleLabel)
        layout.addWidget(self.hintLabel)
        layout.addSpacing(4)
        layout.addWidget(self.categoryLabel)
        layout.addWidget(self.categoryCombo)
        layout.addWidget(self.descLabel)
        layout.addWidget(self.descEdit)

        _resolveBodyWidget(self).setMinimumWidth(480)
        _resolveBodyWidget(self).setMinimumHeight(340)

        # 按钮
        self.yesButton.setText("提交")
        self.cancelButton.setText("取消")
        try:
            self.yesButton.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.yesButton.clicked.connect(self._onSubmit)
        self.buttonGroup.setMinimumWidth(220)

    # ---------- 槽 ----------
    def _onSubmit(self) -> None:
        desc = self.descEdit.toPlainText().strip()
        if not desc:
            self.descEdit.setFocus()
            InfoBar.warning(
                title="描述不能为空",
                content="请填写反馈内容",
                parent=self,
                duration=2500,
                position=InfoBarPosition.TOP,
            )
            return

        category = self.categoryCombo.currentText()
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        feedbackId = f"fb_{timestamp}"
        userId = getAuthService().currentUserId()

        FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)

        # 写描述
        desc_path = FEEDBACK_DIR / f"{feedbackId}.md"
        desc_path.write_text(
            f"# {category}\n\n{desc}\n\nuserId: {userId or 'N/A'}\n",
            encoding="utf-8",
        )

        # 打包最近 1 个日志文件
        log_zip = FEEDBACK_DIR / f"{feedbackId}.zip"
        try:
            log_files = (
                sorted(LOG_FOLDER.glob("*.log"))[-1:]
                if LOG_FOLDER.exists() else []
            )
            with zipfile.ZipFile(log_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(desc_path, arcname=desc_path.name)
                for lf in log_files:
                    zf.write(lf, arcname=lf.name)
        except Exception as e:
            logger.warning(f"[Feedback] 打包日志失败: {e}")
            log_zip = None  # 不阻断主流程

        InfoBar.success(
            title="反馈已保存",
            content=f"已生成 {feedbackId},请到 datas/feedback/ 目录查找",
            parent=self,
            duration=3500,
            position=InfoBarPosition.TOP,
        )
        self.accept()