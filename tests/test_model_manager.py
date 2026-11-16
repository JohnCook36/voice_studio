import json
from pathlib import Path
import pytest

from voice_studio.licensing import BUILTIN_LICENSES
from voice_studio.model_manager import ModelManager, ModelManagerError, MANIFEST


@pytest.fixture
def model_id():
    return next(iter(BUILTIN_LICENSES))


def test_list_all_models_undownloaded(tmp_path):
    manager = ModelManager(tmp_path)
    all_models = manager.list_models()
    assert len(all_models) == len(BUILTIN_LICENSES)
    assert all(x.status == "not_downloaded" and x.size_bytes == 0 for x in all_models)


def test_installed_size_and_delete_only_managed_folder(tmp_path, model_id):
    manager = ModelManager(tmp_path / "managed")
    path = manager.path(model_id)
    path.mkdir(parents=True)
    (path / "config.json").write_text("{}", encoding="utf-8")
    (path / "model.safetensors").write_bytes(b"abcdef" * 500)
    (path / MANIFEST).write_text(json.dumps({"model_id": model_id}), encoding="utf-8")
    outside = tmp_path / "personal_voice.wav"
    outside.write_bytes(b"personal")
    info = manager.info(model_id)
    assert info.status == "installed"
    assert info.size_bytes > 3000
    assert manager.delete(model_id) == info.size_bytes
    assert not path.exists() and outside.read_bytes() == b"personal"
    assert manager.delete(model_id) == 0


def test_partial_download_can_be_deleted(tmp_path, model_id):
    manager = ModelManager(tmp_path)
    path = manager.path(model_id)
    path.mkdir()
    (path / "broken").write_bytes(b"temp")
    assert manager.info(model_id).status == "partial"
    manager.delete(model_id)
    assert not path.exists()


def test_unknown_model_cannot_escape_root(tmp_path):
    manager = ModelManager(tmp_path)
    with pytest.raises(ModelManagerError):
        manager.delete("../../Documents")
    with pytest.raises(ModelManagerError):
        manager.path("SomeOther/Model")


def test_busy_synthesis_prohibits_delete(tmp_path, model_id):
    manager = ModelManager(tmp_path)
    path = manager.path(model_id)
    path.mkdir()
    (path / "partial").write_bytes(b"data")
    with pytest.raises(ModelManagerError, match="генерации"):
        manager.delete(model_id, synthesis_active=True)
    assert path.exists()


def test_refuse_symlink_inside_managed_tree(tmp_path, model_id):
    manager = ModelManager(tmp_path / "models")
    path = manager.path(model_id)
    path.mkdir(parents=True)
    personal = tmp_path / "personal"
    personal.mkdir()
    (personal / "private.txt").write_text("secret")
    try:
        (path / "private-link").symlink_to(personal, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable on this Windows runner")
    with pytest.raises(ModelManagerError, match="ссылку"):
        manager.delete(model_id)
    assert (personal / "private.txt").read_text() == "secret"
