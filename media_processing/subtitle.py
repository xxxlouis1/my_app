"""
字幕数据结构

与成员 2（业务逻辑 & SRT 解析）共享的数据结构。
业务层解析 SRT 后生成 Subtitle 列表，传给本模块导出 / 烧录。
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Subtitle:
    """单条字幕"""

    index: int                    # 序号（从 1 开始）
    start: float                  # 起始时间（秒）
    end: float                    # 结束时间（秒）
    text: str                     # 字幕文本（多行用 \n 分隔）

    def duration(self) -> float:
        """字幕时长（秒）"""
        return max(0.0, self.end - self.start)

    def is_valid(self) -> bool:
        """基本合法性：起止时间非负、结束晚于起始、文本非空"""
        return (
            self.start >= 0
            and self.end >= 0
            and self.end > self.start
            and bool(self.text.strip())
        )


@dataclass
class WaveformSegment:
    """单句波形片段数据（供 GUI 第二区域渲染）"""

    subtitle_index: int           # 对应字幕序号
    start: float                  # 片段起始时间（秒）
    end: float                    # 片段结束时间（秒）
    samples: List[float]          # 归一化后的采样点数组（-1.0 ~ 1.0）
    sample_rate: int              # 采样率（原始音频，非压缩后）


@dataclass
class FullWaveform:
    """全局完整波形数据（供 GUI 第一区域渲染）"""

    samples: List[float]          # 压缩后的采样点数组（-1.0 ~ 1.0）
    duration: float               # 音频总时长（秒）
    sample_rate: int              # 原始采样率
    target_samples: int           # 压缩后的目标采样点数
