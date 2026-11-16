import pytest
from voice_studio.engines import QwenEngine, EngineError
from voice_studio.markup import Style


def test_custom_voice_small_rejects_emotion():
    engine = QwenEngine("Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")
    with pytest.raises(EngineError, match="эмоциями"):
        engine.validate_style(Style(emotion="sad"))


def test_qwen_base_rejects_emotion_and_intensity():
    engine = QwenEngine("Qwen/Qwen3-TTS-12Hz-0.6B-Base")
    with pytest.raises(EngineError, match="эмоциями"):
        engine.validate_style(Style(emotion="anger"))
    with pytest.raises(EngineError, match="интенсивности"):
        engine.validate_style(Style(intensity=0.8))


def test_custom_voice_large_accepts_emotional_instruction_preflight():
    engine = QwenEngine("Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
    engine.validate_style(Style(emotion="sad", intensity=0.9))
    with pytest.raises(EngineError, match="манер|speed"):
        engine.validate_style(Style(voice="whisper"))
