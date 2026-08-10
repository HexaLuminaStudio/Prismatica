# coding: utf-8
"""
HSK语料库服务模块
提供HSK相关的网络请求服务
"""

import json
import requests
from PySide6.QtCore import QThread, Signal

from app.core.services.cloud_api import CloudApiError
from app.core.services.official_corpus import requestOfficialCorpusToken
from app.core.utils import log


class HskTokenRefreshThread(QThread):
    """HSK Token刷新线程"""

    finished = Signal(str)
    error = Signal(str)

    def __init__(self, username=None, password=None, useOfficial=None):
        super().__init__()
        from app.core.utils.config import qconfig, Config

        if useOfficial is None:
            useOfficial = (
                username is None
                and password is None
                and qconfig.get(Config.HSKUseOfficialAccount)
            )

        if username is None:
            username = qconfig.get(Config.HSKLoginUsername)
        if password is None:
            password = qconfig.get(Config.HSKLoginPassword)

        self.username = username
        self.password = password
        self.useOfficial = bool(useOfficial)

    def run(self):
        if self.useOfficial:
            try:
                log.info("[HskTokenRefresh] 开始通过 Prismatica 官方账号刷新 Token")
                self.finished.emit(requestOfficialCorpusToken("hsk"))
            except CloudApiError as error:
                log.warning(
                    f"[HskTokenRefresh] 官方账号刷新失败: code={error.code}"
                )
                self.error.emit(error.message)
            return

        if not self.username or not self.password:
            log.warning("[HskTokenRefresh] 缺少登录账号或密码,取消刷新")
            self.error.emit("请先在设置中配置HSK登录账号密码")
            return

        try:
            log.info("[HskTokenRefresh] 开始刷新Token")
            url = "https://hsk.blcu.edu.cn/api/v1/login/access-token"
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            # HSK密码不需要加密
            payload = {"username": self.username, "password": self.password}

            response = requests.post(url, headers=headers, json=payload, timeout=30)
            responseText = response.text.strip()

            if not responseText:
                log.error(
                    f"[HskTokenRefresh] 服务器响应为空, status={response.status_code}"
                )
                self.error.emit("服务器响应为空")
                return

            result = json.loads(responseText)

            if result.get("code") == 0 and "data" in result:
                log.info("[HskTokenRefresh] Token刷新成功")
                self.finished.emit(result["data"])
            else:
                message = result.get("msg", "未知错误")
                log.warning(
                    f"[HskTokenRefresh] Token刷新被服务端拒绝, "
                    f"status={response.status_code}, message={message}"
                )
                self.error.emit(message)

        except requests.Timeout:
            log.error("[HskTokenRefresh] 请求超时")
            self.error.emit("请求超时，请检查网络连接")
        except requests.ConnectionError:
            log.error("[HskTokenRefresh] 网络连接失败")
            self.error.emit("网络连接失败，请检查网络")
        except requests.RequestException as e:
            log.error(f"[HskTokenRefresh] 网络请求失败: {type(e).__name__}: {e}")
            self.error.emit("网络请求错误: " + str(e))
        except Exception as e:
            log.exception(f"[HskTokenRefresh] 刷新异常: {e}")
            self.error.emit("刷新异常: " + str(e))


