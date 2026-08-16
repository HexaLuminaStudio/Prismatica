# coding: utf-8
"""Excel 导出数据清洗工具。"""

from typing import Any

from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE


def sanitizeExcelCellValue(value: Any) -> Any:
    """移除 OpenXML 工作表不允许的控制字符，保留其他值及合法换行。"""
    if not isinstance(value, str):
        return value
    return ILLEGAL_CHARACTERS_RE.sub("", value)
