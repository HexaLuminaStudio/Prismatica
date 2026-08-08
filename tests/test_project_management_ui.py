"""项目管理设计落地的组件级测试。"""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QAbstractButton, QFrame, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import MessageBoxBase

from app.core.models.project import AiInsight, Project, Resource


_app = QApplication.instance() or QApplication(sys.argv)


class _FakeProjectManager(QObject):
    projectListChanged = Signal()
    activeProjectChanged = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        resource = Resource(
            id="resource-1",
            type="freq",
            title="高频词统计",
            summary="展示目标语料中最常见的词语。",
            createdAt="2026-08-08T10:30:00",
        )
        insight = AiInsight(
            id="insight-1",
            analysisType="research_report",
            content="高频词分布体现了语料中的核心教学话题。",
            createdAt="2026-08-08T11:00:00",
        )
        self.project = Project(
            id="project-1",
            name="现代汉语口语研究",
            description="面向课堂互动语料的探索性研究。",
            tags=["口语", "教学"],
            createdAt="2026-08-01T09:00:00",
            updatedAt="2026-08-08T11:00:00",
            resources=[resource],
            aiInsights=[insight],
        )

    def listProjects(self):
        return [self.project]

    def activeProject(self):
        return self.project

    def getProject(self, projectId: str):
        return self.project if projectId == self.project.id else None

    def listResources(self, projectId: str):
        project = self.getProject(projectId)
        return list(project.resources) if project else []

    def listAiInsights(self, projectId: str):
        project = self.getProject(projectId)
        return list(project.aiInsights) if project else []


def _host(width: int = 1200, height: int = 800) -> QWidget:
    parent = QWidget()
    parent.resize(width, height)
    parent.show()
    QApplication.processEvents()
    return parent


def test_new_project_form_is_message_box_and_returns_design_fields() -> None:
    from app.view.widgets.project_manager_dialogs import NewProjectDialog

    parent = _host()
    dialog = NewProjectDialog(parent)

    assert isinstance(dialog, MessageBoxBase)
    assert dialog.yesButton.text() == "创建项目"
    assert dialog.yesButton.minimumHeight() == 36
    assert not dialog.yesButton.icon().isNull()
    assert all(
        item.minimumHeight() == 36
        for item in dialog.templatePicker.findChildren(QAbstractButton)
    )
    assert not dialog.yesButton.isEnabled()

    dialog.nameEdit.setText("课堂口语研究")
    dialog.tagsEdit.setText("口语，教学, 口语")
    dialog.templatePicker.setCurrentItem("academic")
    dialog._setTemplate("academic")
    result = dialog.getResult()

    assert dialog.yesButton.isEnabled()
    assert result == {
        "name": "课堂口语研究",
        "description": "",
        "template": "学术研究",
        "tags": ["口语", "教学"],
    }
    dialog.close()
    parent.close()


def test_project_list_uses_metrics_filters_and_card_grid(monkeypatch) -> None:
    import app.view.widgets.project_manager_widget as module

    fake = _FakeProjectManager()
    monkeypatch.setattr(module, "projectManager", fake)
    widget = module.ProjectManagerWidget()

    assert widget.totalMetric.value.text() == "1 个项目"
    assert widget.filterPivot is not None
    assert len(widget._cards) == 1
    assert widget._cards[0].project.name == "现代汉语口语研究"
    assert widget.newButton.minimumHeight() == 36
    assert widget.newButton.minimumWidth() == 112
    widget.close()


def test_project_list_reflows_controls_before_text_is_compressed(monkeypatch) -> None:
    import app.view.widgets.project_manager_widget as module

    fake = _FakeProjectManager()
    monkeypatch.setattr(module, "projectManager", fake)
    widget = module.ProjectManagerWidget()
    host = _host(720, 760)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(widget)
    QApplication.processEvents()
    widget._reflowResponsive()

    assert widget._layoutMode == "compact"
    assert widget.resourceMetric.y() > widget.totalMetric.y()
    assert widget.searchEdit.y() > widget.filterPivot.y()
    assert widget._columns == 1
    openButton = widget._cards[0].findChild(QAbstractButton, "projectOpenButton")
    assert openButton is not None and openButton.height() >= 34
    host.close()


def test_project_empty_state_hides_metrics_and_filters(monkeypatch) -> None:
    import app.view.widgets.project_manager_widget as module

    fake = _FakeProjectManager()
    fake.project = None
    fake.listProjects = lambda: []
    fake.activeProject = lambda: None
    monkeypatch.setattr(module, "projectManager", fake)
    widget = module.ProjectManagerWidget()

    assert widget.emptyHost.isVisibleTo(widget)
    assert widget.metricsHost.isHidden()
    assert widget.toolbar.isHidden()
    widget.close()


def test_project_dashboard_renders_three_design_panels(monkeypatch) -> None:
    import app.view.widgets.project_dashboard_widget as module

    fake = _FakeProjectManager()
    monkeypatch.setattr(module, "projectManager", fake)
    widget = module.ProjectDashboardWidget()

    panels = widget.page.findChildren(QFrame, "dashboardPanel")
    assert len(panels) == 3
    assert widget.projectNameLabel.text() == "现代汉语口语研究"
    assert widget.resourceLayout.count() == 1
    assert widget.insightLayout.count() == 1
    assert widget.generateButton.text() == "生成新解读"
    widget.resize(760, 800)
    QApplication.processEvents()
    widget._reflowPanels()
    assert widget._panelColumns == 1
    infoIndex = widget.headerGrid.indexOf(widget.headerInfoHost)
    infoRow, _column, _rowSpan, _columnSpan = (
        widget.headerGrid.getItemPosition(infoIndex)
    )
    assert infoRow == 1
    category = widget.leftPanel.findChild(QAbstractButton, "resourceCategoryButton")
    assert category is not None and category.minimumHeight() == 32
    assert category.findChild(QLabel, "resourceCategoryCount") is not None
    widget.close()


def test_project_dashboard_wide_layout_matches_reference_density(monkeypatch) -> None:
    import app.view.widgets.project_dashboard_widget as module

    fake = _FakeProjectManager()
    monkeypatch.setattr(module, "projectManager", fake)
    widget = module.ProjectDashboardWidget()
    host = QWidget()
    host.resize(1260, 800)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(widget)
    host.show()
    QApplication.processEvents()
    widget._reflowPanels()

    assert widget._panelColumns == 3
    assert widget.leftPanel.maximumWidth() == 280
    assert widget.rightPanel.maximumWidth() == 320
    resourceRow = widget.centerPanel.findChild(QFrame, "dashboardResourceRow")
    insightCard = widget.rightPanel.findChild(QFrame, "dashboardInsightCard")
    assert resourceRow is not None and resourceRow.height() == 72
    assert insightCard is not None and insightCard.minimumHeight() == 118
    host.close()
