# coding: utf-8
"""Prismatica 凭证生成器 —— 主窗口 UI

独立的运营工具窗口,不依赖主程序(project_manager / chat / splash / auth)。

功能:
    - 生成邀请码 (INV) / 体验码 (TRY) / 充值码 (RCH)
    - 批量导入并验签
    - 表格展示生成结果(每条带复制按钮)
    - 导出为 .txt(与 tools/ops.py 格式一致)
    - 复制全部 / 单条到剪贴板

启动:python tools/code_generator.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# 让脚本可独立运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Pivot,
    PrimaryPushButton,
    PushButton,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    StrongBodyLabel,
    BodyLabel,
    CaptionLabel,
)

from app.core.models.auth_models import UserTier
from app.core.utils.signed_code import (
    decodeSignedCode,
    makeInviteCode,
    makeRechargeCode,
    makeTrialCode,
    tryParseAnyCode,
)


# 主题色:与 SplashWindow 保持一致
THEME_COLOR = "#00b09c"
SHADOW_COLOR = QColor(0, 0, 0, 50)


# =====================================================================
# 卡片容器(仿 SplashWindow 的 _card)
# =====================================================================


class Card(QFrame):
    """圆角 + 阴影的卡片容器"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("opsCard")
        self.setStyleSheet(
            "#opsCard {"
            "background-color: white;"
            "border-radius: 10px;"
            "border: 1px solid #e6e6e6;"
            "}"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(SHADOW_COLOR)
        self.setGraphicsEffect(shadow)


def _hSep() -> QFrame:
    """水平分隔线"""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setStyleSheet("color: #e6e6e6;")
    return line


# =====================================================================
# 主窗口
# =====================================================================


class CodeGeneratorWindow(QWidget):
    """凭证生成器主窗口"""

    KIND_INVITE = "invite"
    KIND_TRIAL = "trial"
    KIND_RECHARGE = "recharge"

    WINDOW_WIDTH = 960
    WINDOW_HEIGHT = 720

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._currentKind = self.KIND_INVITE
        self._generatedRows: list[dict] = []
        # 每行结构:{"kind": str, "code": str, "params": str, "row": int}

        self.setWindowTitle("Prismatica 凭证生成器")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.resize(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)
        self._buildUi()
        self._connectSignals()
        self._onKindChanged()  # 初始化表单显隐

    # ------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------
    def _buildUi(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # 顶部 header
        header = QHBoxLayout()
        title = StrongBodyLabel("Prismatica 凭证生成器")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch(1)
        hint = CaptionLabel(
            "运营工具 —— 生成邀请码/体验码/充值码,基于 LICENSE_SECRET HMAC 签名"
        )
        hint.setStyleSheet("color: #888;")
        header.addWidget(hint)
        outer.addLayout(header)

        # ---- 卡片 1:生成 + 验签(并排) ----
        topRow = QHBoxLayout()
        topRow.setSpacing(12)
        topRow.addWidget(self._buildGenerateCard(), 1)
        topRow.addWidget(self._buildVerifyCard(), 1)
        outer.addLayout(topRow)

        # ---- 卡片 2:生成结果表 ----
        outer.addWidget(self._buildResultCard(), 1)

        # ---- 卡片 3:验签结果 ----
        outer.addWidget(self._buildVerifyResultCard())

    def _buildGenerateCard(self) -> QWidget:
        card = Card(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = StrongBodyLabel("生成")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(_hSep())

        # 类型选择(Pivot)
        self.kindPivot = Pivot(card)
        self.kindPivot.addItem("invite", "邀请码 INV")
        self.kindPivot.addItem("trial", "体验码 TRY")
        self.kindPivot.addItem("recharge", "充值码 RCH")
        self.kindPivot.setCurrentItem("invite")
        layout.addWidget(self.kindPivot)

        # 参数表单(用 grid 简化布局)
        form = QVBoxLayout()
        form.setSpacing(6)

        self.countSpin = QSpinBox(card)
        self.countSpin.setRange(1, 10000)
        self.countSpin.setValue(10)
        self.countSpin.setSuffix(" 条")

        self.balanceSpin = QSpinBox(card)
        self.balanceSpin.setRange(0, 1_000_000)
        self.balanceSpin.setValue(100)
        self.balanceSpin.setSuffix(" 币")

        self.daysSpin = QSpinBox(card)
        self.daysSpin.setRange(1, 3650)
        self.daysSpin.setValue(30)
        self.daysSpin.setSuffix(" 天")

        self.noteEdit = QLineEdit(card)
        self.noteEdit.setPlaceholderText("运营备注(可选,仅充值码使用)")
        self.noteEdit.setMaxLength(80)

        self.tierCombo = QComboBox(card)
        self.tierCombo.addItems([t.value for t in UserTier])
        self.tierCombo.setCurrentText(UserTier.BETA.value)

        # 用 row 形式摆:标签 + 控件
        def row(label: str, widget: QWidget) -> QHBoxLayout:
            r = QHBoxLayout()
            r.setSpacing(8)
            lab = BodyLabel(label)
            lab.setFixedWidth(72)
            lab.setStyleSheet("color: #555;")
            r.addWidget(lab)
            r.addWidget(widget, 1)
            return r

        form.addLayout(row("数量", self.countSpin))
        form.addLayout(row("赠送余额", self.balanceSpin))
        form.addLayout(row("有效天数", self.daysSpin))
        form.addLayout(row("用户档位", self.tierCombo))
        form.addLayout(row("备注", self.noteEdit))
        layout.addLayout(form)

        self.generateBtn = PrimaryPushButton("生成", card)
        self.generateBtn.setFixedHeight(36)
        layout.addWidget(self.generateBtn)

        layout.addStretch(1)
        return card

    def _buildVerifyCard(self) -> QWidget:
        card = Card(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = StrongBodyLabel("验签(批量)")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(_hSep())

        hint = CaptionLabel("每行粘贴一个码(支持 INV/TRY/RCH)")
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)

        self.verifyInput = QPlainTextEdit(card)
        self.verifyInput.setPlaceholderText("粘贴多个码,每行一个")
        self.verifyInput.setMinimumHeight(180)
        layout.addWidget(self.verifyInput, 1)

        self.verifyBtn = PrimaryPushButton("验证全部", card)
        self.verifyBtn.setFixedHeight(36)
        layout.addWidget(self.verifyBtn)

        layout.addStretch(1)
        return card

    def _buildResultCard(self) -> QWidget:
        card = Card(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = StrongBodyLabel("生成结果")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch(1)
        self.resultCountLabel = CaptionLabel("0 条")
        self.resultCountLabel.setStyleSheet("color: #888;")
        header.addWidget(self.resultCountLabel)
        layout.addLayout(header)
        layout.addWidget(_hSep())

        # 表格
        self.resultTable = QTableWidget(card)
        self.resultTable.setColumnCount(5)
        self.resultTable.setHorizontalHeaderLabels(
            ["#", "类型", "码(后 12 位)", "参数", "操作"]
        )
        self.resultTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.resultTable.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.resultTable.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        self.resultTable.verticalHeader().setVisible(False)
        self.resultTable.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.resultTable.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.resultTable, 1)

        # 操作行
        actionRow = QHBoxLayout()
        actionRow.setSpacing(8)
        self.copyAllBtn = PushButton("复制全部", card)
        self.exportBtn = PushButton("导出 .txt", card)
        self.clearBtn = PushButton("清空", card)
        actionRow.addWidget(self.copyAllBtn)
        actionRow.addWidget(self.exportBtn)
        actionRow.addStretch(1)
        actionRow.addWidget(self.clearBtn)
        layout.addLayout(actionRow)

        return card

    def _buildVerifyResultCard(self) -> QWidget:
        card = Card(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = StrongBodyLabel("验签结果")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(_hSep())

        self.verifyOutput = QPlainTextEdit(card)
        self.verifyOutput.setReadOnly(True)
        self.verifyOutput.setMaximumHeight(160)
        self.verifyOutput.setStyleSheet(
            "QPlainTextEdit {"
            "font-family: Consolas, monospace;"
            "font-size: 11px;"
            "background: #fafafa;"
            "}"
        )
        layout.addWidget(self.verifyOutput)
        return card

    # ------------------------------------------------------------
    # 信号
    # ------------------------------------------------------------
    def _connectSignals(self) -> None:
        self.kindPivot.currentItemChanged.connect(self._onKindChanged)
        self.generateBtn.clicked.connect(self._onGenerate)
        self.verifyBtn.clicked.connect(self._onVerify)
        self.copyAllBtn.clicked.connect(self._onCopyAll)
        self.exportBtn.clicked.connect(self._onExport)
        self.clearBtn.clicked.connect(self._onClear)

    # ------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------
    def _onKindChanged(self) -> None:
        key = self.kindPivot.currentRouteKey()
        if key == "invite":
            self._currentKind = self.KIND_INVITE
        elif key == "trial":
            self._currentKind = self.KIND_TRIAL
        else:
            self._currentKind = self.KIND_RECHARGE
        # INV/TRY 显示 balance/days/tier,RCH 显示 amount/note
        isRecharge = self._currentKind == self.KIND_RECHARGE
        # tier 仅 INV 有意义
        self.tierCombo.setEnabled(self._currentKind == self.KIND_INVITE)
        # 余额/天数在 RCH 下语义不同:balance 作为 amount,days 隐藏
        if isRecharge:
            self.balanceSpin.setPrefix("面额 ")
            self.daysSpin.setEnabled(False)
            self.daysSpin.setValue(365)  # 默认 1 年过期
            self.noteEdit.setEnabled(True)
        else:
            self.balanceSpin.setPrefix("赠送 ")
            self.daysSpin.setEnabled(True)
            self.noteEdit.setEnabled(False)

    def _onGenerate(self) -> None:
        count = self.countSpin.value()
        try:
            new_codes: list[tuple[str, str]] = []  # (kind, raw_code)
            if self._currentKind == self.KIND_INVITE:
                for _ in range(count):
                    raw = makeInviteCode(
                        maxUses=1,
                        grantedBalance=self.balanceSpin.value(),
                        grantedDays=self.daysSpin.value(),
                        tier=self.tierCombo.currentText(),
                    )
                    new_codes.append((self.KIND_INVITE, raw))
            elif self._currentKind == self.KIND_TRIAL:
                for _ in range(count):
                    raw = makeTrialCode(
                        grantedBalance=self.balanceSpin.value(),
                        grantedDays=self.daysSpin.value(),
                    )
                    new_codes.append((self.KIND_TRIAL, raw))
            else:
                for _ in range(count):
                    raw = makeRechargeCode(
                        amount=self.balanceSpin.value(),
                        note=self.noteEdit.text().strip(),
                    )
                    new_codes.append((self.KIND_RECHARGE, raw))

            # 追加到结果表
            start_row = len(self._generatedRows)
            for i, (kind, raw) in enumerate(new_codes):
                row_idx = start_row + i
                params = self._formatParams(kind)
                self._generatedRows.append(
                    {"kind": kind, "code": raw, "params": params, "row": row_idx}
                )
                self._appendTableRow(row_idx + 1, kind, raw, params)

            self.resultCountLabel.setText(f"{len(self._generatedRows)} 条")
            self._info(f"已生成 {count} 条{self._kindLabel(self._currentKind)}")
        except Exception as e:
            self._error(f"生成失败: {e}")

    def _onVerify(self) -> None:
        text = self.verifyInput.toPlainText().strip()
        if not text:
            self._error("请粘贴要验证的码")
            return
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        results: list[dict] = []
        for line in lines:
            entry: dict = {"code": line[-12:] if len(line) >= 12 else line}
            try:
                kind, model = tryParseAnyCode(line)
                data = decodeSignedCode(line)
                payload = {k: v for k, v in data.items() if k != "signature"}
                entry.update(
                    {
                        "ok": True,
                        "kind": kind,
                        "payload": payload,
                        "model": model.model_dump(mode="json"),
                    }
                )
            except Exception as e:
                entry.update({"ok": False, "error": str(e)})
            results.append(entry)

        # 渲染输出
        output_lines: list[str] = []
        for i, r in enumerate(results, 1):
            status = "✅" if r["ok"] else "❌"
            output_lines.append(f"[{i}] {status} {r['code']}")
            if r["ok"]:
                output_lines.append(f"    类型: {r['kind']}")
                output_lines.append(
                    "    payload: " + json.dumps(r["payload"], ensure_ascii=False)
                )
            else:
                output_lines.append(f"    错误: {r['error']}")
            output_lines.append("")
        self.verifyOutput.setPlainText("\n".join(output_lines))

        ok = sum(1 for r in results if r["ok"])
        fail = len(results) - ok
        if fail == 0:
            self._info(f"全部 {ok} 条验签通过")
        else:
            self._error(f"验签完成: {ok} 通过,{fail} 失败")

    def _onCopyAll(self) -> None:
        if not self._generatedRows:
            self._error("没有可复制的码")
            return
        text = "\n".join(r["code"] for r in self._generatedRows)
        QGuiApplication.clipboard().setText(text)
        self._info(f"已复制 {len(self._generatedRows)} 条到剪贴板")

    def _onExport(self) -> None:
        if not self._generatedRows:
            self._error("没有可导出的码")
            return
        # 按类型分组,每组一个 header
        groups: dict[str, list[dict]] = {}
        for r in self._generatedRows:
            groups.setdefault(r["kind"], []).append(r)

        # 弹出保存对话框
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 .txt",
            f"codes_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt",
            "Text Files (*.txt)",
        )
        if not path:
            return
        try:
            lines: list[str] = []
            lines.append(f"# Prismatica 凭证导出")
            lines.append(f"# generated_at: {datetime.utcnow().isoformat()}")
            lines.append("")
            for kind, rows in groups.items():
                label = self._kindLabel(kind)
                lines.append(f"# === {label} x{len(rows)} ===")
                for r in rows:
                    lines.append(r["code"])
                lines.append("")
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            self._info(f"已导出到 {path}")
        except Exception as e:
            self._error(f"导出失败: {e}")

    def _onClear(self) -> None:
        self._generatedRows.clear()
        self.resultTable.setRowCount(0)
        self.resultCountLabel.setText("0 条")

    # ------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------
    def _appendTableRow(self, idx: int, kind: str, code: str, params: str) -> None:
        row = self.resultTable.rowCount()
        self.resultTable.insertRow(row)
        self.resultTable.setItem(row, 0, QTableWidgetItem(str(idx)))
        self.resultTable.setItem(row, 1, QTableWidgetItem(self._kindLabel(kind)))
        self.resultTable.setItem(row, 2, QTableWidgetItem("…" + code[-12:]))
        self.resultTable.setItem(row, 3, QTableWidgetItem(params))

        # 操作列:复制按钮
        copy_btn = PushButton("复制", self.resultTable)
        copy_btn.setFixedWidth(64)
        copy_btn.clicked.connect(lambda _=False, c=code: self._copyOne(c))
        self.resultTable.setCellWidget(row, 4, copy_btn)

    def _copyOne(self, code: str) -> None:
        QGuiApplication.clipboard().setText(code)
        self._info(f"已复制: …{code[-12:]}")

    def _formatParams(self, kind: str) -> str:
        if kind == self.KIND_INVITE:
            return (
                f"+{self.balanceSpin.value()}币/"
                f"{self.daysSpin.value()}天/"
                f"{self.tierCombo.currentText()}"
            )
        if kind == self.KIND_TRIAL:
            return f"+{self.balanceSpin.value()}币/{self.daysSpin.value()}天"
        note = self.noteEdit.text().strip()
        base = f"面额 {self.balanceSpin.value()}币"
        return base + (f"/{note}" if note else "")

    @staticmethod
    def _kindLabel(kind: str) -> str:
        return {
            "invite": "INV",
            "trial": "TRY",
            "recharge": "RCH",
        }.get(kind, kind)

    def _info(self, msg: str) -> None:
        InfoBar.success(
            title="成功",
            content=msg,
            parent=self,
            duration=2000,
            position=InfoBarPosition.TOP,
        )

    def _error(self, msg: str) -> None:
        InfoBar.error(
            title="失败",
            content=msg,
            parent=self,
            duration=3000,
            position=InfoBarPosition.TOP,
        )


# 让本文件可单独 import 测试
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = CodeGeneratorWindow()
    w.show()
    sys.exit(app.exec())