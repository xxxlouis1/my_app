# -*- coding: utf-8 -*-
"""视频播放预览区（第一板块，Aboboo 式下方左侧）。

自上而下：
  - 顶部文字工具栏（视频 ▾ / 隐藏原文 / 单词显隐）
  - 16:9 视频画面（无真实视频流时用深色占位图 + 中央「视频画面」提示）
  - 画面底部内嵌大字幕：亮绿色加粗 + 黑色描边
  - 底部控制栏（播放/暂停 + 进度条）

真实视频流、播放、字幕/原文显隐等由后端（成员2/3）实现；本组件只做
渲染 + 发出控制信号。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMenu,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


class VideoCanvas(QWidget):
    """视频画面 + 内嵌大字幕。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._subtitle = ""
        self.setMinimumHeight(220)

    def set_subtitle(self, text: str):
        self._subtitle = text
        self.update()

    def _video_rect(self) -> QRectF:
        w = self.width()
        h = self.height()
        target_h = w * 9.0 / 16.0
        if target_h <= h:
            vw, vh = w, target_h
        else:
            vw, vh = h * 16.0 / 9.0, h
        return QRectF((w - vw) / 2.0, (h - vh) / 2.0, vw, vh)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#0d0d0d"))

        r = self._video_rect()
        p.fillRect(r, QColor("#141414"))
        p.setPen(QColor("#2a2a2a"))
        p.drawRect(r)

        p.setPen(QColor("#555555"))
        f = QFont()
        f.setPointSize(14)
        p.setFont(f)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "视频画面")

        if self._subtitle:
            self._draw_subtitle(p, r)

    def _draw_subtitle(self, p: QPainter, r: QRectF):
        text = self._subtitle
        font = QFont()
        size = 20
        while size > 10:
            f = QFont()
            f.setPointSize(size)
            f.setBold(True)
            fm = QFontMetrics(f)
            if fm.horizontalAdvance(text) <= r.width() - 20:
                font = f
                break
            size -= 1

        fm = QFontMetrics(font)
        x = r.center().x() - fm.horizontalAdvance(text) / 2.0
        y = r.bottom() - 18.0

        path = QPainterPath()
        path.addText(x, y, font, text)
        p.setPen(QPen(QColor("#000000"), 6, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)                      # 黑色描边
        p.fillPath(path, QColor("#00e676"))   # 亮绿色填充


class VideoPanel(QWidget):
    """视频预览区（工具栏 + 画面 + 控制栏）。"""

    playPauseRequested = Signal()
    seekRequested = Signal(float)  # 0..1 进度
    hideOriginalToggled = Signal(bool)
    wordToggleToggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QWidget { background-color: #0d0d0d; color: #dbe2ee; }")
        self._subtitle = ""
        self._translation = ""
        self._hide_original = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # 顶部文字工具栏（可点击）
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        btn_qss = ("QPushButton { color: #9fb2c8; background: transparent; "
                   "border: none; padding: 2px 6px; } "
                   "QPushButton:hover { color: #dbe2ee; } "
                   "QPushButton:checked { color: #4fc3f7; }")

        self.video_btn = QPushButton("视频 ▾")
        self.video_btn.setStyleSheet(btn_qss)
        self.video_menu = QMenu(self)
        self.video_menu.addAction("（暂无视频源，待成员3接入）").setEnabled(False)
        self.video_btn.setMenu(self.video_menu)

        self.hide_btn = QPushButton("隐藏原文")
        self.hide_btn.setCheckable(True)
        self.hide_btn.setStyleSheet(btn_qss)
        self.hide_btn.toggled.connect(self.hideOriginalToggled.emit)

        self.word_btn = QPushButton("单词显隐")
        self.word_btn.setCheckable(True)
        self.word_btn.setStyleSheet(btn_qss)
        self.word_btn.toggled.connect(self.wordToggleToggled.emit)

        toolbar.addWidget(self.video_btn)
        toolbar.addWidget(self.hide_btn)
        toolbar.addWidget(self.word_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        # 画面 + 大字幕
        self.canvas = VideoCanvas()
        layout.addWidget(self.canvas, 1)

        # 底部控制栏
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        self.play_btn = QPushButton("▶ 播放")
        self.play_btn.setStyleSheet(
            "QPushButton { background: #1e2a3a; color: #dbe2ee; padding: 4px 14px; "
            "border: none; border-radius: 3px; } "
            "QPushButton:hover { background: #2a3a50; }")
        self.play_btn.clicked.connect(self.playPauseRequested.emit)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.valueChanged.connect(lambda v: self.seekRequested.emit(v / 1000.0))
        ctrl.addWidget(self.play_btn)
        ctrl.addWidget(self.slider, 1)
        layout.addLayout(ctrl)

    # ------------------------------------------------------------ 对外
    def set_subtitle(self, text: str, translation: str = ""):
        self._subtitle = text
        self._translation = translation
        self._update_canvas()

    def set_hide_original(self, hide: bool):
        """隐藏原文 → 画面大字幕改显示译文。"""
        self._hide_original = hide
        self.hide_btn.setChecked(hide)
        self.hide_btn.setText("显示原文" if hide else "隐藏原文")
        self._update_canvas()

    def _update_canvas(self):
        shown = (self._translation if (self._hide_original and self._translation)
                 else self._subtitle)
        self.canvas.set_subtitle(shown)

    def set_position(self, frac: float):
        self.slider.blockSignals(True)
        self.slider.setValue(int(max(0.0, min(1.0, frac)) * 1000))
        self.slider.blockSignals(False)

    def set_playing(self, playing: bool):
        self.play_btn.setText("⏸ 暂停" if playing else "▶ 播放")
