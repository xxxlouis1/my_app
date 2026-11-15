"""
GUI 对接桥接层（成员3 → 成员1 / 成员2）

背景
----
主项目 GUI 已实现双轨波形画布，其数据契约（从发布包反解确认）为：

    view.waveform_view.WaveformTrack(label: str, peaks: List[Tuple[float, float]],
                                     duration: float, sample_rate: int)
    view.waveform_panel.WaveformPanel.set_tracks(original, processed)
    view.waveform_panel.WaveformPanel.set_cues(cues)   # cues 为 SrtCue（毫秒）

其中 ``peaks`` 是 **每组 (min, max) 的包络序列**，值域 [-1, 1]。
本模块把 media_processing 的 FFmpeg 采样转换为该格式，补齐
"media_processing 未与 GUI 对接" 的缺口。

设计原则
--------
1. 不依赖 PyQt：可在无界面环境下单测（GUI 侧靠鸭子类型直接吃字段）。
2. 只做数据转换，不改 GUI 结构；GUI 侧只需 1 处调用替换。
3. 失败兜底：媒体不可读时返回全零包络，不抛异常打断界面。

用法（GUI 侧）
--------------
    from media_processing.gui_bridge import build_tracks, push_to_panel

    original, processed = build_tracks(mp4_path, processed_media_path=None)
    push_to_panel(self.waveform_panel, original, processed)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np

from .ffmpeg_wrapper import FFmpegWrapper
from .subtitle import Subtitle
from .waveform_processor import (
    WaveformProcessor,
    downsample_minmax,
    normalize_samples,
)

# GUI 波形默认列数（与 view.waveform_view 的绘制宽度上限匹配）
DEFAULT_TRACK_POINTS = 2000


@dataclass
class GuiWaveTrack:
    """
    与 view.waveform_view.WaveformTrack 字段一致的波形轨道数据。

    该对象可被 GUI 直接使用（鸭子类型）：画布只访问
    ``label`` / ``peaks`` / ``duration`` / ``sample_rate`` 四个属性。
    """

    label: str = ""
    peaks: List[Tuple[float, float]] = field(default_factory=list)
    duration: float = 0.0
    sample_rate: int = 0

    def __post_init__(self) -> None:
        # 保证 peaks 是真正的 Python list[tuple[float, float]]，
        # 避免 numpy 标量进入 QPainter 计算链。
        if not isinstance(self.peaks, list):
            self.peaks = [
                (float(p[0]), float(p[1])) for p in self.peaks
            ]

    def __len__(self) -> int:
        return len(self.peaks)


def _minmax_pairs(samples: np.ndarray, target_points: int) -> List[Tuple[float, float]]:
    """把采样数组压缩为 GUI 需要的 [(min, max), ...]。"""
    if samples.size == 0:
        return [(0.0, 0.0)] * max(1, target_points)
    normalized = normalize_samples(samples)
    envelope = downsample_minmax(normalized, target_points)
    return [(float(lo), float(hi)) for lo, hi in envelope]


def build_wave_track(
    media_path: str,
    label: str = "原始波形",
    target_points: int = DEFAULT_TRACK_POINTS,
    processor: Optional[WaveformProcessor] = None,
) -> GuiWaveTrack:
    """
    从媒体文件生成一条 GUI 波形轨道。

    :param media_path:    视频/音频文件路径（MP4/WAV 均可）
    :param label:         轨道标签（GUI 左上角显示）
    :param target_points: 包络列数
    :param processor:     可复用已有 WaveformProcessor（保留音频缓存）
    :raises WaveformGenerationError: 提取失败（调用方可改用 safe 版本）
    """
    if not os.path.isfile(media_path):
        raise FileNotFoundError(f"媒体文件不存在：{media_path}")

    wp = processor or WaveformProcessor(use_cache=True)
    samples, sr = wp.get_raw_samples(media_path)
    duration = float(len(samples) / sr) if sr > 0 else 0.0
    peaks = _minmax_pairs(np.asarray(samples), target_points)
    return GuiWaveTrack(
        label=label,
        peaks=peaks,
        duration=duration,
        sample_rate=int(sr),
    )


def build_wave_track_safe(
    media_path: str,
    label: str = "波形",
    target_points: int = DEFAULT_TRACK_POINTS,
    processor: Optional[WaveformProcessor] = None,
) -> GuiWaveTrack:
    """build_wave_track 的兜底版本：任何异常都返回全零包络。"""
    try:
        return build_wave_track(media_path, label, target_points, processor)
    except Exception:
        return GuiWaveTrack(
            label=label,
            peaks=[(0.0, 0.0)] * max(1, target_points),
            duration=0.0,
            sample_rate=0,
        )


def build_tracks(
    media_path: str,
    processed_media: Optional[str] = None,
    target_points: int = DEFAULT_TRACK_POINTS,
    processor: Optional[WaveformProcessor] = None,
) -> Tuple[GuiWaveTrack, GuiWaveTrack]:
    """
    生成双轨波形（画布第 1 轨 = 原始，第 2 轨 = Whisper 处理后）。

    :param media_path:       原始 MP4
    :param processed_media:  Whisper 处理后音频（WAV/MP4）；None 时第 2 轨
                             复用原始音频，保证界面有波形可交互。
    :return: (original_track, processed_track)
    """
    wp = processor or WaveformProcessor(use_cache=True)
    original = build_wave_track(
        media_path, label="原始波形", target_points=target_points, processor=wp
    )

    if processed_media and os.path.isfile(processed_media):
        processed = build_wave_track_safe(
            processed_media, label="处理后波形",
            target_points=target_points, processor=wp,
        )
        # 长度对齐：以原始时长为准，避免两轨断点错位
        processed.duration = original.duration or processed.duration
    else:
        processed = GuiWaveTrack(
            label="处理后波形",
            peaks=list(original.peaks),
            duration=original.duration,
            sample_rate=original.sample_rate,
        )
    return original, processed


def build_segment_track(
    media_path: str,
    start: float,
    end: float,
    label: str = "单句波形",
    target_points: int = 1000,
    processor: Optional[WaveformProcessor] = None,
) -> GuiWaveTrack:
    """按字幕时间切片生成单句波形轨道（第 2 分区）。"""
    wp = processor or WaveformProcessor(use_cache=True)
    start = max(0.0, float(start))
    end = max(start + 0.01, float(end))
    samples, sr = wp.get_raw_samples(media_path)
    start_idx = int(start * sr)
    end_idx = min(int(end * sr), len(samples))
    segment = np.asarray(samples[max(0, start_idx):max(start_idx + 1, end_idx)])
    return GuiWaveTrack(
        label=label,
        peaks=_minmax_pairs(segment, target_points),
        duration=end - start,
        sample_rate=int(sr),
    )


def to_gui_track(track: GuiWaveTrack) -> Any:
    """
    尝试用主项目真实的 WaveformTrack 类包装（GUI 已导入时）。
    失败则原样返回（鸭子类型兼容）。
    """
    try:
        from view.waveform_view import WaveformTrack  # type: ignore

        return WaveformTrack(
            label=track.label,
            peaks=list(track.peaks),
            duration=track.duration,
            sample_rate=track.sample_rate,
        )
    except Exception:
        return track


def push_to_panel(panel: Any, original: GuiWaveTrack, processed: GuiWaveTrack) -> None:
    """把双轨数据推给 WaveformPanel.set_tracks（GUI 唯一需要改动的接线点）。"""
    if panel is None or not hasattr(panel, "set_tracks"):
        raise AttributeError("panel 缺少 set_tracks 方法，无法对接波形面板")
    panel.set_tracks(to_gui_track(original), to_gui_track(processed))


def cues_from_subtitles(
    subtitles: Sequence[Subtitle],
    cue_factory: Optional[Any] = None,
) -> List[Any]:
    """
    把字幕对象转换为 GUI 的 SrtCue（毫秒口径），供 set_cues 使用。

    :param subtitles:   Subtitle 列表（秒口径）
    :param cue_factory: 可选，SrtCue 构造器；缺省时尝试
                        ``from view.srt_parser import SrtCue``，再退化为 dict。
    """
    factory = cue_factory
    if factory is None:
        try:
            from view.srt_parser import SrtCue  # type: ignore

            factory = SrtCue
        except Exception:
            factory = None

    cues: List[Any] = []
    for order, sub in enumerate(subtitles, start=1):
        start_ms = int(round(sub.start * 1000))
        end_ms = int(round(sub.end * 1000))
        cues.append(
            _make_cue(
                factory, order, start_ms, end_ms, sub.start, sub.end, sub.text
            )
        )
    return cues


def _make_cue(factory, order, start_ms, end_ms, start_s, end_s, text):
    if factory is not None:
        try:
            return factory(
                index=order,
                start=_fmt_ts(start_s),
                end=_fmt_ts(end_s),
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
            )
        except Exception:
            pass
    return {
        "index": order,
        "start": _fmt_ts(start_s),
        "end": _fmt_ts(end_s),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "text": text,
    }


def _fmt_ts(seconds: float) -> str:
    """秒 → 'HH:MM:SS,mmm'（GUI 展示口径）。"""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
