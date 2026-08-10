# coding: utf-8
"""为安装器未使用的可选视觉依赖提供轻量运行时占位。"""

from __future__ import annotations

import sys
from types import ModuleType


def installOptionalDependencyStubs() -> None:
    """避免仅供 AcrylicLabel 使用的完整 SciPy 被打入安装器。"""
    if "scipy" in sys.modules:
        return

    scipyModule = ModuleType("scipy")
    scipyModule.__path__ = []
    ndimageModule = ModuleType("scipy.ndimage")
    ndimageModule.__path__ = []
    filtersModule = ModuleType("scipy.ndimage.filters")

    def gaussianFilter(inputArray, *_arguments, **_keywordArguments):
        return inputArray

    setattr(filtersModule, "gaussian_filter", gaussianFilter)
    setattr(ndimageModule, "filters", filtersModule)
    setattr(scipyModule, "ndimage", ndimageModule)
    sys.modules["scipy"] = scipyModule
    sys.modules["scipy.ndimage"] = ndimageModule
    sys.modules["scipy.ndimage.filters"] = filtersModule


__all__ = ["installOptionalDependencyStubs"]

