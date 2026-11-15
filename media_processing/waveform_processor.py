"""
波形数据处理器

职责（本周任务核心）：
1. 从 MP4 提取音频，生成全局完整波形数据（第一区域总波形）
2. 根据字幕时间切片音频，生成单句片段波形（第二区域选中波形）
3. 波形数据归一化、采样压缩，优化渲染性能
4. 波形数据缓存，避免重复提取音频；处理音频异常、采样失败兜底
5. 输出波形采样点数组，供 GUI 绘制

时间基准统一：所有波形均使用 sample_rate=16000 单声道作为基准，
保证原始波形与 Whisper 处理后波形可在同一时间轴上对齐。
"""

import os
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

from .exceptions import NoAudioStreamError, WaveformGenerationError
from .ffmpeg_wrapper import FFmpegWrapper
from .subtitle import FullWaveform, WaveformSegment

# 统一基准采样率（与 Whisper 输入一致，保证时间轴对齐）
BASE_SAMPLE_RATE = 16000
BASE_CHANNELS = 1

# 默认压缩后的全局波形采样点数
DEFAULT_FULL_SAMPLES = 4000
# 默认单句波形采样点数
DEFAULT_SEGMENT_SAMPLES = 1000


def normalize_samples(samples: np.ndarray) -> np.ndarray:
    """
    将采样值归一化到 [-1.0, 1.0]。
    若全为 0 或最大幅值极小，返回原样（避免除零）。
    """
    if samples.size == 0:
        return samples
    max_abs = float(np.max(np.abs(samples)))
    if max_abs < 1e-8:
        return samples.astype(np.float32)
    return (samples.astype(np.float32) / max_abs).astype(np.float32)


def downsample_peaks(samples: np.ndarray, target_count: int) -> np.ndarray:
    """
    峰值降采样：将采样点压缩到 target_count 个点。
    每个 bin 取绝对值最大的样本值（保留正负号），适合波形可视化。

    :param samples:      原始采样数组（float）
    :param target_count: 目标点数
    :return: 压缩后的数组
    """
    if samples.size == 0:
        return np.zeros(target_count, dtype=np.float32)
    if target_count <= 0:
        return np.array([], dtype=np.float32)
    if samples.size <= target_count:
        # 点数不足，用零填充到 target_count
        out = np.zeros(target_count, dtype=np.float32)
        out[: samples.size] = samples.astype(np.float32)
        return out

    # 分段，每段取峰值（带符号）—— numpy 向量化（reshape）实现
    n = samples.size
    bin_size = n // target_count
    if bin_size < 1:
        bin_size = 1
    # 截断到整数倍，丢弃尾部最多 target_count-1 个采样点（对波形可视化无影响）
    trimmed = bin_size * target_count
    reshaped = samples[:trimmed].reshape(target_count, bin_size)
    abs_r = np.abs(reshaped)
    # 每个 bin 取绝对值最大的样本值（保留符号）
    idx = np.argmax(abs_r, axis=1)
    out = reshaped[np.arange(target_count), idx]
    return out.astype(np.float32)


def downsample_minmax(samples: np.ndarray, target_count: int) -> np.ndarray:
    """
    Min/Max 包络降采样（GUI 波形绘制标准格式）。

    将采样点压缩为 target_count 组 (min, max)，与主流波形控件
    （如主项目 view.waveform_view.WaveformTrack.peaks）保持一致。

    :param samples:      原始采样数组（建议已归一化到 [-1, 1]）
    :param target_count: 目标列数
    :return: shape=(target_count, 2) 的 float32 数组，每行为 (min, max)
    """
    if target_count <= 0:
        return np.zeros((0, 2), dtype=np.float32)
    if samples.size == 0:
        return np.zeros((target_count, 2), dtype=np.float32)

    samples = samples.astype(np.float32, copy=False)
    n = samples.size
    if n < target_count:
        # 点数不足：每组一个样本，空位补零
        out = np.zeros((target_count, 2), dtype=np.float32)
        out[:n, 0] = samples
        out[:n, 1] = samples
        return out

    bin_size = n // target_count
    if bin_size < 1:
        bin_size = 1
    trimmed = bin_size * target_count
    reshaped = samples[:trimmed].reshape(target_count, bin_size)

    out = np.empty((target_count, 2), dtype=np.float32)
    out[:, 0] = reshaped.min(axis=1)
    out[:, 1] = reshaped.max(axis=1)
    return out


