"""Small, explicit Voice Studio tag language. Tags are never read aloud."""
from dataclasses import dataclass, replace
import re


class MarkupError(ValueError):
    pass


@dataclass(frozen=True)
class Style:
    speaker: str = "Ryan"
    emotion: str = "neutral"
    intensity: float = 0.5
    voice: str = "normal"
    speed: float = 1.0


@dataclass(frozen=True)
class Speech:
    text: str
    style: Style


@dataclass(frozen=True)
class Pause:
    milliseconds: int


Event = Speech | Pause
TAG = re.compile(r"\[([a-z_]+)=([^\[\]\n]+)\]")
EMOTIONS = {"neutral", "happy", "sad", "anger", "fear", "surprise", "sarcasm"}
VOICES = {"normal", "whisper", "shout"}
SENTENCE = re.compile(r"(?<=[.!?…])\s+")


def _change(style: Style, key: str, value: str) -> Style:
    if key == "speaker":
        if not re.fullmatch(r"[\w-]{1,64}", value, flags=re.UNICODE):
            raise MarkupError("Некорректное имя персонажа")
        return replace(style, speaker=value)
    if key == "emotion":
        if value not in EMOTIONS:
            raise MarkupError(f"Неизвестная эмоция: {value}")
        return replace(style, emotion=value)
    if key == "voice":
        if value not in VOICES:
            raise MarkupError(f"Неизвестная манера речи: {value}")
        return replace(style, voice=value)
    if key in ("speed", "intensity"):
        try:
            number = float(value)
        except ValueError as exc:
            raise MarkupError(f"Число ожидается в [{key}=...]") from exc
        if not (0 <= number <= 1 if key == "intensity" else 0.5 <= number <= 2):
            raise MarkupError(f"Недопустимое значение: {key}={value}")
        return replace(style, **{key: number})
    raise MarkupError(f"Неизвестный тег: [{key}=...]")


def _split(text: str, limit: int) -> list[str]:
    """Split on word boundaries without losing words."""
    text = " ".join(text.split())
    if not text:
        return []
    if limit < 20:
        raise ValueError("limit must be >= 20")
    result: list[str] = []
    buffer = ""
    for sentence in SENTENCE.split(text):
        for word in sentence.split(" "):
            if len(word) > limit:
                raise MarkupError("Слишком длинное слово без пробелов; исправьте текст")
            candidate = f"{buffer} {word}" if buffer else word
            if len(candidate) > limit:
                result.append(buffer)
                buffer = word
            else:
                buffer = candidate
    if buffer:
        result.append(buffer)
    return result


def parse(text: str, *, speaker: str = "Ryan", max_chars: int = 320) -> list[Event]:
    if not text.strip():
        raise MarkupError("Текст пуст")
    style = Style(speaker=speaker)
    events: list[Event] = []
    cursor = 0

    def emit(span: str) -> None:
        for chunk in _split(span, max_chars):
            events.append(Speech(chunk, style))

    for match in TAG.finditer(text):
        emit(text[cursor:match.start()])
        key, value = match.groups()
        if key == "pause":
            ms_text = value.removesuffix("ms")
            if not ms_text.isdigit():
                raise MarkupError("Пауза должна быть целым количеством миллисекунд")
            ms = int(ms_text)
            if not 0 <= ms <= 30000:
                raise MarkupError("Пауза должна быть от 0 до 30000 мс")
            events.append(Pause(ms))
        else:
            style = _change(style, key, value)
        cursor = match.end()
    emit(text[cursor:])
    if not any(isinstance(e, Speech) for e in events):
        raise MarkupError("В документе нет текста для озвучки")
    for e in events:
        if isinstance(e, Speech) and re.search(r"\[[a-z_]+=[^\]]+\]", e.text):
            raise MarkupError("Обнаружен неподдерживаемый или некорректный тег")
    return events
