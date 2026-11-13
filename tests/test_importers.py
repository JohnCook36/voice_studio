from pathlib import Path
from zipfile import ZipFile
import pytest
from voice_studio.importers import load_text, ImportErrorText


def test_txt_unicode_and_windows1251(tmp_path):
    path = tmp_path / "chapter.txt"
    path.write_text("Привет, мир!", encoding="utf-8-sig")
    assert load_text(path) == "Привет, мир!"
    path.write_bytes("Книга и слово".encode("cp1251"))
    assert load_text(path) == "Книга и слово"


def test_docx(tmp_path):
    path = tmp_path / "chapter.docx"
    with ZipFile(path, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Глава первая</w:t></w:r></w:p><w:p><w:r><w:t>Однажды...</w:t></w:r></w:p></w:body></w:document>')
    assert load_text(path) == "Глава первая\nОднажды..."


def test_epub_is_explicitly_not_supported(tmp_path):
    with pytest.raises(ImportErrorText, match="EPUB"):
        load_text(tmp_path / "book.epub")
