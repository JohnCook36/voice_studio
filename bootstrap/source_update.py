"""Bootstrap-only update activation without importing the application runtime.

Both modules intentionally use the same on-disk metadata format. Kept standalone
because PySide6 and the voice_studio package may not yet be installed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from zipfile import ZipFile, BadZipFile

STAGED_ZIP = ".update-pending.zip"
STAGED_MANIFEST = ".update-pending.json"
MAX_BYTES = 25 * 1024 * 1024
VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")
DECLARATION = re.compile(r"""^__version__\s*=\s*['"]([^'"]+)['"]\s*$""", re.MULTILINE)


class SourceUpdateError(ValueError):
    pass


def version_key(value: str):
    match = VERSION.fullmatch(value)
    if not match:
        raise SourceUpdateError("Неверный номер версии")
    major, minor, patch, stage, seq = match.groups()
    return (int(major), int(minor), int(patch),
            {None: 3, "a": 0, "b": 1, "rc": 2}[stage], int(seq or 0))


def get_installed_version(path: Path) -> str:
    try:
        with ZipFile(path) as archive:
            if archive.getinfo("voice_studio/__init__.py").file_size > 1024 * 64:
                raise SourceUpdateError("Слишком большой файл версии")
            if "voice_studio/app.py" not in archive.namelist():
                raise SourceUpdateError("Отсутствует модуль программы")
            for item in archive.infolist():
                name = item.filename
                if item.file_size > 5 * 1024 * 1024 or name.startswith("/") or ".." in Path(name).parts or "\\" in name:
                    raise SourceUpdateError("Повреждённый пакет приложения")
                if not (name.startswith("voice_studio/") and (name.endswith(".py") or item.is_dir())):
                    raise SourceUpdateError("Недопустимое содержимое приложения")
            raw = archive.read("voice_studio/__init__.py").decode("utf-8")
        match = DECLARATION.search(raw)
        if not match:
            raise SourceUpdateError("Не указана версия приложения")
        result = match.group(1)
        version_key(result)
        return result
    except (BadZipFile, KeyError, OSError, UnicodeDecodeError) as exc:
        raise SourceUpdateError("Пакет приложения недействителен") from exc


def apply_pending_update(root: Path) -> str | None:
    pending, metadata = root / STAGED_ZIP, root / STAGED_MANIFEST
    if not pending.exists() and not metadata.exists():
        return None
    if not (pending.is_file() and metadata.is_file()):
        raise SourceUpdateError("Не полностью скачанное обновление")
    try:
        meta = json.loads(metadata.read_text(encoding="utf-8"))
        version, size, digest = str(meta["version"]), int(meta["size"]), str(meta["sha256"])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise SourceUpdateError("Повреждены метаданные обновления") from exc
    if not re.fullmatch(r"[a-f0-9]{64}", digest) or not 1 <= size <= MAX_BYTES or pending.stat().st_size != size:
        raise SourceUpdateError("Некорректные размер или контрольная сумма")
    h = hashlib.sha256()
    with pending.open("rb") as source:
        while chunk := source.read(128 * 1024):
            h.update(chunk)
    if h.hexdigest() != digest or get_installed_version(pending) != version:
        raise SourceUpdateError("Проверка обновления не прошла")
    installed = root / "app-src.zip"
    if installed.exists():
        old = get_installed_version(installed)
        if version_key(version) <= version_key(old):
            pending.unlink()
            metadata.unlink()
            return None
        # Preserve a known-good backup. If the updated app fails to start,
        # the previous bundle can be restored by a future recovery command.
        backup_tmp = root / ".app-src.backup.tmp"
        try:
            shutil.copyfile(installed, backup_tmp)
            os.replace(backup_tmp, root / "app-src.previous.zip")
        finally:
            backup_tmp.unlink(missing_ok=True)
    os.replace(pending, installed)
    metadata.unlink(missing_ok=True)
    return version
