# coding: utf-8
"""统一数据路径管理 — 所有数据文件(语料库、注册表、状态、导出)的权威路径

目录结构(开发模式与打包后模式保持一致):
    <INSTALL_DIR>/
    ├── config/                 ← 配置文件(config.json)
    ├── download/               ← 用户下载的语料原始文件
    ├── logs/                   ← 应用日志
    └── datas/                  ← 【本模块管理】所有运行时数据
        ├── corpora_registry.db ← 语料库注册表(全局唯一)
        ├── corpus_state.json   ← 当前/上次活动语料库记忆
        ├── corpora/            ← 各语料库独立 SQLite 数据库
        │   ├── default.db
        │   └── ...
        └── exports/            ← 预留:导出文件(报告/图表/CSV)
            ├── reports/
            ├── charts/
            └── csv/

设计原则:
    1. **唯一权威**: 所有模块都通过本模块的常量访问路径,严禁硬编码
    2. **自动迁移**: 启动时自动从旧路径(<INSTALL_DIR>/app/data/)迁移数据到新位置
    3. **集中管理**: 新增数据类别只需在本模块添加路径常量

依赖关系:
    setting.py    → 定义 INSTALL_DIR / DATA_FOLDER
    data_paths.py → 本模块,所有数据路径的权威
    corpus_*      → 引入本模块的路径常量
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Final

from .setting import CONFIG_FOLDER, DATA_FOLDER, INSTALL_DIR

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from loguru import logger


# ---------------------------------------------------------------------------
# 路径常量(所有数据文件路径的权威来源)
# ---------------------------------------------------------------------------

# 数据根目录:<INSTALL_DIR>/datas/
DATA_DIR: Final[Path] = DATA_FOLDER

# 语料库注册表:全局唯一,记录所有语料库的元信息
CORPORA_REGISTRY_DB: Final[Path] = DATA_DIR / "corpora_registry.db"

# 当前/上次活动语料库的状态记忆文件(JSON)
CORPUS_STATE_FILE: Final[Path] = DATA_DIR / "corpus_state.json"

# 各语料库独立 SQLite 数据库目录
CORPORA_DIR: Final[Path] = DATA_DIR / "corpora"

# 默认语料库文件名
DEFAULT_CORPUS_NAME: Final[str] = "default"
DEFAULT_CORPUS_FILE: Final[Path] = CORPORA_DIR / "default.db"

# 词频分析高级设置记忆文件(JSON)
FREQ_ANALYZER_SETTINGS_FILE: Final[Path] = DATA_DIR / "freq_analyzer_settings.json"

# 导出文件目录(预留,本模块不强制约束内部结构)
EXPORTS_DIR: Final[Path] = DATA_DIR / "exports"
EXPORT_REPORTS_DIR: Final[Path] = EXPORTS_DIR / "reports"
EXPORT_CHARTS_DIR: Final[Path] = EXPORTS_DIR / "charts"
EXPORT_CSV_DIR: Final[Path] = EXPORTS_DIR / "csv"

# PRD-002 研究项目(REQ-PROJ-001)
# - 项目元数据 + 资源索引 SQLite(单一权威)
# - 每个项目的物理快照文件夹(便于备份/分享/.prisma 导出)
PROJECTS_DB: Final[Path] = DATA_DIR / "projects.db"
PROJECTS_DIR: Final[Path] = DATA_DIR / "projects"

# 当前激活项目 id 记忆(JSON)
PROJECT_STATE_FILE: Final[Path] = DATA_DIR / "project_state.json"

# HSK 作文语料专用库(独立 db 文件,与 default.db 平级)
# 用途:静态检索语料(从 Excel 一次性导入 + 全文本列 NOCASE 索引)
# 与 CorpusStore 的 FTS5 互补:本库只读,适合「按列 LIKE 模糊查询」场景
HSK_CORPUS_DB: Final[Path] = CORPORA_DIR / "hsk_corpus.db"
HSK_CORPUS_SCHEMA_VERSION: Final[int] = 1


# ---------------------------------------------------------------------------
# 旧路径(用于迁移)
# ---------------------------------------------------------------------------

# 旧版数据根目录:<INSTALL_DIR>/app/data/
# (重写前语料数据库存放处)
_LEGACY_APP_DATA_DIR: Final[Path] = INSTALL_DIR / "app" / "data"

# 旧版单库路径(用于迁移到 corpora/default.db)
_LEGACY_SINGLE_DB: Final[Path] = _LEGACY_APP_DATA_DIR / "corpus.db"

# 旧版注册表路径
_LEGACY_REGISTRY_DB: Final[Path] = _LEGACY_APP_DATA_DIR / "corpora_registry.db"

# 旧版状态文件
_LEGACY_STATE_FILE: Final[Path] = _LEGACY_APP_DATA_DIR / "corpus_state.json"

# 旧版 corpora 目录
_LEGACY_CORPORA_DIR: Final[Path] = _LEGACY_APP_DATA_DIR / "corpora"


# ---------------------------------------------------------------------------
# 目录初始化 + 自动迁移
# ---------------------------------------------------------------------------


def ensureDataDirs() -> None:
    """确保所有数据目录存在(幂等,可重复调用)

    同时执行:
        1. 创建新目录结构
        2. 检测旧路径并自动迁移数据到新位置
    """
    # 1. 确保新目录存在
    for d in (
        DATA_DIR,
        CORPORA_DIR,
        EXPORTS_DIR,
        EXPORT_REPORTS_DIR,
        EXPORT_CHARTS_DIR,
        EXPORT_CSV_DIR,
        # PRD-002:研究项目目录
        PROJECTS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)

    # 2. 自动迁移(如果有旧数据)
    _migrateLegacyData()


def _migrateLegacyData() -> None:
    """从旧路径 <INSTALL_DIR>/app/data/ 迁移到新路径 <INSTALL_DIR>/datas/

    迁移项目:
        - corpus.db                    → datas/corpora/default.db(若注册表为空)
        - corpora_registry.db          → datas/corpora_registry.db(直接覆盖/合并)
        - corpus_state.json            → datas/corpus_state.json
        - corpora/ 下所有 *.db         → datas/corpora/*.db

    原则:
        - 旧文件不存在 → 跳过
        - 新文件已存在 → 不覆盖,保留新位置的数据(避免破坏用户数据)
        - 迁移成功后删除旧文件(节省空间,避免下次重复迁移)
        - 任意步骤失败 → logger.warning,不阻断程序启动
    """
    if not _LEGACY_APP_DATA_DIR.exists():
        return

    migrated = []

    # 1. 旧注册表 → 新注册表
    if _LEGACY_REGISTRY_DB.exists() and not CORPORA_REGISTRY_DB.exists():
        try:
            shutil.copy2(str(_LEGACY_REGISTRY_DB), str(CORPORA_REGISTRY_DB))
            migrated.append(f"registry: {_LEGACY_REGISTRY_DB} → {CORPORA_REGISTRY_DB}")
        except Exception as e:
            logger.warning(f"[DataPaths] 迁移注册表失败: {e}")

    # 2. 旧状态文件 → 新状态文件
    if _LEGACY_STATE_FILE.exists() and not CORPUS_STATE_FILE.exists():
        try:
            shutil.copy2(str(_LEGACY_STATE_FILE), str(CORPUS_STATE_FILE))
            migrated.append(f"state: {_LEGACY_STATE_FILE} → {CORPUS_STATE_FILE}")
        except Exception as e:
            logger.warning(f"[DataPaths] 迁移状态文件失败: {e}")

    # 3. 旧 corpora 目录 → 新 corpora 目录
    if _LEGACY_CORPORA_DIR.exists():
        for legacy_db in _LEGACY_CORPORA_DIR.glob("*.db"):
            target_db = CORPORA_DIR / legacy_db.name
            if target_db.exists():
                continue  # 新位置已有同名文件,不覆盖
            try:
                shutil.copy2(str(legacy_db), str(target_db))
                # 同时迁移 -shm / -wal 副文件
                for suffix in ("-shm", "-wal"):
                    src = Path(str(legacy_db) + suffix)
                    if src.exists():
                        shutil.copy2(str(src), str(target_db) + suffix)
                migrated.append(f"corpus: {legacy_db} → {target_db}")
            except Exception as e:
                logger.warning(f"[DataPaths] 迁移语料库失败 {legacy_db}: {e}")

    # 4. 旧单库 corpus.db → 新 corpora/default.db(仅当注册表为空时)
    if _LEGACY_SINGLE_DB.exists() and not DEFAULT_CORPUS_FILE.exists():
        try:
            shutil.copy2(str(_LEGACY_SINGLE_DB), str(DEFAULT_CORPUS_FILE))
            for suffix in ("-shm", "-wal"):
                src = Path(str(_LEGACY_SINGLE_DB) + suffix)
                if src.exists():
                    shutil.copy2(str(src), str(DEFAULT_CORPUS_FILE) + suffix)
            migrated.append(f"single-db: {_LEGACY_SINGLE_DB} → {DEFAULT_CORPUS_FILE}")
        except Exception as e:
            logger.warning(f"[DataPaths] 迁移旧单库失败: {e}")

    # 5. 清理旧文件(只在成功迁移后才删除,避免误删)
    if migrated:
        logger.info(
            f"[DataPaths] 已迁移 {len(migrated)} 项数据到新位置 datas/: {migrated}"
        )
        _cleanupLegacyFiles()


def _cleanupLegacyFiles() -> None:
    """清理已迁移的旧文件(在迁移成功后调用)

    仅删除那些在新位置已确认存在的文件。
    """
    cleanup_targets = []
    if _LEGACY_REGISTRY_DB.exists() and CORPORA_REGISTRY_DB.exists():
        cleanup_targets.append(_LEGACY_REGISTRY_DB)
    if _LEGACY_STATE_FILE.exists() and CORPUS_STATE_FILE.exists():
        cleanup_targets.append(_LEGACY_STATE_FILE)
    if _LEGACY_SINGLE_DB.exists() and DEFAULT_CORPUS_FILE.exists():
        cleanup_targets.append(_LEGACY_SINGLE_DB)
        cleanup_targets.append(Path(str(_LEGACY_SINGLE_DB) + "-shm"))
        cleanup_targets.append(Path(str(_LEGACY_SINGLE_DB) + "-wal"))
    if _LEGACY_CORPORA_DIR.exists():
        for legacy_db in _LEGACY_CORPORA_DIR.glob("*.db"):
            if (CORPORA_DIR / legacy_db.name).exists():
                cleanup_targets.append(legacy_db)
                cleanup_targets.append(Path(str(legacy_db) + "-shm"))
                cleanup_targets.append(Path(str(legacy_db) + "-wal"))

    for path in cleanup_targets:
        try:
            os.remove(path)
        except OSError:
            pass

    # 如果旧 corpora 目录为空,尝试删除
    if _LEGACY_CORPORA_DIR.exists():
        try:
            _LEGACY_CORPORA_DIR.rmdir()
        except OSError:
            pass

    # 如果旧 app/data 目录为空,尝试删除
    if _LEGACY_APP_DATA_DIR.exists():
        try:
            _LEGACY_APP_DATA_DIR.rmdir()
        except OSError:
            pass


# 模块导入时自动执行目录初始化(轻量、可重复)
ensureDataDirs()
