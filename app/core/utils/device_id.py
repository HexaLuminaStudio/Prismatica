# coding:utf-8
"""
设备标识模块
负责设备指纹采集、加密存储和自动登录功能
"""

import platform
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from app.core.utils import logger

from .encryption import AESCipherGCM, deriveKey, hash256


_IS_WINDOWS = sys.platform.startswith("win")
# Windows: 隐藏子进程控制台窗口,防止 GUI 进程启动 wmic 时弹出 conhost 终端
_CREATE_NO_WINDOW = 0x08000000
_deviceIdentifierLock = threading.RLock()


def _runHidden(cmd, timeout=5):
    """运行子进程但不弹出控制台窗口。

    Windows 下 subprocess 启动外部命令时,若无创建标志,系统会为该子进程
    派生一个 conhost.exe 窗口。对于 GUI 程序(主进程无控制台),这部分窗口
    会残留在桌面上,且部分用户的 wmic 在兼容层下会卡住,导致"启动期弹出
    多个终端且无法关闭"的问题。这里统一加 CREATE_NO_WINDOW 标志。
    """
    kwargs = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if _IS_WINDOWS:
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    return subprocess.run(cmd, **kwargs)


# P1-fix 2026-07-18:设备特征采集的安全阈值。
# 采集到的非空特征数量必须达到这个最小值,否则视为环境异常
# (例如 Windows 沙箱禁止 wmic、Linux 容器缺少 /proc 等)。
# 此时生成的设备码碰撞概率会显著升高,直接抛错比继续返回
# 「看似合法」的设备码更安全。
_MIN_REQUIRED_FEATURES = 3


