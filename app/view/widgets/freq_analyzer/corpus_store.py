# coding: utf-8
"""CorpusStoreV2 — 基于 SQLite + FTS5 的语料存储层

设计目标:
    - 持久化:语料在应用重启后仍可用,无需重复导入
    - 性能:使用 FTS5 倒排索引加速 KWIC 检索与词频统计
    - API 兼容:保持原 CorpusStore 的方法签名,UI 层无需感知变化
    - 可观测:实时统计文件数/字符数,保留清洗规则与启用状态

Schema:
    documents        — 原文表        (doc_id PK, file_name UNIQUE, raw_text, char_count, imported_at)
    documents_fts    — FTS5 虚拟表   (基于 documents.raw_text 建立倒排索引)
    clean_cache      — 清洗结果缓存  (file_name PK, cleaned_text, rule_hash)
    corpus_meta      — 元数据 KV 表  (key PK, value)

数据目录: <INSTALL_DIR>/datas/corpora/default.db

注: 所有数据路径(语料库/注册表/状态)由 app.core.utils.data_paths 集中管理,
    本模块不再硬编码任何路径常量。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PySide6.QtCore import QObject, Signal

from app.view.widgets.freq_analyzer.freq_engine import CleanRule, TextCleaner

logger = logging.getLogger(__name__)


# 语料库默认位置: <INSTALL_DIR>/datas/corpora/default.db
# 所有路径统一从 app.core.utils.data_paths 派生,严禁硬编码
from app.core.utils.data_paths import DEFAULT_CORPUS_FILE

DEFAULT_DB_PATH = DEFAULT_CORPUS_FILE


# ---------------------------------------------------------------------------
# Schema 定义
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
-- 原文表
CREATE TABLE IF NOT EXISTS documents (
    doc_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name   TEXT    NOT NULL UNIQUE,
    raw_text    TEXT    NOT NULL,
    char_count  INTEGER NOT NULL DEFAULT 0,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_documents_file_name ON documents(file_name);

-- FTS5 倒排索引(基于原文建立,tokenize='unicode61' 内置分词)
-- 注:对中文等非空格分隔语言,FTS5 默认分词效果有限;但可显著加速"包含某词"型查询
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    file_name,
    raw_text,
    content='documents',
    content_rowid='doc_id',
    tokenize='unicode61 remove_diacritics 2'
);

-- FTS5 ↔ documents 同步触发器
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, file_name, raw_text)
    VALUES (new.doc_id, new.file_name, new.raw_text);
END;
CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, file_name, raw_text)
    VALUES ('delete', old.doc_id, old.file_name, old.raw_text);
END;
CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, file_name, raw_text)
    VALUES ('delete', old.doc_id, old.file_name, old.raw_text);
    INSERT INTO documents_fts(rowid, file_name, raw_text)
    VALUES (new.doc_id, new.file_name, new.raw_text);
END;

-- 清洗结果缓存(按 file_name + rule_hash 缓存清洗后文本)
CREATE TABLE IF NOT EXISTS clean_cache (
    file_name    TEXT NOT NULL,
    rule_hash    TEXT NOT NULL,
    cleaned_text TEXT NOT NULL,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (file_name, rule_hash)
);

-- 分词结果缓存(按 text_hash + backend + model_version 缓存 token 列表)
-- 用于加速重复分词:同一文本只分词一次
CREATE TABLE IF NOT EXISTS token_cache (
    text_hash      TEXT NOT NULL,    -- sha1(text)[:16]
    backend_name   TEXT NOT NULL,    -- 'jieba' / 'char'
    model_version  TEXT NOT NULL,    -- 后端模型版本(用于失效检测)
    tokens_json    TEXT NOT NULL,    -- JSON array of tokens
    token_count    INTEGER NOT NULL,
    duration_ms    REAL NOT NULL,    -- 分词耗时(毫秒)
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (text_hash, backend_name, model_version)
);
CREATE INDEX IF NOT EXISTS idx_token_cache_backend
    ON token_cache(backend_name);

-- 语料元数据(清洗规则/启用状态/统计)
CREATE TABLE IF NOT EXISTS corpus_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# FTS5 可用性检测
# ---------------------------------------------------------------------------
def _check_fts5_support(db_path: Path) -> bool:
    """检测 SQLite 是否支持 FTS5 模块。Python 内置 sqlite3 自 3.9 起默认包含。"""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='fts5_test'"
            )
            cur.fetchone()
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts5_test USING fts5(x)")
            conn.execute("INSERT INTO fts5_test(x) VALUES ('hello world')")
            cur = conn.execute("SELECT x FROM fts5_test WHERE fts5_test MATCH 'hello'")
            cur.fetchone()
            conn.execute("DROP TABLE fts5_test")
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[FTS5] 检测失败: {e}")
        return False


# ---------------------------------------------------------------------------
# CorpusStoreV2
# ---------------------------------------------------------------------------
class CorpusStore(QObject):
    """基于 SQLite + FTS5 的语料存储。

    Signals:
        textsChanged:     文本新增/删除/清空时触发
        cleanRuleChanged: 清洗规则或启用状态变更时触发

    API(向后兼容 + 增强):
        addRawText(name, text)              添加或替换单文件语料
        removeRawText(name)                 删除指定文件语料
        clearAll()                          清空所有语料
        fileCount() -> int                  文件数
        totalChars() -> int                 总字符数(原文)
        effectiveTexts() -> Dict[str, str]  按清洗规则返回最终文本(带缓存)
        rawTexts -> Dict[str, str]          原文 Dict 视图(实时查表)
        cleanRule / cleanEnabled            属性
        setCleanEnabled(bool)               切换清洗开关
        setCleanRule(CleanRule)             设置清洗规则
        setCleanRuleEnabled(key, bool)      单独开关某条规则(保留兼容)
        kwicCandidates(searchWord) -> set   【新增】FTS5 加速:返回包含 searchWord 的文件集合
        ftsAvailable -> bool                【新增】FTS5 是否可用
        searchWithFts(searchWord, limit)    【新增】FTS5 MATCH 查询
        dbPath -> str                       【新增】数据库文件路径
    """

    textsChanged = Signal()
    cleanRuleChanged = Signal()

    def __init__(self, dbPath: Optional[Union[str, QObject]] = None, parent=None):
        """兼容两种调用模式:
        - CorpusStore() / CorpusStore(parent=QObject())
        - CorpusStore(dbPath='...') / CorpusStore(qObject) (旧接口传入 QObject 时被视为 parent)
        """
        # 如果第一个位置参数是 QObject 且未提供 parent,把它当作 parent
        if isinstance(dbPath, QObject) and parent is None:
            parent, dbPath = dbPath, None
        super().__init__(parent)
        self._dbPath: str = dbPath or str(DEFAULT_DB_PATH)
        # 数据库所在目录需先创建
        os.makedirs(os.path.dirname(self._dbPath), exist_ok=True)
        # FTS5 支持检测(若不支持则降级为 LIKE 查询)
        self._ftsAvailable: bool = _check_fts5_support(Path(self._dbPath))
        if not self._ftsAvailable:
            logger.warning(
                "[CorpusStore] 当前 SQLite 不支持 FTS5,KWIC 检索将降级为 LIKE 扫描"
            )

        # 单连接多线程并不安全,使用锁 + 检查点机制
        self._lock = threading.RLock()
        self._conn = self._connect()
        self._initSchema()

        # 内存缓存(避免每次访问都查 DB;但写入时立即刷新)
        self._cleaner: Optional[TextCleaner] = None

        # 加载清洗规则与启用状态
        self._cleanRule: CleanRule = self._loadCleanRule()
        self._cleanEnabled: bool = self._loadCleanEnabled()

        # 加载词性配置
        self._posTags: Optional[set] = self._loadPosTags()
        self._posEnabled: bool = self._loadPosEnabled()

        # 分词结果缓存(用于加速 HanLP 等慢速后端)
        # 跨会话持久化,HanLP 等的 token 化结果只计算一次
        try:
            from app.view.widgets.freq_analyzer.token_cache import TokenCache

            self._tokenCache: Optional[TokenCache] = TokenCache(self._conn, self._lock)
        except Exception as _e:
            logger.warning(f"[CorpusStore] TokenCache 初始化失败: {_e}")
            self._tokenCache = None

    # ---------------- 连接管理 ----------------
    def _connect(self) -> sqlite3.Connection:
        """建立 SQLite 连接。启用 WAL 模式提升并发性能。"""
        conn = sqlite3.connect(
            self._dbPath,
            check_same_thread=False,
            timeout=10.0,
        )
        conn.row_factory = sqlite3.Row
        # WAL 模式:读写并发,适合"主线程读 + 后台写"场景
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initSchema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()

    def close(self) -> None:
        """关闭数据库连接(应用退出时调用)。"""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ---------------- 属性 ----------------
    @property
    def dbPath(self) -> str:
        return self._dbPath

    @property
    def ftsAvailable(self) -> bool:
        return self._ftsAvailable

    @property
    def posTags(self) -> Optional[set]:
        """当前生效的词性标签集合(只读视图);空集合表示未启用。"""
        return set(self._posTags) if self._posTags else None

    @property
    def posEnabled(self) -> bool:
        return self._posEnabled

    @property
    def cleanRule(self) -> CleanRule:
        """返回清洗规则副本(避免外部 mutate 直接影响内部状态)。"""
        return self._deserializeRule(self._serializeRule(self._cleanRule))

    @cleanRule.setter
    def cleanRule(self, rule: CleanRule) -> None:
        self.setCleanRule(rule)

    @property
    def cleanEnabled(self) -> bool:
        return self._cleanEnabled

    @cleanEnabled.setter
    def cleanEnabled(self, value: bool) -> None:
        self.setCleanEnabled(value)

    @property
    def rawTexts(self) -> Dict[str, str]:
        """原文 Dict 视图:每次访问从 DB 实时读取,确保多源数据一致。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT file_name, raw_text FROM documents ORDER BY file_name"
            )
            return {row["file_name"]: row["raw_text"] for row in cur.fetchall()}

    # ---------------- 文本变更 ----------------
    def addRawText(self, fileName: str, text: str) -> None:
        """添加或替换单个文件语料。"""
        if not fileName:
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO documents(file_name, raw_text, char_count)
                VALUES(?, ?, ?)
                ON CONFLICT(file_name) DO UPDATE SET
                    raw_text = excluded.raw_text,
                    char_count = excluded.char_count,
                    imported_at = CURRENT_TIMESTAMP
                """,
                (fileName, text or "", len(text or "")),
            )
            # 失效该文件的清洗缓存(rule hash 不匹配)
            self._conn.execute(
                "DELETE FROM clean_cache WHERE file_name = ?",
                (fileName,),
            )
            self._conn.commit()
        self.textsChanged.emit()

    def removeRawText(self, fileName: str) -> None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM documents WHERE file_name = ?", (fileName,)
            )
            if cur.fetchone() is None:
                return
            self._conn.execute("DELETE FROM documents WHERE file_name = ?", (fileName,))
            self._conn.execute(
                "DELETE FROM clean_cache WHERE file_name = ?", (fileName,)
            )
            self._conn.commit()
        self.textsChanged.emit()

    def clearAll(self) -> None:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) AS n FROM documents")
            if cur.fetchone()["n"] == 0:
                return
            self._conn.execute("DELETE FROM documents")
            self._conn.execute("DELETE FROM clean_cache")
            self._conn.commit()
        self.textsChanged.emit()

    # ---------------- 清洗规则 ----------------
    @staticmethod
    def _serializeRule(rule: CleanRule) -> Dict[str, Any]:
        """把 CleanRule 序列化为 dict(用于 JSON 持久化)。"""
        return {
            "removeEnglish": bool(getattr(rule, "removeEnglish", False)),
            "removeDigits": bool(getattr(rule, "removeDigits", False)),
            "removePunct": bool(getattr(rule, "removePunct", False)),
            "removeWhitespace": bool(getattr(rule, "removeWhitespace", True)),
            "removeSpecialSymbols": bool(getattr(rule, "removeSpecialSymbols", False)),
            "customRemoveList": list(getattr(rule, "customRemoveList", []) or []),
            "customRegexList": list(getattr(rule, "customRegexList", []) or []),
            "replaceMap": dict(getattr(rule, "replaceMap", {}) or {}),
            "lowercase": bool(getattr(rule, "lowercase", False)),
        }

    @staticmethod
    def _deserializeRule(data: Dict[str, Any]) -> CleanRule:
        """从 dict 重建 CleanRule 实例。"""
        rule = CleanRule()
        for key, value in data.items():
            if hasattr(rule, key):
                setattr(rule, key, value)
        return rule

    @classmethod
    def _ruleHash(cls, rule: CleanRule) -> str:
        """生成清洗规则的指纹(用于缓存键)。"""
        payload = json.dumps(
            cls._serializeRule(rule), sort_keys=True, ensure_ascii=False
        )
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:16]

    def _loadCleanRule(self) -> CleanRule:
        with self._lock:
            cur = self._conn.execute(
                "SELECT value FROM corpus_meta WHERE key='clean_rule'"
            )
            row = cur.fetchone()
        if row is None:
            return CleanRule()
        try:
            return self._deserializeRule(json.loads(row["value"]))
        except Exception as e:
            logger.warning(f"[CorpusStore] 读取 clean_rule 失败: {e}")
            return CleanRule()

    def _loadCleanEnabled(self) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT value FROM corpus_meta WHERE key='clean_enabled'"
            )
            row = cur.fetchone()
        if row is None:
            return False
        return row["value"] == "1"

    def _saveCleanRule(self, rule: CleanRule) -> None:
        payload = json.dumps(self._serializeRule(rule), ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO corpus_meta(key, value) VALUES('clean_rule', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (payload,),
            )
            self._conn.commit()

    def _saveCleanEnabled(self, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO corpus_meta(key, value) VALUES('clean_enabled', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("1" if enabled else "0",),
            )
            self._conn.commit()

    def setCleanEnabled(self, enabled: bool) -> None:
        if enabled == self._cleanEnabled:
            return
        self._cleanEnabled = enabled
        self._saveCleanEnabled(enabled)
        self.cleanRuleChanged.emit()

    def setCleanRule(self, rule: CleanRule) -> None:
        """同步设置清洗规则(立即生效)

        适用于已经准备好缓存的场景;若语料较大且 cache 未预热,
        推荐使用 CleanCoordinator 异步路径以避免 UI 卡顿。
        """
        if self._ruleHash(rule) == self._ruleHash(self._cleanRule):
            return
        self._cleanRule = rule
        self._saveCleanRule(rule)
        self.cleanRuleChanged.emit()

    def applyCleanRuleAsync(
        self,
        rule: CleanRule,
        enabled: bool,
        ruleHash: Optional[str] = None,
    ) -> None:
        """由 CleanCoordinator 调用:在不触发 UI 阻塞的前提下切换规则

        工作流程:
            1. 仅更新内存中的 _cleanRule / _cleanEnabled
            2. 不持久化(由 Worker 异步完成)
            3. 不 emit cleanRuleChanged(由 Coordinator 在 Worker 完成后统一 emit)
        """
        if ruleHash is None:
            ruleHash = self._ruleHash(rule)
        # 仅当规则真的变化才更新(避免无意义的状态切换)
        if ruleHash != self._ruleHash(self._cleanRule):
            self._cleanRule = rule
        if enabled != self._cleanEnabled:
            self._cleanEnabled = enabled
        logger.debug(
            f"[CorpusStore] applyCleanRuleAsync: hash={ruleHash[:8]} enabled={enabled}"
        )

    def setCleanRuleEnabled(self, key: str, enabled: bool) -> None:
        """单条清洗规则开关(向后兼容:通过 setattr 实现)。"""
        if not hasattr(self._cleanRule, key):
            logger.warning(f"[CorpusStore] setCleanRuleEnabled: 未知的规则属性 {key!r}")
            return
        setattr(self._cleanRule, key, bool(enabled))
        self._saveCleanRule(self._cleanRule)
        self.cleanRuleChanged.emit()

    # ---------------- 词性配置 ----------------
    def _loadPosTags(self) -> Optional[set]:
        """从 corpus_meta 读取词性标签集合(逗号分隔字符串)。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT value FROM corpus_meta WHERE key='pos_tags'"
            )
            row = cur.fetchone()
        if row is None or not row["value"]:
            return None
        items = [t.strip() for t in row["value"].split(",") if t.strip()]
        return set(items) if items else None

    def _loadPosEnabled(self) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT value FROM corpus_meta WHERE key='pos_enabled'"
            )
            row = cur.fetchone()
        if row is None:
            return False
        return row["value"] == "1"

    def _savePosTags(self, posTags: Optional[set]) -> None:
        if posTags:
            value = ",".join(sorted(posTags))
        else:
            value = ""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO corpus_meta(key, value) VALUES('pos_tags', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (value,),
            )
            self._conn.commit()

    def _savePosEnabled(self, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO corpus_meta(key, value) VALUES('pos_enabled', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("1" if enabled else "0",),
            )
            self._conn.commit()

    def setPosTags(
        self, posTags: Optional[set], enabled: Optional[bool] = None
    ) -> None:
        """运行时更新词性过滤配置。

        Args:
            posTags: 仅保留的词性标签集合;None 或空表示关闭过滤
            enabled: 显式开关;若为 None 则根据 posTags 是否非空自动判断
        """
        newSet = set(posTags) if posTags else None
        newEnabled = bool(enabled) if enabled is not None else bool(newSet)
        # 空集合视为关闭
        if not newSet:
            newEnabled = False

        if newSet == self._posTags and newEnabled == self._posEnabled:
            return
        self._posTags = newSet
        self._posEnabled = newEnabled
        self._savePosTags(newSet)
        self._savePosEnabled(newEnabled)
        # 复用 cleanRuleChanged 信号:POS 变更也属于「分析参数变更」一类
        self.cleanRuleChanged.emit()

    # ---------------- 派生数据 ----------------
    def _cleanerInstance(self) -> TextCleaner:
        if self._cleaner is None:
            self._cleaner = TextCleaner()
        return self._cleaner

    def _cleanOne(self, rawText: str) -> str:
        cleaner = self._cleanerInstance()
        if cleaner.rule is not self._cleanRule:
            cleaner.setRule(self._cleanRule)
        return cleaner.clean(rawText)

    def effectiveTexts(self) -> Dict[str, str]:
        """根据当前清洗规则返回最终文本,带按规则指纹的缓存。

        性能:首次访问会清洗全部文本;之后只要规则不变,直接从缓存读取。
        """
        if not self._cleanEnabled:
            return self.rawTexts

        ruleHash = self._ruleHash(self._cleanRule)
        cleaner = self._cleanerInstance()
        if cleaner.rule is not self._cleanRule:
            cleaner.setRule(self._cleanRule)

        result: Dict[str, str] = {}
        # 读取所有原文 + 已缓存的清洗结果
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT d.file_name, d.raw_text, c.cleaned_text
                FROM documents d
                LEFT JOIN clean_cache c
                  ON c.file_name = d.file_name AND c.rule_hash = ?
                ORDER BY d.file_name
                """,
                (ruleHash,),
            )
            rows = cur.fetchall()

        toCache: List[Tuple[str, str, str]] = []  # (file_name, rule_hash, cleaned)
        for row in rows:
            fileName = row["file_name"]
            raw = row["raw_text"]
            cached = row["cleaned_text"]
            if cached is not None:
                result[fileName] = cached
            else:
                cleaned = cleaner.clean(raw)
                result[fileName] = cleaned
                toCache.append((fileName, ruleHash, cleaned))

        if toCache:
            with self._lock:
                self._conn.executemany(
                    """
                    INSERT INTO clean_cache(file_name, rule_hash, cleaned_text)
                    VALUES(?, ?, ?)
                    ON CONFLICT(file_name, rule_hash) DO UPDATE SET
                        cleaned_text = excluded.cleaned_text,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    toCache,
                )
                self._conn.commit()
        return result

    def fileCount(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) AS n FROM documents")
            return int(cur.fetchone()["n"])

    def totalChars(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COALESCE(SUM(char_count), 0) AS s FROM documents"
            )
            return int(cur.fetchone()["s"])

    # ---------------- Token 缓存接口 ----------------
    def tokenCache(self):
        """获取分词缓存管理器(可能为 None,如初始化失败)

        用途:避免同一文本重复分词,加速二次分析
        """
        return getattr(self, "_tokenCache", None)

    def flushTokenCache(self, maxWait: float = 0.5):
        """强制刷新 token cache 待写入队列"""
        cache = self.tokenCache()
        if cache is not None:
            cache.flush(maxWait=maxWait)

    # ---------------- FTS5 加速接口 ----------------
    def kwicCandidates(self, searchWord: str) -> List[str]:
        """【FTS5 加速】返回包含 searchWord 的文件列表。

        用于 KWIC 检索的"候选文件筛选":先通过 FTS5 MATCH 找出哪些文件可能命中,
        避免对所有文件做线性扫描。

        Args:
            searchWord: 检索词;若含 FTS5 特殊字符会被安全转义
        Returns:
            文件名列表(已去重)
        """
        if not searchWord or not self._ftsAvailable:
            return self.allFileNames()
        # 转义 FTS5 特殊字符(用双引号包裹 + 内部双引号转义)
        escaped = '"' + searchWord.replace('"', '""') + '"'
        try:
            with self._lock:
                cur = self._conn.execute(
                    """
                    SELECT DISTINCT d.file_name
                    FROM documents_fts f
                    JOIN documents d ON d.doc_id = f.rowid
                    WHERE documents_fts MATCH ?
                    ORDER BY d.file_name
                    """,
                    (escaped,),
                )
                return [row["file_name"] for row in cur.fetchall()]
        except sqlite3.OperationalError as e:
            logger.warning(f"[FTS5] kwicCandidates fallback: {e}")
            return self.allFileNames()

    def searchWithFts(self, searchWord: str, limit: int = 500) -> List[Tuple[str, int]]:
        """【FTS5 加速】返回每个文件中 searchWord 的出现次数估计。

        Args:
            searchWord: 检索词
            limit: 返回文件数上限
        Returns:
            [(file_name, estimated_count), ...]  按估计次数降序
        """
        if not searchWord or not self._ftsAvailable:
            return []
        escaped = '"' + searchWord.replace('"', '""') + '"'
        try:
            with self._lock:
                cur = self._conn.execute(
                    """
                    SELECT d.file_name,
                           LENGTH(d.raw_text) - LENGTH(REPLACE(d.raw_text, ?, '')) AS approx
                    FROM documents d
                    WHERE d.doc_id IN (
                        SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?
                    )
                    ORDER BY approx DESC
                    LIMIT ?
                    """,
                    (searchWord, escaped, int(limit)),
                )
                return [
                    (row["file_name"], int(row["approx"])) for row in cur.fetchall()
                ]
        except sqlite3.OperationalError as e:
            logger.warning(f"[FTS5] searchWithFts fallback: {e}")
            return []

    def allFileNames(self) -> List[str]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT file_name FROM documents ORDER BY file_name"
            )
            return [row["file_name"] for row in cur.fetchall()]

    def getRawText(self, fileName: str) -> Optional[str]:
        """获取单个文件原文。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT raw_text FROM documents WHERE file_name = ?", (fileName,)
            )
            row = cur.fetchone()
        return row["raw_text"] if row else None

    # ---------------- 诊断 / 调试 ----------------
    def stats(self) -> Dict[str, Any]:
        """返回当前语料库统计快照(用于 UI 显示 / 调试)。"""
        with self._lock:
            docCount = self.fileCount()
            totalChars = self.totalChars()
            cur = self._conn.execute("SELECT COUNT(*) AS n FROM clean_cache")
            cacheEntries = int(cur.fetchone()["n"])
        return {
            "db_path": self._dbPath,
            "fts_available": self._ftsAvailable,
            "doc_count": docCount,
            "total_chars": totalChars,
            "clean_cache_entries": cacheEntries,
            "clean_enabled": self._cleanEnabled,
        }
