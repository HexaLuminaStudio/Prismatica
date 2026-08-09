"""HSK / Global 共用下载任务工作台回归测试。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QBoxLayout

from app.view.global_interface import GlobalInterface
from app.view.hsk_interface import HskInterface
from app.view.resource import resource  # noqa: F401
from app.view.widgets import download_workbench as workbenchModule
from app.view.widgets.download_workbench import DownloadTaskWorkbench
from app.core.services import batchApplyService


def test_hsk_and_global_share_the_same_workbench_component(qtbot) -> None:
    batchApplyService.clearAll()
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
    assert hskInterface.batchAddButton.text() == "加入清单"
    assert hskInterface.batchDownloadButton.text() == "提交批量任务 (0)"


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


def test_mode_card_icons_stay_anchored_during_responsive_reflow(qtbot) -> None:
    interfaces = (HskInterface(), GlobalInterface())
    for interface in interfaces:
        qtbot.addWidget(interface)
        interface.show()

        for width in (900, 680, 1300, 760):
            interface.resize(width, 820)
            qtbot.wait(10)
            for button in interface.workbench.modeRail._buttons.values():
                iconGeometry = button.iconWidget.geometry()
                buttonCenterY = button.rect().center().y()
                assert 10 <= iconGeometry.left() <= 16
                assert abs(iconGeometry.center().y() - buttonCenterY) <= 1
                assert button.titleLabel.geometry().left() >= (
                    iconGeometry.right() + 8
                )

        targetButton = interface.workbench.modeRail.button(
            list(interface.workbench.modeRail._buttons)[1]
        )
        qtbot.mouseClick(targetButton, Qt.MouseButton.LeftButton)
        assert targetButton.isChecked()


def test_batch_add_uses_inline_preview_instead_of_a_dialog(
    qtbot,
    monkeypatch,
) -> None:
    batchApplyService.clearAll()
    hskInterface = HskInterface()
    qtbot.addWidget(hskInterface)
    hskInterface.stringGeneralSearchWidget.keyWord.setText("学习")
    queuedInfo = []
    monkeypatch.setattr(
        hskInterface.workbench,
        "enqueueBatchItem",
        lambda infoDict: queuedInfo.append(infoDict) or True,
    )

    hskInterface._onBatchAddClicked()

    assert len(queuedInfo) == 1
    assert queuedInfo[0]["payload"]["keyword"] == "学习"


def test_inline_batch_list_renders_removes_and_keeps_source_isolation(
    qtbot,
) -> None:
    batchApplyService.clearAll()
    hskInterface = HskInterface()
    globalInterface = GlobalInterface()
    qtbot.addWidget(hskInterface)
    qtbot.addWidget(globalInterface)

    hskInterface.workbench._pendingBatchInfoDict = {
        "url": "https://example.test/hsk",
        "payload": {"keyword": "学习", "nationality": "日本"},
    }
    hskInterface.workbench._onBatchPreviewFinished(128)

    assert batchApplyService.getCount("hskDownload") == 1
    assert hskInterface.batchDownloadButton.text() == "提交批量任务 (1)"
    assert hskInterface.workbench._batchListScroll.isVisibleTo(
        hskInterface.workbench._summaryPanel
    )
    assert globalInterface.batchDownloadButton.text() == "提交批量任务 (0)"

    hskInterface.workbench.removeBatchItem(0)

    assert batchApplyService.getCount("hskDownload") == 0
    assert not hskInterface.batchDownloadButton.isEnabled()


def test_zero_result_is_not_added_to_batch_list(qtbot, monkeypatch) -> None:
    batchApplyService.clearAll()
    hskInterface = HskInterface()
    qtbot.addWidget(hskInterface)
    warnings = []
    monkeypatch.setattr(
        workbenchModule.InfoBar,
        "warning",
        lambda *args, **kwargs: warnings.append(kwargs),
    )
    hskInterface.workbench._pendingBatchInfoDict = {
        "url": "https://example.test/hsk",
        "payload": {"keyword": "不存在的条件"},
    }

    hskInterface.workbench._onBatchPreviewFinished(0)

    assert batchApplyService.getCount("hskDownload") == 0
    assert warnings[0]["title"] == "未加入清单"