class WaveformProcessor:
    """
    波形数据处理器。

    支持两种使用模式：
    - 无缓存：每次调用都重新提取音频（简单、内存占用低）
    - 有缓存：首次提取完整音频后缓存原始 PCM，后续片段直接切片，速度快

    GUI 应使用缓存模式以提升交互流畅度。
    """

    def __init__(
        self,
        ffmpeg: Optional[FFmpegWrapper] = None,
        use_cache: bool = True,
        base_sample_rate: int = BASE_SAMPLE_RATE,
    ):
        self._ffmpeg = ffmpeg or FFmpegWrapper()
        self._use_cache = use_cache
        self._base_sample_rate = base_sample_rate

        # 缓存：video_path -> (samples, sample_rate)
        self._cache: Dict[str, Tuple[np.ndarray, int]] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 缓存管理
    # ------------------------------------------------------------------
    def _load_full_audio(self, video_path: str) -> Tuple[np.ndarray, int]:
        """加载完整音频（带缓存）"""
        key = os.path.abspath(video_path)
        with self._cache_lock:
            if key in self._cache:
                return self._cache[key]

        try:
            samples, sr = self._ffmpeg.extract_audio_samples(
                video_path,
                sample_rate=self._base_sample_rate,
                channels=BASE_CHANNELS,
            )
        except NoAudioStreamError:
            raise
        except Exception as e:
            raise WaveformGenerationError(f"音频提取失败: {e}")

        if self._use_cache:
            with self._cache_lock:
                self._cache[key] = (samples, sr)
        return samples, sr

    def clear_cache(self, video_path: Optional[str] = None) -> None:
        """
        清空缓存。

        :param video_path: 指定文件则只清该文件缓存，None 清空全部
        """
        with self._cache_lock:
            if video_path is None:
                self._cache.clear()
            else:
                self._cache.pop(os.path.abspath(video_path), None)

    @property
    def cache_size(self) -> int:
        """当前缓存的文件数"""
        with self._cache_lock:
            return len(self._cache)

    # ------------------------------------------------------------------
    # 全局完整波形（第一区域）
    # ------------------------------------------------------------------
    def get_full_waveform(
        self,
        video_path: str,
        target_samples: int = DEFAULT_FULL_SAMPLES,
    ) -> FullWaveform:
        """
        生成全局完整波形数据。

        :param video_path:     视频文件路径
        :param target_samples: 压缩后的采样点数（默认 4000，平衡精度与性能）
        :return: FullWaveform
        :raises WaveformGenerationError: 生成失败
        """
        samples, sr = self._load_full_audio(video_path)
        duration = len(samples) / sr if sr > 0 else 0.0

        try:
            normalized = normalize_samples(samples)
            compressed = downsample_peaks(normalized, target_samples)
        except Exception as e:
            raise WaveformGenerationError(f"波形压缩失败: {e}")

        return FullWaveform(
            samples=compressed.tolist(),
            duration=duration,
            sample_rate=sr,
            target_samples=target_samples,
        )

    # ------------------------------------------------------------------
    # 单句片段波形（第二区域）
    # ------------------------------------------------------------------
    def get_segment_waveform(
        self,
        video_path: str,
        start: float,
        end: float,
        subtitle_index: int = -1,
        target_samples: int = DEFAULT_SEGMENT_SAMPLES,
    ) -> WaveformSegment:
        """
        根据字幕起止时间生成单句片段波形。

        :param video_path:     视频文件路径
        :param start:          起始时间（秒）
        :param end:            结束时间（秒）
        :param subtitle_index: 对应字幕序号（用于 GUI 标记）
        :param target_samples: 压缩后的采样点数
        :return: WaveformSegment
        :raises WaveformGenerationError: 生成失败
        """
        if start < 0:
            start = 0.0
        if end <= start:
            # 兜底：给一个极短片段
            end = start + 0.01

        samples, sr = self._load_full_audio(video_path)
        duration = len(samples) / sr if sr > 0 else 0.0

        # 时间转采样索引
        start_idx = int(start * sr)
        end_idx = int(end * sr)
        if start_idx < 0:
            start_idx = 0
        if end_idx > len(samples):
            end_idx = len(samples)
        if end_idx <= start_idx:
            end_idx = min(start_idx + 1, len(samples))

        segment = samples[start_idx:end_idx]

        try:
            normalized = normalize_samples(segment)
            compressed = downsample_peaks(normalized, target_samples)
        except Exception as e:
            raise WaveformGenerationError(f"片段波形压缩失败: {e}")

        return WaveformSegment(
            subtitle_index=subtitle_index,
            start=start,
            end=min(end, duration),
            samples=compressed.tolist(),
            sample_rate=sr,
        )

    def get_segment_waveform_batch(
        self,
        video_path: str,
        ranges: List[Tuple[int, float, float]],
        target_samples: int = DEFAULT_SEGMENT_SAMPLES,
    ) -> List[WaveformSegment]:
        """
        批量生成多个字幕片段波形（一次加载音频，多次切片，效率更高）。

        :param ranges: [(subtitle_index, start, end), ...]
        :return: WaveformSegment 列表
        """
        # 先触发一次完整音频加载（缓存）
        self._load_full_audio(video_path)
        return [
            self.get_segment_waveform(
                video_path,
                start=s,
                end=e,
                subtitle_index=idx,
                target_samples=target_samples,
            )
            for idx, s, e in ranges
        ]

    # ------------------------------------------------------------------
    # 原始采样数据（供高级用途，如 Whisper 对齐）
    # ------------------------------------------------------------------
    def get_raw_samples(self, video_path: str) -> Tuple[np.ndarray, int]:
        """
        获取原始 PCM 采样数据（未压缩），供 Whisper 等模块直接使用。

        :return: (samples, sample_rate)
        """
        return self._load_full_audio(video_path)

    def get_audio_duration(self, video_path: str) -> float:
        """获取音频总时长（秒）"""
        samples, sr = self._load_full_audio(video_path)
        return len(samples) / sr if sr > 0 else 0.0

    # ------------------------------------------------------------------
    # 双轨波形：从音频文件加载（Whisper 处理后音频）
    # ------------------------------------------------------------------
    def get_full_waveform_from_audio(
        self,
        audio_path: str,
        target_samples: int = DEFAULT_FULL_SAMPLES,
    ) -> FullWaveform:
        """
        从音频文件（WAV/MP3 等）生成全局波形，用于双轨对比展示中的
        "处理后波形"轨道（如 Whisper 输出的处理后音频）。

        与 get_full_waveform 使用相同的基准采样率，保证时间轴对齐。

        :param audio_path:     音频文件路径
        :param target_samples: 压缩后的采样点数
        :return: FullWaveform
        """
        # extract_audio_samples 内部用 ffprobe 探测，对纯音频文件同样适用
        try:
            samples, sr = self._ffmpeg.extract_audio_samples(
                audio_path,
                sample_rate=self._base_sample_rate,
                channels=BASE_CHANNELS,
            )
        except NoAudioStreamError:
            raise
        except Exception as e:
            raise WaveformGenerationError(f"处理后音频提取失败: {e}")

        duration = len(samples) / sr if sr > 0 else 0.0
        normalized = normalize_samples(samples)
        compressed = downsample_peaks(normalized, target_samples)

        return FullWaveform(
            samples=compressed.tolist(),
            duration=duration,
            sample_rate=sr,
            target_samples=target_samples,
        )

    # ------------------------------------------------------------------
    # 容错兜底：失败时返回零数组，避免 GUI 崩溃
    # ------------------------------------------------------------------
    def safe_get_full_waveform(
        self,
        video_path: str,
        target_samples: int = DEFAULT_FULL_SAMPLES,
    ) -> FullWaveform:
        """
        容错版全局波形：生成失败时返回全零数组，不抛异常。
        GUI 应优先调用此方法，保证界面始终可渲染。
        """
        try:
            return self.get_full_waveform(video_path, target_samples)
        except Exception:
            return FullWaveform(
                samples=[0.0] * target_samples,
                duration=0.0,
                sample_rate=self._base_sample_rate,
                target_samples=target_samples,
            )

    def safe_get_segment_waveform(
        self,
        video_path: str,
        start: float,
        end: float,
        subtitle_index: int = -1,
        target_samples: int = DEFAULT_SEGMENT_SAMPLES,
    ) -> WaveformSegment:
        """
        容错版片段波形：生成失败时返回全零数组，不抛异常。
        """
        try:
            return self.get_segment_waveform(
                video_path, start, end, subtitle_index, target_samples
            )
        except Exception:
            return WaveformSegment(
                subtitle_index=subtitle_index,
                start=start,
                end=end,
                samples=[0.0] * target_samples,
                sample_rate=self._base_sample_rate,
            )

    # ------------------------------------------------------------------
    # 时间 ↔ 采样索引映射（供 GUI 绘制字幕断点）
    # ------------------------------------------------------------------
    @staticmethod
    def time_to_sample_index(time_sec: float, sample_rate: int) -> int:
        """
        将时间（秒）转换为原始采样数组的索引。
        GUI 可据此在波形上定位字幕断点位置。
        """
        return max(0, int(time_sec * sample_rate))

    @staticmethod
    def sample_index_to_time(index: int, sample_rate: int) -> float:
        """
        将采样索引转换回时间（秒）。
        GUI 拖拽断点后，用此方法把新位置换算成时间戳更新字幕。
        """
        return index / sample_rate if sample_rate > 0 else 0.0

    @staticmethod
    def time_to_waveform_x(
        time_sec: float, duration: float, waveform_width_px: int
    ) -> int:
        """
        将时间映射到波形画布的 X 坐标（像素）。
        GUI 绘制断点时直接使用。

        :param time_sec:           时间（秒）
        :param duration:           音频总时长（秒）
        :param waveform_width_px:  波形画布宽度（像素）
        :return: X 坐标
        """
        if duration <= 0:
            return 0
        ratio = max(0.0, min(1.0, time_sec / duration))
        return int(ratio * waveform_width_px)

    @staticmethod
    def waveform_x_to_time(
        x_px: int, duration: float, waveform_width_px: int
    ) -> float:
        """
        将波形画布 X 坐标（像素）反算为时间（秒）。
        GUI 拖拽断点后换算新时间戳。
        """
        if waveform_width_px <= 0:
            return 0.0
        ratio = max(0.0, min(1.0, x_px / waveform_width_px))
        return ratio * duration
