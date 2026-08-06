# coding: utf-8
"""
HSK 本地语料镜像库服务(PRD-005,只读版本)
=========================================

仅负责读 `datas/corpora/hsk_corpus_local.db`(由 PRD-004 导入 worker 写入),
不负责导入、不负责写入,职责收敛到查询/枚举。

为什么仍需要这个 service(即便 db 已存在):
    - 视图层不允许直接打开 SQLite,必须经 services 层
    - 统一处理 Title 解析、LIKE 通配符转义、batch 查询

API:
    - isAvailable()       : db 文件存在且可读
    - rowCount()          : 总行数
    - getRecord(zwhao)    : 按作文母号精确查
    - fetchRecordsByZwhaoList(zwhaoList) : 批量按 zwhao 查(导出主路径)
    - iterAll(batchSize)  : 全量迭代生成器
    - extractTitle(dataText) : 从 data 文本中解析 Title 行
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app.core.utils import logger

from app.core.utils.data_paths import HSK_LOCAL_CORPUS_DB


# 作文正文里 Title 行的正则:前导若干空白 + Title + ≥1 空白 + 标题正文
_TITLE_RE = re.compile(r"^\s*Title\s+(.+)$", flags=re.MULTILINE)


def _extractTitle(rawData: str) -> str:
    """从 data 文本中提取作文标题(空则返回 '')。"""
    if not rawData:
        return ""
    m = _TITLE_RE.search(rawData)
    return m.group(1).strip()[:40] if m else ""


def _hasTitle(rawData: str) -> bool:
    """data 文本是否包含篇目行。"""
    if not rawData:
        return False
    return bool(_TITLE_RE.search(rawData))


class HskLocalCorpusService:
    """HSK 本地语料镜像库服务(只读)。"""

    _instance: Optional["HskLocalCorpusService"] = None
    _instanceLock = threading.Lock()

    def __init__(self) -> None:
        self._dbPath: Path = HSK_LOCAL_CORPUS_DB

    # ------------------------------------------------------------------
    # 单例
    # ------------------------------------------------------------------
    @classmethod
    def instance(cls) -> "HskLocalCorpusService":
        if cls._instance is None:
            with cls._instanceLock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------
    @property
    def dbPath(self) -> Path:
        return self._dbPath

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._dbPath), timeout=10.0)

    def isAvailable(self) -> bool:
        """db 文件存在且包含 hsk_local_corpus 表。"""
        if not self._dbPath.exists():
            return False
        try:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='hsk_local_corpus' LIMIT 1"
                )
                return cur.fetchone() is not None
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[HskLocalCorpusService] isAvailable 失败: {e}")
            return False

    def rowCount(self) -> int:
        if not self.isAvailable():
            return 0
        try:
            conn = self._connect()
            try:
                cur = conn.execute("SELECT COUNT(*) FROM hsk_local_corpus")
                return int(cur.fetchone()[0])
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[HskLocalCorpusService] rowCount 失败: {e}")
            return 0

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def getRecord(self, zwhao: str) -> Optional[Dict[str, Any]]:
        """按作文母号精确查。"""
        if not self.isAvailable() or not zwhao:
            return None
        try:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT zwhao, shard_id, line_offset, data, fetched_at "
                    "FROM hsk_local_corpus WHERE zwhao = ?",
                    (zwhao,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return self._rowToRecord(row)
            finally:
                conn.close()
        except Exception as e:
            logger.warning(
                f"[HskLocalCorpusService] getRecord({zwhao}) 失败: {e}"
            )
            return None

    def fetchRecordsByZwhaoList(
        self, zwhaoList: List[str]
    ) -> List[Dict[str, Any]]:
        """按 zwhao 列表批量查(导出场景的主路径)。"""
        if not self.isAvailable() or not zwhaoList:
            return []
        placeholders = ",".join("?" for _ in zwhaoList)
        try:
            conn = self._connect()
            try:
                cur = conn.execute(
                    f"SELECT zwhao, shard_id, line_offset, data, fetched_at "
                    f"FROM hsk_local_corpus WHERE zwhao IN ({placeholders})",
                    zwhaoList,
                )
                return [self._rowToRecord(row) for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(
                f"[HskLocalCorpusService] fetchRecordsByZwhaoList 失败: {e}"
            )
            return []

    def iterAll(
        self, batchSize: int = 500
    ) -> Iterator[List[Dict[str, Any]]]:
        """全量迭代生成器(导出大场景的备用路径)。"""
        if not self.isAvailable():
            return
        try:
            conn = sqlite3.connect(
                str(self._dbPath), timeout=10.0, check_same_thread=False
            )
            try:
                cur = conn.execute(
                    "SELECT zwhao, shard_id, line_offset, data, fetched_at "
                    "FROM hsk_local_corpus"
                )
                batch: List[Dict[str, Any]] = []
                for row in cur:
                    batch.append(self._rowToRecord(row))
                    if len(batch) >= batchSize:
                        yield batch
                        batch = []
                if batch:
                    yield batch
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[HskLocalCorpusService] iterAll 失败: {e}")

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def extractTitle(self, dataText: str) -> str:
        """对外暴露:解析 Title。"""
        return _extractTitle(dataText)

    def hasTitle(self, dataText: str) -> bool:
        """对外暴露:是否含篇目行。"""
        return _hasTitle(dataText)

    @staticmethod
    def _rowToRecord(row: tuple) -> Dict[str, Any]:
        zwhao, shardId, lineOffset, dataText, fetchedAt = row
        return {
            "zwhao": zwhao,
            "shardId": shardId,
            "lineOffset": lineOffset,
            "data": dataText,
            "fetchedAt": fetchedAt,
            "title": _extractTitle(dataText or ""),
            "hasTitle": _hasTitle(dataText or ""),
        }


# 全局单例
hskLocalCorpusService = HskLocalCorpusService.instance()