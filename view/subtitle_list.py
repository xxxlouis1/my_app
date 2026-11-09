# -*- coding: utf-8 -*-
"""字幕列表区（第一板块，Aboboo 式第 4 分区）。

扁平化深色列表：列 = 复选框 / 序号 / 原文 / 译文。
- 去掉网格线，干净的深色背景
- 选中行深蓝高亮
- 错误行：原文/译文标红 + 斜体 + ✗，并叠加半透明红色大 X（批改感）

点击某行发出 selected 信号，供上层切换单句波形与错误信息。
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from view.subtitle_model import SubtitleItem

COLUMNS = ["", "序号", "原文", "译文"]


class SubtitleListView(QWidget):
    """字幕列表（复选框/序号/原文/译文），错误行标红斜体 + 大 X。"""

    selected = Signal(object)  # 发出选中的 SubtitleItem

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: List[SubtitleItem] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # 复选框
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # 序号
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)           # 原文
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)           # 译文

        self.table.itemClicked.connect(self._on_clicked)
        layout.addWidget(self.table)

    # ------------------------------------------------------------ 对外
    def set_items(self, items: List[SubtitleItem]):
        self._items = list(items)
        self._refresh()
        if self._items:
            self.table.selectRow(0)

    def _refresh(self):
        self.table.setRowCount(len(self._items))
        err_font = QFont()
        err_font.setItalic(True)
        err_bg = QBrush(QColor(255, 60, 60, 40))

        for r, item in enumerate(self._items):
            err = item.error is not None

            # 复选框
            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable)
            cb.setCheckState(Qt.CheckState.Unchecked)
            cb.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if err:
                cb.setBackground(err_bg)
            self.table.setItem(r, 0, cb)

            # 序号（错误加 ✗ 标红）
            no_text = f"✗ {item.no}" if err else str(item.no)
            no_item = QTableWidgetItem(no_text)
            no_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if err:
                no_item.setForeground(Qt.GlobalColor.red)
                no_item.setFont(err_font)
                no_item.setBackground(err_bg)
            self.table.setItem(r, 1, no_item)

            # 原文 / 译文
            for c, val in ((2, item.text), (3, item.translation)):
                cell = QTableWidgetItem(str(val))
                if err:
                    cell.setForeground(Qt.GlobalColor.red)
                    cell.setFont(err_font)
                    cell.setBackground(err_bg)
                self.table.setItem(r, c, cell)

    def select_item(self, item: Optional[SubtitleItem]):
        """外部指定要选中的字幕（供联动）。"""
        if item is None:
            return
        for r, it in enumerate(self._items):
            if it.no == item.no:
                self.table.selectRow(r)
                return

    def set_hide_original(self, hide: bool):
        """隐藏原文列（第 2 列），只保留译文。"""
        self.table.setColumnHidden(2, hide)

    def _on_clicked(self, cell):
        r = cell.row()
        if 0 <= r < len(self._items):
            self.selected.emit(self._items[r])
