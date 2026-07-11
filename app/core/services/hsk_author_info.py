# coding: utf-8
"""
HSK 作者信息解析
将 HSK 下载数据中的 `auther_info` 字段从
    [国籍:新加坡][性别:男][考试时间:200103][作文题目:我对离婚问题的看法]...
拆分为多个独立字段：
    国籍 / 性别 / 考试时间 / 作文题目 / 口试分数 / 作文分数 /
    听力理解分数 / 阅读理解分数 / 综合表达考试分数 / 考试总分 / 证书级别
"""

import re
from typing import Any, Dict, List

import pandas as pd
from loguru import logger


# 字段映射：原始键 -> 输出列名
FIELD_MAPPING: Dict[str, str] = {
    "国籍": "作者国籍",
    "性别": "作者性别",
    "考试时间": "考试时间",
    "作文题目": "作文题目",
    "口试分数": "口试分数",
    "作文分数": "作文分数",
    "听力理解分数": "听力理解分数",
    "阅读理解分数": "阅读理解分数",
    "综合表达考试分数": "综合表达考试分数",
    "考试总分": "考试总分",
    "证书级别": "证书级别",
}


def parseAuthorInfo(rawInfo: str) -> Dict[str, str]:
    """
    解析一条 auther_info 字符串。

    Args:
        rawInfo: 形如 `[国籍:新加坡][性别:男]...` 的字符串。

    Returns:
        {输出列名: 值} 的字典，未出现的字段不会出现在结果中。
    """
    result: Dict[str, str] = {}
    if not rawInfo or not isinstance(rawInfo, str):
        return result

    # 匹配 [键:值] 形式，键中允许中文/英文/数字，值允许任意字符（非 ]）
    pattern = re.compile(r"\[([^\]:]+):([^\]]*)\]")
    for match in pattern.finditer(rawInfo):
        key = match.group(1).strip()
        value = match.group(2).strip()
        if key in FIELD_MAPPING:
            result[FIELD_MAPPING[key]] = value

    return result


def splitAuthorInfoColumn(
    df: pd.DataFrame,
    sourceColumn: str = "auther_info",
) -> pd.DataFrame:
    """
    将 DataFrame 中的 auther_info 列拆分为多列，并保留原始列。

    Args:
        df: 原始 DataFrame。
        sourceColumn: 源列名（默认 auther_info）。

    Returns:
        新 DataFrame：包含原列 + 拆分后的新列。
    """
    if df is None or df.empty or sourceColumn not in df.columns:
        return df

    splitRows: List[Dict[str, Any]] = []
    nonEmptyCount = 0
    for value in df[sourceColumn]:
        info = parseAuthorInfo(str(value) if value is not None else "")
        splitRows.append(info)
        if info:
            nonEmptyCount += 1

    if not nonEmptyCount:
        logger.debug("[HSK] auther_info 列均为空，跳过拆分")
        return df

    splitDf = pd.DataFrame(splitRows, index=df.index)

    # 仅添加拆分出的新列，避免覆盖原 df 中已有的同名列
    for col in splitDf.columns:
        if col not in df.columns:
            df[col] = splitDf[col]
        else:
            # 原 df 中已存在同名列（如"国籍"），将拆分结果写入 `作者XX` 别名
            # 但因为我们已经做了映射（"国籍" -> "作者国籍"），理论上不会冲突
            df[f"{col}_parsed"] = splitDf[col]

    logger.info(
        f"[HSK] auther_info 拆分完成: {nonEmptyCount}/{len(df)} 条非空，"
        f"新增列: {list(splitDf.columns)}"
    )
    return df
