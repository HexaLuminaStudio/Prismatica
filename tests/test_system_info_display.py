# coding: utf-8
"""设置页系统信息显示回归测试。"""

from types import SimpleNamespace

from app.core.services import system_info_service
from app.view.setting_interface import AboutSettingWidget


def testSystemInfoValuesKeepVisibleWidth(qtbot) -> None:
    widget = AboutSettingWidget()
    widget.resize(832, 560)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.wait(20)

    assert len(widget.systemInfoValueLabels) == 4
    assert all(label.text().strip() for label in widget.systemInfoValueLabels)
    assert all(label.width() > 0 for label in widget.systemInfoValueLabels)


def testSystemInfoValuesStayVisibleInCompactLayout(qtbot) -> None:
    widget = AboutSettingWidget()
    widget.setCompactLayout(True)
    widget.resize(420, 680)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.wait(20)

    for cell, valueLabel in zip(
        widget.systemInfoCells, widget.systemInfoValueLabels, strict=True
    ):
        assert valueLabel.width() > 0
        assert valueLabel.geometry().right() <= cell.contentsRect().right()


def testWindows11UsesBuildNumberInsteadOfCompatibilityRelease(monkeypatch) -> None:
    monkeypatch.setattr(system_info_service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(system_info_service.platform, "release", lambda: "10")
    monkeypatch.setattr(
        system_info_service.sys,
        "getwindowsversion",
        lambda: SimpleNamespace(major=10, build=26200),
        raising=False,
    )

    label = system_info_service.SystemInfoService._getOperatingSystemLabel()

    assert label == "Windows 11"
