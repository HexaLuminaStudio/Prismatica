# coding: utf-8
"""顶栏项目切换器不可选项回归测试。"""
from __future__ import annotations

from app.core.models.project import Project
from app.view.widgets import project_switcher_widget as switcherModule
from app.view.widgets.project_switcher_widget import ProjectSwitcher


def _buildSwitcher(qtbot, monkeypatch, activeProject):
    projects = [activeProject] if activeProject is not None else []
    setActiveCalls = []
    monkeypatch.setattr(
        switcherModule.projectManager,
        "activeProject",
        lambda: activeProject,
    )
    monkeypatch.setattr(
        switcherModule.projectManager,
        "listProjects",
        lambda: projects,
    )
    monkeypatch.setattr(
        switcherModule.projectManager,
        "setActiveProject",
        lambda projectId: setActiveCalls.append(projectId),
    )
    widget = ProjectSwitcher()
    qtbot.addWidget(widget)
    return widget, setActiveCalls


def testSeparatorIsDisabledAndCannotReplaceActiveProject(qtbot, monkeypatch) -> None:
    activeProject = Project(id="active-project", name="当前项目")
    widget, setActiveCalls = _buildSwitcher(
        qtbot,
        monkeypatch,
        activeProject,
    )
    comboBox = widget._comboBox
    separatorIndex = comboBox.findData(switcherModule._SENTINEL_SEPARATOR)

    assert separatorIndex >= 0
    assert comboBox.items[separatorIndex].isEnabled is False
    assert comboBox.currentData() == activeProject.id

    comboBox._onItemClicked(separatorIndex)

    assert comboBox.currentData() == activeProject.id
    assert comboBox.currentText() == activeProject.name
    assert setActiveCalls == []

    # 即使外部代码强制设置到分隔线，防御逻辑也必须恢复真实项目。
    comboBox.setCurrentIndex(separatorIndex)

    assert comboBox.currentData() == activeProject.id
    assert comboBox.currentText() == activeProject.name
    assert setActiveCalls == []


def testNoActivePlaceholderAndSeparatorAreBothDisabled(qtbot, monkeypatch) -> None:
    widget, setActiveCalls = _buildSwitcher(qtbot, monkeypatch, None)
    comboBox = widget._comboBox
    placeholderIndex = comboBox.findData(switcherModule._SENTINEL_NO_ACTIVE)
    separatorIndex = comboBox.findData(switcherModule._SENTINEL_SEPARATOR)

    assert comboBox.items[placeholderIndex].isEnabled is False
    assert comboBox.items[separatorIndex].isEnabled is False
    assert comboBox.currentData() == switcherModule._SENTINEL_NO_ACTIVE
    assert comboBox.currentText() == "未选择项目"
    assert setActiveCalls == []


def testManagementActionsRemainSelectableAndRestoreActiveProject(
    qtbot,
    monkeypatch,
) -> None:
    activeProject = Project(id="active-project", name="当前项目")
    widget, setActiveCalls = _buildSwitcher(
        qtbot,
        monkeypatch,
        activeProject,
    )
    comboBox = widget._comboBox
    emittedActions = []
    widget.manageRequested.connect(lambda: emittedActions.append("manage"))
    widget.newRequested.connect(lambda: emittedActions.append("new"))
    manageIndex = comboBox.findData(switcherModule._SENTINEL_MANAGE)
    newIndex = comboBox.findData(switcherModule._SENTINEL_NEW)

    assert comboBox.items[manageIndex].isEnabled is True
    assert comboBox.items[newIndex].isEnabled is True

    comboBox._onItemClicked(manageIndex)
    comboBox._onItemClicked(newIndex)

    assert emittedActions == ["manage", "new"]
    assert comboBox.currentData() == activeProject.id
    assert comboBox.currentText() == activeProject.name
    assert setActiveCalls == []
