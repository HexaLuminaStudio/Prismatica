"""HSK / Global 共用下载任务工作台回归测试。"""

from PySide6.QtWidgets import QBoxLayout

from app.view.global_interface import GlobalInterface
from app.view.hsk_interface import HskInterface
from app.view.resource import resource  # noqa: F401
from app.view.widgets.download_workbench import DownloadTaskWorkbench


def test_hsk_and_global_share_the_same_workbench_component(qtbot) -> None:
    hskInterface = HskInterface()
    globalInterface = GlobalInterface()
    qtbot.addWidget(hskInterface)
    qtbot.addWidget(globalInterface)

    assert isinstance(hskInterface.workbench, DownloadTaskWorkbench)
    assert isinstance(globalInterface.workbench, DownloadTaskWorkbench)
    assert hskInterface.workbench.searchStack.count() == 4
    assert globalInterface.workbench.searchStack.count() == 4
    assert hskInterface.runTaskButton.text() == "创建下载任务"
    assert globalInterface.runTaskButton.text() == "创建下载任务"


def test_workbench_switches_real_forms_and_collapses_advanced_filters(qtbot) -> None:
    globalInterface = GlobalInterface()
    qtbot.addWidget(globalInterface)

    assert globalInterface.workbench.currentRouteKey() == "stringGeneral"
    assert globalInterface.workbench.searchStack.currentWidget() is (
        globalInterface.stringGeneralSearchWidget
    )
    assert all(
        groupWidget.isHidden()
        for groupWidget in globalInterface.advancedSettingCardWidget.groupWidgets
    )

    globalInterface.typeSegmentedWidget.setCurrentItem("speechPart")
    globalInterface.advancedSettingCardWidget.enableCheckBox.setChecked(True)

    assert globalInterface.workbench.searchStack.currentWidget() is (
        globalInterface.speechPartSearchWidget
    )
    assert all(
        not groupWidget.isHidden()
        for groupWidget in globalInterface.advancedSettingCardWidget.groupWidgets
    )


def test_workbench_reflows_and_keeps_real_download_payloads(qtbot) -> None:
    hskInterface = HskInterface()
    globalInterface = GlobalInterface()
    qtbot.addWidget(hskInterface)
    qtbot.addWidget(globalInterface)
    hskInterface.show()
    globalInterface.show()

    hskInterface.resize(1300, 800)
    qtbot.wait(10)
    assert hskInterface.workbench._workspaceLayout.direction() == (
        QBoxLayout.Direction.LeftToRight
    )

    globalInterface.resize(900, 760)
    qtbot.wait(10)
    assert globalInterface.workbench._workspaceLayout.direction() == (
        QBoxLayout.Direction.TopToBottom
    )

    hskInterface.stringGeneralSearchWidget.keyWord.setText("学习")
    globalInterface.stringGeneralSearchWidget.keyWord.setText("学习")
    hskInfo = hskInterface._buildInfoDict()
    globalInfo = globalInterface._buildInfoDict()

    assert hskInfo["payload"]["keyword"] == "学习"
    assert hskInfo["url"].endswith("/sentence/search/keyword")
    assert globalInfo["payload"]["keystr"] == "学习"
    assert globalInfo["url"].endswith("/corp/index/getzfcsample")
