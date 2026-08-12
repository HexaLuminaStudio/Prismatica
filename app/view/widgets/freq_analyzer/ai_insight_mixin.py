# coding: utf-8
"""
AI 解读 Mixin 基类（PRD-001 REQ-AI-001）

为所有分析子页面提供统一的 AI 解读接入能力,消除 7+ 个 widget 重复 80+ 行模板代码。

使用方法:
    class CollocationWidget(AiInsightMixin, QWidget):
        _AI_INSIGHT_PANEL_NAME = "搭配分析"
        _AI_INSIGHT_TYPE = AiInsightService.TYPE_COLLOCATION

        def _aiCollectExplainArgs(self) -> Optional[Tuple[str, Dict[str, Any]]]:
            # 返回 (analysis_type, data) 或 None(无数据)
            if self._result is None:
                return None
            return (
                AiInsightService.TYPE_COLLOCATION,
                {"result": self._result},
            )

    widget.setupAiInsightButton(self.runBtn)   # 接入按钮
    widget.disableAiInsightButton()            # 无结果时禁用

抽象协议:
    - _AI_INSIGHT_PANEL_NAME (str): 抽屉标题面板名
    - _AI_INSIGHT_TYPE (str, 可选): 默认 analysis type
    - _aiCollectExplainArgs() -> Optional[Tuple[str, Dict[str, Any]]]: 返回 (type, data)
    - _aiHasResult() -> bool: 是否有可解读结果(用于按钮启用/禁用)

内置能力:
    - setupAiInsightButton(button): 一行接入按钮
    - disableAiInsightButton(): 无结果时禁用
    - enableAiInsightButton(): 有结果时启用
    - _collectCorpusMeta(): 复用 3 个原 widget 的实现
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PySide6.QtWidgets import QWidget

from app.core.utils import cfg, logger, qconfig


class AiInsightMixin:
    """AI 解读 Mixin - 给分析子页面用

    必备属性(由子类提供):
        - self._aiInsightBtn: 接入的 QPushButton
        - self._corpusStore: CorpusStore(可选,用于 _collectCorpusMeta)
    """

    # 子类可覆盖:抽屉标题面板名
    _AI_INSIGHT_PANEL_NAME: str = "AI 解读"

    # 子类可覆盖:默认 analysis type
    _AI_INSIGHT_TYPE: str = ""

    # ------------------------------------------------------------------
    # 子类可重写的钩子
    # ------------------------------------------------------------------
    def _aiHasResult(self) -> bool:
        """是否有可解读的分析结果(子类重写)"""
        return True

    def _aiCollectExplainArgs(self) -> Optional[Tuple[str, Dict[str, Any]]]:
        """返回 (analysis_type, data_dict);无数据时返回 None

        data_dict 取决于分析类型,通常为 {"result": result_obj}
        """
        return None

    def _collectCorpusMeta(self) -> Dict[str, Any]:
        """汇总语料元信息(子类可重写以补充字段)"""
        meta: Dict[str, Any] = {
            "corpusName": "当前语料",
            "fileCount": 0,
            "totalChars": 0,
            "tokenCount": 0,
        }
        store = getattr(self, "_corpusStore", None)
        if store is not None:
            try:
                meta["fileCount"] = store.fileCount()
                meta["totalChars"] = store.totalChars()
            except Exception:
                pass
            try:
                dbPath = store.dbPath
                meta["corpusName"] = Path(dbPath).stem or "当前语料"
            except Exception:
                pass
        # 词种数 / token 数(子类可重写 _aiHasResult 来提供具体数据)
        return meta

    # ------------------------------------------------------------------
    # 公共 API:接入按钮
    # ------------------------------------------------------------------
    def setupAiInsightButton(self, button) -> None:
        """一行接入 AI 解读按钮

        - 首次点击初始化抽屉 + 服务(懒初始化)
        - 按当前 _aiHasResult 自动启用/禁用
        """
        self._aiInsightBtn = button
        button.setEnabled(self._aiHasResult())
        # 避免重复连接:先判断信号是否已包含本 slot(PySide6 在「未连接」时
        # disconnect 会从 C++ 侧打印 `qWarning: Failed to disconnect`,且无 Python
        # warnings 钩子可拦截)。用 receivers() 检测 slot 数量,>0 时才安全断开。
        # 注:PySide6 中 QObject.receivers(signal) 需带信号签名版本号,如 "2clicked()"
        if button.receivers("2clicked()") > 0:
            try:
                button.clicked.disconnect(self._openAiInsight)
            except (TypeError, RuntimeError):
                # 极端情况:有其他 slot 但不是 _openAiInsight → 放过即可
                pass
        button.clicked.connect(self._openAiInsight)

    def enableAiInsightButton(self) -> None:
        btn = getattr(self, "_aiInsightBtn", None)
        if btn is not None:
            btn.setEnabled(True)

    def disableAiInsightButton(self) -> None:
        btn = getattr(self, "_aiInsightBtn", None)
        if btn is not None:
            btn.setEnabled(False)

    def refreshAiInsightButton(self) -> None:
        """根据当前是否有结果,刷新按钮启用状态"""
        if self._aiHasResult():
            self.enableAiInsightButton()
        else:
            self.disableAiInsightButton()

    # ------------------------------------------------------------------
    # 抽屉 + 服务生命周期
    # ------------------------------------------------------------------
    def _initAiInsight(self) -> None:
        """初始化 AI 解读抽屉 + 服务(懒初始化)"""
        if getattr(self, "_aiInsightService", None) is not None:
            return
        # 延迟导入避免启动期开销
        from qfluentwidgetspro import Drawer, DrawerPosition

        from app.core.services import AiInsightService
        from app.view.widgets.freq_analyzer.ai_insight_drawer_view import (
            AiInsightDrawerView,
        )

        self._aiInsightService = AiInsightService(self)
        self._aiInsightView = AiInsightDrawerView(self)
        self._aiInsightView.setPanelTitle(self._AI_INSIGHT_PANEL_NAME)
        self._aiInsightView.setModelName("Prismatica 平台模型")
        self._aiInsightView.regenerateRequested.connect(self._openAiInsight)
        self._aiInsightView.closeRequested.connect(self._collapseAiInsight)
        self._aiInsightView.styleChanged.connect(self._onAiInsightStyleChanged)

        self._aiInsightService.textReceived.connect(self._aiInsightView.setStreamText)
        self._aiInsightService.progressChanged.connect(self._aiInsightView.setProgress)
        self._aiInsightService.streamFinished.connect(self._onAiInsightFinished)
        self._aiInsightService.failed.connect(self._aiInsightView.setError)

        self._aiInsightDrawer = Drawer(self._aiInsightView, self, DrawerPosition.RIGHT)
        # 关闭「点击外部自动收起」:避免动画期间被状态机吞掉
        self._aiInsightDrawer.setHiddenOnClickOutside(False)

    def _openAiInsight(self) -> None:
        """打开 AI 解读抽屉并发起解读"""
        # 数据守卫
        args = self._aiCollectExplainArgs()
        if args is None:
            logger.warning(
                f"[{self.__class__.__name__}] 暂无可解读数据,忽略 AI 解读请求"
            )
            return

        analysisType, data = args
        self._initAiInsight()
        assert self._aiInsightService is not None

        # 兜底:理论上 _collapseAiInsight 已 stop,极端情况下再 stop 一次
        if self._aiInsightService.isRunning:
            logger.warning(
                f"[{self.__class__.__name__}] _openAiInsight 时 service 仍在跑,强制 stop"
            )
            self._aiInsightService.stop()

        corpusMeta = self._collectCorpusMeta()
        self._aiInsightView.clearText()
        self._aiInsightView.setRunning(True)
        ok = self._aiInsightService.explain(analysisType, data, corpusMeta=corpusMeta)
        if not ok:
            # 被 _guardData 拒绝(空数据等)→ service 已 emit failed
            # 让 view 显示错误信息
            self._aiInsightView.setRunning(False)
            return
        # 始终调用 expand():Drawer 内部状态机自行处理重复调用
        self._aiInsightDrawer.expand()

    def _collapseAiInsight(self) -> None:
        """关闭抽屉:中断正在跑的 LLM 并复位 UI"""
        service = getattr(self, "_aiInsightService", None)
        if service is not None and service.isRunning:
            logger.info(
                f"[{self.__class__.__name__}] 用户收起抽屉,中断进行中的 AI 解读"
            )
            service.stop()
            view = getattr(self, "_aiInsightView", None)
            if view is not None:
                view.setRunning(False)
                view.setStatus("已中断（用户关闭抽屉）")
        drawer = getattr(self, "_aiInsightDrawer", None)
        if drawer is not None:
            drawer.collapse()

    def _onAiInsightFinished(self) -> None:
        """流式结束后切换 Drawer 状态"""
        service = getattr(self, "_aiInsightService", None)
        view = getattr(self, "_aiInsightView", None)
        if service is None or view is None:
            return
        view.setFinalText(service.responseText)

    def _onAiInsightStyleChanged(self, style: str) -> None:
        """风格切换:仅记录,下一次解读生效"""
        qconfig.set(cfg.AiInsightStyle, style)
        logger.info(f"[{self.__class__.__name__}] AI 解读风格切换为: {style}")


# 类型提示导出
__all__ = ["AiInsightMixin"]
