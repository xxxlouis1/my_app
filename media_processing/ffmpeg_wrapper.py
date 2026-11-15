"""
FFmpeg 封装层

职责：
1. 定位 ffmpeg / ffprobe 可执行文件（系统 PATH 优先，其次 imageio-ffmpeg）
2. 视频信息探测（时长、音视频流存在性、编码格式）
3. 音频提取（输出 PCM 采样数据，供波形渲染 / Whisper 识别）
4. 字幕烧录（硬字幕）/ 内封（软字幕）到 MP4
5. 统一异常捕获与抛出
"""

import json
import os
import shutil
import subprocess
from typing import List, Optional, Tuple

import numpy as np

from .exceptions import (
    AudioExtractionError,
    FFmpegNotFoundError,
    NoAudioStreamError,
    NoVideoStreamError,
    SubtitleBurnError,
    VideoCorruptedError,
)


def _find_executable(name: str) -> Optional[str]:
    """在系统 PATH 中查找可执行文件"""
    found = shutil.which(name)
    if found:
        return found
    # Windows 下常见的 .exe 后缀
    if not name.endswith(".exe"):
        found = shutil.which(f"{name}.exe")
        if found:
            return found
    return None


def _get_ffmpeg_exe() -> str:
    """获取 ffmpeg 可执行文件路径"""
    path = _find_executable("ffmpeg")
    if path:
        return path
    # 回退：imageio-ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise FFmpegNotFoundError()


def _get_ffprobe_exe() -> str:
    """获取 ffprobe 可执行文件路径"""
    path = _find_executable("ffprobe")
    if path:
        return path
    # imageio-ffmpeg 只带 ffmpeg，不带 ffprobe；尝试同目录查找
    try:
        import imageio_ffmpeg

        ffmpeg_dir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
        candidate = os.path.join(ffmpeg_dir, "ffprobe.exe")
        if os.path.exists(candidate):
            return candidate
    except Exception:
        pass
    raise FFmpegNotFoundError("未找到 ffprobe，请安装完整的 ffmpeg 套件")


class VideoInfo:
    """视频探测结果"""

    def __init__(
        self,
        path: str,
        duration: float,
        has_audio: bool,
        has_video: bool,
        audio_codec: Optional[str] = None,
        video_codec: Optional[str] = None,
        sample_rate: Optional[int] = None,
        channels: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: float = 0.0,
    ):
        self.path = path
        self.duration = duration
        self.has_audio = has_audio
        self.has_video = has_video
        self.audio_codec = audio_codec
        self.video_codec = video_codec
        self.sample_rate = sample_rate
        self.channels = channels
        self.width = width
        self.height = height
        self.fps = fps

    def __repr__(self) -> str:
        return (
            f"VideoInfo(path={self.path!r}, duration={self.duration:.3f}s, "
            f"audio={self.has_audio}, video={self.has_video})"
        )


