# coding: utf-8
"""
Global语料库服务模块
提供Global相关的网络请求服务
"""

import hashlib
import json
import requests
from PySide6.QtCore import QThread, Signal

from app.core.services.cloud_api import CloudApiError
from app.core.services.official_corpus import requestOfficialCorpusToken
from app.core.utils import log


class GlobalTokenRefreshThread(QThread):
    """Global Token刷新线程"""

    finished = Signal(str)
    error = Signal(str)

    def __init__(self, userId=None, password=None, useOfficial=None):
        super().__init__()
        from app.core.utils.config import qconfig, Config

        if useOfficial is None:
            useOfficial = (
                userId is None
                and password is None
                and qconfig.get(Config.GlobalUseOfficialAccount)
            )

        if userId is None:
            userId = qconfig.get(Config.GlobalLoginUsername)
        if password is None:
            password = qconfig.get(Config.GlobalLoginPassword)

        self.userId = userId
        self.password = password
        self.useOfficial = bool(useOfficial)

    @staticmethod
    def md5(text):
        """MD5加密"""
        if not isinstance(text, str):
            text = str(text)
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def run(self):
        if self.useOfficial:
            try:
                log.info("[GlobalTokenRefresh] 开始通过 Prismatica 官方账号刷新 Token")
                self.finished.emit(requestOfficialCorpusToken("global"))
            except CloudApiError as error:
                log.warning(
                    f"[GlobalTokenRefresh] 官方账号刷新失败: code={error.code}"
                )
                self.error.emit(error.message)
            return

        if not self.userId or not self.password:
            log.warning("[GlobalTokenRefresh] 缺少登录账号或密码,取消刷新")
            self.error.emit("请先在设置中配置Global登录账号密码")
            return

        try:
            log.info("[GlobalTokenRefresh] 开始刷新Token")
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
                log.error(
                    f"[GlobalTokenRefresh] 服务器响应为空, status={response.status_code}"
                )
                self.error.emit("服务器响应为空")
                return

            # 去除UTF-8 BOM
            responseText = responseText.lstrip("\ufeff")

            result = json.loads(responseText)

            if result.get("stats") == "1":
                token = result.get("token")
                if token:
                    log.info("[GlobalTokenRefresh] Token刷新成功")
                    self.finished.emit(token)
                else:
                    log.error("[GlobalTokenRefresh] 响应成功但缺少Token")
                    self.error.emit("登录失败")
            else:
                message = result.get("msg", "登录失败")
                log.warning(
                    f"[GlobalTokenRefresh] Token刷新被服务端拒绝, "
                    f"status={response.status_code}, message={message}"
                )
                self.error.emit(message)

        except requests.Timeout:
            log.error("[GlobalTokenRefresh] 请求超时")
            self.error.emit("请求超时，请检查网络连接")
        except requests.ConnectionError:
            log.error("[GlobalTokenRefresh] 网络连接失败")
            self.error.emit("网络连接失败，请检查网络")
        except requests.RequestException as e:
            log.error(
                f"[GlobalTokenRefresh] 网络请求失败: {type(e).__name__}: {e}"
            )
            self.error.emit("网络请求错误: " + str(e))
        except Exception as e:
            log.exception(f"[GlobalTokenRefresh] 刷新异常: {e}")
            self.error.emit("刷新异常: " + str(e))
