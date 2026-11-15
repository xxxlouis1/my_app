# -*- coding: utf-8 -*-
"""把界面离屏渲染成 PNG，便于核对"模样"（不弹窗、不干扰桌面）。

用法：python scripts/render_ui_preview.py [视频] [SRT]
产物：test_output/ui_preview/*.png
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

# 弹窗打桩，避免模态阻塞
for _m in ("information", "warning", "critical", "question"):
    setattr(QMessageBox, _m, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


def main():
    video = str(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "test.mp4")
    srt = str(Path(sys.argv[2]) if len(sys.argv) > 2
              else ROOT / "test_output" / "pipeline" / "test.srt")
    out_dir = ROOT / "test_output" / "ui_preview"
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    from view.main_window import MainWindow

    w = MainWindow()
    w.resize(1600, 950)
    w.show()

    # 选视频
    w.video_path = video
    w.video_edit.setText(video)
    if w._playback is not None:
        w._playback.load(video)
    w._load_global_waveform()

    deadline = time.time() + 90
    while (w._wave_worker is not None and w._wave_worker.isRunning()
           and time.time() < deadline):
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()

    # 加载 SRT
    w.srt_path = srt
    w._load_srt()
    app.processEvents()

    # 选中中间一条字幕，触发单句波形
    items = w.aboboo_view._items
    if items:
        item = items[len(items) // 3]
        w.aboboo_view.subtitle_list.selected.emit(item)
        app.processEvents()
        time.sleep(0.4)
        app.processEvents()

    # 播放头放到该句起点附近
    if w._playback is not None and items:
        frac = (item.start_ms / 1000.0) / max(1e-6, w._playback.duration)
        w._playback.seek(frac)
        app.processEvents()
        time.sleep(0.3)
        app.processEvents()

    print(f"  波形时长={w._duration:.1f}s  字幕={len(items)} 条")
    seg = getattr(w.aboboo_view.sentence_plot, "_track", None)
    if seg:
        print(f"  单句切片时长={seg.duration:.2f}s  点数={len(seg.peaks)}")

    # 渲染三个页签
    for i in range(w.tabs.count()):
        name = w.tabs.tabText(i)
        w.tabs.setCurrentIndex(i)
        app.processEvents()
        time.sleep(0.25)
        app.processEvents()
        pix = w.grab()
        safe = "".join(ch if ch.isalnum() else "_" for ch in name)
        f = out_dir / f"{i}_{safe}.png"
        pix.save(str(f), "PNG")
        print(f"  已渲染：{f.name}  {f.stat().st_size/1024:.0f} KB")

    # 关闭，避免占用解码句柄
    if w._playback is not None:
        w._playback.close()


if __name__ == "__main__":
    main()
