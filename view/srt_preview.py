# -*- coding: utf-8 -*-
"""字幕预览（第一板块）：展示导入的 SRT，支持分页，避免大量字幕一次渲染卡顿。"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from view.srt_parser import parse_srt, read_srt_file

PAGE_SIZE = 100


class SrtPreviewWidget(QWidget):
    """SRT 预览表格（序号 / 开始 / 结束 / 文本），分页展示。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cues = []
        self._page = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.info_label = QLabel("未加载字幕")
        layout.addWidget(self.info_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["序号", "开始", "结束", "字幕文本"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, stretch=1)

        page_bar = QHBoxLayout()
        self.prev_btn = QPushButton("上一页")
        self.page_label = QLabel("第 0/0 页")
        self.next_btn = QPushButton("下一页")
        self.prev_btn.clicked.connect(self._prev_page)
        self.next_btn.clicked.connect(self._next_page)
        page_bar.addStretch(1)
        page_bar.addWidget(self.prev_btn)
        page_bar.addWidget(self.page_label)
        page_bar.addWidget(self.next_btn)
        page_bar.addStretch(1)
        layout.addLayout(page_bar)

    # ------------------------------------------------------------ 对外
    def load_srt(self, path: str):
        """加载并预览 SRT。返回 (条数, 错误)；出错时条数为 0。"""
        text, err = read_srt_file(path)
        if err is not None:
            self.clear()
            return 0, err
        cues, _ = parse_srt(text)
        self._cues = cues
        self._page = 0
        self._refresh()
        return len(cues), None

    def clear(self):
        self._cues = []
        self._page = 0
        self.table.setRowCount(0)
        self.info_label.setText("未加载字幕")
        self.page_label.setText("第 0/0 页")

    # ------------------------------------------------------------ 分页
    @property
    def _page_count(self) -> int:
        return max(1, math.ceil(len(self._cues) / PAGE_SIZE))

    def _prev_page(self):
        if self._page > 0:
            self._page -= 1
            self._refresh()

    def _next_page(self):
        if self._page < self._page_count - 1:
            self._page += 1
            self._refresh()

    def _refresh(self):
        total = len(self._cues)
        if total == 0:
            self.clear()
            return
        start = self._page * PAGE_SIZE
        end = min(start + PAGE_SIZE, total)
        rows = self._cues[start:end]
        self.table.setRowCount(len(rows))
        for r, cue in enumerate(rows):
            for c, val in enumerate((str(cue.index), cue.start, cue.end, cue.text)):
                item = QTableWidgetItem(val)
                if c in (0, 1, 2):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(r, c, item)
        self.info_label.setText(f"共 {total} 条字幕")
        self.page_label.setText(f"第 {self._page + 1}/{self._page_count} 页")
