import pytest
from voice_studio.markup import MarkupError, Pause, Speech, parse


def test_styles_and_pauses():
    e = parse("Начало. [pause=500ms][speaker=Anna][emotion=sad][intensity=0.7]Конец.")
    assert isinstance(e[0], Speech) and e[0].text == "Начало."
    assert e[1] == Pause(500)
    assert isinstance(e[2], Speech) and e[2].style.speaker == "Anna"
    assert e[2].style.emotion == "sad" and e[2].style.intensity == 0.7


def test_many_fragments_bounded():
    text = "Это начало предложения. " * 200
    parts = parse(text, max_chars=100)
    assert all(0 < len(x.text) <= 100 for x in parts if isinstance(x, Speech))
    assert " ".join(x.text for x in parts) == " ".join(text.split())


@pytest.mark.parametrize("t", ["[emotion=joy]Привет", "[pause=50000]Привет", "[speed=0]Привет", "[intensity=nan]Привет", "[unknown=x]Привет", "[speaker= ]Привет", " "])
def test_bad_markup(t):
    with pytest.raises(MarkupError):
        parse(t)
