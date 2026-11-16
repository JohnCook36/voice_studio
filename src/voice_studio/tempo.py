"""Post-synthesis, pitch-preserving narration speed control."""
from __future__ import annotations

import math

from .engines import Audio

MIN_TEMPO = 0.5
MAX_TEMPO = 2.0


class TempoError(ValueError):
    pass


def validate_tempo(tempo: float) -> float:
    if not isinstance(tempo, (float, int)) or not math.isfinite(tempo):
        raise TempoError("Скорость должна быть конечным числом")
    tempo = float(tempo)
    if not MIN_TEMPO <= tempo <= MAX_TEMPO:
        raise TempoError(f"Скорость должна быть от {MIN_TEMPO}x до {MAX_TEMPO}x")
    return tempo


def change_tempo(audio: Audio, tempo: float) -> Audio:
    """Returns audio with a different duration, retaining the original sample rate.

    librosa's phase vocoder generally preserves pitch, but can introduce
    transient/sibilant artifacts, especially at extreme speeds.
    """
    tempo = validate_tempo(tempo)
    if abs(tempo - 1.0) < 1e-8:
        return audio
    try:
        import librosa
        import numpy as np
    except ImportError as exc:
        raise TempoError("Для регулировки скорости нужна библиотека librosa из Qwen-TTS") from exc
    samples = np.asarray(audio.samples, dtype=np.float32).reshape(-1)
    if samples.size == 0 or not np.isfinite(samples).all():
        raise TempoError("Некорректная запись для изменения скорости")
    if samples.size < 1024:
        raise TempoError("Слишком короткий звуковой фрагмент для безопасного изменения скорости")
    try:
        stretched = librosa.effects.time_stretch(samples, rate=tempo)
    except Exception as exc:
        raise TempoError(f"Ошибка изменения темпа: {exc}") from exc
    return Audio(stretched, audio.rate)
