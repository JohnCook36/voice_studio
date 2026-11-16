"""Bootstrap tests use no downloads and cannot change system Python."""
from pathlib import Path
import pytest
from zipfile import ZipFile
from bootstrap.core import (
    BootstrapError, RuntimeStatus, application_env, install_source_zip,
    managed_python, probe_python, usable_external, ensure_managed_env, ensure_packages,
)

def test_missing_python(tmp_path):
    assert not probe_python(tmp_path / "missing").python_ok

def test_managed_environment_path(tmp_path):
    assert ".venv" in str(managed_python(tmp_path))

def test_isolated_application_path(tmp_path):
    env = application_env(tmp_path / "app-src.zip")
    assert env["PYTHONPATH"] == str(tmp_path / "app-src.zip")
    assert env["PYTHONNOUSERSITE"] == "1"

def test_source_zip_is_verified_and_reused(tmp_path):
    original = tmp_path / "source.zip"
    with ZipFile(original, "w") as archive:
        archive.writestr("voice_studio/__init__.py", '__version__ = "0.2.0a1"')
        archive.writestr("voice_studio/app.py", "print('ok')")
    assert install_source_zip(original, tmp_path / "local").is_file()
    assert install_source_zip(original, tmp_path / "local") == tmp_path / "local" / "app-src.zip"

def test_source_zip_cannot_contain_path_traversal(tmp_path):
    original = tmp_path / "source.zip"
    with ZipFile(original, "w") as archive:
        archive.writestr("voice_studio/__init__.py", "")
        archive.writestr("voice_studio/app.py", "pass")
        archive.writestr("../bad", "danger")
    with pytest.raises(BootstrapError):
        install_source_zip(original, tmp_path / "local")

def test_external_environment_needs_full_gui(monkeypatch,tmp_path):
    monkeypatch.setattr("bootstrap.core.probe_python",lambda x: RuntimeStatus(python_ok=True))
    with pytest.raises(BootstrapError):
        usable_external(tmp_path / "python.exe",False)

def test_complete_external_environment_can_be_reused(monkeypatch,tmp_path):
    monkeypatch.setattr("bootstrap.core.probe_python",lambda x: RuntimeStatus(
        python_ok=True,gui=True,qwen=True,cuda=True))
    assert usable_external(tmp_path / "python.exe",True).cuda

def test_existing_env_does_not_download_python(monkeypatch,tmp_path):
    py = managed_python(tmp_path)
    py.parent.mkdir(parents=True)
    py.touch()
    uv = tmp_path / "uv"
    uv.touch()
    monkeypatch.setattr("bootstrap.core.probe_python",lambda x: RuntimeStatus(python_ok=True))
    monkeypatch.setattr("bootstrap.core._run",lambda *a,**k: (_ for _ in ()).throw(AssertionError("download")))
    assert ensure_managed_env(uv,tmp_path) == py

def test_ready_packages_do_not_install_again(monkeypatch,tmp_path):
    py = tmp_path / "python"
    monkeypatch.setattr("bootstrap.core.probe_python",lambda x: RuntimeStatus(
        python_ok=True,gui=True,qwen=True,cuda=True))
    monkeypatch.setattr("bootstrap.core._run",lambda *a,**k: (_ for _ in ()).throw(AssertionError("install")))
    assert ensure_packages(tmp_path / "uv",py,neural=True).cuda

def test_gui_only_never_installs_cuda(monkeypatch,tmp_path):
    py = tmp_path / "python"
    states = iter([RuntimeStatus(python_ok=True),RuntimeStatus(python_ok=True,gui=True)])
    monkeypatch.setattr("bootstrap.core.probe_python",lambda x: next(states))
    calls = []
    monkeypatch.setattr("bootstrap.core._run",lambda cmd,**kwargs: calls.append(cmd))
    assert ensure_packages(tmp_path / "uv",py,neural=False).gui
    assert len(calls) == 1 and "torch" not in calls[0]

def test_neural_mode_installs_cudawheels_and_qwen(monkeypatch,tmp_path):
    py = tmp_path / "python"
    states = iter([RuntimeStatus(python_ok=True,gui=True),
                   RuntimeStatus(python_ok=True,gui=True,qwen=True,cuda=True)])
    monkeypatch.setattr("bootstrap.core.probe_python",lambda x: next(states))
    calls = []
    monkeypatch.setattr("bootstrap.core._run",lambda cmd,**kwargs: calls.append(cmd))
    assert ensure_packages(tmp_path / "uv",py,neural=True).cuda
    assert len(calls)==2 and "torch" in calls[0] and "qwen-tts" in calls[1]
