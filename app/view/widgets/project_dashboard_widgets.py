# coding: utf-8
"""项目仪表盘子组件（PRD-002 REQ-PROJ-001 / F3 项目仪表盘）

三个独立的可复用面板,组装到 ProjectDashboardWidget 里:

    - ResourcePoolPanel(左):按类型分组展示资源,带状态徽章 + 选中高亮
    - ResourceDetailPanel(中):展示当前选中资源的摘要 / 参数 / 跳转原模块
    - AiInsightsPanel(右):AI 解读面板

设计原则:
    - 仅依赖 projectManager(Project 数据 + listResources / getProject 等接口)
    - 面板之间通过父 widget 协调(单一选中态)
    - 失败容错:任何异常仅 logger.warning,不抛给上层
    - 复用 qfluentwidgets 控件,保持与现有 UI 一致
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    SubtitleLabel,
    ToolButton,
)

from app.core.models.project import (
    RESOURCE_STATUS_CANDIDATE,
    RESOURCE_STATUS_NEW,
    RESOURCE_STATUS_REJECTED,
    RESOURCE_STATUS_SELECTED,
    RESOURCE_TYPE_FREQ,
    RESOURCE_TYPE_NETWORK,
    RESOURCE_TYPE_KWIC,
    RESOURCE_TYPE_COLLOCATION,
    RESOURCE_TYPE_CONSTRUCTION,
    RESOURCE_TYPE_DEPENDENCY,
    RESOURCE_TYPE_KEYWORD_LIST,
    RESOURCE_TYPE_NGRAM_CLUSTER,
    RESOURCE_TYPE_SENTIMENT,
    RESOURCE_TYPE_WORD_CLOUD,
    RESOURCE_TYPE_WORD_ANALYSIS,
    Resource,
)
from app.core.services import projectManager
from app.core.utils import logger


# ---------------------------------------------------------------------------
# 资源类型 → 显示名 + 图标的映射(用于资源池分组展示)
# ---------------------------------------------------------------------------

_RESOURCE_TYPE_DISPLAY: Dict[str, Dict[str, Any]] = {
    RESOURCE_TYPE_FREQ: {"name": "词频", "icon": "📊", "order": 1},
    RESOURCE_TYPE_COLLOCATION: {"name": "搭配", "icon": "🔗", "order": 2},
    RESOURCE_TYPE_NETWORK: {"name": "共现网络", "icon": "🌐", "order": 3},
    RESOURCE_TYPE_KEYWORD_LIST: {"name": "关键词", "icon": "⭐", "order": 4},
    RESOURCE_TYPE_NGRAM_CLUSTER: {"name": "N-gram", "icon": "📝", "order": 5},
    RESOURCE_TYPE_KWIC: {"name": "KWIC 检索", "icon": "🔍", "order": 6},
    RESOURCE_TYPE_CONSTRUCTION: {"name": "构式识别", "icon": "🧩", "order": 7},
    RESOURCE_TYPE_DEPENDENCY: {"name": "句法依存", "icon": "🌳", "order": 8},
    RESOURCE_TYPE_SENTIMENT: {"name": "情感分析", "icon": "💬", "order": 9},
    RESOURCE_TYPE_WORD_CLOUD: {"name": "词云", "icon": "☁️", "order": 10},
    RESOURCE_TYPE_WORD_ANALYSIS: {"name": "词语分析", "icon": "🔤", "order": 11},
}


def _resourceTypeLabel(resourceType: str) -> str:
    """资源类型 → 显示文本(未知类型 fallback 为原文)。"""
    info = _RESOURCE_TYPE_DISPLAY.get(resourceType)
    if info is None:
        return resourceType or "未分类"
    return f"{info['icon']} {info['name']}"


def _resourceStatusLabel(status: str) -> str:
    """资源状态 → 显示文本 + 徽章字符。"""
    mapping = {
        RESOURCE_STATUS_NEW: "🆕 新建",
        RESOURCE_STATUS_CANDIDATE: "⭐ 候选",
        RESOURCE_STATUS_SELECTED: "✓ 选中",
        RESOURCE_STATUS_REJECTED: "✗ 弃用",
        "pending": "? 待定",
    }
    return mapping.get(status, status or "—")


# ---------------------------------------------------------------------------
# ResourcePoolPanel — 左栏:资源池(按类型分组,QListWidget)
# ---------------------------------------------------------------------------


class ResourcePoolPanel(QWidget):
    """资源池面板:按类型分组展示当前项目的所有资源。

    Signals:
        resourceSelected(str): 用户选中某资源时发射,参数为 resource.id
        resourceDoubleClicked(str): 双击某资源时发射,用于「跳转到原分析模块」
    """

    resourceSelected = Signal(str)
    resourceDoubleClicked = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("resourcePoolPanel")
        # 当前项目 id(由父 widget 通过 setProject 设置)
        self._projectId: str = ""
        # 当前项目中的所有资源(由 refresh 拉取)
        self._resources: List[Resource] = []
        # 当前选中资源 id
        self._currentResourceId: str = ""
        self._buildUi()

    def _buildUi(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 8, 12)
        layout.setSpacing(8)

        # 标题 + 统计
        headerRow = QHBoxLayout()
        headerRow.setSpacing(6)
        self.titleLabel = StrongBodyLabel("资源池", self)
        headerRow.addWidget(self.titleLabel)
        headerRow.addStretch(1)
        self.countLabel = CaptionLabel("0 个资源", self)
        self.countLabel.setStyleSheet("color: gray;")
        headerRow.addWidget(self.countLabel)
        layout.addLayout(headerRow)

        # 状态徽章图例
        legend = CaptionLabel("🆕 新建   ⭐ 候选   ✓ 选中   ✗ 弃用", self)
        legend.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(legend)

        # 列表区:listWidget + emptyLabel 放在 QStackedWidget 里互斥切换,
        # 避免两个 widget 同时争 stretch factor 导致空态时被垂直居中。
        self.contentStack = QStackedWidget(self)
        self.contentStack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.listWidget = QListWidget(self.contentStack)
        self.listWidget.setSelectionMode(QListWidget.SingleSelection)
        self.listWidget.setUniformItemSizes(False)
        self.listWidget.setSpacing(2)
        self.listWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.listWidget.currentItemChanged.connect(self._onCurrentChanged)
        self.listWidget.itemDoubleClicked.connect(self._onItemDoubleClicked)

        # 空态占位:放进一个容器,顶部对齐 + 水平居中,避免被布局强行垂直居中
        emptyContainer = QWidget(self.contentStack)
        emptyContainer.setObjectName("resourcePoolEmptyContainer")
        emptyLayout = QVBoxLayout(emptyContainer)
        emptyLayout.setContentsMargins(0, 24, 0, 0)
        emptyLayout.setSpacing(4)
        self.emptyLabel = BodyLabel("尚无资源", emptyContainer)
        self.emptyLabel.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.emptyLabel.setStyleSheet("color: gray;")
        self.emptyHintLabel = CaptionLabel("", emptyContainer)
        self.emptyHintLabel.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.emptyHintLabel.setStyleSheet("color: gray; font-size: 11px;")
        self.emptyHintLabel.setWordWrap(True)
        emptyLayout.addWidget(self.emptyLabel)
        emptyLayout.addWidget(self.emptyHintLabel)
        emptyLayout.addStretch(1)

        self.contentStack.addWidget(self.listWidget)  # idx 0
        self.contentStack.addWidget(emptyContainer)  # idx 1
        layout.addWidget(self.contentStack, 1)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def setProject(self, projectId: str) -> None:
        """切换当前项目。projectId="" 表示无激活项目,显示引导态。"""
        self._projectId = projectId
        self.refresh()

    def refresh(self) -> None:
        """重新从 projectManager 拉取当前项目的资源列表,刷新 UI。"""
        try:
            self.listWidget.blockSignals(True)
            try:
                self.listWidget.clear()
            finally:
                self.listWidget.blockSignals(False)
            self._resources = []
            self._currentResourceId = ""
            if not self._projectId:
                self._renderEmpty("请先选择或创建一个项目")
                return
            self._resources = projectManager.listResources(self._projectId)
            if not self._resources:
                self._renderEmpty("该项目尚无资源\n去跑一次分析,结果会自动归档到这里")
                return
            self._renderResources()
        except Exception as e:
            logger.exception(f"[ResourcePoolPanel] refresh 失败: {e}")
            self._renderEmpty(f"加载失败:{e}")

    def currentResourceId(self) -> str:
        """返回当前选中资源 id(无选中返回 "")."""
        return self._currentResourceId

    def currentResource(self) -> Optional[Resource]:
        """返回当前选中 Resource 对象(无选中返回 None)。"""
        if not self._currentResourceId:
            return None
        for r in self._resources:
            if r.id == self._currentResourceId:
                return r
        return None

    def selectResource(self, resourceId: str) -> None:
        """按 id 选中资源(由父 widget 在外部触发,例如详情页要求定位某资源)。"""
        if not resourceId:
            return
        for i in range(self.listWidget.count()):
            item = self.listWidget.item(i)
            if item is None:
                continue
            if item.data(Qt.UserRole) == resourceId:
                self.listWidget.setCurrentItem(item)
                return

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _renderEmpty(self, message: str) -> None:
        """渲染空态(无资源 / 无项目)。"""
        # message 现在被解读为「主标题 + 提示」两行,中间用 \n 分割
        # 兼容旧用法:无换行时全部塞主标题,提示为空
        parts = message.split("\n", 1)
        self.emptyLabel.setText(parts[0] if parts else "尚无资源")
        self.emptyHintLabel.setText(parts[1] if len(parts) > 1 else "")
        self.contentStack.setCurrentIndex(1)
        self.countLabel.setText("0 个资源")
        self.titleLabel.setText("资源池")

    def _renderResources(self) -> None:
        """按类型分组渲染资源。"""
        self.contentStack.setCurrentIndex(0)

        # 按类型分组
        groups: Dict[str, List[Resource]] = {}
        for r in self._resources:
            groups.setdefault(r.type or "其他", []).append(r)

        # 按预定义 order 排序,未知类型放到末尾
        def _groupOrder(t: str) -> int:
            info = _RESOURCE_TYPE_DISPLAY.get(t)
            return info["order"] if info else 999

        orderedTypes = sorted(groups.keys(), key=_groupOrder)

        totalCount = len(self._resources)
        self.countLabel.setText(f"{totalCount} 个资源")

        for typeKey in orderedTypes:
            items = groups[typeKey]
            # 分组标题(不可选)
            header = QListWidgetItem(_resourceTypeLabel(typeKey))
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            header.setFlags(Qt.NoItemFlags)  # 不可选中
            header.setData(Qt.UserRole, "")  # 空 id 标识分组
            header.setBackground(Qt.lightGray)
            self.listWidget.addItem(header)

            for r in items:
                title = r.title or "(无标题)"
                statusBadge = _resourceStatusLabel(r.status)
                displayText = f"{title}\n  {statusBadge}  ·  {r.createdAt[5:16] if r.createdAt else '—'}"
                item = QListWidgetItem(displayText)
                item.setData(Qt.UserRole, r.id)
                item.setToolTip(f"{title}\n摘要:{r.summary[:200]}")
                self.listWidget.addItem(item)

    # ------------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------------
    def _onCurrentChanged(
        self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]
    ) -> None:
        """列表当前项变化 → 通知父 widget。"""
        try:
            if current is None:
                self._currentResourceId = ""
                self.resourceSelected.emit("")
                return
            resourceId = current.data(Qt.UserRole) or ""
            if not resourceId:
                # 分组标题被「选中」(理论上因 NoItemFlags 不会触发,但兜底)
                self._currentResourceId = ""
                self.resourceSelected.emit("")
                return
            self._currentResourceId = resourceId
            self.resourceSelected.emit(resourceId)
        except Exception as e:
            logger.warning(f"[ResourcePoolPanel] _onCurrentChanged 失败: {e}")

    def _onItemDoubleClicked(self, item: QListWidgetItem) -> None:
        """双击资源 → 通知父 widget「跳转到原分析模块」。"""
        try:
            resourceId = item.data(Qt.UserRole) or ""
            if not resourceId:
                return
            self.resourceDoubleClicked.emit(resourceId)
        except Exception as e:
            logger.warning(f"[ResourcePoolPanel] _onItemDoubleClicked 失败: {e}")


# ---------------------------------------------------------------------------
# ResourceDetailPanel — 中栏:资源详情(摘要 / 参数 / 跳转)
# ---------------------------------------------------------------------------


class ResourceDetailPanel(QWidget):
    """资源详情面板。

    Signals:
        jumpRequested(str): 用户点击「🚀 跳转分析模块」,参数为 resource.type
    """

    jumpRequested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("resourceDetailPanel")
        self._currentResource: Optional[Resource] = None
        self._buildUi()

    def _buildUi(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        # 顶部:类型徽章 + 标题
        headerRow = QHBoxLayout()
        headerRow.setSpacing(8)
        self.typeBadge = CaptionLabel("", self)
        self.typeBadge.setStyleSheet("color: #00b09c; font-weight: bold;")
        headerRow.addWidget(self.typeBadge)
        headerRow.addStretch(1)
        self.statusBadge = CaptionLabel("", self)
        headerRow.addWidget(self.statusBadge)
        outer.addLayout(headerRow)

        self.titleLabel = SubtitleLabel("未选中资源", self)
        self.titleLabel.setWordWrap(True)
        outer.addWidget(self.titleLabel)

        self.metaLabel = CaptionLabel("", self)
        self.metaLabel.setStyleSheet("color: gray;")
        self.metaLabel.setWordWrap(True)
        outer.addWidget(self.metaLabel)

        # 分隔
        line = QLabel(self)
        line.setFrameShape(QLabel.HLine)
        line.setStyleSheet("color: #dcdcdc;")
        outer.addWidget(line)

        # 摘要
        self.summaryTitle = StrongBodyLabel("摘要", self)
        outer.addWidget(self.summaryTitle)
        self.summaryLabel = BodyLabel("", self)
        self.summaryLabel.setWordWrap(True)
        self.summaryLabel.setStyleSheet("color: #444; line-height: 1.5;")
        outer.addWidget(self.summaryLabel)

        # 参数
        self.paramsTitle = StrongBodyLabel("可复现参数", self)
        outer.addWidget(self.paramsTitle)
        self.paramsLabel = BodyLabel("", self)
        self.paramsLabel.setWordWrap(True)
        self.paramsLabel.setStyleSheet(
            "color: #555; font-family: Consolas, monospace; font-size: 12px;"
        )
        self.paramsLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.paramsLabel)

        # 标签
        self.tagsLabel = CaptionLabel("", self)
        self.tagsLabel.setStyleSheet("color: gray;")
        outer.addWidget(self.tagsLabel)

        outer.addStretch(1)

        # 跳转按钮
        buttonRow = QHBoxLayout()
        buttonRow.addStretch(1)
        self.jumpButton = PushButton("🚀 跳转到分析模块", self)
        self.jumpButton.setEnabled(False)
        self.jumpButton.clicked.connect(self._onJumpClicked)
        buttonRow.addWidget(self.jumpButton)
        outer.addLayout(buttonRow)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def setResource(self, resource: Optional[Resource]) -> None:
        """设置当前展示的资源;传 None 显示空态。"""
        self._currentResource = resource
        self._render()

    def _render(self) -> None:
        r = self._currentResource
        if r is None:
            self.typeBadge.setText("")
            self.statusBadge.setText("")
            self.titleLabel.setText("未选中资源")
            self.metaLabel.setText("从左侧资源池选择一项以查看详情")
            self.summaryLabel.setText("")
            self.paramsLabel.setText("")
            self.tagsLabel.setText("")
            self.jumpButton.setEnabled(False)
            return

        self.typeBadge.setText(_resourceTypeLabel(r.type))
        self.statusBadge.setText(_resourceStatusLabel(r.status))
        self.titleLabel.setText(r.title or "(无标题)")
        metaParts = []
        if r.createdAt:
            metaParts.append(f"创建于 {r.createdAt}")
        if r.snapshotRelPath:
            metaParts.append(f"快照:{r.snapshotRelPath}")
        self.metaLabel.setText("  ·  ".join(metaParts))

        self.summaryLabel.setText(r.summary or "(暂无摘要)")

        # 参数:key=value 多行展示
        params = r.parameters or {}
        if params:
            lines = []
            for k, v in params.items():
                # 截断过长的 value
                vStr = str(v)
                if len(vStr) > 80:
                    vStr = vStr[:77] + "..."
                lines.append(f"  {k} = {vStr}")
            self.paramsLabel.setText("\n".join(lines))
        else:
            self.paramsLabel.setText("(无参数)")

        tagsText = ""
        if r.tags:
            tagsText = "标签:" + " / ".join(r.tags)
        self.tagsLabel.setText(tagsText)

        # 跳转按钮只在已知类型时启用
        self.jumpButton.setEnabled(bool(r.type))

    def _onJumpClicked(self) -> None:
        if self._currentResource is None:
            return
        try:
            self.jumpRequested.emit(self._currentResource.type or "")
        except Exception as e:
            logger.warning(f"[ResourceDetailPanel] _onJumpClicked 失败: {e}")


# ---------------------------------------------------------------------------
# AiInsightsPanel — 右栏:AI 解读面板
# ---------------------------------------------------------------------------


class AiInsightsPanel(QWidget):
    """AI 解读面板(原 NotesInsightsPanel,笔记功能已下线)。

    - 「✨ 生成研究报告」按钮:拼装项目信息 → ChatService → 流式输出 → 归档到
      Project.aiInsights(analysisType="research_report")
    - 「解读列表」:按时间倒序展示已归档的 AI 解读
    - 「选中解读」:右侧查看面板显示内容 + 「📋 复制」/「🗑️ 删除」按钮
    - 报告范围:可选仅针对「当前选中资源」(由 setResourceScope 切换)

    注:原笔记列表与提示已移除(笔记功能下放到 Word)。
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiInsightsPanel")
        self._projectId: str = ""
        # 资源 scope:None = 全部,或某个资源 id
        self._resourceScope: Optional[str] = None
        # 解读缓存(避免反复从 project 拷)
        self._insights: List[Any] = []
        # 当前选中的解读 id
        self._currentInsightId: str = ""
        self._buildUi()
        self._connectAiService()

    def _buildUi(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        aiHeader = QHBoxLayout()
        self.aiTitle = StrongBodyLabel("🤖 AI 解读", self)
        aiHeader.addWidget(self.aiTitle)
        aiHeader.addStretch(1)
        self.generateReportBtn = PushButton("✨ 生成研究报告", self)
        self.generateReportBtn.setToolTip(
            "基于本项目的资源/笔记/历史解读,生成一份研究报告并归档"
        )
        self.generateReportBtn.clicked.connect(self._onGenerateReportClicked)
        aiHeader.addWidget(self.generateReportBtn)
        layout.addLayout(aiHeader)

        self.aiHint = CaptionLabel("", self)
        self.aiHint.setStyleSheet("color: gray; font-size: 11px;")
        self.aiHint.setWordWrap(True)
        layout.addWidget(self.aiHint)
        self._updateAiHint()

        self.aiStatusLabel = CaptionLabel("", self)
        self.aiStatusLabel.setStyleSheet("color: gray;")
        layout.addWidget(self.aiStatusLabel)

        # 解读列表 + 内容预览(左右分栏)
        aiArea = QHBoxLayout()
        aiArea.setSpacing(6)

        self.insightList = QListWidget(self)
        self.insightList.setFixedWidth(250)
        self.insightList.currentItemChanged.connect(self._onInsightSelectionChanged)
        aiArea.addWidget(self.insightList)

        # 解读内容预览容器
        insightContainer = QWidget(self)
        insightLayout = QVBoxLayout(insightContainer)
        insightLayout.setContentsMargins(0, 0, 0, 0)
        insightLayout.setSpacing(4)
        insightToolbar = QHBoxLayout()
        self.insightMetaLabel = CaptionLabel("", insightContainer)
        self.insightMetaLabel.setStyleSheet("color: gray;")
        insightToolbar.addWidget(self.insightMetaLabel)
        insightToolbar.addStretch(1)
        self.copyInsightBtn = PushButton("📋 复制", insightContainer)
        self.copyInsightBtn.setEnabled(False)
        self.copyInsightBtn.clicked.connect(self._onCopyInsightClicked)
        insightToolbar.addWidget(self.copyInsightBtn)
        self.deleteInsightBtn = PushButton("🗑️ 删除", insightContainer)
        self.deleteInsightBtn.setEnabled(False)
        self.deleteInsightBtn.clicked.connect(self._onDeleteInsightClicked)
        insightToolbar.addWidget(self.deleteInsightBtn)
        insightLayout.addLayout(insightToolbar)

        self.insightView = QLabel(
            "(选中左侧解读以查看内容)\n\n点击「✨ 生成研究报告」可基于本项目生成新的 AI 报告。",
            insightContainer,
        )
        self.insightView.setWordWrap(True)
        self.insightView.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.insightView.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.insightView.setStyleSheet(
            "color: #444; background: #fafafa; padding: 8px; "
            "border: 1px solid #e5e7eb; border-radius: 4px;"
        )
        self.insightView.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        insightLayout.addWidget(self.insightView, 1)
        aiArea.addWidget(insightContainer, 1)
        layout.addLayout(aiArea, 1)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def setProject(self, projectId: str) -> None:
        """切换当前项目,刷新只读笔记列表 + AI 解读 + 内容面板。"""
        self._projectId = projectId
        self._currentInsightId = ""
        self.refresh()

    def setResourceScope(self, resourceId: Optional[str]) -> None:
        """由父容器在选中资源时调用 — 控制「生成研究报告」的范围。

        Args:
            resourceId: 资源 id;None 表示回到「全项目」范围
        """
        self._resourceScope = resourceId
        self._updateAiHint()

    def refresh(self) -> None:
        """刷新 AI 解读列表 + 内容面板。"""
        try:
            self._renderInsightList()
            self._updateAiHint()
            self._updateGenerateBtnState()
        except Exception as e:
            logger.warning(f"[AiInsightsPanel] refresh 失败: {e}")

    # ------------------------------------------------------------------
    # AI 解读管理
    # ------------------------------------------------------------------
    def _connectAiService(self) -> None:
        """连接 ResearchReportService 信号。"""
        try:
            from app.core.services.research_report_service import researchReportService

            researchReportService.reportStarted.connect(self._onReportStarted)
            researchReportService.reportStreamReceived.connect(
                self._onReportStreamReceived
            )
            researchReportService.reportFinished.connect(self._onReportFinished)
            researchReportService.reportFailed.connect(self._onReportFailed)
        except Exception as e:
            logger.warning(f"[AiInsightsPanel] 连接 AI 服务失败: {e}")

    def _renderInsightList(self) -> None:
        """渲染 AI 解读列表(支持选中查看)。"""
        try:
            self.insightList.blockSignals(True)
            try:
                self.insightList.clear()
                self._insights = []
                if not self._projectId:
                    return
                self._insights = projectManager.listAiInsights(self._projectId)
                for a in self._insights:
                    typeBadge = "📝"  # 默认
                    if (a.analysisType or "") == "research_report":
                        typeBadge = "📄"
                    elif (a.analysisType or "") == "ai_insight":
                        typeBadge = "🤖"
                    ts = (a.createdAt or "")[5:16]
                    title = (a.content or "").strip().split("\n", 1)[0][:24] or "(报告)"
                    display = f"{typeBadge} {title}\n   {ts}"
                    item = QListWidgetItem(display)
                    item.setData(Qt.UserRole, a.id)
                    item.setToolTip(a.content or "")
                    self.insightList.addItem(item)
            finally:
                self.insightList.blockSignals(False)
            # 选中态恢复
            if self._currentInsightId:
                self._selectInsightInList(self._currentInsightId)
            elif self._insights:
                firstId = getattr(self._insights[0], "id", "")
                if firstId:
                    self._selectInsightInList(firstId)
            else:
                self._clearInsightView()
        except Exception as e:
            logger.warning(f"[AiInsightsPanel] _renderInsightList 失败: {e}")

    def _selectInsightInList(self, insightId: str) -> bool:
        for i in range(self.insightList.count()):
            item = self.insightList.item(i)
            if item is None:
                continue
            if item.data(Qt.UserRole) == insightId:
                self.insightList.setCurrentItem(item)
                return True
        return False

    def _clearInsightView(self) -> None:
        self.insightView.setText(
            "(选中左侧解读以查看内容)\n\n点击「✨ 生成研究报告」可基于本项目生成新的 AI 报告。"
        )
        self.insightMetaLabel.setText("")
        self.copyInsightBtn.setEnabled(False)
        self.deleteInsightBtn.setEnabled(False)

    def _updateAiHint(self) -> None:
        """更新 AI 提示文本(说明当前报告范围)。"""
        if not self._projectId:
            self.aiHint.setText("(请先选择或创建一个项目)")
            return
        if self._resourceScope:
            self.aiHint.setText(
                "📊 报告范围:仅当前选中资源(同时纳入项目级 + 该资源的笔记)"
            )
        else:
            self.aiHint.setText("📁 报告范围:全项目资源(自动跳过已弃用资源)")

    def _updateGenerateBtnState(self) -> None:
        """根据项目状态启用/禁用生成按钮。"""
        from app.core.services.research_report_service import researchReportService

        canGenerate = bool(self._projectId) and not researchReportService.isRunning()
        self.generateReportBtn.setEnabled(canGenerate)
        if researchReportService.isRunning():
            self.generateReportBtn.setText("⏳ 生成中…")
        else:
            self.generateReportBtn.setText("✨ 生成研究报告")

    def _onInsightSelectionChanged(
        self,
        current: Optional[QListWidgetItem],
        _previous: Optional[QListWidgetItem],
    ) -> None:
        try:
            if current is None:
                self._currentInsightId = ""
                self._clearInsightView()
                return
            insightId = current.data(Qt.UserRole) or ""
            if not insightId:
                self._currentInsightId = ""
                self._clearInsightView()
                return
            insight = self._findInsightById(insightId)
            self._currentInsightId = insightId
            if insight is None:
                self._clearInsightView()
                return
            content = insight.content or ""
            self.insightView.setText(content)
            metaParts = [
                f"类型:{insight.analysisType or '?'}",
                f"创建:{insight.createdAt or '—'}",
            ]
            if insight.model:
                metaParts.append(f"模型:{insight.model}")
            if insight.confidence:
                metaParts.append(f"置信度:{insight.confidence}")
            self.insightMetaLabel.setText("  ·  ".join(metaParts))
            self.copyInsightBtn.setEnabled(bool(content))
            self.deleteInsightBtn.setEnabled(True)
        except Exception as e:
            logger.warning(f"[AiInsightsPanel] _onInsightSelectionChanged 失败: {e}")

    def _findInsightById(self, insightId: str) -> Optional[Any]:
        for a in self._insights:
            if getattr(a, "id", "") == insightId:
                return a
        if self._projectId:
            project = projectManager.getProject(self._projectId)
            if project is not None:
                for a in project.aiInsights:
                    if a.id == insightId:
                        return a
        return None

    # ------------------------------------------------------------------
    # AI 报告生成槽
    # ------------------------------------------------------------------
    def _onGenerateReportClicked(self) -> None:
        try:
            from app.core.services.research_report_service import researchReportService

            if not self._projectId:
                return
            if researchReportService.isRunning():
                return
            ok = researchReportService.generate(
                projectId=self._projectId,
                resourceScope=self._resourceScope,
            )
            if ok:
                # 切到流式预览(临时显示正在生成)
                self.insightView.setText("⏳ 正在生成研究报告…")
                self.insightMetaLabel.setText("AI 思考中,请稍候")
                self.copyInsightBtn.setEnabled(False)
                self.deleteInsightBtn.setEnabled(False)
                self._updateGenerateBtnState()
        except Exception as e:
            logger.warning(f"[AiInsightsPanel] _onGenerateReportClicked 失败: {e}")

    def _onReportStarted(self) -> None:
        try:
            self._updateGenerateBtnState()
            self.aiStatusLabel.setText("⏳ AI 正在思考…")
            self.aiStatusLabel.setStyleSheet("color: #d97706;")
        except Exception as e:
            logger.warning(f"[AiInsightsPanel] _onReportStarted 回调异常: {e}")

    def _onReportStreamReceived(self, delta: str) -> None:
        try:
            # 把流式增量追加到预览(初次进入时初始化)
            currentText = self.insightView.text()
            if currentText.startswith("⏳") or currentText.startswith("(选中"):
                self.insightView.setText(delta)
            else:
                self.insightView.setText(currentText + delta)
        except Exception as e:
            logger.warning(f"[AiInsightsPanel] _onReportStreamReceived 异常: {e}")

    def _onReportFinished(self, insightId: str, content: str) -> None:
        try:
            self.aiStatusLabel.setText("✓ 报告已归档")
            self.aiStatusLabel.setStyleSheet("color: #16a34a;")
            self._currentInsightId = insightId
            self._renderInsightList()
            self._selectInsightInList(insightId)
            self._updateGenerateBtnState()
            # 通知父容器刷新(让顶部 banner 统计也更新)
            projectManager.projectListChanged.emit()
        except Exception as e:
            logger.warning(f"[AiInsightsPanel] _onReportFinished 回调异常: {e}")

    def _onReportFailed(self, errorMsg: str) -> None:
        try:
            self.aiStatusLabel.setText(f"✗ 失败:{errorMsg}")
            self.aiStatusLabel.setStyleSheet("color: #dc2626;")
            self.insightView.setText(f"(生成失败)\n\n{errorMsg}")
            self._updateGenerateBtnState()
        except Exception as e:
            logger.warning(f"[AiInsightsPanel] _onReportFailed 回调异常: {e}")

    def _onCopyInsightClicked(self) -> None:
        try:
            insight = self._findInsightById(self._currentInsightId)
            if insight is None:
                return
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(insight.content or "")
            self.aiStatusLabel.setText("📋 已复制到剪贴板")
            self.aiStatusLabel.setStyleSheet("color: #16a34a;")
        except Exception as e:
            logger.warning(f"[AiInsightsPanel] _onCopyInsightClicked 失败: {e}")

    def _onDeleteInsightClicked(self) -> None:
        try:
            if not self._currentInsightId or not self._projectId:
                return
            from qfluentwidgets import MessageBox

            mb = MessageBox(
                "确认删除",
                "确定删除这条 AI 解读归档吗?\n\n此操作不可撤销。",
                self.window(),
            )
            mb.yesButton.setText("删除")
            mb.cancelButton.setText("取消")
            if not mb.exec():
                return
            projectManager.deleteAiInsight(self._projectId, self._currentInsightId)
            self._currentInsightId = ""
            self.refresh()
        except Exception as e:
            logger.warning(f"[AiInsightsPanel] _onDeleteInsightClicked 失败: {e}")


__all__ = [
    "ResourcePoolPanel",
    "ResourceDetailPanel",
    "AiInsightsPanel",
    "resourceTypeLabel",
]


# 暴露一个公共别名,方便外部直接 import
def resourceTypeLabel(resourceType: str) -> str:
    return _resourceTypeLabel(resourceType)
