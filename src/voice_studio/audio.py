"""PCM16 WAV output, segment joining and optional local ffmpeg MP3 conversion."""
from array import array
from pathlib import Path
import math
import os
import shutil
import subprocess
import tempfile
import wave

from .engines import Audio


class AudioError(RuntimeError):
    pass


def write_wav(path: Path, audio: Audio) -> None:
    if audio.rate < 8000 or audio.rate > 192000:
        raise AudioError("Некорректная частота дискретизации")
    samples = list(audio.samples)
    if not samples:
        raise AudioError("Пустая запись")
    vals = array("h")
    for sample in samples:
        val = float(sample)
        if not math.isfinite(val):
            raise AudioError("Некорректные значения аудио")
        vals.append(round(max(-1.0, min(1.0, val)) * 32767))
    if os.sys.byteorder != "little":
        vals.byteswap()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".segment_", suffix=".wav", dir=path.parent)
    os.close(fd)
    try:
        with wave.open(temp, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(audio.rate)
            writer.writeframes(vals.tobytes())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def wav_rate(path: Path) -> int:
    with wave.open(str(path), "rb") as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
            raise AudioError(f"Нужен монофонический PCM16 WAV: {path}")
        return reader.getframerate()


def join_wavs(target: Path, segments: list[Path | int]) -> None:
    rate = next((wav_rate(e) for e in segments if isinstance(e, Path)), None)
    if rate is None:
        raise AudioError("Нет фрагментов для объединения")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".render_", suffix=".wav", dir=target.parent)
    os.close(fd)
    try:
        with wave.open(temp, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(rate)
            for item in segments:
                if isinstance(item, int):
                    writer.writeframes(b"\0\0" * round(rate * item / 1000))
                else:
                    with wave.open(str(item), "rb") as reader:
                        if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getframerate() != rate:
                            raise AudioError("Частота/формат фрагмента не совпадает")
                        while frames := reader.readframes(16384):
                            writer.writeframes(frames)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def wav_to_mp3(wav_file: Path, mp3_file: Path) -> None:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise AudioError("Для MP3 установите ffmpeg и добавьте его в PATH")
    fd, temp = tempfile.mkstemp(prefix=".encode_", suffix=".mp3", dir=mp3_file.parent)
    os.close(fd)
    try:
        result = subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-y", "-i",
                                 str(wav_file), "-codec:a", "libmp3lame", "-qscale:a", "2", temp],
                                capture_output=True, text=True, timeout=3600, check=False)
        if result.returncode or not os.path.getsize(temp):
            raise AudioError("MP3-конвертация не удалась: " + result.stderr[-500:])
        os.replace(temp, mp3_file)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
