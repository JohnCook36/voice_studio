"""User-managed Qwen model downloads and safe local deletion.

Only model directories created under Voice Studio's own root can be removed.
External Hugging Face caches, voice samples and manually chosen directories are
never touched by the manager.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import stat

from .licensing import BUILTIN_LICENSES

MANIFEST = ".voice_studio_model.json"


class ModelManagerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelInfo:
    model_id: str
    path: Path
    status: str
    size_bytes: int


def default_models_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "VoiceStudio" / "models"


def _unsafe_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    junction = getattr(path, "is_junction", None)
    if callable(junction) and junction():
        return True
    return False


def _tree_size(folder: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(folder, followlinks=False):
        for name in dirs + files:
            item = Path(root) / name
            if _unsafe_link(item):
                raise ModelManagerError("Модель содержит символическую ссылку или junction")
        for name in files:
            item = Path(root) / name
            if item.is_file():
                total += item.stat(follow_symlinks=False).st_size
    return total


class ModelManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else default_models_root()

    def _path(self, model_id: str) -> Path:
        if model_id not in BUILTIN_LICENSES:
            raise ModelManagerError("Неизвестная модель; управление разрешено только моделями из каталога")
        # Exact allowlist maps names to a fixed one-level folder.
        folder = model_id.replace("/", "--")
        root = self.root.expanduser().absolute()
        if _unsafe_link(root) or _unsafe_link(root.parent):
            raise ModelManagerError("Папка моделей использует ссылку или junction")
        target = root / folder
        if _unsafe_link(target):
            raise ModelManagerError("Папка модели использует ссылку или junction")
        return target

    def path(self, model_id: str) -> Path:
        return self._path(model_id)

    def info(self, model_id: str) -> ModelInfo:
        target = self._path(model_id)
        if not target.exists():
            return ModelInfo(model_id, target, "not_downloaded", 0)
        if not target.is_dir():
            raise ModelManagerError("Путь модели занят файлом")
        size = _tree_size(target)
        marker = target / MANIFEST
        status = "partial"
        if marker.is_file():
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                if payload.get("model_id") == model_id and (target / "config.json").is_file():
                    status = "installed"
            except (OSError, ValueError):
                pass
        return ModelInfo(model_id, target, status, size)

    def list_models(self) -> list[ModelInfo]:
        return [self.info(model_id) for model_id in BUILTIN_LICENSES]

    def download(self, model_id: str) -> Path:
        target = self._path(model_id)
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise ModelManagerError("Отсутствует huggingface_hub в сборке") from exc
        target.mkdir(parents=True, exist_ok=True)
        # Hugging Face local_dir stores data here rather than in a hidden global
        # cache, allowing an exact, predictable user-controlled removal.
        snapshot_download(repo_id=model_id, local_dir=str(target))
        if not (target / "config.json").is_file():
            raise ModelManagerError("Модель скачана не полностью (нет config.json)")
        marker_tmp = target / (MANIFEST + ".tmp")
        marker_tmp.write_text(json.dumps({"model_id": model_id, "managed_by": "VoiceStudio"},
                                          ensure_ascii=False), encoding="utf-8")
        marker_tmp.replace(target / MANIFEST)
        return target

    def delete(self, model_id: str, *, synthesis_active: bool = False) -> int:
        if synthesis_active:
            raise ModelManagerError("Нельзя удалять модель во время генерации")
        info = self.info(model_id)
        if info.status == "not_downloaded":
            return 0
        target = self._path(model_id)
        # Refuse deleting any tree containing links or junctions, even nested,
        # and do not allow arbitrary externally selected model directories.
        _tree_size(target)
        shutil.rmtree(target)
        return info.size_bytes
