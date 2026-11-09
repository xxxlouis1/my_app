# -*- coding: utf-8 -*-
"""
字幕错误检测（第四板块）

产出 GUI（第一板块）可直接渲染的错误清单，字段与
``view/subtitle_model.py`` 的 ``ErrorInfo`` **完全一致**：
``subtitle_no / error_type / message / timestamp_ms``，另附两个扩展字段
``auto_fixable``（能否自动修）与 ``hint``（修法说明）。

检测的两类依据
--------------
1. **波形对不齐**（时间轴类）
   拿每条字幕的起止去比对音频波形的真实语音边界。这是客观参照，
   与 ASR 模型无关。

2. **ASR 置信度低**（识别类）
   用 faster-whisper 每条 segment 自带的质量指标，实测这几项很有判别力：

   · ``avg_logprob`` 低        -> 置信度不足，常见于噪声/口音/专有名词
   · ``compression_ratio`` 高  -> 解码陷入重复循环（典型幻觉）
   · ``temperature`` > 0.2     -> 低温解码已失败才升到这里，质量通常差
   · ``no_speech_prob`` 高     -> 该段很可能是静音被"听"成了话

   注意：这些指标只能**提示可能出错**，不能指出具体错在哪个词
   （那需要语言模型或人工校对）。所以错误类型统一叫"疑似"。
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .asr import QualityThresholds
from .subtitle_fix import Cue, FixConfig
from .waveform import SpeechBoundaries, filter_speech

# ---------------------------------------------------------------- 错误类型
# 字符串直接沿用 GUI 约定的措辞，便于界面分类标色
ET_OFFSET = "时间轴偏移"      # 起点偏离真实语音起点
ET_END_SHORT = "终点未覆盖"    # 终点早于语音结束
ET_OVERLAP = "时间重叠"
ET_ILLEGAL = "时间非法"        # 负值 / 零时长 / 逆序
ET_DURATION = "时长异常"
ET_NO_SPEECH = "无语音覆盖"    # 该时间段内检测不到语音
ET_ASR_LOW = "疑似识别错误"     # 置信度低
ET_HALLUCINATION = "疑似幻觉"   # 高 no_speech_prob / 重复循环
ET_TEXT_EMPTY = "文本缺失"
ET_LINE_LONG = "行长超限"
ET_SPEED = "阅读过快"


@dataclass
class DetectedError:
    """一条错误。前 4 个字段与 GUI 的 ErrorInfo 对齐。"""

    subtitle_no: int
    error_type: str
    message: str
    timestamp_ms: int = 0
    auto_fixable: bool = False     # 能否由自动修正解决
    hint: str = ""                 # 建议的处理方式
    severity: int = 1              # 1=提示 2=警告 3=严重

    SEVERITY_TEXT = {1: "提示", 2: "警告", 3: "严重"}

    def to_error_info(self) -> Dict:
        """转成 GUI 的 ErrorInfo 结构（dict 形式，便于跨层传递）。"""
        return {
            "subtitle_no": self.subtitle_no,
            "error_type": self.error_type,
            "message": self.message,
            "timestamp_ms": self.timestamp_ms,
            # 扩展字段：GUI 可选用
            "auto_fixable": self.auto_fixable,
            "hint": self.hint,
            "severity": self.severity,
            "severity_text": self.SEVERITY_TEXT.get(self.severity, ""),
        }


@dataclass
class ErrorReport:
    """检测汇总，便于 GUI 显示"共 N 条错误"并在列表里标红。"""

    errors: List[DetectedError] = field(default_factory=list)
    total_cues: int = 0

    def by_type(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for e in self.errors:
            out[e.error_type] = out.get(e.error_type, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    @property
    def fixable_count(self) -> int:
        return sum(1 for e in self.errors if e.auto_fixable)

    def subtitles_with_errors(self) -> List[int]:
        return sorted({e.subtitle_no for e in self.errors})

    def summary(self) -> str:
        if not self.errors:
            return f"共 0 条错误 / {self.total_cues} 条字幕"
        fixed = self.fixable_count
        return (f"共 {len(self.errors)} 条错误 / {self.total_cues} 条字幕"
                f"（可自动修 {fixed} 条，需人工 {len(self.errors) - fixed} 条）")

    def to_gui_items(self) -> List[Dict]:
        return [e.to_error_info() for e in self.errors]

    def to_index(self) -> Dict[int, List[Dict]]:
        """按字幕编号分组，供 GUI 的"选中某条 -> 显示它的错误"使用。"""
        idx: Dict[int, List[Dict]] = {}
        for e in self.errors:
            idx.setdefault(e.subtitle_no, []).append(e.to_error_info())
        return idx


# ---------------------------------------------------------------- 辅助

def _anchors(boundaries: SpeechBoundaries, cfg: FixConfig
             ) -> List[Tuple[float, float]]:
    """与吸附阶段一致的锚点（必须用同一套区间，否则判定会自相矛盾）。"""
    if not boundaries or not boundaries.speech:
        return []
    a = filter_speech(boundaries.speech, cfg.min_anchor_speech)
    return a or filter_speech(boundaries.speech, cfg.min_speech)


def _region_of(start: float, anchors: Sequence[Tuple[float, float]],
               pre_tol: float = 0.15, post_tol: float = 0.05
               ) -> Optional[Tuple[float, float]]:
    """找起点所属（或紧邻）的语音区间。

    容差是不对称的，这是有意的：
      · ``pre_tol=150ms``：起点比语音起点**早**一点是正常的。吸附对 ASR 原始段
        取"下一个语音起点"，但 VAD/波形检测本身有几十毫秒误差，实测出现过
        起点比区间早 52ms 的情况 —— 如果容差只给 50ms 就会误报成"无语音覆盖"。
      · ``post_tol=50ms``：起点比区间起点**晚**太多就说明真的错位了，
        不该再宽容。
    """
    for a, b in anchors:
        if a - pre_tol <= start <= b + post_tol:
            return (a, b)
    return None


def _speech_overlap(start: float, end: float,
                    anchors: Sequence[Tuple[float, float]]) -> float:
    """字幕时间段与语音区间的重叠时长。"""
    total = 0.0
    for a, b in anchors:
        lo, hi = max(start, a), min(end, b)
        if hi > lo:
            total += hi - lo
    return total


# ---------------------------------------------------------------- 主入口

def detect_errors(
    cues: Sequence[Cue],
    boundaries: Optional[SpeechBoundaries] = None,
    cfg: Optional[FixConfig] = None,
    quality: Optional[QualityThresholds] = None,
    media_duration: Optional[float] = None,
) -> ErrorReport:
    """检测字幕中的所有可判定错误。

    参数
    ----
    cues
        待检查的字幕（可以是 ASR 结果，也可以是从 SRT 读入的）。
    boundaries
        音频波形边界。为 None 时跳过"时间轴类"检测，只做结构与置信度检测。
    media_duration
        音频总时长，用于检查"字幕超出媒体时长"。为 None 时取 boundaries.duration。
    """
    cfg = cfg or FixConfig()
    q = quality or QualityThresholds()
    report = ErrorReport(total_cues=len(cues))
    if not cues:
        return report
    anchors = _anchors(boundaries, cfg) if boundaries else []
    if media_duration is None and boundaries is not None:
        media_duration = boundaries.duration or None

    def add(no: int, etype: str, msg: str, ts: int = 0,
            fixable: bool = False, hint: str = "", sev: int = 1) -> None:
        report.errors.append(DetectedError(
            subtitle_no=no, error_type=etype, message=msg,
            timestamp_ms=ts, auto_fixable=fixable, hint=hint, severity=sev))

    for i, c in enumerate(cues, 1):
        ts = c.start_ms

        # ---------- 1. 时间合法性 ----------
        if c.text.strip() == "":
            add(i, ET_TEXT_EMPTY, "文本为空", ts, True,
                "自动修正会剔除该条", 3)
        if c.start < 0:
            add(i, ET_ILLEGAL, f"起始时间为负（{c.start:.3f}s）", ts, True,
                "自动修正会钳制到 0", 3)
        if c.end <= c.start:
            add(i, ET_ILLEGAL,
                f"结束时间不晚于开始（{c.start:.3f} -> {c.end:.3f}）", ts, True,
                "自动修正会按最小时长重建", 3)
        if media_duration and c.end > media_duration + 0.5:
            add(i, ET_ILLEGAL,
                f"结束时间超出音频时长（{c.end:.2f}s > {media_duration:.2f}s）",
                ts, True, "自动修正会钳制到音频结尾", 2)

        # ---------- 2. 时长 ----------
        dur = c.duration_ms
        if c.end > c.start and dur > cfg.max_duration_ms:
            add(i, ET_DURATION,
                f"时长 {dur}ms 超过上限 {cfg.max_duration_ms}ms", ts, True,
                "自动修正会在静音处切分（不是截断）", 2)
        elif c.end > c.start and cfg.min_duration_ms and dur < cfg.min_duration_ms:
            add(i, ET_DURATION,
                f"时长 {dur}ms 短于下限 {cfg.min_duration_ms}ms", ts, True,
                "若所属语音区间更长，自动修正会延长", 1)

        # ---------- 3. 行长 / 阅读速度 ----------
        lines = c.text.split("\n")
        if any(len(ln) > cfg.max_line_chars for ln in lines):
            longest = max(len(ln) for ln in lines)
            add(i, ET_LINE_LONG,
                f"单行 {longest} 字符，超过上限 {cfg.max_line_chars}", ts, True,
                "自动修正会按词边界换行", 1)
        if c.end > c.start:
            cps = len(c.text.replace("\n", " ")) / max(1e-6, (c.end - c.start))
            if cps > 25:
                add(i, ET_SPEED, f"阅读速度 {cps:.1f} 字符/秒（上限 25）", ts,
                    False, "需拆分或精简文本，自动修正不动文本", 1)

        # ---------- 4. 与波形比对（时间轴类）----------
        if anchors and c.end > c.start:
            reg = _region_of(c.start, anchors)
            if reg is None:
                # 起点既不在任何语音区间内，附近也没有 -> 很可能错位
                near = min((abs(c.start - a) for a, _ in anchors), default=99)
                add(i, ET_NO_SPEECH,
                    f"起始时间附近 {near*1000:.0f}ms 内没有语音", ts, True,
                    "自动修正会吸附到最近的语音起点", 3)
            else:
                a, b = reg
                off = c.start - a
                # 容差换算成秒（FixConfig.snap_tolerance 单位是秒）
                tol = cfg.snap_tolerance
                if off > tol and off > 0.15:
                    add(i, ET_OFFSET,
                        f"起点比语音起点晚 {off*1000:.0f}ms", ts, True,
                        "自动修正会吸附到语音起点", 2)
                elif off < -0.15:
                    add(i, ET_OFFSET,
                        f"起点比语音起点早 {-off*1000:.0f}ms", ts, True,
                        "自动修正会吸附到语音起点", 2)
                if b - c.end > 0.25:
                    add(i, ET_END_SHORT,
                        f"终点早于语音结束 {(b-c.end)*1000:.0f}ms", ts, True,
                        "自动修正会把终点延到语音结束", 2)

        # ---------- 5. ASR 置信度（识别类）----------
        if c.avg_logprob is not None and c.avg_logprob < q.logprob_suspect:
            sev = 3 if c.avg_logprob < q.logprob_bad else 2
            add(i, ET_ASR_LOW,
                f"置信度偏低（avg_logprob={c.avg_logprob:.2f}）", ts, False,
                "建议人工核对这条文本，或改用更大的模型重跑", sev)
        if (c.compression_ratio is not None
                and c.compression_ratio > q.compression_suspect):
            add(i, ET_HALLUCINATION,
                f"文本重复度异常（compression_ratio="
                f"{c.compression_ratio:.2f}），疑似解码循环", ts, True,
                "自动修正会折叠完全重复的内容", 3)
        if c.temperature is not None and c.temperature > q.temperature_suspect:
            add(i, ET_ASR_LOW,
                f"低温解码失败后回退（temperature={c.temperature:.1f}）", ts,
                False, "该条识别可靠性较低，建议人工核对", 2)
        if (c.no_speech_prob is not None
                and c.no_speech_prob > 0.6 and c.duration_ms < 2000):
            add(i, ET_HALLUCINATION,
                f"静音概率高（no_speech_prob={c.no_speech_prob:.2f}）且时长很短",
                ts, False, "很可能是不存在的语音，建议人工确认后删除", 3)

    # ---------- 6. 相邻关系（重叠 / 逆序）----------
    for i in range(len(cues) - 1):
        a, b = cues[i], cues[i + 1]
        if a.end > b.start + 1e-6:
            add(i + 1, ET_OVERLAP,
                f"与第 {i+2} 条重叠 {(a.end-b.start)*1000:.0f}ms",
                b.start_ms, True, "自动修正会让前一条避让", 3)
        if b.start < a.start - 1e-6:
            add(i + 2, ET_ILLEGAL, f"起始时间早于第 {i+1} 条", b.start_ms, True,
                "自动修正会重排时间轴", 3)

    return report


def count_suspect_segments(cues: Sequence[Cue],
                           quality: Optional[QualityThresholds] = None
                           ) -> Dict[str, int]:
    """按质量等级统计，便于在日志里一句话说明 ASR 整体可靠性。"""
    q = QualityThresholds()
    out = {"ok": 0, "suspect": 0, "bad": 0}
    for c in cues:
        out[c.quality(q)] = out.get(c.quality(q), 0) + 1
    return out
