"""PySide6 desktop shell; never initializes heavy models on startup."""
import argparse
from pathlib import Path
import sys
from threading import Event

from .licensing import BUILTIN_LICENSES, LicenseStatus
from .pipeline import Job

MODELS = tuple(BUILTIN_LICENSES)
SPEAKERS = ("Ryan", "Aiden", "Serena", "Vivian", "Uncle_Fu", "Dylan", "Eric", "Ono_Anna", "Sohee")


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice Studio")
    parser.add_argument("--smoke", action="store_true", help="Проверить запуск окна без моделей")
    opts = parser.parse_args()
    try:
        from PySide6.QtCore import QThread, Signal, QTimer
        from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                                       QHBoxLayout, QTextEdit, QLabel, QComboBox, QPushButton,
                                       QLineEdit, QCheckBox, QFileDialog, QMessageBox,
                                       QProgressBar, QGroupBox)
    except ImportError as exc:
        raise SystemExit("Для графического интерфейса: pip install -e .[gui]") from exc

    class Worker(QThread):
        progress = Signal(int, int)
        success = Signal(str)
        failure = Signal(str)

        def __init__(self, job: Job, local_model: str, allow_download: bool):
            super().__init__()
            self.job = job
            self.local_model = local_model
            self.allow_download = allow_download
            self.stop = Event()

        def run(self):
            try:
                from .engines import QwenEngine
                from .pipeline import render
                engine = QwenEngine(self.job.model_id, local_model=self.local_model,
                                    download_allowed=self.allow_download)
                path = render(self.job, BUILTIN_LICENSES[self.job.model_id], engine,
                              progress=lambda done, total: self.progress.emit(done, total),
                              cancelled=self.stop.is_set)
                self.success.emit(str(path))
            except Exception as exc:
                self.failure.emit(f"{type(exc).__name__}: {exc}")

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Voice Studio · локальная озвучка (прототип)")
            self.resize(900, 720)
            self.worker = None
            box = QWidget()
            self.setCentralWidget(box)
            layout = QVBoxLayout(box)
            layout.addWidget(QLabel("Текст на русском. Теги: [emotion=sad], [pause=500ms], [speaker=Ryan]."))
            self.editor = QTextEdit()
            self.editor.setPlaceholderText("Вставьте текст для озвучки…")
            layout.addWidget(self.editor)
            line = QHBoxLayout()
            self.open_btn = QPushButton("Загрузить TXT/DOCX")
            self.open_btn.clicked.connect(self.open_document)
            line.addWidget(self.open_btn)
            line.addWidget(QLabel("Модель:"))
            self.models = QComboBox()
            for m in MODELS:
                self.models.addItem(m.split("12Hz-")[1], userData=m)
            line.addWidget(self.models, 1)
            layout.addLayout(line)
            line2 = QHBoxLayout()
            line2.addWidget(QLabel("Голос:"))
            self.speakers = QComboBox()
            self.speakers.addItems(SPEAKERS)
            line2.addWidget(self.speakers)
            line2.addWidget(QLabel("Папка весов (необязательно):"))
            self.model_dir = QLineEdit()
            line2.addWidget(self.model_dir, 1)
            b = QPushButton("…")
            b.clicked.connect(self.choose_model_dir)
            line2.addWidget(b)
            layout.addLayout(line2)
            self.download = QCheckBox("Разрешить скачивание модели из Hugging Face (несколько ГБ)")
            layout.addWidget(self.download)
            clone_box = QGroupBox("Клонирование: только свой или разрешённый голос (Base)")
            clone_layout = QVBoxLayout(clone_box)
            refline = QHBoxLayout()
            self.ref_path = QLineEdit()
            self.ref_path.setPlaceholderText("Путь к референсному WAV")
            refline.addWidget(self.ref_path)
            btn = QPushButton("Выбрать WAV")
            btn.clicked.connect(self.choose_reference)
            refline.addWidget(btn)
            clone_layout.addLayout(refline)
            self.ref_text = QLineEdit()
            self.ref_text.setPlaceholderText("Точная расшифровка аудиообразца")
            clone_layout.addWidget(self.ref_text)
            self.consent = QCheckBox("Это мой голос либо у меня есть разрешение владельца")
            clone_layout.addWidget(self.consent)
            layout.addWidget(clone_box)
            self.commercial = QCheckBox("Коммерческое использование (только проверенные лицензии)")
            self.commercial.toggled.connect(self.check_license)
            self.models.currentIndexChanged.connect(self.check_license)
            layout.addWidget(self.commercial)
            self.license_label = QLabel()
            layout.addWidget(self.license_label)
            controls = QHBoxLayout()
            self.start_btn = QPushButton("Озвучить и сохранить WAV/MP3")
            self.start_btn.clicked.connect(self.start)
            controls.addWidget(self.start_btn)
            self.cancel_btn = QPushButton("Остановить")
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.clicked.connect(self.cancel)
            controls.addWidget(self.cancel_btn)
            layout.addLayout(controls)
            self.bar = QProgressBar()
            self.bar.setRange(0, 1)
            layout.addWidget(self.bar)
            self.status = QLabel("Готово к работе. Для MP3 нужен ffmpeg в PATH.")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            self.check_license()

        def check_license(self):
            record = BUILTIN_LICENSES[self.models.currentData()]
            allowed = not self.commercial.isChecked() or record.status == LicenseStatus.COMMERCIAL_ALLOWED
            self.start_btn.setEnabled(allowed and not (self.worker and self.worker.isRunning()))
            self.license_label.setText("Лицензия: " + record.status.value + ". " +
                                       ("Коммерческая генерация заблокирована до аудита." if not allowed
                                        else "Не используйте аудио коммерчески без проверки лицензии."))

        def open_document(self):
            from .importers import load_text
            path, _ = QFileDialog.getOpenFileName(self, "Открыть текст", "", "Текст (*.txt *.docx)")
            if path:
                try:
                    self.editor.setPlainText(load_text(Path(path)))
                except Exception as exc:
                    QMessageBox.warning(self, "Ошибка импорта", str(exc))

        def choose_model_dir(self):
            path = QFileDialog.getExistingDirectory(self, "Папка локальной модели")
            if path:
                self.model_dir.setText(path)

        def choose_reference(self):
            path, _ = QFileDialog.getOpenFileName(self, "Голосовой образец", "", "Аудио (*.wav)")
            if path:
                self.ref_path.setText(path)

        def start(self):
            from .licensing import authorize
            model = self.models.currentData()
            try:
                authorize(BUILTIN_LICENSES[model], model, self.commercial.isChecked())
            except Exception as exc:
                QMessageBox.warning(self, "Лицензия", str(exc))
                return
            if not self.editor.toPlainText().strip():
                QMessageBox.warning(self, "Текст", "Введите текст")
                return
            if model.endswith("-Base") and (not self.ref_path.text().strip() or not self.ref_text.text().strip() or not self.consent.isChecked()):
                QMessageBox.warning(self, "Голос", "Для Base нужны WAV, текст образца и подтверждение согласия")
                return
            if not self.model_dir.text().strip() and not self.download.isChecked():
                QMessageBox.warning(self, "Модель", "Укажите локальную папку или разрешите скачивание")
                return
            if not self.model_dir.text().strip():
                result = QMessageBox.question(self, "Загрузка весов",
                    "Модель может занимать несколько ГБ. Начать скачивание?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if result != QMessageBox.StandardButton.Yes:
                    return
            output, _ = QFileDialog.getSaveFileName(self, "Сохранить озвучку",
                                                    "voice_studio.wav", "WAV (*.wav);;MP3 (*.mp3)")
            if not output:
                return
            ref = Path(self.ref_path.text()) if model.endswith("-Base") else None
            job = Job(model, self.editor.toPlainText(), Path(output),
                      speaker=self.speakers.currentText(), commercial=self.commercial.isChecked(),
                      reference=ref, reference_text=self.ref_text.text(), voice_consent=self.consent.isChecked())
            self.worker = Worker(job, self.model_dir.text(), self.download.isChecked())
            self.worker.progress.connect(self.on_progress)
            self.worker.success.connect(self.on_success)
            self.worker.failure.connect(self.on_failure)
            self.worker.finished.connect(self.on_finished)
            self.bar.setRange(0, 0)
            self.status.setText("Подготовка модели / генерация…")
            self.start_btn.setEnabled(False)
            self.cancel_btn.setEnabled(True)
            self.worker.start()

        def on_progress(self, current: int, total: int):
            self.bar.setRange(0, total)
            self.bar.setValue(current)
            self.status.setText(f"Готово фрагментов: {current}/{total}")

        def on_success(self, path: str):
            self.status.setText(f"Сохранено: {path} + provenance.json")
            QMessageBox.information(self, "Voice Studio", "Озвучка сохранена:\n" + path)

        def on_failure(self, message: str):
            self.status.setText(message)
            QMessageBox.warning(self, "Ошибка озвучки", message)

        def on_finished(self):
            self.cancel_btn.setEnabled(False)
            self.check_license()

        def cancel(self):
            if self.worker:
                self.worker.stop.set()
                self.cancel_btn.setEnabled(False)
                self.status.setText("Отмена после текущего фрагмента…")

        def closeEvent(self, event):
            if self.worker and self.worker.isRunning():
                QMessageBox.information(self, "Voice Studio", "Сначала остановите генерацию")
                event.ignore()
            else:
                event.accept()

    app = QApplication(sys.argv[:1])
    window = Window()
    window.show()
    if opts.smoke:
        QTimer.singleShot(600, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
