# coding: utf-8
"""
任务管理器服务
统一管理所有下载任务的创建、调度和状态跟踪
"""

import threading
from typing import Any, Dict, List, Optional

from app.core.utils import logger
from PySide6.QtCore import QObject, Signal

from app.core.api.task_control import taskControl


class TaskManager(QObject):
    """任务管理器，统一管理所有下载任务"""

    # 信号定义
    taskStarted = Signal(str)  # 任务启动信号
    taskProgress = Signal(str, dict)  # 任务进度信号
    taskCompleted = Signal(str, str)  # 任务完成信号 (taskId, filePath)
    taskFailed = Signal(str, str)  # 任务失败信号
    taskCancelled = Signal(str)  # 任务取消信号
    taskPaused = Signal(str)  # 任务暂停信号
    taskResumed = Signal(str)  # 任务恢复信号
    # P0-fix:新增删除信号,UI 监听后可同步移除卡片。
    # 原 download_card 直接调 taskControl.deleteTask() 后只 deleteLater
    # 自己,DownloadedScrollArea.completedCards 字典里的引用泄漏,
    # 切到其他语料 / 重建滚动区域时会出现悬空卡片。
    taskDeleted = Signal(str)  # 任务删除信号 (taskId)

    def __init__(self, maxConcurrentTasks: int = 3):
        super().__init__()
        self.lock = threading.RLock()
        self.workers: Dict[str, Any] = {}  # 存储运行中的worker
        self.maxConcurrentTasks = maxConcurrentTasks
        self.pendingQueue: List[str] = []  # 待处理任务队列
        self.isRunning = True

        # 恢复数据库中未完成的任务
        self.restorePendingTasks()

    def restorePendingTasks(self):
        """恢复数据库中pending状态的任务到内存队列"""
        try:
            restoredCount = 0
            pendingTasks = taskControl.getTasksByStatus("pending")
            for task in pendingTasks:
                taskId = task.get("id")
                if taskId and taskId not in self.pendingQueue:
                    self.pendingQueue.append(taskId)
                    restoredCount += 1
            if restoredCount:
                logger.info(f"[TaskManager] 已恢复 {restoredCount} 个等待任务")
        except Exception as e:
            logger.error(f"[TaskManager] 恢复pending任务失败: {e}")

    def createTask(self, taskType: str, taskInfo: Dict[str, Any]) -> str:
        """创建新任务"""
        with self.lock:
            # 添加任务到数据库
            taskId = taskControl.addTask(taskType, taskInfo)
            taskInfo["taskId"] = taskId

            # 添加到待处理队列
            self.pendingQueue.append(taskId)

            logger.info(f"[TaskManager] 创建任务: {taskId}, 类型: {taskType}")
            # 尝试启动队列中的任务
            self.processQueue()

            return taskId

    def processQueue(self):
        """处理任务队列，启动可执行的任务"""
        with self.lock:
            runningCount = len(self.workers)
            availableSlots = self.maxConcurrentTasks - runningCount

            if availableSlots <= 0:
                logger.debug(
                    f"[TaskManager] 并发任务数已达上限 ({self.maxConcurrentTasks})"
                )
                return

            # 从队列中取出任务并启动
            for _ in range(min(availableSlots, len(self.pendingQueue))):
                taskId = self.pendingQueue.pop(0)
                self.startTaskInternal(taskId)

    def startTaskInternal(self, taskId: str) -> bool:
        """内部方法：实际启动任务"""
        # 查询任务信息
        taskInfo = taskControl.queryTask(taskId)
        if not taskInfo:
            logger.error(f"[TaskManager] 任务不存在: {taskId}")
            return False

        # 检查任务状态
        if taskInfo.get("status") != "pending":
            logger.warning(f"[TaskManager] 任务状态不是pending: {taskId}")
            return False

        taskType = taskInfo.get("type")
        info = taskInfo.get("info", {})
        info["taskId"] = taskId

        # 创建对应的下载worker
        worker = None
        if taskType == "hskDownload":
            from app.core.services.hsk_download import HSKDownloadWorker

            worker = HSKDownloadWorker(info)
        elif taskType == "globalDownload":
            from app.core.services.global_download import GlobalDownloadWorker

            worker = GlobalDownloadWorker(info)

        if not worker:
            logger.error(f"[TaskManager] 未知任务类型: {taskType}")
            return False

        # 连接信号
        # finished 信号携带 (success, message, filePath),避免下游从 worker 属性读取
        worker.progress.connect(
            lambda progressInfo, tid=taskId: self.onTaskProgress(tid, progressInfo)
        )
        worker.finished.connect(
            lambda success, message, filePath, tid=taskId: self.onTaskFinished(
                tid, success, message, filePath or ""
            )
        )
        worker.failed.connect(lambda error, tid=taskId: self.onTaskFailed(tid, error))

        # 更新数据库状态
        if not taskControl.startTask(taskId):
            logger.error(f"[TaskManager] 启动任务失败，数据库状态更新失败: {taskId}")
            return False

        # 启动worker
        worker.start()
        self.workers[taskId] = worker

        self.taskStarted.emit(taskId)
        logger.info(f"[TaskManager] 启动任务: {taskId}")
        return True

    def startTask(self, taskId: str) -> bool:
        """启动指定任务"""
        with self.lock:
            if taskId in self.workers:
                logger.warning(f"[TaskManager] 任务已在运行: {taskId}")
                return False

            if taskId in self.pendingQueue:
                self.pendingQueue.remove(taskId)
                self.pendingQueue.insert(0, taskId)
                self.processQueue()
                return True

            taskInfo = taskControl.queryTask(taskId)
            if not taskInfo:
                logger.error(f"[TaskManager] 任务不存在: {taskId}")
                return False

            if taskInfo.get("status") != "pending":
                logger.error(f"[TaskManager] 任务状态错误: {taskId}")
                return False

            self.pendingQueue.insert(0, taskId)
            self.processQueue()
            return True

    def stopTask(self, taskId: str) -> bool:
        """停止任务。

        P1-fix:不再持 RLock 调用 worker.wait(10000)。
            持锁时 wait 最多 10s,期间跨线程反向持锁极易死锁,
            且会冻结主线程 UI。改为:
                1. 在锁内发出停止信号并摘出 worker 引用
                2. 在锁外做短超时 wait(QThread.requestInterruption
                   + isInterruptionRequested 由 worker run() 周期性检查)
        """
        # 阶段 1:在锁内完成取消标记与摘出 worker 引用
        with self.lock:
            # 检查是否在队列中
            if taskId in self.pendingQueue:
                self.pendingQueue.remove(taskId)
                taskControl.cancelTask(taskId)
                self.taskCancelled.emit(taskId)
                logger.info(f"[TaskManager] 取消队列中的任务: {taskId}")
                return True

            # 检查是否在运行中
            if taskId not in self.workers:
                logger.warning(f"[TaskManager] 任务不在运行中: {taskId}")
                return False

            worker = self.workers.pop(taskId)
            self._requestStopLocked(worker)
            cancelOk = True

        # 阶段 2:锁外做短超时 wait(不阻塞其他持锁者)
        self._waitWorkerOutsideLock(worker, taskId, timeoutMs=2000)

        # 阶段 3:更新数据库并通知(锁内做轻量操作)
        with self.lock:
            taskControl.cancelTask(taskId)
            self.taskCancelled.emit(taskId)
            logger.info(f"[TaskManager] 停止任务: {taskId}")
            self.processQueue()

        return cancelOk

    def _requestStopLocked(self, worker) -> None:
        """在持锁状态下请求 worker 停止(不等待)。

        优先调用 requestInterruption()(非阻塞、跨平台),
        再调用 worker.stop()(项目内既有约定),两者皆为信号式通知。
        """
        try:
            worker.requestInterruption()
        except Exception:
            pass
        try:
            if hasattr(worker, "stop"):
                worker.stop()
        except Exception:
            pass

    def _waitWorkerOutsideLock(
        self, worker, taskId: str, timeoutMs: int = 2000
    ) -> bool:
        """在锁外等待 worker 停止,避免与 worker 内部持锁形成环路。

        返回 True 表示已结束,False 表示超时。

        设计:
            - 用较短超时(默认 2s),大部分 stop() 后 worker 会快速响应
            - 超时后仍发出 taskCancelled,worker.run() 完成后会通过
              finished 信号回调 onTaskFinished 完成清理
        """
        try:
            return bool(worker.wait(timeoutMs))
        except Exception as e:
            logger.warning(f"[TaskManager] wait worker 异常 {taskId}: {e}")
            return False

    def pauseTask(self, taskId: str) -> bool:
        """暂停任务"""
        with self.lock:
            if taskId not in self.workers:
                logger.error(f"[TaskManager] 任务不在运行中: {taskId}")
                return False

            worker = self.workers[taskId]
            if worker and hasattr(worker, "pause"):
                worker.pause()
                self.taskPaused.emit(taskId)
                logger.info(f"[TaskManager] 暂停任务: {taskId}")
                return True

            return False

    def resumeTask(self, taskId: str) -> bool:
        """恢复任务"""
        with self.lock:
            if taskId not in self.workers:
                logger.error(f"[TaskManager] 任务不在运行中: {taskId}")
                return False

            worker = self.workers[taskId]
            if worker and hasattr(worker, "resume"):
                worker.resume()
                self.taskResumed.emit(taskId)
                logger.info(f"[TaskManager] 恢复任务: {taskId}")
                return True

            return False

    def getTask(self, taskId: str) -> Optional[Dict[str, Any]]:
        """获取任务信息"""
        return taskControl.queryTask(taskId)

    def getAllTasks(self) -> List[Dict[str, Any]]:
        """获取所有任务"""
        return taskControl.getAllTasks()

    def getRunningTasks(self) -> List[str]:
        """获取正在运行的任务ID列表"""
        with self.lock:
            return list(self.workers.keys())

    def getPendingTasks(self) -> List[str]:
        """获取待处理的任务ID列表"""
        with self.lock:
            return self.pendingQueue.copy()

    def removeTask(self, taskId: str) -> bool:
        """移除任务

        P0-fix:成功删除数据库记录后,emit taskDeleted 信号。
        下游 DownloadedScrollArea / 已完成面板监听此信号后可
        同步移除卡片,避免 completedCards 字典里残留悬空引用。
        """
        with self.lock:
            if taskId in self.workers:
                self.stopTask(taskId)

            if taskId in self.pendingQueue:
                self.pendingQueue.remove(taskId)

            result = taskControl.deleteTask(taskId)
            if result:
                logger.info(f"[TaskManager] 移除任务: {taskId}")

        # 在锁外 emit,避免下游回调重入 TaskManager 锁
        if result:
            self.taskDeleted.emit(taskId)

        return result

    # ========================================================================
    # 视图层高阶接口(P0-A1 fix 2026-07-18)
    # ========================================================================
    # 设计目标:把 app.core.api.taskControl 的直接调用从视图层
    # (app/view/widgets/*)完全收敛到 TaskManager,做到「视图层只依赖
    # services 层」的单向依赖。这样:
    #   - 替换底层数据源(比如换 SQLite → PostgreSQL / 文件 → 远程 API)
    #     只需改 TaskManager 内部,UI 完全不动
    #   - 视图层不需要 try/except 处理每个底层 API 的异常路径
    #   - 单元测试可以 mock TaskManager 接口而不必 mock 数据库
    # ========================================================================

    def getDownloadPath(self, taskId: str) -> Optional[str]:
        """获取任务的下载文件路径(视图层入口)

        Returns:
            文件绝对路径,或 None(任务不存在 / 尚未记录路径)
        """
        try:
            return taskControl.getDownloadPath(taskId)
        except Exception as e:
            logger.error(f"[TaskManager] getDownloadPath 失败 {taskId}: {e}")
            return None

    def getDoneTasks(self) -> List[Dict[str, Any]]:
        """获取已完成的任务列表(视图层入口)

        返回 status IN ('completed', 'failed', 'cancelled') 的任务,
        按 endedAt 倒序。
        """
        try:
            return taskControl.getDoneTasks()
        except Exception as e:
            logger.error(f"[TaskManager] getDoneTasks 失败: {e}")
            return []

    def getInProgressTasks(self) -> List[Dict[str, Any]]:
        """获取正在进行的任务列表(视图层入口)

        返回 status='in_progress' 的任务,按 createdAt 倒序。
        """
        try:
            return taskControl.getTasksByStatus("in_progress")
        except Exception as e:
            logger.error(f"[TaskManager] getInProgressTasks 失败: {e}")
            return []

    def getPendingTasksFromDb(self) -> List[Dict[str, Any]]:
        """从数据库获取 pending 状态任务列表(视图层入口)

        与 getPendingTasks() 的区别:本方法返回完整 task dict 列表
        (含 info / createdAt 等),供 UI 卡片渲染使用;而 getPendingTasks()
        仅返回内存队列中的 taskId 字符串列表。

        Returns:
            List[Dict]: status='pending' 的任务,按 createdAt 倒序。
        """
        try:
            return taskControl.getTasksByStatus("pending")
        except Exception as e:
            logger.error(f"[TaskManager] getPendingTasksFromDb 失败: {e}")
            return []

    def removeTaskWithFallback(self, taskId: str) -> bool:
        """删除任务,带降级路径(视图层入口)

        流程:
            1. 优先调用 removeTask() — 走 TaskManager 完整流程,
               包括停止运行中的 worker + emit taskDeleted 信号
            2. removeTask() 内部抛异常时,降级为直接调底层 API
               (即使数据库写失败也不抛出,只记日志 + 返回 False)

        这就是 P0-A1 修复要求的 removeTaskWithFallback 接口 — 视图层
        只需要调这一个方法,不需要自己处理 taskControl fallback。

        Args:
            taskId: 任务 ID

        Returns:
            True 表示已成功删除(含降级路径),False 表示完全失败
        """
        try:
            return self.removeTask(taskId)
        except Exception as e:
            # 降级路径:即使 TaskManager 内部异常,也要保证记录被清掉
            logger.error(
                f"[TaskManager] removeTaskWithFallback 主路径异常 {taskId}: {e}, "
                f"降级到直接调 taskControl"
            )
            try:
                result = taskControl.deleteTask(taskId)
                if result:
                    # 即使是降级路径,也要 emit 信号让 UI 同步移除卡片
                    self.taskDeleted.emit(taskId)
                return result
            except Exception as inner:
                logger.exception(
                    f"[TaskManager] removeTaskWithFallback 降级路径也失败 {taskId}: {inner}"
                )
                return False

    def stopAllTasks(self) -> int:
        """停止所有任务。

        P1-fix:不再在锁内调用 stopTask()(会导致嵌套持锁 + 嵌套 wait);
            先在锁内批量收集 worker 引用与取消数据库记录,
            再在锁外统一 wait。
        """
        # 阶段 1:锁内收集待取消的 worker 列表
        workersToStop = []
        with self.lock:
            # 取消队列中的任务(不涉及 worker,直接做完)
            for taskId in self.pendingQueue.copy():
                self.pendingQueue.remove(taskId)
                taskControl.cancelTask(taskId)
                self.taskCancelled.emit(taskId)

            # 从 workers 字典中摘出所有 worker(用 pop 避免迭代时修改)
            taskIds = list(self.workers.keys())
            for taskId in taskIds:
                worker = self.workers.pop(taskId)
                workersToStop.append((taskId, worker))

        stoppedCount = len(workersToStop)
        # 阶段 2:锁外统一请求停止(不阻塞 UI)
        for taskId, worker in workersToStop:
            self._requestStopLocked(worker)
        for taskId, worker in workersToStop:
            self._waitWorkerOutsideLock(worker, taskId, timeoutMs=2000)

        # 阶段 3:锁内做轻量收尾
        with self.lock:
            logger.info(f"[TaskManager] 停止所有任务，共停止: {stoppedCount} 个")
        return stoppedCount

    def shutdown(self):
        """关闭任务管理器"""
        with self.lock:
            self.isRunning = False
            logger.info("[TaskManager] 开始关闭...")

            stoppedCount = self.stopAllTasks()

            import time

            maxWaitTime = 15
            startTime = time.time()

            while self.workers and (time.time() - startTime) < maxWaitTime:
                time.sleep(0.5)

                completedTasks = []
                for taskId, worker in self.workers.items():
                    if worker.isFinished():
                        completedTasks.append(taskId)

                for taskId in completedTasks:
                    del self.workers[taskId]

            logger.info(f"[TaskManager] 已关闭，共停止 {stoppedCount} 个任务")

    # 内部回调方法
    def onTaskProgress(self, taskId: str, progressInfo: Dict[str, Any]):
        """处理任务进度更新"""
        progress = progressInfo.get("progress", 0)
        taskControl.updateProgress(taskId, progress)
        self.taskProgress.emit(taskId, progressInfo)

    def onTaskFinished(
        self, taskId: str, success: bool, message: str, filePath: str = ""
    ):
        """处理任务完成。

        Args:
            taskId: 任务 ID
            success: 是否成功
            message: 描述信息
            filePath: 文件路径(来自 worker finished 信号参数,P1-fix)
                     取代原先 `getattr(worker, 'filePath', None)` 的属性读取,
                     避免 stop 后 worker 属性未设置的竞态。
        """
        # 阶段 1:锁内摘出 worker(轻量)
        worker = None
        with self.lock:
            worker = self.workers.pop(taskId, None)

        # 阶段 2:锁外做短超时 wait(finished 信号到达时 worker 通常已结束)
        if worker is not None:
            self._waitWorkerOutsideLock(worker, taskId, timeoutMs=200)

        # 阶段 3:锁内做数据库与信号通知
        with self.lock:
            if success:
                taskControl.finishTask(
                    taskId, {"message": message, "filePath": filePath}
                )
                self.taskCompleted.emit(taskId, filePath or "")
                logger.info(f"[TaskManager] 任务完成: {taskId}, filePath={filePath}")
            else:
                taskControl.failTask(taskId, message)
                self.taskFailed.emit(taskId, message)
                logger.error(f"[TaskManager] 任务失败: {taskId}, {message}")

            self.processQueue()

    def onTaskFailed(self, taskId: str, error: str):
        """处理任务失败。

        P1-fix:不再持 RLock 调用 worker.wait(10000),
        改为锁内摘出 → 锁外 wait(200ms) → 锁内通知。
        """
        # 阶段 1:锁内摘出 worker
        worker = None
        with self.lock:
            worker = self.workers.pop(taskId, None)

        # 阶段 2:锁外 wait(failed 信号到达时 worker 通常已结束)
        if worker is not None:
            self._waitWorkerOutsideLock(worker, taskId, timeoutMs=200)

        # 阶段 3:锁内做数据库与信号通知
        with self.lock:
            taskControl.failTask(taskId, error)
            self.taskFailed.emit(taskId, error)
            logger.error(f"[TaskManager] 任务失败: {taskId}, 错误: {error}")

            self.processQueue()


# 创建全局任务管理器实例
taskManager = TaskManager()
