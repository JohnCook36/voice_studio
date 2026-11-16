import json
import pytest
from voice_studio.pronunciation import (
    PronunciationError, apply_pronunciations, validate_rules,
    load_dictionary, save_dictionary
)


def test_replacements_keep_original_words_and_case():
    source = "Замок, замок, ЗАМОК, подзамок. Замо́к!"
    result = apply_pronunciations(source, {"замок": "замо́к"})
    assert result.startswith("Замо́к, замо́к, ЗАМО́К, подзамок.")
    # Already accented variant is not overwritten by the unaccented dictionary key.
    assert result.endswith("Замо́к!")


def test_no_recursive_replacements():
    assert apply_pronunciations("кот", {"кот": "слон", "слон": "лев"}) == "слон"


def test_new_terms_cannot_insert_markup():
    with pytest.raises(PronunciationError):
        validate_rules({"дом": "[speaker=Other]"})


def test_duplicate_casefold_rejected():
    with pytest.raises(PronunciationError):
        validate_rules({"Слово": "сло́во", "слово": "другое"})


def test_roundtrip_and_invalid_json(tmp_path):
    path = tmp_path / "rules.json"
    save_dictionary({"город": "го́род"}, path)
    assert load_dictionary(path) == {"город": "го́род"}
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    path.write_text("garbage", encoding="utf-8")
    with pytest.raises(PronunciationError):
        load_dictionary(path)
