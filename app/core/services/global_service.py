# coding: utf-8
"""
Global语料库服务模块
提供Global相关的网络请求服务
"""

import hashlib
import json
import requests
from PySide6.QtCore import QThread, Signal


class GlobalTokenRefreshThread(QThread):
    """Global Token刷新线程"""

    finished = Signal(str)
    error = Signal(str)

    def __init__(self, userId=None, password=None):
        super().__init__()
        from app.core.utils.config import qconfig, Config

        if userId is None:
            userId = qconfig.get(Config.GlobalLoginUsername)
        if password is None:
            password = qconfig.get(Config.GlobalLoginPassword)

        self.userId = userId
        self.password = password

    @staticmethod
    def md5(text):
        """MD5加密"""
        if not isinstance(text, str):
            text = str(text)
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def run(self):
        if not self.userId or not self.password:
            self.error.emit("请先在设置中配置Global登录账号密码")
            return

        try:
            url = "https://qqk.blcu.edu.cn/sys/index/login"
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            # Global密码需要MD5加密
            encryptedPassword = self.md5(self.password)
            payload = {
                "UserID": self.userId,
                "Password": encryptedPassword,
            }

            response = requests.post(url, headers=headers, json=payload, timeout=30)
            responseText = response.text.strip()

            if not responseText:
                self.error.emit("服务器响应为空")
                return

            # 去除UTF-8 BOM
            responseText = responseText.lstrip("\ufeff")

            result = json.loads(responseText)

            if result.get("stats") == "1":
                token = result.get("token")
                if token:
                    self.finished.emit(token)
                else:
                    self.error.emit("登录失败")
            else:
                self.error.emit(result.get("msg", "登录失败"))

        except requests.Timeout:
            self.error.emit("请求超时，请检查网络连接")
        except requests.ConnectionError:
            self.error.emit("网络连接失败，请检查网络")
        except requests.RequestException as e:
            self.error.emit("网络请求错误: " + str(e))
        except Exception as e:
            self.error.emit("刷新异常: " + str(e))
