# coding:utf-8
"""
设备标识模块
负责设备指纹采集、加密存储和自动登录功能
"""

import hashlib
import platform
import uuid
from pathlib import Path

from .encryption import AESCipherGCM, deriveKey, hash256


class DeviceIdentifier:
    """设备标识管理器"""
    
    def __init__(self, storagePath: str = None):
        """
        初始化设备标识管理器
        
        :param storagePath: 设备标识存储路径，默认使用 %APPDATA%\Prismatica\device.bin
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
        appData = Path.home() / "AppData" / "Roaming" if platform.system() == "Windows" else Path.home() / ".config"
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
            return ":".join([mac[e:e+2] for e in range(0, 11, 2)])
        except Exception:
            return ""
    
    def _collectMotherboardSerial(self) -> str:
        """
        采集主板序列号（SMBIOS UUID）
        
        :return: 主板序列号字符串
        """
        try:
            if platform.system() == "Windows":
                # Windows 系统：读取注册表或使用 WMI
                import subprocess
                result = subprocess.run(
                    ["wmic", "baseboard", "get", "SerialNumber"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                serial = result.stdout.strip().split("\n")[-1].strip()
                if serial and serial != "SerialNumber":
                    return serial
        except Exception:
            pass
        
        try:
            # 备选方案：使用机器的UUID
            return uuid.getnode().__str__()
        except Exception:
            return ""
    
    def _collectDiskSerial(self) -> str:
        """
        采集系统盘序列号
        
        :return: 硬盘序列号字符串
        """
        try:
            if platform.system() == "Windows":
                import subprocess
                result = subprocess.run(
                    ["wmic", "diskdrive", "get", "SerialNumber"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                serial = result.stdout.strip().split("\n")[-1].strip()
                if serial and serial != "SerialNumber":
                    return serial
        except Exception:
            pass
        
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
                261
            )
            return hex(serialNumber.value)
        except Exception:
            return ""
    
    def _collectDeviceName(self) -> str:
        """
        采集设备名称
        
        :return: 设备名称字符串
        """
        try:
            return platform.node() or platform.node()
        except Exception:
            return ""
    
    def collectDeviceFeatures(self) -> dict:
        """
        采集设备特征信息
        
        :return: 设备特征字典
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
                "timestamp": str(__import__("time").time())
            }
            
            # 使用AES-256-GCM加密
            import json
            jsonData = json.dumps(storageData, ensure_ascii=False)
            encryptedData = self._cipher.encrypt(jsonData)
            
            # 保存到文件
            storagePath = self._getAppDataPath()
            storagePath.write_bytes(encryptedData.encode("utf-8"))
            
            return True
            
        except Exception as e:
            print(f"保存设备标识失败: {e}")
            return False
    
    def load(self) -> bool:
        """
        从本地文件加载设备标识
        
        :return: 加载是否成功
        """
        try:
            storagePath = self._getAppDataPath()
            
            if not storagePath.exists():
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
                return True
            else:
                # 设备特征不匹配，可能是硬件变更
                print("警告: 设备特征已变更，设备标识无效")
                return False
                
        except Exception as e:
            print(f"加载设备标识失败: {e}")
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
        try:
            storagePath = self._getAppDataPath()
            if storagePath.exists():
                storagePath.unlink()
            
            self.deviceId = None
            self.deviceFeatures = {}
            self._cipher = None
            
            return True
        except Exception as e:
            print(f"重置设备标识失败: {e}")
            return False


# 创建全局单例
_deviceIdentifier = None


def getDeviceIdentifier() -> DeviceIdentifier:
    """
    获取全局设备标识管理器实例
    
    :return: DeviceIdentifier实例
    """
    global _deviceIdentifier
    if _deviceIdentifier is None:
        _deviceIdentifier = DeviceIdentifier()
    return _deviceIdentifier


def generateOrLoadDeviceId() -> str:
    """
    生成或加载设备标识
    
    :return: 设备唯一标识符
    """
    device = getDeviceIdentifier()
    
    # 尝试加载现有标识
    if device.load():
        return device.deviceId
    
    # 生成新标识
    device.collectDeviceFeatures()
    deviceId = device.generateDeviceId()
    device.save()
    
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
