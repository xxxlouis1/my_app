# -*- coding: utf-8 -*-
"""处理实例视频并逐级校验（HEVC / 14 分钟 / 720x580）。

用法：
    python scripts/process_example.py "Muzzy Dvd1-2.mp4" [--model large-v3] [--cpu]
"""
import argparse
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from model.asr import AsrEngine                        # noqa: E402
from model.error_detect import detect_errors           # noqa: E402
from model.pipeline import extract_audio, process      # noqa: E402
from model.subtitle_fix import FixConfig, self_check   # noqa: E402
from model.waveform import extract_boundaries, filter_speech  # noqa: E402


def _save_transcript(tr, path):
    """把转写结果存成 JSON，便于调参时避免重复跑 ASR（每次约 77s）。"""
    import json
    data = {
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text,
             "avg_logprob": s.avg_logprob, "no_speech_prob": s.no_speech_prob,
             "compression_ratio": s.compression_ratio,
             "temperature": s.temperature,
             "words": [{"start": w.start, "end": w.end, "text": w.text,
                        "probability": w.probability} for w in s.words]}
            for s in tr.segments],
        "language": tr.language, "duration": tr.duration,
        "model": tr.model, "device": tr.device,
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return tr


def _load_transcript(path):
    import json
    from model.asr import Segment, Transcript, Word
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    segs = []
    for s in d["segments"]:
        segs.append(Segment(
            start=s["start"], end=s["end"], text=s["text"],
            words=[Word(start=w["start"], end=w["end"], text=w["text"],
                        probability=w.get("probability"))
                   for w in s.get("words", [])],
            avg_logprob=s.get("avg_logprob"),
            no_speech_prob=s.get("no_speech_prob"),
            compression_ratio=s.get("compression_ratio"),
            temperature=s.get("temperature")))
    return Transcript(segments=segs, language=d.get("language", "en"),
                      language_probability=1.0, duration=d.get("duration", 0.0),
                      elapsed=0.0, model=d.get("model", "cached"),
                      device=d.get("device", "cached"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?", default=str(ROOT / "Muzzy Dvd1-2.mp4"))
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--language", default="en")
    ap.add_argument("--no-cache", action="store_true",
                    help="忽略转写缓存，强制重新跑 ASR")
    args = ap.parse_args()

    video = Path(args.video)
    out_dir = ROOT / "test_output" / "muzzy"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 76)
    print(f"处理实例视频：{video.name}")
    print("=" * 76)

    # ---------- 1. 抽音频
    t0 = time.time()
    wav = out_dir / f"{video.stem}.wav"
    if not wav.exists():
        print("\n[1] 提取音频（16kHz 单声道）…")
        extract_audio(str(video), str(wav))
    print(f"    {wav.name}  {wav.stat().st_size/1024/1024:.1f} MB  "
          f"（{time.time()-t0:.1f}s）")

    # ---------- 2. 波形边界
    print("\n[2] 分析语音边界…")
    b = extract_boundaries(str(wav))
    print(f"    {b.summary()}")
    # 锚点阈值必须与吸附/检测用的 FixConfig 保持一致，
    # 否则命中率统计会拿一套锚点去衡量另一套吸附结果（会低估）。
    _cfg0 = FixConfig()
    anchors = filter_speech(b.speech, _cfg0.min_anchor_speech)
    print(f"    有效锚点 {len(anchors)} 个"
          f"（阈值 {_cfg0.min_anchor_speech*1000:.0f}ms）")

    # ---------- 3. ASR
    from model.gpu_env import gpu_available
    device = "cpu" if args.cpu else ("cuda" if gpu_available() else "cpu")
    ctype = "int8" if device == "cpu" else "int8_float16"
    print(f"\n[3] ASR 转写（{args.model} / {device} / {ctype}）…")
    cache = out_dir / f"{video.stem}.transcript.json"
    if cache.exists() and not args.no_cache:
        print(f"    复用缓存：{cache.name}")
        tr = _load_transcript(cache)
    else:
        eng = AsrEngine(model=args.model, device=device, compute_type=ctype,
                        language=args.language)
        tr = eng.transcribe(str(wav), language=args.language)
        _save_transcript(tr, cache)
    print(f"    {tr.summary()}")
    quality = {"ok": 0, "suspect": 0, "bad": 0}
    for s in tr.segments:
        quality[s.quality()] += 1
    print(f"    片段质量分布：{quality}")

    # ---------- 4. 校正
    print("\n[4] 字幕校正…")
    from model.subtitle_fix import fix_subtitles, write_srt
    cues, rep = fix_subtitles(tr.segments, b, FixConfig())
    print(f"    {len(cues)} 条  |  {rep.summary()}")

    srt = out_dir / f"{video.stem}.srt"
    write_srt(cues, str(srt))
    print(f"    已写出：{srt}")

    # ---------- 5. 校验
    print("\n[5] 校验")
    probs = self_check(cues, FixConfig())
    print(f"    结构自检：{'通过' if not probs else f'{len(probs)} 项问题'}")
    err = detect_errors(cues, b, media_duration=b.duration)
    print(f"    错误检测：{err.summary()}")
    for t, n in err.by_type().items():
        print(f"        {t}: {n}")

    onsets = [a for a, _ in anchors]
    dev = [min(abs(c.start - o) for o in onsets) for c in cues]
    if dev:
        exact = sum(1 for d in dev if d <= 0.005)
        print(f"    起点精确命中：{exact}/{len(cues)} = {exact/len(cues)*100:.1f}%"
              f"（中位 {statistics.median(dev)*1000:.0f}ms）")

    # 独立校验器
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_srt.py"),
                        str(srt), "--media", str(video)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    import re
    m = re.search(r"结果：(\d+) 个 ERROR，(\d+) 个 WARN", out)
    if m:
        print(f"    check_srt：{m.group(1)} ERROR / {m.group(2)} WARN")
    else:
        print(f"    check_srt 退出码 {r.returncode}")

    # 文本完整性
    import re as _re
    from collections import Counter
    def words(seq):
        o = []
        for t in seq:
            o += _re.findall(r"[A-Za-z0-9']+", t.lower())
        return o
    wb = words([s.text for s in tr.segments])
    wa = words([c.text for c in cues])
    lost = sum((Counter(wb) - Counter(wa)).values())
    print(f"    文本完整性：{len(wb)} -> {len(wa)} 词，丢失 {lost} 词次")

    print(f"\n    总耗时 {time.time()-t0:.1f}s（不含抽音频/转写）")
    print(f"    产出：{srt}")


if __name__ == "__main__":
    main()
