"""Dependency detection and isolation for the Voice Studio bootstrapper.

No global pip installs, no system PATH modification, no silent reuse of unrelated
site-packages. Full compatible external Python environments can be used read-only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass

GUI_REQUIREMENTS = ("PySide6>=6.7,<7", "huggingface_hub>=0.25")
QWEN_REQUIREMENTS = ("qwen-tts", "soundfile")
PYTHON_SPEC = "3.12"
CUDA_INDEX = "https://download.pytorch.org/whl/cu128"


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeStatus:
    python_ok: bool = False
    python_version: str = ""
    gui: bool = False
    qwen: bool = False
    cuda: bool = False
    torch_version: str = ""
    message: str = ""


def data_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "VoiceStudio"


def managed_python(root: Path) -> Path:
    if os.name == "nt":
        return root / "runtime" / ".venv" / "Scripts" / "python.exe"
    return root / "runtime" / ".venv" / "bin" / "python"


def _probe_source() -> str:
    return """
import importlib.util, json, platform, sys
modules = {name: importlib.util.find_spec(name) is not None for name in
           ('PySide6', 'huggingface_hub', 'qwen_tts', 'torch', 'soundfile')}
cuda = False
torch_version = ''
if modules['torch']:
    try:
        import torch
        torch_version = str(torch.__version__)
        cuda = bool(torch.cuda.is_available())
    except Exception:
        pass
print(json.dumps({'version': sys.version_info[:3], 'bits': platform.architecture()[0],
                  'modules': modules, 'cuda': cuda, 'torch_version': torch_version}))