class GetTotalWorker(QThread):
    """获取语料总数的线程"""

    finished = Signal(int)
    failed = Signal(str)

    def __init__(self, payload):
        super().__init__()
        self._isRunning = True
        self.token = None
        self.maxRetries = 3

        self.url = payload.get(
            "url", "https://hsk.blcu.edu.cn/api/v1/sentence/search/keyword"
        )
        self.searchPayload = payload.get("payload", {})
        self.searchPayload["per_page"] = 20

        from app.core.utils.config import qconfig, Config

        self.token = qconfig.get(Config.HSKLoginToken)
        self.maxRetries = qconfig.get(Config.MaximumAttempts)

    def stop(self):
        self._isRunning = False

    def run(self):
        if not self.token:
            log.warning("[HskGetTotal] 缺少Token,取消查询")
            self.failed.emit("请先在设置中配置HSK登录Token")
            return

        log.info(
            f"[HskGetTotal] 开始查询语料总数, maxRetries={self.maxRetries}"
        )
        self.msleep(500)

        for attempt in range(self.maxRetries):
            if not self._isRunning:
                return

            headers = {
                "Accept": "application/json",
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            currentPayload = self.searchPayload.copy()
            currentPayload["page"] = 1

            try:
                response = requests.post(
                    self.url,
                    headers=headers,
                    json=currentPayload,
                    timeout=60,
                )

                if not self._isRunning:
                    return

                data = response.json()
                if data.get("code") == 0:
                    total = data.get("total", 0)
                    log.info(f"[HskGetTotal] 查询成功, total={total}")
                    self.finished.emit(total)
                    return
                else:
                    if attempt == self.maxRetries - 1:
                        msg = data.get("msg", "未知错误")
                        log.error(
                            f"[HskGetTotal] API最终失败, attempt={attempt + 1}, "
                            f"message={msg}"
                        )
                        self.failed.emit("API错误: " + msg)
                        return
                    log.warning(
                        f"[HskGetTotal] API返回失败,准备重试, "
                        f"attempt={attempt + 1}/{self.maxRetries}"
                    )
                    self.msleep(1000 * (attempt + 1))
                    continue

            except requests.exceptions.Timeout:
                if attempt == self.maxRetries - 1:
                    log.error(
                        f"[HskGetTotal] 请求最终超时, attempts={self.maxRetries}"
                    )
                    self.failed.emit("请求超时，请检查网络连接")
                    return
                log.warning(
                    f"[HskGetTotal] 请求超时,准备重试, "
                    f"attempt={attempt + 1}/{self.maxRetries}"
                )
                self.msleep(1000 * (attempt + 1))
                continue
            except requests.exceptions.ConnectionError:
                if attempt == self.maxRetries - 1:
                    log.error(
                        f"[HskGetTotal] 连接最终失败, attempts={self.maxRetries}"
                    )
                    self.failed.emit("网络连接失败，请检查网络")
                    return
                log.warning(
                    f"[HskGetTotal] 连接失败,准备重试, "
                    f"attempt={attempt + 1}/{self.maxRetries}"
                )
                self.msleep(1000 * (attempt + 1))
                continue
            except requests.exceptions.HTTPError as e:
                # HTTP 4xx/5xx:区分可重试(5xx)与不可重试(4xx)
                status = (
                    e.response.status_code if e.response is not None else 0
                )
                if status and 400 <= status < 500 and status != 408 and status != 429:
                    # 客户端错误(除 408/429 之外)无需重试
                    log.error(
                        f"[HskGetTotal] 请求被拒绝, status={status}, "
                        f"attempt={attempt + 1}"
                    )
                    self.failed.emit(
                        f"请求被拒绝(HTTP {status}): {str(e)[:80]}"
                    )
                    return
                if attempt == self.maxRetries - 1:
                    log.error(
                        f"[HskGetTotal] HTTP最终失败, status={status}, "
                        f"attempts={self.maxRetries}"
                    )
                    self.failed.emit(
                        f"HTTP 错误 {status}: {str(e)[:80]}"
                    )
                    return
                log.warning(
                    f"[HskGetTotal] HTTP失败,准备重试, status={status}, "
                    f"attempt={attempt + 1}/{self.maxRetries}"
                )
                self.msleep(1000 * (attempt + 1))
                continue
            except requests.exceptions.RequestException as e:
                # requests 库所有异常的基类(除 HTTPError/Timeout/ConnectionError 之外)
                # 例如 InvalidURL、TooManyRedirects、SSLError 等
                if attempt == self.maxRetries - 1:
                    log.error(
                        f"[HskGetTotal] 请求最终失败, type={type(e).__name__}, "
                        f"attempts={self.maxRetries}"
                    )
                    self.failed.emit(f"请求失败: {str(e)[:80]}")
                    return
                log.warning(
                    f"[HskGetTotal] 请求异常,准备重试, type={type(e).__name__}, "
                    f"attempt={attempt + 1}/{self.maxRetries}"
                )
                self.msleep(1000 * (attempt + 1))
                continue
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                # 响应解析阶段异常 — 不重试(服务端不会突然给出合法 JSON)
                log.error(f"[HskGetTotal] 响应解析失败: {type(e).__name__}: {e}")
                self.failed.emit(f"响应解析失败: {str(e)[:80]}")
                return
            except Exception as e:
                # 兜底:未预期的异常(如 SQLite 锁、磁盘错误等)。
                # P0-fix:不再无脑重试,直接 fail-fast,避免错误信息被吞没。
                log.exception(f"[HskGetTotal] 未预期错误: {e}")
                self.failed.emit(f"未预期错误: {type(e).__name__}: {str(e)[:80]}")
                return
