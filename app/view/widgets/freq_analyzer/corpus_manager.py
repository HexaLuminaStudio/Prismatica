# coding: utf-8
"""多语料库调度器

需求文档:
    test/6D-CorpusClient_需求文档_v3.md §2.3.1 自建语料库 / §2.3.1.2 语料库管理

设计目标:
    - 用户可同时维护多个语料库(例如「HSK-A级」、「学术汉语」、「新闻语料」)
    - 每个语料库 = 一个独立的 SQLite 数据库文件
    - 所有语料库的元信息统一登记在「注册表」中
    - 上次打开的语料库 + 当前活动语料库会被记忆到配置,程序下次启动自动加载

模块拆分:
    - CorpusRegistry       语料库注册表(列表/新建/删除/改默认)
    - CorpusManager        高级入口:负责选择/切换语料库 + 持久化记忆
    - CorpusSwitcherWidget Qt UI 组件:下拉选择 + 新建/删除按钮
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from app.core.utils import logger


# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
# 所有数据路径统一从 app.core.utils.data_paths 派生
# (避免在此处硬编码,确保与 setting.py 保持一致)
from app.core.utils.data_paths import (
    CORPORA_DIR,
    CORPORA_REGISTRY_DB,
    CORPUS_STATE_FILE,
    DEFAULT_CORPUS_FILE,
    DEFAULT_CORPUS_NAME,
)

REGISTRY_DB_PATH = CORPORA_REGISTRY_DB


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class CorpusInfo:
    """语料库元信息(对应注册表一行)

    Fields:
        id:          自增主键
        name:        用户可读的语料库名(全局唯一)
        dbPath:      对应的 SQLite 文件绝对路径
        description: 备注/描述
        createdAt:   ISO 字符串
        updatedAt:   ISO 字符串
        fileCount:   冗余字段,用于 UI 列表展示(由 manager 异步刷新)
        totalChars:  同上
    """

    id: int = 0
    name: str = ""
    dbPath: str = ""
    description: str = ""
    createdAt: str = ""
    updatedAt: str = ""
    fileCount: int = 0
    totalChars: int = 0

    def toDict(self) -> Dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# 注册表(纯 SQLite 操作,不依赖 Qt)
# ---------------------------------------------------------------------------
class CorpusRegistry:
    """语料库注册表 - 持久化所有已创建的语料库元信息

    表结构:
        corpora(id PK, name UNIQUE, db_path, description, created_at, updated_at)
    """

    SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS corpora (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT    NOT NULL UNIQUE,
        db_path      TEXT    NOT NULL,
        description  TEXT    NOT NULL DEFAULT '',
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_corpora_name ON corpora(name);
    """

    def __init__(self, registryPath: Optional[str] = None):
        self._path = Path(registryPath) if registryPath else REGISTRY_DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(self.SCHEMA_SQL)
        self._conn.commit()
        # 首次启动:迁移旧库 + 创建默认库
        # 注意:旧路径 → 新路径的迁移逻辑已统一在 data_paths.ensureDataDirs() 中
        # 完成(模块导入时自动执行)。此处不再重复迁移代码。
        self._ensureDefaultCorpus()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ---------------- 默认语料库 ----------------
    def _ensureDefaultCorpus(self) -> None:
        """确保存在 default 语料库(供程序初次启动使用)"""
        if self.getByName(DEFAULT_CORPUS_NAME) is not None:
            return
        CORPORA_DIR.mkdir(parents=True, exist_ok=True)
        dbPath = str(DEFAULT_CORPUS_FILE)
        # 创建空 db 文件以避免初次启动延迟
        Path(dbPath).touch(exist_ok=True)
        self.create(
            name=DEFAULT_CORPUS_NAME,
            dbPath=dbPath,
            description="默认语料库(导入新文件即可使用)",
        )

    # ---------------- CRUD ----------------
    def list(self) -> List[CorpusInfo]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, name, db_path, description, created_at, updated_at "
                "FROM corpora ORDER BY id ASC"
            )
            return [
                CorpusInfo(
                    id=row["id"],
                    name=row["name"],
                    dbPath=row["db_path"],
                    description=row["description"] or "",
                    createdAt=str(row["created_at"] or ""),
                    updatedAt=str(row["updated_at"] or ""),
                )
                for row in cur.fetchall()
            ]

    def getById(self, corpusId: int) -> Optional[CorpusInfo]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, name, db_path, description, created_at, updated_at "
                "FROM corpora WHERE id = ?",
                (corpusId,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return CorpusInfo(
            id=row["id"],
            name=row["name"],
            dbPath=row["db_path"],
            description=row["description"] or "",
            createdAt=str(row["created_at"] or ""),
            updatedAt=str(row["updated_at"] or ""),
        )

    def getByName(self, name: str) -> Optional[CorpusInfo]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, name, db_path, description, created_at, updated_at "
                "FROM corpora WHERE name = ?",
                (name,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return CorpusInfo(
            id=row["id"],
            name=row["name"],
            dbPath=row["db_path"],
            description=row["description"] or "",
            createdAt=str(row["created_at"] or ""),
            updatedAt=str(row["updated_at"] or ""),
        )

    def create(
        self,
        name: str,
        dbPath: str,
        description: str = "",
    ) -> CorpusInfo:
        """创建语料库注册项(只写注册表,db 文件可由 CorpusStore 后续创建)

        Args:
            name: 语料库名(必须唯一,1-32 字符)
            dbPath: 对应 SQLite 文件的绝对路径
            description: 可选描述

        Returns:
            CorpusInfo

        Raises:
            ValueError: name 为空、过长、已存在或 dbPath 非法
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("语料库名不能为空")
        if len(name) > 32:
            raise ValueError("语料库名不能超过 32 个字符")
        if "/" in name or "\\" in name or ":" in name:
            raise ValueError("语料库名不能包含路径分隔符")
        if not dbPath:
            raise ValueError("数据库路径不能为空")

        dbPath = str(Path(dbPath).resolve())
        Path(dbPath).parent.mkdir(parents=True, exist_ok=True)
        Path(dbPath).touch(exist_ok=True)

        if self.getByName(name) is not None:
            raise ValueError(f"语料库名已存在: {name}")

        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO corpora(name, db_path, description) VALUES(?, ?, ?)",
                (name, dbPath, description),
            )
            self._conn.commit()
            newId = int(cur.lastrowid or 0)

        info = self.getById(newId)
        assert info is not None
        return info

    def delete(self, corpusId: int, deleteDbFile: bool = False) -> bool:
        """删除语料库注册项

        Args:
            corpusId: 注册表 id
            deleteDbFile: 是否同时删除物理 db 文件
                - True: 物理文件被删除,数据不可恢复
                - False: 仅注销,数据保留,后续可重新导入
        """
        info = self.getById(corpusId)
        if info is None:
            return False
        if info.name == DEFAULT_CORPUS_NAME:
            raise ValueError("默认语料库不可删除(可重命名或清空文件)")

        with self._lock:
            self._conn.execute("DELETE FROM corpora WHERE id = ?", (corpusId,))
            self._conn.commit()

        if deleteDbFile:
            for p in (info.dbPath, info.dbPath + "-shm", info.dbPath + "-wal"):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError as e:
                    logger.warning(f"[CorpusRegistry] 删除文件失败 {p}: {e}")
        return True

    def rename(self, corpusId: int, newName: str) -> CorpusInfo:
        info = self.getById(corpusId)
        if info is None:
            raise ValueError("语料库不存在")
        newName = (newName or "").strip()
        if not newName:
            raise ValueError("新名称不能为空")
        if newName == info.name:
            return info
        if self.getByName(newName) is not None:
            raise ValueError(f"语料库名已存在: {newName}")
        with self._lock:
            self._conn.execute(
                "UPDATE corpora SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (newName, corpusId),
            )
            self._conn.commit()
        return self.getById(corpusId)  # type: ignore[return-value]

    def updateDescription(self, corpusId: int, description: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE corpora SET description = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (description, corpusId),
            )
            self._conn.commit()


# ---------------------------------------------------------------------------
# 全局管理器(QObject,带 Qt 信号)
# ---------------------------------------------------------------------------
class CorpusManager(QObject):
    """语料库管理器(QObject,便于 UI 监听)

    Signals:
        activeCorpusChanged(int)   当前活动语料库 id 变更
        registryChanged()           语料库列表变化(新建/删除)
        statsUpdated(int)           指定语料库的统计信息(fileCount/totalChars)已刷新

    工作模式:
        - 通过 setActive(corpusId) 切换活动语料库
        - UI 端订阅 activeCorpusChanged,然后重新创建/替换 CorpusStore
        - 内部使用 last_active.json 持久化「当前语料库 + 上次语料库」
    """

    activeCorpusChanged = Signal(int)
    registryChanged = Signal()
    statsUpdated = Signal(int)

    # 记忆文件: <INSTALL_DIR>/datas/corpus_state.json
    _STATE_FILE = CORPUS_STATE_FILE

    def __init__(self, registry: Optional[CorpusRegistry] = None, parent=None):
        # 防止调用者把 QObject parent 当作 registry 传入
        if registry is not None and not isinstance(registry, CorpusRegistry):
            raise TypeError(
                f"[CorpusManager] 第一个参数必须是 CorpusRegistry 或 None,"
                f" 实际收到 {type(registry).__name__}。"
                f" 如要设置 Qt parent,请使用关键字参数 parent=..."
            )
        super().__init__(parent)
        self._registry = registry or CorpusRegistry()
        self._activeId: int = 0
        self._lastId: int = 0  # 「上次」语料库(关闭前正在使用)
        self._loadState()
        self._normalizeState()

    # ---------------- 状态持久化 ----------------
    def _loadState(self) -> None:
        try:
            if not self._STATE_FILE.exists():
                return
            data = json.loads(self._STATE_FILE.read_text(encoding="utf-8"))
            self._activeId = int(data.get("active_id") or 0)
            self._lastId = int(data.get("last_id") or 0)
        except Exception as e:
            logger.warning(f"[CorpusManager] 读取状态文件失败: {e}")

    def _saveState(self) -> None:
        try:
            self._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {"active_id": self._activeId, "last_id": self._lastId}
            self._STATE_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"[CorpusManager] 保存状态失败: {e}")

    def _normalizeState(self) -> None:
        """清理已删除语料库留下的活动/上次 id，并持久化有效回退项。"""
        items = self._registry.list()
        validIds = {item.id for item in items}
        fallbackId = items[0].id if items else 0
        normalizedActiveId = (
            self._activeId if self._activeId in validIds else fallbackId
        )
        normalizedLastId = (
            self._lastId if self._lastId in validIds else normalizedActiveId
        )
        if (
            normalizedActiveId == self._activeId
            and normalizedLastId == self._lastId
        ):
            return
        self._activeId = normalizedActiveId
        self._lastId = normalizedLastId
        self._saveState()

    # ---------------- 访问器 ----------------
    @property
    def registry(self) -> CorpusRegistry:
        return self._registry

    def listAll(self) -> List[CorpusInfo]:
        return self._registry.list()

    def activeCorpus(self) -> Optional[CorpusInfo]:
        if self._activeId <= 0:
            return None
        return self._registry.getById(self._activeId)

    def lastCorpus(self) -> Optional[CorpusInfo]:
        """返回上次(关闭前)使用的语料库,与 active 可能不同"""
        if self._lastId <= 0 or self._lastId == self._activeId:
            return self.activeCorpus()
        return self._registry.getById(self._lastId)

    def activeDbPath(self) -> Optional[str]:
        info = self.activeCorpus()
        return info.dbPath if info else None

    # ---------------- 切换 ----------------
    def setActive(self, corpusId: int) -> CorpusInfo:
        info = self._registry.getById(corpusId)
        if info is None:
            raise ValueError(f"语料库不存在: id={corpusId}")
        if corpusId != self._activeId:
            self._lastId = self._activeId
            self._activeId = corpusId
            self._saveState()
            self.activeCorpusChanged.emit(corpusId)
        return info

    def setActiveByName(self, name: str) -> CorpusInfo:
        info = self._registry.getByName(name)
        if info is None:
            raise ValueError(f"语料库不存在: {name}")
        return self.setActive(info.id)

    def activateLast(self) -> Optional[CorpusInfo]:
        """切换回「上次」语料库(用户主动要求)"""
        if self._lastId <= 0 or self._lastId == self._activeId:
            return self.activeCorpus()
        if self._registry.getById(self._lastId) is None:
            self._lastId = self._activeId
            self._saveState()
            return self.activeCorpus()
        return self.setActive(self._lastId)

    # ---------------- CRUD 封装 ----------------
    def createCorpus(
        self,
        name: str,
        dbPath: Optional[str] = None,
        description: str = "",
    ) -> CorpusInfo:
        """创建并注册新语料库(自动切换为活动)"""
        if not dbPath:
            CORPORA_DIR.mkdir(parents=True, exist_ok=True)
            dbPath = str(CORPORA_DIR / f"{name}.db")
        info = self._registry.create(name=name, dbPath=dbPath, description=description)
        self.setActive(info.id)  # 创建后自动切换
        self.registryChanged.emit()
        return info

    def deleteCorpus(self, corpusId: int, deleteDbFile: bool = False) -> bool:
        info = self._registry.getById(corpusId)
        if info is None:
            return False
        wasActive = self._activeId == corpusId
        self._registry.delete(corpusId, deleteDbFile=deleteDbFile)
        if wasActive:
            remaining = self._registry.list()
            if remaining:
                self._activeId = remaining[0].id
                self._lastId = self._activeId
            else:
                self._activeId = 0
                self._lastId = 0
            self._saveState()
            self.activeCorpusChanged.emit(self._activeId)
        elif self._lastId == corpusId:
            self._lastId = self._activeId
            self._saveState()
        self.registryChanged.emit()
        return True

    def renameCorpus(self, corpusId: int, newName: str) -> CorpusInfo:
        info = self._registry.rename(corpusId, newName)
        self.registryChanged.emit()
        return info

    def updateStats(self, corpusId: int, fileCount: int, totalChars: int) -> None:
        """UI 端在语料变化时调用,触发 statsUpdated 信号"""
        self.statsUpdated.emit(corpusId)
