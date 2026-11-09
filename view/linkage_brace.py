# -*- coding: utf-8 -*-
"""波形联动指示条（第一板块）。

放在「总波形」与「单句波形」之间，用柔和的红色曲线（大括号形）从总
波形的高亮红框引出，指向下方单句波形，形成「局部放大」的联动感。
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class LinkageBrace(QWidget):
    """红色柔和联动括号：上端对齐选区，下端展开到整条单句波形。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._start = 0.0  # 选区在总波形中的位置（0~1）
        self._end = 0.1
        self.setFixedHeight(18)

    def set_span(self, start_frac: float, end_frac: float):
        self._start = max(0.0, min(1.0, start_frac))
        self._end = max(self._start, min(1.0, end_frac))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#0f1220"))  # 与波形区同底色

        W = self.width()
        H = self.height()
        x0 = self._start * W
        x1 = self._end * W
        if x1 - x0 < 2.0:
            x1 = x0 + 2.0

        # 柔和曲线漏斗：上端窄（选区）→ 下端展开到整条单句波形
        path = QPainterPath()
        path.moveTo(x0, 0.0)
        path.cubicTo(x0, H * 0.35, 0.0, H * 0.6, 0.0, H)
        path.lineTo(W, H)
        path.cubicTo(W, H * 0.6, x1, H * 0.35, x1, 0.0)
        path.closeSubpath()
        p.setPen(QPen(QColor(255, 82, 82, 150), 1))
        p.setBrush(QColor(255, 82, 82, 30))
        p.drawPath(path)

        # 两条竖线强调与上方红框对齐
        p.setPen(QPen(QColor(255, 82, 82, 180), 2))
        p.drawLine(int(x0), 0, int(x0), int(H * 0.4))
        p.drawLine(int(x1), 0, int(x1), int(H * 0.4))
