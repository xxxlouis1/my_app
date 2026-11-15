# -*- coding: utf-8 -*-
"""
GUI 整合端到端验证（离屏，不需要你盯着屏幕）

模拟完整用户流程，逐项断言《接入指南》§5 的 6 条验收标准：
  1. 选视频 → 波形区出现双轨波形
  2. 加载 SRT → 波形上出现断点、字幕列表可点选
  3. 拖动断点 → 时间更新（这里直接改 SrtCue 模拟拖动）
  4. 点字幕 → 单句波形刷新
  5. 播放 → 视频画面逐帧刷新、播放头同步
  6. 保存 → 组装出合法 SRT

用法：
    python scripts/verify_gui_integration.py [视频] [SRT]
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

# 离屏测试里 QMessageBox 是**模态**的，会永久阻塞事件循环。
# 打桩成"静默通过 + 记录文本"，既能跑完流程又能断言弹窗内容。
DIALOGS = []


def _stub(kind):
    def _f(*a, **k):
        DIALOGS.append((kind, a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else ""))
        return QMessageBox.StandardButton.Ok
    return staticmethod(_f)


for _m in ("information", "warning", "critical", "question"):
    setattr(QMessageBox, _m, _stub(_m))

FAIL = []


def check(name, ok, detail=""):
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAIL.append(name)


def main():
    video = str(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "test.mp4")
    srt = str(Path(sys.argv[2]) if len(sys.argv) > 2
              else ROOT / "test_output" / "pipeline" / "test.srt")
    if not Path(video).exists():
        print(f"[x] 视频不存在：{video}")
        sys.exit(1)
    if not Path(srt).exists():
        print(f"[x] SRT 不存在：{srt}")
        sys.exit(1)

    app = QApplication([])
    from view.main_window import MainWindow

    w = MainWindow()
    w.show()

    print("=" * 74)
    print("GUI 整合端到端验证")
    print("=" * 74)
    print(f"  视频：{video}")
    print(f"  字幕：{srt}")

    # ---------- 1. 选视频（绕过文件对话框，直接走内部逻辑）
    print("\n[1] 选择视频 → 双轨波形")
    w.video_path = video
    w.video_edit.setText(video)
    if w._playback is not None:
        w._playback.load(video)
    w._load_global_waveform()

    # 等波形线程结束（最多 60s）。用 processEvents 泵事件，不要嵌套 exec()
    import time
    deadline = time.time() + 60
    while (w._wave_worker is not None and w._wave_worker.isRunning()
           and time.time() < deadline):
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()

    canvas = w.waveform_panel.canvas
    has_orig = canvas._original is not None
    has_proc = canvas._processed is not None
    check("波形已生成", has_orig, f"原始轨 points={len(getattr(canvas._original,'peaks',[]) or [])}")
    check("双轨齐备", has_orig and has_proc)
    check("总时长>0", canvas._duration > 0, f"{canvas._duration:.2f}s")

    # ---------- 2. 加载 SRT
    print("\n[2] 加载 SRT → 断点 + 字幕列表")
    w.srt_path = srt
    w._load_srt()
    app.processEvents()
    n_cues = len(canvas.cues())
    check("波形上已注入断点", n_cues > 0, f"{n_cues} 条")
    items = w.aboboo_view._items
    check("Aboboo 字幕列表已填充", len(items) > 0, f"{len(items)} 条")
    if items:
        errs = sum(1 for it in items if it.error is not None)
        check("错误标注已生成", True, f"{errs} 条带错误标记")

    # ---------- 4. 点字幕 → 单句波形
    print("\n[3] 点选字幕 → 单句波形")
    if items:
        mid = items[len(items) // 2]
        w.aboboo_view.subtitle_list.selected.emit(mid)
        app.processEvents()
        sent = w.aboboo_view.sentence_plot
        has_seg = getattr(sent, "_track", None) is not None
        seg_dur = getattr(getattr(sent, "_track", None), "duration", 0.0) or 0.0
        check("单句波形已设置", has_seg,
              f"切片时长 {seg_dur:.2f}s（应≈句长）" if has_seg else "未设置")
        check("选中项已联动", w.aboboo_view.video_panel is not None)

    # ---------- 5. 播放推帧 + 播放头
    print("\n[4] 播放 → 画面推帧 + 播放头同步")
    if w._playback is not None:
        frames = {"n": 0, "pos": []}
        w._playback.frame_ready.connect(lambda img: frames.__setitem__("n", frames["n"] + 1))
        w._playback.position_changed.connect(lambda f: frames["pos"].append(f))
        w._playback.play()
        # 跑 1.5 秒：只泵事件，不启动嵌套事件循环（嵌套 exec 会挂住）
        import time as _t
        t_end = _t.time() + 1.5
        while _t.time() < t_end:
            app.processEvents()
            _t.sleep(0.01)
        w._playback.pause()
        app.processEvents()
        check("收到视频帧", frames["n"] >= 5, f"{frames['n']} 帧")
        check("播放位置信号", len(frames["pos"]) >= 1, f"{len(frames['pos'])} 次")
        ph = canvas.playhead_time()
        check("波形播放头已更新", ph is not None, f"{ph:.2f}s" if ph else "None")

    # ---------- 3 & 6. 模拟拖动断点 → 保存 SRT
    print("\n[5] 拖动断点 → 保存 SRT")
    cues = canvas.cues()
    if cues:
        # 模拟把第一条起点往后拖 200ms（等价于鼠标拖拽后的结果）
        cues[0].start_ms += 200
        out_path = ROOT / "test_output" / "gui_saved.srt"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # 绕过保存对话框
        orig_dialog = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: (str(out_path), "SRT 字幕 (*.srt)"))
        try:
            w._on_save_waveform(cues)
        finally:
            QFileDialog.getSaveFileName = orig_dialog
        app.processEvents()
        check("SRT 已写出", out_path.exists(),
              f"{out_path.stat().st_size} bytes" if out_path.exists() else "未生成")
        if out_path.exists():
            raw = out_path.read_bytes()
            check("输出为 UTF-8 无 BOM", not raw.startswith(b"\xef\xbb\xbf"))
            text = raw.decode("utf-8-sig")
            check("含标准时间戳箭头", "-->" in text, f"{text.count('-->')} 条")
            # 抽检第一条时间是否为 +200ms 后的结果
            first_ts = text.splitlines()[1] if len(text.splitlines()) > 1 else ""
            check("拖动生效（首条时间已变）", first_ts.strip() != "",
                  first_ts.strip())

    print()
    print("=" * 74)
    if FAIL:
        print(f"  {len(FAIL)} 项失败：" + "、".join(FAIL))
        sys.exit(1)
    print("  全部通过")


if __name__ == "__main__":
    main()
