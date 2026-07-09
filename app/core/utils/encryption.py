import base64
import json

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

# from .setting import ENCRYPTKEY
ENCRYPTKEY = b"lunminalinguaai_"


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
        json_data = json.dumps(data)
        plain_bytes = json_data.encode("utf-8")

        # 填充并加密数据
        padded_bytes = pad(plain_bytes, AES.block_size)
        cipher_bytes = cipher.encrypt(padded_bytes)

        # 组合IV和密文并进行Base64编码
        encrypted_data = iv + cipher_bytes
        return base64.b64encode(encrypted_data).decode("utf-8")

    def decrypt(self, enc_data):
        """
        解密数据并恢复原始格式
        :param enc_data: Base64编码的加密字符串
        :return: 原始数据（保持原始格式）
        """
        # Base64解码
        encrypted_data = base64.b64decode(enc_data)

        # 提取初始化向量
        iv = encrypted_data[: AES.block_size]
        cipher_bytes = encrypted_data[AES.block_size :]

        # 创建AES解密器
        cipher = AES.new(self.key, AES.MODE_CBC, iv)

        # 解密并去除填充
        decrypted_bytes = cipher.decrypt(cipher_bytes)
        unpadded_bytes = unpad(decrypted_bytes, AES.block_size)

        # 解码JSON并恢复原始数据结构
        json_data = unpadded_bytes.decode("utf-8")
        return json.loads(json_data)


encrypt = AESCipher(ENCRYPTKEY)


# 密码验证
import bcrypt
import hashlib


def hashPassword(password: str) -> str:
    """哈希密码，处理长度限制"""
    # 对密码进行预处理，确保不超过72字节
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 72:
        # 使用SHA256哈希长密码
        password_bytes = hashlib.sha256(password_bytes).digest()

    # 生成盐并哈希密码
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verifyPassword(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    try:
        # 同样的预处理
        password_bytes = plain_password.encode("utf-8")
        if len(password_bytes) > 72:
            password_bytes = hashlib.sha256(password_bytes).digest()

        hashed_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(password_bytes, hashed_bytes)
    except Exception as e:
        return False


if __name__ == "__main__":
    data = "sk-3e47a49bf60e49e8ab08bb1f1550aa86"
    enc_data = encrypt.encrypt(data)
    de = encrypt.decrypt(
        "VRldfRScA0HPBObO6MYivEYLlBAt1jz9jr7676mQzT4BlAlq5Q9JeVy9wNtk+/ALHE6s5xOsAC3BEtQTjMufOA=="
    )
