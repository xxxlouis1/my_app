# -*- coding: utf-8 -*-
"""轻量 SRT 解析器（仅用于 GUI 预览，不参与后端校正）。

后端（第二/第四板块）有自己的 SRT 解析与合法性校验；这里只负责把 SRT
读出来渲染到预览表格，因此刻意做成零依赖、容错优先：解析失败不抛异常，
尽量返回已解析出的内容，错误由调用方提示。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# 时间戳形如 00:00:00,378 或 00:00:00.378
_TS = r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
_TIME_LINE = re.compile(r"^\s*" + _TS + r"\s*-->\s*" + _TS + r"\s*$")


@dataclass
class SrtCue:
    index: int
    start: str            # 原始时间串，如 "00:00:00,378"
    end: str
    start_ms: int
    end_ms: int
    text: str


def _ts_to_ms(h: str, m: str, s: str, ms: str) -> int:
    return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms.ljust(3, "0")[:3])


def read_srt_file(path: str) -> Tuple[Optional[str], Optional[str]]:
    """读取 SRT 文件，自动探测编码。返回 (文本, 错误)；出错时文本为 None。"""
    for enc in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), None
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as e:
            return None, str(e)
    return None, "无法识别文件编码（请使用 UTF-8 编码的 SRT）"


def parse_srt(text: str) -> Tuple[List[SrtCue], List[str]]:
    """解析 SRT 文本，返回 (字幕列表, 错误列表)。容错优先，不抛异常。"""
    cues: List[SrtCue] = []
    errors: List[str] = []

    blocks = re.split(r"\r?\n\s*\r?\n", (text or "").strip())
    for block in blocks:
        lines = [ln.rstrip("\r") for ln in block.split("\n")]
        lines = [ln for ln in lines if ln.strip()]

        t_i = None
        for i, ln in enumerate(lines):
            if _TIME_LINE.match(ln):
                t_i = i
                break
        if t_i is None:
            continue  # 非标准块，跳过

        m = _TIME_LINE.match(lines[t_i])
        h1, m1, s1, ms1, h2, m2, s2, ms2 = m.groups()  # type: ignore[union-attr]
        start = f"{h1}:{m1}:{s1},{ms1.ljust(3, '0')[:3]}"
        end = f"{h2}:{m2}:{s2},{ms2.ljust(3, '0')[:3]}"
        start_ms = _ts_to_ms(h1, m1, s1, ms1)
        end_ms = _ts_to_ms(h2, m2, s2, ms2)
        text = "\n".join(lines[t_i + 1:]).strip()

        cues.append(SrtCue(index=len(cues) + 1, start=start, end=end,
                           start_ms=start_ms, end_ms=end_ms, text=text))

    return cues, errors
