"""
SRT 导出器

严格遵循 Aboboo 规范：
- 时间格式：HH:MM:SS,mmm（逗号分隔毫秒，不是句点）
- 每条字幕结构：序号 / 时间轴 / 文本 / 空行
- 换行使用 \\n（Windows 下写出时统一为 \\r\\n 由文件系统处理或保持 \\n）
- 编码：UTF-8（带 BOM 兼容部分播放器，Aboboo 推荐 UTF-8）

同时提供时间戳格式化函数，供业务层 / GUI 复用。
"""

import os
from typing import List

from .exceptions import SRTExportError
from .subtitle import Subtitle


def format_timestamp(seconds: float) -> str:
    """
    将秒数格式化为 Aboboo 规范的时间戳：HH:MM:SS,mmm

    :param seconds: 时间（秒），支持小数
    :return: 形如 "00:01:23,456" 的字符串
    """
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _validate_subtitles(subtitles: List[Subtitle]) -> None:
    """
    导出前校验字幕列表合法性。

    :raises SRTExportError: 发现非法字幕
    """
    if not subtitles:
        raise SRTExportError("字幕列表为空，无法导出")

    prev_end = -1.0
    for i, sub in enumerate(subtitles):
        if not isinstance(sub, Subtitle):
            raise SRTExportError(f"第 {i + 1} 条不是 Subtitle 类型")
        if sub.start < 0 or sub.end < 0:
            raise SRTExportError(
                f"第 {sub.index} 条字幕时间为负数: start={sub.start}, end={sub.end}"
            )
        if sub.end <= sub.start:
            raise SRTExportError(
                f"第 {sub.index} 条字幕结束时间不晚于起始时间: "
                f"start={sub.start}, end={sub.end}"
            )
        # 时间重叠检查（允许极小重叠，阈值 0 严格不重叠）
        if sub.start < prev_end:
            raise SRTExportError(
                f"第 {sub.index} 条字幕与上一条时间重叠: "
                f"prev_end={prev_end:.3f}, cur_start={sub.start:.3f}"
            )
        prev_end = sub.end


class SRTExporter:
    """SRT 文件导出器"""

    def __init__(self, encoding: str = "utf-8-sig"):
        """
        :param encoding: 文件编码，默认 utf-8-sig（带 BOM，兼容 Aboboo）
        """
        self._encoding = encoding

    def export(
        self,
        subtitles: List[Subtitle],
        output_path: str,
        validate: bool = True,
        renumber: bool = True,
    ) -> str:
        """
        导出字幕列表到 SRT 文件。

        :param subtitles:  字幕对象列表
        :param output_path: 输出 .srt 文件路径
        :param validate:   是否进行合法性校验（默认开启）
        :param renumber:   是否重新编号（从 1 开始连续编号，默认开启）
        :return: 输出文件路径
        :raises SRTExportError: 导出失败
        """
        if validate:
            _validate_subtitles(subtitles)

        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)

        lines: List[str] = []
        for i, sub in enumerate(subtitles, start=1):
            idx = i if renumber else sub.index
            start_ts = format_timestamp(sub.start)
            end_ts = format_timestamp(sub.end)
            # 文本：多行保持 \n，去除首尾空白
            text = sub.text.strip()
            lines.append(str(idx))
            lines.append(f"{start_ts} --> {end_ts}")
            lines.append(text)
            lines.append("")  # 字幕间空行

        # 末尾保留一个换行
        content = "\n".join(lines) + "\n"

        try:
            with open(output_path, "w", encoding=self._encoding, newline="") as f:
                f.write(content)
        except OSError as e:
            raise SRTExportError(f"写入文件失败: {e}")

        if not os.path.isfile(output_path):
            raise SRTExportError(f"SRT 文件未生成: {output_path}")

        return output_path

    def to_string(self, subtitles: List[Subtitle], renumber: bool = True) -> str:
        """将字幕列表转为 SRT 文本字符串（不写文件）"""
        _validate_subtitles(subtitles)
        lines: List[str] = []
        for i, sub in enumerate(subtitles, start=1):
            idx = i if renumber else sub.index
            lines.append(str(idx))
            lines.append(f"{format_timestamp(sub.start)} --> {format_timestamp(sub.end)}")
            lines.append(sub.text.strip())
            lines.append("")
        return "\n".join(lines) + "\n"


def export_srt(
    subtitles: List[Subtitle],
    output_path: str,
    encoding: str = "utf-8-sig",
    **kwargs,
) -> str:
    """便捷函数：导出 SRT"""
    return SRTExporter(encoding=encoding).export(subtitles, output_path, **kwargs)
