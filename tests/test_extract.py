import pymupdf
import pytest

from docqa.extract import ExtractionError, extract
from tests.conftest import make_pdf


def test_pdf_pages_are_labeled(sample_pdf):
    secs = extract(sample_pdf, ocr=None)
    assert [s.label for s in secs] == ["p. 1", "p. 2", "p. 3"]
    assert "three years" in secs[1].text
    assert secs[1].page_no == 2 and not secs[1].ocr


def test_scanned_pdf_page_uses_ocr(tmp_path):
    path = make_pdf(tmp_path / "scan.pdf", ["Normal text page here.", ""], scanned_page=1)
    calls = []

    def fake_ocr(png: bytes) -> str:
        calls.append(png)
        assert png.startswith(b"\x89PNG")
        return "Text read from the scan."

    secs = extract(path, ocr=fake_ocr)
    assert len(calls) == 1
    assert secs[1].ocr and secs[1].text == "Text read from the scan."
    assert secs[1].label == "p. 2"


def test_scanned_pdf_without_key_explains(tmp_path):
    path = make_pdf(tmp_path / "scan.pdf", [""], scanned_page=0)
    with pytest.raises(ExtractionError, match="scanned"):
        extract(path, ocr=None)


def test_encrypted_pdf(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    path = tmp_path / "locked.pdf"
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    with pytest.raises(ExtractionError, match="password"):
        extract(path, ocr=None)


def test_docx_sections_by_heading(sample_docx):
    secs = extract(sample_docx, ocr=None)
    assert [s.label for s in secs] == ['"Start of document"', '"Budget"', '"Timeline"']
    assert secs[1].paragraphs[1] == "The total budget for 2027 is 4.2 million dollars."
    assert "Design | June 2027" in secs[2].paragraphs


def test_text_blocks_and_encoding(sample_txt, tmp_path):
    secs = extract(sample_txt, ocr=None)
    assert secs[0].label == "lines 1–40" and secs[1].label == "lines 41–44"
    assert "화요일" in secs[1].text
    latin = tmp_path / "latin.txt"
    latin.write_bytes("Café crème".encode("cp1252"))
    assert "Caf" in extract(latin, ocr=None)[0].text


def test_image_uses_ocr(tmp_path):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), True)
    pix.clear_with(255)
    path = tmp_path / "photo.png"
    pix.save(path)
    secs = extract(path, ocr=lambda png: "사진 속 글자")
    assert secs[0].label == "image" and secs[0].ocr and secs[0].text == "사진 속 글자"


def test_empty_and_unsupported(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("   \n  ")
    with pytest.raises(ExtractionError, match="No readable text"):
        extract(empty, ocr=None)
    other = tmp_path / "data.xlsx"
    other.write_bytes(b"x")
    with pytest.raises(ExtractionError, match="Unsupported"):
        extract(other, ocr=None)
