# -*- coding: utf-8 -*-
"""波形编辑功能演示 / 冒烟测试。

正常用法（给成员1 看界面）：
    venv\\Scripts\\python.exe scripts\\demo_waveform.py

冒烟测试（无窗口，自动退出，用于 CI / 自检）：
    venv\\Scripts\\python.exe scripts\\demo_waveform.py --offscreen

因为成员3/4 尚未产出真实波形采样数据，这里用合成数据模拟后端调用：
    win.set_waveform(original, processed)   # 波形采样（成员3 提供）
    win.waveform_panel.set_cues(cues)       # 字幕断点（成员4/成员2 提供）
"""
from __future__ import annotations

import math
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from view.main_window import MainWindow
from view.srt_parser import SrtCue, ms_to_ts
from view.waveform_view import WaveformTrack


def _synth(t: float, rnd: random.Random, amp: float, f0: float) -> float:
    env = 0.5 + 0.5 * math.sin(2 * math.pi * 0.13 * t)
    tone = math.sin(2 * math.pi * f0 * t)
    overtone = 0.4 * math.sin(2 * math.pi * f0 * 2.1 * t)
    v = amp * env * (tone + overtone) + rnd.uniform(-0.04, 0.04)
    if (t % 4.0) > 2.8:  # 每 4 秒留一段静音，模拟句间停顿
        v *= 0.05
    return max(-1.0, min(1.0, v))


def make_peaks(duration: float, buckets: int = 4000,
               seed: int = 0, amp: float = 0.7, f0: float = 2.5):
    rnd = random.Random(seed)
    step = duration / buckets
    per = 8
    peaks = []
    for i in range(buckets):
        t0 = i * step
        lo, hi = 1.0, -1.0
        for k in range(per):
            v = _synth(t0 + (k + 0.5) * step / per, rnd, amp, f0)
            if v < lo:
                lo = v
            if v > hi:
                hi = v
        peaks.append((lo, hi))
    return peaks


def make_cues(duration: float, n: int = 10):
    cues = []
    for i in range(n):
        s = int(i * (duration / n) * 1000) + 200
        e = s + 3000
        cues.append(SrtCue(index=i + 1, start=ms_to_ts(s), end=ms_to_ts(e),
                           start_ms=s, end_ms=e, text=f"示例字幕 {i + 1}"))
    return cues


def main():
    offscreen = "--offscreen" in sys.argv
    if offscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.argv = [a for a in sys.argv if a != "--offscreen"]

    app = QApplication(sys.argv)
    win = MainWindow()

    # 模拟后端调用：波形采样数据
    duration = 40.0
    win.set_waveform(
        WaveformTrack("原始波形", make_peaks(duration, seed=1), duration),
        WaveformTrack("处理后波形", make_peaks(duration, seed=2, amp=0.55, f0=2.2), duration),
    )
    # 模拟后端调用：字幕断点
    win.waveform_panel.set_cues(make_cues(duration))
    win.srt_preview.set_cues(win.waveform_panel.cues())

    win.show()

    if offscreen:
        QTimer.singleShot(600, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
