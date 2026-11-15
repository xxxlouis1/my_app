"""
自定义异常类

所有音视频处理相关异常均继承自 MediaProcessingError，
便于上层（GUI / 业务层）统一捕获并展示错误信息。
"""


class MediaProcessingError(Exception):
    """音视频处理基础异常"""

    def __init__(self, message: str = "", detail: str = ""):
        self.message = message
        self.detail = detail
        super().__init__(message)

    def __str__(self) -> str:
        if self.detail:
            return f"{self.message}\n详情: {self.detail}"
        return self.message


class FFmpegNotFoundError(MediaProcessingError):
    """未找到 ffmpeg / ffprobe 可执行文件"""

    def __init__(self, message: str = "未找到 ffmpeg / ffprobe，请安装后重试"):
        super().__init__(message)


class VideoCorruptedError(MediaProcessingError):
    """视频文件损坏或无法解析"""

    def __init__(self, path: str = "", detail: str = ""):
        msg = f"视频文件损坏或无法解析: {path}" if path else "视频文件损坏或无法解析"
        super().__init__(msg, detail)


class NoAudioStreamError(MediaProcessingError):
    """视频中无音频流"""

    def __init__(self, path: str = ""):
        msg = f"视频中未检测到音频流: {path}" if path else "视频中未检测到音频流"
        super().__init__(msg)


class NoVideoStreamError(MediaProcessingError):
    """视频中无视频流"""

    def __init__(self, path: str = ""):
        msg = f"视频中未检测到视频流: {path}" if path else "视频中未检测到视频流"
        super().__init__(msg)


class AudioExtractionError(MediaProcessingError):
    """音频提取失败"""

    def __init__(self, detail: str = ""):
        super().__init__("音频提取失败", detail)


class WaveformGenerationError(MediaProcessingError):
    """波形生成失败"""

    def __init__(self, detail: str = ""):
        super().__init__("波形生成失败", detail)


class SRTExportError(MediaProcessingError):
    """SRT 导出失败"""

    def __init__(self, detail: str = ""):
        super().__init__("SRT 文件导出失败", detail)


class SubtitleBurnError(MediaProcessingError):
    """字幕烧录 / 内封失败"""

    def __init__(self, detail: str = ""):
        super().__init__("字幕烧录 / 内封失败", detail)
