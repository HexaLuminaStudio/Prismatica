# coding: utf-8
"""Prismatica 凭证生成器 —— 独立小工具入口

启动方法(在项目根目录):
    python tools/code_generator.py

特点:
    - 不依赖 main.py / qfluentwidgetspro / main_window / project_manager
    - 启动快,体积小,只引入 PySide6 + qfluentwidgets + signed_code
    - 适用于运营人员本地批量生成 INV/TRY/RCH 凭证
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from tools.code_generator_window import CodeGeneratorWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Prismatica 凭证生成器")
    w = CodeGeneratorWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())