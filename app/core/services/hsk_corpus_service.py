# coding:utf-8
"""
HSK 语料检索服务层
====================

封装 `hsk_corpus.db` 的所有读写,对外暴露:
    - instance()        进程级单例
    - ensureSchema()    启动期建表 + 建索引(幂等)
    - rowCount()        总行数
    - availableColumns() 可检索列名(UI 填充下拉框)
    - search()          按列 LIKE 模糊检索(白名单 + 通配符转义)

可检索列白名单(8 列,文本 3 + 分数 5):
    - 国籍 / 证书级别 / 作文题目
    - 听力理解分数 / 阅读理解分数 / 综合表达考试分数 / 口试分数 / 作文分数

注:
    - db schema 仍保留全部列,只是 UI 不开放其他列的检索入口
    - 列名走白名单(`_SEARCHABLE_COLUMNS`),杜绝 SQL 注入
    - 用户输入的 LIKE 通配符 `%` / `_` 会被转义为反斜杠形式,
      配 `ESCAPE '\\'` 使用,避免「输入 % 命中全部」
    - 子线程调用方应自行开新连接(`check_same_thread=False`),
      本 service 不缓存连接
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from app.core.utils.data_paths import HSK_CORPUS_DB, HSK_CORPUS_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 列定义(必须与 scripts/build_hsk_corpus_db.py 保持一致;与 Excel 列名一致)
# ---------------------------------------------------------------------------
# Excel 真实 12 列
_COLUMNS: List[tuple] = [
    ("作文题目", "TEXT"),
    ("证书级别", "TEXT"),
    ("国籍", "TEXT"),
    ("总词数", "INTEGER"),
    ("总字数", "INTEGER"),
    ("听力理解分数", "INTEGER"),
    ("阅读理解分数", "INTEGER"),
    ("综合表达考试分数", "INTEGER"),
    ("口试分数", "INTEGER"),
    ("作文分数", "INTEGER"),
    ("作文母号", "TEXT"),
    ("性别", "TEXT"),
]

# 全部列名(不含 id / imported_at)
_ALL_COLUMNS: List[str] = [n for n, _ in _COLUMNS]

# 可检索的列(白名单 — UI 下拉框与 SQL 校验共用此集合)
# 用户指定:文本 3 + 分数 5
_SEARCHABLE_COLUMNS: List[str] = [
    "国籍",  # 文本
    "证书级别",  # 文本(级别)
    "作文题目",  # 文本
    "听力理解分数",  # 分数
    "阅读理解分数",  # 分数
    "综合表达考试分数",  # 分数
    "口试分数",  # 分数
    "作文分数",  # 分数
]


# ---------------------------------------------------------------------------
# 中文表头映射(给 UI 显示用)— 中文列名已是 UI 友好,这里只对 id / imported_at 补一个
# ---------------------------------------------------------------------------
_CN_HEADER_MAP: Dict[str, str] = {
    "id": "记录ID",
    "imported_at": "导入时间",
}


# ---------------------------------------------------------------------------
# Schema SQL(必须与 scripts/build_hsk_corpus_db.py 一致)
# ---------------------------------------------------------------------------
def _buildSchemaSql() -> str:
    colDefs = ",\n    ".join(f"{n} {t}" for n, t in _COLUMNS)
    parts = [
        f"""
