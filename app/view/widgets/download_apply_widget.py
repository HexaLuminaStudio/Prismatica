# coding: utf-8

from typing import Literal, Dict, Any
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QFrame
from PySide6.QtCore import Qt
from app.core.utils import logger

from qfluentwidgets import (
    MessageBoxBase,
    SubtitleLabel,
    BodyLabel,
    CardWidget,
    ImageLabel,
)

from app.core.services import GetTotalWorker, GlobalGetTotalWorker


# 参数标签映射（中文化）
PARAM_LABELS = {
    # HSK参数
    "keyword": "关键词",
    "nationality": "国籍",
    "hsk_level": "HSK等级",
    "essay_title": "作文题目",
    "score": "分数",
    "写作文体": "写作文体",
    "作文长度": "作文长度",
    "性别": "性别",
    "第一语言": "第一语言",
    "第二语言": "第二语言",
    "page": "页码",
    "per_page": "每页数量",
    "title": "作文题目",
    "level": "作文等级",
    "nation": "国籍",
    "start_word": "首字符串",
    "pre_word": "前词",
    "word_distance": "词距",
    "post_word": "后词",
    "end_word": "尾字符串",
    "depType": "句法结构",
    "wrong_type": "错句类型",
    # Global参数
    "keystr": "关键字",
    "tablename": "语料类型",
    "shou": "首字符串",
    "kaishi": "前词",
    "num": "距离",
    "jieshu": "后词",
    "wei": "尾字符串",
    "orderstr": "排序方向",
    "showlenght": "检索后字符数",
    "tag": "标签",
    "txt": "文本",
    "mothertongue": "母语",
    "shkgrade": "HSK等级",
    "ext1": "汉语水平",
    "authornationality": "作者国籍",
    "ft": "语料类型",
    "corp_org_id": "机构ID",
    "isDeptCheck": "部门检查",
}


def formatParams(params: Dict[str, Any]) -> str:
    """格式化参数字典为易读字符串"""
    if not params:
        return "无"

    lines = []
    for key, value in params.items():
        label = PARAM_LABELS.get(key, key)
        if isinstance(value, list):
            valueStr = ", ".join(str(v) for v in value)
        elif isinstance(value, bool):
            valueStr = "是" if value else "否"
        else:
            valueStr = str(value)
        lines.append(f"{label}: {valueStr}")

    return "\n".join(lines)


