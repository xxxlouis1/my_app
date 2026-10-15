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

from PySide6.QtCore import Qt, QTimer, QUrl
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from controller.qt_worker import JobRow, SchedulerWorker
from controller.scheduler import JobState
from view.srt_preview import SrtPreviewWidget
from view.whisper_panel import WhisperPanel


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

        self.worker = SchedulerWorker()
        self._connect_worker()

        self._build_ui()
        self._setup_loading_animation()

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
        root.addWidget(splitter, stretch=1)

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
        # 切换视频时清空字幕预览
        self.srt_preview.clear()
        self._log("info", f"已选择视频：{os.path.basename(path)}")

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
        self._log("info", "已清空任务与预览")

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
