from __future__ import annotations


def test_download_card_supports_all_visual_states(qtbot):
    from app.view.widgets.download_card import DownloadCard

    card = DownloadCard(
        {
            "taskId": "task-ui-test",
            "type": "hskDownload",
            "payload": {"keyword": "现代汉语", "hsk_level": "5"},
        }
    )
    qtbot.addWidget(card)

    card.setQueued()
    assert card.statusLabel.text() == "排队中"
    assert not card.pauseButton.isEnabled()

    card.setRunning()
    card.updateProgress(64, "第 64 / 100 页", "4.2 MB/s", "剩余 02:41")
    assert card.statusLabel.text() == "下载中"
    assert card.progressBar.value() == 64
    assert card.percentLabel.text() == "64%"

    card.setFailed("网络请求连续三次超时")
    assert card.statusLabel.text() == "失败"
    assert not card.errorFrame.isHidden()
    assert card.errorLabel.text() == "网络请求连续三次超时"
    assert card.height() == 178

    card.setCompleted()
    assert card.statusLabel.text() == "成功"
    assert card.progressBar.value() == 100
    assert card.percentLabel.text() == "100%"
    assert card.height() == 120
    assert card.errorFrame.isHidden()


def test_task_interface_has_three_status_pages(qtbot):
    from app.view.task_interface import TaskInterface

    page = TaskInterface()
    qtbot.addWidget(page)

    assert page.stackedWidget.count() == 3
    assert set(page.pivot.items) == {"inProgress", "completed", "failed"}
    assert page.newTaskShortcut.key().toString() == "Ctrl+N"
