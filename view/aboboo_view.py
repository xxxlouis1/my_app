# -*- coding: utf-8 -*-
"""Aboboo 式分区界面（第一板块）。

布局：
  上（~35%）：总波形区 → 红色联动括号 → 单句波形区
  下（~65%）：视频预览区 | (字幕列表 + 错误信息)

联动：
  - 点击字幕列表某行 → 单句波形放大到该句、总波形红框+括号移到对应位置、
    错误信息刷新、视频区大字幕更新
  - 点击总波形/单句波形某处 → 跳转到该时间点所在字幕
  - 播放进度 → 红色游标、视频进度条联动

数据接口：
  - 波形：成员3 的 ``WaveformTrack``（set_waveform）
  - 字幕+错误：成员2 的 ``SubtitleItem`` 列表（set_subtitles）
  - 播放位置：成员2/3 的播放器通过 set_playhead(秒) 驱动

本视图只做「布局 + 渲染 + 点击联动」，不解析 SRT、不生成波形、不做
错误识别。
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from view.error_panel import ErrorPanel
from view.linkage_brace import LinkageBrace
from view.subtitle_list import SubtitleListView
from view.subtitle_model import SubtitleItem
from view.video_panel import VideoPanel
from view.waveform_plot import WaveformPlot
from view.waveform_view import WaveformTrack


class AbobooView(QWidget):
    """Aboboo 式分区主视图。"""

    seekRequested = Signal(float)  # 点击波形请求跳转播放位置（秒），供成员3播放器 seek

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: List[SubtitleItem] = []
        self._duration = 0.0
        self._playing_no = None  # 当前播放游标落在哪一句（用于换句时高亮）

        # ---- 上：总波形 + 联动括号 + 单句波形 ----
        self.full_plot = WaveformPlot()
        self.sentence_plot = WaveformPlot()
        self.brace = LinkageBrace()

        top = QSplitter(Qt.Orientation.Vertical)
        top.addWidget(self.full_plot)
        top.addWidget(self.brace)
        top.addWidget(self.sentence_plot)
        top.setSizes([170, 14, 150])

        # ---- 下：视频预览 | (字幕列表 + 错误信息) ----
        self.video_panel = VideoPanel()
        self.error_panel = ErrorPanel()
        self.subtitle_list = SubtitleListView()

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.subtitle_list)
        right.addWidget(self.error_panel)
        right.setSizes([540, 220])

        bottom = QSplitter(Qt.Orientation.Horizontal)
        bottom.addWidget(self.video_panel)
        bottom.addWidget(right)
        bottom.setSizes([640, 760])

        outer = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(top)
        outer.addWidget(bottom)
        outer.setSizes([300, 720])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(outer)

        # 联动
        self.subtitle_list.selected.connect(self._on_select)
        self.full_plot.seekRequested.connect(self._on_seek)
        self.sentence_plot.seekRequested.connect(self._on_seek)
        self.video_panel.hideOriginalToggled.connect(self._on_hide_original)

    # ------------------------------------------------------------ 对外
    def set_waveform(self, track: Optional[WaveformTrack]):
        """成员3 产出完整波形数据后调用。"""
        self._duration = track.duration if track else 0.0
        self.full_plot.set_track(track)

    def set_subtitles(self, items: List[SubtitleItem]):
        """成员2 产出字幕条目（含错误标注）后调用。"""
        self._items = list(items)
        self.subtitle_list.set_items(self._items)
        self.error_panel.set_total(self._items)
        if self._items:
            self._select_item(self._items[0])

    def set_playhead(self, t_sec: float):
        """成员2/3 的播放器回调当前播放位置（秒）。"""
        self.full_plot.set_playhead(t_sec)
        self.sentence_plot.set_playhead(t_sec)
        if self._duration > 0:
            self.video_panel.set_position(t_sec / self._duration)

        # 游标走到哪句，就自动高亮/显示该句字幕（只在换句时刷新一次）
        t_ms = t_sec * 1000.0
        for item in self._items:
            if item.start_ms <= t_ms <= item.end_ms:
                if item.no != self._playing_no:
                    self._playing_no = item.no
                    self._select_item(item)
                break

    def clear(self):
        self._items = []
        self._duration = 0.0
        self._playing_no = None
        self.subtitle_list.set_items([])
        self.error_panel.set_total([])
        self.full_plot.clear()
        self.sentence_plot.clear()
        self.brace.set_span(0.0, 0.0)
        self.video_panel.set_subtitle("")

    # ------------------------------------------------------------ 联动
    def _on_select(self, item: SubtitleItem):
        self._select_item(item)

    def _on_seek(self, t_sec: float):
        """点击波形 → 跳转播放位置 + 高亮对应字幕。"""
        self.seekRequested.emit(t_sec)  # 通知播放器（成员3）seek 到该秒
        self.set_playhead(t_sec)        # 移动游标/视频进度条，并自动高亮该句字幕

    def _on_hide_original(self, hide: bool):
        """隐藏原文：字幕列表隐藏原文列，视频区大字幕改显示译文。"""
        self.subtitle_list.set_hide_original(hide)
        self.video_panel.set_hide_original(hide)

    def _select_item(self, item: SubtitleItem):
        start_s = item.start_ms / 1000.0
        end_s = item.end_ms / 1000.0
        pad = max(0.3, (end_s - start_s) * 0.3)
        self.sentence_plot.set_view(start_s - pad, end_s + pad)
        self.full_plot.set_selection(start_s, end_s)
        if self._duration > 0:
            self.brace.set_span(start_s / self._duration, end_s / self._duration)
        self.error_panel.set_error(item)
        self.video_panel.set_subtitle(item.text, item.translation)
        self.subtitle_list.select_item(item)
