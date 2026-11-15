"""
双轨波形视图组件

功能：
1. 双轨展示：原始波形（上轨）+ 处理后波形（下轨）
2. 拖动平移、滚轮缩放
3. 字幕断点标记（起始/结束竖线 + 区间填充）
4. 断点拖拽修改时间戳
5. 保存修改后字幕为 SRT

坐标体系统一：以时间戳（秒）为唯一口径。
可视范围由 view_start / view_duration 控制。
像素 x = (time - view_start) / view_duration * track_width
"""

import os
from enum import Enum
from typing import List, Optional, Tuple

from PyQt5.QtCore import QPoint, QRect, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush, QFont, QPolygonF
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QMessageBox, QSizePolicy,
)

from .subtitle import Subtitle
from .srt_exporter import SRTExporter


class DragMode(Enum):
    """鼠标交互状态"""
    NONE = "none"
    PAN = "pan"               # 拖动平移
    DRAG_START = "drag_start"  # 拖拽字幕起始断点
    DRAG_END = "drag_end"      # 拖拽字幕结束断点


# 颜色配置
COLOR_BG = QColor(20, 22, 30)
COLOR_GRID = QColor(60, 65, 80)
COLOR_WAVE_ORIG = QColor(100, 180, 255)    # 原始波形 - 蓝色
COLOR_WAVE_PROC = QColor(120, 255, 150)    # 处理后波形 - 绿色
COLOR_BREAKPOINT = QColor(255, 100, 100)   # 断点 - 红色
COLOR_REGION = QColor(255, 100, 100, 40)   # 字幕区间填充 - 半透明红
COLOR_AXIS = QColor(180, 180, 180)
COLOR_TEXT = QColor(220, 220, 220)
COLOR_SELECTED = QColor(255, 215, 0)        # 选中字幕 - 金色

BREAKPOINT_HIT_TOLERANCE = 8  # 断点命中容差（像素）


