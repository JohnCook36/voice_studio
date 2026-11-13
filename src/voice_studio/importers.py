"""Import TXT and DOCX without cloud services or external converters."""
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile


class ImportErrorText(ValueError):
    pass


def load_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".txt":
        raw = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-16", "cp1251"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                pass
        raise ImportErrorText("Неизвестная кодировка TXT")
    if ext == ".docx":
        try:
            with ZipFile(path) as z:
                doc = z.getinfo("word/document.xml")
                if doc.file_size > 100 * 1024 * 1024:
                    raise ImportErrorText("Слишком большой XML DOCX")
                root = ElementTree.fromstring(z.read(doc))
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            return "\n".join("".join(t.text or "" for t in p.iter(ns + "t"))
                             for p in root.iter(ns + "p"))
        except (KeyError, ValueError, ElementTree.ParseError) as exc:
            raise ImportErrorText("Некорректный DOCX") from exc
    if ext == ".epub":
        raise ImportErrorText("EPUB пока не реализован. Переведите книгу в TXT или DOCX")
    raise ImportErrorText("Поддерживаются TXT и DOCX")
