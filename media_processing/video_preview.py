"""
视频预览播放引擎（成员3）

补齐主项目 ``view.video_panel.VideoPanel`` 的缺口：
当前界面只显示占位文字「暂无视频源，待成员3接入」，没有真实画面。

本模块用 FFmpeg 解码视频帧，提供两种使用方式：

1. 无界面（可单测）：``VideoFrameReader``
   - ``frame_at(seconds) -> bytes``（RGB24 原始像素）
   - ``info`` 暴露 width / height / fps / duration

2. 接入 GUI：``PlaybackController``（QObject，需 PyQt5/PySide6）
   - ``load(path)`` / ``play()`` / ``pause()`` / ``toggle()`` / ``seek(frac)``
   - 信号 ``frame_ready(QImage)`` / ``position_changed(float)`` / ``finished()``
   - GUI 侧接到 ``VideoCanvas`` 的绘制函数即可

解码实现：启动一个常驻 ffmpeg 子进程输出 rawvideo(rgb24)，
按帧读取；seek 时以 -ss 重启进程（关键帧定位 + 精确解码）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Optional

from .ffmpeg_wrapper import FFmpegWrapper, VideoInfo

# 输出像素格式
PIX_FMT = "rgb24"


class VideoFrameReader:
    """
    视频帧读取器（无 Qt 依赖）。

    通过常驻 ffmpeg 管道顺序读帧，seek 时重启管道。
    线程安全性：所有公开方法内部加锁，可在工作线程中调用。
    """

    def __init__(self, video_path: str, ffmpeg_wrapper: Optional[FFmpegWrapper] = None):
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"视频文件不存在：{video_path}")
        self._path = video_path
        self._wrapper = ffmpeg_wrapper or FFmpegWrapper()
        self._info: VideoInfo = self._wrapper.probe(video_path)
        if not self._info.has_video:
            raise ValueError(f"文件不含视频流：{video_path}")

        self._width = int(self._info.width or 0)
        self._height = int(self._info.height or 0)
        if self._width <= 0 or self._height <= 0:
            raise ValueError(f"无法获取视频分辨率：{video_path}")

        self._fps = float(self._info.fps) if self._info.fps > 0 else 25.0
        self._duration = float(self._info.duration or 0.0)
        self._frame_bytes = self._width * self._height * 3

        self._proc: Optional[subprocess.Popen] = None
        self._pos = 0.0          # 当前已读到的时间（秒）
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------
    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def info(self) -> VideoInfo:
        return self._info

    @property
    def position(self) -> float:
        return self._pos

    # ------------------------------------------------------------------
    # 管道控制
    # ------------------------------------------------------------------
    def _start_pipe(self, start_sec: float) -> None:
        """在 start_sec 处重新拉起 ffmpeg 解码管道。"""
        self._stop_pipe()
        start_sec = max(0.0, min(start_sec, max(0.0, self._duration - 0.001)))
        cmd = [
            self._wrapper.ffmpeg_path,
            "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_sec:.3f}",
            "-i", self._path,
            "-an",                       # 不要音频（预览画面即可）
            "-f", "rawvideo",
            "-pix_fmt", PIX_FMT,
            "pipe:1",
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=self._frame_bytes * 2,
        )
        self._pos = start_sec

    def _stop_pipe(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            try:
                self._proc.stdout.close()
            except Exception:
                pass
            self._proc = None

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def read_next_frame(self) -> Optional[bytes]:
        """顺序读取下一帧 RGB24 数据；流结束返回 None。"""
        with self._lock:
            if self._proc is None:
                self._start_pipe(0.0)
            if self._proc is None or self._proc.stdout is None:
                return None
            data = self._proc.stdout.read(self._frame_bytes)
            if not data or len(data) < self._frame_bytes:
                self._stop_pipe()
                return None
            self._pos += 1.0 / self._fps
            return data

    def frame_at(self, seconds: float) -> Optional[bytes]:
        """取指定时间点的一帧（seek + 读 1 帧）。"""
        with self._lock:
            self._start_pipe(seconds)
            if self._proc is None or self._proc.stdout is None:
                return None
            data = self._proc.stdout.read(self._frame_bytes)
            if not data or len(data) < self._frame_bytes:
                return None
            return data

    def seek(self, seconds: float) -> None:
        """跳转到指定时间（下一次 read_next_frame 从该处继续）。"""
        with self._lock:
            self._start_pipe(seconds)

    def close(self) -> None:
        with self._lock:
            self._stop_pipe()

    def __enter__(self) -> "VideoFrameReader":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ----------------------------------------------------------------------
# GUI 播放控制器（可选依赖 Qt）
# ----------------------------------------------------------------------
def _qt_bindings():
    """
    返回 (QtCore, QtGui)，优先复用宿主进程已加载的绑定。

    重要：PyQt5 与 PySide6 在同一进程内混用会导致崩溃（0xC0000005），
    因此必须先检测 ``sys.modules`` 里已导入的绑定，再决定新导入哪个。
    """
    loaded_pyside = any(m.startswith("PySide6") for m in sys.modules)
    loaded_pyqt = any(m.startswith("PyQt5") for m in sys.modules)

    if loaded_pyqt and not loaded_pyside:
        order = ("pyqt5",)
    elif loaded_pyside and not loaded_pyqt:
        order = ("pyside6",)
    else:
        order = ("pyside6", "pyqt5")

    for name in order:
        try:
            if name == "pyside6":
                from PySide6 import QtCore, QtGui  # type: ignore

                return QtCore, QtGui
            from PyQt5 import QtCore, QtGui  # type: ignore

            return QtCore, QtGui
        except Exception:
            continue
    return None, None


def make_playback_controller_class():
    """
    延迟构造 PlaybackController 类。

    避免在无 Qt 环境下 import 本模块直接失败：只有真正要接 GUI 时才构造。
    """
    QtCore, QtGui = _qt_bindings()
    if QtCore is None or QtGui is None:
        raise ImportError("未安装 PySide6 / PyQt5，无法创建播放控制器")

    # 使用已检测到的绑定，避免再次导入另一套 Qt
    QObject = QtCore.QObject
    QTimer = QtCore.QTimer
    Signal = getattr(QtCore, "Signal", None) or QtCore.pyqtSignal

    class PlaybackController(QObject):
        """
        视频播放控制器：驱动 VideoFrameReader，按帧率向界面推 QImage。

        与 view.video_panel.VideoPanel 对接：
          - VideoPanel.set_playing(playing)  ← 由本类状态驱动
          - VideoPanel.set_position(frac)    ← position_changed
          - VideoCanvas 绘制                ← frame_ready
        """

        frame_ready = Signal(object)        # QImage
        position_changed = Signal(float)    # 0.0 ~ 1.0
        finished = Signal()
        error = Signal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._reader: Optional[VideoFrameReader] = None
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._on_tick)
            self._playing = False

        # ---------------- 生命周期 ----------------
        def load(self, video_path: Optional[str]) -> bool:
            self.stop()
            if self._reader is not None:
                self._reader.close()
                self._reader = None
            if not video_path:
                return False
            try:
                self._reader = VideoFrameReader(video_path)
            except Exception as e:
                self.error.emit(str(e))
                return False
            # 先推首帧，避免界面空白
            self._emit_frame(0.0)
            self.position_changed.emit(0.0)
            return True

        def close(self) -> None:
            self.stop()
            if self._reader is not None:
                self._reader.close()
                self._reader = None

        # ---------------- 播放控制 ----------------
        @property
        def is_playing(self) -> bool:
            return self._playing

        @property
        def duration(self) -> float:
            return self._reader.duration if self._reader else 0.0

        def play(self) -> None:
            if self._reader is None or self._playing:
                return
            self._playing = True
            interval = max(10, int(1000.0 / self._reader.fps))
            self._timer.start(interval)

        def pause(self) -> None:
            self._playing = False
            self._timer.stop()

        def toggle(self) -> None:
            self.pause() if self._playing else self.play()

        def stop(self) -> None:
            self.pause()

        def seek(self, frac: float) -> None:
            """按比例跳转（与 VideoPanel.set_position(frac) 口径一致）。"""
            if self._reader is None:
                return
            frac = max(0.0, min(1.0, float(frac)))
            target = frac * self._reader.duration
            self._reader.seek(target)
            self._emit_frame(target)
            self.position_changed.emit(frac)

        # ---------------- 内部 ----------------
        def _emit_frame(self, _pos: float) -> None:
            if self._reader is None:
                return
            raw = self._reader.read_next_frame()
            if not raw:
                return
            img = QtGui.QImage(
                raw,
                self._reader.width,
                self._reader.height,
                self._reader.width * 3,
                QtGui.QImage.Format_RGB888,
            )
            # 复制一份，避免底层 bytes 释放后悬空
            self.frame_ready.emit(img.copy())

        def _on_tick(self) -> None:
            if self._reader is None:
                self.pause()
                return
            raw = self._reader.read_next_frame()
            if not raw:
                self.pause()
                self.finished.emit()
                return
            img = QtGui.QImage(
                raw,
                self._reader.width,
                self._reader.height,
                self._reader.width * 3,
                QtGui.QImage.Format_RGB888,
            )
            self.frame_ready.emit(img.copy())
            dur = self._reader.duration
            if dur > 0:
                self.position_changed.emit(min(1.0, self._reader.position / dur))

    return PlaybackController


def create_playback_controller(parent=None):
    """便捷工厂：返回 PlaybackController 实例（需要 Qt）。"""
    cls = make_playback_controller_class()
    return cls(parent)
