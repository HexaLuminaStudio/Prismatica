# coding: utf-8
"""
HSK语料库服务模块
提供HSK相关的网络请求服务
"""

import json
import os
import requests
from PySide6.QtCore import QThread, Signal


def _get_hsk_credentials() -> dict:
    """获取HSK登录凭证，优先从环境变量读取"""
    return {
        "username": os.getenv("HSK_USERNAME", ""),
        "password": os.getenv("HSK_PASSWORD", ""),
    }


class HskTokenRefreshThread(QThread):
    """HSK Token刷新线程"""

    finished = Signal(str)  # 完成信号，传递获取到的token
    error = Signal(str)  # 错误信号，传递错误信息

    def run(self):
        """执行刷新请求"""
        # 从环境变量或配置获取凭证
        credentials = _get_hsk_credentials()
        username = credentials.get("username")
        password = credentials.get("password")

        if not username or not password:
            self.error.emit("请在环境变量中配置 HSK_USERNAME 和 HSK_PASSWORD")
            return

        try:
            url = "https://hsk.blcu.edu.cn/api/v1/login/access-token"
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            payload = {"username": username, "password": password}

            response = requests.post(url, headers=headers, json=payload, timeout=30)

            # 获取响应内容
            responseText = response.text.strip()

            # 检查响应是否为空
            if not responseText:
                self.error.emit("服务器响应为空")
                return

            # 尝试解析JSON
            try:
                result = json.loads(responseText)
            except json.JSONDecodeError:
                errorMsg = "响应格式错误: " + responseText[:500]
                self.error.emit(errorMsg)
                return

            # 检查业务状态码
            if result.get("code") == 0 and "data" in result:
                token = result["data"]
                self.finished.emit(token)
            else:
                errorMsg = result.get("msg", "未知错误")
                self.error.emit(errorMsg)

        except requests.Timeout:
            self.error.emit("请求超时，请检查网络连接")
        except requests.ConnectionError:
            self.error.emit("网络连接失败，请检查网络")
        except requests.RequestException as e:
            self.error.emit("网络请求错误: " + str(e))
        except Exception as e:
            self.error.emit("刷新异常: " + str(e))


class GetTotalWorker(QThread):
    """获取语料总数的线程，避免阻塞主界面"""

    finished = Signal(int)  # 成功信号
    failed = Signal(str)  # 失败信号

    def __init__(self, payload: dict):
        super().__init__()
        self.isRunning = True
        self.token = None
        self.maxRetries = 3

        # 请求参数
        self.url = payload.get(
            "url", "https://hsk.blcu.edu.cn/api/v1/sentence/search/keyword"
        )
        self.searchPayload = payload.get("payload", {})
        self.searchPayload["per_page"] = 20

        # 延迟导入避免循环依赖
        from app.core.utils.config import qconfig, Config

        # 从配置获取Token，确保格式正确
        rawToken = qconfig.get(Config.HSKLoginToken)
        if rawToken:
            # 如果Token已经有Bearer前缀，直接使用
            if rawToken.startswith("Bearer "):
                self.token = rawToken
            else:
                # 否则添加Bearer前缀
                self.token = f"Bearer {rawToken}"

        self.maxRetries = qconfig.get(Config.MaximumAttempts)

    def stop(self):
        """停止线程"""
        self.isRunning = False

    def run(self):
        self.msleep(500)  # 延迟启动，避免429错误

        for attempt in range(self.maxRetries):
            if not self.isRunning:
                return

            headers = {
                "Accept": "application/json",
                "Authorization": self.token,
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

                # 解析响应
                data = response.json()
                if data.get("code") == 0:
                    total = data.get("total", 0)
                    self.finished.emit(total)
                    return
                else:
                    errorMsg = f"API错误: {data.get('msg', '未知错误')}"
                    if attempt == self.maxRetries - 1:
                        self.failed.emit(errorMsg)
                        return
                    self.msleep(1000 * (attempt + 1))  # 递增延迟
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
                    self.failed.emit(f"请求失败: {str(e)}")
                    return
                self.msleep(1000 * (attempt + 1))
                continue
