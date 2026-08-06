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
    - searchByScore()   按分数列做区间检索(支持 70-85 / >=70 / <85 等格式)

可检索列白名单(8 列,文本 3 + 分数 5):
    - 国籍 / 证书级别 / 作文题目
    - 听力理解分数 / 阅读理解分数 / 综合表达考试分数 / 口试分数 / 作文分数

分数列检索:
    - 用户输入形如 70-85 / 70~85 / 70 to 85 / >=70 / <85 等区间字符串
    - parseScoreRange() 解析为 {"min": int|None, "max": int|None}
    - SQL 走 >= / <= 范围比较,11337 行 < 5ms

注:
    - db schema 仍保留全部列,只是 UI 不开放其他列的检索入口
    - 列名走白名单(`_SEARCHABLE_COLUMNS`),杜绝 SQL 注入
    - 用户输入的 LIKE 通配符 `%` / `_` 会被转义为反斜杠形式,
      配 `ESCAPE '\\'` 使用,避免「输入 % 命中全部」
    - 子线程调用方应自行开新连接(`check_same_thread=False`),
      本 service 不缓存连接
"""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional

from app.core.utils import logger

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

# 分数列(走区间检索,不走 LIKE)
_SCORE_COLUMNS: List[str] = [
    "听力理解分数",
    "阅读理解分数",
    "综合表达考试分数",
    "口试分数",
    "作文分数",
]

# 文本列(走 LIKE 模糊检索)
_TEXT_COLUMNS: List[str] = [
    "国籍",
    "证书级别",
    "作文题目",
]

# 空值哨兵:UI 选择「证书级别 = 无」时,在 conditions 里以这个 keyword 传递,
# service 端识别后转写为 `col IS NULL OR TRIM(col) = ''`。
# 不使用空串 / None,避免被当作"未填写条件"过滤掉。
_EMPTY_KEYWORD_SENTINEL: str = "__EMPTY__"


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
# 分数区间解析
# ---------------------------------------------------------------------------
# 支持以下写法(全部大小写不敏感,允许中英文符号):
#   70-85   /   70~85   /   70—85   /   70~85
#   >=70    /   >=70-<=85   /   >70-<85
#   <85     /   <=85
#   70      (单值 → 区间 [70, 70])
#   , 70-85 (允许首尾空白)
#
# 解析返回 dict: {"min": int|None, "max": int|None, "raw": str}
#   - min 为 None 表示无下界
#   - max 为 None 表示无上界
#   - raw 是清洗后的输入(供 UI 反馈)
_SCORE_RANGE_PATTERN = re.compile(
    r"""
    \s*
    (?: >= | > | <= | < )?       # 允许单边比较符开头
    \s*
    -?\d+                          # 第一个数字
    \s*
    (?: - | ~ | — | – | to )      # 分隔符
    \s*
    -?\d+                          # 第二个数字
    |
    \s*
    (?: >= | > )                   # 大于
    \s*
    -?\d+
    |
    \s*
    (?: <= | < )                   # 小于
    \s*
    -?\d+
    |
    \s*
    -?\d+                          # 纯数字
    \s*
    """,
    re.VERBOSE | re.IGNORECASE,
)


def parseScoreRange(raw: str) -> Dict[str, Optional[int]]:
    """解析分数区间字符串。

    支持写法(全部可选空白):
        - "70-85" / "70~85" / "70—85" / "70–85" / "70 to 85"
        - ">=70-<=85" / ">70-<85" / ">=70" / "<85" / ">70" / "<=85"
        - "70"(单值 → [70, 70])
        - 空字符串 → {"min": None, "max": None, "raw": ""}

    Returns:
        {"min": int | None, "max": int | None, "raw": str}
        若解析失败,抛出 ValueError(包含原始输入,便于 UI 反馈)。
    """
    if raw is None:
        return {"min": None, "max": None, "raw": ""}
    text = str(raw).strip()
    if not text:
        return {"min": None, "max": None, "raw": ""}

    # 统一分隔符:—、–、to、~ 都视作 "-"
    normalized = (
        text.replace("—", "-")
        .replace("–", "-")
        .replace("~", "-")
        .replace("to", "-", 1)
        .replace("TO", "-", 1)
        .replace("To", "-", 1)
    )
    # 去掉空格
    normalized = re.sub(r"\s+", "", normalized)

    # 形如 ">=70-<=85" / ">70-<85":拆分为两段
    m = re.match(
        r"^(?P<op1>>=|<=|>|<)(?P<n1>-?\d+)-(?P<op2>>=|<=|>|<)(?P<n2>-?\d+)$",
        normalized,
    )
    if m:
        op1, n1 = m.group("op1"), m.group("n1")
        op2, n2 = m.group("op2"), m.group("n2")
        # 形如 ">70-<=85":op1='>', n1='70', op2='<=', n2='85'
        lo: Optional[float]
        hi: Optional[float]
        if op1 == ">=":
            lo = float(n1)
        elif op1 == ">":
            lo = float(n1) + 1
        elif op1 == "<=":
            hi = float(n1)  # 左闭右闭区间 "<=70-<=85" 不规范,但宽松处理
            lo = None
        else:  # op1 == "<"
            hi = float(n1) - 1
            lo = None
        # 上界
        if op2 == "<=":
            hi = float(n2)
        elif op2 == "<":
            hi = float(n2) - 1
        elif op2 == ">=":
            lo = float(n2)
        else:  # op2 == ">"
            lo = float(n2) + 1
        return {
            "min": int(lo) if lo is not None else None,
            "max": int(hi) if hi is not None else None,
            "raw": text,
        }

    # 形如 "70-85"
    m = re.match(r"^(?P<lo>-?\d+)-(?P<hi>-?\d+)$", normalized)
    if m:
        lo = int(m.group("lo"))
        hi = int(m.group("hi"))
        if lo > hi:
            raise ValueError(f"区间下限大于上限:{text!r}")
        return {"min": lo, "max": hi, "raw": text}

    # 形如 ">=70" / ">70"
    m = re.match(r"^(?P<op>>=|<=|>|<)(?P<n>-?\d+)$", normalized)
    if m:
        op, n = m.group("op"), m.group("n")
        num = int(n)
        if op == ">=":
            return {"min": num, "max": None, "raw": text}
        if op == ">":
            return {"min": num + 1, "max": None, "raw": text}
        if op == "<=":
            return {"min": None, "max": num, "raw": text}
        if op == "<":
            return {"min": None, "max": num - 1, "raw": text}

    # 形如 "70"(单值)
    m = re.match(r"^-?\d+$", normalized)
    if m:
        num = int(normalized)
        return {"min": num, "max": num, "raw": text}

    raise ValueError(f"无法解析的分数区间:{text!r}")


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

    def scoreColumns(self) -> List[str]:
        """分数列名(支持区间检索)。"""
        return list(_SCORE_COLUMNS)

    def textColumns(self) -> List[str]:
        """文本列名(走 LIKE 模糊检索)。"""
        return list(_TEXT_COLUMNS)

    def isScoreColumn(self, column: str) -> bool:
        """判定给定列名是否为分数列。"""
        return column in _SCORE_COLUMNS

    # ------------------------------------------------------------------
    # 多条件组合检索(AND)— 支持多列筛选
    # ------------------------------------------------------------------
    # 条件数据结构:
    #   {"type": "text",    "column": "国籍",         "keyword": "日本"}
    #   {"type": "cert",    "column": "证书级别",     "keyword": "A"}
    #   {"type": "country", "column": "国籍",         "keyword": "日本"}
    #   {"type": "score",   "column": "听力理解分数", "min": 70, "max": 100}
    #
    # 所有条件之间 AND 拼接,空条件会被忽略。
    def _buildConditionsWhere(self, conditions: List[Dict]) -> tuple:
        """根据条件列表构造 WHERE 子句与绑定值。

        Returns:
            (where_sql: str, params: list)
            where_sql 已做白名单校验,可直接拼到 SELECT 语句里
            params 为对应位置的绑定值(顺序与占位符一致)
            当无有效条件时,where_sql = "1=1"
        """
        clauses: List[str] = []
        params: List = []
        for cond in conditions or []:
            if not isinstance(cond, dict):
                continue
            col = cond.get("column")
            ctype = cond.get("type")
            if not col or col not in _SEARCHABLE_COLUMNS:
                # 非法列名直接跳过(由调用方做预校验)
                continue
            if ctype == "score" or col in _SCORE_COLUMNS:
                lo = cond.get("min")
                hi = cond.get("max")
                if lo is None and hi is None:
                    continue
                if lo is not None:
                    clauses.append(f"{col} >= ?")
                    params.append(int(lo))
                if hi is not None:
                    clauses.append(f"{col} <= ?")
                    params.append(int(hi))
            else:
                keyword = (cond.get("keyword") or "").strip()
                if not keyword:
                    continue
                # 空值哨兵 → 匹配 NULL / 空字符串(专用于「证书级别 = 无」)
                if keyword == _EMPTY_KEYWORD_SENTINEL:
                    clauses.append(f"({col} IS NULL OR TRIM(COALESCE({col}, '')) = '')")
                    # 无需绑定参数,但保持 params 列表与占位符数量一致
                    continue
                escaped = _escapeLike(keyword)
                clauses.append(f"{col} LIKE ? ESCAPE '\\'")
                params.append(f"%{escaped}%")
        if not clauses:
            return "1=1", []
        return " AND ".join(clauses), params

    def searchByConditions(
        self,
        conditions: List[Dict],
        pageSize: int = 1000,
        maxRows: Optional[int] = None,
    ) -> List[Dict]:
        """按多条件组合(AND)分页检索。

        Args:
            conditions: 条件列表(由 UI 收集),结构见 _buildConditionsWhere
            pageSize:   每页大小
            maxRows:    最大返回行数

        Returns:
            list[dict]
        """
        whereSql, params = self._buildConditionsWhere(conditions)
        # 没有有效条件 → 全表(一般 UI 不会触发,但兜底)
        pageSize = max(1, min(int(pageSize), 5000))

        conn = sqlite3.connect(str(self._dbPath), timeout=10.0, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row

            rows: List[Dict] = []
            offset = 0
            while True:
                if maxRows is not None and len(rows) >= maxRows:
                    break
                need = pageSize
                if maxRows is not None:
                    need = min(pageSize, maxRows - len(rows))
                sql = (
                    f"SELECT * FROM hsk_corpus "
                    f"WHERE {whereSql} "
                    f"LIMIT ? OFFSET ?"
                )
                cur = conn.execute(sql, (*params, need, offset))
                pageRows = cur.fetchall()
                if not pageRows:
                    break
                rows.extend(dict(r) for r in pageRows)
                if len(pageRows) < need:
                    break
                offset += need
            return rows
        finally:
            conn.close()

    def iterSearchByConditions(
        self,
        conditions: List[Dict],
        pageSize: int = 1000,
        maxRows: Optional[int] = None,
    ):
        """按多条件组合(AND)流式检索生成器。"""
        whereSql, params = self._buildConditionsWhere(conditions)
        pageSize = max(1, min(int(pageSize), 5000))

        conn = sqlite3.connect(str(self._dbPath), timeout=10.0, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
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
                    f"WHERE {whereSql} "
                    f"LIMIT ? OFFSET ?"
                )
                cur = conn.execute(sql, (*params, need, offset))
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

    def countByConditions(self, conditions: List[Dict]) -> int:
        """多条件匹配行数统计(主线程用,期望 < 50ms)。"""
        whereSql, params = self._buildConditionsWhere(conditions)
        conn = sqlite3.connect(str(self._dbPath), timeout=10.0)
        try:
            sql = f"SELECT COUNT(*) AS n FROM hsk_corpus WHERE {whereSql}"
            cur = conn.execute(sql, tuple(params))
            return int(cur.fetchone()[0])
        finally:
            conn.close()

    def iterZwhaoByConditions(
        self,
        conditions: List[Dict],
        pageSize: int = 1000,
    ) -> Iterator[List[str]]:
        """按多条件组合(AND)流式 yield「作文母号」列。

        Args:
            conditions: 与 iterSearchByConditions 相同的条件结构
            pageSize:   每页返回多少个 zwhao(默认 1000)

        Yields:
            list[str]: 一批 zwhao(可能为空 = 流结束)
        """
        whereSql, params = self._buildConditionsWhere(conditions)
        pageSize = max(1, min(int(pageSize), 5000))

        conn = sqlite3.connect(
            str(self._dbPath), timeout=10.0, check_same_thread=False
        )
        try:
            offset = 0
            while True:
                sql = (
                    f"SELECT 作文母号 FROM hsk_corpus "
                    f"WHERE {whereSql} AND 作文母号 IS NOT NULL "
                    f"LIMIT ? OFFSET ?"
                )
                cur = conn.execute(sql, (*params, pageSize, offset))
                pageRows = [r[0] for r in cur.fetchall() if r[0]]
                if not pageRows:
                    break
                yield pageRows
                if len(pageRows) < pageSize:
                    break
                offset += pageSize
        finally:
            conn.close()

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
    # 分数区间检索(score 列专用)
    # ------------------------------------------------------------------
    def _buildScoreRangeWhere(
        self, column: str, rangeDict: Dict[str, Optional[int]]
    ) -> tuple:
        """根据区间 dict 构造 WHERE 子句与绑定值。

        Returns:
            (where_sql: str, params: list)
            where_sql 已做白名单校验,可直接拼到 SELECT 语句里
            params 为对应位置的绑定值(顺序与占位符一致)
        """
        if column not in _SCORE_COLUMNS:
            raise ValueError(f"非分数列 {column!r},不能使用区间检索")
        lo = rangeDict.get("min")
        hi = rangeDict.get("max")
        clauses: List[str] = []
        params: List = []
        if lo is not None:
            clauses.append(f"{column} >= ?")
            params.append(int(lo))
        if hi is not None:
            clauses.append(f"{column} <= ?")
            params.append(int(hi))
        if not clauses:
            return "1=1", []
        return " AND ".join(clauses), params

    def searchByScore(
        self,
        column: str,
        rangeDict: Dict[str, Optional[int]],
        pageSize: int = 1000,
        maxRows: Optional[int] = None,
    ) -> List[Dict]:
        """按分数列做区间检索(走范围比较,不走 LIKE)。

        Args:
            column:    分数列名(必须出现在 _SCORE_COLUMNS 中)
            rangeDict: 由 parseScoreRange() 返回的 dict
                       {"min": int|None, "max": int|None, "raw": str}
                       若 min 与 max 均为 None,返回空列表(避免无意义全表扫描)
            pageSize:  分页大小
            maxRows:   最大返回行数

        Returns:
            list[dict]
        """
        if column not in _SCORE_COLUMNS:
            raise ValueError(f"非分数列 {column!r},必须在 {_SCORE_COLUMNS} 中")
        if not rangeDict:
            return []
        if rangeDict.get("min") is None and rangeDict.get("max") is None:
            return []
        pageSize = max(1, min(int(pageSize), 5000))

        whereSql, params = self._buildScoreRangeWhere(column, rangeDict)

        conn = sqlite3.connect(str(self._dbPath), timeout=10.0, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row

            rows: List[Dict] = []
            offset = 0
            while True:
                if maxRows is not None and len(rows) >= maxRows:
                    break
                need = pageSize
                if maxRows is not None:
                    need = min(pageSize, maxRows - len(rows))
                sql = (
                    f"SELECT * FROM hsk_corpus "
                    f"WHERE {whereSql} "
                    f"LIMIT ? OFFSET ?"
                )
                cur = conn.execute(sql, (*params, need, offset))
                pageRows = cur.fetchall()
                if not pageRows:
                    break
                rows.extend(dict(r) for r in pageRows)
                if len(pageRows) < need:
                    break
                offset += need
            return rows
        finally:
            conn.close()

    def iterSearchByScore(
        self,
        column: str,
        rangeDict: Dict[str, Optional[int]],
        pageSize: int = 1000,
        maxRows: Optional[int] = None,
    ):
        """按分数列做区间检索的生成器版本。"""
        if column not in _SCORE_COLUMNS:
            raise ValueError(f"非分数列 {column!r},必须在 {_SCORE_COLUMNS} 中")
        if not rangeDict:
            return
        if rangeDict.get("min") is None and rangeDict.get("max") is None:
            return
        pageSize = max(1, min(int(pageSize), 5000))

        whereSql, params = self._buildScoreRangeWhere(column, rangeDict)

        conn = sqlite3.connect(str(self._dbPath), timeout=10.0, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
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
                    f"WHERE {whereSql} "
                    f"LIMIT ? OFFSET ?"
                )
                cur = conn.execute(sql, (*params, need, offset))
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

    def countMatchesByScore(
        self, column: str, rangeDict: Dict[str, Optional[int]]
    ) -> int:
        """分数区间匹配行数统计(主线程用,期望 < 50ms)。"""
        if column not in _SCORE_COLUMNS:
            raise ValueError(f"非分数列 {column!r}")
        if not rangeDict:
            return 0
        if rangeDict.get("min") is None and rangeDict.get("max") is None:
            return 0
        whereSql, params = self._buildScoreRangeWhere(column, rangeDict)
        conn = sqlite3.connect(str(self._dbPath), timeout=10.0)
        try:
            sql = f"SELECT COUNT(*) AS n FROM hsk_corpus WHERE {whereSql}"
            cur = conn.execute(sql, tuple(params))
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
            "score_columns": list(_SCORE_COLUMNS),
            "text_columns": list(_TEXT_COLUMNS),
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
