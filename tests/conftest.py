# coding: utf-8
"""pytest 全局 fixtures + sys.path 修复(2026-08-05 T8)。

由于 PrismaticaUI 不是 installable 包,直接 `pytest tests/` 默认
会找不到 `app` 这个 root 包。在 pyproject 已配 pythonpath=["."],
但部分环境下(尤其 Windows + 中文路径)需要兜底。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 把项目根加进 sys.path,保证 `import app.xxx` 可用
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# 阻止 loguru 在测试期向上传播导致卡顿
try:
    from loguru import logger as _loguru

    _loguru.remove()
    _loguru.add(lambda m: None, level="WARNING")
except Exception:  # noqa: BLE001
    pass
