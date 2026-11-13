"""Pluggable synthesis engines; imports large ML libraries only on demand."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from .markup import Style


class EngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Audio:
    samples: Sequence[float]
    rate: int


class Engine(Protocol):
    model_id: str

    def synthesize(self, text: str, style: Style, reference: Path | None,
                   reference_text: str) -> Audio: ...


class QwenEngine:
    def __init__(self, model_id: str, *, download_allowed: bool = False,
                 local_model: str = "") -> None:
        if not model_id.startswith("Qwen/Qwen3-TTS-12Hz-") or not model_id.endswith(("-Base", "-CustomVoice")):
            raise EngineError("Неподдерживаемая Qwen-модель")
        self.model_id = model_id
        self.download_allowed = download_allowed
        self.local_model = local_model
        self.cache_identity = model_id + ":" + (str(Path(local_model).resolve()) if local_model else "official")
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        source = self.local_model.strip() or self.model_id
        if not self.local_model and not self.download_allowed:
            raise EngineError("Укажите локальную папку модели либо разрешите скачивание")
        if self.local_model and not Path(self.local_model).is_dir():
            raise EngineError("Папка модели не найдена")
        try:
            import torch
            from qwen_tts import Qwen3TTSModel
        except ImportError as exc:
            raise EngineError("Не установлены компоненты Qwen. Выполните pip install -e .[qwen]") from exc
        if not torch.cuda.is_available():
            raise EngineError("CUDA не обнаружена. Для первого прототипа нужна NVIDIA GPU с CUDA")
        try:
            self._model = Qwen3TTSModel.from_pretrained(
                source, device_map="cuda:0", dtype=torch.bfloat16, attn_implementation="sdpa")
        except Exception as exc:
            raise EngineError(f"Не удалось загрузить модель {self.model_id}: {exc}") from exc
        return self._model

    def validate_style(self, style: Style) -> None:
        is_base = self.model_id.endswith("-Base")
        if style.speed != 1.0 or style.voice != "normal":
            raise EngineError("Выбранный адаптер пока не поддерживает speed/whisper/shout")
        if is_base and style.emotion != "neutral":
            raise EngineError("Base: управление эмоциями не подтверждено – используйте CustomVoice 1.7B")
        if not is_base and "0.6B" in self.model_id and style.emotion != "neutral":
            raise EngineError("CustomVoice 0.6B не поддерживает управление эмоциями")
        if style.intensity != 0.5 and (is_base or "0.6B" in self.model_id or style.emotion == "neutral"):
            raise EngineError("Этот движок не поддерживает настройку интенсивности в текущем режиме")
        if not is_base and style.speaker not in {"Ryan", "Aiden", "Serena", "Vivian", "Uncle_Fu", "Dylan", "Eric", "Ono_Anna", "Sohee"}:
            raise EngineError("Для готового голоса выберите встроенный профиль Qwen")

    def synthesize(self, text: str, style: Style, reference: Path | None,
                   reference_text: str) -> Audio:
        self.validate_style(style)
        is_base = self.model_id.endswith("-Base")
        if is_base and (not reference or not reference.is_file() or not reference_text.strip()):
            raise EngineError("Для клонирования Base нужны референс WAV и его точная расшифровка")
        model = self._load()
        if is_base:
            wavs, rate = model.generate_voice_clone(
                text=text, language="Russian", ref_audio=str(reference), ref_text=reference_text)
        else:
            options = dict(text=text, language="Russian", speaker=style.speaker)
            if "1.7B" in self.model_id and style.emotion != "neutral":
                emotion_prompt = {"happy": "joyfully", "sad": "sadly", "anger": "angrily",
                                  "fear": "fearfully", "surprise": "with surprise", "sarcasm": "sarcastically"}
                strength = "subtly" if style.intensity < 0.35 else ("very expressively" if style.intensity > 0.75 else "clearly")
                options["instruct"] = f"Speak in Russian {strength} {emotion_prompt[style.emotion]}, with natural expression."
            wavs, rate = model.generate_custom_voice(**options)
        if not wavs or not rate:
            raise EngineError("Нейросеть вернула пустое аудио")
        return Audio(wavs[0], int(rate))
