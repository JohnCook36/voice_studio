from pathlib import Path
import json
import wave
import pytest
from voice_studio.engines import Audio
from voice_studio.markup import Style
from voice_studio.pipeline import Cancelled, Job, render
from voice_studio.licensing import LicenseError, LicenseRecord, LicenseStatus


class Fake:
    model_id = "fake/model"
    calls = 0

    def synthesize(self, text: str, style: Style, reference: Path | None, reference_text: str) -> Audio:
        self.calls += 1
        return Audio([0.0, 0.2, -0.2, 0.0] * 40, 16000)


def trial(tmp_path: Path, txt="Первая фраза. [pause=150]Вторая фраза."):
    job = Job("fake/model", txt, tmp_path / "out.wav")
    lic = LicenseRecord("fake/model", LicenseStatus.REVIEW_REQUIRED, "test")
    return job, lic


def test_render_cache_provenance_and_pause(tmp_path):
    job, lic = trial(tmp_path)
    engine = Fake()
    messages = []
    output = render(job, lic, engine, lambda a, b: messages.append((a, b)))
    assert output.is_file()
    assert engine.calls == 2
    assert messages[-1] == (2, 2)
    with wave.open(str(output), "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.getnframes() >= 2400
    info = json.loads((tmp_path / "out.wav.provenance.json").read_text())
    assert not info["commercial_approved"] and len(info["segments"]) == 2
    render(job, lic, engine)
    assert engine.calls == 2


def test_change_one_fragment_regenerates_only_one(tmp_path):
    job, lic = trial(tmp_path)
    engine = Fake()
    render(job, lic, engine)
    newjob, lic = trial(tmp_path, "Первая фраза. [pause=150]Изменённая фраза.")
    render(newjob, lic, engine)
    assert engine.calls == 3


def test_cancel_preserves_cache_no_final(tmp_path):
    job, lic = trial(tmp_path)
    engine = Fake()
    counter = [0]
    def progress(a, b):
        counter[0] = a
    with pytest.raises(Cancelled):
        render(job, lic, engine, progress=progress, cancelled=lambda: counter[0] >= 1)
    assert engine.calls == 1
    assert not job.output.exists()
    render(job, lic, engine)
    assert engine.calls == 2


def test_commercial_fails_before_writing(tmp_path):
    job, lic = trial(tmp_path)
    job = Job(job.model_id, job.text, job.output, commercial=True)
    engine = Fake()
    with pytest.raises(LicenseError):
        render(job, lic, engine)
    assert engine.calls == 0 and not job.output.exists()


def test_ref_requires_consent(tmp_path):
    ref = tmp_path / "voice.wav"
    ref.write_bytes(b"sample")
    job, lic = trial(tmp_path)
    job = Job(job.model_id, job.text, job.output, reference=ref, voice_consent=False)
    with pytest.raises(ValueError):
        render(job, lic, Fake())

def test_multiple_base_speakers_rejected_before_synthesis(tmp_path):
    ref = tmp_path / "voice.wav"
    ref.write_bytes(b"sample")
    model = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    lic = LicenseRecord(model, LicenseStatus.REVIEW_REQUIRED, "test")
    job = Job(model, "[speaker=A]Первый. [speaker=B]Второй.", tmp_path / "out.wav",
              reference=ref, reference_text="Образец", voice_consent=True)
    class BaseFake(Fake):
        model_id = model
    engine = BaseFake()
    with pytest.raises(ValueError, match="нескольких персонажей"):
        render(job, lic, engine)
    assert engine.calls == 0
