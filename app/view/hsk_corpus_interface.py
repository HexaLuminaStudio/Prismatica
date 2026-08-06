# coding:utf-8
"""
HSK 作文语料检索主页面
========================

作为与 HskInterface(HSK 下载)同级的独立页面,挂在主窗口导航栏。
本文件只做最薄的包装,实际功能由
[app.view.widgets.hsk_corpus.hsk_corpus_browser.HskCorpusBrowser](file:///e:/Prismatica/app/view/widgets/hsk_corpus/hsk_corpus_browser.py) 实现。

设计:
    - 主窗口构造阶段调用 ensureSchema()(确保 db / 索引就绪)
    - 单一职责:只挂 Browser,不做其他副作用
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from app.core.utils import log
from app.view.widgets.hsk_corpus.hsk_corpus_browser import HskCorpusBrowser


class HskCorpusInterface(QWidget):
    """HSK 作文语料检索主页面(与 HSK 下载同级)。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent=parent)
        self.setObjectName("HskCorpusInterface")

        # 浏览器实例(整个页面只有一个)
        self.browser = HskCorpusBrowser(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.browser)

        # 启动期确保 schema / 索引就绪(幂等,极快)
        from app.core.services.hsk_corpus_service import HskCorpusService

        try:
            HskCorpusService.instance().ensureSchema()
        except Exception as e:
            log.warning(f"[HskCorpusInterface] ensureSchema 失败: {e}")
