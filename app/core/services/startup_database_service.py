# coding: utf-8
"""启动期 HSK 作文数据库检查与下载服务。"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from app.core.api.database_download import (
    DatabaseDownloadCancelled,
    DatabaseDownloadError,
    streamDownload,
)
from app.core.utils import cfg, logger, qconfig
from app.core.utils.data_paths import HSK_CORPUS_DB, HSK_LOCAL_CORPUS_DB


class DatabaseResourceError(RuntimeError):
    """启动数据库资源不可用。"""


@dataclass(frozen=True)
class DatabaseResource:
    """随软件运行所需的一个数据库文件。"""

    key: str
    displayName: str
    targetPath: Path
    url: str
    tableName: str
    expectedSha256: str = ""


@dataclass(frozen=True)
class DatabaseVerificationResult:
    """单个数据库资源的深度校验结果。"""

    resource: DatabaseResource
    isValid: bool
    message: str
    rowCount: int = 0
    fileSize: int = 0


def getStartupDatabaseResources() -> List[DatabaseResource]:
    """从集中配置构建两个 HSK 作文数据库资源。"""
    return [
        DatabaseResource(
            key="hskCorpus",
            displayName="HSK 作文数据表",
            targetPath=HSK_CORPUS_DB,
            url=str(qconfig.get(cfg.hskCorpusDownloadUrl) or "").strip(),
            tableName="hsk_corpus",
            expectedSha256=str(
                qconfig.get(cfg.hskCorpusDownloadSha256) or ""
            ).strip(),
        ),
        DatabaseResource(
            key="hskLocalCorpus",
            displayName="HSK 作文正文库",
            targetPath=HSK_LOCAL_CORPUS_DB,
            url=str(qconfig.get(cfg.hskLocalCorpusDownloadUrl) or "").strip(),
            tableName="hsk_local_corpus",
            expectedSha256=str(
                qconfig.get(cfg.hskLocalCorpusDownloadSha256) or ""
            ).strip(),
        ),
    ]


class StartupDatabaseService:
    """检查、下载并原子安装启动必需数据库。"""

    def __init__(
        self,
        resources: Optional[Iterable[DatabaseResource]] = None,
        downloadFunction: Callable = streamDownload,
    ) -> None:
        self._resources = (
            list(resources) if resources is not None else getStartupDatabaseResources()
        )
        self._downloadFunction = downloadFunction

    @property
    def resources(self) -> List[DatabaseResource]:
        return list(self._resources)

    def validateDatabase(
        self,
        resource: DatabaseResource,
        databasePath: Optional[Path] = None,
    ) -> Tuple[bool, str]:
        """轻量验证 SQLite 文件、目标表和至少一行真实数据。"""
        result = self.verifyResource(
            resource,
            databasePath=databasePath,
            deepCheck=False,
        )
        return result.isValid, result.message

    def verifyResource(
        self,
        resource: DatabaseResource,
        databasePath: Optional[Path] = None,
        deepCheck: bool = True,
    ) -> DatabaseVerificationResult:
        """校验资源文件；设置页深度校验时额外执行 quick_check 与行数统计。"""
        targetPath = Path(databasePath or resource.targetPath)
        if not targetPath.is_file():
            return DatabaseVerificationResult(resource, False, "文件不存在")
        try:
            fileSize = targetPath.stat().st_size
            if fileSize < 100:
                return DatabaseVerificationResult(
                    resource,
                    False,
                    "文件为空或不完整",
                    fileSize=fileSize,
                )
            with targetPath.open("rb") as databaseFile:
                if databaseFile.read(16) != b"SQLite format 3\x00":
                    return DatabaseVerificationResult(
                        resource,
                        False,
                        "不是有效的 SQLite 数据库",
                        fileSize=fileSize,
                    )
        except OSError:
            return DatabaseVerificationResult(resource, False, "文件不可读")

        if not re.fullmatch(r"[A-Za-z0-9_]+", resource.tableName):
            return DatabaseVerificationResult(
                resource,
                False,
                "数据库表名配置无效",
                fileSize=fileSize,
            )

        try:
            connection = sqlite3.connect(
                f"file:{targetPath.resolve().as_posix()}?mode=ro",
                uri=True,
                timeout=5.0,
            )
            cursor = None
            try:
                cursor = connection.cursor()
                cursor.execute("PRAGMA query_only = ON")
                tableRow = cursor.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                    (resource.tableName,),
                ).fetchone()
                if tableRow is None:
                    return DatabaseVerificationResult(
                        resource,
                        False,
                        f"缺少数据表 {resource.tableName}",
                        fileSize=fileSize,
                    )
                if deepCheck:
                    quickCheckRows = cursor.execute("PRAGMA quick_check").fetchall()
                    if quickCheckRows != [("ok",)]:
                        return DatabaseVerificationResult(
                            resource,
                            False,
                            "SQLite 完整性检查失败",
                            fileSize=fileSize,
                        )
                    rowCount = int(
                        cursor.execute(
                            f'SELECT COUNT(*) FROM "{resource.tableName}"'
                        ).fetchone()[0]
                    )
                else:
                    dataRow = cursor.execute(
                        f'SELECT 1 FROM "{resource.tableName}" LIMIT 1'
                    ).fetchone()
                    rowCount = 1 if dataRow is not None else 0
                if rowCount <= 0:
                    return DatabaseVerificationResult(
                        resource,
                        False,
                        "数据库没有可用数据",
                        fileSize=fileSize,
                    )
            finally:
                if cursor is not None:
                    cursor.close()
                connection.close()
        except sqlite3.Error:
            return DatabaseVerificationResult(
                resource,
                False,
                "SQLite 结构损坏或不可读取",
                fileSize=fileSize,
            )
        return DatabaseVerificationResult(
            resource,
            True,
            "完整性正常" if deepCheck else "可用",
            rowCount=rowCount,
            fileSize=fileSize,
        )

    def verifyResources(self) -> List[DatabaseVerificationResult]:
        """深度校验全部启动数据库资源。"""
        return [self.verifyResource(resource) for resource in self._resources]

    def missingResources(self) -> List[DatabaseResource]:
        """返回缺失或无效的数据库资源。"""
        missingResources = []
        for resource in self._resources:
            isValid, reason = self.validateDatabase(resource)
            if isValid:
                continue
            logger.warning(
                "[StartupDatabase] 数据库需要下载: file={} reason={}",
                resource.targetPath.name,
                reason,
            )
            missingResources.append(resource)
        return missingResources

    def downloadResources(
        self,
        resources: Iterable[DatabaseResource],
        onProgress: Optional[Callable[[int, int, str, int, int, int], None]] = None,
        onStatus: Optional[Callable[[str], None]] = None,
        isCancelled: Optional[Callable[[], bool]] = None,
    ) -> None:
        """依次下载资源，校验成功后原子替换正式文件。"""
        resourceList = list(resources)
        totalCount = len(resourceList)
        for resourceIndex, resource in enumerate(resourceList, start=1):
            if isCancelled is not None and isCancelled():
                raise DatabaseDownloadCancelled("数据库下载已取消。")
            if not resource.url:
                raise DatabaseResourceError(
                    f"{resource.displayName}的下载地址尚未配置。"
                )

            targetPath = Path(resource.targetPath)
            targetPath.parent.mkdir(parents=True, exist_ok=True)
            temporaryPath = targetPath.with_name(f"{targetPath.name}.download")
            temporaryPath.unlink(missing_ok=True)
            if onStatus is not None:
                onStatus(f"正在下载{resource.displayName}…")

            def _forwardProgress(downloadedBytes: int, totalBytes: int) -> None:
                resourcePercent = (
                    min(100, int(downloadedBytes * 100 / totalBytes))
                    if totalBytes > 0
                    else -1
                )
                if onProgress is not None:
                    onProgress(
                        resourceIndex,
                        totalCount,
                        resource.displayName,
                        downloadedBytes,
                        totalBytes,
                        resourcePercent,
                    )

            try:
                self._downloadFunction(
                    resource.url,
                    temporaryPath,
                    onProgress=_forwardProgress,
                    isCancelled=isCancelled,
                    expectedSha256=resource.expectedSha256,
                )
                if onStatus is not None:
                    onStatus(f"正在校验{resource.displayName}…")
                isValid, reason = self.validateDatabase(resource, temporaryPath)
                if not isValid:
                    raise DatabaseResourceError(
                        f"{resource.displayName}校验失败：{reason}。"
                    )
                targetPath.parent.mkdir(parents=True, exist_ok=True)
                os.replace(temporaryPath, targetPath)
                logger.info(
                    "[StartupDatabase] 数据库安装完成: file={}",
                    targetPath.name,
                )
            except (DatabaseDownloadCancelled, DatabaseDownloadError, DatabaseResourceError):
                temporaryPath.unlink(missing_ok=True)
                raise
            except OSError as exc:
                temporaryPath.unlink(missing_ok=True)
                raise DatabaseResourceError(
                    f"无法安装{resource.displayName}，请检查磁盘空间和目录权限。"
                ) from exc

        remainingResources = self.missingResources()
        if remainingResources:
            names = "、".join(resource.displayName for resource in remainingResources)
            raise DatabaseResourceError(f"数据库安装后仍不可用：{names}。")


class DatabaseVerificationThread(QThread):
    """设置页使用的数据库深度校验线程。"""

    verificationFinished = Signal(object)
    verificationFailed = Signal(str)

    def __init__(
        self,
        service: Optional[StartupDatabaseService] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service or StartupDatabaseService()

    def run(self) -> None:
        try:
            self.verificationFinished.emit(self._service.verifyResources())
        except Exception as exc:
            logger.exception("[StartupDatabase] 设置页资源校验异常: {}", exc)
            self.verificationFailed.emit(str(exc))


class StartupResourcePreparationThread(QThread):
    """启动窗口使用的资源深度校验与缺失文件下载线程。"""

    progressChanged = Signal(int, str, str)
    preparationFinished = Signal(object)
    preparationFailed = Signal(str)

    def __init__(
        self,
        service: Optional[StartupDatabaseService] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service or StartupDatabaseService()

    @staticmethod
    def _formatBytes(byteCount: int) -> str:
        value = float(max(0, byteCount))
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return (
                    f"{value:.0f} {unit}"
                    if unit == "B"
                    else f"{value:.1f} {unit}"
                )
            value /= 1024
        return f"{value:.1f} GB"

    def run(self) -> None:
        try:
            self.progressChanged.emit(
                28,
                "正在校验 HSK 作文资源",
                "检查文件格式、SQLite 完整性与数据行",
            )
            results = self._service.verifyResources()
            invalidResources = [
                result.resource for result in results if not result.isValid
            ]
            if invalidResources:
                self.progressChanged.emit(
                    32,
                    "检测到资源缺失或损坏",
                    f"需要准备 {len(invalidResources)} 个数据库文件",
                )

                def _onProgress(
                    resourceIndex: int,
                    resourceCount: int,
                    displayName: str,
                    downloadedBytes: int,
                    totalBytes: int,
                    resourcePercent: int,
                ) -> None:
                    normalizedPercent = max(0, resourcePercent)
                    overallFraction = (
                        (resourceIndex - 1) + normalizedPercent / 100
                    ) / max(1, resourceCount)
                    startupPercent = 34 + int(overallFraction * 16)
                    if totalBytes > 0:
                        sizeText = (
                            f"{self._formatBytes(downloadedBytes)} / "
                            f"{self._formatBytes(totalBytes)}"
                        )
                    else:
                        sizeText = f"已下载 {self._formatBytes(downloadedBytes)}"
                    self.progressChanged.emit(
                        startupPercent,
                        f"正在下载{displayName}",
                        f"文件 {resourceIndex}/{resourceCount} · {sizeText}",
                    )

                self._service.downloadResources(
                    invalidResources,
                    onProgress=_onProgress,
                )

            self.progressChanged.emit(
                51,
                "正在复核资源文件",
                "确认数据库可读且数据完整",
            )
            finalResults = self._service.verifyResources()
            failedResults = [result for result in finalResults if not result.isValid]
            if failedResults:
                details = "；".join(
                    f"{result.resource.displayName}：{result.message}"
                    for result in failedResults
                )
                raise DatabaseResourceError(details)

            totalRows = sum(result.rowCount for result in finalResults)
            self.progressChanged.emit(
                55,
                "HSK 作文资源准备完成",
                f"2 个数据库 · 共 {totalRows:,} 条数据",
            )
            self.preparationFinished.emit(finalResults)
        except Exception as exc:
            logger.warning("[StartupDatabase] 启动资源准备失败: {}", exc)
            self.preparationFailed.emit(str(exc))