class DeviceIdentifier:
    """设备标识管理器"""

    def __init__(self, storagePath: str = None):
        """
        初始化设备标识管理器

        :param storagePath: 设备标识存储路径，默认使用 %APPDATA%\\Prismatica\\device.bin
        """
        self.storagePath = storagePath
        self.deviceId = None
        self.deviceFeatures = {}
        self._cipher = None

    def _getAppDataPath(self) -> Path:
        """获取应用数据目录路径"""
        if self.storagePath:
            return Path(self.storagePath)

        # 根据操作系统获取 APPDATA 路径
        appData = (
            Path.home() / "AppData" / "Roaming"
            if platform.system() == "Windows"
            else Path.home() / ".config"
        )
        appPath = appData / "Prismatica"
        appPath.mkdir(parents=True, exist_ok=True)
        return appPath / "device.bin"

    def _collectMacAddress(self) -> str:
        """
        采集主网卡 MAC 地址

        :return: MAC地址字符串，格式为冒号分隔
        """
        try:
            import uuid

            mac = uuid.UUID(int=uuid.getnode()).hex[-12:]
            return ":".join([mac[e : e + 2] for e in range(0, 11, 2)])
        except Exception as exc:
            logger.debug(
                "[DeviceID] MAC 地址采集失败: errorType={}",
                type(exc).__name__,
            )
            return ""

    def _collectMotherboardSerial(self) -> str:
        """
        采集主板序列号（SMBIOS UUID）

        :return: 主板序列号字符串
        """
        try:
            if platform.system() == "Windows":
                # Windows 系统：读取注册表或使用 WMI
                # 使用 _runHidden 避免弹出 conhost 终端窗口
                result = _runHidden(
                    ["wmic", "baseboard", "get", "SerialNumber"],
                    timeout=5,
                )
                serial = result.stdout.strip().split("\n")[-1].strip()
                if serial and serial != "SerialNumber":
                    return serial
        except Exception as exc:
            logger.debug(
                "[DeviceID] 主板序列号主方案失败,尝试备用方案: errorType={}",
                type(exc).__name__,
            )

        try:
            # 备选方案：使用机器的UUID
            return uuid.getnode().__str__()
        except Exception as exc:
            logger.debug(
                "[DeviceID] 主板序列号备用方案失败: errorType={}",
                type(exc).__name__,
            )
            return ""

    def _collectDiskSerial(self) -> str:
        """
        采集系统盘序列号

        :return: 硬盘序列号字符串
        """
        try:
            if platform.system() == "Windows":
                # 使用 _runHidden 避免弹出 conhost 终端窗口
                result = _runHidden(
                    ["wmic", "diskdrive", "get", "SerialNumber"],
                    timeout=5,
                )
                serial = result.stdout.strip().split("\n")[-1].strip()
                if serial and serial != "SerialNumber":
                    return serial
        except Exception as exc:
            logger.debug(
                "[DeviceID] 磁盘序列号主方案失败,尝试备用方案: errorType={}",
                type(exc).__name__,
            )

        try:
            # 备选方案：使用驱动器序列号
            import ctypes

            kernel32 = ctypes.windll.kernel32
            volumeNameBuffer = ctypes.create_unicode_buffer(261)
            fileSystemNameBuffer = ctypes.create_unicode_buffer(261)
            serialNumber = ctypes.c_ulong(0)
            maxComponentLength = ctypes.c_ulong(0)
            fileSystemFlags = ctypes.c_ulong(0)

            kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(str(Path.home().anchor)),
                volumeNameBuffer,
                261,
                ctypes.byref(serialNumber),
                ctypes.byref(maxComponentLength),
                ctypes.byref(fileSystemFlags),
                fileSystemNameBuffer,
                261,
            )
            return hex(serialNumber.value)
        except Exception as exc:
            logger.debug(
                "[DeviceID] 磁盘序列号备用方案失败: errorType={}",
                type(exc).__name__,
            )
            return ""

    def _collectDeviceName(self) -> str:
        """
        采集设备名称

        :return: 设备名称字符串
        """
        try:
            return platform.node() or platform.node()
        except Exception as exc:
            logger.debug(
                "[DeviceID] 设备名称采集失败: errorType={}",
                type(exc).__name__,
            )
            return ""

    def collectDeviceFeatures(self) -> dict:
        """
        采集设备特征信息

        :return: 设备特征字典

        P1-fix 2026-07-18:采集后校验非空特征数。少于 _MIN_REQUIRED_FEATURES
        时抛 RuntimeError,避免在 sandbox / 受限环境(如 wmic 被禁用、
        /proc 不可读)中生成「特征过少、设备码碰撞率高」的伪合法设备码,
        后续激活会被错误地批量匹配到同一设备,客服侧难以定位。
        """
        features = {
            "mac": self._collectMacAddress(),
            "motherboard": self._collectMotherboardSerial(),
            "disk": self._collectDiskSerial(),
            "hostname": self._collectDeviceName(),
            "platform": platform.system(),
            "platformVersion": platform.version(),
            "processor": platform.processor(),
        }

        # 过滤空值
        self.deviceFeatures = {k: v for k, v in features.items() if v}

        # 校验特征数量
        featureCount = len(self.deviceFeatures)
        if featureCount < _MIN_REQUIRED_FEATURES:
            missingKeys = [k for k, v in features.items() if not v]
            msg = (
                f"设备特征采集不足:仅 {featureCount}/{len(features)} 项,"
                f"缺少:{', '.join(missingKeys)}。"
                f"低于安全阈值 {_MIN_REQUIRED_FEATURES},无法生成可靠的设备码。"
            )
            logger.error(f"[DeviceID] {msg}")
            # 抛错而不是返回不可靠结果,这样上层可以在启动时给用户
            # 明确的提示(沙箱/权限问题),而不是默默生成一个错误码
            self.deviceFeatures = {}
            raise RuntimeError(msg)

        logger.debug(
            f"[DeviceID] 采集到 {featureCount} 项特征: "
            f"{list(self.deviceFeatures.keys())}"
        )
        return self.deviceFeatures

    def generateDeviceId(self) -> str:
        """
        生成设备唯一标识（DFID）
        使用SHA-256哈希所有设备特征组合

        :return: 设备唯一标识符（64位十六进制字符串）
        """
        if not self.deviceFeatures:
            self.collectDeviceFeatures()

        # 按键排序确保一致性
        sortedFeatures = sorted(self.deviceFeatures.items())
        combined = "|".join(f"{k}:{v}" for k, v in sortedFeatures)

        # SHA-256哈希
        deviceId = hash256(combined)
        self.deviceId = deviceId
        return deviceId

    def deriveEncryptionKey(self) -> bytes:
        """
        从设备特征派生加密密钥

        :return: 32字节的加密密钥
        """
        if not self.deviceFeatures:
            self.collectDeviceFeatures()

        # 组合设备特征作为密码
        sortedFeatures = sorted(self.deviceFeatures.items())
        combined = "|".join(f"{k}:{v}" for k, v in sortedFeatures)

        # 使用固定的盐（基于设备特征的哈希）确保每次派生相同密钥
        saltSource = "|".join(f"{k}:{v}" for k, v in sortedFeatures)
        fixedSalt = hash256(saltSource).encode()[:32]

        # 使用PBKDF2派生密钥（迭代100000次）
        key, _ = deriveKey(combined, iterations=100000, keyLength=32, salt=fixedSalt)

        return key

    def save(self, salt: bytes = None) -> bool:
        """
        保存设备标识到本地文件

        :param salt: 可选的盐值，不提供则自动生成
        :return: 保存是否成功
        """
        try:
            if not self.deviceId:
                self.generateDeviceId()

            # 派生加密密钥
            key = self.deriveEncryptionKey()

            # 创建GCM加密器
            self._cipher = AESCipherGCM(key)

            # 准备存储数据
            storageData = {
                "deviceId": self.deviceId,
                "features": self.deviceFeatures,
                "platform": platform.system(),
                "timestamp": str(__import__("time").time()),
            }

            # 使用AES-256-GCM加密
            import json

            jsonData = json.dumps(storageData, ensure_ascii=False)
            encryptedData = self._cipher.encrypt(jsonData)

            # 保存到文件
            storagePath = self._getAppDataPath()
            storagePath.write_bytes(encryptedData.encode("utf-8"))

            logger.info(
                "[DeviceID] 设备标识已保存: storage={} featureCount={}",
                storagePath.name,
                len(self.deviceFeatures),
            )
            return True

        except Exception:
            logger.exception("[DeviceID] 保存设备标识失败")
            return False

    def load(self) -> bool:
        """
        从本地文件加载设备标识

        :return: 加载是否成功
        """
        try:
            storagePath = self._getAppDataPath()

            if not storagePath.exists():
                logger.debug("[DeviceID] 本地设备标识不存在: storage={}", storagePath.name)
                return False

            # 读取加密数据
            encryptedData = storagePath.read_bytes().decode("utf-8")

            # 先采集当前设备特征用于派生密钥
            self.collectDeviceFeatures()

            # 派生解密密钥
            key = self.deriveEncryptionKey()

            # 创建GCM解密器
            self._cipher = AESCipherGCM(key)

            # 解密数据
            decryptedData = self._cipher.decrypt(encryptedData)

            # 解析JSON
            import json

            storageData = json.loads(decryptedData)

            # 验证设备ID一致性
            currentDeviceId = self.generateDeviceId()
            if storageData.get("deviceId") == currentDeviceId:
                self.deviceId = storageData["deviceId"]
                self.deviceFeatures = storageData.get("features", {})
                logger.info(
                    "[DeviceID] 已加载并验证本地设备标识: featureCount={}",
                    len(self.deviceFeatures),
                )
                return True
            else:
                # 设备特征不匹配，可能是硬件变更
                logger.warning("[DeviceID] 设备特征已变更,本地设备标识失效")
                return False

        except Exception:
            logger.exception("[DeviceID] 加载或解密本地设备标识失败")
            return False

    def verify(self) -> bool:
        """
        验证当前设备标识是否有效

        :return: 验证是否通过
        """
        if not self.deviceId:
            return self.load()

        # 重新生成并比对
        currentId = self.generateDeviceId()
        return currentId == self.deviceId

    def reset(self) -> bool:
        """
        重置设备标识

        :return: 重置是否成功
        """
        with _deviceIdentifierLock:
            try:
                storagePath = self._getAppDataPath()
                if storagePath.exists():
                    storagePath.unlink()

                self.deviceId = None
                self.deviceFeatures = {}
                self._cipher = None

                logger.info("[DeviceID] 本地设备标识已重置")
                return True
            except Exception:
                logger.exception("[DeviceID] 重置本地设备标识失败")
                return False


