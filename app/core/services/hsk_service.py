# coding: utf-8
"""
HSK语料库服务模块
提供HSK相关的网络请求服务
"""

import json
import requests
from PySide6.QtCore import QThread, Signal


class HskTokenRefreshThread(QThread):
    """HSK Token刷新线程"""

    finished = Signal(str)
    error = Signal(str)

    def __init__(self, username=None, password=None):
        super().__init__()
        from app.core.utils.config import qconfig, Config

        if username is None:
            username = qconfig.get(Config.HSKLoginUsername)
        if password is None:
            password = qconfig.get(Config.HSKLoginPassword)

        self.username = username
        self.password = password

    def run(self):
        if not self.username or not self.password:
            self.error.emit("请先在设置中配置HSK登录账号密码")
            return

        try:
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
                self.error.emit("服务器响应为空")
                return

            result = json.loads(responseText)

            if result.get("code") == 0 and "data" in result:
                self.finished.emit(result["data"])
            else:
                self.error.emit(result.get("msg", "未知错误"))

        except requests.Timeout:
            self.error.emit("请求超时，请检查网络连接")
        except requests.ConnectionError:
            self.error.emit("网络连接失败，请检查网络")
        except requests.RequestException as e:
            self.error.emit("网络请求错误: " + str(e))
        except Exception as e:
            self.error.emit("刷新异常: " + str(e))


class GetTotalWorker(QThread):
    """获取语料总数的线程"""

    finished = Signal(int)
    failed = Signal(str)

    def __init__(self, payload):
        super().__init__()
        self.isRunning = True
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
        self.isRunning = False

    def run(self):
        if not self.token:
            self.failed.emit("请先在设置中配置HSK登录Token")
            return

        self.msleep(500)

        for attempt in range(self.maxRetries):
            if not self.isRunning:
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

                if not self.isRunning:
                    return

                data = response.json()
                if data.get("code") == 0:
                    total = data.get("total", 0)
                    self.finished.emit(total)
                    return
                else:
                    if attempt == self.maxRetries - 1:
                        msg = data.get("msg", "未知错误")
                        self.failed.emit("API错误: " + msg)
                        return
                    self.msleep(1000 * (attempt + 1))
                    continue

            except requests.exceptions.Timeout:
                if attempt == self.maxRetries - 1:
                    self.failed.emit("请求超时，请检查网络连接")
                    return
                self.msleep(1000 * (attempt + 1))
                continue
            except requests.exceptions.ConnectionError:
                if attempt == self.maxRetries - 1:
                    self.failed.emit("网络连接失败，请检查网络")
                    return
                self.msleep(1000 * (attempt + 1))
                continue
            except Exception as e:
                if attempt == self.maxRetries - 1:
                    self.failed.emit("请求失败: " + str(e))
                    return
                self.msleep(1000 * (attempt + 1))
                continue
