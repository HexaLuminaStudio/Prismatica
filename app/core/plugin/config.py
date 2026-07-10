# coding: utf-8
"""
插件配置管理
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger

from app.core.utils import CONFIG_FOLDER


class PluginConfig:
    """插件配置管理"""

    def __init__(self):
        self.configFile = CONFIG_FOLDER / "plugins.json"
        self.config: Dict[str, dict] = {}
        self._loadConfig()

    def _loadConfig(self):
        """加载配置文件"""
        try:
            if self.configFile.exists():
                with open(self.configFile, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            else:
                self.config = {"enabled": {}, "settings": {}}
                self._saveConfig()
        except Exception as e:
            logger.error(f"[PluginConfig] 加载配置失败: {e}")
            self.config = {"enabled": {}, "settings": {}}

    def _saveConfig(self):
        """保存配置文件"""
        try:
            self.configFile.parent.mkdir(parents=True, exist_ok=True)
            with open(self.configFile, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[PluginConfig] 保存配置失败: {e}")

    def isEnabled(self, pluginId: str) -> bool:
        """检查插件是否启用"""
        return self.config.get("enabled", {}).get(pluginId, False)

    def setEnabled(self, pluginId: str, enabled: bool):
        """设置插件启用状态"""
        if "enabled" not in self.config:
            self.config["enabled"] = {}
        self.config["enabled"][pluginId] = enabled
        self._saveConfig()

    def getPluginSettings(self, pluginId: str) -> dict:
        """获取插件设置"""
        return self.config.get("settings", {}).get(pluginId, {})

    def updatePluginSettings(self, pluginId: str, settings: dict):
        """更新插件设置"""
        if "settings" not in self.config:
            self.config["settings"] = {}
        self.config["settings"][pluginId] = settings
        self._saveConfig()

    def removePluginSettings(self, pluginId: str):
        """删除插件设置"""
        if "settings" in self.config and pluginId in self.config["settings"]:
            del self.config["settings"][pluginId]
            self._saveConfig()

    def getEnabledList(self) -> List[str]:
        """获取已启用插件列表"""
        return [
            pid for pid, enabled in self.config.get("enabled", {}).items()
            if enabled
        ]


# 全局单例
_pluginConfig: Optional[PluginConfig] = None


def getPluginConfig() -> PluginConfig:
    """获取插件配置单例"""
    global _pluginConfig
    if _pluginConfig is None:
        _pluginConfig = PluginConfig()
    return _pluginConfig
