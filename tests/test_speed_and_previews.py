from dataclasses import replace
import json
from pathlib import Path
import wave

import pytest
from voice_studio.engines import Audio, QwenEngine
from voice_studio.licensing import LicenseRecord, LicenseStatus
from voice_studio.markup import Style
from voice_studio.pipeline import Job, render
from voice_studio.tempo import TempoError, change_tempo, validate_tempo
from voice_studio.voice_profiles import VOICE_PROFILES, VOICE_SAMPLE_RU


class Fake:
    model_id = "fake-speed"
    calls = 0

    def synthesize(self, text, style, reference, reference_text):
        self.calls += 1
        self.last_text = text
        return Audio([0.0] * 16000, 16000)


def test_tempo_validation():
    for val in (0, -1, 2.5, float("nan"), float("inf")):
        with pytest.raises(TempoError):
            validate_tempo(val)
    assert validate_tempo(0.85) == .85
    test = Audio([0.1] * 10, 24000)
    assert change_tempo(test, 1.0) is test


def test_speed_changes_exported_wav_and_cache(tmp_path, monkeypatch):
    import voice_studio.pipeline as pipeline

    def fake_stretch(audio, speed):
        return Audio([0.0] * round(len(audio.samples) / speed), audio.rate)
    monkeypatch.setattr(pipeline, "change_tempo", fake_stretch)
    engine = Fake()
    lic = LicenseRecord(engine.model_id, LicenseStatus.REVIEW_REQUIRED, "test")
    base = Job(engine.model_id, "Слово.", tmp_path / "read.wav",
               speed=.8, pronunciations=(("слово", "сло́во"),))
    render(base, lic, engine)
    with wave.open(str(base.output), "rb") as wavefile:
        assert wavefile.getnframes() == 20000
    assert engine.last_text == "Сло́во."
    render(base, lic, engine)
    assert engine.calls == 1
    faster = replace(base, speed=1.25)
    render(faster, lic, engine)
    assert engine.calls == 2
    with wave.open(str(base.output), "rb") as wavefile:
        assert wavefile.getnframes() == 12800
    metadata = json.loads((tmp_path / "read.wav.provenance.json").read_text())
    assert metadata["global_speed"] == 1.25
    assert metadata["pronunciation_rules_count"] == 1


def test_invalid_compound_speed_fails_before_model(tmp_path):
    engine = Fake()
    record = LicenseRecord(engine.model_id, LicenseStatus.REVIEW_REQUIRED, "test")
    job = Job(engine.model_id, "[speed=2.0]Скорость.", tmp_path / "o.wav", speed=1.3)
    with pytest.raises(TempoError):
        render(job, record, engine)
    assert engine.calls == 0


def test_customvoice_accepts_postprocessing_speed():
    engine = QwenEngine("Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")
    engine.validate_style(Style(speed=.75))


def test_voice_catalog_has_nine_real_profiles():
    assert len(VOICE_PROFILES) == 9
    assert "Ryan" in VOICE_PROFILES and "Sohee" in VOICE_PROFILES
    assert all(native != "Русский" for _, native in VOICE_PROFILES.values())
    assert "Voice Studio" in VOICE_SAMPLE_RU
