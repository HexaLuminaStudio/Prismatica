# coding:utf-8
"""
激活码验证模块
提供激活码生成、验证和激活状态管理功能
"""

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .encryption import AESCipherGCM, deriveKey, hash256


class LicenseManager:
    """激活码管理器"""

    def __init__(self):
        """初始化激活码管理器"""
        self.activationData = None
        self._loadActivationData()

    def _getStoragePath(self) -> Path:
        """获取激活数据存储路径"""
        from app.core.utils.setting import CONFIG_FOLDER

        storagePath = CONFIG_FOLDER / "activation.dat"
        return storagePath

    def _getEncryptionKey(self) -> bytes:
        """获取加密密钥（与device_id.py保持一致）"""
        from .device_id import getDeviceIdentifier

        device = getDeviceIdentifier()
        if not device.deviceFeatures:
            device.collectDeviceFeatures()

        # 组合设备特征作为密码（与device_id.py一致）
        sortedFeatures = sorted(device.deviceFeatures.items())
        combined = "|".join(f"{k}:{v}" for k, v in sortedFeatures)

        # 使用固定的盐（基于设备特征的哈希）
        saltSource = "|".join(f"{k}:{v}" for k, v in sortedFeatures)
        fixedSalt = hash256(saltSource).encode()[:32]

        # 使用PBKDF2派生密钥（迭代100000次）
        key, _ = deriveKey(combined, iterations=100000, keyLength=32, salt=fixedSalt)

        return key

    def _loadActivationData(self) -> bool:
        """加载本地激活数据"""
        try:
            storagePath = self._getStoragePath()
            if not storagePath.exists():
                return False

            # 读取加密数据
            encryptedData = storagePath.read_bytes()

            # 解密
            key = self._getEncryptionKey()
            cipher = AESCipherGCM(key)
            jsonData = cipher.decrypt(encryptedData.decode())

            # 解析JSON
            self.activationData = json.loads(jsonData)
            return True

        except Exception as e:
            print(f"加载激活数据失败: {e}")
            return False

    def _saveActivationData(self) -> bool:
        """保存激活数据"""
        try:
            if self.activationData is None:
                return False

            storagePath = self._getStoragePath()
            storagePath.parent.mkdir(parents=True, exist_ok=True)

            # 序列化为JSON
            jsonData = json.dumps(self.activationData, ensure_ascii=False)

            # 加密
            key = self._getEncryptionKey()
            cipher = AESCipherGCM(key)
            encryptedData = cipher.encrypt(jsonData)

            # 写入文件
            storagePath.write_bytes(encryptedData.encode())
            return True

        except Exception as e:
            print(f"保存激活数据失败: {e}")
            return False

    def isActivated(self) -> bool:
        """检查是否已激活"""
        if self.activationData is None:
            return False

        # 检查有效期
        validityPeriod = self.activationData.get("validityPeriod")
        if validityPeriod:
            try:
                expiryDate = datetime.strptime(validityPeriod, "%Y-%m-%d")
                if datetime.now() > expiryDate:
                    return False
            except Exception:
                pass

        return True

    def getActivationInfo(self) -> dict:
        """获取激活信息"""
        if self.activationData is None:
            return {}
        return self.activationData.copy()

    def verifyActivationCode(self, activationCode: str, deviceCode: str) -> dict:
        """
        验证激活码

        :param activationCode: 激活码
        :param deviceCode: 设备码
        :return: 验证结果 {"success": bool, "message": str, "data": dict}
        """
        try:
            # Base64解码
            try:
                decoded = base64.b64decode(activationCode).decode()
            except Exception:
                return {"success": False, "message": "激活码格式错误"}

            # JSON解析
            try:
                licenseData = json.loads(decoded)
            except Exception:
                return {"success": False, "message": "激活码数据解析失败"}

            # 验证设备码
            storedDeviceCode = licenseData.get("deviceCode")
            if storedDeviceCode != deviceCode:
                return {"success": False, "message": "激活码与设备不匹配"}

            # 验证有效期
            validityPeriod = licenseData.get("validityPeriod")
            if validityPeriod:
                try:
                    expiryDate = datetime.strptime(validityPeriod, "%Y-%m-%d")
                    if datetime.now() > expiryDate:
                        return {
                            "success": False,
                            "message": f"激活码已过期（有效期至{validityPeriod}）",
                        }
                except Exception:
                    pass

            # 验证通过
            return {"success": True, "message": "激活成功", "data": licenseData}

        except Exception as e:
            return {"success": False, "message": f"验证失败: {str(e)}"}

    def activate(self, activationCode: str, deviceCode: str) -> bool:
        """
        激活软件

        :param activationCode: 激活码
        :param deviceCode: 设备码
        :return: 是否激活成功
        """
        # 先验证
        result = self.verifyActivationCode(activationCode, deviceCode)

        if not result["success"]:
            return False

        # 保存激活数据
        self.activationData = result["data"]
        self.activationData["activatedAt"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self.activationData["activationCode"] = (
            activationCode[:8] + "..."
        )  # 只保存激活码前8位

        return self._saveActivationData()

    def getUserType(self) -> str:
        """获取用户类型"""
        if self.activationData is None:
            return "普通用户"
        return self.activationData.get("userType", "普通用户")

    def getExpiryDate(self) -> Optional[str]:
        """获取过期日期"""
        if self.activationData is None:
            return None
        return self.activationData.get("validityPeriod")

    def getDaysRemaining(self) -> int:
        """获取剩余天数"""
        expiryDate = self.getExpiryDate()
        if not expiryDate:
            return -1

        try:
            expiry = datetime.strptime(expiryDate, "%Y-%m-%d")
            remaining = (expiry - datetime.now()).days
            return max(0, remaining)
        except Exception:
            return -1


# 创建全局单例
_licenseManager = None


def getLicenseManager() -> LicenseManager:
    """获取全局激活码管理器实例"""
    global _licenseManager
    if _licenseManager is None:
        _licenseManager = LicenseManager()
    return _licenseManager


def isActivated() -> bool:
    """检查是否已激活"""
    return getLicenseManager().isActivated()


def getUserType() -> str:
    """获取用户类型"""
    return getLicenseManager().getUserType()


def getDeviceCode() -> str:
    """获取当前设备码"""
    from .device_id import generateOrLoadDeviceId

    return generateOrLoadDeviceId()
