# coding:utf-8
"""
加密工具模块
提供AES加密、AES-GCM加密、PBKDF2密钥派生、SHA-256哈希等功能
"""

import base64
import hashlib
import json
import os

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

ENCRYPTKEY = b"lunminalinguaai_"

# PBKDF2 迭代次数
PBKDF2_ITERATIONS = 100000


class AESCipher:
    def __init__(self, key):
        """
        初始化AES加密器
        :param key: 加密密钥（16/24/32字节），可以是字符串或字节
        """
        if isinstance(key, str):
            key = key.encode("utf-8")
        if len(key) not in [16, 24, 32]:
            raise ValueError("密钥长度必须为16、24或32字节")
        self.key = key

    def encrypt(self, data):
        """
        加密数据（支持字符串/列表/字典等JSON可序列化类型）
        :param data: 要加密的数据
        :return: 返回Base64编码的加密字符串
        """
        # 生成随机初始化向量
        iv = get_random_bytes(AES.block_size)

        # 创建AES加密器
        cipher = AES.new(self.key, AES.MODE_CBC, iv)

        # 序列化数据为JSON字符串并编码为字节
        jsonData = json.dumps(data)
        plainBytes = jsonData.encode("utf-8")

        # 填充并加密数据
        paddedBytes = pad(plainBytes, AES.block_size)
        cipherBytes = cipher.encrypt(paddedBytes)

        # 组合IV和密文并进行Base64编码
        encryptedData = iv + cipherBytes
        return base64.b64encode(encryptedData).decode("utf-8")

    def decrypt(self, encData):
        """
        解密数据并恢复原始格式
        :param encData: Base64编码的加密字符串
        :return: 原始数据（保持原始格式）
        """
        # Base64解码
        encryptedData = base64.b64decode(encData)

        # 提取初始化向量
        iv = encryptedData[: AES.block_size]
        cipherBytes = encryptedData[AES.block_size :]

        # 创建AES解密器
        cipher = AES.new(self.key, AES.MODE_CBC, iv)

        # 解密并去除填充
        decryptedBytes = cipher.decrypt(cipherBytes)
        unpaddedBytes = unpad(decryptedBytes, AES.block_size)

        # 解码JSON并恢复原始数据结构
        jsonData = unpaddedBytes.decode("utf-8")
        return json.loads(jsonData)


class AESCipherGCM:
    """AES-256-GCM 加密器，提供更强的安全性（包含认证标签）"""

    def __init__(self, key):
        """
        初始化AES-256-GCM加密器
        :param key: 加密密钥（32字节），可以是字符串或字节
        """
        if isinstance(key, str):
            key = key.encode("utf-8")
        if len(key) != 32:
            raise ValueError("GCM模式密钥长度必须为32字节")
        self.key = key

    def encrypt(self, data):
        """
        加密数据并生成认证标签
        :param data: 要加密的数据（字符串或字节）
        :return: Base64编码的加密数据（包含nonce + 密文 + 标签）
        """
        if isinstance(data, str):
            data = data.encode("utf-8")

        # 生成随机nonce（12字节，GCM推荐）
        nonce = get_random_bytes(12)

        # 创建GCM加密器
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)

        # 加密数据
        cipherText, tag = cipher.encrypt_and_digest(data)

        # 组合 nonce + 密文 + 认证标签
        encryptedData = nonce + cipherText + tag
        return base64.b64encode(encryptedData).decode("utf-8")

    def decrypt(self, encData):
        """
        解密数据并验证认证标签
        :param encData: Base64编码的加密数据
        :return: 解密后的原始数据
        :raises ValueError: 认证标签验证失败时抛出
        """
        # Base64解码
        encryptedData = base64.b64decode(encData)

        # 提取 nonce、密文和标签
        nonce = encryptedData[:12]
        tag = encryptedData[-16:]
        cipherText = encryptedData[12:-16]

        # 创建GCM解密器
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)

        # 解密并验证标签
        try:
            plainData = cipher.decrypt_and_verify(cipherText, tag)
            return plainData.decode("utf-8")
        except ValueError:
            raise ValueError("数据完整性验证失败，可能被篡改")


def deriveKey(
    password: str,
    salt: bytes = None,
    iterations: int = PBKDF2_ITERATIONS,
    keyLength: int = 32,
) -> tuple:
    """
    使用PBKDF2算法从密码派生密钥

    :param password: 密码字符串
    :param salt: 盐值（可选，不提供则自动生成）
    :param iterations: 迭代次数，默认100000
    :param keyLength: 密钥长度，默认32字节（256位）
    :return: (派生的密钥, 盐值) 元组
    """
    if salt is None:
        salt = get_random_bytes(32)

    if isinstance(password, str):
        password = password.encode("utf-8")

    # 使用PBKDF2派生密钥
    key = hashlib.pbkdf2_hmac("sha256", password, salt, iterations, dklen=keyLength)

    return key, salt


def hash256(data: str) -> str:
    """
    计算字符串的SHA-256哈希值

    :param data: 要哈希的数据
    :return: 十六进制哈希字符串
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


encrypt = AESCipher(ENCRYPTKEY)


# 密码验证
import bcrypt


def hashPassword(password: str) -> str:
    """哈希密码，处理长度限制"""
    # 对密码进行预处理，确保不超过72字节
    passwordBytes = password.encode("utf-8")
    if len(passwordBytes) > 72:
        # 使用SHA256哈希长密码
        passwordBytes = hashlib.sha256(passwordBytes).digest()

    # 生成盐并哈希密码
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(passwordBytes, salt)
    return hashed.decode("utf-8")


def verifyPassword(plainPassword: str, hashedPassword: str) -> bool:
    """验证密码"""
    try:
        # 同样的预处理
        passwordBytes = plainPassword.encode("utf-8")
        if len(passwordBytes) > 72:
            passwordBytes = hashlib.sha256(passwordBytes).digest()

        hashedBytes = hashedPassword.encode("utf-8")
        return bcrypt.checkpw(passwordBytes, hashedBytes)
    except Exception as e:
        return False


if __name__ == "__main__":
    # 测试AES-CBC加密
    data = "sk-3e47a49bf60e49e8ab08bb1f1550aa86"
    encData = encrypt.encrypt(data)
    de = encrypt.decrypt(
        "VRldfRScA0HPBObO6MYivEYLlBAt1jz9jr7676mQzT4BlAlq5Q9JeVy9wNtk+/ALHE6s5xOsAC3BEtQTjMufOA=="
    )
    print(f"AES-CBC 测试: {de}")

    # 测试PBKDF2密钥派生
    key, salt = deriveKey("test_password")
    print(f"PBKDF2 密钥派生: {key.hex()[:32]}...")

    # 测试AES-GCM加密
    gcmCipher = AESCipherGCM(key)
    encrypted = gcmCipher.encrypt("敏感数据")
    decrypted = gcmCipher.decrypt(encrypted)
    print(f"AES-GCM 测试: {decrypted}")
