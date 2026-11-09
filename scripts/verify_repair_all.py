# -*- coding: utf-8 -*-
"""
验证"自动修正能修掉所有可修错误"

对比三个阶段：
  A. Whisper 原始输出（只剔除无效段）
  B. 完整校正后
并调用独立校验器 check_srt.py 确认最终产物 0 ERROR。

用法：
    python scripts/verify_repair_all.py [视频] [--model large-v3] [--cpu]
"""
import argparse
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from model.asr import AsrEngine                                   # noqa: E402
from model.error_detect import detect_errors                      # noqa: E402
from model.pipeline import extract_audio                          # noqa: E402
from model.subtitle_fix import (FixConfig, FixReport, drop_invalid,  # noqa: E402
                                self_check, to_srt, write_srt)
from model.waveform import extract_boundaries, filter_speech      # noqa: E402

FAIL = []


def rule(t):
    print("\n" + "=" * 76)
    print(t)
    print("=" * 76)


def check(name, ok, detail=""):
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAIL.append(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?", default=str(ROOT / "test.mp4"))
    ap.add_argument("--model", default="small")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--language", default="en")
    args = ap.parse_args()

    video = Path(args.video)
    out_dir = ROOT / "test_output" / "repair_verify"
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / f"{video.stem}.wav"
    if not wav.exists():
        extract_audio(str(video), str(wav))

    rule("准备：音频与波形边界")
    b = extract_boundaries(str(wav))
    print(f"  {b.summary()}")
    anchors = filter_speech(b.speech, 0.30)
    onsets = [a for a, _ in anchors]

    rule(f"ASR 转写（{args.model}）")
    from model.gpu_env import gpu_available
    device = "cpu" if args.cpu else ("cuda" if gpu_available() else "cpu")
    ctype = "int8" if device == "cpu" else "int8_float16"
    print(f"  设备：{device} / {ctype}")
    eng = AsrEngine(model=args.model, device=device, compute_type=ctype,
                    language=args.language)
    tr = eng.transcribe(str(wav), language=args.language)
    print(f"  {tr.summary()}")

    cfg = FixConfig()

    # ---------- A. 原始输出：仅剔除无效段
    rule("A. Whisper 原始输出（未校正）")
    raws = list(drop_invalid(tr.segments, cfg, FixReport()))
    err_a = detect_errors(raws, b, cfg, media_duration=b.duration)
    print(f"  {len(raws)} 条字幕")
    print(f"  {err_a.summary()}")
    for t, n in err_a.by_type().items():
        print(f"      {t}: {n}")
    short_a = sum(1 for c in raws if c.duration_ms > cfg.max_duration_ms)
    print(f"  超长段(>{cfg.max_duration_ms}ms): {short_a} 条")
    print(f"  最长段: {max((c.duration_ms for c in raws), default=0)}ms")

    # ---------- B. 完整校正
    rule("B. 完整校正后")
    from model.subtitle_fix import fix_subtitles
    cues, rep = fix_subtitles(tr.segments, b, cfg)
    print(f"  {len(cues)} 条字幕")
    print(f"  统计：{rep.summary()}")
    err_b = detect_errors(cues, b, cfg, media_duration=b.duration)
    print(f"  {err_b.summary()}")
    for t, n in err_b.by_type().items():
        print(f"      {t}: {n}")
    print(f"  最短/最长段: {min((c.duration_ms for c in cues), default=0)}"
          f" / {max((c.duration_ms for c in cues), default=0)}ms")

    # ---------- 写文件 + 独立校验
    rule("C. 独立校验（check_srt.py）")
    srt = out_dir / f"{video.stem}.srt"
    write_srt(cues, str(srt))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_srt.py"),
                        str(srt), "--media", str(video)],
                       capture_output=True, text=True, errors="replace",
                       # 必须显式 utf-8：Windows 默认按 ANSI(GBK) 解码，
                       # 会把 check_srt 的 UTF-8 输出变成替换符，正则匹配失败。
                       encoding="utf-8")
    out = (r.stdout or "") + (r.stderr or "")
    import re as _re
    m = _re.search(r"结果：(\d+) 个 ERROR，(\d+) 个 WARN", out)
    n_err = int(m.group(1)) if m else -1
    n_warn = int(m.group(2)) if m else -1
    print(f"  ERROR {n_err} 条，WARN {n_warn} 条")

    # ---------- 判定
    rule("结论")
    improved = len(err_b.errors) < len(err_a.errors)
    check("错误总数减少", improved,
          f"{len(err_a.errors)} -> {len(err_b.errors)}")
    check("无非法时间戳(负值/零时长/超出时长)",
          not any(e.error_type == "时间非法" for e in err_b.errors))
    check("无重叠", not any(e.error_type == "时间重叠" for e in err_b.errors))
    check("无超长段（已切分而非截断）",
          not any(e.error_type == "时长异常" and "超过上限" in e.message
                  for e in err_b.errors))
    check("check_srt 无 ERROR", n_err == 0, f"{n_err} 条")
    check("结构自检通过", not self_check(cues, cfg))

    # 对齐质量
    dev = [min(abs(c.start - o) for o in onsets) for c in cues]
    if dev:
        exact = sum(1 for d in dev if d <= 0.005)
        check("起点精确命中语音起点 ≥90%",
              exact / len(cues) >= 0.9,
              f"{exact}/{len(cues)} = {exact/len(cues)*100:.0f}%"
              f"（中位 {statistics.median(dev)*1000:.0f}ms）")

    # 文本完整性
    import re as _re2
    from collections import Counter
    def words(seq):
        o = []
        for t in seq:
            o += _re2.findall(r"[A-Za-z0-9']+", t.lower())
        return o
    wb = words([s.text for s in tr.segments])
    wa = words([c.text for c in cues])
    lost = sum((Counter(wb) - Counter(wa)).values())
    check("文本零丢失", lost == 0, f"丢失 {lost} 词次（基线 {len(wb)}）")

    print()
    if FAIL:
        print(f"  {len(FAIL)} 项未通过：" + "、".join(FAIL))
        sys.exit(1)
    print("  全部通过")


if __name__ == "__main__":
    main()
