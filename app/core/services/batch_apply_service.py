# coding: utf-8
"""
批量下载申请服务（会话级）

持有"本次会话"用户配置的 N 组 HSK 检索条件。
由 HskInterface 顶栏徽章与 BatchDownloadDialog 共享。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal


@dataclass
class BatchItem:
    """清单条目:一组待提交的检索条件"""

    url: str
    payload: Dict[str, Any]
    total: int = 0
    addedAt: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def toInfoDict(self) -> Dict[str, Any]:
        """构造 taskManager.createTask 所需的 infoDict"""
        return {"url": self.url, "payload": dict(self.payload)}

    def isSameAs(self, url: str, payload: Dict[str, Any]) -> bool:
        """判断是否与给定 url+payload 重复"""
        if self.url != url:
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

    def addItem(self, url: str, payload: Dict[str, Any], total: int = 0) -> bool:
        """添加一项到清单。

        Returns:
            True 表示新增成功,False 表示与既有条目重复(未添加)。
        """
        for existing in self._items:
            if existing.isSameAs(url, payload):
                return False
        self._items.append(BatchItem(url=url, payload=dict(payload), total=total))
        self.itemsChanged.emit(len(self._items))
        return True

    def removeItem(self, index: int) -> bool:
        """按索引删除一项。"""
        if 0 <= index < len(self._items):
            del self._items[index]
            self.itemsChanged.emit(len(self._items))
            return True
        return False

    def clearAll(self) -> None:
        """清空清单。"""
        if self._items:
            self._items.clear()
            self.itemsChanged.emit(0)

    def getItems(self) -> List[BatchItem]:
        """返回清单副本(避免外部误改内部状态)。"""
        return list(self._items)

    def getCount(self) -> int:
        """返回清单数量。"""
        return len(self._items)

    def getItem(self, index: int) -> Optional[BatchItem]:
        """按索引获取一项(返回副本不可变性的引用)。"""
        if 0 <= index < len(self._items):
            return self._items[index]
        return None


# 全局单例
batchApplyService = BatchApplyService()
