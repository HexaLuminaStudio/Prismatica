# coding: utf-8
"""项目研究报告生成服务（PRD-002 F4 + AI 联动 MVP）

把当前激活项目的以下信息拼装成一段上下文,丢给 ChatService 让 AI 生成
「项目研究报告」:

    - 项目元数据(名称 / 描述 / 标签 / 模板)
    - 项目级 + 资源级笔记(Note.content,只读)
    - 资源列表(摘要 / 参数 / 创建时间);可选过滤「仅未弃用的资源」
    - 已归档的 AI 解读(aiInsights)— 提供上下文连贯性,避免重复/矛盾

生成流程:
    1. UI 点击「📝 生成研究报告」 → 调 ResearchReportService.generate(projectId, ...)
    2. 服务内部拼装 prompt → 通过 ChatService.ask() 发起对话
    3. ChatService 流式回调 textReceived / failed / streamFinished
    4. 流式输出结束后服务调 projectManager.addAiInsight(...) 归档
    5. 通过 reportGenerated / reportFailed 信号通知 UI 刷新

设计原则:
    - 不阻塞 UI(全部异步,信号回调驱动)
    - 一次只生成一份报告(MVP 不支持多轮追问)
    - 失败时回传错误消息给 UI(由 InfoBar 提示)
    - 重入保护:running 状态为 True 时再次调用会被忽略
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from app.core.models.project import (
    AiInsight,
    Project,
    RESOURCE_STATUS_REJECTED,
    Resource,
)
from app.core.services import projectManager
from app.core.services.chat_service import ChatService
from app.core.utils import logger


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "你是一位资深的中文语言学/汉语教学研究者,擅长根据项目已有的"
    "研究素材(资源摘要 + 笔记 + 历史解读)撰写结构化的「项目研究报告」。\n"
    "要求:\n"
    "1. 用中文撰写,Markdown 格式;\n"
    "2. 报告分章节,建议结构:研究背景 / 数据与方法 / 主要发现 / 局限与展望;\n"
    "3. 引用具体资源时使用「📊 <资源类型>:<标题>」格式;\n"
    "4. 客观陈述事实,不要凭空编造资源中没有的数据;\n"
    "5. 控制在 800-1500 字之间,语言凝练。"
)


def _format_resources(
    resources: List[Resource],
    resourceTypeLabels: Optional[Dict[str, str]] = None,
) -> str:
    """把资源列表格式化为可读文本。"""
    if not resources:
        return "(无)"
    lines = []
    for i, r in enumerate(resources, 1):
        typeLabel = (resourceTypeLabels or {}).get(r.type, r.type or "?")
        title = r.title or "(无标题)"
        summary = (r.summary or "").strip()[:200]
        params = r.parameters or {}
        paramStr = ", ".join(f"{k}={v}" for k, v in list(params.items())[:6])
        lines.append(
            f"{i}. [{typeLabel}] {title}\n"
            f"   状态:{r.status or 'new'}\n"
            f"   创建:{r.createdAt or '—'}\n"
            f"   摘要:{summary or '(无)'}\n"
            f"   参数:{paramStr or '(无)'}"
        )
    return "\n".join(lines)


def _format_insights(insights: List[AiInsight]) -> str:
    """把已归档的 AI 解读格式化为可读文本。"""
    if not insights:
        return "(无)"
    lines = []
    for i, a in enumerate(insights, 1):
        content = (a.content or "").strip()[:300]
        lines.append(
            f"{i}. [{a.analysisType or '?'}] {a.createdAt or '—'}\n"
            f"   摘要:{content}..."
        )
    return "\n".join(lines)


def _buildReportPrompt(
    project: Project,
    resources: List[Resource],
    insights: List[AiInsight],
    resourceTypeLabels: Dict[str, str],
) -> str:
    """组装发往 LLM 的用户消息正文。"""
    parts = []
    parts.append("# 项目元数据")
    parts.append(
        f"- 项目名:{project.name or '(未命名)'}\n"
        f"- 描述:{project.description or '(无)'}\n"
        f"- 标签:{', '.join(project.tags) if project.tags else '(无)'}\n"
        f"- 模板:{project.template or '(无)'}\n"
        f"- 创建:{project.createdAt or '—'}\n"
        f"- 最近更新:{project.updatedAt or '—'}\n"
        f"- 资源数:{len(project.resources)} (本次纳入:{len(resources)})"
    )
    parts.append("\n# 资源列表(已按未弃用过滤)")
    parts.append(_format_resources(resources, resourceTypeLabels))
    parts.append("\n# 历史 AI 解读(用于连贯性)")
    parts.append(_format_insights(insights))
    parts.append(
        "\n# 任务\n"
        "请基于以上素材,撰写一份 800-1500 字的项目研究报告,Markdown 格式,"
        "分章节,客观陈述。"
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 服务类
# ---------------------------------------------------------------------------


class ResearchReportService(QObject):
    """项目研究报告生成服务(单例 + 异步)。

    Signals:
        reportStarted(): 开始生成(用于 UI 切到 busy 态)
        reportStreamReceived(str): 流式收到一段文本
        reportFinished(str, str): 完成(参数: insight_id, 完整正文)
        reportFailed(str): 失败(参数: 错误描述)
    """

    reportStarted = Signal()
    reportStreamReceived = Signal(str)
    reportFinished = Signal(str, str)
    reportFailed = Signal(str)

    _instance: Optional["ResearchReportService"] = None

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._running: bool = False
        self._currentProjectId: str = ""
        self._currentResourceScope: Optional[str] = None  # None = 全部
        self._currentBuffer: str = ""
        self._currentModel: str = ""
        # 持有一份本地 ChatService — 与 chat_interface 等独立,不共享 thread。
        self._chat = ChatService(self)
        self._chat.textReceived.connect(self._onChatTextReceived)
        self._chat.streamFinished.connect(self._onChatStreamFinished)
        self._chat.failed.connect(self._onChatFailed)

    # ------------------------------------------------------------------
    # 单例
    # ------------------------------------------------------------------
    @classmethod
    def instance(cls) -> "ResearchReportService":
        if cls._instance is None:
            cls._instance = ResearchReportService()
        return cls._instance

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def isRunning(self) -> bool:
        return self._running

    def generate(
        self,
        projectId: str,
        resourceScope: Optional[str] = None,
        includeRejected: bool = False,
    ) -> bool:
        """发起一次研究报告生成。

        Args:
            projectId: 目标项目 id
            resourceScope: 若指定,只纳入该资源(以及项目级笔记);None = 所有资源
            includeRejected: True → 包含 status=rejected 的资源,默认 False

        Returns:
            True = 已发起;False = 项目无效 / ChatService 已在运行 / 已有报告任务在跑
        """
        if self._running:
            logger.warning("[ResearchReport] 已有任务在跑,新请求被忽略")
            return False
        if self._chat.isRunning:
            logger.warning("[ResearchReport] ChatService 正在跑,新请求被忽略")
            return False
        project = projectManager.getProject(projectId)
        if project is None:
            logger.warning(f"[ResearchReport] 项目不存在: {projectId}")
            return False

        # 收集资源(过滤)
        resources = self._selectResources(project, resourceScope, includeRejected)
        insights = projectManager.listAiInsights(projectId)
        # 资源类型 → 中文标签(复用 UI 端的同款映射,简单内联避免循环依赖)
        resourceTypeLabels = self._loadResourceTypeLabels()

        user_message = _buildReportPrompt(
            project, resources, insights, resourceTypeLabels
        )

        # 记录上下文
        self._running = True
        self._currentProjectId = projectId
        self._currentResourceScope = resourceScope
        self._currentBuffer = ""
        self._currentModel = ""  # 由 ChatService 设置

        logger.info(
            f"[ResearchReport] 发起生成: project={projectId}, "
            f"resources={len(resources)}, "
            f"history_insights={len(insights)}"
        )
        self.reportStarted.emit()
        self._chat.ask(
            message=user_message,
            prompt=_SYSTEM_PROMPT,
        )
        return True

    def cancel(self) -> None:
        """请求取消正在进行的生成(简单粗暴 — 停 ChatService)。"""
        if not self._running:
            return
        try:
            self._chat.stop()
        except Exception as e:
            logger.warning(f"[ResearchReport] cancel 失败: {e}")
        self._running = False
        self._currentBuffer = ""

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _selectResources(
        self,
        project: Project,
        resourceScope: Optional[str],
        includeRejected: bool,
    ) -> List[Resource]:
        items = list(project.resources)
        if resourceScope is not None:
            items = [r for r in items if r.id == resourceScope]
        if not includeRejected:
            items = [r for r in items if r.status != RESOURCE_STATUS_REJECTED]
        items.sort(key=lambda r: r.createdAt or "", reverse=True)
        return items

    def _loadResourceTypeLabels(self) -> Dict[str, str]:
        # 内联一份轻量映射(完整 UI 映射在 widgets/project_dashboard_widgets.py,
        # 这里只取研究报告需要的常见类型)
        return {
            "freq": "词频",
            "collocation": "搭配",
            "network": "共现网络",
            "kwic": "KWIC",
            "construction": "构式",
            "dependency": "句法依存",
            "keyword_list": "关键词",
            "ngram_cluster": "N-gram",
            "sentiment": "情感",
            "word_cloud": "词云",
            "word_analysis": "词语分析",
        }

    # ------------------------------------------------------------------
    # ChatService 信号回调
    # ------------------------------------------------------------------
    def _onChatTextReceived(self, delta: str, _totalTokens: int) -> None:
        if not self._running:
            return
        self._currentBuffer += delta
        try:
            self.reportStreamReceived.emit(delta)
        except Exception as e:
            logger.warning(f"[ResearchReport] reportStreamReceived 回调异常: {e}")

    def _onChatStreamFinished(self) -> None:
        if not self._running:
            return
        content = self._currentBuffer.strip()
        projectId = self._currentProjectId
        resourceScope = self._currentResourceScope
        model = ""
        try:
            model = self._chat._thread._model  # noqa: SLF001 — 取模型名做归档
        except Exception:
            model = ""

        # 重置状态(先重置,避免回调里再次触发 cancel 时双重清空)
        self._running = False
        self._currentBuffer = ""
        self._currentProjectId = ""

        if not content:
            self.reportFailed.emit("AI 未返回任何内容")
            return

        try:
            insight = projectManager.addAiInsight(
                projectId=projectId,
                content=content,
                analysisType="research_report",
                model=model,
                confidence="medium",
                resourceId=resourceScope,
            )
            if insight is None:
                self.reportFailed.emit("归档 AI 解读失败(项目可能已被删除)")
                return
            logger.info(
                f"[ResearchReport] 完成并归档: id={insight.id}, len={len(content)}"
            )
            self.reportFinished.emit(insight.id, content)
        except Exception as e:
            logger.exception(f"[ResearchReport] 归档异常: {e}")
            self.reportFailed.emit(f"归档失败:{type(e).__name__}: {e}")

    def _onChatFailed(self, errorMsg: str) -> None:
        if not self._running:
            return
        self._running = False
        self._currentBuffer = ""
        self._currentProjectId = ""
        try:
            self.reportFailed.emit(errorMsg or "AI 调用失败")
        except Exception as e:
            logger.warning(f"[ResearchReport] reportFailed 回调异常: {e}")


# 模块级单例
researchReportService = ResearchReportService.instance()


__all__ = ["ResearchReportService", "researchReportService"]
