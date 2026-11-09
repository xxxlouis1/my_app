# -*- coding: utf-8 -*-
"""字幕条目与错误信息的 GUI 视图模型（第一板块）。

后端（成员2）维护「波形编号 ↔ 字幕条目」映射与错误数据结构；这里定义
GUI 渲染所需的视图模型，后端按此填充。仅用于展示，不参与后端修正逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ErrorInfo:
    """一条字幕的错误信息。"""
    subtitle_no: int        # 对应字幕编号（波形编号）
    error_type: str         # 错误类型，如 "时间轴偏移" / "识别错误" / "缺失"
    message: str            # 错误详情
    timestamp_ms: int = 0   # 出错位置的时间戳（可选）


@dataclass
class SubtitleItem:
    """字幕列表里的一行（GUI 视图模型）。"""
    no: int                 # 波形编号（与字幕一一对应，即序号）
    start_ms: int
    end_ms: int
    text: str               # 原文
    translation: str = ""   # 译文（暂无翻译则留空）
    error: Optional[ErrorInfo] = None
