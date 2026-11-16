"""Offline regression tests: versions, download integrity and non-destructive restart."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import pytest

from voice_studio.updater import (
    UpdateError, UpdateInfo, check_update, version_key,
    get_installed_version, stage_update, apply_pending_update,
    RELEASES_API, RELEASES_PAGE, UPDATE_PENDING, UPDATE_METADATA,
)
from bootstrap.core import install_source_zip
from bootstrap.source_update import get_installed_version as bootstrap_version


def bundle(path: Path, version: str) -> bytes:
    with ZipFile(path, "w") as z:
        z.writestr("voice_studio/__init__.py", f'__version__ = "{version}"\n')
        z.writestr("voice_studio/app.py", "from . import __version__\n")
    return path.read_bytes()


def info_for(raw: bytes, version: str = "0.2.0a2") -> UpdateInfo:
    return UpdateInfo(
        version=version,
        url=f"https://github.com/JohnCook36/voice_studio/releases/download/v{version}/voice_studio_source.zip",
        size=len(raw),
        sha256=sha256(raw).hexdigest(),
        release_page=RELEASES_PAGE,
    )


def test_version_precedence():
    assert version_key("0.2.0a2") > version_key("0.2.0a1")
    assert version_key("v0.2.0") > version_key("0.2.0rc9")
    assert version_key("1.0.0b1") > version_key("1.0.0a20")
    assert version_key("1.1.0a1") > version_key("1.0.99")
    with pytest.raises(UpdateError):
        version_key("main")


def test_release_selection_only_verified_official_zip(tmp_path):
    raw = bundle(tmp_path / "new.zip", "0.2.0a2")
    info = info_for(raw)
    releases = [
        {"tag_name": "v0.2.0a2", "draft": False,
         "assets": [{"name": "voice_studio_source.zip",
                     "browser_download_url": info.url,
                     "digest": "sha256:" + info.sha256, "size": info.size}]},
        {"tag_name": "v3.0.0", "draft": False,
         "assets": [{"name": "voice_studio_source.zip",
                     "browser_download_url": "https://evil.example/update.zip",
                     "digest": "sha256:" + "f" * 64, "size": 200}]},
        {"tag_name": "v9.0.0", "draft": True,
         "assets": [{"name": "voice_studio_source.zip",
                     "browser_download_url": info.url,
                     "digest": "sha256:" + info.sha256, "size": info.size}]},
    ]
    def fetch(url):
        assert url == RELEASES_API
        return releases
    assert check_update("0.2.0a1", fetch_json=fetch).version == "0.2.0a2"
    assert check_update("0.2.0a2", fetch_json=fetch) is None


def test_stage_verify_and_bootstrap_activate_without_downgrade(tmp_path):
    old = tmp_path / "old.zip"
    new = tmp_path / "new.zip"
    bundle(old, "0.2.0a1")
    raw = bundle(new, "0.2.0a2")
    root = tmp_path / "user"
    initial = install_source_zip(old, root)
    assert get_installed_version(initial) == "0.2.0a1"
    info = info_for(raw)
    staged = stage_update(info, root, "0.2.0a1", opener=lambda *args, **kw: BytesIO(raw))
    assert staged.name == UPDATE_PENDING
    assert (root / UPDATE_METADATA).is_file()
    assert get_installed_version(initial) == "0.2.0a1"
    # Old .exe must activate staged update instead of reverting to bundled code.
    installed = install_source_zip(old, root)
    assert get_installed_version(installed) == "0.2.0a2"
    assert bootstrap_version(installed) == "0.2.0a2"
    assert get_installed_version(root / "app-src.previous.zip") == "0.2.0a1"
    assert install_source_zip(old, root) == installed
    assert get_installed_version(installed) == "0.2.0a2"


def test_tampered_download_refused_and_previous_version_preserved(tmp_path):
    old = tmp_path / "old.zip"
    new = tmp_path / "new.zip"
    bundle(old, "0.2.0a1")
    raw = bundle(new, "0.2.0a2")
    root = tmp_path / "user"
    install_source_zip(old, root)
    info = info_for(raw)
    with pytest.raises(UpdateError, match="Контрольная сумма"):
        stage_update(info, root, "0.2.0a1", opener=lambda *a, **kw: BytesIO(raw[:-1] + bytes([raw[-1] ^ 1])))
    assert get_installed_version(root / "app-src.zip") == "0.2.0a1"
    assert not (root / UPDATE_PENDING).exists()


def test_reject_missing_managed_install(tmp_path):
    raw = bundle(tmp_path / "new.zip", "0.2.0a2")
    with pytest.raises(UpdateError, match="облегчённой"):
        stage_update(info_for(raw), tmp_path / "not-installed", "0.2.0a1")


def test_reject_update_when_version_in_zip_differs(tmp_path):
    old = tmp_path / "old.zip"
    new = tmp_path / "new.zip"
    bundle(old, "0.2.0a1")
    raw = bundle(new, "0.2.0a3")
    root = tmp_path / "user"
    install_source_zip(old, root)
    with pytest.raises(UpdateError, match="Версия внутри"):
        stage_update(info_for(raw, "0.2.0a2"), root, "0.2.0a1",
                     opener=lambda *a, **kw: BytesIO(raw))


def test_reject_corrupt_pending_metadata(tmp_path):
    old = tmp_path / "old.zip"
    new = tmp_path / "new.zip"
    bundle(old, "0.2.0a1")
    raw = bundle(new, "0.2.0a2")
    root = tmp_path / "user"
    install_source_zip(old, root)
    stage_update(info_for(raw), root, "0.2.0a1", opener=lambda *a, **kw: BytesIO(raw))
    (root / UPDATE_PENDING).write_bytes(b"invalid")
    with pytest.raises(Exception):
        apply_pending_update(root)
    assert get_installed_version(root / "app-src.zip") == "0.2.0a1"


def test_no_downgrade_to_older_installer(tmp_path):
    old = tmp_path / "old.zip"
    newer = tmp_path / "new.zip"
    bundle(old, "0.2.0a1")
    bundle(newer, "0.2.0a2")
    root = tmp_path / "user"
    install_source_zip(newer, root)
    install_source_zip(old, root)
    assert bootstrap_version(root / "app-src.zip") == "0.2.0a2"
