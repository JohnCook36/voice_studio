"""Local, explicit pronunciation overrides. Never modifies the user's source text."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from .model_manager import default_models_root

PRONUNCIATION_FILE = "pronunciation.json"
WORD = re.compile(r"^[^\W\d_][\w\-\u0301]{0,79}$", re.UNICODE)
MAX_RULES = 2000


class PronunciationError(ValueError):
    pass


def default_dictionary_path() -> Path:
    return default_models_root().parent / PRONUNCIATION_FILE


def validate_rules(rules: dict[str, str]) -> dict[str, str]:
    if len(rules) > MAX_RULES:
        raise PronunciationError(f"Слишком много правил (максимум {MAX_RULES})")
    clean: dict[str, str] = {}
    for source, target in rules.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise PronunciationError("Словарь должен содержать пары строк")
        key, value = source.strip(), target.strip()
        if not WORD.fullmatch(key) or not value or len(value) > 120:
            raise PronunciationError(f"Некорректное правило произношения: {source!r}")
        if any(ch in value for ch in "[]\n\r"):
            raise PronunciationError("В заменах запрещены теги, переводы строк и скобки []")
        normalized = key.casefold()
        if normalized in clean:
            raise PronunciationError(f"Повторяется слово: {key}")
        clean[normalized] = value
    return clean


def apply_pronunciations(text: str, rules: dict[str, str]) -> str:
    """Word-boundary aware; keeps original text intact outside replacement.

    Ambiguous homographs must be changed manually in the document; a global rule
    cannot tell castle from lock without linguistic context.
    """
    if not rules:
        return text
    clean = validate_rules(rules)
    if not clean:
        return text
    # Combine into one pattern: do not recursively replace generated text.
    keys = sorted(clean, key=len, reverse=True)
    expression = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, keys)) + r")(?!\w)",
                            re.IGNORECASE | re.UNICODE)

    def replace(match: re.Match[str]) -> str:
        original = match.group()
        spoken = clean.get(original.casefold())
        if spoken is None:
            return original
        if original.isupper():
            return spoken.upper()
        if original[:1].isupper():
            return spoken[0].upper() + spoken[1:]
        return spoken

    return expression.sub(replace, text)


def load_dictionary(path: Path | None = None) -> dict[str, str]:
    file = path or default_dictionary_path()
    if not file.exists():
        return {}
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise PronunciationError("Неизвестный формат словаря произношений")
        data = payload.get("rules")
        if not isinstance(data, dict):
            raise PronunciationError("Словарь повреждён")
        return validate_rules(data)
    except (OSError, UnicodeError, ValueError) as exc:
        raise PronunciationError(f"Не удалось открыть словарь: {exc}") from exc


def save_dictionary(rules: dict[str, str], path: Path | None = None) -> Path:
    file = path or default_dictionary_path()
    cleaned = validate_rules(rules)
    file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "rules": cleaned}
    fd, temp = tempfile.mkstemp(prefix=".pronunciation_", suffix=".json", dir=file.parent)
    os.close(fd)
    try:
        Path(temp).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, file)
    finally:
        Path(temp).unlink(missing_ok=True)
    return file
