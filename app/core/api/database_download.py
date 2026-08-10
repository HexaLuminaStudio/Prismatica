# coding: utf-8
"""启动数据库文件下载接口。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests


class DatabaseDownloadError(RuntimeError):
    """数据库文件下载失败。"""


class DatabaseDownloadCancelled(DatabaseDownloadError):
    """用户取消数据库文件下载。"""


def _requestErrorMessage(exc: requests.RequestException) -> str:
    """把 requests 异常转换为不泄露下载 URL 的中文提示。"""
    response = getattr(exc, "response", None)
    statusCode = getattr(response, "status_code", None)
    if statusCode:
        return f"下载服务器返回 HTTP {statusCode}，请稍后重试。"
    if isinstance(exc, requests.Timeout):
        return "连接下载服务器超时，请检查网络后重试。"
    return "无法连接下载服务器，请检查网络后重试。"


def streamDownload(
    url: str,
    destinationPath: Path,
    onProgress: Optional[Callable[[int, int], None]] = None,
    isCancelled: Optional[Callable[[], bool]] = None,
    expectedSha256: str = "",
    chunkSize: int = 256 * 1024,
) -> int:
    """把 HTTP(S) 文件流式下载到指定临时路径。

    Args:
        url: 数据库文件直链。
        destinationPath: 临时文件路径；调用方负责最终原子替换。
        onProgress: 接收 ``(已下载字节, 总字节)``，总字节未知时为 0。
        isCancelled: 返回 True 时尽快中止下载。
        expectedSha256: 可选的 SHA-256 十六进制摘要。
        chunkSize: 单次读取大小。

    Returns:
        实际写入字节数。
    """
    normalizedUrl = str(url or "").strip()
    parsedUrl = urlparse(normalizedUrl)
    if parsedUrl.scheme not in {"http", "https"} or not parsedUrl.netloc:
        raise DatabaseDownloadError("下载地址未配置或不是有效的 HTTP(S) 直链。")

    targetPath = Path(destinationPath)
    targetPath.parent.mkdir(parents=True, exist_ok=True)
    normalizedSha256 = str(expectedSha256 or "").strip().lower()
    hasher = hashlib.sha256()
    downloadedBytes = 0

    try:
        with requests.get(
            normalizedUrl,
            stream=True,
            timeout=(10, 60),
            allow_redirects=True,
            headers={"User-Agent": "Prismatica/1.0 DatabaseBootstrap"},
        ) as response:
            response.raise_for_status()
            try:
                totalBytes = max(0, int(response.headers.get("Content-Length", 0)))
            except (TypeError, ValueError):
                totalBytes = 0

            with targetPath.open("wb") as outputFile:
                for chunk in response.iter_content(chunk_size=max(8192, int(chunkSize))):
                    if isCancelled is not None and isCancelled():
                        raise DatabaseDownloadCancelled("数据库下载已取消。")
                    if not chunk:
                        continue
                    outputFile.write(chunk)
                    hasher.update(chunk)
                    downloadedBytes += len(chunk)
                    if onProgress is not None:
                        onProgress(downloadedBytes, totalBytes)

        if downloadedBytes <= 0:
            raise DatabaseDownloadError("下载服务器返回了空文件。")
        if normalizedSha256 and hasher.hexdigest().lower() != normalizedSha256:
            raise DatabaseDownloadError("下载文件完整性校验失败，请重试。")
        return downloadedBytes
    except DatabaseDownloadCancelled:
        targetPath.unlink(missing_ok=True)
        raise
    except requests.RequestException as exc:
        targetPath.unlink(missing_ok=True)
        raise DatabaseDownloadError(_requestErrorMessage(exc)) from exc
    except DatabaseDownloadError:
        targetPath.unlink(missing_ok=True)
        raise
    except OSError as exc:
        targetPath.unlink(missing_ok=True)
        raise DatabaseDownloadError("无法写入数据库文件，请检查安装目录权限和磁盘空间。") from exc