class WaveformTrack(QWidget):
    """
    单条波形轨道。

    负责绘制一条波形 + 该轨道上的字幕断点标记，并处理鼠标交互。
    双轨视图中上下两条轨道共享同一时间轴（view_start / view_duration）。
    """

    # 字幕时间被修改时发射：(subtitle_index, new_start, new_end)
    subtitle_changed = pyqtSignal(int, float, float)
    # 可视范围变化时发射（用于双轨联动）
    view_changed = pyqtSignal(float, float)  # (view_start, view_duration)

    def __init__(
        self,
        samples: Optional[List[float]] = None,
        duration: float = 0.0,
        wave_color: QColor = COLOR_WAVE_ORIG,
        label: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._samples = samples or []
        self._duration = duration
        self._wave_color = wave_color
        self._label = label

        # 字幕列表（Subtitle 对象）
        self._subtitles: List[Subtitle] = []
        # 当前选中的字幕序号（从 1 开始），0 表示无选中
        self._selected_index: int = 0

        # 可视时间范围
        self._view_start = 0.0
        self._view_duration = duration if duration > 0 else 1.0

        # 交互状态
        self._drag_mode = DragMode.NONE
        self._drag_subtitle_index = 0  # 正在拖拽的字幕序号
        self._last_mouse_pos = QPoint()
        self._pan_last_x = 0

        # 轨道内边距（左侧留时间轴标签空间）
        self._pad_left = 0
        self._pad_right = 0
        self._pad_top = 20
        self._pad_bottom = 20

        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    # 数据设置
    # ------------------------------------------------------------------
    def set_samples(self, samples: List[float], duration: float) -> None:
        """设置波形采样数据与总时长"""
        self._samples = samples
        self._duration = duration
        if duration > 0:
            self._view_duration = duration
        self.update()

    def set_subtitles(self, subtitles: List[Subtitle]) -> None:
        """设置字幕列表（用于绘制断点）"""
        self._subtitles = subtitles
        self.update()

    def set_selected_subtitle(self, index: int) -> None:
        """设置当前选中的字幕序号"""
        self._selected_index = index
        self.update()

    def set_view_range(self, start: float, duration: float) -> None:
        """设置可视时间范围"""
        if duration <= 0:
            return
        self._view_start = max(0.0, start)
        self._view_duration = duration
        self.update()

    def get_view_range(self) -> Tuple[float, float]:
        return self._view_start, self._view_duration

    def get_duration(self) -> float:
        return self._duration

    # ------------------------------------------------------------------
    # 坐标转换（统一以时间戳为口径）
    # ------------------------------------------------------------------
    def _plot_rect(self) -> QRect:
        """波形绘制区域（扣除内边距）"""
        w = self.width()
        h = self.height()
        return QRect(
            self._pad_left,
            self._pad_top,
            max(1, w - self._pad_left - self._pad_right),
            max(1, h - self._pad_top - self._pad_bottom),
        )

    def time_to_x(self, time_sec: float) -> float:
        """时间 -> 像素 x（相对整个 widget）"""
        rect = self._plot_rect()
        if self._view_duration <= 0:
            return rect.left()
        ratio = (time_sec - self._view_start) / self._view_duration
        return rect.left() + ratio * rect.width()

    def x_to_time(self, x: float) -> float:
        """像素 x -> 时间"""
        rect = self._plot_rect()
        if rect.width() <= 0:
            return self._view_start
        ratio = (x - rect.left()) / rect.width()
        return self._view_start + ratio * self._view_duration

    def sample_to_y(self, value: float, rect: QRect) -> float:
        """采样值 [-1,1] -> 像素 y（中线为 rect 中心）"""
        center = rect.center().y()
        half_h = rect.height() / 2.0
        return center - value * half_h

    # ------------------------------------------------------------------
    # 断点命中测试
    # ------------------------------------------------------------------
    def _hit_test_breakpoint(self, x: int) -> Optional[Tuple[int, str]]:
        """
        测试像素 x 是否命中某个字幕的断点。
        返回 (subtitle_index, 'start'/'end') 或 None。
        """
        best = None
        best_dist = BREAKPOINT_HIT_TOLERANCE
        for sub in self._subtitles:
            sx = self.time_to_x(sub.start)
            ex = self.time_to_x(sub.end)
            d_start = abs(x - sx)
            d_end = abs(x - ex)
            if d_start < best_dist:
                best_dist = d_start
                best = (sub.index, "start")
            if d_end < best_dist:
                best_dist = d_end
                best = (sub.index, "end")
        return best

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self._plot_rect()

        # 背景
        painter.fillRect(self.rect(), COLOR_BG)

        # 网格 + 时间轴
        self._draw_grid(painter, rect)

        # 波形
        self._draw_waveform(painter, rect)

        # 字幕区间 + 断点
        self._draw_subtitles(painter, rect)

        # 标签
        if self._label:
            painter.setPen(COLOR_TEXT)
            painter.setFont(QFont("Arial", 9))
            painter.drawText(rect.left() + 6, rect.top() + 14, self._label)

        painter.end()

    def _draw_grid(self, painter: QPainter, rect: QRect):
        """绘制网格线与时间刻度"""
        painter.setPen(QPen(COLOR_GRID, 1, Qt.DotLine))
        # 水平中线
        mid_y = rect.center().y()
        painter.drawLine(rect.left(), mid_y, rect.right(), mid_y)

        # 垂直时间刻度：根据可视时长决定刻度数
        n_ticks = 10
        painter.setPen(QPen(COLOR_GRID, 1, Qt.DotLine))
        for i in range(n_ticks + 1):
            t = self._view_start + self._view_duration * i / n_ticks
            x = self.time_to_x(t)
            if rect.left() <= x <= rect.right():
                painter.drawLine(int(x), rect.top(), int(x), rect.bottom())

        # 时间刻度标签
        painter.setPen(COLOR_AXIS)
        painter.setFont(QFont("Arial", 8))
        for i in range(n_ticks + 1):
            t = self._view_start + self._view_duration * i / n_ticks
            x = self.time_to_x(t)
            if rect.left() <= x <= rect.right():
                label = self._format_time_short(t)
                painter.drawText(int(x) - 20, rect.bottom() + 14, 40, 16, Qt.AlignCenter, label)

    def _draw_waveform(self, painter: QPainter, rect: QRect):
        """绘制波形（使用像素列 MinMax 包络，保真尖峰）"""
        if not self._samples or self._view_duration <= 0:
            return

        n = len(self._samples)
        total_duration = self._duration if self._duration > 0 else 1.0
        # 可视范围内的采样索引区间
        start_ratio = self._view_start / total_duration
        end_ratio = (self._view_start + self._view_duration) / total_duration
        start_idx = max(0, int(start_ratio * n))
        end_idx = min(n, int(end_ratio * n))
        if end_idx <= start_idx:
            return

        visible = self._samples[start_idx:end_idx]
        if not visible:
            return

        plot_w = rect.width()
        # 按像素列做 MinMax 包络
        col_count = min(plot_w, len(visible))
        if col_count < 1:
            col_count = 1
        samples_per_col = len(visible) / col_count

        painter.setPen(QPen(self._wave_color, 1))
        mid_y = rect.center().y()

        for col in range(col_count):
            s = int(col * samples_per_col)
            e = int((col + 1) * samples_per_col)
            if e <= s:
                e = s + 1
            chunk = visible[s:e]
            v_max = max(chunk)
            v_min = min(chunk)
            x = rect.left() + col
            y_top = self.sample_to_y(v_max, rect)
            y_bot = self.sample_to_y(v_min, rect)
            painter.drawLine(int(x), int(y_top), int(x), int(y_bot))

        # 零轴
        painter.setPen(QPen(COLOR_GRID, 1, Qt.DotLine))
        painter.drawLine(rect.left(), int(mid_y), rect.right(), int(mid_y))

    def _draw_subtitles(self, painter: QPainter, rect: QRect):
        """绘制字幕区间填充 + 断点竖线"""
        for sub in self._subtitles:
            x_start = self.time_to_x(sub.start)
            x_end = self.time_to_x(sub.end)
            # 只绘制可视范围内的
            if x_end < rect.left() or x_start > rect.right():
                continue

            is_selected = (sub.index == self._selected_index)
            region_color = COLOR_SELECTED if is_selected else COLOR_REGION

            # 区间填充
            fill_rect = QRectF(
                max(rect.left(), x_start),
                rect.top(),
                max(1.0, min(rect.right(), x_end) - max(rect.left(), x_start)),
                rect.height(),
            )
            painter.fillRect(fill_rect, QBrush(region_color))

            # 断点竖线
            pen_color = COLOR_SELECTED if is_selected else COLOR_BREAKPOINT
            painter.setPen(QPen(pen_color, 2))
            if rect.left() <= x_start <= rect.right():
                painter.drawLine(int(x_start), rect.top(), int(x_start), rect.bottom())
            if rect.left() <= x_end <= rect.right():
                painter.drawLine(int(x_end), rect.top(), int(x_end), rect.bottom())

            # 字幕序号标签
            if is_selected:
                painter.setPen(COLOR_SELECTED)
                painter.setFont(QFont("Arial", 9, QFont.Bold))
                label_x = max(rect.left() + 2, x_start)
                painter.drawText(int(label_x), rect.top() + 14, f"#{sub.index}")

    @staticmethod
    def _format_time_short(seconds: float) -> str:
        if seconds < 0:
            seconds = 0
        m = int(seconds // 60)
        s = seconds - m * 60
        return f"{m}:{s:05.2f}"

    # ------------------------------------------------------------------
    # 鼠标交互
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        x = event.x()
        if event.button() == Qt.LeftButton:
            # 优先测试断点命中
            hit = self._hit_test_breakpoint(x)
            if hit:
                idx, which = hit
                self._drag_subtitle_index = idx
                self._drag_mode = DragMode.DRAG_START if which == "start" else DragMode.DRAG_END
                self._selected_index = idx
                self.update()
            else:
                # 空白处拖动平移
                self._drag_mode = DragMode.PAN
                self._pan_last_x = x
        elif event.button() == Qt.RightButton:
            # 右键重置视图
            self._view_start = 0.0
            self._view_duration = self._duration if self._duration > 0 else 1.0
            self.view_changed.emit(self._view_start, self._view_duration)
            self.update()

    def mouseMoveEvent(self, event):
        x = event.x()
        if self._drag_mode == DragMode.PAN:
            dx = x - self._pan_last_x
            rect = self._plot_rect()
            if rect.width() > 0 and self._view_duration > 0:
                time_dx = dx / rect.width() * self._view_duration
                self._view_start = max(0.0, self._view_start - time_dx)
                self.view_changed.emit(self._view_start, self._view_duration)
            self._pan_last_x = x
            self.update()

        elif self._drag_mode in (DragMode.DRAG_START, DragMode.DRAG_END):
            # 拖拽断点，更新对应字幕时间
            new_time = self.x_to_time(x)
            for sub in self._subtitles:
                if sub.index == self._drag_subtitle_index:
                    if self._drag_mode == DragMode.DRAG_START:
                        # 起始不能超过结束
                        sub.start = min(max(0.0, new_time), sub.end - 0.01)
                    else:
                        # 结束不能小于起始，不能超过总时长
                        max_end = self._duration if self._duration > 0 else sub.end
                        sub.end = max(new_time, sub.start + 0.01)
                        if max_end > 0:
                            sub.end = min(sub.end, max_end)
                    self.subtitle_changed.emit(sub.index, sub.start, sub.end)
                    break
            self.update()

        else:
            # 悬停：更新光标
            hit = self._hit_test_breakpoint(x)
            if hit:
                self.setCursor(Qt.SplitHCursor)
            else:
                self.setCursor(Qt.OpenHandCursor if self._drag_mode == DragMode.PAN else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self._drag_mode = DragMode.NONE
        self.setCursor(Qt.ArrowCursor)

    def wheelEvent(self, event):
        # 滚轮缩放：以鼠标位置为中心
        x = event.x()
        zoom_factor = 0.8 if event.angleDelta().y() > 0 else 1.25  # 上滚放大，下滚缩小
        old_duration = self._view_duration
        new_duration = max(0.1, min(self._duration if self._duration > 0 else old_duration,
                                    old_duration * zoom_factor))

        # 保持鼠标所在时间点不动
        mouse_time = self.x_to_time(x)
        rect = self._plot_rect()
        if rect.width() > 0:
            ratio = (x - rect.left()) / rect.width()
            self._view_start = mouse_time - ratio * new_duration
            self._view_start = max(0.0, self._view_start)

        self._view_duration = new_duration
        self.view_changed.emit(self._view_start, self._view_duration)
        self.update()


class DualWaveformView(QWidget):
    """
    双轨波形视图：原始波形（上）+ 处理后波形（下）。

    两条轨道共享同一时间轴，平移/缩放联动。
    """

    # 字幕修改信号：(subtitle_index, new_start, new_end)
    subtitle_changed = pyqtSignal(int, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._subtitles: List[Subtitle] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # 上轨：原始波形
        self._track_orig = WaveformTrack(
            wave_color=COLOR_WAVE_ORIG, label="原始波形", parent=self
        )
        # 下轨：处理后波形
        self._track_proc = WaveformTrack(
            wave_color=COLOR_WAVE_PROC, label="处理后波形", parent=self
        )

        layout.addWidget(self._track_orig, 1)
        layout.addWidget(self._track_proc, 1)

        # 联动：任一轨道视图变化，同步另一轨道
        self._track_orig.view_changed.connect(self._on_view_changed)
        self._track_proc.view_changed.connect(self._on_view_changed)
        self._track_orig.subtitle_changed.connect(self._on_subtitle_changed)
        self._track_proc.subtitle_changed.connect(self._on_subtitle_changed)

        self.setMinimumHeight(300)

    # ------------------------------------------------------------------
    # 外部接口
    # ------------------------------------------------------------------
    def set_original_waveform(self, samples: List[float], duration: float) -> None:
        """设置原始波形数据"""
        self._track_orig.set_samples(samples, duration)

    def set_processed_waveform(self, samples: List[float], duration: float) -> None:
        """设置处理后波形数据"""
        self._track_proc.set_samples(samples, duration)

    def set_subtitles(self, subtitles: List[Subtitle]) -> None:
        """设置字幕列表（两条轨道都绘制断点）"""
        self._subtitles = subtitles
        self._track_orig.set_subtitles(subtitles)
        self._track_proc.set_subtitles(subtitles)

    def get_subtitles(self) -> List[Subtitle]:
        """获取当前字幕（可能已被拖拽修改）"""
        return self._subtitles

    def select_subtitle(self, index: int) -> None:
        """选中某条字幕（高亮显示）"""
        self._track_orig.set_selected_subtitle(index)
        self._track_proc.set_selected_subtitle(index)

    def save_to_srt(self, output_path: str) -> str:
        """将当前字幕保存为 SRT 文件"""
        if not self._subtitles:
            raise ValueError("没有可保存的字幕")
        return SRTExporter().export(self._subtitles, output_path)

    # ------------------------------------------------------------------
    # 内部联动
    # ------------------------------------------------------------------
    def _on_view_changed(self, start: float, duration: float):
        sender = self.sender()
        # 同步另一轨道的视图范围（避免递归）
        if sender is self._track_orig:
            self._track_proc.set_view_range(start, duration)
        else:
            self._track_orig.set_view_range(start, duration)

    def _on_subtitle_changed(self, index: int, start: float, end: float):
        # 同步到另一条轨道的字幕数据
        for sub in self._subtitles:
            if sub.index == index:
                sub.start = start
                sub.end = end
                break
        # 两条轨道都重绘
        self._track_orig.update()
        self._track_proc.update()
        self.subtitle_changed.emit(index, start, end)
