# -*- coding: utf-8 -*-
"""
实验：直接修正"已有的 Whisper SRT"（不跑 ASR）

验证本项目能否满足"对 whisper 生成的 srt 进行修正"这一需求。
用现有 model/ 里的函数组装流程，并量化修正效果。

用法：
    python scripts/_probe_correct_existing_srt.py [原始SRT] [音频]
"""
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from model.subtitle_fix import (FixConfig, FixReport, Cue, enforce_constraints,
                                read_srt_cues, self_check, snap_to_waveform,
                                to_srt, wrap_lines)
from model.waveform import extract_boundaries, filter_speech


def stats(cues, onsets, label):
    """量化：起点偏离真实语音起点的程度、重叠数、时长分布"""
    dev = []
    for c in cues:
        if onsets:
            dev.append(min(abs(c.start - o) for o in onsets))
    overlap = sum(1 for i in range(len(cues) - 1)
                  if cues[i].end > cues[i + 1].start + 1e-6)
    durs = [c.duration_ms for c in cues]
    return {
        "label": label,
        "条数": len(cues),
        "起点偏差中位ms": round(statistics.median(dev) * 1000, 1) if dev else None,
        "起点偏差平均ms": round(statistics.fmean(dev) * 1000, 1) if dev else None,
        "精确命中(<5ms)": sum(1 for d in dev if d <= 0.005),
        "重叠数": overlap,
        "时长中位ms": round(statistics.median(durs), 0) if durs else 0,
        "最短ms": min(durs) if durs else 0,
        "最长ms": max(durs) if durs else 0,
    }


def main():
    srt_in = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        ROOT / "test_output" / "probe" / "raw_small.srt"
    audio = Path(sys.argv[2]) if len(sys.argv) > 2 else \
        ROOT / "test_output" / "test.wav"

    print("=" * 78)
    print("实验：直接修正已有 Whisper SRT（跳过 ASR）")
    print("=" * 78)
    print(f"  输入 SRT: {srt_in}")
    print(f"  参考音频: {audio}")
    if not srt_in.exists():
        print(f"  [x] SRT 不存在：{srt_in}")
        sys.exit(1)
    if not audio.exists():
        print(f"  [x] 音频不存在：{audio}")
        sys.exit(1)

    # ---- 1. 读入 Whisper 的 SRT
    cues_in = read_srt_cues(str(srt_in))
    print(f"\n  读入 {len(cues_in)} 条字幕")

    # ---- 2. 从音频提取真实语音边界（不依赖 ASR）
    b = extract_boundaries(str(audio))
    anchors = filter_speech(b.speech, 0.30)
    onsets = [a for a, _ in anchors]
    print(f"  波形：{b.summary()}")
    print(f"  有效锚点 {len(onsets)} 个")

    before = stats(cues_in, onsets, "before")

    # ---- 3. 校正流水线（复用 model/ 中已有函数）
    cfg = FixConfig()
    rep = FixReport()
    print("\n  执行校正（吸附 -> 约束 -> 行长）…")
    cues = snap_to_waveform(cues_in, b, cfg, rep)
    cues = enforce_constraints(cues, cfg, rep)
    cues = wrap_lines(cues, cfg, rep)
    problems = self_check(cues, cfg)
    after = stats(cues, onsets, "after")

    # ---- 4. 对比
    print("\n" + "=" * 78)
    print("修正前后对比")
    print("=" * 78)
    keys = ["条数", "起点偏差中位ms", "起点偏差平均ms", "精确命中(<5ms)",
            "重叠数", "时长中位ms", "最短ms", "最长ms"]
    print(f"  {'指标':<18}{'修正前':>12}{'修正后':>12}")
    for k in keys:
        print(f"  {k:<18}{str(before[k]):>12}{str(after[k]):>12}")

    print(f"\n  校正统计: {rep.summary()}")
    print(f"  结构自检: {len(problems)} 项问题"
          + ("" if not problems else f" -> {problems[:3]}"))

    # ---- 5. 关键判定
    print("\n" + "=" * 78)
    print("结论")
    print("=" * 78)
    dev_b = before["起点偏差中位ms"]
    dev_a = after["起点偏差中位ms"]
    if dev_b is not None and dev_a is not None:
        print(f"  起点偏差中位: {dev_b}ms -> {dev_a}ms")
        print(f"  精确命中: {before['精确命中(<5ms)']} -> {after['精确命中(<5ms)']} 条")
    print(f"  重叠: {before['重叠数']} -> {after['重叠数']}")
    print(f"  结构错误: {len(problems)}")
    ok = (dev_a is not None and dev_a <= max(5.0, (dev_b or 0) * 0.5)
          and after["重叠数"] == 0 and not problems)
    print(f"\n  => {'可以做到：修正后的 SRT 对齐到真实语音边界' if ok else '效果不达预期，需排查'}")

    # ---- 6. 写出结果
    out = ROOT / "test_output" / "corrected_from_srt.srt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_srt(cues), encoding="utf-8", newline="\n")
    print(f"\n  已写出: {out}")

    # ---- 7. 长句切分能力说明（诚实标注缺口）
    long_cues = [c for c in cues if c.duration_ms > 10000]
    print(f"\n  注意：本次未做「按静音切分长句」——该步需要**词级时间戳**，")
    print(f"        而已有 SRT 里没有词级信息。当前 {len(long_cues)} 条超过 10 秒的段未切分。")
    if long_cues:
        for c in long_cues[:3]:
            print(f"          {c.duration_ms}ms  {c.text[:40]!r}")


if __name__ == "__main__":
    main()
