"""Resumable per-segment synthesis with license checks and auditable output."""
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
import hashlib
import json
import os
import tempfile

from .audio import join_wavs, wav_rate, wav_to_mp3, write_wav
from .engines import Engine
from .licensing import LicenseRecord, authorize
from .markup import Pause, Speech, parse
from .pronunciation import apply_pronunciations, validate_rules
from .tempo import change_tempo, validate_tempo


class Cancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class Job:
    model_id: str
    text: str
    output: Path
    speaker: str = "Ryan"
    commercial: bool = False
    reference: Path | None = None
    reference_text: str = ""
    voice_consent: bool = False
    speed: float = 1.0
    pronunciations: tuple[tuple[str, str], ...] = ()


def _json_atomic(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".provenance_", dir=path.parent)
    os.close(fd)
    try:
        Path(tmp).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _digest_file(path: Path | None) -> str:
    if not path:
        return ""
    h = hashlib.sha256()
    with path.open("rb") as inp:
        for block in iter(lambda: inp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def render(job: Job, license_record: LicenseRecord, engine: Engine,
           progress: Callable[[int, int], None] | None = None,
           cancelled: Callable[[], bool] | None = None) -> Path:
    authorize(license_record, job.model_id, job.commercial)
    if engine.model_id != job.model_id:
        raise ValueError("Движок не соответствует выбранной модели")
    if job.output.suffix.lower() not in (".wav", ".mp3"):
        raise ValueError("Поддерживаются WAV и MP3")
    if not job.text.strip():
        raise ValueError("Текст пуст")
    if job.reference and (not job.reference.is_file() or not job.voice_consent):
        raise ValueError("Нужно разрешение владельца голоса и существующая запись")
    if job.model_id.endswith("-Base") and (not job.reference or not job.reference_text.strip()):
        raise ValueError("Для Base необходимы референс и текст образца")

    global_speed = validate_tempo(job.speed)
    rules = validate_rules(dict(job.pronunciations))
    events = parse(job.text, speaker=job.speaker)
    styles = [x.style for x in events if isinstance(x, Speech)]
    for style in styles:
        validate_tempo(global_speed * style.speed)
    if job.model_id.endswith("-Base") and len({x.speaker for x in styles}) > 1:
        raise ValueError("Для нескольких персонажей Base нужны отдельные голосовые профили; функция ещё не реализована")
    validator = getattr(engine, "validate_style", None)
    if validator is not None:
        for style in styles:
            validator(style)
    total = len(styles)
    ref_sha = _digest_file(job.reference)
    cache = job.output.parent / ".voice_studio_cache"
    cache.mkdir(parents=True, exist_ok=True)
    segments: list[Path | int] = []
    records: list[dict] = []
    done = 0
    for event in events:
        if cancelled and cancelled():
            raise Cancelled("Озвучка остановлена. Готовые фрагменты сохранены")
        if isinstance(event, Pause):
            segments.append(event.milliseconds)
            continue
        spoken_text = apply_pronunciations(event.text, rules)
        effective_speed = global_speed * event.style.speed
        key_data = dict(text=spoken_text, style=asdict(event.style), model=job.model_id,
                        effective_speed=effective_speed, pronunciation_engine="dict-v1",
                        revision=license_record.weights_revision or "unverified",
                        license_status=license_record.status.value,
                        engine_source=getattr(engine, "cache_identity", job.model_id),
                        reference_sha256=ref_sha, reference_text=job.reference_text,
                        generator_version="0.2.0a2")
        key = hashlib.sha256(json.dumps(key_data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        wav = cache / (key + ".wav")
        if not wav.is_file():
            audio = engine.synthesize(spoken_text, event.style, job.reference, job.reference_text)
            audio = change_tempo(audio, effective_speed)
            write_wav(wav, audio)
        wav_rate(wav)
        segments.append(wav)
        records.append({"segment_sha256": key, "model_id": job.model_id,
                        "license_status": license_record.status.value,
                        "weights_revision": license_record.weights_revision})
        done += 1
        if progress:
            progress(done, total)

    if job.output.suffix.lower() == ".wav":
        join_wavs(job.output, segments)
    else:
        intermediate = cache / (hashlib.sha256(str(job.output).encode()).hexdigest() + ".assembled.wav")
        join_wavs(intermediate, segments)
        try:
            wav_to_mp3(intermediate, job.output)
        finally:
            intermediate.unlink(missing_ok=True)

    metadata = {"format_version": 1, "model_id": job.model_id,
                "license_status": license_record.status.value,
                "license_label": license_record.license_name,
                "commercial_approved": job.commercial and license_record.output_commercial_allowed,
                "weights_revision": license_record.weights_revision,
                "license_evidence_url": license_record.evidence_url,
                "reference_sha256": ref_sha, "segments": records,
                "text_sha256": hashlib.sha256(job.text.encode("utf-8")).hexdigest(),
                "global_speed": global_speed, "pronunciation_rules_count": len(rules)}
    _json_atomic(job.output.with_suffix(job.output.suffix + ".provenance.json"), metadata)
    return job.output
