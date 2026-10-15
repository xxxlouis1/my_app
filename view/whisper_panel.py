# -*- coding: utf-8 -*-
"""Whisper 参数面板（第一板块）。

收集用户在界面上选择/填写的 Whisper 参数，打包成字典交给后端。
后端（第四板块）负责参数的合法性校验与互斥参数判断。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QSpinBox,
)

MODELS = ["tiny", "base", "small", "medium", "large-v3"]
LANGUAGES = ["auto", "en", "zh", "ja", "ko", "fr", "de", "es"]
DEVICES = ["auto", "cpu", "cuda"]


class WhisperPanel(QGroupBox):
    """Whisper 参数面板，通过 ``whisper_params()`` 对外提供参数字典。"""

    def __init__(self, parent=None):
        super().__init__("Whisper 参数", parent)
        form = QFormLayout(self)

        self.model_combo = QComboBox()
        self.model_combo.addItems(MODELS)
        self.model_combo.setCurrentText("small")

        self.language_combo = QComboBox()
        self.language_combo.addItems(LANGUAGES)
        self.language_combo.setEditable(True)
        self.language_combo.setCurrentText("en")
        self.language_combo.setToolTip("留空或选 auto 表示自动检测语言")

        self.device_combo = QComboBox()
        self.device_combo.addItems(DEVICES)
        self.device_combo.setCurrentText("auto")
        self.device_combo.setToolTip("auto = 自动探测 GPU，不可用则回退 CPU")

        self.temperature_edit = QLineEdit()
        self.temperature_edit.setPlaceholderText("留空用默认，如 0.0, 0.2, 0.4")
        self.temperature_edit.setToolTip("逗号分隔的浮点数列表（faster-whisper 温度序列）")

        self.beam_spin = QSpinBox()
        self.beam_spin.setRange(1, 10)
        self.beam_spin.setValue(5)
        self.beam_spin.setToolTip("beam search 宽度（temperature 为单个值时生效）")

        self.vad_check = QCheckBox("启用 VAD 过滤")
        self.vad_check.setChecked(True)
        self.vad_check.setToolTip("过滤静音段，抑制幻觉复读")

        form.addRow("模型 model", self.model_combo)
        form.addRow("语言 language", self.language_combo)
        form.addRow("设备 device", self.device_combo)
        form.addRow("temperature", self.temperature_edit)
        form.addRow("beam_size", self.beam_spin)
        form.addRow("", self.vad_check)

    def whisper_params(self) -> dict:
        """返回用户填写的 Whisper 参数字典，供后端使用。"""
        return {
            "model": self.model_combo.currentText().strip() or "small",
            "language": self.language_combo.currentText().strip() or "auto",
            "device": self.device_combo.currentText().strip() or "auto",
            "temperature": self.temperature_edit.text().strip(),
            "beam_size": self.beam_spin.value(),
            "vad_filter": self.vad_check.isChecked(),
        }
