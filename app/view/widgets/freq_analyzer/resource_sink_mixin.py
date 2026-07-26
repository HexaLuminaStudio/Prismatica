# coding: utf-8
"""
资源归档 Mixin（PRD-002 REQ-PROJ-001）

为分析子页面提供把分析结果摘要自动归档到当前激活项目的能力。

使用方法:
    class FreqAnalyzerWidget(ResourceSinkMixin, QWidget):
        _RESOURCE_TYPE = RESOURCE_TYPE_FREQ
        _RESOURCE_TITLE_PREFIX = "词频分析"

        def _collectResourcePayload(self) -> dict | None:
            # 返回 (title, summary, parameters, snapshotData)
            # 或 None(无数据/不应归档)
            ...

        def _onFreqFinished(...):
            ...  # 原逻辑
            self.notifyResourceCreated()  # ← 在结果处理末尾加这一行

约束:
    - 子类需提供 _RESOURCE_TYPE / _RESOURCE_TITLE_PREFIX
    - 子类需实现 _collectResourcePayload(),返回 dict 或 None
    - notifyResourceCreated() 必须从 Qt 主线程调用
"""

from __future__ import annotations

import traceback
from typing import Any, Dict, Optional

from app.core.services import projectManager
from loguru import logger


class ResourceSinkMixin:
    """分析完成后,把结果摘要归档到当前激活项目"""

    # 子类覆盖:Resource.type(参见 app.core.models.project 的 RESOURCE_TYPE_*)
    _RESOURCE_TYPE: str = ""

    # 子类覆盖:用户可读的资源标题前缀(用于自动生成 title)
    _RESOURCE_TITLE_PREFIX: str = "分析"

    # ------------------------------------------------------------------
    # 子类必须实现的钩子
    # ------------------------------------------------------------------
    def _collectResourcePayload(self) -> Optional[Dict[str, Any]]:
        """子类返回 (title, summary, parameters, snapshotData) 或 None

        Returns:
            dict, 包含:
                - title (str) — 资源标题
                - summary (str) — 200 字以内摘要
                - parameters (dict) — 可复现参数
                - snapshotData (dict) — 序列化的结果数据
            或 None — 表示本次结果不应归档
        """
        return None

    # ------------------------------------------------------------------
    # 公共 API:触发归档
    # ------------------------------------------------------------------
    def notifyResourceCreated(self) -> None:
        """在分析结果回调末尾调用,把结果摘要归档到当前激活项目

        设计原则:
            - 仅在有激活项目时归档(无激活 = 静默忽略)
            - 任何异常仅 logger.warning,不抛给上层(避免打断分析主流程)
            - 子类的 _collectResourcePayload 返回 None = 跳过
        """
        try:
            activeProject = projectManager.activeProject()
            if activeProject is None:
                # 无激活项目 → 静默忽略(用户可能不想归档)
                return
            payload = self._collectResourcePayload()
            if not payload:
                logger.debug(
                    f"[{type(self).__name__}] _collectResourcePayload 返回空,跳过归档"
                )
                return
            # 默认 title 兜底
            title = payload.get("title") or self._buildDefaultTitle()
            summary = payload.get("summary", "")
            parameters = payload.get("parameters") or {}
            snapshotData = payload.get("snapshotData") or {}
            tags = payload.get("tags") or []
            projectManager.addResource(
                projectId=activeProject.id,
                resourceType=self._RESOURCE_TYPE,
                title=title,
                summary=summary,
                parameters=parameters,
                snapshotData=snapshotData,
                tags=tags,
            )
        except Exception as e:
            logger.warning(
                f"[{type(self).__name__}] 归档资源失败(不影响主流程): {e}\n"
                f"{traceback.format_exc()}"
            )

    def _buildDefaultTitle(self) -> str:
        """生成默认 title:<前缀> <HH:MM:SS>"""
        from datetime import datetime

        ts = datetime.now().strftime("%H:%M:%S")
        prefix = self._RESOURCE_TITLE_PREFIX or "分析"
        return f"{prefix} {ts}"


__all__ = ["ResourceSinkMixin"]