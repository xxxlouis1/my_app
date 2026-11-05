# -*- coding: utf-8 -*-
"""Aboboo 式分区界面演示 / 冒烟测试。

正常用法（给成员1 看界面）：
    venv\\Scripts\\python.exe scripts\\demo_aboboo.py

冒烟测试（无窗口，自动退出）：
    venv\\Scripts\\python.exe scripts\\demo_aboboo.py --offscreen

成员3/4 尚未产出真实波形与错误数据，这里用合成数据模拟：
    view.set_waveform(track)     # 完整波形（成员3）
    view.set_subtitles(items)    # 字幕条目 + 错误标注（成员2/4）
    view.set_playhead(t)         # 播放位置（本演示用定时器模拟播放）
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
from PySide6.QtWidgets import QApplication, QMainWindow

from view.aboboo_view import AbobooView
from view.subtitle_model import ErrorInfo, SubtitleItem
from view.theme import DARK_QSS
from view.waveform_view import WaveformTrack

def make_peaks(duration: float, buckets: int = 6000,
               seed: int = 0, amp: float = 0.7, f0: float = 2.5):
    rnd = random.Random(seed)
    step = duration / buckets
    per = 6
    peaks = []
    for i in range(buckets):
        t0 = i * step
        lo, hi = 1.0, -1.0
        for k in range(per):
            t = t0 + (k + 0.5) * step / per
            env = 0.5 + 0.5 * math.sin(2 * math.pi * 0.09 * t)
            tone = math.sin(2 * math.pi * f0 * t) + 0.4 * math.sin(2 * math.pi * f0 * 2.1 * t)
            v = amp * env * tone + rnd.uniform(-0.04, 0.04)
            if (t % 5.0) > 4.0:  # 句间停顿
                v *= 0.05
            v = max(-1.0, min(1.0, v))
            lo = min(lo, v)
            hi = max(hi, v)
        peaks.append((lo, hi))
    return peaks


_SENTENCES = [
    ("I didn't feel like eating because I felt sick.", "我不想吃东西，因为我觉得不舒服。", None),
    ("It was a cloudy day in Moscow.", "莫斯科天气阴沉。", None),
    ("We visited Red Square in the morning.", "早上我们参观了红场。",
     ErrorInfo(3, "时间轴偏移", "该句起点比波形实际语音起点早约 300ms，需右移对齐。", 12340)),
    ("The fountain looked beautiful in the sun.", "阳光下喷泉很美。", None),
    ("I bought a postcard for my sister.", "我给妹妹买了张明信片。", None),
    ("Lunch was at a small café near the river.", "午饭在河边的小咖啡馆。",
     ErrorInfo(6, "识别错误", "“café”被识别为“coffee”，与原文不符，需修正。", 28500)),
    ("The soup was too salty for me.", "汤对我来说太咸了。", None),
    ("We walked along the street after lunch.", "午饭后我们沿街散步。", None),
    ("I took many photos of the church.", "我拍了很多教堂的照片。", None),
    ("In the evening we watched a show.", "晚上我们看了场演出。", None),
    ("I felt much better by the end of the day.", "到一天结束时我感觉好多了。",
     ErrorInfo(11, "缺失", "该句在音频中无对应语音，疑似漏读，需补录或删除。", 51000)),
    ("We planned to visit the museum tomorrow.", "我们计划明天去博物馆。", None),
]


def make_items(duration: float):
    n = len(_SENTENCES)
    slot = duration / n
    items = []
    for i, (text, trans, err) in enumerate(_SENTENCES):
        start_ms = int(i * slot * 1000) + 300
        end_ms = int((i + 1) * slot * 1000) - 1200
        items.append(SubtitleItem(no=i + 1, start_ms=start_ms, end_ms=end_ms,
                                  text=text, translation=trans, error=err))
    return items


def main():
    offscreen = "--offscreen" in sys.argv
    if offscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.argv = [a for a in sys.argv if a != "--offscreen"]

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)

    win = QMainWindow()
    win.setWindowTitle("MP4 字幕矫正工具 · Aboboo 式界面预览")
    win.resize(1400, 860)

    view = AbobooView()
    win.setCentralWidget(view)

    duration = 60.0
    view.set_waveform(WaveformTrack("完整波形", make_peaks(duration, seed=7), duration))
    view.set_subtitles(make_items(duration))

    # 用定时器模拟播放，驱动红色游标 / 视频进度条
    state = {"t": 0.0, "playing": False}
    timer = QTimer()
    timer.setInterval(40)

    def tick():
        state["t"] += 0.04
        if state["t"] >= duration:
            state["t"] = 0.0
        view.set_playhead(state["t"])

    timer.timeout.connect(tick)

    def toggle():
        if state["playing"]:
            timer.stop()
            state["playing"] = False
        else:
            timer.start()
            state["playing"] = True
        view.video_panel.set_playing(state["playing"])

    def on_seek(frac):
        state["t"] = frac * duration
        view.set_playhead(state["t"])

    def on_wave_seek(t_sec):
        state["t"] = t_sec  # 播放基准跳到点击位置（游标已由视图内部移动）

    view.video_panel.playPauseRequested.connect(toggle)
    view.video_panel.seekRequested.connect(on_seek)
    view.seekRequested.connect(on_wave_seek)

    win.show()
    view.set_playhead(0.0)
    # 默认不自动播放：点「▶ 播放」后，模拟播放器才会推进游标

    if offscreen:
        QTimer.singleShot(600, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