"""


def probe_python(python: Path, timeout: int = 35) -> RuntimeStatus:
    if not python.is_file():
        return RuntimeStatus(message="Python не найден")
    try:
        p = subprocess.run([str(python), "-I", "-c", _probe_source()],
                           capture_output=True, text=True, timeout=timeout, check=False,
                           env={**os.environ, "PYTHONNOUSERSITE": "1"})
        if p.returncode:
            return RuntimeStatus(message="Не удалось проверить Python: " + (p.stderr or "")[-250:])
        payload = json.loads(p.stdout.strip().splitlines()[-1])
        ver = payload["version"]
        valid = (ver[0], ver[1]) == (3, 12) and payload["bits"] == "64bit"
        modules = payload["modules"]
        return RuntimeStatus(
            python_ok=valid, python_version=".".join(map(str, ver)),
            gui=valid and modules["PySide6"] and modules["huggingface_hub"],
            qwen=valid and modules["qwen_tts"] and modules["soundfile"] and modules["torch"],
            cuda=bool(payload.get("cuda")),
            torch_version=str(payload.get("torch_version", "")),
            message="Совместимая Python 3.12 x64" if valid else "Нужна Python 3.12 x64"
        )
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired, KeyError) as exc:
        return RuntimeStatus(message="Ошибка проверки Python: " + str(exc))


def find_system_python() -> list[Path]:
    """Candidates only. Every executable is checked by probe_python before use."""
    found: list[Path] = []
    for name in ("python.exe", "python3.12.exe", "python"):
        path = shutil.which(name)
        if path:
            candidate = Path(path)
            if candidate not in found:
                found.append(candidate)
    if os.name == "nt":
        try:
            p = subprocess.run(["py", "-3.12", "-c", "import sys;print(sys.executable)"],
                               capture_output=True, text=True, timeout=5, check=False)
            if p.returncode == 0:
                candidate = Path(p.stdout.strip())
                if candidate not in found:
                    found.append(candidate)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return found


def usable_external(python: Path, require_qwen: bool) -> RuntimeStatus:
    status = probe_python(python)
    if not status.python_ok or not status.gui:
        raise BootstrapError("В выбранном Python не найден полный совместимый набор GUI-библиотек")
    if require_qwen and not (status.qwen and status.cuda):
        raise BootstrapError("Уже установленная среда не содержит рабочие Qwen-TTS и CUDA")
    return status


def _run(command: list[str], *, reporter=None, timeout=3600, env=None) -> None:
    if reporter:
        reporter("Выполняется: " + command[1] if len(command) > 1 else command[0])
    completed = subprocess.run(command, capture_output=True, text=True,
                               timeout=timeout, check=False, env=env)
    if completed.returncode:
        output = (completed.stdout + "\n" + completed.stderr).strip()
        raise BootstrapError(f"Команда завершилась с кодом {completed.returncode}: {output[-1400:]}")


def ensure_managed_env(uv: Path, root: Path, reporter=None) -> Path:
    if not uv.is_file():
        raise BootstrapError("В установщике отсутствует проверенный uv.exe")
    python = managed_python(root)
    state = probe_python(python)
    if not state.python_ok:
        destination = python.parent.parent
        destination.parent.mkdir(parents=True, exist_ok=True)
        # uv reuses installed compatible Python if available; otherwise downloads
        # an isolated managed Python 3.12, without changing the user's PATH.
        _run([str(uv), "venv", "--python", PYTHON_SPEC,
              "--python-preference", "system", str(destination)], reporter=reporter)
    if not probe_python(python).python_ok:
        raise BootstrapError("Не удалось создать совместимое Python-окружение")
    return python


def ensure_packages(uv: Path, python: Path, *, neural: bool, reporter=None) -> RuntimeStatus:
    """Only install missing groups into our own runtime; uv reuses package cache."""
    status = probe_python(python)
    if not status.python_ok:
        raise BootstrapError("Неподдерживаемая версия Python")
    if not status.gui:
        if reporter:
            reporter("Устанавливается интерфейс PySide6 (только недостающие пакеты)…")
        _run([str(uv), "pip", "install", "--python", str(python),
              *GUI_REQUIREMENTS], reporter=reporter)
        status = probe_python(python)
        if not status.gui:
            raise BootstrapError("Не удалось подготовить интерфейс")
    if neural and (not status.qwen or not status.cuda):
        if reporter:
            reporter("Устанавливается CUDA-движок Qwen (может потребоваться несколько ГБ)…")
        # CUDA wheels must come from the official PyTorch package index.
        _run([str(uv), "pip", "install", "--python", str(python),
              "--index-url", CUDA_INDEX, "torch", "torchaudio"], reporter=reporter)
        _run([str(uv), "pip", "install", "--python", str(python),
              *QWEN_REQUIREMENTS], reporter=reporter)
        status = probe_python(python)
        if not status.qwen:
            raise BootstrapError("Qwen-TTS установлен не полностью")
        if not status.cuda:
            raise BootstrapError("PyTorch установлен, но CUDA недоступна. Проверьте драйвер NVIDIA")
    return status


def install_source_zip(resource: Path, root: Path) -> Path:
    """Activate verified pending update, never downgrade an installed version.

    The small setup EXE contains the version originally bundled with it.
    Blindly copying it on every launch would undo in-app updates.
    """
    from .source_update import apply_pending_update, get_installed_version, version_key, SourceUpdateError

    if not resource.is_file():
        raise BootstrapError("В установщике отсутствует исходный пакет Voice Studio")
    try:
        bundled_version = get_installed_version(resource)
    except SourceUpdateError as exc:
        raise BootstrapError(str(exc)) from exc

    root.mkdir(parents=True, exist_ok=True)
    target = root / "app-src.zip"

    try:
        apply_pending_update(root)
    except SourceUpdateError as exc:
        raise BootstrapError("Проверка обновления не прошла: " + str(exc)) from exc

    if target.is_file():
        try:
            installed_version = get_installed_version(target)
        except SourceUpdateError as exc:
            raise BootstrapError("Установленная версия повреждена: " + str(exc)) from exc
        if version_key(installed_version) >= version_key(bundled_version):
            return target

    fd, tmp = tempfile.mkstemp(prefix=".voice-studio_", suffix=".zip", dir=root)
    os.close(fd)
    try:
        shutil.copyfile(resource, tmp)
        if get_installed_version(Path(tmp)) != bundled_version:
            raise BootstrapError("Неверная контрольная сумма исходников")
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return target


def application_env(source_zip: Path) -> dict[str, str]:
    env = dict(os.environ)
    # One copy of our own source precedes any other Python projects in sys.path.
    env["PYTHONPATH"] = str(source_zip)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def launch(python: Path, source_zip: Path, *, smoke: bool = False):
    if not probe_python(python).gui:
        raise BootstrapError("GUI-компоненты не установлены")
    args = [str(python), "-m", "voice_studio"]
    if smoke:
        args.append("--smoke")
        return subprocess.run(args, env=application_env(source_zip),
                              timeout=30, check=False).returncode
    return subprocess.Popen(args, env=application_env(source_zip),
                            cwd=str(data_root()))
