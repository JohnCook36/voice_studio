"""Safe, opt-in application-code updates from official GitHub Releases.

The lightweight installation swaps only the application source ZIP. CUDA, models,
voice profiles and user files are never downloaded or modified by this updater.
An old, frozen all-in-one executable is deliberately not self-patched.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen
from urllib.error import URLError
from zipfile import ZipFile, BadZipFile

REPO = "JohnCook36/voice_studio"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=15"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
APP_ASSET = "voice_studio_source.zip"
UPDATE_PENDING = ".update-pending.zip"
UPDATE_METADATA = ".update-pending.json"
MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_RELEASES_BYTES = 2 * 1024 * 1024
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")
VERSION_DECL = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)


class UpdateError(RuntimeError):
    pass


def version_key(version: str) -> tuple[int, int, int, int, int]:
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise UpdateError(f"Неизвестный формат версии: {version}")
    major, minor, patch, stage, sequence = match.groups()
    return (
        int(major), int(minor), int(patch),
        {"a": 0, "b": 1, "rc": 2, None: 3}[stage],
        int(sequence or 0),
    )


def get_installed_version(source: Path) -> str:
    """Read bundled version without importing or executing downloaded Python."""
    try:
        with ZipFile(source) as archive:
            for item in archive.infolist():
                if item.file_size > 5 * 1024 * 1024 or item.filename.startswith("/"):
                    raise UpdateError("Неверный формат пакета обновления")
                if ".." in Path(item.filename).parts or "\\" in item.filename:
                    raise UpdateError("Некорректный путь в обновлении")
                if not (item.filename.startswith("voice_studio/") and
                        (item.filename.endswith(".py") or item.is_dir())):
                    raise UpdateError("Пакет содержит посторонние файлы")
            if "voice_studio/app.py" not in archive.namelist():
                raise UpdateError("Отсутствует приложение в обновлении")
            declaration = archive.read("voice_studio/__init__.py").decode("utf-8")
        match = VERSION_DECL.search(declaration)
        if not match:
            raise UpdateError("В пакете не найдена версия")
        version = match.group(1)
        version_key(version)
        return version
    except (OSError, BadZipFile, UnicodeDecodeError, KeyError) as exc:
        raise UpdateError(f"Повреждённый пакет приложения: {exc}") from exc


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    size: int
    sha256: str
    release_page: str
    notes: str = ""


def _safe_url(url: str) -> bool:
    prefix = f"https://github.com/{REPO}/releases/download/"
    return url.startswith(prefix) and url.endswith("/" + APP_ASSET) and "#" not in url and "?" not in url


def _download_json(url: str, *, timeout=12) -> object:
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "VoiceStudio-Updater/1",
    })
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_RELEASES_BYTES + 1)
    if len(raw) > MAX_RELEASES_BYTES:
        raise UpdateError("Слишком большой ответ GitHub")
    return json.loads(raw)


def check_update(current_version: str, *, fetch_json=_download_json) -> UpdateInfo | None:
    version_key(current_version)
    try:
        releases = fetch_json(RELEASES_API)
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise UpdateError(f"Проверка обновлений не удалась: {exc}") from exc
    if not isinstance(releases, list):
        raise UpdateError("GitHub вернул неожиданный формат")
    candidates: list[UpdateInfo] = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str):
            continue
        try:
            if version_key(tag) <= version_key(current_version):
                continue
        except UpdateError:
            continue
        # The updateable payload is a *source ZIP* created by our trusted
        # GitHub Actions publisher. No automatic binary execution.
        for asset in release.get("assets", []):
            if not isinstance(asset, dict) or asset.get("name") != APP_ASSET:
                continue
            url, digest, size = asset.get("browser_download_url"), asset.get("digest"), asset.get("size")
            if not (isinstance(url, str) and _safe_url(url)):
                continue
            if not (isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)):
                continue
            if not isinstance(size, int) or not 1 <= size <= MAX_SOURCE_BYTES:
                continue
            candidates.append(UpdateInfo(
                version=tag.lstrip("v"), url=url, size=size,
                sha256=digest.split(":", 1)[1].lower(),
                release_page=RELEASES_PAGE,
                notes=str(release.get("body") or "")[:1000],
            ))
    return max(candidates, key=lambda x: version_key(x.version)) if candidates else None


def stage_update(info: UpdateInfo, data_folder: Path, current_version: str,
                 *, opener=urlopen) -> Path:
    """Download and verify; a bootstrapper activates the staged file on restart."""
    if version_key(info.version) <= version_key(current_version):
        raise UpdateError("Версия не новее установленной")
    if not _safe_url(info.url) or not re.fullmatch(r"[0-9a-f]{64}", info.sha256):
        raise UpdateError("Источник обновления или контрольная сумма недействительны")
    if not 1 <= info.size <= MAX_SOURCE_BYTES:
        raise UpdateError("Слишком большой пакет обновления")
    installed = data_folder / "app-src.zip"
    if not installed.is_file():
        raise UpdateError(
            "Обновление внутри программы доступно для облегчённой установки. "
            "Для Portable EXE откройте официальный раздел GitHub Releases."
        )
    data_folder.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".download_", suffix=".zip", dir=data_folder)
    os.close(fd)
    tmp = Path(temporary)
    try:
        request = Request(info.url, headers={"User-Agent": "VoiceStudio-Updater/1"})
        hasher, total = hashlib.sha256(), 0
        with opener(request, timeout=35) as response, tmp.open("wb") as output:
            while block := response.read(128 * 1024):
                total += len(block)
                if total > info.size or total > MAX_SOURCE_BYTES:
                    raise UpdateError("Размер обновления не соответствует заявленному")
                hasher.update(block)
                output.write(block)
        if total != info.size or hasher.hexdigest() != info.sha256:
            raise UpdateError("Контрольная сумма обновления не совпала")
        if get_installed_version(tmp) != info.version:
            raise UpdateError("Версия внутри архива не совпала с опубликованной")
        # Save matching hash in metadata; rechecked on activation.
        metadata = {
            "version": info.version,
            "sha256": info.sha256,
            "size": info.size,
        }
        tmp.replace(data_folder / UPDATE_PENDING)
        pending_meta = data_folder / UPDATE_METADATA
        fd2, metadata_temp = tempfile.mkstemp(prefix=".update_", suffix=".json", dir=data_folder)
        os.close(fd2)
        try:
            Path(metadata_temp).write_text(json.dumps(metadata), encoding="utf-8")
            os.replace(metadata_temp, pending_meta)
        finally:
            Path(metadata_temp).unlink(missing_ok=True)
        return data_folder / UPDATE_PENDING
    finally:
        tmp.unlink(missing_ok=True)


def apply_pending_update(data_folder: Path) -> str | None:
    """Activated by bootstrapper before starting voice_studio, not by active UI."""
    pending = data_folder / UPDATE_PENDING
    meta_path = data_folder / UPDATE_METADATA
    if not (pending.exists() or meta_path.exists()):
        return None
    if not pending.is_file() or not meta_path.is_file():
        raise UpdateError("Обновление неполное; повторите скачивание")
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        version = str(metadata["version"])
        digest = str(metadata["sha256"])
        size = int(metadata["size"])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise UpdateError(f"Неверная информация об обновлении: {exc}") from exc
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise UpdateError("Неверная контрольная сумма пакета")
    if pending.stat().st_size != size or size > MAX_SOURCE_BYTES:
        raise UpdateError("Размер пакета изменён")
    hasher = hashlib.sha256()
    with pending.open("rb") as source:
        for chunk in iter(lambda: source.read(128 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != digest or get_installed_version(pending) != version:
        raise UpdateError("Пакет обновления не прошёл повторную проверку")
    installed = data_folder / "app-src.zip"
    if installed.is_file():
        installed_version = get_installed_version(installed)
        if version_key(version) <= version_key(installed_version):
            pending.unlink()
            meta_path.unlink()
            return None
        backup = data_folder / "app-src.previous.zip"
        # Atomic copy of known-good previous package, available for recovery.
        temp_backup = data_folder / ".app-src.previous.tmp"
        try:
            with installed.open("rb") as source, temp_backup.open("wb") as output:
                while chunk := source.read(128 * 1024):
                    output.write(chunk)
            os.replace(temp_backup, backup)
        finally:
            temp_backup.unlink(missing_ok=True)
    os.replace(pending, installed)
    meta_path.unlink(missing_ok=True)
    return version
