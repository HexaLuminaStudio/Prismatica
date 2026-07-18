# coding:utf-8
"""
激活码验证模块
提供激活码生成、验证和激活状态管理功能

新增(内测时间锁):
    - 内测期间:首次启动自动记录 start_date(加密存储)
    - 截止日 2026-7-30:硬上限,过期后无法使用
    - 安全性:由于 start_date 加密存储 + 设备特征派生密钥,
             简单修改系统时间无法绕过
"""

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .encryption import AESCipherGCM, deriveKey, hash256
from .setting import LICENSE_SECRET

logger = logging.getLogger(__name__)

# ============================================================================
# 内测时间锁常量
# ============================================================================
# 内测截止日(绝对硬上限,任何内测用户均受此日期约束)
BETA_HARD_DEADLINE = "2026-07-19"
# 内测模式最大有效期(天):从首次启动算起,防止无限延期
BETA_MAX_VALID_DAYS = 30
# 内测模式启动时记录的密钥标识(派生自设备特征,防止复制 license.dat 到另一台机器)
# 这里的常量本身不参与加密,仅作为 license.dat 内字段的命名空间
BETA_MODE = "beta_timelock"


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
        """检查是否已激活

        激活判断条件(必须同时满足):
            1. activationData 不为 None
            2. 包含 validityPeriod 字段
            3. 当前日期 <= validityPeriod
            4. 包含 deviceCode 字段(绑定设备)

        仅包含 betaLock(内测锁)不算激活 — 那是另一套机制。
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
        """激活软件

        P1-fix(2026-07-18):返回值改为 dict,保留失败原因给上层 UI 展示。
        旧的 bool 返回会让上层吞掉所有异常,用户只看到「数据保存失败」,
        客服难以定位问题。

        P1-fix(2026-07-18,二次):内测版(IS_BETA=True)下**不允许激活**,
        即便用户输入了正确的激活码,也直接拒绝并返回明确错误。
        内测用户走 beta_timelock 机制(30 天 / 2026-07-30 截止日),无需激活码。
        这是 setting.IS_BETA = True 决定的硬开关,在 publish 正式版时改为 False。

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
        # 1) 内测版硬开关:不允许激活
        try:
            from app.core.utils.setting import IS_BETA

            if IS_BETA:
                logger.warning(
                    "[License] 内测版拒绝激活请求 "
                    f"(activationCode={activationCode[:8]}...)"
                )
                return {
                    "success": False,
                    "message": (
                        "当前为内测版本,不支持激活码激活。"
                        "内测期间所有功能可免费使用,"
                        "正式版发布后再使用激活码。"
                    ),
                    "data": None,
                }
        except Exception as e:
            # 设置模块加载失败不应阻断主流程(其它地方已捕获),但这里至少打日志
            logger.warning(f"[License] 检查 IS_BETA 失败: {e}")

        # 2) 先做 HMAC 验签 + 设备码匹配 + 有效期校验
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

        # 3) 通过校验 → 保存激活数据(落盘 + 加密)
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

    # ========================================================================
    # 内测时间锁(Beta Time-Lock)
    # ========================================================================
    # 工作机制:
    #   1. 首次启动:isBetaActive() 返回 True 时调用 ensureBetaTimelock()
    #      → 记录当前日期为 beta_start_date(加密存储在 activation.dat)
    #      → 同时计算 HMAC 签名(start_date + deadline + 设备特征派生 secret)
    #   2. 后续启动:每次都验证 HMAC + 截止日 + 最大有效期
    #   3. 修改系统时间无法绕过,因为:
    #      a) start_date 一旦写入就加密,不能回拨
    #      b) 即使回拨系统时间,start_date + MAX_VALID_DAYS 已固定
    #      c) 截止日 2026-7-30 是硬上限,与系统时间无关
    #      d) license.dat 复制到另一台机器将无法解密(密钥来自设备特征)
    # ========================================================================

    def _getBetaSecret(self) -> bytes:
        """派生内测签名密钥(基于设备特征)

        Returns:
            32 字节密钥(从设备指纹派生)
        """
        from .device_id import getDeviceIdentifier

        device = getDeviceIdentifier()
        if not device.deviceFeatures:
            device.collectDeviceFeatures()

        sortedFeatures = sorted(device.deviceFeatures.items())
        # 设备特征 → 哈希作为内测签名密钥
        fingerprint = "|".join(f"{k}:{v}" for k, v in sortedFeatures)
        # 用 SHA-256 截断得到稳定 32 字节
        return hashlib.sha256(f"beta_lock_v1::{fingerprint}".encode("utf-8")).digest()

    def _computeBetaSignature(
        self,
        startDate: str,
        deadline: str,
        secret: bytes,
    ) -> str:
        """计算内测时间锁的 HMAC 签名

        Args:
            startDate: 内测启动日期 YYYY-MM-DD
            deadline:  截止日期 YYYY-MM-DD
            secret:    派生自设备特征的密钥

        Returns:
            16 进制签名字符串
        """
        payload = f"{startDate}|{deadline}|{BETA_MAX_VALID_DAYS}"
        return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def _isBetaActiveOrExpired(self) -> dict:
        """检测内测模式当前状态

        Returns:
            dict:
                status: "in_beta" | "expired_hard" | "expired_30d"
                daysRemaining: 内测剩余天数(若在期内)
                deadline: 截止日期
                startDate: 首次启动日期(若有)
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # 1. 绝对硬上限:超过 2026-7-30 立即失效
        if today > BETA_HARD_DEADLINE:
            return {
                "status": "expired_hard",
                "daysRemaining": 0,
                "deadline": BETA_HARD_DEADLINE,
                "startDate": (
                    self.activationData.get("betaStartDate")
                    if self.activationData
                    else None
                ),
                "reason": f"内测期已结束(截止 {BETA_HARD_DEADLINE})",
            }

        # 2. 检查已存储的内测起始日(若已记录)
        betaRecord = (
            self.activationData.get("betaLock") if self.activationData else None
        )
        if betaRecord:
            startDate = betaRecord.get("startDate")
            deadline = betaRecord.get("deadline", BETA_HARD_DEADLINE)
            signature = betaRecord.get("signature", "")
            secret = self._getBetaSecret()
            expectedSig = self._computeBetaSignature(startDate, deadline, secret)

            # 签名校验失败:license.dat 被篡改或复制到其他机器
            if not hmac.compare_digest(signature, expectedSig):
                logger.warning("[License] 内测时间锁签名校验失败")
                return {
                    "status": "expired_hard",
                    "daysRemaining": 0,
                    "deadline": BETA_HARD_DEADLINE,
                    "startDate": startDate,
                    "reason": "内测时间锁签名校验失败(可能 license.dat 被篡改)",
                }

            # 3. 起始日 + 30 天检查(防止无限延期)
            try:
                from datetime import timedelta

                start = datetime.strptime(startDate, "%Y-%m-%d")
                maxExpiry = start + timedelta(days=BETA_MAX_VALID_DAYS)
                deadlineDt = datetime.strptime(BETA_HARD_DEADLINE, "%Y-%m-%d")
                effectiveDeadline = min(maxExpiry, deadlineDt)
                if datetime.now() > effectiveDeadline:
                    return {
                        "status": "expired_30d",
                        "daysRemaining": 0,
                        "deadline": effectiveDeadline.strftime("%Y-%m-%d"),
                        "startDate": startDate,
                        "reason": f"内测 {BETA_MAX_VALID_DAYS} 天体验期已过",
                    }
                # 还在期内
                remaining = (effectiveDeadline - datetime.now()).days
                return {
                    "status": "in_beta",
                    "daysRemaining": max(0, remaining),
                    "deadline": effectiveDeadline.strftime("%Y-%m-%d"),
                    "startDate": startDate,
                    "reason": None,
                }
            except Exception as e:
                logger.warning(f"[License] 内测时间锁解析失败: {e}")
                return {
                    "status": "expired_hard",
                    "daysRemaining": 0,
                    "deadline": BETA_HARD_DEADLINE,
                    "startDate": startDate,
                    "reason": "内测时间锁数据损坏",
                }

        # 4. 首次启动:在硬上限前都视为可激活
        return {
            "status": "in_beta",
            "daysRemaining": -1,  # 尚未记录 startDate
            "deadline": BETA_HARD_DEADLINE,
            "startDate": None,
            "reason": None,
        }

    def ensureBetaTimelock(self) -> dict:
        """确保内测时间锁已建立(在主窗口创建前调用)

        行为:
            - 首次启动:记录 start_date = today + 签名
            - 后续启动:验证签名/截止日/有效期
            - 过期:返回 status="expired_*",主程序应阻止运行

        Returns:
            dict: 内测状态
                status: "in_beta" | "expired_hard" | "expired_30d"
                daysRemaining: int
                deadline: str
                startDate: Optional[str]
                reason: Optional[str] (仅过期时存在)
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # 1. 绝对硬上限:超过 2026-7-30 立即失效(无需任何数据)
        if today > BETA_HARD_DEADLINE:
            logger.warning(f"[License] 内测期已结束(超过 {BETA_HARD_DEADLINE})")
            return {
                "status": "expired_hard",
                "daysRemaining": 0,
                "deadline": BETA_HARD_DEADLINE,
                "startDate": None,
                "reason": f"内测期已结束(截止 {BETA_HARD_DEADLINE})",
            }

        # 2. 如果已有正式激活码,优先使用(内测锁仅适用于无激活码的用户)
        if self.activationData and self.isActivated():
            return {
                "status": "activated",
                "daysRemaining": self.getDaysRemaining(),
                "deadline": self.getExpiryDate() or "",
                "startDate": None,
                "reason": None,
            }

        # 3. 已有内测记录 → 校验
        betaRecord = (
            self.activationData.get("betaLock") if self.activationData else None
        )
        if betaRecord:
            return self._isBetaActiveOrExpired()

        # 4. 首次启动:写入内测时间锁记录
        if self.activationData is None:
            self.activationData = {}

        secret = self._getBetaSecret()
        deadline = BETA_HARD_DEADLINE
        signature = self._computeBetaSignature(today, deadline, secret)
        self.activationData["betaLock"] = {
            "startDate": today,
            "deadline": deadline,
            "maxValidDays": BETA_MAX_VALID_DAYS,
            "signature": signature,
            "createdAt": datetime.now().isoformat(),
        }
        # 同时记录激活类型
        self.activationData["mode"] = BETA_MODE
        # 写入磁盘
        if self._saveActivationData():
            logger.info(
                f"[License] 内测时间锁已建立: startDate={today}, "
                f"deadline={deadline}"
            )
        else:
            logger.error("[License] 内测时间锁写入失败")

        return {
            "status": "in_beta",
            "daysRemaining": BETA_MAX_VALID_DAYS,
            "deadline": deadline,
            "startDate": today,
            "reason": None,
        }

    def isBetaExpired(self) -> bool:
        """快速判断:内测是否已过期(用于启动时阻止主窗口)

        Returns:
            True 表示已过期,应阻止启动
        """
        status = self.ensureBetaTimelock()
        return status.get("status") in ("expired_hard", "expired_30d")


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
