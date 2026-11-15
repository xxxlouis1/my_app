"""
音视频处理模块（FFmpeg 封装）

本模块负责：
1. MP4 音视频流提取、视频信息探测
2. 字幕烧录 / 内封到 MP4
3. 标准 SRT 文件导出（遵循 Aboboo 规范）
4. 波形数据生成（全局波形、单句片段波形）、归一化、采样压缩与缓存
5. 音视频异常捕获与抛出

依赖：
- ffmpeg / ffprobe 可执行文件（优先系统 PATH，其次 imageio-ffmpeg）
- numpy
"""

from .exceptions import (
    MediaProcessingError,
    FFmpegNotFoundError,
    VideoCorruptedError,
    NoAudioStreamError,
    NoVideoStreamError,
    AudioExtractionError,
    WaveformGenerationError,
    SRTExportError,
    SubtitleBurnError,
)
from .subtitle import Subtitle
from .ffmpeg_wrapper import FFmpegWrapper
from .waveform_processor import WaveformProcessor
from .srt_exporter import SRTExporter, format_timestamp
from .gui_bridge import (
    GuiWaveTrack,
    build_wave_track,
    build_wave_track_safe,
    build_tracks,
    build_segment_track,
    push_to_panel,
    to_gui_track,
    cues_from_subtitles,
)
from .video_preview import VideoFrameReader, create_playback_controller

__all__ = [
    # exceptions
    "MediaProcessingError",
    "FFmpegNotFoundError",
    "VideoCorruptedError",
    "NoAudioStreamError",
    "NoVideoStreamError",
    "AudioExtractionError",
    "WaveformGenerationError",
    "SRTExportError",
    "SubtitleBurnError",
    # data
    "Subtitle",
    # core
    "FFmpegWrapper",
    "WaveformProcessor",
    "SRTExporter",
    "format_timestamp",
    # GUI 对接
    "GuiWaveTrack",
    "build_wave_track",
    "build_wave_track_safe",
    "build_tracks",
    "build_segment_track",
    "push_to_panel",
    "to_gui_track",
    "cues_from_subtitles",
    # 视频预览
    "VideoFrameReader",
    "create_playback_controller",
]

__version__ = "1.0.0"
