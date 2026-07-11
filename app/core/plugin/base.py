# coding: utf-8
"""
插件基类
所有插件必须继承此基类
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class PluginBase(ABC):
    """插件基类，所有插件必须继承此类"""

    # 插件元数据（子类可覆盖）
    manifest = {
        "id": "",
        "name": "",
        "version": "1.0.0",
        "apiVersion": "1.0",
        "description": "",
        "author": "",
        "category": "tool",
        "permissions": [],
        "dependencies": {},
        "entry": "plugin.py",
        "minAppVersion": "1.0.0",
    }

    def __init__(self):
        self.enabled = False
        self.settings = {}

    @abstractmethod
    def onLoad(self) -> bool:
        """
        插件加载时调用
        返回 True 表示加载成功，False 表示加载失败
        """
        pass

    @abstractmethod
    def onActivate(self):
        """插件激活时调用"""
        pass

    @abstractmethod
    def onDeactivate(self):
        """插件停用时调用"""
        pass

    def onUnload(self):
        """插件卸载时调用（可选）"""
        pass

    def getInterface(self):
        """
        获取插件界面组件

        Returns:
            插件的UI组件（QWidget），如果插件没有界面则返回None
        """
        return None

    def getIconPath(self) -> str:
        """
        获取插件图标路径

        Returns:
            图标文件路径，支持 .png, .svg 等格式
            如果返回空字符串，则使用默认图标
        """
        return ""

    def getSettings(self) -> Dict[str, Any]:
        """获取插件设置"""
        return self.settings.copy()

    def updateSettings(self, settings: Dict[str, Any]):
        """更新插件设置"""
        self.settings.update(settings)

    def getMeta(self, key: str, default: Any = None) -> Any:
        """获取插件元数据"""
        return self.manifest.get(key, default)

    @property
    def pluginId(self) -> str:
        """获取插件ID"""
        return self.manifest.get("id", "")

    @property
    def pluginName(self) -> str:
        """获取插件名称"""
        return self.manifest.get("name", "")

    @property
    def pluginVersion(self) -> str:
        """获取插件版本"""
        return self.manifest.get("version", "")


class PluginManifest:
    """插件清单解析器"""

    def __init__(self, data: Dict[str, Any]):
        self.data = data

    @property
    def pluginId(self) -> str:
        return self.data.get("id", "")

    @property
    def name(self) -> str:
        return self.data.get("name", "")

    @property
    def version(self) -> str:
        return self.data.get("version", "1.0.0")

    @property
    def apiVersion(self) -> str:
        return self.data.get("apiVersion", "1.0")

    @property
    def description(self) -> str:
        return self.data.get("description", "")

    @property
    def author(self) -> str:
        return self.data.get("author", "")

    @property
    def category(self) -> str:
        return self.data.get("category", "tool")

    @property
    def permissions(self) -> List[str]:
        return self.data.get("permissions", [])

    @property
    def dependencies(self) -> Dict[str, List[str]]:
        return self.data.get("dependencies", {})

    @property
    def entry(self) -> str:
        return self.data.get("entry", "plugin.py")

    @property
    def minAppVersion(self) -> str:
        return self.data.get("minAppVersion", "1.0.0")

    def isValid(self) -> bool:
        """检查清单是否有效"""
        requiredFields = ["id", "name", "version", "entry"]
        return all(self.data.get(field) for field in requiredFields)


class PluginMetadata:
    """插件元数据容器"""

    def __init__(self, manifest: PluginManifest, path: str):
        self.manifest = manifest
        self.path = path
        self.instance: Optional[PluginBase] = None
        self.enabled = False
        self.loaded = False
        self.iconPath = ""  # 插件图标路径
        self.missingDeps = []  # 缺失的依赖列表

    @property
    def pluginId(self) -> str:
        return self.manifest.pluginId

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def category(self) -> str:
        return self.manifest.category

    @property
    def description(self) -> str:
        return self.manifest.description

    def __repr__(self):
        return f"<Plugin {self.pluginId} v{self.version} ({'enabled' if self.enabled else 'disabled'})>"


# 导出
__all__ = ["PluginBase", "PluginManifest", "PluginMetadata"]
