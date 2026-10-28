# -*- coding: utf-8 -*-
"""波形对比编辑画布（第一板块 GUI）。

双轨展示「原始波形 / 处理后波形」，在时间轴上绘制字幕断点，支持
缩放、平移、拖拽断点修改字幕时间。

数据接口（成员 3/4 按此提供数据）
--------------------------------
- ``WaveformTrack``：一条波形轨道。``peaks`` 为等宽时间 bucket 的
  ``(min, max)`` 幅度列表，归一化到 [-1, 1]；两条轨道 bucket 数量与
  duration 一致，才能对齐渲染。
- 断点直接复用 ``view.srt_parser.SrtCue``（``start_ms``/``end_ms`` 是
  时间真相；拖拽后这两个字段与 ``start``/``end`` 字符串一起更新）。

本模块只做「渲染 + 交互」，不解析音频、不生成波形采样、不做时间
合法性校验（重叠/负数由成员 2 负责）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from view.srt_parser import SrtCue, ms_to_ts

# 断点拖动时的最小间隔（毫秒），避免 start 拖到 end 之后
MIN_GAP_MS = 10

# 断点命中/拖拽热区半径（像素）
HANDLE_PX = 6

# 顶部时间轴高度、底部字幕条高度（像素）
AXIS_PX = 22
CUE_LANE_PX = 30


@dataclass
class WaveformTrack:
    """一条波形轨道的数据契约（由后端降采样后交给 GUI 渲染）。

    参数
    ----
    label: 显示名，如 "原始波形" / "处理后波形"
    peaks: 等宽时间 bucket 的 ``(min, max)`` 幅度列表，每个值归一化到 [-1, 1]。
           两条轨道必须 bucket 数量一致、duration 一致才能对齐。
    duration: 媒体总时长（秒）
    sample_rate: 可选，仅用于展示，不参与绘制
    """
    label: str
    peaks: List[Tuple[float, float]]
    duration: float
    sample_rate: float = 0.0


class WaveformCanvas(QWidget):
    """双轨波形画布：绘制波形、字幕断点，支持缩放/平移/断点拖拽。"""

    cueChanged = Signal()  # 断点被拖动（释放鼠标）后发出，外部刷新字幕表格

    def __init__(self, parent=None):
        super().__init__(parent)
        self._original: Optional[WaveformTrack] = None
        self._processed: Optional[WaveformTrack] = None
        self._cues: List[SrtCue] = []
        self._duration = 0.0
        self._view_start = 0.0
        self._view_end = 1.0
        self._drag: Optional[dict] = None
        self.setMouseTracking(True)

    # ------------------------------------------------------------ 对外数据
    def set_tracks(self, original: Optional[WaveformTrack],
                   processed: Optional[WaveformTrack] = None):
        """设置两条波形轨道（后端产出采样数据后调用）。"""
        self._original = original
        self._processed = processed
        self._duration = self._calc_duration()
        self.fit()

    def set_cues(self, cues: List[SrtCue]):
        """设置字幕断点（复用 SrtCue；会被拖拽就地修改）。"""
        self._cues = list(cues)
        self._duration = self._calc_duration()
        if self._view_end <= self._view_start:
            self.fit()
        else:
            self._view_end = min(self._view_end, max(self._duration, 0.05))
            self.update()

    def cues(self) -> List[SrtCue]:
        """返回当前字幕列表（含拖拽修改后的时间）。"""
        return self._cues

    def clear(self):
        self._original = None
        self._processed = None
        self._cues = []
        self._duration = 0.0
        self._view_start = 0.0
        self._view_end = 1.0
        self.update()

    # ------------------------------------------------------------ 视野
    def fit(self):
        self._view_start = 0.0
        self._view_end = max(self._duration, 1.0)
        self.update()

    def zoom_in(self):
        self.zoom_at(1.25, self.width() / 2.0)

    def zoom_out(self):
        self.zoom_at(0.8, self.width() / 2.0)

    def zoom_at(self, factor: float, anchor_x: float):
        span = self._view_end - self._view_start
        if span <= 0:
            return
        t = self._x_to_time(anchor_x)
        new_span = span / factor
        new_span = max(0.05, min(new_span, max(self._duration * 2, 1.0)))
        ratio = (t - self._view_start) / span
        self._view_start = t - ratio * new_span
        self._view_end = self._view_start + new_span
        self._clamp_view()
        self.update()

    def _calc_duration(self) -> float:
        d = 0.0
        if self._original:
            d = max(d, self._original.duration)
        if self._processed:
            d = max(d, self._processed.duration)
        if self._cues:
            d = max(d, max((c.end_ms for c in self._cues), default=0) / 1000.0)
        return d

    def _clamp_view(self):
        span = self._view_end - self._view_start
        if self._duration <= 0:
            self._view_start = 0.0
            self._view_end = max(span, 1.0)
            return
        if span >= self._duration:
            self._view_start = 0.0
            self._view_end = max(self._duration, span)
        else:
            self._view_start = max(0.0, min(self._view_start, self._duration - span))
            self._view_end = self._view_start + span

    # ------------------------------------------------------------ 坐标映射
    def _time_to_x(self, t: float) -> float:
        span = self._view_end - self._view_start
        if span <= 0:
            return 0.0
        return (t - self._view_start) / span * self.width()

    def _x_to_time(self, x: float) -> float:
        if self.width() <= 0:
            return 0.0
        return self._view_start + (x / self.width()) * (self._view_end - self._view_start)

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1b1e23"))

        W = self.width()
        H = self.height()
        cue_h = CUE_LANE_PX if self._cues else 0
        wave_h = H - AXIS_PX - cue_h
        if wave_h < 10:
            return

        # 两轨各占一半
        has_both = self._original is not None and self._processed is not None
        if has_both:
            lane_h = wave_h / 2.0
            lanes = [
                (self._original, AXIS_PX, lane_h, QColor("#4caf50")),
                (self._processed, AXIS_PX + lane_h, lane_h, QColor("#ffa726")),
            ]
        else:
            track = self._original or self._processed
            lanes = [(track, AXIS_PX, wave_h, QColor("#4caf50"))] if track else []

        for track, top, h, color in lanes:
            self._draw_track(p, track, top, h, color)

        # 无波形时提示
        if not lanes:
            p.setPen(QColor("#7f8791"))
            p.drawText(10, AXIS_PX + 20, "未加载波形（等待后端成员3/4 提供采样数据）")

        self._draw_breakpoints(p, AXIS_PX, wave_h)
        if self._cues:
            self._draw_cue_lane(p, H - cue_h, cue_h)
        self._draw_axis(p, AXIS_PX)

    def _draw_track(self, p, track, top, height, color):
        if not track or not track.peaks:
            return
        W = self.width()
        n = len(track.peaks)
        dur = track.duration if track.duration > 0 else (self._duration or 1.0)
        span = self._view_end - self._view_start
        if span <= 0 or n == 0:
            return

        # 每像素列聚合 min/max，避免过采样/欠采样造成的闪烁
        mins = [1.0] * W
        maxs = [-1.0] * W
        has = [False] * W
        t0 = self._view_start
        i0 = max(0, int(t0 / dur * n) - 1)
        i1 = min(n, int(self._view_end / dur * n) + 2)
        for i in range(i0, i1):
            lo, hi = track.peaks[i]
            xa = int((i / n * dur - t0) / span * W)
            xb = int(((i + 1) / n * dur - t0) / span * W)
            if xb <= xa:
                xb = xa + 1
            if xa < 0:
                xa = 0
            if xb > W:
                xb = W
            for x in range(xa, xb):
                if not has[x]:
                    mins[x] = lo
                    maxs[x] = hi
                    has[x] = True
                else:
                    if lo < mins[x]:
                        mins[x] = lo
                    if hi > maxs[x]:
                        maxs[x] = hi

        mid = top + height / 2.0
        amp_px = height * 0.45

        # 零线
        p.setPen(QPen(QColor("#3a3f45"), 1))
        p.drawLine(0, int(mid), W, int(mid))

        # 波形
        p.setPen(QPen(color, 1))
        for x in range(W):
            if not has[x]:
                continue
            y0 = mid - maxs[x] * amp_px
            y1 = mid - mins[x] * amp_px
            if y1 - y0 < 1.0:
                y1 = y0 + 1.0
            p.drawLine(x, int(y0), x, int(y1))

        # 轨道名
        font = QFont()
        font.setPointSize(8)
        p.setFont(font)
        p.setPen(QColor("#ffffff"))
        p.drawText(6, int(top + 13), track.label)

    def _draw_breakpoints(self, p, top, wave_h):
        if not self._cues:
            return
        H = self.height()
        bottom = H - (CUE_LANE_PX if self._cues else 0)
        for cue in self._cues:
            sx = round(self._time_to_x(cue.start_ms / 1000.0))
            ex = round(self._time_to_x(cue.end_ms / 1000.0))
            for x, side in ((sx, "start"), (ex, "end")):
                color = QColor("#66bb6a") if side == "start" else QColor("#ef5350")
                p.setPen(QPen(color, 1))
                p.drawLine(x, top, x, bottom)
                tri = QPolygonF([QPointF(x, top), QPointF(x - 5, top + 7),
                                 QPointF(x + 5, top + 7)])
                p.setBrush(QBrush(color))
                p.setPen(QPen(color.darker(130), 1))
                p.drawPolygon(tri)

    def _draw_cue_lane(self, p, top, height):
        W = self.width()
        for i, cue in enumerate(self._cues):
            sx = round(self._time_to_x(cue.start_ms / 1000.0))
            ex = round(self._time_to_x(cue.end_ms / 1000.0))
            if ex < 0 or sx > W:
                continue
            color = QColor("#42a5f5") if i % 2 == 0 else QColor("#7e57c2")
            color.setAlpha(150)
            p.setBrush(QBrush(color))
            p.setPen(QPen(QColor("#d0d4d9"), 1))
            p.drawRoundedRect(QRectF(sx, top + 4, max(1, ex - sx), height - 8), 3, 3)

    def _draw_axis(self, p, height):
        W = self.width()
        p.setPen(QPen(QColor("#3a3f45"), 1))
        p.drawLine(0, height - 1, W, height - 1)
        span = self._view_end - self._view_start
        if span <= 0:
            return
        step = self._nice_step(span)
        font = QFont()
        font.setPointSize(8)
        p.setFont(font)
        p.setPen(QColor("#9aa0a6"))
        t = math.floor(self._view_start / step) * step
        while t <= self._view_end:
            x = round(self._time_to_x(t))
            p.drawLine(x, height - 4, x, height - 1)
            p.drawText(x + 3, height - 6, self._fmt_tick(t))
            t += step

    @staticmethod
    def _nice_step(span: float) -> float:
        target = span / 8.0
        for s in (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5,
                  1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0, 300.0,
                  600.0, 1200.0, 1800.0, 3600.0):
            if s >= target:
                return s
        return 3600.0

    @staticmethod
    def _fmt_tick(t: float) -> str:
        t = max(0.0, t)
        if t >= 3600:
            return f"{int(t) // 3600}:{int(t) % 3600 // 60:02d}:{int(t) % 60:02d}"
        if t >= 60:
            return f"{int(t) // 60}:{int(t) % 60:02d}"
        return f"{t:.1f}"

    # ------------------------------------------------------------ 交互
    def _hit_breakpoint(self, pos) -> Optional[Tuple[int, str]]:
        if not self._cues:
            return None
        cue_top = self.height() - CUE_LANE_PX
        best = None
        best_d = HANDLE_PX + 1.0
        for i, cue in enumerate(self._cues):
            sx = self._time_to_x(cue.start_ms / 1000.0)
            ex = self._time_to_x(cue.end_ms / 1000.0)
            in_cue = pos.y() >= cue_top
            in_wave = AXIS_PX <= pos.y() < cue_top
            if not (in_cue or in_wave):
                continue
            for cx, side in ((sx, "start"), (ex, "end")):
                d = abs(pos.x() - cx)
                if d <= HANDLE_PX and d < best_d:
                    best_d = d
                    best = (i, side)
        return best

    def _apply_breakpoint(self, idx: int, side: str, t: float):
        cue = self._cues[idx]
        ms = max(0, int(round(t * 1000)))
        if side == "start":
            ms = min(ms, cue.end_ms - MIN_GAP_MS)
            cue.start_ms = ms
            cue.start = ms_to_ts(ms)
        else:
            ms = max(ms, cue.start_ms + MIN_GAP_MS)
            cue.end_ms = ms
            cue.end = ms_to_ts(ms)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            hit = self._hit_breakpoint(e.position())
            if hit is not None:
                self._drag = {"kind": "breakpoint", "cue": hit[0], "side": hit[1]}
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self._drag = {"kind": "pan", "last_x": e.position().x()}
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is None:
            hit = self._hit_breakpoint(e.position())
            self.setCursor(Qt.CursorShape.SizeHorCursor if hit else Qt.CursorShape.ArrowCursor)
            return
        if self._drag["kind"] == "pan":
            dx = e.position().x() - self._drag["last_x"]
            self._drag["last_x"] = e.position().x()
            span = self._view_end - self._view_start
            if span > 0 and self.width() > 0:
                dt = -dx / self.width() * span
                self._view_start += dt
                self._view_end += dt
                self._clamp_view()
                self.update()
        elif self._drag["kind"] == "breakpoint":
            t = self._x_to_time(e.position().x())
            self._apply_breakpoint(self._drag["cue"], self._drag["side"], t)
            self.update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag is not None and self._drag["kind"] == "breakpoint":
            self.cueChanged.emit()
        self._drag = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        factor = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.zoom_at(factor, e.position().x())
