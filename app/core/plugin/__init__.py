# coding: utf-8
"""
插件系统

提供插件管理和加载功能
"""

from .base import PluginBase, PluginManifest, PluginMetadata
from .manager import PluginManager, getPluginManager
from .config import PluginConfig, getPluginConfig

# 驼峰命名导出（兼容界面调用）
def getPluginManager() -> PluginManager:
    """获取插件管理器单例"""
    from .manager import getPluginManager as _get
    return _get()

def getPluginConfig() -> PluginConfig:
    """获取插件配置单例"""
    from .config import getPluginConfig as _get
    return _get()

# 导出
__all__ = [
    "PluginBase",
    "PluginManifest", 
    "PluginMetadata",
    "PluginManager",
    "getPluginManager",
    "PluginConfig",
    "getPluginConfig",
]
