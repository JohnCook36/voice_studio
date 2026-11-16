"""Qt dialogs for Russian pronunciation overrides and genuine voice previews."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal, QUrl
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QProgressBar
)

from .licensing import BUILTIN_LICENSES
from .model_manager import default_models_root
from .pipeline import Job, render
from .pronunciation import (
    apply_pronunciations, load_dictionary, save_dictionary, PronunciationError,
    validate_rules
)

from .voice_profiles import VOICE_PROFILES, VOICE_SAMPLE_RU

VOICE_INFO = VOICE_PROFILES
SAMPLE_TEXT = VOICE_SAMPLE_RU


class PronunciationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Словарь произношения")
        self.resize(660, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Словарь заменяет слово только перед озвучкой. Текст книги остаётся без изменений. "
            "Ударения в Qwen не гарантируются – можно попробовать фонетическую замену."))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["В тексте", "Для произношения"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        inputs = QHBoxLayout()
        self.original = QLineEdit()
        self.original.setPlaceholderText("Например: замок")
        self.replacement = QLineEdit()
        self.replacement.setPlaceholderText("Например: замо́к")
        inputs.addWidget(self.original)
        inputs.addWidget(self.replacement)
        layout.addLayout(inputs)
        actions = QHBoxLayout()
        add_button = QPushButton("Добавить / заменить")
        add_button.clicked.connect(self.add_rule)
        actions.addWidget(add_button)
        remove_button = QPushButton("Удалить выбранное")
        remove_button.clicked.connect(self.remove_rule)
        actions.addWidget(remove_button)
        save_button = QPushButton("Сохранить словарь")
        save_button.clicked.connect(self.save)
        actions.addWidget(save_button)
        layout.addLayout(actions)
        self.test = QLineEdit()
        self.test.setPlaceholderText("Вставьте предложение и проверьте подготовленный текст…")
        layout.addWidget(self.test)
        test_btn = QPushButton("Показать текст для нейросети")
        test_btn.clicked.connect(self.preview)
        layout.addWidget(test_btn)
        self.result = QLabel()
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        try:
            for key, value in load_dictionary().items():
                self._append(key, value)
        except PronunciationError as exc:
            QMessageBox.warning(self, "Словарь", str(exc))

    def _append(self, key, value):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(key))
        self.table.setItem(row, 1, QTableWidgetItem(value))

    def rules(self):
        result = {}
        for row in range(self.table.rowCount()):
            source = self.table.item(row, 0)
            target = self.table.item(row, 1)
            if source is not None and target is not None:
                result[source.text().strip()] = target.text().strip()
        return validate_rules(result)

    def add_rule(self):
        source, replacement = self.original.text().strip(), self.replacement.text().strip()
        try:
            validate_rules({source: replacement})
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).text().casefold() == source.casefold():
                    self.table.item(row, 1).setText(replacement)
                    return
            self._append(source, replacement)
            self.original.clear()
            self.replacement.clear()
        except PronunciationError as exc:
            QMessageBox.warning(self, "Ошибка правила", str(exc))

    def remove_rule(self):
        if (row := self.table.currentRow()) >= 0:
            self.table.removeRow(row)

    def preview(self):
        try:
            self.result.setText("Для синтеза: " + apply_pronunciations(self.test.text(), self.rules()))
        except PronunciationError as exc:
            QMessageBox.warning(self, "Словарь", str(exc))

    def save(self):
        try:
            path = save_dictionary(self.rules())
        except (PronunciationError, OSError) as exc:
            QMessageBox.warning(self, "Словарь", str(exc))
            return
        self.result.setText("Словарь сохранён: " + str(path))
        self.accept()


class VoicePreviewWorker(QThread):
    audio_ready = Signal(str, str)
    failure = Signal(str)
    progress = Signal(int, int)

    def __init__(self, model_id: str, local_model: str, speakers: list[str],
                 speed: float, pronunciations: dict[str, str]):
        super().__init__()
        self.model_id = model_id
        self.local_model = local_model
        self.speakers = speakers
        self.speed = speed
        self.pronunciations = tuple(sorted(pronunciations.items()))
        self.stop = Event()

    def output_for(self, speaker: str) -> Path:
        config = Path(self.local_model) / "config.json"
        version = (config.stat().st_mtime_ns, config.stat().st_size) if config.is_file() else (0, 0)
        data = json.dumps([self.model_id, self.local_model, version, speaker,
                           self.speed, self.pronunciations, SAMPLE_TEXT], ensure_ascii=False)
        key = hashlib.sha256(data.encode("utf-8")).hexdigest()
        return default_models_root().parent / "voice_previews" / (key + ".wav")

    def run(self):
        try:
            from .engines import QwenEngine
            engine = None
            for idx, speaker in enumerate(self.speakers):
                if self.stop.is_set():
                    break
                target = self.output_for(speaker)
                if not target.is_file():
                    if engine is None:
                        engine = QwenEngine(self.model_id, local_model=self.local_model)
                    job = Job(self.model_id, SAMPLE_TEXT, target, speaker=speaker,
                              commercial=False, speed=self.speed,
                              pronunciations=self.pronunciations)
                    render(job, BUILTIN_LICENSES[self.model_id], engine, cancelled=self.stop.is_set)
                self.audio_ready.emit(speaker, str(target))
                self.progress.emit(idx + 1, len(self.speakers))
        except Exception as exc:
            self.failure.emit(f"{type(exc).__name__}: {exc}")


class VoicePreviewDialog(QDialog):
    def __init__(self, model_id: str, local_model: str, speed: float,
                 pronunciations: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Девять голосов Qwen – русские примеры")
        self.resize(740, 560)
        self.model_id = model_id
        self.local_model = local_model
        self.speed = speed
        self.rules = pronunciations
        self.worker = None
        self.ready_paths: dict[str, str] = {}
        self.buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Все голоса читают один русский текст. Родной язык профиля не русский; "
            "сравните произношение и ударения на слух."))
        layout.addWidget(QLabel("Текст образца: " + SAMPLE_TEXT))
        self.bar = QProgressBar()
        self.bar.setRange(0, 9)
        layout.addWidget(self.bar)
        for speaker, (description, native) in VOICE_INFO.items():
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{speaker} · {description} · родной: {native}"), 1)
            button = QPushButton("▶ Создать и послушать")
            button.clicked.connect(lambda checked=False, name=speaker: self.play_one(name))
            row.addWidget(button)
            self.buttons[speaker] = button
            layout.addLayout(row)
        controls = QHBoxLayout()
        self.all_button = QPushButton("Сгенерировать все девять")
        self.all_button.clicked.connect(self.generate_all)
        controls.addWidget(self.all_button)
        self.cancel_btn = QPushButton("Остановить после текущего")
        self.cancel_btn.clicked.connect(self.cancel)
        self.cancel_btn.setEnabled(False)
        controls.addWidget(self.cancel_btn)
        layout.addLayout(controls)
        self.status = QLabel("Для генерации нужен скачанный CustomVoice. Готовые пробы повторно не генерируются.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        probe = VoicePreviewWorker(model_id, local_model, [], speed, pronunciations)
        for speaker, button in self.buttons.items():
            target = probe.output_for(speaker)
            if target.is_file():
                self.ready_paths[speaker] = str(target)
                button.setText("▶ Послушать готовое")

    def _playing(self):
        return self.worker is not None and self.worker.isRunning()

    def play_one(self, speaker):
        if self._playing():
            return
        if speaker in self.ready_paths and Path(self.ready_paths[speaker]).is_file():
            self.play_path(self.ready_paths[speaker])
            return
        self.start_generation([speaker])

    def play_path(self, path):
        owner = self.parent()
        owner.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        owner.player.play()

    def generate_all(self):
        if not self._playing():
            self.start_generation(list(VOICE_INFO))

    def start_generation(self, speakers):
        if self._playing():
            return
        self.worker = VoicePreviewWorker(self.model_id, self.local_model, speakers,
                                         self.speed, self.rules)
        self.worker.audio_ready.connect(self.on_ready)
        self.worker.progress.connect(self.on_progress)
        self.worker.failure.connect(self.on_failure)
        self.worker.finished.connect(self.on_finished)
        for button in self.buttons.values():
            button.setEnabled(False)
        self.all_button.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.bar.setRange(0, len(speakers))
        self.bar.setValue(0)
        self.status.setText("Генерация голосовых образцов…")
        self.worker.start()

    def on_ready(self, speaker, path):
        self.ready_paths[speaker] = path
        self.buttons[speaker].setText("▶ Послушать готовое")
        if len(self.worker.speakers) == 1:
            self.play_path(path)

    def on_progress(self, complete, total):
        self.bar.setValue(complete)
        self.status.setText(f"Готово: {complete}/{total}. Каждый голос можно прослушать отдельно.")

    def on_failure(self, error):
        self.status.setText("Не удалось создать образец: " + error)

    def on_finished(self):
        for button in self.buttons.values():
            button.setEnabled(True)
        self.all_button.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    def cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop.set()
            self.cancel_btn.setEnabled(False)

    def closeEvent(self, event):
        if self._playing():
            QMessageBox.information(self, "Озвучка", "Дождитесь окончания текущего образца или остановите создание")
            event.ignore()
        else:
            event.accept()
