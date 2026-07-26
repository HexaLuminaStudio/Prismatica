# coding: utf-8
"""研究项目管理服务(PRD-002 REQ-PROJ-001)

ProjectManager 是 QObject 单例,负责:
    - 项目 CRUD(创建/查询/重命名/删除)
    - 资源归档 addResource(由 ResourceSinkMixin 触发)
    - AI 解读归档 addAiInsight(由 ResearchReportService 触发)
    - 持久化:SQLite(projects.db) + 文件夹(<PROJECTS_DIR>/<id>/)
    - 跨模块信号:activeProjectChanged / projectListChanged

存储布局(SQLite 为权威 + JSON 冗余):
    <INSTALL_DIR>/datas/
    ├── projects.db                       ← 项目元数据 + 资源 + AI 解读
    ├── projects/<id>/
    │   ├── project.json                  ← Project 完整对象(冗余 / 备份)
    │   ├── resources/<uuid>.json         ← 资源物理快照
    │   ├── notes/                        ← MVP 占位
    │   └── insights/                     ← MVP 占位
    └── project_state.json                ← 当前激活项目 id

持久化:
    - **SQLite (单一事实源)**:projects / project_resources / project_ai_insights 三表
      + _loadAllProjectsFromDb() 反序列化到内存
    - project.json 仍写入,作为离线备份/分享格式
    - _saveProjectJson 一直存在,只是不再依赖它来加载

并发:write 操作加 threading.RLock(可重入);SQLite 用 DELETE journal + 默认事务模式,
在 Windows 上行为可预期。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QThread, Signal

from app.core.models.project import (
    AiInsight,
    CorpusRef,
    Project,
    RESOURCE_STATUS_NEW,
    Resource,
    genId,
    projectToDict,
)
from app.core.utils.data_paths import (
    PROJECTS_DB,
    PROJECTS_DIR,
    PROJECT_STATE_FILE,
)
from loguru import logger


def _nowIso() -> str:
    """返回 ISO8601 字符串(本地时区)"""
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    tags TEXT,
    template TEXT,
    version TEXT,
    schema_version INTEGER,
    status TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at DESC);

CREATE TABLE IF NOT EXISTS project_resources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    type TEXT,
    title TEXT,
    summary TEXT,
    parameters TEXT,
    tags TEXT,
    status TEXT,
    created_at TEXT,
    snapshot_rel_path TEXT,
    thumbnail_rel_path TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_resources_project ON project_resources(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS project_ai_insights (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    analysis_type TEXT,
    content TEXT,
    citations TEXT,
    confidence TEXT,
    model TEXT,
    resource_id TEXT,
    created_at TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ai_insights_project ON project_ai_insights(project_id, created_at DESC);
"""


# ---------------------------------------------------------------------------
# ProjectManager(QObject 单例)
# ---------------------------------------------------------------------------