class ParamDisplay(QFrame):
    """参数展示框，支持多行显示"""

    def __init__(self, params: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.params = params
        self.setMinimumHeight(60)
        self.setMaximumHeight(150)
        self.setupUi(params)

    def setupUi(self, params: Dict[str, Any]):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        # 遍历参数，创建每一行的显示
        for key, value in params.items():
            if key == "page":
                continue  # 跳过分页参数

            label = PARAM_LABELS.get(key, key)
            itemLayout = QHBoxLayout()
            itemLayout.setSpacing(8)

            # 标签
            labelWidget = BodyLabel(f"{label}：", self)
            labelWidget.setFixedWidth(80)
            labelWidget.setStyleSheet("color: #888;")

            # 值
            if isinstance(value, list):
                valueStr = ", ".join(str(v) for v in value)
            elif isinstance(value, bool):
                valueStr = "是" if value else "否"
            else:
                valueStr = str(value)

            valueWidget = BodyLabel(valueStr, self)
            valueWidget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            valueWidget.setWordWrap(True)

            itemLayout.addWidget(labelWidget)
            itemLayout.addWidget(valueWidget, 1)
            layout.addLayout(itemLayout)

        self.setLayout(layout)


class InfoItem(QFrame):
    """信息展示项：图标 + 标签 + 内容"""

    def __init__(self, iconPath: str, label: str, value: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setupUi(iconPath, label, value)

    def setupUi(self, iconPath: str, label: str, value: str):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(16)

        # 图标
        self.iconLabel = ImageLabel(iconPath, self)
        self.iconLabel.scaledToHeight(32)
        self.iconLabel.scaledToWidth(32)
        self.iconLabel.setFixedSize(32, 32)
        self.iconLabel.setScaledContents(True)

        # 标签
        self.labelWidget = BodyLabel(label, self)
        self.labelWidget.setFixedWidth(100)
        self.labelWidget.setStyleSheet("color: #666;")

        # 内容
        self.valueWidget = BodyLabel(value, self)
        self.valueWidget.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        layout.addWidget(self.iconLabel)
        layout.addWidget(self.labelWidget)
        layout.addWidget(self.valueWidget, 1)  # stretch=1 使内容区域自动扩展

        self.setLayout(layout)

    def updateValue(self, value: str):
        """更新内容值"""
        self.valueWidget.setText(value)


class DownloadApplyWidget(MessageBoxBase):
    def __init__(
        self,
        downloadType: Literal["Hsk", "Global"],
        infoDict: Dict[str, Any],
        parent=None,
    ):
        super().__init__(parent=parent)

        # 保存参数
        self.downloadType = downloadType
        self.infoDict = infoDict

        # 根据下载类型选择图标
        iconName = "Hsk" if downloadType == "Hsk" else "Global"
        # P0-fix:统一使用 :app/icons/ 前缀(项目其它 83 处都使用此形式)。
        # 原 :/app/icons/ 多一个斜杠,在部分 Qt 资源解析路径下不可用。
        iconPath = f":app/icons/{iconName}.svg"

        # 标题区域
        self.titleLabel = SubtitleLabel("申请下载任务", self)
        self.titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 信息卡片容器
        self.cardWidget = CardWidget(self)
        self.cardLayout = QVBoxLayout(self.cardWidget)
        self.cardLayout.setContentsMargins(8, 8, 8, 8)
        self.cardLayout.setSpacing(8)

        # 下载类型信息项
        self.downloadTypeItem = InfoItem(
            iconPath, "下载来源：", downloadType, self.cardWidget
        )

        # 下载参数标签
        self.paramsLabel = BodyLabel("下载参数：", self.cardWidget)
        self.paramsLabel.setStyleSheet("color: #666; font-weight: bold;")

        # 下载参数展示
        self.paramsDisplay = ParamDisplay(
            self.infoDict.get("payload", {}), self.cardWidget
        )

        # 语料数量信息项
        self.numberItem = InfoItem(
            ":app/icons/Number.svg", "语料数量：", "查询中...", self.cardWidget
        )

        # 添加到卡片布局
        self.cardLayout.addWidget(self.downloadTypeItem)
        self.cardLayout.addWidget(self.paramsLabel)
        self.cardLayout.addWidget(self.paramsDisplay)
        self.cardLayout.addWidget(self.numberItem)

        # 添加到视图布局
        self.viewLayout.addWidget(self.titleLabel, 0, Qt.AlignmentFlag.AlignCenter)
        self.viewLayout.addWidget(self.cardWidget, 0, Qt.AlignmentFlag.AlignCenter)

        # 设置按钮文本
        self.yesButton.setText("确认")
        self.yesButton.setEnabled(False)
        self.cancelButton.setText("取消")

        # 设置对话框宽度
        self.widget.setFixedWidth(480)

        # 启动查询线程
        self.worker = None
        self.startQuery()

    def startQuery(self):
        """启动查询线程"""
        if self.worker is None:
            # 根据下载类型选择不同的Worker
            if self.downloadType == "Hsk":
                self.worker = GetTotalWorker(self.infoDict)
            else:
                self.worker = GlobalGetTotalWorker(self.infoDict)
            self.worker.finished.connect(self.onQueryFinished)
            self.worker.failed.connect(self.onQueryFailed)
            self.worker.start()

    def onQueryFinished(self, total: int):
        """查询成功回调"""
        if total > 0:
            self.numberItem.updateValue(f"{total} 条")
            self.yesButton.setEnabled(True)
        else:
            self.numberItem.updateValue("未找到数据")
            self.yesButton.setEnabled(False)
        self.cleanupWorker()

    def onQueryFailed(self, errorMsg: str):
        """查询失败回调"""
        self.numberItem.updateValue("查询失败")
        self.yesButton.setEnabled(False)
        self.cleanupWorker()

    def cleanupWorker(self):
        """清理工作线程

        P0-fix(2026-07-18):清理 worker 报 'bool' object is not callable。
        根因是 GetTotalWorker/GlobalGetTotalWorker 的 __init__ 写了
        self.isRunning = True,这个属性**遮蔽**了 QThread 继承的
        isRunning() 方法,导致 worker.isRunning() 变成对 bool 调用。
        此类 worker 内部的 isRunning 已统一改名为 _isRunning,
        这里也改用 wait(0) 来探测运行状态,避免再次踩坑。
        """
        worker = self.worker
        if worker is not None:
            try:
                # 用 callable() 严格守卫,防止任何属性被遮蔽时崩溃
                stopFn = getattr(worker, "stop", None)
                if callable(stopFn):
                    try:
                        stopFn()
                    except Exception:
                        pass
                # wait(0) 非阻塞检测线程是否还在跑:True=已结束,False=还在跑
                if callable(getattr(worker, "isRunning", None)):
                    try:
                        if worker.isRunning():
                            worker.wait(1000)
                    except Exception:
                        pass
                # 安全地断开所有连接到本对象(self)的信号
                finishedSignal = getattr(worker, "finished", None)
                if finishedSignal is not None:
                    try:
                        finishedSignal.disconnect(self.onQueryFinished)
                    except (RuntimeError, TypeError):
                        pass
                failedSignal = getattr(worker, "failed", None)
                if failedSignal is not None:
                    try:
                        failedSignal.disconnect(self.onQueryFailed)
                    except (RuntimeError, TypeError):
                        pass
                if callable(getattr(worker, "deleteLater", None)):
                    worker.deleteLater()
            except Exception as e:
                logger.warning(f"[DownloadApplyWidget] 清理 worker 异常: {e}")
            self.worker = None

    def closeEvent(self, event):
        """对话框关闭时清理资源"""
        self.cleanupWorker()
        super().closeEvent(event)

    def _toInfoDict(self) -> Dict[str, Any]:
        """构造 taskManager.createTask 所需的 infoDict(PRD-003 批量下载复用)

        Returns:
            {"url": str, "payload": dict} 格式,与现有 _runTask 提交的格式一致。
        """
        return {
            "url": self.infoDict.get("url", ""),
            "payload": dict(self.infoDict.get("payload", {})),
        }
