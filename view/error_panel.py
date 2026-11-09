# -*- coding: utf-8 -*-
"""错误信息面板（第一板块，Aboboo 式第 3 分区）。

展示当前选中字幕对应的错误详情；无错误则显示「无错误」。
错误数据结构来自成员2（见 view/subtitle_model.py）。
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from view.subtitle_model import SubtitleItem


class ErrorPanel(QWidget):
    """错误信息面板：汇总 + 当前字幕的错误详情。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 与字幕列表用不同深度的深蓝色区分
        self.setStyleSheet("QWidget { background-color: #1e1e2e; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.summary_label = QLabel("共 0 条错误 / 0 条字幕")
        self.summary_label.setStyleSheet("color: #8b95a5;")
        layout.addWidget(self.summary_label)

        self.detail_label = QLabel("请选择一条字幕")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextFormat(Qt.TextFormat.RichText)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.detail_label, stretch=1)

    # ------------------------------------------------------------ 对外
    def set_total(self, items: List[SubtitleItem]):
        total = len(items)
        errs = sum(1 for it in items if it.error is not None)
        self.summary_label.setText(f"共 {errs} 条错误 / {total} 条字幕")

    def set_error(self, item: Optional[SubtitleItem]):
        if item is None:
            self.detail_label.setText("<span style='color:#8b95a5'>请选择一条字幕</span>")
            return
        if item.error is None:
            self.detail_label.setText(
                f"<b style='color:#4caf50'>✓ 本条字幕无错误</b><br>"
                f"<span style='color:#8b95a5'>波形编号 {item.no}</span>")
            return
        e = item.error
        html = (
            f"<b style='color:#ff5252; font-size:15px;'>✗ {e.error_type}</b><br><br>"
            f"<span style='color:#dbe2ee'>{e.message}</span><br><br>"
            f"<span style='color:#8b95a5'>字幕编号：{item.no}</span>"
        )
        if e.timestamp_ms:
            html += f"<br><span style='color:#8b95a5'>时间戳：{e.timestamp_ms} ms</span>"
        self.detail_label.setText(html)