class ProjectManager(QObject):
    """研究项目管理服务(单例)"""

    activeProjectChanged = Signal(str)  # 参数:project_id 或 ""
    projectListChanged = Signal()  # 新建/删除/重命名后触发

    _instance: Optional["ProjectManager"] = None
    _instanceLock = threading.Lock()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # 用 RLock(可重入)而非 Lock,防止嵌套 acquire 死锁:
        # _CreateProjectWorker.run() 持锁后调 _writeProjectMetaRow,
        # 内部又会 acquire — 改 Lock 会永久阻塞同一线程。
        self._dbLock = threading.RLock()
        self._memCache: Dict[str, Project] = {}  # id → Project(项目元数据缓存)
        self._activeProjectId: str = ""
        # 待回收的 create 任务 worker:worker → (onSuccess, onError)
        # 任务完成后由 _onCreateProjectFinished / _onCreateProjectFailed 弹出
        self._pendingCreateWorkers: Dict["_CreateProjectWorker", tuple] = {}
        self._initDb()
        self._loadAllProjectsFromDb()
        self._restoreActiveProject()

    # ------------------------------------------------------------------
    # 单例入口
    # ------------------------------------------------------------------
    @classmethod
    def instance(cls) -> "ProjectManager":
        """返回进程级单例(首次调用时创建)"""
        if cls._instance is None:
            with cls._instanceLock:
                if cls._instance is None:
                    cls._instance = ProjectManager()
        return cls._instance

    # ------------------------------------------------------------------
    # 初始化 / 持久化
    # ------------------------------------------------------------------
    def _connectDb(self) -> sqlite3.Connection:
        """创建并配置 SQLite 连接(每次调用获得新连接,避免跨线程问题)"""
        # 确保父目录存在
        PROJECTS_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(PROJECTS_DB), timeout=10.0, check_same_thread=False)
        # 使用 Python 默认事务模式(isolation_level="" 即 "" 表示自动 BEGIN/COMMIT)
        # 说明:之前用 isolation_level=None + PRAGMA journal_mode=WAL,
        #     在 Windows 上与「写后立刻用新连接读取」存在诡异的数据丢失。
        #     改为默认事务模式 + DELETE journal,确保持久化语义可预期。
        conn.isolation_level = ""
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.DatabaseError as e:
            logger.warning(f"[ProjectManager] 设置 PRAGMA 失败: {e}")
        return conn

    def _initDb(self) -> None:
        """初始化 SQLite schema"""
        try:
            with self._dbLock:
                conn = self._connectDb()
                try:
                    conn.executescript(_SCHEMA_SQL)
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            logger.exception(f"[ProjectManager] 初始化 SQLite 失败: {e}")

    def _loadAllProjectsFromDb(self) -> None:
        """启动时加载所有项目元数据 + 资源索引 + AI 解读 进内存缓存"""
        try:
            with self._dbLock:
                conn = self._connectDb()
                try:
                    rows = conn.execute(
                        "SELECT id, name, description, tags, template, version, "
                        "schema_version, status, created_at, updated_at "
                        "FROM projects"
                    ).fetchall()
                    resourceRows = conn.execute(
                        "SELECT id, project_id, type, title, summary, parameters, "
                        "tags, status, created_at, snapshot_rel_path, "
                        "thumbnail_rel_path FROM project_resources"
                    ).fetchall()
                    insightRows = conn.execute(
                        "SELECT id, project_id, analysis_type, content, citations, "
                        "confidence, model, resource_id, created_at "
                        "FROM project_ai_insights"
                    ).fetchall()
                finally:
                    conn.close()
            # 项目元数据
            for r in rows:
                project = Project(
                    id=r[0],
                    name=r[1],
                    description=r[2] or "",
                    tags=_jsonLoadList(r[3]),
                    template=r[4],
                    version=r[5] or "1.0.0",
                    schemaVersion=int(r[6] or 1),
                    status=r[7] or "active",
                    createdAt=r[8] or "",
                    updatedAt=r[9] or "",
                )
                self._memCache[project.id] = project
            # 资源 → project.resources
            for r in resourceRows:
                project = self._memCache.get(r[1])
                if project is None:
                    continue
                resource = Resource(
                    id=r[0],
                    type=r[2] or "",
                    title=r[3] or "",
                    summary=r[4] or "",
                    parameters=_jsonLoadDict(r[5]),
                    tags=_jsonLoadList(r[6]),
                    status=r[7] or RESOURCE_STATUS_NEW,
                    createdAt=r[8] or "",
                    snapshotRelPath=r[9] or "",
                    thumbnailRelPath=r[10],
                )
                project.resources.append(resource)
            # AI 解读 → project.aiInsights
            for r in insightRows:
                project = self._memCache.get(r[1])
                if project is None:
                    continue
                insight = AiInsight(
                    id=r[0],
                    analysisType=r[2] or "",
                    content=r[3] or "",
                    citations=_jsonLoadListOfDict(r[4]),
                    confidence=r[5] or "medium",
                    model=r[6] or "",
                    resourceId=r[7],
                    createdAt=r[8] or "",
                )
                project.aiInsights.append(insight)
            # 把每个项目的列表按 createdAt DESC 排好,UI 直接用
            for project in self._memCache.values():
                project.resources.sort(key=lambda x: x.createdAt or "", reverse=True)
                project.aiInsights.sort(key=lambda x: x.createdAt or "", reverse=True)
            logger.info(
                f"[ProjectManager] 加载项目 {len(self._memCache)} 个, "
                f"资源 {sum(len(p.resources) for p in self._memCache.values())} 条, "
                f"AI 解读 {sum(len(p.aiInsights) for p in self._memCache.values())} 条"
            )
        except Exception as e:
            logger.exception(f"[ProjectManager] 加载项目元数据失败: {e}")

    def _restoreActiveProject(self) -> None:
        """启动时恢复激活项目 id。

        优先级:
            1. project_state.json 里的 activeProjectId(若仍存在于 memCache)
            2. 否则取最近 updated_at 那个项目(用户希望"有项目就别空着")
            3. memCache 为空 → 不设置,保持无激活态
        注意:不在 __init__ 内 emit 信号(避免 QApplication 尚未启动的副作用),
        由 UI 在启动完成后主动调 refresh;但本方法**会**设置 self._activeProjectId,
        这样 projectManager.activeProject() 立刻可用。
        """
        # 1) 尝试从 project_state.json 恢复
        restoredFromState = False
        if PROJECT_STATE_FILE.exists():
            try:
                data = json.loads(PROJECT_STATE_FILE.read_text(encoding="utf-8"))
                activeId = (data.get("activeProjectId") or "").strip()
                if activeId and activeId in self._memCache:
                    self._activeProjectId = activeId
                    restoredFromState = True
                    logger.info(
                        f"[ProjectManager] 恢复激活项目 id={activeId}(来源 project_state.json)"
                    )
                elif activeId:
                    logger.info(
                        f"[ProjectManager] project_state.json 中 id={activeId} 不存在,忽略"
                    )
            except Exception as e:
                logger.warning(f"[ProjectManager] 读取 project_state.json 失败: {e}")

        if restoredFromState:
            return

        # 2) 没有有效 state 或 state 失效:挑最近更新的那个项目作为激活
        if self._memCache:
            # 按 updatedAt DESC 排序,取最新一个
            recent = max(
                self._memCache.values(),
                key=lambda p: (p.updatedAt or "", p.createdAt or ""),
            )
            self._activeProjectId = recent.id
            logger.info(
                f"[ProjectManager] 未找到有效 state,自动激活最近项目 "
                f"id={recent.id}, name={recent.name}"
            )
            # 把这个选择也回写到 state,保证下次启动一致
            self._saveActiveProjectState(recent.id)

    def _saveActiveProjectState(self, projectId: str) -> None:
        """把激活项目 id 写到 project_state.json"""
        try:
            PROJECT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            PROJECT_STATE_FILE.write_text(
                json.dumps(
                    {"activeProjectId": projectId}, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[ProjectManager] 写入 project_state.json 失败: {e}")

    def _projectDir(self, projectId: str) -> Path:
        """返回项目文件夹路径(不创建)"""
        return PROJECTS_DIR / projectId

    def _ensureProjectDirs(self, projectId: str) -> Path:
        """确保项目文件夹及其子目录存在,返回路径"""
        projectDir = self._projectDir(projectId)
        for sub in ("", "resources", "notes", "insights"):
            (projectDir / sub).mkdir(parents=True, exist_ok=True)
        return projectDir

    def _saveProjectJson(self, project: Project) -> None:
        """把 Project 完整对象写到项目文件夹下的 project.json"""
        try:
            projectDir = self._ensureProjectDirs(project.id)
            (projectDir / "project.json").write_text(
                json.dumps(projectToDict(project), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[ProjectManager] 写 project.json 失败: {e}")

    def _writeProjectMetaRow(self, project: Project) -> None:
        """写一行 projects 表(upsert)"""
        with self._dbLock:
            conn = self._connectDb()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO projects "
                    "(id, name, description, tags, template, version, "
                    " schema_version, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        project.id,
                        project.name,
                        project.description,
                        json.dumps(project.tags, ensure_ascii=False),
                        project.template,
                        project.version,
                        project.schemaVersion,
                        project.status,
                        project.createdAt,
                        project.updatedAt,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def _deleteProjectMetaRow(self, projectId: str) -> None:
        """从 SQLite 删除项目元数据(CASCADE 自动删除资源索引)"""
        with self._dbLock:
            conn = self._connectDb()
            try:
                conn.execute("DELETE FROM projects WHERE id = ?", (projectId,))
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # 公共 API:查询
    # ------------------------------------------------------------------
    def listProjects(self) -> List[Project]:
        """所有项目,按 updated_at DESC 排序(MVP 阶段从内存缓存返回)"""
        items = list(self._memCache.values())
        items.sort(key=lambda p: p.updatedAt or "", reverse=True)
        return items

    def getProject(self, projectId: str) -> Optional[Project]:
        """按 id 查项目(MVP 阶段返回元数据,不自动加载 resources)"""
        return self._memCache.get(projectId)

    def activeProject(self) -> Optional[Project]:
        """当前激活项目(若无返回 None)"""
        activeId = self._activeProjectId
        if not activeId:
            return None
        return self._memCache.get(activeId)

    @property
    def activeProjectId(self) -> str:
        """当前激活项目 id(只读属性)"""
        return self._activeProjectId

    # ------------------------------------------------------------------
    # 公共 API:变更
    # ------------------------------------------------------------------
    def setActiveProject(self, projectId: str) -> bool:
        """切换激活项目。projectId="" 表示清除激活。

        Returns:
            bool — 是否实际改变了状态
        """
        if projectId == self._activeProjectId:
            return False
        if projectId and projectId not in self._memCache:
            logger.warning(
                f"[ProjectManager] setActiveProject 收到未知 id: {projectId}"
            )
            return False
        self._activeProjectId = projectId
        self._saveActiveProjectState(projectId)
        logger.info(f"[ProjectManager] 切换激活项目: {projectId or '(无)'}")
        self.activeProjectChanged.emit(projectId)
        return True

    def createProject(
        self,
        name: str,
        template: Optional[str] = None,
        description: str = "",
    ) -> Project:
        """同步版创建项目入口(磁盘 I/O 在当前线程完成,返回创建的 Project)。

        注意:此方法在 UI 线程上做磁盘 I/O(创建目录、写 SQLite、写 JSON),
        在 Windows 上偶发「整个软件无响应」现象。UI 上推荐改用
        createProjectAsync(),由 worker 子线程完成 I/O,UI 线程保持响应。

        Args:
            name: 项目名(必填,已 strip)
            template: 来源模板名(可选)
            description: 一句话描述(可选)

        Raises:
            ValueError: 当 name 为空或仅包含空白字符时(由 UI 层 NewProjectDialog
                        的 _onNameChanged 校验挡住,正常 UI 流程不会触发)
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("项目名不能为空")
        project = Project(
            id=genId(),
            name=name,
            description=description,
            template=template,
            createdAt=_nowIso(),
            updatedAt=_nowIso(),
        )
        self._persistProjectSync(project)
        self._memCache[project.id] = project
        self.setActiveProject(project.id)
        logger.info(f"[ProjectManager] 创建项目: id={project.id}, name={name}")
        self.projectListChanged.emit()
        return project

    def createProjectAsync(
        self,
        name: str,
        template: Optional[str] = None,
        description: str = "",
        onSuccess: Optional[Any] = None,
        onError: Optional[Any] = None,
    ) -> None:
        """异步创建项目(磁盘 I/O 全部跑在 QThread 子线程,不阻塞 UI)。

        调用后立即返回;真正创建完成后,会在 UI 线程上回调:
            - onSuccess(project: Project)   成功
            - onError(errorMsg: str)        失败
        信号 activeProjectChanged / projectListChanged 也在 UI 线程发射。

        Args:
            name: 项目名
            template: 来源模板名(可选)
            description: 一句话描述(可选)
            onSuccess: 成功回调,签名 (Project) -> None
            onError:   失败回调,签名 (str) -> None

        Note:
            空名会在主线程同步抛出 ValueError(由 UI 层 NewProjectDialog
            的 _onNameChanged 校验挡住,正常 UI 流程不会触发)。
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("项目名不能为空")
        now = _nowIso()
        project = Project(
            id=genId(),
            name=name,
            description=description,
            template=template,
            createdAt=now,
            updatedAt=now,
        )

        worker = _CreateProjectWorker(self, project)
        # 持有引用,避免 GC;worker 完成后由 _onCreateFinished 释放
        self._pendingCreateWorkers[worker] = (onSuccess, onError)
        worker.finishedWithResult.connect(self._onCreateProjectFinished)
        worker.failed.connect(self._onCreateProjectFailed)
        # finished / failed 任意一个触发都回收 worker
        worker.finishedWithResult.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        # 启动子线程,执行 SQLite INSERT + 文件夹创建 + project.json 写入
        worker.start()
        logger.info(
            f"[ProjectManager] 已启动异步创建任务: id={project.id}, name={name}"
        )

    def _onCreateProjectFinished(self, project: "Project") -> None:
        """worker 完成回调(主线程)— 缓存 + 切激活 + emit 信号 + 调用 onSuccess"""
        # 从 pending 字典取出回调并清理
        worker = self.sender()
        callbacks: Optional[tuple] = None
        if worker is not None and worker in self._pendingCreateWorkers:
            callbacks = self._pendingCreateWorkers.pop(worker)
        # 内存缓存
        self._memCache[project.id] = project
        # 切到新项目(会 emit activeProjectChanged,在主线程)
        self.setActiveProject(project.id)
        # 通知列表刷新
        self.projectListChanged.emit()
        logger.info(
            f"[ProjectManager] 异步创建完成: id={project.id}, name={project.name}"
        )
        if callbacks and callbacks[0] is not None:
            try:
                callbacks[0](project)
            except Exception as e:
                logger.exception(
                    f"[ProjectManager] createProjectAsync onSuccess 回调异常: {e}"
                )

    def _onCreateProjectFailed(self, errMsg: str) -> None:
        """worker 失败回调(主线程)— 调用 onError"""
        worker = self.sender()
        callbacks: Optional[tuple] = None
        if worker is not None and worker in self._pendingCreateWorkers:
            callbacks = self._pendingCreateWorkers.pop(worker)
        logger.warning(f"[ProjectManager] 异步创建失败: {errMsg}")
        if callbacks and callbacks[1] is not None:
            try:
                callbacks[1](errMsg)
            except Exception as e:
                logger.exception(
                    f"[ProjectManager] createProjectAsync onError 回调异常: {e}"
                )

    def _persistProjectSync(self, project: "Project") -> None:
        """在当前线程同步执行创建项目的所有磁盘 I/O(供 createProject 调用)。"""
        with self._dbLock:
            self._writeProjectMetaRow(project)
            self._saveProjectJson(project)

    def renameProject(self, projectId: str, newName: str) -> bool:
        """重命名项目(空名拒绝)"""
        newName = (newName or "").strip()
        if not newName:
            logger.warning("[ProjectManager] renameProject 拒绝空名")
            return False
        project = self._memCache.get(projectId)
        if project is None:
            logger.warning(f"[ProjectManager] renameProject 找不到项目: {projectId}")
            return False
        if project.name == newName:
            return False
        project.name = newName
        project.updatedAt = _nowIso()
        with self._dbLock:
            self._writeProjectMetaRow(project)
            self._saveProjectJson(project)
        logger.info(f"[ProjectManager] 重命名项目: {projectId} → {newName}")
        # 若改名的是当前激活项目,通知 UI 刷新顶栏
        if self._activeProjectId == projectId:
            self.activeProjectChanged.emit(projectId)
        self.projectListChanged.emit()
        return True

    def deleteProject(self, projectId: str) -> bool:
        """删除项目(级联删除 SQLite 行 + 项目文件夹)"""
        if projectId not in self._memCache:
            return False
        # 1) 如果是当前激活项目,先清除激活
        if self._activeProjectId == projectId:
            self.setActiveProject("")
        # 2) 从 SQLite 删除
        self._deleteProjectMetaRow(projectId)
        # 3) 从内存缓存删除
        del self._memCache[projectId]
        # 4) 删除文件夹(容错:失败仅 warn)
        projectDir = self._projectDir(projectId)
        if projectDir.exists():
            try:
                import shutil

                shutil.rmtree(projectDir, ignore_errors=True)
            except Exception as e:
                logger.warning(f"[ProjectManager] 删除项目文件夹失败: {e}")
        logger.info(f"[ProjectManager] 删除项目: {projectId}")
        self.projectListChanged.emit()
        return True

    # ------------------------------------------------------------------
    # 公共 API:资源归档
    # ------------------------------------------------------------------
    def addResource(
        self,
        projectId: str,
        resourceType: str,
        title: str,
        summary: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        snapshotData: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[Resource]:
        """添加资源到项目(由 ResourceSinkMixin 调用)

        Args:
            projectId: 目标项目 id;若为空或项目不存在,返回 None
            resourceType: RESOURCE_TYPE_* 常量
            title: 资源标题(用户可读)
            summary: 200 字以内摘要
            parameters: 可复现参数字典
            snapshotData: 物理快照(序列化为 JSON 落到 resources/<uuid>.json)
            tags: 标签列表

        Returns:
            创建的 Resource 对象;若 projectId 无效则返回 None
        """
        project = self._memCache.get(projectId)
        if project is None:
            logger.debug(
                f"[ProjectManager] addResource 跳过: project={projectId} 不存在"
            )
            return None
        resourceId = genId()
        now = _nowIso()
        snapshotRel = f"resources/{resourceId}.json"
        # 1) 落物理快照
        try:
            self._ensureProjectDirs(projectId)
            payload = {
                "type": resourceType,
                "title": title,
                "summary": summary,
                "parameters": parameters or {},
                "snapshotData": snapshotData or {},
                "tags": tags or [],
                "createdAt": now,
            }
            (self._projectDir(projectId) / snapshotRel).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[ProjectManager] 写资源快照失败: {e}")
            return None
        # 2) 构造 Resource 对象
        resource = Resource(
            id=resourceId,
            type=resourceType,
            title=title,
            summary=summary,
            parameters=parameters or {},
            tags=tags or [],
            createdAt=now,
            snapshotRelPath=snapshotRel,
        )
        # 3) 更新内存中的 Project + 持久化
        project.resources.append(resource)
        project.updatedAt = now
        with self._dbLock:
            # 写资源索引到 SQLite(默认事务模式由 Python 管理)
            try:
                conn = self._connectDb()
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO project_resources "
                        "(id, project_id, type, title, summary, parameters, "
                        " tags, status, created_at, snapshot_rel_path, "
                        " thumbnail_rel_path) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            resource.id,
                            project.id,
                            resource.type,
                            resource.title,
                            resource.summary,
                            json.dumps(
                                resource.parameters,
                                ensure_ascii=False,
                                default=str,
                            ),
                            json.dumps(resource.tags, ensure_ascii=False),
                            resource.status,
                            resource.createdAt,
                            resource.snapshotRelPath,
                            resource.thumbnailRelPath,
                        ),
                    )
                    conn.execute(
                        "UPDATE projects SET updated_at = ? WHERE id = ?",
                        (now, project.id),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                logger.exception(f"[ProjectManager] 写资源索引失败: {e}")
            # 仅写 project.json(避免双重调用 _writeProjectMetaRow)
            self._saveProjectJson(project)
        logger.info(
            f"[ProjectManager] 添加资源: project={projectId}, type={resourceType}, title={title}"
        )
        self.projectListChanged.emit()
        return resource

    def listResources(self, projectId: str) -> List[Resource]:
        """列出项目的所有资源(按 createdAt DESC)"""
        project = self._memCache.get(projectId)
        if project is None:
            return []
        items = list(project.resources)
        items.sort(key=lambda r: r.createdAt or "", reverse=True)
        return items

    def countResources(self, projectId: str) -> int:
        """快速获取资源数量"""
        project = self._memCache.get(projectId)
        if project is None:
            return 0
        return len(project.resources)

    # ------------------------------------------------------------------
    # 公共 API:corpus refs(MVP 占位,后续迭代接 CorpusManager)
    # ------------------------------------------------------------------
    def setCorporaRefs(self, projectId: str, refs: List[CorpusRef]) -> None:
        """设置项目关联的语料库列表(覆盖式)"""
        project = self._memCache.get(projectId)
        if project is None:
            return
        project.corporaRefs = list(refs)
        project.updatedAt = _nowIso()
        with self._dbLock:
            self._writeProjectMetaRow(project)
            self._saveProjectJson(project)

    # ------------------------------------------------------------------
    # 公共 API:AI 解读归档（PRD-002 / AI 联动 MVP）
    # ------------------------------------------------------------------
    def listAiInsights(self, projectId: str) -> List[AiInsight]:
        """列出项目的 AI 解读归档,按 createdAt DESC 排序。"""
        project = self._memCache.get(projectId)
        if project is None:
            return []
        items = list(project.aiInsights)
        items.sort(key=lambda x: x.createdAt or "", reverse=True)
        return items

    def addAiInsight(
        self,
        projectId: str,
        content: str,
        analysisType: str = "research_report",
        model: str = "",
        confidence: str = "medium",
        resourceId: Optional[str] = None,
    ) -> Optional[AiInsight]:
        """添加 AI 解读归档(由 ResearchReportService 调用)。

        Args:
            projectId: 项目 id;无效时返回 None
            content: AI 生成的解读正文
            analysisType: 解读类型(默认 "research_report",后续可扩展)
            model: 生成模型名(如 "deepseek-chat")
            confidence: "high" | "medium" | "low"
            resourceId: 关联资源 id;None 表示项目级解读

        Returns:
            创建的 AiInsight 对象;失败返回 None
        """
        project = self._memCache.get(projectId)
        if project is None:
            logger.warning(f"[ProjectManager] addAiInsight: 项目 {projectId} 不存在")
            return None
        now = _nowIso()
        insight = AiInsight(
            id=genId(),
            analysisType=analysisType,
            content=content,
            citations=[],
            confidence=confidence,
            model=model,
            resourceId=resourceId,
            createdAt=now,
        )
        project.aiInsights.append(insight)
        project.updatedAt = now
        with self._dbLock:
            try:
                conn = self._connectDb()
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO project_ai_insights "
                        "(id, project_id, analysis_type, content, citations, "
                        " confidence, model, resource_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            insight.id,
                            project.id,
                            insight.analysisType,
                            insight.content,
                            json.dumps(
                                insight.citations or [],
                                ensure_ascii=False,
                            ),
                            insight.confidence,
                            insight.model,
                            insight.resourceId,
                            insight.createdAt,
                        ),
                    )
                    conn.execute(
                        "UPDATE projects SET updated_at = ? WHERE id = ?",
                        (now, project.id),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                logger.exception(f"[ProjectManager] 写 AI 解读到 SQLite 失败: {e}")
                return None
            # 仅写 project.json;UPDATE projects 已在上面完成
            self._saveProjectJson(project)
        logger.info(
            f"[ProjectManager] 添加 AI 解读: project={projectId}, "
            f"type={analysisType}, len={len(content)}"
        )
        self.projectListChanged.emit()
        return insight

    def deleteAiInsight(self, projectId: str, insightId: str) -> bool:
        """删除单条 AI 解读(供 UI「删除报告」使用)。"""
        project = self._memCache.get(projectId)
        if project is None:
            return False
        before = len(project.aiInsights)
        project.aiInsights = [a for a in project.aiInsights if a.id != insightId]
        if len(project.aiInsights) == before:
            return False
        now = _nowIso()
        project.updatedAt = now
        with self._dbLock:
            try:
                conn = self._connectDb()
                try:
                    conn.execute(
                        "DELETE FROM project_ai_insights WHERE id = ?",
                        (insightId,),
                    )
                    conn.execute(
                        "UPDATE projects SET updated_at = ? WHERE id = ?",
                        (now, project.id),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                logger.exception(f"[ProjectManager] 删除 AI 解读(SQLite)失败: {e}")
            # 仅写 project.json;UPDATE projects 已在上面完成
            self._saveProjectJson(project)
        logger.info(
            f"[ProjectManager] 删除 AI 解读: project={projectId}, insight={insightId}"
        )
        self.projectListChanged.emit()
        return True


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _jsonLoadList(raw: Optional[str]) -> List[str]:
    """安全地解析 JSON 数组字符串,失败返回 []"""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return [str(x) for x in v]
    except Exception:
        pass
    return []


def _jsonLoadDict(raw: Optional[str]) -> Dict[str, Any]:
    """安全地解析 JSON 对象字符串,失败返回 {}"""
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    return {}


def _jsonLoadListOfDict(raw: Optional[str]) -> List[Dict[str, Any]]:
    """安全地解析 JSON 列表（元素为 dict）字符串,失败返回 []"""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# 模块级单例访问器(对齐 taskManager 风格)
# ---------------------------------------------------------------------------

projectManager = ProjectManager.instance()


# ---------------------------------------------------------------------------
# 创建项目的后台 worker(在子线程跑磁盘 I/O,不阻塞 UI)
# ---------------------------------------------------------------------------


class _CreateProjectWorker(QThread):
    """后台线程:执行创建项目所需的所有磁盘 I/O。

    跑在子线程的工作:
        1. 创建项目文件夹及其子目录(resources / notes / insights)
        2. 写一行 SQLite projects 表(SQLite 连接 check_same_thread=False)
        3. 把 Project 序列化为 project.json

    完成后通过 finishedWithResult(Project) 信号通知主线程
    ProjectManager._onCreateProjectFinished;失败通过 failed(str) 通知。
    不在子线程里改 _memCache / 发 Qt 信号 — 那些必须在主线程。
    """

    finishedWithResult = Signal(object)  # Project
    failed = Signal(str)  # 错误描述

    def __init__(self, manager: "ProjectManager", project: "Project") -> None:
        super().__init__(manager)
        self._manager = manager
        self._project = project

    def run(self) -> None:  # noqa: D401
        try:
            # 拿到 dbLock(主线程 _loadAllProjectsFromDb 等也用它,确保互斥)
            with self._manager._dbLock:
                self._manager._writeProjectMetaRow(self._project)
                self._manager._saveProjectJson(self._project)
            self.finishedWithResult.emit(self._project)
        except Exception as e:
            logger.exception(f"[CreateProjectWorker] 创建失败: {e}")
            self.failed.emit(f"{type(e).__name__}: {e}")
