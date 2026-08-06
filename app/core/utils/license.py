# coding:utf-8
"""
激活码验证模块
提供激活码生成、验证和激活状态管理功能

2026-08-06 简化:
    - 删除本地内测时间锁(BetaTimelock / BETA_HARD_DEADLINE / BETA_MAX_VALID_DAYS)
      全部授权与有效期由云端 PrismaticaAPI 接管,本地不再做日期限制。
    - 保留本地 HMAC 激活码的生成 / 验签能力,以兼容运营 CLI 工具。
"""

import base64
import hashlib
import hmac
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .encryption import AESCipherGCM, deriveKey, hash256
from .setting import LICENSE_SECRET

# P0-A2 fix 2026-07-18:改用统一的 loguru logger,享受敏感信息过滤 + 文件轮转
from app.core.utils import logger


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
            logger.warning(f"[License] 加载激活数据失败: {e}")
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
            logger.warning(f"[License] 保存激活数据失败: {e}")
            return False

    def isActivated(self) -> bool:
        """检查是否已激活(本地 HMAC 激活码体系,2026-08-06 仅保留兼容路径)。

        激活判断条件(必须同时满足):
            1. activationData 不为 None
            2. 包含 validityPeriod 字段
            3. 当前日期 <= validityPeriod
            4. 包含 deviceCode 字段(绑定设备)
        """
        if self.activationData is None:
            return False

        # 必须有有效期字段才视为正式激活
        validityPeriod = self.activationData.get("validityPeriod")
        if not validityPeriod:
            return False

        # 检查有效期
        try:
            expiryDate = datetime.strptime(validityPeriod, "%Y-%m-%d")
            if datetime.now() > expiryDate:
                return False
        except Exception:
            return False

        # 必须有设备码才算绑定设备
        if not self.activationData.get("deviceCode"):
            return False

        return True

    def getActivationInfo(self) -> dict:
        """获取激活信息"""
        if self.activationData is None:
            return {}
        return self.activationData.copy()

    def verifyActivationCode(self, activationCode: str, deviceCode: str) -> dict:
        """验证激活码(已修复:增加 HMAC-SHA256 签名校验)

        验证步骤(必须按顺序):
            1. Base64 解码激活码
            2. JSON 解析出 payload
            3. HMAC-SHA256 验签(必须先于设备码/有效期检查,
               否则攻击者可任意构造 deviceCode + 有效期绕过校验)
            4. 设备码匹配
            5. 有效期检查

        安全性:
            - 密钥来自 setting.LICENSE_SECRET(优先环境变量 LICENSE_SECRET)
            - 客户端仅持有签名密钥做验签,无法仅凭此构造合法签名
              (服务端私钥/共享密钥由发行方持有)
            - 使用 hmac.compare_digest 防御时序攻击

        Args:
            activationCode: 形如 base64(JSON(payload) + signature)
            deviceCode:     当前设备码

        Returns:
            {"success": bool, "message": str, "data": dict}
        """
        try:
            # 1) Base64 解码
            try:
                decoded = base64.b64decode(activationCode).decode()
            except Exception:
                return {"success": False, "message": "激活码格式错误"}

            # 2) JSON 解析
            try:
                licenseData = json.loads(decoded)
            except Exception:
                return {"success": False, "message": "激活码数据解析失败"}

            if not isinstance(licenseData, dict):
                return {"success": False, "message": "激活码结构非法"}

            # 3) HMAC 签名校验(必须先于设备码/有效期,防止伪造)
            signature = licenseData.get("signature")
            if not signature or not isinstance(signature, str):
                logger.warning("[License] 激活码缺少 signature 字段,拒绝激活")
                return {"success": False, "message": "激活码无效:缺少签名"}

            payloadWithoutSig = {
                k: v for k, v in licenseData.items() if k != "signature"
            }
            expectedSig = self._signActivationPayload(payloadWithoutSig)
            if not hmac.compare_digest(expectedSig, signature):
                logger.warning("[License] 激活码签名校验失败,可能为伪造")
                return {"success": False, "message": "激活码无效:签名校验失败"}

            # 4) 设备码匹配
            storedDeviceCode = licenseData.get("deviceCode")
            if storedDeviceCode != deviceCode:
                return {"success": False, "message": "激活码与设备不匹配"}

            # 5) 有效期检查
            validityPeriod = licenseData.get("validityPeriod")
            if validityPeriod:
                try:
                    expiryDate = datetime.strptime(validityPeriod, "%Y-%m-%d")
                    if datetime.now() > expiryDate:
                        return {
                            "success": False,
                            "message": f"激活码已过期(有效期至{validityPeriod})",
                        }
                except Exception:
                    pass

            # 全部通过
            return {
                "success": True,
                "message": "激活成功",
                "data": licenseData,
            }

        except Exception as e:
            logger.exception(f"[License] 激活码校验异常: {e}")
            return {"success": False, "message": f"验证失败: {str(e)}"}

    @staticmethod
    def _canonicalPayload(payload: dict) -> str:
        """构造签名前的规范化字符串。

        排序 keys + UTF-8 + ensure_ascii=False,以确保：
            - 字段顺序不影响签名结果
            - 中文/特殊字符也能正确签名
            - 跨 Python 版本表现一致

        Args:
            payload: 不含 signature 字段的字典

        Returns:
            规范化 JSON 字符串
        """
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @classmethod
    def _signActivationPayload(cls, payload: dict) -> str:
        """计算激活码 payload 的 HMAC-SHA256 签名。

        优先级与密钥来源:
            1. 环境变量 LICENSE_SECRET(推荐生产部署)
            2. setting.LICENSE_SECRET 常量(开发占位)

        Returns:
            16 进制签名字符串
        """
        canonical = cls._canonicalPayload(payload)
        return hmac.new(
            LICENSE_SECRET.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @classmethod
    def generateActivationCode(
        cls,
        deviceCode: str,
        validityPeriod: str,
        userType: str = "正式用户",
        extras: Optional[dict] = None,
    ) -> str:
        """生成已签名的激活码(仅供服务端 / 开发期使用)。

        生成的格式:
            base64( JSON( {
                "deviceCode": "...",
                "validityPeriod": "YYYY-MM-DD",
                "userType": "正式用户",
                "issuedAt": "ISO-8601",
                "extras": {...},   # 可选
                "signature": "<hmac-sha256-hex>"
            } ) )

        Args:
            deviceCode:     目标设备码
            validityPeriod: 有效期至(YYYY-MM-DD)
            userType:       用户类型字符串
            extras:         其它业务字段(版本号、套餐等)

        Returns:
            base64 编码的激活码字符串
        """
        payload: dict[str, Any] = {
            "deviceCode": deviceCode,
            "validityPeriod": validityPeriod,
            "userType": userType,
            "issuedAt": datetime.now().isoformat(),
        }
        if extras:
            payload.update(extras)
        payload["signature"] = cls._signActivationPayload(payload)
        return base64.b64encode(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")

    def activate(self, activationCode: str, deviceCode: str) -> dict:
        """激活软件(2026-08-06 简化:删除 IS_BETA 内测版硬开关)

        P1-fix(2026-07-18):返回值改为 dict,保留失败原因给上层 UI 展示。
        旧的 bool 返回会让上层吞掉所有异常,用户只看到「数据保存失败」,
        客服难以定位问题。

        Args:
            activationCode: 激活码
            deviceCode: 设备码

        Returns:
            {
                "success": bool,
                "message": str,
                "data": Optional[dict],   # 成功时为激活数据
            }
        """
        # 1) 先做 HMAC 验签 + 设备码匹配 + 有效期校验
        try:
            result = self.verifyActivationCode(activationCode, deviceCode)
        except Exception as e:
            # 不让任何异常逃逸到 UI 层 → 用 loguru 记录 + 返回明确消息
            logger.exception(f"[License] 校验激活码异常: {e}")
            return {
                "success": False,
                "message": f"校验激活码失败:{type(e).__name__}: {e}",
                "data": None,
            }

        if not result.get("success"):
            # 透传 verifyActivationCode 给出的具体原因
            return {
                "success": False,
                "message": result.get("message", "激活码无效"),
                "data": None,
            }

        # 2) 通过校验 → 保存激活数据(落盘 + 加密)
        try:
            self.activationData = result["data"]
            self.activationData["activatedAt"] = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            self.activationData["activationCode"] = (
                activationCode[:8] + "..."
            )  # 只保存激活码前8位

            if not self._saveActivationData():
                return {
                    "success": False,
                    "message": "激活数据保存失败,请检查磁盘权限或重新尝试",
                    "data": None,
                }
        except Exception as e:
            logger.exception(f"[License] 保存激活数据异常: {e}")
            return {
                "success": False,
                "message": f"保存激活数据失败:{type(e).__name__}: {e}",
                "data": None,
            }

        logger.info(f"[License] 激活成功: deviceCode={deviceCode[:16]}...")
        return {
            "success": True,
            "message": "激活成功",
            "data": self.activationData,
        }

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
