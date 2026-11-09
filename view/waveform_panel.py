# -*- coding: utf-8 -*-
"""波形编辑面板（第一板块）。

把波形画布 + 缩放/保存按钮组织成一个可嵌入主窗口的面板。
「保存 SRT」只负责把修改后的断点数据交出去（发 ``saveRequested`` 信号），
SRT 的组装与另存由成员 2 实现。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from view.waveform_view import WaveformCanvas


class WaveformPanel(QWidget):
    """波形编辑页：工具栏 + 双轨画布 + 操作提示。"""

    saveRequested = Signal(list)  # 携带修改后的字幕列表（SrtCue）

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        self.btn_fit = QPushButton("适应全览")
        self.btn_zoom_in = QPushButton("放大")
        self.btn_zoom_out = QPushButton("缩小")
        self.btn_save = QPushButton("保存 SRT")
        self.status_label = QLabel("未加载波形")

        self.btn_fit.clicked.connect(self._fit)
        self.btn_zoom_in.clicked.connect(self._zoom_in)
        self.btn_zoom_out.clicked.connect(self._zoom_out)
        self.btn_save.clicked.connect(self._save)

        bar.addWidget(self.btn_fit)
        bar.addWidget(self.btn_zoom_in)
        bar.addWidget(self.btn_zoom_out)
        bar.addStretch(1)
        bar.addWidget(self.status_label)
        bar.addWidget(self.btn_save)
        layout.addLayout(bar)

        self.canvas = WaveformCanvas()
        self.canvas.setMinimumHeight(260)
        self.canvas.cueChanged.connect(self._on_cue_changed)
        layout.addWidget(self.canvas, stretch=1)

        hint = QLabel("滚轮缩放 · 拖动空白处平移 · 拖动断点（绿=开始 / 红=结束）修改字幕时间")
        hint.setStyleSheet("color: #808080;")
        layout.addWidget(hint)

    # ------------------------------------------------------------ 对外
    def set_tracks(self, original, processed=None):
        """后端(成员3/4) 产出波形采样数据后调用。"""
        self.canvas.set_tracks(original, processed)
        self.status_label.setText("波形已加载")

    def set_cues(self, cues):
        self.canvas.set_cues(cues)

    def cues(self):
        return self.canvas.cues()

    def clear(self):
        self.canvas.clear()
        self.status_label.setText("未加载波形")

    # ------------------------------------------------------------ 槽
    def _fit(self):
        self.canvas.fit()

    def _zoom_in(self):
        self.canvas.zoom_in()

    def _zoom_out(self):
        self.canvas.zoom_out()

    def _save(self):
        cues = self.canvas.cues()
        if not cues:
            QMessageBox.warning(self, "提示", "请先在「字幕校正」页加载 SRT 字幕。")
            return
        self.saveRequested.emit(cues)

    def _on_cue_changed(self):
        self.status_label.setText(f"字幕 {len(self.canvas.cues())} 条（已修改，待保存）")
