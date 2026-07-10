# coding: utf-8
"""
Global下载工作线程
支持暂停、恢复、取消操作
"""

import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from loguru import logger
from PySide6.QtCore import QThread, Signal

from app.core.api.task_control import taskControl
from app.core.utils.config import qconfig, cfg


class GlobalDownloadWorker(QThread):
    """Global下载工作线程"""

    progress = Signal(dict)  # 进度信息字典
    finished = Signal(bool, str)  # 完成信号
    failed = Signal(str)  # 失败信号
    folderMissing = Signal(str)  # 文件夹缺失信号

    def __init__(self, taskInfo: Dict[str, Any]):
        super().__init__()
        self.taskId = taskInfo.get("taskId", str(int(time.time())))
        self.token = qconfig.get(cfg.GlobalLoginToken)
        self.outputDir = qconfig.get(cfg.DownloadSavePath)

        # 配置参数
        self.requestTimeout = 30  # 请求超时时间
        self.maxRetries = qconfig.get(cfg.MaximumAttempts)
        self.perPage = qconfig.get(cfg.NumberPerDownloads)

        # 状态控制
        self.isRunning = True
        self.isPaused = False

        # 进度计算
        self.startTime = None
        self.lastRequestTime = None
        self.completedPages = 0
        self.totalPages = 0

        # 请求参数
        self.url = taskInfo.get("url", "")
        self.payload = taskInfo.get("payload", {})
        self.payload["pagesize"] = self.perPage
        self.payload["token"] = self.token

        # 文件路径（完成后设置）
        self.filePath = None

        logger.info(f"[Global] 初始化下载任务, taskId={self.taskId}")

    def stop(self):
        """停止下载"""
        logger.info(f"[Global] 收到停止信号, taskId={self.taskId}")
        self.isRunning = False

    def pause(self):
        """暂停下载"""
        logger.info(f"[Global] 暂停下载, taskId={self.taskId}")
        self.isPaused = True

    def resume(self):
        """继续下载"""
        logger.info(f"[Global] 恢复下载, taskId={self.taskId}")
        self.isPaused = False

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

    def _checkOutputDir(self) -> bool:
        """检查输出目录是否存在"""
        if not os.path.exists(self.outputDir):
            logger.warning(f"[Global] 输出目录已删除: {self.outputDir}")
            self.folderMissing.emit(self.outputDir)
            return False
        return True

    def _emitProgress(self, status: str = ""):
        """发送进度更新"""
        progressInfo = self._calculateProgressInfo()
        if status:
            progressInfo["status"] = status
        self.progress.emit(progressInfo)

    def _makeRequest(self, page: int) -> Optional[Dict]:
        """发送请求，带有重试机制"""
        for attempt in range(self.maxRetries):
            if not self.isRunning:
                return None

            headers = {
                "Accept": "application/json",
                "Authorization": "Bearer login",
                "Content-Type": "application/json",
                "Cookie": "PHPSESSID=sf1brsl4k7e437ntbmuvu3fufd",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            currentPayload = self.payload.copy()
            currentPayload["currpage"] = page

            try:
                self.lastRequestTime = time.time()
                response = requests.post(
                    self.url,
                    headers=headers,
                    json=currentPayload,
                    timeout=self.requestTimeout,
                )

                if response.status_code != 200:
                    if attempt == self.maxRetries - 1:
                        return None
                    continue

                data = response.json()
                return data

            except requests.exceptions.Timeout:
                if attempt == self.maxRetries - 1:
                    return None
                continue
            except Exception:
                if attempt == self.maxRetries - 1:
                    return None
                continue

        return None

    def _processDataToExcel(self, data: List[Dict], outputPath: str) -> bool:
        """处理数据并保存为Excel"""
        try:
            if not data:
                return False

            df = pd.DataFrame(data)
            if df.empty:
                return False

            # 清理数据
            objectColumns = df.select_dtypes(include=["object"]).columns
            for col in objectColumns:
                df[col] = df[col].astype(str)

            # 处理非法字符
            from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

            def cleanIllegal(val):
                if isinstance(val, str):
                    return ILLEGAL_CHARACTERS_RE.sub("", val)
                return val

            # 保存Excel
            try:
                df.to_excel(outputPath, index=False)
            except Exception:
                for col in objectColumns:
                    df[col] = df[col].apply(cleanIllegal)
                df.to_excel(outputPath, index=False)

            return True

        except Exception as e:
            logger.error(f"[Global] 保存Excel时出错: {e}")
            return False

    def run(self):
        """执行下载任务"""
        logger.info(f"[Global] 下载任务启动, taskId={self.taskId}")
        try:
            self.startTime = time.time()
            self.lastRequestTime = self.startTime

            # 创建输出目录
            os.makedirs(self.outputDir, exist_ok=True)
            logger.debug(f"[Global] 输出目录已创建: {self.outputDir}")

            # 第一步：获取第一页数据确定总页数
            self._emitProgress("正在获取总页数...")
            firstPage = self._downloadPage(1)
            if not firstPage:
                errorMsg = "获取初始数据失败"
                self.failed.emit(errorMsg)
                self.finished.emit(False, errorMsg)
                return

            # 解析总数据量
            total = firstPage.get("count")
            if isinstance(total, list):
                total = total[0][0]["num"]

            self.totalPages = max(1, (total + self.perPage - 1) // self.perPage)

            if total == 0:
                errorMsg = "未找到相关数据"
                self.failed.emit(errorMsg)
                self.finished.emit(False, errorMsg)
                return

            # 准备数据列表
            allData = [firstPage.get("data", [])]
            if firstPage.get("data") and isinstance(firstPage.get("data")[0], list):
                allData = [firstPage.get("data")[0]]

            # 第二步：顺序下载剩余页面
            pausedShown = False
            for page in range(2, self.totalPages + 1):
                if not self.isRunning:
                    break

                # 检查是否暂停
                while self.isPaused and self.isRunning:
                    time.sleep(0.5)
                    if not pausedShown:
                        self._emitProgress("已暂停")
                        pausedShown = True

                pausedShown = False
                if not self.isRunning:
                    break

                # 每5页检查一次目标文件夹
                if page % 5 == 1 and not self._checkOutputDir():
                    self.failed.emit("目标文件夹已被删除")
                    self.finished.emit(False, "目标文件夹已被删除")
                    return

                pageData = self._downloadPage(page)
                if pageData:
                    if pageData.get("data") and isinstance(
                        pageData.get("data")[0], list
                    ):
                        allData.append(pageData.get("data")[0])
                    else:
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
            if not self._checkOutputDir():
                self.failed.emit("目标文件夹已被删除")
                self.finished.emit(False, "目标文件夹已被删除")
                return

            logger.info(
                f"[Global] 开始保存Excel, taskId={self.taskId}, 数据条数={len(mergedData)}"
            )

            # 生成文件名
            fileNameParts = ["Global"]

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
                        downloadPath=outputPath,  # 存完整路径（含文件名）
                        taskName=os.path.splitext(safeFileName)[0],
                        fileSize=fileSize,
                        fileName=safeFileName,
                    )
                    logger.info(f"[Global] 更新下载信息成功: {outputPath}")
                except Exception as e:
                    logger.error(f"[Global] 更新下载信息时出错: {e}")

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

    def _downloadPage(self, page: int) -> Optional[Dict]:
        """下载单页数据"""
        if not self.isRunning:
            return None

        result = self._makeRequest(page)
        if result:
            self.completedPages += 1
            self._emitProgress(f"已下载第{page}页")

        return result


class GlobalGetTotalWorker(QThread):
    """获取Global语料总数"""

    finished = Signal(int)  # 完成信号
    failed = Signal(str)  # 失败信号

    def __init__(self, taskInfo: Dict[str, Any]):
        super().__init__()
        self.isRunning = True
        self.token = qconfig.get(cfg.GlobalLoginToken)
        self.maxRetries = 3

        self.url = taskInfo.get("url", "")
        self.payload = taskInfo.get("payload", {})
        self.payload["pagesize"] = qconfig.get(cfg.NumberPerDownloads)
        self.payload["token"] = self.token

    def stop(self):
        """停止线程"""
        self.isRunning = False

    def _makeRequest(self) -> Optional[Dict]:
        """发送请求"""
        for attempt in range(self.maxRetries):
            if not self.isRunning:
                return None

            headers = {
                "Accept": "application/json",
                "Authorization": "Bearer login",
                "Content-Type": "application/json",
                "Cookie": "PHPSESSID=sf1brsl4k7e437ntbmuvu3fufd",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            currentPayload = self.payload.copy()
            currentPayload["currpage"] = 1

            try:
                if not self.isRunning:
                    return None
                response = requests.post(
                    self.url,
                    headers=headers,
                    json=currentPayload,
                    timeout=30,
                )

                if response.status_code != 200:
                    if attempt == self.maxRetries - 1:
                        return None
                    continue

                data = response.json()
                return data

            except Exception:
                if attempt == self.maxRetries - 1:
                    return None
                continue

        return None

    def run(self):
        """执行任务"""
        try:
            firstPage = self._makeRequest()
            if not firstPage:
                self.failed.emit("获取初始数据失败")
                return

            # 解析总数据量
            total = firstPage.get("count")
            if isinstance(total, list):
                total = total[0][0]["num"]

            if total == 0:
                self.failed.emit("未找到相关数据")
                return

            self.finished.emit(total)

        except Exception as e:
            self.failed.emit(f"处理失败: {str(e)}")
