# coding: utf-8
"""
Global语料库服务模块
提供Global相关的网络请求服务
"""

import json
import os
import re
import requests
from PySide6.QtCore import QThread, Signal


def _get_global_credentials() -> dict:
    """获取Global登录凭证，优先从环境变量读取"""
    return {
        "userID": os.getenv("GLOBAL_USER_ID", ""),
        "password": os.getenv("GLOBAL_PASSWORD", ""),
    }


class GlobalTokenRefreshThread(QThread):
    """Global Token刷新线程"""

    finished = Signal(str)  # 完成信号，传递获取到的token
    error = Signal(str)  # 错误信号，传递错误信息

    def run(self):
        """执行刷新请求"""
        # 从环境变量或配置获取凭证
        credentials = _get_global_credentials()
        userID = credentials.get("userID")
        password = credentials.get("password")

        if not userID or not password:
            self.error.emit("请在环境变量中配置 GLOBAL_USER_ID 和 GLOBAL_PASSWORD")
            return

        try:
            url = "https://qqk.blcu.edu.cn/sys/index/login"
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            payload = {
                "UserID": userID,
                "Password": password,
            }

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
                # JSON解析失败，但响应看起来是成功的
                # 尝试直接从响应文本中提取token
                tokenMatch = re.search(r'"token"\s*:\s*"([^"]+)"', responseText)
                if tokenMatch:
                    token = tokenMatch.group(1)
                    self.finished.emit(token)
                    return
                else:
                    errorMsg = "响应格式错误: " + responseText[:200]
                    self.error.emit(errorMsg)
                    return

            # 检查业务状态码
            if result.get("stats") == "1" and "token" in result:
                token = result["token"]
                self.finished.emit(token)
            else:
                errorMsg = result.get("msg", "登录失败")
                self.error.emit(errorMsg)

        except requests.Timeout:
            self.error.emit("请求超时，请检查网络连接")
        except requests.ConnectionError:
            self.error.emit("网络连接失败，请检查网络")
        except requests.RequestException as e:
            self.error.emit("网络请求错误: " + str(e))
        except Exception as e:
            self.error.emit("刷新异常: " + str(e))
