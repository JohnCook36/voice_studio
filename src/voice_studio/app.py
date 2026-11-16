"""PySide6 desktop shell; never initializes heavy models on startup."""
import argparse
from pathlib import Path
import sys
from threading import Event

from .licensing import BUILTIN_LICENSES, LicenseStatus
from .pipeline import Job
from .model_manager import ModelManager, ModelManagerError

MODELS = tuple(BUILTIN_LICENSES)
SPEAKERS = ("Ryan", "Aiden", "Serena", "Vivian", "Uncle_Fu", "Dylan", "Eric", "Ono_Anna", "Sohee")


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice Studio")
    parser.add_argument("--smoke", action="store_true", help="Проверить запуск окна без моделей")
    opts = parser.parse_args()
    try:
        from PySide6.QtCore import QThread, Signal, QTimer, QUrl
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                                       QHBoxLayout, QTextEdit, QLabel, QComboBox, QPushButton,
                                       QLineEdit, QCheckBox, QFileDialog, QMessageBox,
                                       QProgressBar, QGroupBox, QDialog)
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

    class DownloadWorker(QThread):
        success = Signal(str)
        failure = Signal(str)

        def __init__(self, manager: ModelManager, model_id: str):
            super().__init__()
            self.manager = manager
            self.model_id = model_id

        def run(self):
            try:
                self.success.emit(str(self.manager.download(self.model_id)))
            except Exception as exc:
                self.failure.emit(f"{type(exc).__name__}: {exc}")

    class ManagerDialog(QDialog):
        def __init__(self, manager: ModelManager, parent=None):
            super().__init__(parent)
            self.manager = manager
            self.download_worker = None
            self.setWindowTitle("Управление моделями – Voice Studio")
            self.resize(630, 300)
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("Скачанные модели хранятся в: " + str(manager.root)))
            self.selector = QComboBox()
            for mid in MODELS:
                self.selector.addItem(mid.split("12Hz-")[1], userData=mid)
            self.selector.currentIndexChanged.connect(self.refresh)
            layout.addWidget(self.selector)
            self.size_status = QLabel()
            self.size_status.setWordWrap(True)
            layout.addWidget(self.size_status)
            layout.addWidget(QLabel(
                "Можно удалять только модели, скачанные через Voice Studio. "
                "Чужие папки, голоса и аудиозаписи не затрагиваются."))
            actions = QHBoxLayout()
            self.download_btn = QPushButton("Скачать выбранную модель")
            self.download_btn.clicked.connect(self.download_model)
            actions.addWidget(self.download_btn)
            self.delete_btn = QPushButton("Удалить выбранную модель")
            self.delete_btn.clicked.connect(self.delete_model)
            actions.addWidget(self.delete_btn)
            self.refresh_btn = QPushButton("Обновить")
            self.refresh_btn.clicked.connect(self.refresh)
            actions.addWidget(self.refresh_btn)
            layout.addLayout(actions)
            self.message = QLabel()
            self.message.setWordWrap(True)
            layout.addWidget(self.message)
            self.refresh()

        def refresh(self):
            try:
                info = self.manager.info(self.selector.currentData())
                status_names = {"not_downloaded": "Не загружена",
                                "partial": "Неполная загрузка",
                                "installed": "Загружена"}
                gib = info.size_bytes / (1024 ** 3)
                self.size_status.setText(
                    f"Статус: {status_names[info.status]} | Занято: {gib:.2f} ГБ")
                busy = self.download_worker is not None and self.download_worker.isRunning()
                self.download_btn.setEnabled(not busy)
                self.delete_btn.setEnabled(not busy and info.status != "not_downloaded"
                                           and not (self.parent().worker and self.parent().worker.isRunning()))
            except Exception as exc:
                self.message.setText(str(exc))
                self.download_btn.setEnabled(False)
                self.delete_btn.setEnabled(False)

        def download_model(self):
            if self.download_worker and self.download_worker.isRunning():
                return
            model_id = self.selector.currentData()
            if QMessageBox.question(
                self, "Скачать модель",
                f"Загрузить {model_id} из Hugging Face? Файлы могут занимать несколько ГБ. "
                "Нужен интернет; коммерческие права этой ревизии пока не подтверждены.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return
            self.download_worker = DownloadWorker(self.manager, model_id)
            self.download_worker.success.connect(self.on_download_success)
            self.download_worker.failure.connect(self.on_download_failure)
            self.download_worker.finished.connect(self.refresh)
            self.message.setText("Загрузка модели… Закрывать программу во время загрузки нельзя.")
            self.download_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            self.download_worker.start()

        def on_download_success(self, path):
            self.message.setText("Модель сохранена: " + path)
            self.refresh()

        def on_download_failure(self, error):
            self.message.setText("Ошибка загрузки: " + error +
                                 ". Неполные файлы можно удалить или попробовать скачать снова.")
            self.refresh()

        def delete_model(self):
            model_id = self.selector.currentData()
            if self.parent().worker and self.parent().worker.isRunning():
                QMessageBox.warning(self, "Модель занята", "Дождитесь окончания озвучки")
                return
            try:
                info = self.manager.info(model_id)
                if info.status == "not_downloaded":
                    return
                if QMessageBox.question(
                    self, "Удалить модель",
                    f"Удалить модель {model_id}? Будут удалены только файлы в:\n{info.path}\n"
                    f"Объём: {info.size_bytes / (1024 ** 3):.2f} ГБ. Отменить это действие нельзя.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                ) != QMessageBox.StandardButton.Yes:
                    return
                freed = self.manager.delete(model_id)
                self.message.setText(f"Удалено: {freed / (1024 ** 3):.2f} ГБ")
                self.refresh()
            except Exception as exc:
                QMessageBox.warning(self, "Ошибка удаления", str(exc))

        def closeEvent(self, event):
            if self.download_worker and self.download_worker.isRunning():
                QMessageBox.warning(self, "Идёт загрузка", "Дождитесь окончания загрузки модели")
                event.ignore()
            else:
                event.accept()

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Voice Studio · локальная озвучка (прототип)")
            self.resize(900, 720)
            self.worker = None
            self.manager = ModelManager()
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)
            self.audio_output.setVolume(0.8)
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
            line2.addWidget(QLabel("Внешняя папка (необязательно):"))
            self.model_dir = QLineEdit()
            self.model_dir.setPlaceholderText("Внешние модели здесь не удаляются")
            line2.addWidget(self.model_dir, 1)
            b = QPushButton("…")
            b.clicked.connect(self.choose_model_dir)
            line2.addWidget(b)
            layout.addLayout(line2)
            model_line = QHBoxLayout()
            self.model_status = QLabel()
            model_line.addWidget(self.model_status, 1)
            self.manage_btn = QPushButton("Модели: скачать / удалить")
            self.manage_btn.clicked.connect(self.open_model_manager)
            model_line.addWidget(self.manage_btn)
            layout.addLayout(model_line)
            self.models.currentIndexChanged.connect(self.update_model_status)
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
            self.play_btn = QPushButton("▶ Прослушать готовое")
            self.play_btn.setEnabled(False)
            self.play_btn.clicked.connect(self.play_last)
            controls.addWidget(self.play_btn)
            self.pause_btn = QPushButton("⏸ Пауза")
            self.pause_btn.clicked.connect(self.player.pause)
            controls.addWidget(self.pause_btn)
            layout.addLayout(controls)
            self.bar = QProgressBar()
            self.bar.setRange(0, 1)
            layout.addWidget(self.bar)
            self.status = QLabel("Готово к работе. Для MP3 нужен ffmpeg в PATH.")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            self.check_license()
            self.update_model_status()

        def update_model_status(self):
            try:
                model = self.models.currentData()
                info = self.manager.info(model)
                human = {"installed": "скачана", "partial": "неполная загрузка",
                         "not_downloaded": "не скачана"}[info.status]
                self.model_status.setText(f"Выбранная модель: {human}, {info.size_bytes / (1024 ** 3):.2f} ГБ")
            except Exception as exc:
                self.model_status.setText("Ошибка списка моделей: " + str(exc))

        def open_model_manager(self):
            if self.worker and self.worker.isRunning():
                QMessageBox.information(self, "Модели", "Остановите озвучку перед управлением моделями")
                return
            dialog = ManagerDialog(self.manager, self)
            dialog.exec()
            self.update_model_status()

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
            local_model = self.model_dir.text().strip()
            if not local_model:
                try:
                    info = self.manager.info(model)
                except ModelManagerError as exc:
                    QMessageBox.warning(self, "Модель", str(exc))
                    return
                if info.status != "installed":
                    QMessageBox.warning(self, "Модель не скачана",
                        "Откройте «Модели: скачать / удалить» и скачайте выбранную модель.")
                    return
                local_model = str(info.path)
            output, _ = QFileDialog.getSaveFileName(self, "Сохранить озвучку",
                                                    "voice_studio.wav", "WAV (*.wav);;MP3 (*.mp3)")
            if not output:
                return
            ref = Path(self.ref_path.text()) if model.endswith("-Base") else None
            job = Job(model, self.editor.toPlainText(), Path(output),
                      speaker=self.speakers.currentText(), commercial=self.commercial.isChecked(),
                      reference=ref, reference_text=self.ref_text.text(), voice_consent=self.consent.isChecked())
            self.worker = Worker(job, local_model, False)
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
            self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
            self.play_btn.setEnabled(True)
            self.status.setText(f"Сохранено: {path} + provenance.json")
            QMessageBox.information(self, "Voice Studio", "Озвучка сохранена:\n" + path)

        def on_failure(self, message: str):
            self.status.setText(message)
            QMessageBox.warning(self, "Ошибка озвучки", message)

        def on_finished(self):
            self.cancel_btn.setEnabled(False)
            self.check_license()

        def play_last(self):
            if self.player.source().isValid():
                self.player.play()

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