# 创建全局单例
_deviceIdentifier = None


def getDeviceIdentifier() -> DeviceIdentifier:
    """
    获取全局设备标识管理器实例

    :return: DeviceIdentifier实例
    """
    global _deviceIdentifier
    with _deviceIdentifierLock:
        if _deviceIdentifier is None:
            _deviceIdentifier = DeviceIdentifier()
        return _deviceIdentifier


def generateOrLoadDeviceId() -> str:
    """
    生成或加载设备标识

    :return: 设备唯一标识符

    Raises:
        RuntimeError: 设备特征采集失败(沙箱 / 权限问题)。
        P1-fix 2026-07-18:让异常透传,而不是返回空字符串或旧 ID,
        上层 UI 可以捕获并展示明确的失败原因,客服侧可定位。
    """
    with _deviceIdentifierLock:
        device = getDeviceIdentifier()

        # 进程内已经完成加载或生成时直接复用。硬件指纹不会在一次运行期间
        # 自发变化；需要重新采集时由 reset() 显式清除缓存和持久化文件。
        if device.deviceId and device.deviceFeatures:
            return device.deviceId

        # 尝试加载现有标识
        if device.load():
            return device.deviceId

        # 生成新标识。collectDeviceFeatures() 会在特征数不足时抛 RuntimeError,
        # 此处直接透传给调用方。
        if not device.deviceFeatures:
            device.collectDeviceFeatures()
        deviceId = device.generateDeviceId()
        saved = device.save()
        if not saved:
            logger.error("[DeviceID] 新设备标识已生成,但持久化失败")

        return deviceId


if __name__ == "__main__":
    # 测试设备标识模块
    print("=== 设备标识模块测试 ===\n")

    # 创建设备标识管理器
    device = DeviceIdentifier()

    # 采集设备特征
    print("1. 采集设备特征:")
    features = device.collectDeviceFeatures()
    for key, value in features.items():
        print(f"   {key}: {value}")

    # 生成设备ID
    print("\n2. 生成设备ID:")
    deviceId = device.generateDeviceId()
    print(f"   DFID: {deviceId}")

    # 保存设备标识
    print("\n3. 保存设备标识:")
    if device.save():
        print("   保存成功!")

    # 验证设备标识
    print("\n4. 验证设备标识:")
    if device.verify():
        print("   验证通过!")

    # 重新加载设备标识
    print("\n5. 重新加载设备标识:")
    newDevice = DeviceIdentifier()
    if newDevice.load():
        print(f"   加载成功! DFID: {newDevice.deviceId}")

    print("\n所有测试完成!")
