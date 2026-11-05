# -*- coding: utf-8 -*-
"""单轨波形绘图组件（第一板块）。

把一条波形轨道在指定时间范围内渲染成专业音频软件风格：
  - 双层：深蓝色块状底 + 亮蓝色尖刺
  - 中间叠加平滑橙色音量包络线
  - 红色选区括号（当前句）+ 红色播放游标竖线

支持点击跳转：左键点击波形 → seekRequested(时间秒)。

波形数据来自成员3 的 ``WaveformTrack``（见 view/waveform_view.py）。
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from view.waveform_view import WaveformTrack

AXIS_H = 16  # 底部时间轴高度


class WaveformPlot(QWidget):
    """单轨波形 + 选区括号 + 播放游标。"""

    seekRequested = Signal(float)  # 左键点击波形 → 发出时间(秒)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._track: Optional[WaveformTrack] = None
        self._envelope: List[float] = []
        self._view_start = 0.0
        self._view_end = 1.0
        self._selection: Optional[Tuple[float, float]] = None  # 秒
        self._playhead: Optional[float] = None                 # 秒

    # ------------------------------------------------------------ 对外
    def set_track(self, track: Optional[WaveformTrack]):
        self._track = track
        self._envelope = self._smooth_envelope(track.peaks) if track and track.peaks else []
        if track is not None and track.duration > 0:
            self._view_start = 0.0
            self._view_end = track.duration
        self.update()

    def set_view(self, start: float, end: float):
        if end <= start:
            end = start + 0.5
        self._view_start = start
        self._view_end = end
        self.update()

    def set_selection(self, start: float, end: float):
        self._selection = (start, end)
        self.update()

    def set_playhead(self, t: float):
        self._playhead = t
        self.update()

    def clear(self):
        self._track = None
        self._envelope = []
        self._selection = None
        self._playhead = None
        self.update()

    # ------------------------------------------------------------ 交互
    def mousePressEvent(self, event):
        if self._track and event.button() == Qt.MouseButton.LeftButton:
            if self.width() <= 0:
                return
            x = event.position().x()
            t = self._view_start + (x / self.width()) * (self._view_end - self._view_start)
            self.seekRequested.emit(t)

    # ------------------------------------------------------------ 坐标
    def _x(self, t: float) -> float:
        span = self._view_end - self._view_start
        if span <= 0:
            return 0.0
        return (t - self._view_start) / span * self.width()

    @staticmethod
    def _smooth_envelope(peaks: List[Tuple[float, float]]) -> List[float]:
        n = len(peaks)
        mag = [(abs(lo) + abs(hi)) / 2.0 for lo, hi in peaks]
        w = max(3, n // 150)
        out = []
        for i in range(n):
            a = max(0, i - w)
            b = min(n, i + w + 1)
            out.append(sum(mag[a:b]) / (b - a))
        return out

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0f1220"))
        if self._track and self._track.peaks:
            self._draw_waveform(p)
        else:
            p.setPen(QColor("#5a6474"))
            p.drawText(10, self.height() // 2, "等待波形数据（成员3 提供）")
        self._draw_selection(p)
        self._draw_playhead(p)
        self._draw_axis(p)

    def _draw_waveform(self, p):
        track = self._track
        W = self.width()
        H = self.height() - AXIS_H
        n = len(track.peaks)
        dur = track.duration if track.duration > 0 else (self._view_end or 1.0)
        span = self._view_end - self._view_start
        if span <= 0 or n == 0:
            return

        mins = [1.0] * W
        maxs = [-1.0] * W
        has = [False] * W
        env_sum = [0.0] * W
        env_cnt = [0] * W
        t0 = self._view_start
        i0 = max(0, int(t0 / dur * n) - 1)
        i1 = min(n, int(self._view_end / dur * n) + 2)
        for i in range(i0, i1):
            lo, hi = track.peaks[i]
            ev = self._envelope[i] if i < len(self._envelope) else 0.0
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
                env_sum[x] += ev
                env_cnt[x] += 1

        mid = H / 2.0
        amp = H * 0.45

        # 1) 深蓝色块状底：填充 min~max 区域
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#16325c"))
        for x in range(W):
            if not has[x]:
                continue
            y_top = mid - maxs[x] * amp
            y_bot = mid - mins[x] * amp
            if y_bot - y_top < 1.0:
                y_bot = y_top + 1.0
            p.drawRect(x, int(y_top), 1, int(y_bot - y_top))

        # 2) 亮蓝色尖刺：上半亮蓝、下半暗蓝
        p.setPen(QPen(QColor("#4fc3f7"), 1))
        for x in range(W):
            if not has[x]:
                continue
            p.drawLine(x, int(mid), x, int(mid - maxs[x] * amp))
        p.setPen(QPen(QColor("#2b6ba8"), 1))
        for x in range(W):
            if not has[x]:
                continue
            p.drawLine(x, int(mid), x, int(mid - mins[x] * amp))

        # 3) 橙色平滑包络线
        p.setPen(QPen(QColor("#ffa726"), 2))
        prev = None
        for x in range(W):
            if not has[x] or env_cnt[x] == 0:
                prev = None
                continue
            y = mid - (env_sum[x] / env_cnt[x]) * amp
            if prev is not None:
                p.drawLine(prev[0], prev[1], x, int(y))
            prev = (x, int(y))

    def _draw_selection(self, p):
        if self._selection is None:
            return
        s, e = self._selection
        x0 = self._x(s)
        x1 = self._x(e)
        rect = QRectF(x0, 1, max(1.0, x1 - x0), self.height() - AXIS_H - 1)
        p.setPen(QPen(QColor("#ff5252"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)

    def _draw_playhead(self, p):
        if self._playhead is None:
            return
        t = self._playhead
        if t < self._view_start or t > self._view_end:
            return
        x = round(self._x(t))
        p.setPen(QPen(QColor("#ff1744"), 2))
        p.drawLine(x, 0, x, self.height() - AXIS_H)

    def _draw_axis(self, p):
        W = self.width()
        H = self.height()
        base = H - AXIS_H
        p.setPen(QPen(QColor("#2a3346"), 1))
        p.drawLine(0, base, W, base)
        span = self._view_end - self._view_start
        if span <= 0:
            return
        step = self._nice_step(span)
        font = QFont()
        font.setPointSize(7)
        p.setFont(font)
        p.setPen(QColor("#8b95a5"))
        t = math.floor(self._view_start / step) * step
        while t <= self._view_end:
            x = round(self._x(t))
            p.drawLine(x, base, x, base + 3)
            p.drawText(x + 2, H - 4, self._fmt(t))
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
    def _fmt(t: float) -> str:
        t = max(0.0, t)
        if t >= 3600:
            return f"{int(t) // 3600}:{int(t) % 3600 // 60:02d}:{int(t) % 60:02d}"
        if t >= 60:
            return f"{int(t) // 60}:{int(t) % 60:02d}"
        return f"{t:.1f}"