CREATE TABLE IF NOT EXISTS hsk_corpus (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    {colDefs},
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
    ]
    for col in (n for n, t in _COLUMNS if t == "TEXT"):
        parts.append(
            f"CREATE INDEX IF NOT EXISTS idx_hsk_{col} "
            f"ON hsk_corpus({col} COLLATE NOCASE);"
        )
    parts.append(
        """
CREATE TABLE IF NOT EXISTS hsk_corpus_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LIKE 通配符转义
# ---------------------------------------------------------------------------
def _escapeLike(raw: str) -> str:
    """转义 LIKE 通配符,使用反斜杠作为 ESCAPE 字符。

    - % → \\%
    - _ → \\_
    - \\ → \\\\

    返回的字符串外面不再包 %,由调用方拼 "%...%"。
    """
    if not raw:
        return ""
    return raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Service 单例(纯 Python,不依赖 Qt;UI 层用回调订阅事件)
# ---------------------------------------------------------------------------
class HskCorpusService:
    """HSK 语料检索服务(进程级单例)。

    持有 db 路径与 schema 版本号;实际 SQLite 连接由调用方在子线程里
    新开(`check_same_thread=False`),本类不持有连接。

    不继承 QObject,避免非 GUI 脚本(如一次性导入脚本)因缺 PySide6 而无法
    import 本模块。UI 层需要监听事件时,使用回调订阅:
        svc.onSchemaReady(cb)
        svc.onImported(cb)

    事件(回调签名):
        onSchemaReady(cb):    ensureSchema() 完成
        onImported(cb):       importFromExcel() 完成,cb(rows: int)
    """

    # 单例
    _instance: Optional["HskCorpusService"] = None
    _instanceLock = threading.Lock()

    def __init__(self) -> None:
        self._dbPath: Path = HSK_CORPUS_DB
        self._schemaVersion: int = HSK_CORPUS_SCHEMA_VERSION
        # 事件回调列表(替代 Qt Signal,跨模块简单安全)
        self._schemaReadyCallbacks: List = []
        self._importedCallbacks: List = []

    def onSchemaReady(self, callback) -> None:
        """订阅 ensureSchema() 完成事件。callback 签名: () -> None"""
        if callable(callback) and callback not in self._schemaReadyCallbacks:
            self._schemaReadyCallbacks.append(callback)

    def onImported(self, callback) -> None:
        """订阅 importFromExcel() 完成事件。callback 签名: (rows: int) -> None"""
        if callable(callback) and callback not in self._importedCallbacks:
            self._importedCallbacks.append(callback)

    def _emitSchemaReady(self) -> None:
        for cb in list(self._schemaReadyCallbacks):
            try:
                cb()
            except Exception as e:
                logger.warning(f"[HskCorpusService] schemaReady 回调异常: {e}")

    def _emitImported(self, rows: int) -> None:
        for cb in list(self._importedCallbacks):
            try:
                cb(int(rows))
            except Exception as e:
                logger.warning(f"[HskCorpusService] imported 回调异常: {e}")

    @classmethod
    def instance(cls) -> "HskCorpusService":
        """返回进程级单例(首次调用时创建)。"""
        if cls._instance is None:
            with cls._instanceLock:
                if cls._instance is None:
                    cls._instance = HskCorpusService()
        return cls._instance

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------
    @property
    def dbPath(self) -> Path:
        return self._dbPath

    def setDbPath(self, path) -> None:
        """允许外部覆盖 db 路径(测试 / 用户自定义)。"""
        self._dbPath = Path(path)

    # ------------------------------------------------------------------
    # Schema 管理
    # ------------------------------------------------------------------
    def ensureSchema(self) -> None:
        """启动期建表 + 建索引(幂等,IF NOT EXISTS)。"""
        try:
            self._dbPath.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        conn = sqlite3.connect(str(self._dbPath), timeout=10.0)
        try:
            conn.executescript(_buildSchemaSql())
            conn.execute(
                "INSERT OR REPLACE INTO hsk_corpus_meta(key, value) "
                "VALUES('schema_version', ?)",
                (str(self._schemaVersion),),
            )
            conn.commit()
            logger.info(f"[HskCorpusService] schema 已就绪: db={self._dbPath}")
        finally:
            conn.close()
        # 通知订阅者
        self._emitSchemaReady()

    def rowCount(self) -> int:
        """总行数(若 db 不存在返回 0)。"""
        if not self._dbPath.exists():
            return 0
        conn = sqlite3.connect(str(self._dbPath), timeout=10.0)
        try:
            cur = conn.execute("SELECT COUNT(*) AS n FROM hsk_corpus")
            return int(cur.fetchone()[0])
        finally:
            conn.close()

    def isAvailable(self) -> bool:
        """db 文件存在且含数据(可被 UI 检索)。"""
        return self._dbPath.exists() and self.rowCount() > 0

    # ------------------------------------------------------------------
    # 列名 / 表头
    # ------------------------------------------------------------------
    def availableColumns(self) -> List[str]:
        """可检索列名(给 UI 下拉框)。"""
        return list(_SEARCHABLE_COLUMNS)

    def columnHeaderMap(self) -> Dict[str, str]:
        """列名 → 中文表头(给 QTableView headerData 用)。"""
        return dict(_CN_HEADER_MAP)

    def allColumns(self) -> List[str]:
        """所有列(含 id / imported_at),给 SELECT * 用。"""
        return list(_ALL_COLUMNS) + ["imported_at"]

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def search(
        self,
        column: str,
        keyword: str,
        pageSize: int = 1000,
        maxRows: Optional[int] = None,
    ) -> List[Dict]:
        """按列 LIKE 模糊检索(无返回上限,分页拉取后聚合返回)。

        设计:
            - 列名走白名单,杜绝 SQL 注入
            - LIKE 通配符转义后用 ESCAPE '\\',避免「输入 % 命中全部」
            - 分页拉取(SQLite 单次游标限制),Worker 可流式消费
            - 若指定 maxRows,在达到上限时立即停止(节省内存)
            - 单库 11337 行的 LIKE 全量检索实测 < 50ms,完全可接受

        Args:
            column:    列名(必须出现在 _SEARCHABLE_COLUMNS 中,否则抛 ValueError)
            keyword:   用户输入的关键词(空字符串返回空列表)
            pageSize:  分页每页条数,默认 1000(子线程内流式消费)
            maxRows:   最大返回行数(None = 不限)

        Returns:
            list[dict],每个 dict 一行,key 为列名
        """
        if column not in _SEARCHABLE_COLUMNS:
            raise ValueError(f"非法列名 {column!r},必须在 {_SEARCHABLE_COLUMNS} 中")
        if not keyword:
            return []
        pageSize = max(1, min(int(pageSize), 5000))

        conn = sqlite3.connect(str(self._dbPath), timeout=10.0, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            escaped = _escapeLike(keyword)
            likePattern = f"%{escaped}%"

            rows: List[Dict] = []
            offset = 0
            # 流式分页拉取,直到游标耗尽或达到 maxRows
            while True:
                # 如果已达 maxRows,提前结束
                if maxRows is not None and len(rows) >= maxRows:
                    break
                # 当前页请求条数(最后页可能不足)
                need = pageSize
                if maxRows is not None:
                    need = min(pageSize, maxRows - len(rows))

                sql = (
                    f"SELECT * FROM hsk_corpus "
                    f"WHERE {column} LIKE ? ESCAPE '\\' "
                    f"LIMIT ? OFFSET ?"
                )
                cur = conn.execute(sql, (likePattern, need, offset))
                pageRows = cur.fetchall()
                if not pageRows:
                    break
                rows.extend(dict(r) for r in pageRows)
                if len(pageRows) < need:
                    # 不足一页,说明游标已耗尽
                    break
                offset += need
            return rows
        finally:
            conn.close()

    def iterSearch(
        self,
        column: str,
        keyword: str,
        pageSize: int = 1000,
        maxRows: Optional[int] = None,
    ):
        """按列 LIKE 流式检索(生成器)。

        与 search() 区别:不一次性聚合返回,而是逐页 yield。
        Worker 用此方法可以一边读 DB 一边 push 到 UI,内存压力更小。

        Yields:
            list[dict]: 每页一批行,可能为空(流结束)
        """
        if column not in _SEARCHABLE_COLUMNS:
            raise ValueError(f"非法列名 {column!r},必须在 {_SEARCHABLE_COLUMNS} 中")
        if not keyword:
            return
        pageSize = max(1, min(int(pageSize), 5000))

        conn = sqlite3.connect(str(self._dbPath), timeout=10.0, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            escaped = _escapeLike(keyword)
            likePattern = f"%{escaped}%"

            offset = 0
            totalYielded = 0
            while True:
                if maxRows is not None and totalYielded >= maxRows:
                    break
                need = pageSize
                if maxRows is not None:
                    need = min(pageSize, maxRows - totalYielded)
                sql = (
                    f"SELECT * FROM hsk_corpus "
                    f"WHERE {column} LIKE ? ESCAPE '\\' "
                    f"LIMIT ? OFFSET ?"
                )
                cur = conn.execute(sql, (likePattern, need, offset))
                pageRows = cur.fetchall()
                if not pageRows:
                    break
                rows = [dict(r) for r in pageRows]
                totalYielded += len(rows)
                yield rows
                if len(rows) < need:
                    break
                offset += need
        finally:
            conn.close()

    def countMatches(self, column: str, keyword: str) -> int:
        """统计匹配行数(单独走 COUNT,避免 SELECT * 拉全行)。"""
        if column not in _SEARCHABLE_COLUMNS:
            raise ValueError(f"非法列名 {column!r}")
        if not keyword:
            return 0
        conn = sqlite3.connect(str(self._dbPath), timeout=10.0)
        try:
            escaped = _escapeLike(keyword)
            sql = (
                f"SELECT COUNT(*) AS n FROM hsk_corpus "
                f"WHERE {column} LIKE ? ESCAPE '\\'"
            )
            cur = conn.execute(sql, (f"%{escaped}%",))
            return int(cur.fetchone()[0])
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------
    def stats(self) -> Dict:
        """返回 db 状态快照(给 UI / 调试用)。"""
        return {
            "db_path": str(self._dbPath),
            "db_exists": self._dbPath.exists(),
            "row_count": self.rowCount() if self._dbPath.exists() else 0,
            "schema_version": self._schemaVersion,
            "searchable_columns": list(_SEARCHABLE_COLUMNS),
        }


# 模块级单例快捷名(推荐外部使用)
def _moduleInstance() -> HskCorpusService:
    return HskCorpusService.instance()


# 用 property 风格暴露快捷名,首次访问时才创建
class _ModuleProxy:
    """模块级代理对象,延迟创建单例(支持 `hskCorpusService.foo()` 调用)。"""

    def __getattr__(self, name):
        return getattr(_moduleInstance(), name)


hskCorpusService = _ModuleProxy()
