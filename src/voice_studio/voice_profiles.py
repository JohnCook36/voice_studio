"""The nine built-in Qwen3-TTS CustomVoice profiles and their native languages.

None of these predefined voices is a native Russian profile. Language="Russian"
is still supported, but pronunciation/intonation may be imperfect.
"""
VOICE_PROFILES: dict[str, tuple[str, str]] = {
    "Ryan": ("Мужской, энергичный", "Английский"),
    "Aiden": ("Мужской, светлый", "Английский"),
    "Serena": ("Женский, мягкий", "Китайский"),
    "Vivian": ("Женский, звонкий", "Китайский"),
    "Uncle_Fu": ("Мужской, зрелый", "Китайский"),
    "Dylan": ("Мужской, молодой", "Китайский"),
    "Eric": ("Мужской, живой", "Китайский"),
    "Ono_Anna": ("Женский, игривый", "Японский"),
    "Sohee": ("Женский, тёплый", "Корейский"),
}

VOICE_SAMPLE_RU = (
    "Добрый день! Это проба моего голоса в Voice Studio. "
    "Когда наступает вечер, город становится тише, "
    "а за окном зажигаются первые огни."
)
