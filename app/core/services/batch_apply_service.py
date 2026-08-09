# coding: utf-8
"""
批量下载申请服务（会话级）

持有"本次会话"用户配置的 HSK / Global 检索条件，并按任务类型隔离。
由 HskInterface、GlobalInterface 与 BatchDownloadDialog 共享。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal


@dataclass
class BatchItem:
    """清单条目:一组待提交的检索条件"""

    taskType: str
    url: str
    payload: Dict[str, Any]
    total: int = 0
    addedAt: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def toInfoDict(self) -> Dict[str, Any]:
        """构造 taskManager.createTask 所需的 infoDict"""
        return {"url": self.url, "payload": dict(self.payload)}

    def isSameAs(self, taskType: str, url: str, payload: Dict[str, Any]) -> bool:
        """判断是否与给定任务类型、url 和 payload 重复。"""
        if self.taskType != taskType or self.url != url:
            return False
        return self.payload == payload

    def summary(self) -> str:
        """生成检索条件摘要(用于清单列表展示)"""
        if not self.payload:
            return "(无检索条件)"
        parts: List[str] = []
        for key, value in self.payload.items():
            if value in (None, "", [], 0):
                continue
            if isinstance(value, list):
                parts.append(f"{key}={','.join(str(v) for v in value)}")
            else:
                parts.append(f"{key}={value}")
        return "; ".join(parts) if parts else "(无有效条件)"


class BatchApplyService(QObject):
    """批量下载清单服务(进程级单例)。

    Signals:
        itemsChanged(int): 清单数量变化时 emit,参数为最新数量。
    """

    itemsChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._items: List[BatchItem] = []

    def addItem(
        self,
        taskType: str,
        url: str,
        payload: Dict[str, Any],
        total: int = 0,
    ) -> bool:
        """添加一项到清单。

        Returns:
            True 表示新增成功,False 表示与同类既有条目重复(未添加)。
        """
        for existing in self._items:
            if existing.isSameAs(taskType, url, payload):
                return False
        self._items.append(
            BatchItem(
                taskType=taskType,
                url=url,
                payload=dict(payload),
                total=total,
            )
        )
        self.itemsChanged.emit(len(self._items))
        return True

    def removeItem(self, index: int, taskType: Optional[str] = None) -> bool:
        """按指定任务类型清单中的索引删除一项。"""
        matchingIndexes = [
            itemIndex
            for itemIndex, item in enumerate(self._items)
            if taskType is None or item.taskType == taskType
        ]
        if 0 <= index < len(matchingIndexes):
            del self._items[matchingIndexes[index]]
            self.itemsChanged.emit(len(self._items))
            return True
        return False

    def clearAll(self, taskType: Optional[str] = None) -> None:
        """清空全部清单，或仅清空指定任务类型。"""
        if taskType is None:
            if not self._items:
                return
            self._items.clear()
            self.itemsChanged.emit(0)
            return

        remainingItems = [item for item in self._items if item.taskType != taskType]
        if len(remainingItems) != len(self._items):
            self._items = remainingItems
            self.itemsChanged.emit(len(self._items))

    def getItems(self, taskType: Optional[str] = None) -> List[BatchItem]:
        """返回全部或指定任务类型的清单副本。"""
        return [
            item
            for item in self._items
            if taskType is None or item.taskType == taskType
        ]

    def getCount(self, taskType: Optional[str] = None) -> int:
        """返回全部或指定任务类型的清单数量。"""
        return len(self.getItems(taskType))

    def getItem(
        self,
        index: int,
        taskType: Optional[str] = None,
    ) -> Optional[BatchItem]:
        """按指定任务类型清单中的索引获取一项。"""
        items = self.getItems(taskType)
        if 0 <= index < len(items):
            return items[index]
        return None


# 全局单例
batchApplyService = BatchApplyService()
