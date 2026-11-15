# -*- coding: utf-8 -*-
"""主窗口（第一板块 GUI）。

界面：
  - 文件导入区：MP4 选择器 + SRT 选择器 + 输出目录
  - Whisper 参数面板
  - 字幕预览（分页）
  - 任务列表 + 总进度 + 日志
  - 操作按钮：加载 SRT / 字幕校正 / 导出文件 / 取消 / 清空

与后端对接：使用 ``controller.qt_worker.SchedulerWorker``，不自己起线程；
GUI 只通过信号槽接收进度/日志，绝不在工作线程里碰控件。
"""
from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from controller.qt_worker import JobRow, SchedulerWorker
from controller.scheduler import JobState
from view.aboboo_view import AbobooView
from view.srt_preview import SrtPreviewWidget
from view.subtitle_model import ErrorInfo, SubtitleItem
from view.whisper_panel import WhisperPanel
from view.waveform_panel import WaveformPanel


class WaveformWorker(QThread):
    """后台生成波形，避免解码整段音频时界面卡死。

    同时顺带算出音频的语音边界（供错误检测用），因为两者都要解码音频，
    合并到一个线程里做可以少跑一遍。
    """

    done = Signal(object, object, object)   # (原始轨, 处理后轨, SpeechBoundaries)
    failed = Signal(str)

    def __init__(self, video_path: str, processed_media: str = "",
                 target_points: int = 2000, parent=None):
        super().__init__(parent)
        self._video = video_path
        self._processed = processed_media or None
        self._target = target_points

    def run(self):
        try:
            from media_processing import build_tracks
            original, processed = build_tracks(
                self._video, processed_media=self._processed,
                target_points=self._target)
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")
            return
        # 语音边界：失败不影响波形（属增强信息）
        boundaries = None
        try:
            from model.waveform import extract_boundaries
            boundaries = extract_boundaries(self._video)
        except Exception:
            boundaries = None
        self.done.emit(original, processed, boundaries)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MP4 字幕矫正工具")
        self.resize(1120, 800)

        self.video_path = ""
        self.srt_path = ""
        self.out_dir = ""
        self._processing = False
        self._job_rows = {}   # job_id -> 表格行号
        # 播放/波形相关状态（由成员3 的 media_processing 驱动）
        self._playback = None
        self._wave_worker = None
        self._boundaries = None      # SpeechBoundaries，供错误检测用
        self._duration = 0.0

        self.worker = SchedulerWorker()
        self._connect_worker()

        self.waveform_panel = WaveformPanel()
        self.waveform_panel.saveRequested.connect(self._on_save_waveform)
        self.waveform_panel.canvas.cueChanged.connect(self._on_cues_changed)

        self._build_ui()
        self._setup_loading_animation()
        self._setup_playback()

    # ------------------------------------------------------------ 构造
    def _connect_worker(self):
        self.worker.jobUpdated.connect(self._on_job_updated)
        self.worker.progressChanged.connect(self._on_progress)
        self.worker.logMessage.connect(self._on_log)
        self.worker.batchFinished.connect(self._on_batch_finished)
        self.worker.failed.connect(self._on_batch_failed)

    def _setup_loading_animation(self):
        self._loading_dots = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(400)
        self._loading_timer.timeout.connect(self._tick_loading)

    # ------------------------------------------------------------ 播放与波形
    def _setup_playback(self):
        """接成员3 的播放控制器到 Aboboo 界面。

        失败（未装 ffmpeg / 无 PySide6 绑定等）只写日志并禁用预览，
        不影响字幕校正主流程。
        """
        try:
            from media_processing import create_playback_controller
            self._playback = create_playback_controller(self)
        except Exception as e:
            self._playback = None
            self._log("warning", f"视频预览不可用：{e}")
            return

        vp = self.aboboo_view.video_panel
        self._playback.frame_ready.connect(vp.canvas.set_image)
        self._playback.position_changed.connect(self._on_position_changed)
        self._playback.finished.connect(lambda: vp.set_playing(False))
        self._playback.error.connect(lambda m: self._log("error", f"视频预览失败：{m}"))

        vp.playPauseRequested.connect(self._on_play_pause)
        vp.seekRequested.connect(self._on_seek_frac)
        # 点击波形 → 跳转播放位置
        self.aboboo_view.seekRequested.connect(self._on_seek_seconds)
        # 选中字幕 → 第 2 分区显示该句的切片波形
        self.aboboo_view.subtitle_list.selected.connect(self._on_subtitle_selected)
        self._log("info", "视频预览已就绪")

    def _on_subtitle_selected(self, item):
        """选中某条字幕 → 切片出该句波形，喂给单句波形区。

        复用成员3 的 ``WaveformProcessor`` 缓存（同一进程内只解码一次），
        所以连续点击不同句子不会重复解码整段音频。
        """
        if not self.video_path or item is None:
            return
        try:
            from media_processing import build_segment_track, to_gui_track
            seg = build_segment_track(
                self.video_path,
                item.start_ms / 1000.0,
                item.end_ms / 1000.0,
                target_points=1000,
            )
            try:
                seg = to_gui_track(seg)
            except Exception:
                pass
            self.aboboo_view.set_segment_waveform(seg)
        except Exception as e:
            self._log("warning", f"单句波形生成失败：{e}")

    def _on_play_pause(self):
        if self._playback is None:
            self._log("warning", "播放器未就绪，无法播放")
            return
        self._playback.toggle()
        self.aboboo_view.video_panel.set_playing(self._playback.is_playing)

    def _on_position_changed(self, frac: float):
        dur = self._playback.duration if self._playback else 0.0
        if dur > 0:
            self.aboboo_view.set_playhead(frac * dur)
            self.waveform_panel.set_playhead_frac(frac)

    def _on_seek_frac(self, frac: float):
        if self._playback is not None:
            self._playback.seek(frac)
            self.aboboo_view.video_panel.set_position(frac)

    def _on_seek_seconds(self, t_sec: float):
        if self._playback is not None and self._playback.duration > 0:
            frac = max(0.0, min(1.0, t_sec / self._playback.duration))
            self._playback.seek(frac)
            self.aboboo_view.video_panel.set_position(frac)

    def _load_global_waveform(self):
        """选视频后生成双轨波形（后台线程，避免界面卡顿）。"""
        if not self.video_path:
            return
        if self._wave_worker is not None and self._wave_worker.isRunning():
            self._log("warning", "波形正在生成中，请稍候")
            return
        self.waveform_panel.status_label.setText("波形生成中…")
        self._log("info", f"开始生成波形：{os.path.basename(self.video_path)}")
        self._wave_worker = WaveformWorker(self.video_path, parent=self)
        self._wave_worker.done.connect(self._on_waveform_ready)
        self._wave_worker.failed.connect(self._on_waveform_failed)
        self._wave_worker.start()

    def _on_waveform_ready(self, original, processed, boundaries):
        from media_processing import to_gui_track
        try:
            o = to_gui_track(original)
            p = to_gui_track(processed)
        except Exception:
            o, p = original, processed        # 鸭子类型兜底
        self._duration = getattr(original, "duration", 0.0) or 0.0
        self._boundaries = boundaries
        # 波形编辑页（双轨）
        self.waveform_panel.set_tracks(o, p)
        # Aboboo 页（总波形，单轨）
        self.aboboo_view.set_waveform(o)
        self._log("info", f"波形已生成：{len(getattr(o, 'peaks', []) or [])} 点，"
                          f"{self._duration:.1f}s")
        # 波形就绪后刷新字幕列表（错误检测依赖语音边界）
        self._refresh_aboboo_subtitles()

    def _on_waveform_failed(self, msg: str):
        self.waveform_panel.status_label.setText("波形生成失败")
        self._log("warning", f"波形生成失败：{msg}")

    def _refresh_aboboo_subtitles(self):
        """把当前 SRT 以 SubtitleItem（含错误标注）喂给 Aboboo 界面。"""
        if not self.srt_path or not os.path.isfile(self.srt_path):
            return
        try:
            from model.error_detect import detect_errors
            from model.subtitle_fix import read_srt_cues

            cues = read_srt_cues(self.srt_path)
            err_index = {}
            remaining = 0
            if cues:
                rep = detect_errors(cues, self._boundaries,
                                    media_duration=self._duration or None)
                err_index = rep.to_index()
                remaining = len(rep.errors)
                self._log("info", f"字幕检查：{rep.summary()}")

            items = []
            for i, c in enumerate(cues, 1):
                errs = err_index.get(i) or []
                err = None
                if errs:
                    e0 = errs[0]
                    err = ErrorInfo(
                        subtitle_no=e0["subtitle_no"],
                        error_type=e0["error_type"],
                        message="；".join(x["message"] for x in errs[:3]),
                        timestamp_ms=e0.get("timestamp_ms", 0))
                items.append(SubtitleItem(
                    no=i, start_ms=c.start_ms, end_ms=c.end_ms,
                    text=c.text, error=err))
            self.aboboo_view.set_subtitles(items)
            if remaining == 0:
                self._log("info", "未发现可判定的错误")
        except Exception as e:
            self._log("warning", f"字幕列表刷新失败：{e}")

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        root.addWidget(self._build_import_area())

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.whisper_panel = WhisperPanel()
        lv.addWidget(self.whisper_panel)
        preview_group = QGroupBox("字幕预览")
        pv = QVBoxLayout(preview_group)
        self.srt_preview = SrtPreviewWidget()
        pv.addWidget(self.srt_preview)
        lv.addWidget(preview_group, stretch=1)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self._build_task_area())
        rv.addWidget(self._build_log_area(), stretch=1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([460, 640])

        tabs = QTabWidget()
        self.aboboo_view = AbobooView()
        tabs.addTab(self.aboboo_view, "Aboboo 界面")
        tabs.addTab(splitter, "字幕校正")
        tabs.addTab(self.waveform_panel, "波形编辑")
        self.tabs = tabs
        root.addWidget(tabs, stretch=1)

        root.addWidget(self._build_button_bar())

    def _build_import_area(self):
        box = QGroupBox("文件导入")
        form = QFormLayout(box)

        self.video_edit = QLineEdit()
        self.video_edit.setReadOnly(True)
        self.video_edit.setPlaceholderText("请选择要处理的 MP4 视频")
        self.btn_video = QPushButton("选择视频")
        self.btn_video.clicked.connect(self._choose_video)
        row_v = QHBoxLayout()
        row_v.addWidget(self.video_edit, 1)
        row_v.addWidget(self.btn_video)
        form.addRow("视频(MP4)", self._wrap(row_v))

        self.srt_edit = QLineEdit()
        self.srt_edit.setReadOnly(True)
        self.srt_edit.setPlaceholderText("可选：选择已有 SRT 字幕，进行时间轴校正")
        self.btn_srt = QPushButton("选择字幕")
        self.btn_srt.clicked.connect(self._choose_srt)
        row_s = QHBoxLayout()
        row_s.addWidget(self.srt_edit, 1)
        row_s.addWidget(self.btn_srt)
        form.addRow("字幕(SRT)", self._wrap(row_s))

        self.out_edit = QLineEdit()
        self.out_edit.setReadOnly(True)
        self.out_edit.setPlaceholderText("选择输出目录（保存矫正后的 MP4 和 SRT）")
        self.btn_out = QPushButton("选择目录")
        self.btn_out.clicked.connect(self._choose_out_dir)
        row_o = QHBoxLayout()
        row_o.addWidget(self.out_edit, 1)
        row_o.addWidget(self.btn_out)
        form.addRow("输出目录", self._wrap(row_o))

        return box

    def _build_task_area(self):
        box = QGroupBox("任务")
        v = QVBoxLayout(box)

        self.task_table = QTableWidget(0, len(JobRow.COLUMNS))
        self.task_table.setHorizontalHeaderLabels(list(JobRow.COLUMNS))
        self.task_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hh = self.task_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.task_table, stretch=1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        v.addWidget(self.progress_bar)

        self.status_label = QLabel("就绪")
        v.addWidget(self.status_label)
        return box

    def _build_log_area(self):
        box = QGroupBox("日志")
        v = QVBoxLayout(box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        v.addWidget(self.log_view)
        return box

    def _build_button_bar(self):
        bar = QHBoxLayout()
        self.btn_load_srt = QPushButton("加载 SRT")
        self.btn_correct = QPushButton("字幕校正")
        self.btn_export = QPushButton("导出文件")
        self.btn_cancel = QPushButton("取消")
        self.btn_clear = QPushButton("清空")

        self.btn_load_srt.clicked.connect(self._load_srt)
        self.btn_correct.clicked.connect(self._start_correct)
        self.btn_export.clicked.connect(self._export_files)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_clear.clicked.connect(self._clear_all)

        self.btn_cancel.setEnabled(False)
        bar.addStretch(1)
        for b in (self.btn_load_srt, self.btn_correct, self.btn_export,
                  self.btn_cancel, self.btn_clear):
            b.setMinimumWidth(96)
            bar.addWidget(b)
        return self._wrap(bar)

    @staticmethod
    def _wrap(layout):
        w = QWidget()
        w.setLayout(layout)
        return w

    # ------------------------------------------------------------ 文件导入
    def _choose_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频", "",
            "视频文件 (*.mp4 *.m4v *.mov *.mkv *.avi);;所有文件 (*.*)")
        if not path:
            return
        self.video_path = path
        self.video_edit.setText(path)
        # 切换视频时清空字幕预览与波形
        self.srt_preview.clear()
        self.waveform_panel.clear()
        self.aboboo_view.clear()
        self._boundaries = None
        self._log("info", f"已选择视频：{os.path.basename(path)}")
        # 载入播放器（视频预览）+ 后台生成波形
        if self._playback is not None:
            if self._playback.load(path):
                self._log("info", "播放器已加载该视频")
            else:
                self._log("warning", "播放器无法打开该视频（可能编码不支持）")
        self._load_global_waveform()

    def _choose_srt(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择字幕", "", "字幕文件 (*.srt);;所有文件 (*.*)")
        if not path:
            return
        self.srt_path = path
        self.srt_edit.setText(path)
        self._log("info", f"已选择字幕：{os.path.basename(path)}")

    def _choose_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.out_dir = d
            self.out_edit.setText(d)

    # ------------------------------------------------------------ 按钮动作
    def _load_srt(self):
        if not self.srt_path:
            self._choose_srt()
            if not self.srt_path:
                return
        count, err = self.srt_preview.load_srt(self.srt_path)
        if err is not None:
            QMessageBox.critical(self, "加载失败", f"无法解析 SRT：\n{err}")
            self._log("error", f"加载 SRT 失败：{err}")
            return
        self._log("info", f"已加载字幕 {count} 条：{os.path.basename(self.srt_path)}")
        self.waveform_panel.set_cues(self.srt_preview.cues())
        # 同步到 Aboboo 界面（含错误标注）
        self._refresh_aboboo_subtitles()
        if count == 0:
            QMessageBox.warning(self, "提示", "该 SRT 未解析出任何字幕，请检查格式。")

    def _start_correct(self):
        if self._processing:
            return
        if not self.video_path:
            QMessageBox.warning(self, "提示", "请先选择 MP4 视频。")
            return
        if not os.path.isfile(self.video_path):
            QMessageBox.critical(self, "错误", f"视频文件不存在：\n{self.video_path}")
            return
        if not self.out_dir:
            QMessageBox.warning(self, "提示", "请先选择输出目录。")
            return

        params = self.whisper_panel.whisper_params()
        cfg = self.worker.config
        cfg.out_dir = self.out_dir
        cfg.model = params["model"]
        cfg.language = params["language"]
        cfg.device = params["device"]
        cfg.temperature = params["temperature"]
        cfg.beam_size = params["beam_size"]
        cfg.vad_filter = params["vad_filter"]
        cfg.input_srt = self.srt_path if self.srt_path else ""

        self._set_processing(True)
        self._log("info", f"开始字幕校正：{os.path.basename(self.video_path)}"
                          f"（模型 {params['model']}，语言 {params['language']}，"
                          f"设备 {params['device']}）")
        if self.srt_path:
            self._log("info", f"使用参考字幕：{os.path.basename(self.srt_path)}")

        try:
            self.worker.add_files([self.video_path], out_dir=self.out_dir)
            self.worker.start()
        except Exception as e:  # 启动阶段异常兜底
            self._set_processing(False)
            QMessageBox.critical(self, "启动失败", str(e))
            self._log("error", f"启动失败：{e}")

    def _export_files(self):
        if not self.out_dir or not os.path.isdir(self.out_dir):
            QMessageBox.warning(self, "提示", "请先选择有效的输出目录。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.out_dir))
        self._log("info", f"已打开输出目录：{self.out_dir}")

    def _cancel(self):
        if self._processing:
            self.worker.cancel_all()
            self._log("warning", "已请求取消")

    def _clear_all(self):
        if self._processing:
            QMessageBox.warning(self, "提示", "处理中，请先取消。")
            return
        self.worker.clear_finished()
        self.task_table.setRowCount(0)
        self._job_rows.clear()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.status_label.setText("就绪")
        self.srt_preview.clear()
        self.waveform_panel.clear()
        self._log("info", "已清空任务与预览")

    # ------------------------------------------------------------ 波形编辑
    def set_waveform(self, original, processed=None):
        """后端(成员3/4) 产出波形采样数据后调用，把数据喂给波形画布。"""
        self.waveform_panel.set_tracks(original, processed)

    def _on_cues_changed(self):
        # 断点被拖动后，实时同步字幕预览表格
        self.srt_preview.set_cues(self.waveform_panel.cues())

    def _on_save_waveform(self, cues):
        """把波形上拖动后的断点组装成标准 SRT 并另存。

        职责划分（对应分工表）：
          · GUI 只负责收集断点并交出来（不做业务逻辑）
          · 这里做**时间合法性校验**（负数、重叠、逆序、零时长）—— 归成员2/4
          · 组装与写盘复用成员3 的 ``SRTExporter``（Aboboo 规范、UTF-8 无 BOM）
        """
        if not cues:
            QMessageBox.warning(self, "提示", "没有可保存的字幕。")
            return

        # ---- 1. 整理并按时间排序
        rows = sorted(
            ({"no": getattr(c, "index", i + 1),
              "start": int(round(getattr(c, "start_ms", 0))),
              "end": int(round(getattr(c, "end_ms", 0))),
              "text": (getattr(c, "text", "") or "").strip()}
             for i, c in enumerate(cues)),
            key=lambda r: (r["start"], r["end"]))

        # ---- 2. 时间合法性校验与修正
        problems = []
        min_gap = 24          # 与字幕参数一致（毫秒）
        min_dur = 200
        prev_end = None
        for i, r in enumerate(rows, 1):
            if r["start"] < 0:
                problems.append(f"#{i} 起始时间为负，已钳制到 0")
                r["start"] = 0
            if r["end"] <= r["start"]:
                problems.append(f"#{i} 结束时间不晚于开始，已按最短时长修正")
                r["end"] = r["start"] + min_dur
            if prev_end is not None and r["start"] < prev_end + min_gap:
                if r["start"] < prev_end:
                    problems.append(
                        f"#{i} 与前一条重叠 {(prev_end - r['start'])}ms，已避让")
                    r["start"] = prev_end + min_gap
                    if r["end"] <= r["start"]:
                        r["end"] = r["start"] + min_dur
                # 间隙不足时不再强推（拖动是用户意图，只提示不篡改）
            prev_end = r["end"]
            if not r["text"]:
                problems.append(f"#{i} 文本为空（仍保留，Aboboo 会跳过）")

        if problems:
            detail = "\n".join(f"· {p}" for p in problems[:10])
            more = f"\n…还有 {len(problems) - 10} 条" if len(problems) > 10 else ""
            self._log("warning", f"保存前自动修正 {len(problems)} 处：\n{detail}{more}")

        # ---- 3. 选择保存位置
        base = os.path.splitext(os.path.basename(self.srt_path or "subtitle"))[0]
        default_dir = os.path.dirname(self.srt_path) if self.srt_path else (
            self.out_dir or os.path.expanduser("~"))
        default_path = os.path.join(default_dir, f"{base}_corrected.srt")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存字幕", default_path, "SRT 字幕 (*.srt)")
        if not path:
            return
        if not path.lower().endswith(".srt"):
            path += ".srt"

        # ---- 4. 组装并写盘（复用成员3 的导出器，保证 Aboboo 规范）
        # 注意：SRTExporter 默认 encoding="utf-8-sig"（带 BOM）。本项目其余环
        # 一律输出 UTF-8 **无 BOM**，为免同一次交付里格式不一致，这里显式改回
        # utf-8。BOM 对 Aboboo 是否友好双方都未实测，保持与既有产物一致最稳。
        try:
            from media_processing import SRTExporter, Subtitle
            subs = [Subtitle(index=i, start=r["start"] / 1000.0,
                             end=r["end"] / 1000.0, text=r["text"])
                    for i, r in enumerate(rows, 1)]
            exported = SRTExporter(encoding="utf-8").export(
                subs, path, validate=True, renumber=True)
        except Exception as e:
            self._log("error", f"SRT 导出失败：{e}")
            QMessageBox.critical(self, "保存失败", f"无法写出 SRT：\n{e}")
            return

        self._log("info", f"已保存 {len(rows)} 条字幕到：{exported}")
        QMessageBox.information(
            self, "保存成功",
            f"已保存 {len(rows)} 条字幕：\n{exported}"
            + ("\n\n注：保存前自动修正了部分时间问题，详见日志。" if problems else ""))

    # ------------------------------------------------------------ worker 信号
    def _on_job_updated(self, job):
        row = JobRow.from_job(job)
        rid = self._job_rows.get(job.job_id)
        if rid is None:
            rid = self.task_table.rowCount()
            self.task_table.insertRow(rid)
            self._job_rows[job.job_id] = rid
        for c, val in enumerate(row.as_row()):
            item = QTableWidgetItem(str(val))
            if c in (1, 2):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.task_table.setItem(rid, c, item)

    def _on_progress(self, progress):
        self.progress_bar.setValue(int(round(progress.overall * 100)))
        self.progress_bar.setFormat(f"{progress.text()}  %p%")

    def _on_log(self, level, msg):
        self._log(level, msg)

    def _on_batch_finished(self, jobs):
        self._set_processing(False)
        done = sum(1 for j in jobs if j.state == JobState.DONE)
        failed = sum(1 for j in jobs if j.state == JobState.FAILED)
        self._log("info", f"批次结束：成功 {done}，失败 {failed}")
        if failed:
            QMessageBox.warning(self, "处理完成（有失败）",
                                f"成功 {done} 个，失败 {failed} 个。详见日志。")
        else:
            QMessageBox.information(self, "处理完成",
                                    f"成功 {done} 个任务，结果已输出到：\n{self.out_dir}")

    def _on_batch_failed(self, msg):
        self._set_processing(False)
        self._log("error", f"批次级错误：{msg}")
        QMessageBox.critical(self, "处理失败", msg)

    # ------------------------------------------------------------ 状态管理
    def _set_processing(self, processing: bool):
        self._processing = processing
        if processing:
            self._loading_timer.start()
            self.status_label.setText("处理中")
        else:
            self._loading_timer.stop()
            self.status_label.setText("就绪")

        self.btn_correct.setEnabled(not processing)
        self.btn_load_srt.setEnabled(not processing)
        self.btn_export.setEnabled(not processing)
        self.btn_clear.setEnabled(not processing)
        self.btn_cancel.setEnabled(processing)
        self.btn_video.setEnabled(not processing)
        self.btn_srt.setEnabled(not processing)
        self.btn_out.setEnabled(not processing)

    def _tick_loading(self):
        if not self._processing:
            return
        self._loading_dots = (self._loading_dots % 3) + 1
        self.status_label.setText("处理中" + "." * self._loading_dots)

    def _log(self, level, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{ts}][{level}] {msg}")

    def closeEvent(self, event):
        self.worker.shutdown()
        super().closeEvent(event)
