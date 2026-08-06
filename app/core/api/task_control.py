# coding: utf-8
"""
任务控制模块
使用SQLite本地数据库存储任务数据
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.core.utils import logger

from app.core.utils.setting import CONFIG_FOLDER


class TaskControl:
    """任务管理器 - 使用SQLite本地数据库存储"""

    def __init__(self):
        """初始化任务管理器"""
        self.dbPath = CONFIG_FOLDER / "tasks.db"
        self.local = threading.local()
        self.lock = threading.RLock()
        # P0-fix:统一维护所有线程创建的 connection,close() 时一并关闭,
        # 避免仅关闭当前线程导致其他线程 connection 永久泄漏。
        self._connections: set = set()

        # 确保配置目录存在
        CONFIG_FOLDER.mkdir(parents=True, exist_ok=True)

        # 初始化数据库
        self.initDatabase()

        # 清理旧数据
        self.cleanupOldTasks(daysOld=10)

    def getConnection(self) -> sqlite3.Connection:
        """获取线程本地的数据库连接"""
        if not hasattr(self.local, "connection") or self.local.connection is None:
            conn = sqlite3.connect(str(self.dbPath), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self.local.connection = conn
            # P0-fix:登记到全局集合,便于 close() 统一关闭
            with self.lock:
                self._connections.add(conn)
        return self.local.connection

    @contextmanager
    def getCursor(self):
        """获取数据库游标的上下文管理器"""
        conn = None
        cursor = None
        try:
            conn = self.getConnection()
            cursor = conn.cursor()
            yield cursor
            conn.commit()
        except Exception as e:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception as rollbackError:
                    logger.error(
                        f"[TaskControl] 数据库回滚失败, "
                        f"type={type(rollbackError).__name__}: {rollbackError}"
                    )
            logger.exception(
                f"[TaskControl] 数据库事务失败, db={self.dbPath}: {e}"
            )
            raise
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception as closeError:
                    logger.warning(
                        f"[TaskControl] 关闭数据库游标失败, "
                        f"type={type(closeError).__name__}: {closeError}"
                    )

    def initDatabase(self):
        """初始化数据库表结构"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        type TEXT NOT NULL,
                        info TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        progress INTEGER DEFAULT 0,
                        createdAt TEXT NOT NULL,
                        startedAt TEXT,
                        endedAt TEXT,
                        result TEXT,
                        error TEXT,
                        updatedAt TEXT NOT NULL,
                        downloadPath TEXT,
                        taskName TEXT,
                        fileSize INTEGER,
                        fileName TEXT
                    )
                """
                )

                # 创建索引
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)")
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_createdAt ON tasks(createdAt)"
                )

                # 添加可能缺失的列（兼容旧数据库）
                newColumns = [
                    ("downloadPath", "TEXT"),
                    ("taskName", "TEXT"),
                    ("fileSize", "INTEGER"),
                    ("fileName", "TEXT"),
                ]

                for colName, colType in newColumns:
                    try:
                        cursor.execute(
                            f"ALTER TABLE tasks ADD COLUMN {colName} {colType}"
                        )
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" in str(exc).lower():
                            logger.debug(
                                f"[TaskControl] 数据库列已存在,跳过迁移: {colName}"
                            )
                            continue
                        raise sqlite3.OperationalError(
                            f"字段迁移失败 column={colName}: {exc}"
                        ) from exc

    def rowToDict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """将数据库行转换为字典"""
        result = dict(row)

        # 解析JSON字段
        if result.get("info"):
            try:
                result["info"] = json.loads(result["info"])
            except json.JSONDecodeError as e:
                logger.warning(
                    f"[TaskControl] 任务info字段损坏, taskId={result.get('id')}, "
                    f"error={e}"
                )
                result["info"] = {}

        if result.get("result"):
            try:
                result["result"] = json.loads(result["result"])
            except json.JSONDecodeError as e:
                logger.warning(
                    f"[TaskControl] 任务result字段损坏, taskId={result.get('id')}, "
                    f"error={e}"
                )
                result["result"] = None

        return result

    def addTask(self, taskType: str, taskInfo: Dict[str, Any]) -> str:
        """添加新任务"""
        with self.lock:
            taskId = str(uuid4())
            now = datetime.now().isoformat()

            with self.getCursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO tasks (id, type, info, status, progress, createdAt, updatedAt)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        taskId,
                        taskType,
                        json.dumps(taskInfo, ensure_ascii=False),
                        "pending",
                        0,
                        now,
                        now,
                    ),
                )

            logger.info(f"[TaskControl] 添加任务: {taskId}, 类型: {taskType}")
            return taskId

    def queryTask(self, taskId: str) -> Optional[Dict[str, Any]]:
        """查询任务详情"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute("SELECT * FROM tasks WHERE id = ?", (taskId,))
                row = cursor.fetchone()

                if row:
                    return self.rowToDict(row)
                return None

    def getDownloadPath(self, taskId: str) -> Optional[str]:
        """获取任务的下载文件路径"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute("SELECT downloadPath FROM tasks WHERE id = ?", (taskId,))
                row = cursor.fetchone()
                return row["downloadPath"] if row else None

    def startTask(self, taskId: str) -> Optional[Dict[str, Any]]:
        """开始任务"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute("SELECT status FROM tasks WHERE id = ?", (taskId,))
                result = cursor.fetchone()

                if not result or result["status"] != "pending":
                    currentStatus = result["status"] if result else "missing"
                    logger.warning(
                        f"[TaskControl] 拒绝启动任务, taskId={taskId}, "
                        f"status={currentStatus}"
                    )
                    return None

                now = datetime.now().isoformat()
                cursor.execute(
                    "UPDATE tasks SET status = ?, startedAt = ?, updatedAt = ? WHERE id = ?",
                    ("in_progress", now, now, taskId),
                )

            logger.info(f"[TaskControl] 启动任务: {taskId}")
            return self.queryTask(taskId)

    def finishTask(
        self, taskId: str, resultData: Any = None
    ) -> Optional[Dict[str, Any]]:
        """完成任务"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute("SELECT status FROM tasks WHERE id = ?", (taskId,))
                row = cursor.fetchone()

                if not row:
                    logger.warning(f"[TaskControl] 完成任务失败,任务不存在: {taskId}")
                    return None

                now = datetime.now().isoformat()
                resultJson = (
                    json.dumps(resultData, ensure_ascii=False) if resultData else None
                )

                cursor.execute(
                    "UPDATE tasks SET status = ?, endedAt = ?, result = ?, progress = 100, updatedAt = ? WHERE id = ?",
                    ("completed", now, resultJson, now, taskId),
                )

            logger.info(f"[TaskControl] 完成任务: {taskId}")
            return self.queryTask(taskId)

    def failTask(self, taskId: str, errorMsg: str) -> Optional[Dict[str, Any]]:
        """标记任务失败"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute("SELECT status FROM tasks WHERE id = ?", (taskId,))
                row = cursor.fetchone()

                if not row:
                    logger.warning(f"[TaskControl] 标记失败无效,任务不存在: {taskId}")
                    return None

                now = datetime.now().isoformat()

                cursor.execute(
                    "UPDATE tasks SET status = ?, endedAt = ?, error = ?, updatedAt = ? WHERE id = ?",
                    ("failed", now, errorMsg, now, taskId),
                )

            logger.error(f"[TaskControl] 任务失败: {taskId}, 错误: {errorMsg}")
            return self.queryTask(taskId)

    def cancelTask(self, taskId: str) -> Optional[Dict[str, Any]]:
        """取消任务"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute("SELECT status FROM tasks WHERE id = ?", (taskId,))
                row = cursor.fetchone()

                if not row or row["status"] not in ["pending", "in_progress"]:
                    currentStatus = row["status"] if row else "missing"
                    logger.warning(
                        f"[TaskControl] 拒绝取消任务, taskId={taskId}, "
                        f"status={currentStatus}"
                    )
                    return None

                now = datetime.now().isoformat()

                cursor.execute(
                    "UPDATE tasks SET status = ?, endedAt = ?, updatedAt = ? WHERE id = ?",
                    ("cancelled", now, now, taskId),
                )

            logger.info(f"[TaskControl] 取消任务: {taskId}")
            return self.queryTask(taskId)

    def updateProgress(self, taskId: str, progress: int) -> bool:
        """更新任务进度"""
        with self.lock:
            with self.getCursor() as cursor:
                now = datetime.now().isoformat()
                cursor.execute(
                    "UPDATE tasks SET progress = ?, updatedAt = ? WHERE id = ? AND status = 'in_progress'",
                    (progress, now, taskId),
                )
                return cursor.rowcount > 0

    def updateDownloadInfo(
        self,
        taskId: str,
        downloadPath: str = None,
        taskName: str = None,
        fileSize: int = None,
        fileName: str = None,
    ) -> Optional[Dict[str, Any]]:
        """更新任务的下载信息"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute("SELECT * FROM tasks WHERE id = ?", (taskId,))
                row = cursor.fetchone()

                if not row:
                    logger.warning(
                        f"[TaskControl] 更新下载信息失败,任务不存在: {taskId}"
                    )
                    return None

                # 构建更新语句
                updateFields = []
                values = []

                if downloadPath is not None:
                    updateFields.append("downloadPath = ?")
                    values.append(downloadPath)
                if taskName is not None:
                    updateFields.append("taskName = ?")
                    values.append(taskName)
                if fileSize is not None:
                    updateFields.append("fileSize = ?")
                    values.append(fileSize)
                if fileName is not None:
                    updateFields.append("fileName = ?")
                    values.append(fileName)

                # 添加updatedAt字段
                updateFields.append("updatedAt = ?")
                values.append(datetime.now().isoformat())

                # 添加taskId到values
                values.append(taskId)

                # 构建SQL语句
                if updateFields:
                    sql = f"UPDATE tasks SET {', '.join(updateFields)} WHERE id = ?"
                    cursor.execute(sql, values)

                return self.queryTask(taskId)

    def deleteTask(self, taskId: str) -> bool:
        """删除任务"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute("DELETE FROM tasks WHERE id = ?", (taskId,))
                result = cursor.rowcount > 0
                if result:
                    logger.info(f"[TaskControl] 删除任务: {taskId}")
                return result

    def getTasksByStatus(self, status: str) -> List[Dict[str, Any]]:
        """根据状态获取任务列表"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY createdAt DESC",
                    (status,),
                )
                return [self.rowToDict(row) for row in cursor.fetchall()]

    def getAllTasks(self) -> List[Dict[str, Any]]:
        """获取所有任务"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute("SELECT * FROM tasks ORDER BY createdAt DESC")
                return [self.rowToDict(row) for row in cursor.fetchall()]

    def getDoneTasks(self) -> List[Dict[str, Any]]:
        """获取已完成的任务列表"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute(
                    "SELECT * FROM tasks WHERE status IN ('completed', 'failed', 'cancelled') ORDER BY endedAt DESC"
                )
                return [self.rowToDict(row) for row in cursor.fetchall()]

    def getStats(self) -> Dict[str, int]:
        """获取任务统计信息"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute(
                    "SELECT status, COUNT(*) as count FROM tasks GROUP BY status"
                )
                stats = {row["status"]: row["count"] for row in cursor.fetchall()}

                return {
                    "total": sum(stats.values()),
                    "pending": stats.get("pending", 0),
                    "inProgress": stats.get("in_progress", 0),
                    "completed": stats.get("completed", 0),
                    "failed": stats.get("failed", 0),
                    "cancelled": stats.get("cancelled", 0),
                }

    def cleanupOldTasks(self, daysOld: int = 30) -> int:
        """清理旧任务记录"""
        with self.lock:
            with self.getCursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM tasks
                    WHERE status IN ('completed', 'failed', 'cancelled')
                    AND datetime(endedAt) < datetime('now', ?)
                    """,
                    (f"-{daysOld} days",),
                )
                count = cursor.rowcount
                if count > 0:
                    logger.info(f"[TaskControl] 清理 {count} 个旧任务")
                return count

    def close(self):
        """关闭数据库连接

        P0-fix:遍历所有线程创建的 connection 统一关闭,避免仅关闭当前线程
        的 connection 导致其他线程连接永久泄漏。
        """
        with self.lock:
            # 先关闭登记的所有连接
            for conn in list(self._connections):
                try:
                    conn.close()
                except Exception as e:
                    logger.warning(
                        f"[TaskControl] 关闭数据库连接失败: "
                        f"{type(e).__name__}: {e}"
                    )
            self._connections.clear()

            # 关闭当前线程 connection(若未被登记集包含,兜底处理)
            localConn = getattr(self.local, "connection", None)
            if localConn is not None:
                try:
                    localConn.close()
                except Exception as e:
                    logger.warning(
                        f"[TaskControl] 关闭线程数据库连接失败: "
                        f"{type(e).__name__}: {e}"
                    )
                self.local.connection = None


# 创建全局任务控制器实例
taskControl = TaskControl()
