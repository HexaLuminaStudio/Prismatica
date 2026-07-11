# coding: utf-8
"""
插件管理器
负责插件的加载、卸载、启用/禁用等操作
"""

import os
import json
import importlib
import sys
from pathlib import Path
from typing import Dict, List, Optional, Callable, Tuple
from loguru import logger

from .base import PluginBase, PluginManifest, PluginMetadata
from .config import PluginConfig


class PluginManager:
    """
    插件管理器

    功能：
    - 插件加载/卸载
    - 插件启用/禁用
    - 依赖检查
    - 冲突检测
    - 生命周期管理
    """

    # 当前 API 版本
    API_VERSION = "1.0"

    def __init__(self, pluginDir: str = None):
        """
        初始化插件管理器

        Args:
            pluginDir: 插件目录路径，默认为 项目根目录/plugins
        """
        if pluginDir is None:
            # 默认插件目录：项目根目录/plugins
            from app.core.utils.setting import INSTALL_DIR

            self.pluginDir = INSTALL_DIR / "plugins"
        else:
            self.pluginDir = Path(pluginDir)

        # 确保插件目录存在
        self.pluginDir.mkdir(parents=True, exist_ok=True)

        # 插件缓存：pluginId -> PluginMetadata
        self.plugins: Dict[str, PluginMetadata] = {}

        # 回调函数
        self.onPluginLoad: List[Callable] = []
        self.onPluginUnload: List[Callable] = []
        self.onPluginEnable: List[Callable] = []
        self.onPluginDisable: List[Callable] = []

        # 插件配置
        self._config = PluginConfig()

        logger.info(f"[PluginManager] 初始化完成，插件目录: {self.pluginDir}")

    def loadPlugins(self) -> List[PluginMetadata]:
        """
        扫描并加载所有有效插件

        Returns:
            已加载的插件列表
        """
        logger.info("[PluginManager] 开始扫描插件...")
        loaded = []

        if not self.pluginDir.exists():
            logger.warning(f"[PluginManager] 插件目录不存在: {self.pluginDir}")
            return loaded

        # 扫描所有子目录
        for item in self.pluginDir.iterdir():
            if not item.is_dir():
                continue

            # 检查 manifest.json
            manifestPath = item / "manifest.json"
            if not manifestPath.exists():
                logger.debug(f"[PluginManager] 跳过无清单插件: {item.name}")
                continue

            try:
                metadata = self.loadPlugin(item)
                if metadata:
                    self.plugins[metadata.pluginId] = metadata
                    loaded.append(metadata)
                    logger.info(
                        f"[PluginManager] 发现插件: {metadata.name} v{metadata.version}"
                    )
            except Exception as e:
                logger.error(f"[PluginManager] 加载插件失败 {item.name}: {e}")

        logger.info(f"[PluginManager] 共扫描到 {len(loaded)} 个插件")

        # 恢复启用状态
        self._restoreEnabledState()

        return loaded

    def loadPlugin(self, pluginPath: Path) -> Optional[PluginMetadata]:
        """加载单个插件"""
        manifestPath = pluginPath / "manifest.json"

        # 读取清单
        with open(manifestPath, "r", encoding="utf-8") as f:
            manifestData = json.load(f)

        manifest = PluginManifest(manifestData)

        # 检查清单有效性
        if not manifest.isValid():
            raise ValueError(f"插件清单无效: {manifestPath}")

        # API 版本兼容性检查
        if not self.checkApiVersion(manifest.apiVersion):
            raise ValueError(
                f"API 版本不兼容: 需要 {manifest.apiVersion}, 当前 {self.API_VERSION}"
            )

        # 创建元数据对象
        metadata = PluginMetadata(manifest, str(pluginPath))

        # 尝试加载插件主类
        try:
            # 添加插件目录到 sys.path
            pluginPathStr = str(pluginPath)
            if pluginPathStr not in sys.path:
                sys.path.insert(0, pluginPathStr)

            # 动态导入
            entryFile = manifest.entry
            moduleName = entryFile.replace(".py", "").replace("/", ".")

            # 清理可能残留的模块缓存，避免上次加载失败留下半成品
            sys.modules.pop(moduleName, None)
            # 同时清理同包相关的相对导入缓存（按 entry 所在目录推测包名）
            module = importlib.import_module(moduleName)

            # 获取插件类
            pluginClass = getattr(module, "Plugin", None)
            if pluginClass is None:
                # 尝试查找所有 PluginBase 的子类
                for name in dir(module):
                    obj = getattr(module, name)
                    if (
                        isinstance(obj, type)
                        and issubclass(obj, PluginBase)
                        and obj is not PluginBase
                    ):
                        pluginClass = obj
                        break

            if pluginClass is None:
                raise ValueError(f"未找到插件主类: {moduleName}")

            logger.debug(f"[PluginManager] 实例化插件: {pluginClass}")
            # 创建实例（包裹防御性检查，便于定位 __dict__ 等异常来源）
            try:
                metadata.instance = pluginClass()
            except Exception as instErr:
                logger.error(
                    f"[PluginManager] 插件主类实例化失败: {pluginClass} "
                    f"(module={moduleName}): {instErr!r}"
                )
                raise

            if metadata.instance is None:
                raise RuntimeError(
                    f"插件主类实例化返回 None: {pluginClass} ({moduleName})"
                )

            logger.debug(
                f"[PluginManager] 插件实例创建成功: "
                f"{type(metadata.instance).__name__}"
            )

            # 获取插件图标路径
            try:
                if hasattr(metadata.instance, "getIconPath"):
                    iconPath = metadata.instance.getIconPath()
                    if iconPath:
                        metadata.iconPath = str(Path(pluginPath) / iconPath)
            except Exception as iconErr:
                logger.warning(
                    f"[PluginManager] 获取插件图标失败，使用默认图标: {iconErr}"
                )

            # 调用 onLoad
            if not metadata.instance.onLoad():
                logger.warning(f"[PluginManager] 插件加载失败: {metadata.name}")
                return None

            metadata.loaded = True
            logger.debug(f"[PluginManager] 插件加载成功: {metadata.name}")

            return metadata

        except Exception as e:
            import traceback

            logger.error(
                f"[PluginManager] 插件加载异常: {pluginPath}\n{traceback.format_exc()}"
            )
            raise

    def checkApiVersion(self, requiredVersion: str) -> bool:
        """检查 API 版本兼容性"""
        requiredParts = requiredVersion.split(".")
        currentParts = self.API_VERSION.split(".")

        if requiredParts[0] != currentParts[0]:
            return False

        return True

    def _restoreEnabledState(self):
        """恢复插件启用状态"""
        for pluginId, metadata in self.plugins.items():
            if self._config.isEnabled(pluginId):
                try:
                    if metadata.instance:
                        metadata.instance.onActivate()
                    metadata.enabled = True
                    logger.debug(f"[PluginManager] 恢复插件启用状态: {metadata.name}")
                except Exception as e:
                    logger.error(f"[PluginManager] 恢复插件失败 {metadata.name}: {e}")

    def enablePlugin(self, pluginId: str) -> bool:
        """
        启用插件

        Args:
            pluginId: 插件ID

        Returns:
            是否启用成功
        """
        if pluginId not in self.plugins:
            logger.warning(f"[PluginManager] 插件不存在: {pluginId}")
            return False

        metadata = self.plugins[pluginId]

        if metadata.enabled:
            logger.debug(f"[PluginManager] 插件已启用: {pluginId}")
            return True

        # 依赖检查
        depsOk, missingDeps = self.checkDependencies(metadata)
        if not depsOk:
            depList = ", ".join(missingDeps)
            logger.error(f"[PluginManager] 依赖检查失败: {pluginId}, 缺失: {depList}")
            # 保存缺失依赖信息供UI显示
            metadata.missingDeps = missingDeps
            return False

        try:
            # 激活插件
            if metadata.instance:
                metadata.instance.onActivate()

            metadata.enabled = True

            # 保存启用状态
            self._config.setEnabled(pluginId, True)

            # 触发回调
            for callback in self.onPluginEnable:
                callback(metadata)

            logger.info(f"[PluginManager] 插件已启用: {metadata.name}")
            return True

        except Exception as e:
            logger.error(f"[PluginManager] 启用插件失败: {e}")
            return False

    def disablePlugin(self, pluginId: str) -> bool:
        """
        禁用插件

        Args:
            pluginId: 插件ID

        Returns:
            是否禁用成功
        """
        if pluginId not in self.plugins:
            return False

        metadata = self.plugins[pluginId]

        if not metadata.enabled:
            return True

        try:
            if metadata.instance:
                metadata.instance.onDeactivate()

            metadata.enabled = False

            # 保存禁用状态
            self._config.setEnabled(pluginId, False)

            for callback in self.onPluginDisable:
                callback(metadata)

            logger.info(f"[PluginManager] 插件已禁用: {metadata.name}")
            return True

        except Exception as e:
            logger.error(f"[PluginManager] 禁用插件失败: {e}")
            return False

    def uninstallPlugin(self, pluginId: str) -> bool:
        """
        卸载插件

        Args:
            pluginId: 插件ID

        Returns:
            是否卸载成功
        """
        if pluginId not in self.plugins:
            return False

        metadata = self.plugins[pluginId]

        # 如果已启用，先禁用
        if metadata.enabled:
            self.disablePlugin(pluginId)

        try:
            # 调用 onUnload
            if metadata.instance and hasattr(metadata.instance, "onUnload"):
                metadata.instance.onUnload()

            # 触发回调
            for callback in self.onPluginUnload:
                callback(metadata)

            # 从缓存移除
            del self.plugins[pluginId]

            logger.info(f"[PluginManager] 插件已卸载: {metadata.name}")
            return True

        except Exception as e:
            logger.error(f"[PluginManager] 卸载插件失败: {e}")
            return False

    def checkDependencies(self, metadata: PluginMetadata) -> Tuple[bool, List[str]]:
        """
        检查依赖是否满足

        Returns:
            (是否满足, 缺失的依赖列表)
        """
        dependencies = metadata.manifest.dependencies
        missingDeps = []
        pluginPath = Path(metadata.path)

        for pkgType, packages in dependencies.items():
            if pkgType == "python":
                for package in packages:
                    # 解析包名和版本要求
                    pkgName = package.split(">")[0].split("=")[0].split("<")[0]

                    # 1. 先检查系统 Python 环境
                    try:
                        importlib.import_module(pkgName)
                        logger.debug(f"[PluginManager] 找到系统依赖: {pkgName}")
                        continue
                    except ImportError:
                        pass

                    # 2. 检查插件本地目录下的依赖 lib/pkgName/
                    localDepPath = pluginPath / "lib" / pkgName
                    if localDepPath.exists():
                        logger.debug(f"[PluginManager] 找到本地依赖: {pkgName}")
                        if str(localDepPath) not in sys.path:
                            sys.path.insert(0, str(localDepPath))
                        continue

                    # 3. 检查 lib/pkgName/pkgName/ 目录（如 jieba 的情况）
                    localDepPath2 = pluginPath / "lib" / pkgName / pkgName
                    if localDepPath2.exists():
                        logger.debug(f"[PluginManager] 找到本地依赖: {pkgName}")
                        if str(localDepPath2) not in sys.path:
                            sys.path.insert(0, str(localDepPath2))
                        continue

                    # 4. 检查插件根目录下的依赖
                    if (pluginPath / pkgName).exists():
                        logger.debug(f"[PluginManager] 找到本地依赖: {pkgName}")
                        if str(pluginPath) not in sys.path:
                            sys.path.insert(0, str(pluginPath))
                        continue

                    # 依赖缺失
                    logger.error(f"[PluginManager] Python 依赖缺失: {pkgName}")
                    missingDeps.append(pkgName)

        return len(missingDeps) == 0, missingDeps

    def getPlugin(self, pluginId: str) -> Optional[PluginMetadata]:
        """获取插件元数据"""
        return self.plugins.get(pluginId)

    def getAllPlugins(self) -> List[PluginMetadata]:
        """获取所有插件"""
        return list(self.plugins.values())

    def getEnabledPlugins(self) -> List[PluginMetadata]:
        """获取已启用的插件"""
        return [p for p in self.plugins.values() if p.enabled]

    def getPluginsByCategory(self, category: str) -> List[PluginMetadata]:
        """获取指定分类的插件"""
        return [p for p in self.plugins.values() if p.category == category]

    def registerCallback(self, event: str, callback: Callable):
        """注册事件回调"""
        if event == "load":
            self.onPluginLoad.append(callback)
        elif event == "unload":
            self.onPluginUnload.append(callback)
        elif event == "enable":
            self.onPluginEnable.append(callback)
        elif event == "disable":
            self.onPluginDisable.append(callback)

    def shutdown(self):
        """关闭插件管理器，禁用所有插件"""
        logger.info("[PluginManager] 正在关闭...")

        for pluginId in list(self.plugins.keys()):
            if self.plugins[pluginId].enabled:
                self.disablePlugin(pluginId)

        self.plugins.clear()
        logger.info("[PluginManager] 关闭完成")


# 全局单例
_pluginManager: Optional[PluginManager] = None


def getPluginManager() -> PluginManager:
    """获取插件管理器单例"""
    global _pluginManager
    if _pluginManager is None:
        _pluginManager = PluginManager()
    return _pluginManager
