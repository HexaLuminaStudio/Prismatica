# coding: utf-8
"""
任务管理器服务
统一管理所有下载任务的创建、调度和状态跟踪
"""

import threading
from typing import Any, Dict, List, Optional

from loguru import logger
from PySide6.QtCore import QObject, Signal

from app.core.api.task_control import taskControl


class TaskManager(QObject):
    """任务管理器，统一管理所有下载任务"""

    # 信号定义
    taskStarted = Signal(str)  # 任务启动信号
    taskProgress = Signal(str, dict)  # 任务进度信号
    taskCompleted = Signal(str)  # 任务完成信号
    taskFailed = Signal(str, str)  # 任务失败信号
    taskCancelled = Signal(str)  # 任务取消信号
    taskPaused = Signal(str)  # 任务暂停信号
    taskResumed = Signal(str)  # 任务恢复信号

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
            pendingTasks = taskControl.getTasksByStatus("pending")
            for task in pendingTasks:
                taskId = task.get("id")
                if taskId and taskId not in self.pendingQueue:
                    self.pendingQueue.append(taskId)
                    logger.info(f"[TaskManager] 恢复pending任务: {taskId}")
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
        worker.progress.connect(
            lambda progressInfo, tid=taskId: self.onTaskProgress(tid, progressInfo)
        )
        worker.finished.connect(
            lambda success, message, tid=taskId: self.onTaskFinished(
                tid, success, message
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
        """停止任务"""
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

            # 停止worker
            worker = self.workers[taskId]
            if worker:
                worker.stop()
                if not worker.wait(10000):
                    logger.warning(f"[TaskManager] 任务停止超时: {taskId}")

                if taskId in self.workers:
                    del self.workers[taskId]

            # 更新数据库状态
            taskControl.cancelTask(taskId)
            self.taskCancelled.emit(taskId)
            logger.info(f"[TaskManager] 停止任务: {taskId}")

            # 处理队列中的下一个任务
            self.processQueue()

            return True

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
        """移除任务"""
        with self.lock:
            if taskId in self.workers:
                self.stopTask(taskId)

            if taskId in self.pendingQueue:
                self.pendingQueue.remove(taskId)

            result = taskControl.deleteTask(taskId)
            if result:
                logger.info(f"[TaskManager] 移除任务: {taskId}")

            return result

    def stopAllTasks(self) -> int:
        """停止所有任务"""
        with self.lock:
            stoppedCount = 0

            # 停止队列中的任务
            for taskId in self.pendingQueue.copy():
                self.pendingQueue.remove(taskId)
                taskControl.cancelTask(taskId)
                self.taskCancelled.emit(taskId)
                stoppedCount += 1

            # 停止运行中的任务
            for taskId in list(self.workers.keys()):
                if self.stopTask(taskId):
                    stoppedCount += 1

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

    def onTaskFinished(self, taskId: str, success: bool, message: str):
        """处理任务完成"""
        with self.lock:
            worker = self.workers.get(taskId)
            if worker:
                if not worker.wait(10000):
                    logger.warning(f"[TaskManager] 任务线程停止超时: {taskId}")
                del self.workers[taskId]

            if success:
                taskControl.finishTask(taskId, {"message": message})
                self.taskCompleted.emit(taskId)
                logger.info(f"[TaskManager] 任务完成: {taskId}")
            else:
                taskControl.failTask(taskId, message)
                self.taskFailed.emit(taskId, message)
                logger.error(f"[TaskManager] 任务失败: {taskId}")

            self.processQueue()

    def onTaskFailed(self, taskId: str, error: str):
        """处理任务失败"""
        with self.lock:
            worker = self.workers.get(taskId)
            if worker:
                if not worker.wait(10000):
                    logger.warning(f"[TaskManager] 任务线程停止超时: {taskId}")
                del self.workers[taskId]

            taskControl.failTask(taskId, error)
            self.taskFailed.emit(taskId, error)
            logger.error(f"[TaskManager] 任务失败: {taskId}, 错误: {error}")

            self.processQueue()


# 创建全局任务管理器实例
taskManager = TaskManager()
