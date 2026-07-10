# coding: utf-8
"""
HSK下载工作线程
处理HSK语料库的下载任务
"""

import os
import random
import time
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from loguru import logger
from PySide6.QtCore import QThread, Signal

from app.core.api.task_control import taskControl
from app.core.utils.config import qconfig, Config


class HSKDownloadWorker(QThread):
    """优化的下载工作线程，避免429错误"""

    progress = Signal(dict)  # 进度信息字典
    finished = Signal(bool, str)  # 完成信号
    failed = Signal(str)  # 失败信号

    def __init__(self, payload: Dict[str, Any]):
        super().__init__()
        self.isRunning = True
        self.isPaused = False
        self.token = qconfig.get(Config.HSKLoginToken)
        self.outputDir = qconfig.get(Config.DownloadSavePath)

        # 优化延迟策略避免429
        self.baseDelay = 2.5
        self.randomDelayRange = 1.5
        self.requestTimeout = 30
        self.maxRetries = qconfig.get(Config.MaximumAttempts)
        self.perPage = qconfig.get(Config.NumberPerDownloads)

        # 进度计算
        self.startTime = None
        self.completedPages = 0
        self.totalPages = 0
        self.taskId = payload.get("taskId", str(int(time.time())))

        # 请求参数
        self.url = payload.get(
            "url", "https://hsk.blcu.edu.cn/api/v1/sentence/search/keyword"
        )
        self.payload = payload.get("payload", {})
        self.payload["per_page"] = self.perPage

        # 统计信息
        self.requestCount = 0
        self.lastRequestTime = 0

    def stop(self):
        """停止下载"""
        logger.info(f"[HSK] 收到停止信号, taskId={self.taskId}")
        self.isRunning = False

    def pause(self):
        """暂停下载"""
        logger.info(f"[HSK] 暂停下载, taskId={self.taskId}")
        self.isPaused = True

    def resume(self):
        """继续下载"""
        logger.info(f"[HSK] 恢复下载, taskId={self.taskId}")
        self.isPaused = False

    def _calculateDelay(self) -> float:
        """计算请求延迟，避免429错误"""
        currentTime = time.time()
        timeSinceLast = currentTime - self.lastRequestTime

        if timeSinceLast < self.baseDelay:
            remaining = self.baseDelay - timeSinceLast
            time.sleep(remaining)

        delay = self.baseDelay + random.uniform(
            -self.randomDelayRange / 2, self.randomDelayRange / 2
        )
        return max(1.0, delay)

    def _formatTime(self, seconds: float) -> str:
        """格式化时间"""
        if seconds <= 0:
            return "00:00:00"

        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _calculateProgressInfo(self) -> Dict[str, Any]:
        """计算进度信息"""
        if not self.startTime or self.totalPages == 0:
            return {
                "progress": 0,
                "speed": "0.00页/s",
                "time": "剩余00:00:00",
                "page": "等待中...",
                "taskId": self.taskId,
                "filePath": None,
            }

        elapsedTime = time.time() - self.startTime

        progress = (
            min(99, int((self.completedPages / self.totalPages) * 100))
            if self.totalPages > 0
            else 0
        )

        if elapsedTime > 0:
            speed = self.completedPages / elapsedTime
            speedStr = f"{speed:.2f}页/s"
        else:
            speedStr = "0.00页/s"

        if self.completedPages > 0 and progress < 100:
            avgTime = elapsedTime / self.completedPages
            remaining = max(0, self.totalPages - self.completedPages) * avgTime
            timeStr = f"剩余{self._formatTime(remaining)}"
        else:
            timeStr = "剩余00:00:00"

        pageStr = f"{self.completedPages}/{self.totalPages}页"

        return {
            "progress": progress,
            "speed": speedStr,
            "time": timeStr,
            "page": pageStr,
            "taskId": self.taskId,
            "filePath": None,
        }

    def _emitProgress(self, status: str = ""):
        """发送进度更新"""
        progressInfo = self._calculateProgressInfo()
        if status:
            progressInfo["status"] = status
        self.progress.emit(progressInfo)

    def _makeRequest(self, page: int) -> Optional[Dict]:
        """发送请求，带有重试机制和防429策略"""
        for attempt in range(self.maxRetries):
            if not self.isRunning:
                return None

            delay = self._calculateDelay()
            if delay > 0:
                sleepChunks = int(delay * 10)
                for _ in range(sleepChunks):
                    if not self.isRunning:
                        return None
                    time.sleep(0.1)

            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            currentPayload = self.payload.copy()
            currentPayload["page"] = page

            try:
                self.lastRequestTime = time.time()
                response = requests.post(
                    self.url,
                    headers=headers,
                    json=currentPayload,
                    timeout=self.requestTimeout,
                )

                self.requestCount += 1

                # 处理429错误
                if response.status_code == 429:
                    waitTime = 10 * (attempt + 1)
                    if self.isRunning:
                        self.progress.emit(
                            {
                                "progress": self._calculateProgressInfo()["progress"],
                                "speed": "0.00页/s",
                                "time": f"等待{waitTime}秒",
                                "page": f"第{page}页 - 请求过多",
                                "status": f"429错误，等待{waitTime}秒后重试...",
                                "taskId": self.taskId,
                            }
                        )
                        sleepChunks = int(waitTime * 10)
                        for _ in range(sleepChunks):
                            if not self.isRunning:
                                return None
                            time.sleep(0.1)
                    continue

                # 处理其他错误
                if response.status_code != 200:
                    if attempt == self.maxRetries - 1:
                        return None
                    continue

                # 解析响应
                data = response.json()
                if data.get("code") == 0:
                    return data
                else:
                    if attempt == self.maxRetries - 1:
                        return None
                    continue

            except requests.exceptions.Timeout:
                if not self.isRunning or attempt == self.maxRetries - 1:
                    return None
                continue
            except Exception:
                if not self.isRunning or attempt == self.maxRetries - 1:
                    return None
                continue

        return None

    def downloadPage(self, page: int) -> Optional[Dict]:
        """下载单页数据"""
        if not self.isRunning:
            return None

        result = self._makeRequest(page)
        if result:
            self.completedPages += 1
            self._emitProgress(f"已下载第{page}页")

        return result

    def _processDataToExcel(self, data: List[Dict], outputPath: str) -> bool:
        """处理数据并保存为Excel"""
        try:
            if not data:
                return False

            df = pd.DataFrame(data)
            if df.empty:
                return False

            # 确保所有列都是字符串类型
            objectColumns = df.select_dtypes(include=["object"]).columns
            for col in objectColumns:
                df[col] = df[col].astype(str)

            # 保存Excel
            df.to_excel(outputPath, index=False)
            return True

        except Exception as e:
            logger.error(f"[HSK] 保存Excel时出错: {e}")
            return False

    def run(self):
        """执行下载任务"""
        logger.info(f"[HSK] 下载任务启动, taskId={self.taskId}")
        try:
            self.startTime = time.time()
            self.lastRequestTime = self.startTime

            # 创建输出目录
            os.makedirs(self.outputDir, exist_ok=True)
            logger.debug(f"[HSK] 输出目录已创建: {self.outputDir}")

            # 第一步：获取第一页数据确定总页数
            self._emitProgress("正在获取总页数...")

            firstPage = self.downloadPage(1)
            if not firstPage:
                errorMsg = "获取初始数据失败"
                self.failed.emit(errorMsg)
                self.finished.emit(False, errorMsg)
                return

            # 解析总数据量
            total = firstPage.get("total", 0)
            self.totalPages = max(1, (total + self.perPage - 1) // self.perPage)

            if total == 0:
                errorMsg = "未找到相关数据"
                self.failed.emit(errorMsg)
                self.finished.emit(False, errorMsg)
                return

            # 准备数据列表
            allData = [firstPage.get("data", [])]

            # 第二步：顺序下载剩余页面
            pausedShown = False
            for page in range(2, self.totalPages + 1):
                if not self.isRunning:
                    break

                # 检查是否暂停
                while self.isPaused and self.isRunning:
                    time.sleep(0.5)
                    # 暂停时只发送一次状态
                    if not pausedShown:
                        self._emitProgress("已暂停")
                        pausedShown = True

                pausedShown = False
                if not self.isRunning:
                    break

                pageData = self.downloadPage(page)
                if pageData:
                    allData.append(pageData.get("data", []))

                # 每5页更新一次状态
                if page % 5 == 0 or page == self.totalPages:
                    self._emitProgress(f"已处理 {page}/{self.totalPages} 页")

            if not self.isRunning:
                errorMsg = "下载已取消"
                self.failed.emit(errorMsg)
                self.finished.emit(False, errorMsg)
                return

            # 合并数据
            mergedData = []
            for dataChunk in allData:
                if dataChunk:
                    mergedData.extend(dataChunk)

            if not mergedData:
                errorMsg = "没有有效数据"
                self.failed.emit(errorMsg)
                self.finished.emit(False, errorMsg)
                return

            # 第三步：保存为Excel
            logger.info(
                f"[HSK] 开始保存Excel, taskId={self.taskId}, 数据条数={len(mergedData)}"
            )

            # 生成文件名
            fileNameParts = ["HSK"]

            if "keyword" in self.payload:
                keyword = self.payload["keyword"]
                keyword = "".join(
                    c for c in keyword if c.isalnum() or c in (" ", "-", "_")
                )
                fileNameParts.append(keyword)
            elif "keystr" in self.payload:
                keystr = self.payload["keystr"]
                keystr = "".join(
                    c for c in keystr if c.isalnum() or c in (" ", "-", "_")
                )
                fileNameParts.append(keystr)

            fileNameParts.append(f"{len(mergedData)}条")
            fileNameParts.append(datetime.now().strftime("%Y%m%d_%H%M"))

            safeFileName = "_".join(fileNameParts) + ".xlsx"
            outputPath = os.path.join(self.outputDir, safeFileName)

            self._emitProgress("正在生成Excel文件...")

            success = self._processDataToExcel(mergedData, outputPath)

            if success:
                self.completedPages = self.totalPages
                self._emitProgress("下载完成")
                # 保存文件路径供打开文件夹使用
                self.filePath = outputPath

                elapsed = time.time() - self.startTime
                avgSpeed = self.totalPages / elapsed if elapsed > 0 else 0

                # 更新任务信息
                try:
                    fileSize = (
                        os.path.getsize(outputPath)
                        if os.path.exists(outputPath)
                        else None
                    )
                    taskControl.updateDownloadInfo(
                        self.taskId,
                        downloadPath=os.path.dirname(outputPath),
                        taskName=os.path.splitext(safeFileName)[0],
                        fileSize=fileSize,
                        fileName=safeFileName,
                    )
                except Exception as e:
                    logger.error(f"[HSK] 更新下载信息时出错: {e}")

                self.finished.emit(
                    True,
                    f"下载完成！共{len(mergedData)}条数据，平均速度{avgSpeed:.2f}页/秒",
                )
            else:
                errorMsg = "生成Excel文件失败"
                self.failed.emit(errorMsg)
                self.finished.emit(False, errorMsg)

        except Exception as e:
            errorMsg = f"处理失败: {str(e)}"
            self.failed.emit(errorMsg)
            self.finished.emit(False, errorMsg)
