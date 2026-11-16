"""Small Tk-based first-run bootstrapper.

The EXE bundles uv.exe and our Python source ZIP only. All large dependencies are
installed to a private environment after explicit user action.
"""
from __future__ import annotations

from pathlib import Path
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from bootstrap.core import (BootstrapError, data_root, managed_python,
                                find_system_python, probe_python, usable_external,
                                ensure_managed_env, ensure_packages,
                                install_source_zip, launch)
except ImportError:
    # PyInstaller entrypoint using the project root as a source path.
    from core import (BootstrapError, data_root, managed_python,
                      find_system_python, probe_python, usable_external,
                      ensure_managed_env, ensure_packages,
                      install_source_zip, launch)


def bundled_path(filename: str) -> Path:
    directory = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
    return directory / filename


def displayed_version(root: Path) -> str:
    """Show the actually installed app version, not merely setup.exe's age."""
    try:
        from bootstrap.source_update import get_installed_version
        installed = root / "app-src.zip"
        if installed.is_file():
            return get_installed_version(installed)
        bundled = bundled_path("voice_studio_source.zip")
        if bundled.is_file():
            return get_installed_version(bundled)
    except (OSError, ValueError):
        pass
    return "не определена"


class BootstrapWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Voice Studio – установка и запуск")
        root.geometry("670x450")
        root.minsize(620, 420)
        self.rootdir = data_root()
        self.queue: queue.Queue = queue.Queue()
        self.busy = False
        self.external: Path | None = None
        self.neural = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Диагностика готовности…")
        self.details = tk.StringVar(value="")
        self.existing = tk.StringVar(value="")
        self.version_display = tk.StringVar(value="Версия: проверяем…")

        # Permanent bottom-right footer, independent of installation progress.
        ttk.Label(root, textvariable=self.version_display,
                  foreground="#666666").pack(side="bottom", anchor="e", padx=16, pady=(0, 8))
        box = ttk.Frame(root, padding=18)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="Voice Studio", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(box, text="Проверка установленных компонентов перед загрузкой",
                  font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 12))
        ttk.Label(box, textvariable=self.status, wraplength=620).pack(anchor="w", pady=4)
        ttk.Label(box, textvariable=self.existing, wraplength=620).pack(anchor="w", pady=4)
        ttk.Checkbutton(box, text="Установить также Qwen-TTS и CUDA (несколько ГБ)",
                        variable=self.neural).pack(anchor="w", pady=8)
        ttk.Label(box, text="Без этой галочки установится только интерфейс. Позже движок можно добавить здесь.",
                  wraplength=620).pack(anchor="w")
        frame = ttk.Frame(box)
        frame.pack(fill="x", pady=14)
        self.install_btn = ttk.Button(frame, text="Проверить и установить / обновить", command=self.install)
        self.install_btn.pack(side="left", padx=(0, 8))
        self.run_btn = ttk.Button(frame, text="Запустить Voice Studio", command=self.run_app)
        self.run_btn.pack(side="left", padx=(0, 8))
        self.pick_btn = ttk.Button(frame, text="Выбрать существующий Python", command=self.pick_existing)
        self.pick_btn.pack(side="left")
        ttk.Label(box, textvariable=self.details, wraplength=620,
                  foreground="#555555").pack(anchor="w", pady=8)
        self.progress = ttk.Progressbar(box, mode="indeterminate")
        self.progress.pack(fill="x", pady=(8, 0))
        ttk.Label(box, text="Библиотеки ставятся только в отдельное окружение Voice Studio. "
                  "Выбранная существующая среда используется без изменений.",
                  wraplength=620).pack(anchor="w", pady=12)
        self.refresh()
        self.root.after(150, self.drain)

    def refresh(self):
        self.version_display.set("Voice Studio v" + displayed_version(self.rootdir))
        managed = probe_python(managed_python(self.rootdir))
        candidates = []
        for path in find_system_python():
            state = probe_python(path)
            if state.python_ok:
                candidates.append(f"{state.python_version}: {path}")
        self.existing.set("Найденный Python: " + ("; ".join(candidates[:3]) if candidates else
                          "совместимый не найден; uv может скачать Python автоматически"))
        self.status.set(f"Voice Studio: интерфейс {'готов' if managed.gui else 'не установлен'}; "
                        f"Qwen-TTS {'готов' if managed.qwen and managed.cuda else 'не установлен'}.")
        self.run_btn.state(["!disabled"] if managed.gui or self.external else ["disabled"])

    def pick_existing(self):
        path = filedialog.askopenfilename(title="Выберите python.exe совместимого окружения",
                                          filetypes=[("Python", "python.exe"), ("Все файлы", "*")])
        if not path:
            return
        try:
            state = usable_external(Path(path), require_qwen=self.neural.get())
        except BootstrapError as exc:
            messagebox.showwarning("Среда не подходит", str(exc))
            return
        self.external = Path(path)
        self.details.set(f"Используется установленная среда без переустановки: {self.external}. "
                         f"CUDA {'доступна' if state.cuda else 'не проверена для озвучки'}.")
        self.run_btn.state(["!disabled"])

    def work(self, action):
        if self.busy:
            return
        self.busy = True
        self.progress.start(15)
        self.install_btn.state(["disabled"])
        self.run_btn.state(["disabled"])
        self.pick_btn.state(["disabled"])
        thread = threading.Thread(target=self.worker, args=(action,), daemon=True)
        thread.start()

    def worker(self, action):
        try:
            source = install_source_zip(bundled_path("voice_studio_source.zip"), self.rootdir)
            if action == "install":
                if self.external:
                    state = usable_external(self.external, require_qwen=self.neural.get())
                    chosen = self.external
                else:
                    chosen = ensure_managed_env(bundled_path("uv.exe"), self.rootdir, self.send)
                    state = ensure_packages(bundled_path("uv.exe"), chosen,
                                            neural=self.neural.get(), reporter=self.send)
                self.queue.put(("done", "Окружение готово. Можно запускать Voice Studio."))
            else:
                chosen = self.external or managed_python(self.rootdir)
                status = probe_python(chosen)
                if not status.gui:
                    raise BootstrapError("Сначала установите интерфейс")
                if self.neural.get() and not (status.qwen and status.cuda):
                    raise BootstrapError("Нейросеть ещё не установлена. Нажмите кнопку установки")
                launch(chosen, source)
                self.queue.put(("done", "Voice Studio запущена."))
        except Exception as exc:
            self.queue.put(("error", str(exc)))

    def send(self, msg):
        self.queue.put(("message", str(msg)))

    def drain(self):
        try:
            while True:
                kind, value = self.queue.get_nowait()
                if kind == "message":
                    self.details.set(value)
                elif kind in ("done", "error"):
                    self.busy = False
                    self.progress.stop()
                    self.install_btn.state(["!disabled"])
                    self.pick_btn.state(["!disabled"])
                    if kind == "error":
                        self.details.set(value)
                        messagebox.showerror("Ошибка Voice Studio", value)
                    else:
                        self.details.set(value)
                    self.refresh()
        except queue.Empty:
            pass
        self.root.after(150, self.drain)

    def install(self):
        if not messagebox.askyesno(
            "Установка компонентов", "Voice Studio проверит установленные библиотеки и загрузит "
            "только необходимые из PyPI/PyTorch. "
            "Может потребоваться интернет и несколько гигабайт диска. Продолжить?"
        ):
            return
        self.work("install")

    def run_app(self):
        self.work("run")


def main():
    if '--smoke' in sys.argv:
        valid = bundled_path('uv.exe').is_file() and bundled_path('voice_studio_source.zip').is_file()
        raise SystemExit(0 if valid else 2)
    root = tk.Tk()
    BootstrapWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