class FFmpegWrapper:
    """FFmpeg 操作封装"""

    def __init__(self, ffmpeg_path: Optional[str] = None, ffprobe_path: Optional[str] = None):
        self._ffmpeg = ffmpeg_path or _get_ffmpeg_exe()
        self._ffprobe = ffprobe_path or _get_ffprobe_exe()

    # ------------------------------------------------------------------
    # 视频探测
    # ------------------------------------------------------------------
    def probe(self, video_path: str) -> VideoInfo:
        """
        探测视频信息。

        :raises VideoCorruptedError: 文件无法解析
        :raises NoAudioStreamError:  无音频流（且业务需要音频时调用方自行判断）
        :raises NoVideoStreamError:  无视频流
        """
        if not os.path.isfile(video_path):
            raise VideoCorruptedError(video_path, "文件不存在")

        cmd = [
            self._ffprobe,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            video_path,
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise VideoCorruptedError(video_path, e.stderr.decode("utf-8", errors="ignore"))
        except FileNotFoundError:
            raise FFmpegNotFoundError()

        try:
            data = json.loads(result.stdout.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError as e:
            raise VideoCorruptedError(video_path, f"ffprobe 输出解析失败: {e}")

        streams = data.get("streams", [])
        fmt = data.get("format", {})

        duration = 0.0
        try:
            duration = float(fmt.get("duration", 0.0))
        except (TypeError, ValueError):
            duration = 0.0

        has_audio = False
        has_video = False
        audio_codec = None
        video_codec = None
        sample_rate = None
        channels = None
        width = None
        height = None
        fps = 0.0

        for stream in streams:
            codec_type = stream.get("codec_type")
            if codec_type == "audio":
                has_audio = True
                audio_codec = stream.get("codec_name")
                try:
                    sample_rate = int(stream.get("sample_rate", 0)) or None
                except (TypeError, ValueError):
                    sample_rate = None
                try:
                    channels = int(stream.get("channels", 0)) or None
                except (TypeError, ValueError):
                    channels = None
            elif codec_type == "video":
                has_video = True
                video_codec = stream.get("codec_name")
                try:
                    width = int(stream.get("width", 0)) or None
                except (TypeError, ValueError):
                    width = None
                try:
                    height = int(stream.get("height", 0)) or None
                except (TypeError, ValueError):
                    height = None
                # 帧率：优先 avg_frame_rate（如 "25/1"），回退 r_frame_rate
                for key in ("avg_frame_rate", "r_frame_rate"):
                    raw_rate = stream.get(key)
                    if not raw_rate or raw_rate in ("0/0", "0"):
                        continue
                    try:
                        if "/" in raw_rate:
                            num, den = raw_rate.split("/", 1)
                            den_val = float(den)
                            if den_val > 0:
                                fps = float(num) / den_val
                        else:
                            fps = float(raw_rate)
                    except (TypeError, ValueError, ZeroDivisionError):
                        fps = 0.0
                    if fps > 0:
                        break

        info = VideoInfo(
            path=video_path,
            duration=duration,
            has_audio=has_audio,
            has_video=has_video,
            audio_codec=audio_codec,
            video_codec=video_codec,
            sample_rate=sample_rate,
            channels=channels,
            width=width,
            height=height,
            fps=fps,
        )
        return info

    def require_audio(self, video_path: str) -> VideoInfo:
        """探测并确保视频含音频流，否则抛出 NoAudioStreamError"""
        info = self.probe(video_path)
        if not info.has_audio:
            raise NoAudioStreamError(video_path)
        return info

    # ------------------------------------------------------------------
    # 音频提取（PCM 采样数据）
    # ------------------------------------------------------------------
    def extract_audio_samples(
        self,
        video_path: str,
        sample_rate: int = 16000,
        channels: int = 1,
        start: Optional[float] = None,
        end: Optional[float] = None,
    ) -> Tuple[np.ndarray, int]:
        """
        提取音频 PCM 采样数据，返回 (float32 数组, 采样率)。
        采样值归一化到 [-1.0, 1.0]。

        :param video_path:   视频文件路径
        :param sample_rate:  目标采样率（默认 16000，Whisper 标准）
        :param channels:     目标声道数（默认 1 单声道）
        :param start:        起始时间（秒），None 表示从头
        :param end:          结束时间（秒），None 表示到尾
        :return: (samples, sample_rate)
        :raises AudioExtractionError: 提取失败
        """
        info = self.require_audio(video_path)

        cmd = [self._ffmpeg, "-v", "error"]

        # 切片参数：-ss 放在 -i 前更快（关键帧定位），精度足够波形显示
        if start is not None and start > 0:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-i", video_path]
        if end is not None and end > 0:
            # 使用 -to 或 -t
            if start is not None and start > 0:
                cmd += ["-t", f"{(end - start):.3f}"]
            else:
                cmd += ["-to", f"{end:.3f}"]

        cmd += [
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", str(channels),
            "-f", "s16le",
            "-",
        ]

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise AudioExtractionError(e.stderr.decode("utf-8", errors="ignore"))
        except FileNotFoundError:
            raise FFmpegNotFoundError()

        raw = result.stdout
        if not raw:
            raise AudioExtractionError("ffmpeg 未输出音频数据")

        # pcm_s16le -> int16 -> float32 [-1, 1]
        try:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception as e:
            raise AudioExtractionError(f"PCM 数据解析失败: {e}")

        if samples.size == 0:
            raise AudioExtractionError("提取到的音频采样为空")

        return samples, sample_rate

    def extract_audio_to_wav(
        self,
        video_path: str,
        output_path: str,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> str:
        """
        提取音频并保存为 WAV 文件（供 Whisper 等外部工具使用）。

        :return: 输出 WAV 文件路径
        """
        self.require_audio(video_path)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        cmd = [
            self._ffmpeg, "-v", "error", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", str(channels),
            output_path,
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError as e:
            raise AudioExtractionError(e.stderr.decode("utf-8", errors="ignore"))
        except FileNotFoundError:
            raise FFmpegNotFoundError()

        if not os.path.isfile(output_path):
            raise AudioExtractionError(f"WAV 文件未生成: {output_path}")
        return output_path

    # ------------------------------------------------------------------
    # 字幕烧录 / 内封
    # ------------------------------------------------------------------
    def burn_subtitles(
        self,
        video_path: str,
        srt_path: str,
        output_path: str,
        font_name: str = "Arial",
        font_size: int = 24,
    ) -> str:
        """
        将 SRT 字幕烧录（硬字幕）到 MP4 视频中。

        :param video_path:  输入视频路径
        :param srt_path:    SRT 字幕路径
        :param output_path: 输出 MP4 路径
        :param font_name:   字体名
        :param font_size:   字号
        :return: 输出文件路径
        :raises SubtitleBurnError: 烧录失败
        """
        if not os.path.isfile(srt_path):
            raise SubtitleBurnError(f"SRT 文件不存在: {srt_path}")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        # Windows 路径中的反斜杠在 subtitles 滤镜中需要转义
        srt_path_escaped = srt_path.replace("\\", "/").replace(":", r"\:")
        vf = (
            f"subtitles='{srt_path_escaped}'"
            f":force_style='FontName={font_name},FontSize={font_size}'"
        )

        cmd = [
            self._ffmpeg, "-v", "error", "-y",
            "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-c:a", "aac",
            "-b:a", "192k",
            output_path,
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError as e:
            raise SubtitleBurnError(e.stderr.decode("utf-8", errors="ignore"))
        except FileNotFoundError:
            raise FFmpegNotFoundError()

        if not os.path.isfile(output_path):
            raise SubtitleBurnError(f"烧录后视频未生成: {output_path}")
        return output_path

    def embed_subtitles(
        self,
        video_path: str,
        srt_path: str,
        output_path: str,
        language: str = "chi",
    ) -> str:
        """
        将 SRT 字幕内封（软字幕 / mov_text）到 MP4 中，不重新编码画面。

        :param video_path:  输入视频路径
        :param srt_path:    SRT 字幕路径
        :param output_path: 输出 MP4 路径
        :param language:    字幕语言码（ISO 639-2，如 chi / eng）
        :return: 输出文件路径
        """
        if not os.path.isfile(srt_path):
            raise SubtitleBurnError(f"SRT 文件不存在: {srt_path}")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        cmd = [
            self._ffmpeg, "-v", "error", "-y",
            "-i", video_path,
            "-i", srt_path,
            "-c:v", "copy",
            "-c:a", "copy",
            "-c:s", "mov_text",
            "-metadata:s:s:0", f"language={language}",
            output_path,
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError as e:
            raise SubtitleBurnError(e.stderr.decode("utf-8", errors="ignore"))
        except FileNotFoundError:
            raise FFmpegNotFoundError()

        if not os.path.isfile(output_path):
            raise SubtitleBurnError(f"内封字幕后视频未生成: {output_path}")
        return output_path

    # ------------------------------------------------------------------
    # 一体化导出：矫正后 MP4 + SRT
    # ------------------------------------------------------------------
    def export_corrected(
        self,
        video_path: str,
        subtitles,
        output_dir: str,
        base_name: str = "corrected",
        mode: str = "embed",
        srt_encoding: str = "utf-8-sig",
        **kwargs,
    ):
        """
        一体化导出：将字幕写入 SRT 文件，并生成带字幕的 MP4。

        :param video_path:  原始视频路径
        :param subtitles:   Subtitle 列表（来自业务层）
        :param output_dir:  输出目录
        :param base_name:   输出文件名前缀（不带扩展名）
        :param mode:        "burn" 烧录硬字幕 / "embed" 内封软字幕（默认）
        :param srt_encoding: SRT 文件编码
        :param kwargs:      透传给 burn_subtitles / embed_subtitles 的参数
        :return: (srt_path, mp4_path)
        """
        from .srt_exporter import SRTExporter

        os.makedirs(output_dir, exist_ok=True)
        srt_path = os.path.join(output_dir, f"{base_name}.srt")
        mp4_path = os.path.join(output_dir, f"{base_name}.mp4")

        # 1. 导出 SRT
        SRTExporter(encoding=srt_encoding).export(subtitles, srt_path)

        # 2. 生成带字幕的 MP4
        if mode == "burn":
            self.burn_subtitles(video_path, srt_path, mp4_path, **kwargs)
        else:
            self.embed_subtitles(video_path, srt_path, mp4_path, **kwargs)

        return srt_path, mp4_path

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @property
    def ffmpeg_path(self) -> str:
        return self._ffmpeg

    @property
    def ffprobe_path(self) -> str:
        return self._ffprobe

    def get_duration(self, video_path: str) -> float:
        """快速获取视频时长（秒）"""
        return self.probe(video_path).duration
