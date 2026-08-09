"""批量下载清单任务类型隔离回归测试。"""

from app.core.services import batchApplyService
from app.core.services.batch_apply_service import BatchApplyService
from app.core.services.task_manager import taskManager
from app.view import hsk_interface as hskInterfaceModule
from app.view.global_interface import GlobalInterface
from app.view.hsk_interface import HskInterface
from app.view.resource import resource  # noqa: F401


def test_batch_items_are_isolated_by_task_type() -> None:
    service = BatchApplyService()
    payload = {"keyword": "学习"}

    assert service.addItem("hskDownload", "https://example.test/search", payload, 12)
    assert service.addItem(
        "globalDownload",
        "https://example.test/search",
        payload,
        34,
    )
    assert not service.addItem(
        "hskDownload",
        "https://example.test/search",
        payload,
        56,
    )

    assert service.getCount() == 2
    assert service.getCount("hskDownload") == 1
    assert service.getCount("globalDownload") == 1
    assert service.getItems("hskDownload")[0].taskType == "hskDownload"
    assert service.getItems("globalDownload")[0].taskType == "globalDownload"
    assert service.containsItem(
        "hskDownload", "https://example.test/search", payload
    )
    assert not service.containsItem(
        "globalDownload", "https://example.test/other", payload
    )


def test_batch_summary_is_compact_and_user_readable() -> None:
    service = BatchApplyService()
    service.addItem(
        "hskDownload",
        "https://example.test/search",
        {
            "keyword": "学习",
            "nationality": "日本",
            "hsk_level": "六级",
            "page": 1,
            "per_page": 20,
        },
        128,
    )

    assert service.getItems("hskDownload")[0].summary() == (
        "关键词：学习 · 国籍：日本 · HSK 等级：六级"
    )


def test_remove_and_clear_only_affect_selected_task_type() -> None:
    service = BatchApplyService()
    service.addItem("hskDownload", "https://example.test/hsk/1", {"keyword": "一"})
    service.addItem("globalDownload", "https://example.test/global", {"keystr": "二"})
    service.addItem("hskDownload", "https://example.test/hsk/2", {"keyword": "三"})

    assert service.removeItem(0, "hskDownload")
    assert [item.url for item in service.getItems("hskDownload")] == [
        "https://example.test/hsk/2"
    ]
    assert service.getCount("globalDownload") == 1

    service.clearAll("hskDownload")

    assert service.getCount("hskDownload") == 0
    assert service.getCount("globalDownload") == 1
    assert service.getCount() == 1


def test_page_badges_only_show_their_own_task_type(qtbot) -> None:
    batchApplyService.clearAll()
    hskInterface = HskInterface()
    globalInterface = GlobalInterface()
    qtbot.addWidget(hskInterface)
    qtbot.addWidget(globalInterface)

    batchApplyService.addItem(
        "hskDownload",
        "https://example.test/hsk",
        {"keyword": "学习"},
    )

    assert hskInterface.batchDownloadButton.text() == "提交批量任务 (1)"
    assert hskInterface.batchDownloadButton.isEnabled()
    assert globalInterface.batchDownloadButton.text() == "提交批量任务 (0)"
    assert not globalInterface.batchDownloadButton.isEnabled()

    batchApplyService.addItem(
        "globalDownload",
        "https://example.test/global",
        {"keystr": "学习"},
    )
    batchApplyService.clearAll("hskDownload")

    assert hskInterface.batchDownloadButton.text() == "提交批量任务 (0)"
    assert not hskInterface.batchDownloadButton.isEnabled()
    assert globalInterface.batchDownloadButton.text() == "提交批量任务 (1)"
    assert globalInterface.batchDownloadButton.isEnabled()

    batchApplyService.clearAll()


def test_page_submission_keeps_other_task_type_in_the_list(
    qtbot,
    monkeypatch,
) -> None:
    batchApplyService.clearAll()
    createdTaskTypes = []

    def fakeCreateTask(taskType, _infoDict):
        createdTaskTypes.append(taskType)
        return f"task-{len(createdTaskTypes)}"

    monkeypatch.setattr(taskManager, "createTask", fakeCreateTask)
    monkeypatch.setattr(
        hskInterfaceModule.InfoBar,
        "success",
        lambda *args, **kwargs: None,
    )

    hskInterface = HskInterface()
    globalInterface = GlobalInterface()
    qtbot.addWidget(hskInterface)
    qtbot.addWidget(globalInterface)

    batchApplyService.addItem(
        "hskDownload",
        "https://example.test/hsk",
        {"keyword": "学习"},
    )
    batchApplyService.addItem(
        "globalDownload",
        "https://example.test/global",
        {"keystr": "学习"},
    )

    hskInterface._onBatchDownloadClicked()

    assert createdTaskTypes == ["hskDownload"]
    assert batchApplyService.getCount("hskDownload") == 0
    assert batchApplyService.getCount("globalDownload") == 1

    globalInterface._onBatchDownloadClicked()

    assert createdTaskTypes == ["hskDownload", "globalDownload"]
    assert batchApplyService.getCount() == 0


def test_partial_batch_failure_keeps_failed_item_for_retry(
    qtbot,
    monkeypatch,
) -> None:
    batchApplyService.clearAll()
    createdKeywords = []

    def fakeCreateTask(_taskType, infoDict):
        keyword = infoDict["payload"]["keyword"]
        if keyword == "失败项":
            raise RuntimeError("模拟创建失败")
        createdKeywords.append(keyword)
        return "task-success"

    monkeypatch.setattr(taskManager, "createTask", fakeCreateTask)
    monkeypatch.setattr(
        hskInterfaceModule.InfoBar,
        "warning",
        lambda *args, **kwargs: None,
    )
    hskInterface = HskInterface()
    qtbot.addWidget(hskInterface)
    batchApplyService.addItem(
        "hskDownload",
        "https://example.test/success",
        {"keyword": "成功项"},
    )
    batchApplyService.addItem(
        "hskDownload",
        "https://example.test/failure",
        {"keyword": "失败项"},
    )

    hskInterface._onBatchDownloadClicked()

    assert createdKeywords == ["成功项"]
    remainingItems = batchApplyService.getItems("hskDownload")
    assert len(remainingItems) == 1
    assert remainingItems[0].payload["keyword"] == "失败项"
    batchApplyService.clearAll()
