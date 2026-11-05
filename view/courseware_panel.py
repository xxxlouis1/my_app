# -*- coding: utf-8 -*-
"""图文教材/课件展示面板（第一板块，Aboboo 式下方左侧）。

白色背景与暗色主题形成对比。自上而下：
  - 绿色背景板块标签（如 "SECTION B"）
  - 两张并排风景配图
  - 浅黄色英文日记文本框
  - 底部大字幕：当前选中句（亮绿色加粗）

教材内容、配图、翻译等数据后续由后端（成员2）提供；这里先用占位内容
渲染出布局。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

DIARY = (
    "My trip to Moscow — Day 1\n"
    "It was a cloudy day. I didn't feel like eating because I felt sick, "
    "but we still visited Red Square in the morning. The fountain looked "
    "beautiful in the sun."
)


def _make_landscape(text: str, w: int = 360, h: int = 200) -> QPixmap:
    """生成一张简单的占位风景图（后续换成员2 提供的真实配图）。"""
    pm = QPixmap(w, h)
    pm.fill(QColor("#ffffff"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    sky = QLinearGradient(0, 0, 0, h)
    sky.setColorAt(0.0, QColor("#8ecdf0"))
    sky.setColorAt(0.7, QColor("#d9f0fb"))
    p.fillRect(0, 0, w, h, sky)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ffd54f"))
    p.drawEllipse(int(w * 0.72), int(h * 0.10), 44, 44)

    p.setBrush(QColor("#8fd694"))
    p1 = QPainterPath()
    p1.moveTo(0, h)
    p1.quadTo(w * 0.25, h * 0.55, w * 0.55, h * 0.72)
    p1.lineTo(w, h * 0.72)
    p1.lineTo(w, h)
    p1.lineTo(0, h)
    p1.closeSubpath()
    p.drawPath(p1)

    p.setBrush(QColor("#43a047"))
    p2 = QPainterPath()
    p2.moveTo(0, h)
    p2.quadTo(w * 0.45, h * 0.45, w, h * 0.62)
    p2.lineTo(w, h)
    p2.lineTo(0, h)
    p2.closeSubpath()
    p.drawPath(p2)

    p.setPen(QColor("#1b5e20"))
    font = p.font()
    font.setPointSize(11)
    font.setBold(True)
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter, text)
    p.end()
    return pm


class CoursewarePanel(QWidget):
    """图文教材展示面板（白底）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 覆盖全局暗色主题：本面板及其子控件全部白底深色字
        self.setStyleSheet("QWidget { background-color: #ffffff; color: #222222; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # 绿色板块标签
        self.section_label = QLabel("SECTION B")
        self.section_label.setStyleSheet(
            "background-color: #2e7d32; color: #ffffff; font-weight: bold; padding: 4px 10px;")
        layout.addWidget(self.section_label)

        # 两张并排风景配图
        img_row = QHBoxLayout()
        img_row.setSpacing(8)
        self.img1 = QLabel()
        self.img1.setPixmap(_make_landscape("配图 1"))
        self.img1.setScaledContents(True)
        self.img1.setFixedHeight(160)
        self.img2 = QLabel()
        self.img2.setPixmap(_make_landscape("配图 2"))
        self.img2.setScaledContents(True)
        self.img2.setFixedHeight(160)
        img_row.addWidget(self.img1)
        img_row.addWidget(self.img2)
        layout.addLayout(img_row)

        # 浅黄色英文日记文本框
        self.diary_label = QLabel(DIARY)
        self.diary_label.setWordWrap(True)
        self.diary_label.setStyleSheet(
            "background-color: #fff8dc; color: #3a3a3a; border: 1px solid #e0c95a; "
            "border-radius: 4px; padding: 10px;")
        layout.addWidget(self.diary_label)

        # 底部大字幕：当前选中句
        self.sentence_label = QLabel("")
        self.sentence_label.setWordWrap(True)
        self.sentence_label.setStyleSheet(
            "color: #00c853; font-size: 26px; font-weight: bold;")
        layout.addWidget(self.sentence_label)

    # ------------------------------------------------------------ 对外
    def set_sentence(self, text: str):
        """更新底部大字幕（当前选中句）。"""
        self.sentence_label.setText(text)
