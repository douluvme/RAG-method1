"""Turn uploaded files into labeled sections of text.

A *section* is the unit the AI cites: one page of a PDF, one heading block of a
Word document, a block of lines in a text file, or a whole image.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf
from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from . import config

ProgressFn = Callable[[str, float], None]
OcrFn = Callable[[bytes], str]

PDF_EXT = {".pdf"}
DOCX_EXT = {".docx"}
TEXT_EXT = {".txt", ".md", ".markdown"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SUPPORTED_EXT = PDF_EXT | DOCX_EXT | TEXT_EXT | IMAGE_EXT

TEXT_BLOCK_LINES = 40
OCR_DPI = 150


class ExtractionError(Exception):
    """A problem the user should be told about in plain words."""


@dataclass
class Section:
    label: str  # human-readable location, e.g. "p. 12"
    text: str
    page_no: int | None = None  # 1-based PDF page, for page previews
    paragraphs: list[str] = field(default_factory=list)  # DOCX: for ¶ numbers
    first_line: int | None = None  # text files: line number of first line
    ocr: bool = False


def _noop(msg: str, frac: float) -> None:
    pass


def extract(path: Path, ocr: OcrFn | None, progress: ProgressFn = _noop) -> list[Section]:
    ext = path.suffix.lower()
    if ext in PDF_EXT:
        sections = _extract_pdf(path, ocr, progress)
    elif ext in DOCX_EXT:
        sections = _extract_docx(path)
    elif ext in TEXT_EXT:
        sections = _extract_text(path)
    elif ext in IMAGE_EXT:
        sections = _extract_image(path, ocr, progress)
    else:
        raise ExtractionError(
            f"Unsupported file type '{ext}'. Supported: PDF, DOCX, TXT, MD, PNG, JPG, WEBP, GIF."
        )
    sections = [s for s in sections if s.text.strip()]
    if not sections:
        raise ExtractionError("No readable text was found in this file.")
    return sections


# ---------- PDF ----------

def _extract_pdf(path: Path, ocr: OcrFn | None, progress: ProgressFn) -> list[Section]:
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # corrupt or not really a PDF
        raise ExtractionError(f"This PDF could not be opened ({e}).") from e
    with doc:
        if doc.needs_pass:
            raise ExtractionError("This PDF is password-protected. Remove the password and upload again.")
        sections = []
        n = doc.page_count
        for i, page in enumerate(doc):
            progress(f"Reading page {i + 1}/{n}", i / max(n, 1))
            text = page.get_text("text").strip()
            is_ocr = False
            if len(text) < config.MIN_TEXT_CHARS_PER_PAGE and _page_has_ink(page):
                if ocr is None:
                    raise ExtractionError(
                        "This PDF contains scanned pages, which need an OpenAI API key to read."
                    )
                progress(f"Page {i + 1}/{n} looks scanned, reading it with AI vision", i / max(n, 1))
                text = ocr(render_pdf_page(page)).strip()
                is_ocr = True
            sections.append(Section(label=f"p. {i + 1}", text=text, page_no=i + 1, ocr=is_ocr))
        return sections


def _page_has_ink(page: pymupdf.Page) -> bool:
    return bool(page.get_images()) or bool(page.get_drawings())


def render_pdf_page(page: pymupdf.Page, dpi: int = OCR_DPI) -> bytes:
    return page.get_pixmap(dpi=dpi).tobytes("png")


def render_pdf_page_with_highlight(path: Path, page_no: int, quote: str, dpi: int = 110) -> bytes:
    """PNG of one PDF page with the quote highlighted (if it can be found)."""
    with pymupdf.open(path) as doc:
        page = doc[page_no - 1]
        for rect in _find_quote_rects(page, quote):
            annot = page.add_highlight_annot(rect)
            annot.update()
        return page.get_pixmap(dpi=dpi).tobytes("png")


def _find_quote_rects(page: pymupdf.Page, quote: str) -> list:
    quote = " ".join(quote.split())
    if not quote:
        return []
    hits = page.search_for(quote)
    if hits:
        return hits
    # Long quotes often wrap across lines in odd ways; try the start and end.
    words = quote.split()
    if len(words) >= 8:
        return page.search_for(" ".join(words[:6])) + page.search_for(" ".join(words[-6:]))
    return []


# ---------- Word ----------

def _iter_docx_blocks(doc):
    """Paragraphs and tables in document order."""
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, doc)
        elif tag == "tbl":
            yield Table(child, doc)


def _extract_docx(path: Path) -> list[Section]:
    try:
        doc = DocxDocument(str(path))
    except Exception as e:
        raise ExtractionError(f"This Word file could not be opened ({e}).") from e

    sections: list[Section] = []
    heading = "Start of document"
    paras: list[str] = []

    def flush():
        if paras:
            sections.append(
                Section(label=f'"{heading}"', text="\n".join(paras), paragraphs=list(paras))
            )

    for block in _iter_docx_blocks(doc):
        if isinstance(block, Paragraph):
            text = " ".join(block.text.split())
            style = (block.style.name if block.style is not None else "") or ""
            if text and (style.startswith("Heading") or style == "Title"):
                flush()
                heading, paras = text, [text]
            elif text:
                paras.append(text)
        else:  # table: one paragraph per row
            for row in block.rows:
                cells = []
                for cell in row.cells:
                    c = " ".join(cell.text.split())
                    if c and (not cells or cells[-1] != c):  # merged cells repeat
                        cells.append(c)
                if cells:
                    paras.append(" | ".join(cells))
    flush()
    return sections


def docx_paragraph_number(section_paragraphs: list[str], quote_start_char: int) -> int:
    """1-based paragraph number containing the character offset in the joined text."""
    pos = 0
    for i, p in enumerate(section_paragraphs):
        pos += len(p) + 1  # +1 for the joining newline
        if quote_start_char < pos:
            return i + 1
    return len(section_paragraphs)


# ---------- Plain text / Markdown ----------

def _decode(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best is not None:
            return str(best)
    except ImportError:
        pass
    return raw.decode("latin-1")


def _extract_text(path: Path) -> list[Section]:
    lines = _decode(path.read_bytes()).splitlines()
    sections = []
    for start in range(0, len(lines), TEXT_BLOCK_LINES):
        block = lines[start : start + TEXT_BLOCK_LINES]
        end = start + len(block)
        sections.append(
            Section(
                label=f"lines {start + 1}–{end}",
                text="\n".join(block),
                first_line=start + 1,
            )
        )
    return sections


# ---------- Images ----------

def _extract_image(path: Path, ocr: OcrFn | None, progress: ProgressFn) -> list[Section]:
    if ocr is None:
        raise ExtractionError("Images need an OpenAI API key to be read.")
    progress("Reading the image with AI vision", 0.1)
    try:
        # Normalize every format to PNG, which the OCR model always accepts.
        pix = pymupdf.Pixmap(str(path))
        if pix.colorspace is not None and pix.colorspace.n > 3:  # CMYK
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        png = pix.tobytes("png")
    except Exception as e:
        raise ExtractionError(f"This image could not be opened ({e}).") from e
    return [Section(label="image", text=ocr(png), ocr=True)]
