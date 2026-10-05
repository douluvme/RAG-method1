"""Save an uploaded file, read its text, and add it to the library."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import config
from .db import Store
from .extract import IMAGE_EXT, SUPPORTED_EXT, ExtractionError, OcrFn, ProgressFn, Section, extract
from .tokens import count_tokens

SECTION_OVERHEAD_TOKENS = 20  # the <section id=... location=...> markup


@dataclass
class IngestResult:
    doc_id: int
    name: str
    n_sections: int
    n_tokens: int
    n_scanned: int
    unit: str  # "page", "section", "block" or "image"


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name)[-120:] or "file"


def _unit(path: Path) -> str:
    ext = path.suffix.lower()
    return {".pdf": "page", ".docx": "section"}.get(ext, "image" if ext in IMAGE_EXT else "block")


def document_tokens(sections: list[Section]) -> int:
    return sum(count_tokens(s.text) + SECTION_OVERHEAD_TOKENS for s in sections)


def ingest(store: Store, name: str, data: bytes, ocr: OcrFn | None,
           progress: ProgressFn = lambda m, f: None) -> IngestResult:
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise ExtractionError(
            f"Unsupported file type '{ext or name}'. Supported: PDF, DOCX, TXT, MD, PNG, JPG, WEBP, GIF."
        )
    if any(d.name == name for d in store.list_documents()):
        raise ExtractionError("A document with this name is already in your library. Delete it first to replace it.")

    config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    stored = config.FILES_DIR / f"{uuid.uuid4().hex[:8]}_{_safe_filename(name)}"
    stored.write_bytes(data)
    try:
        sections = extract(stored, ocr, progress)
        n_tokens = document_tokens(sections)
        used = store.total_tokens()
        if used + n_tokens > config.CONTEXT_LIMIT_TOKENS:
            raise ExtractionError(
                f"This document is too large to add: it needs about {n_tokens:,} tokens, but only "
                f"{max(config.CONTEXT_LIMIT_TOKENS - used, 0):,} of {config.CONTEXT_LIMIT_TOKENS:,} are left. "
                "Delete some documents first."
            )
        progress("Saving", 0.99)
        doc_id = store.add_document(name, stored, sections, n_tokens)
    except BaseException:
        stored.unlink(missing_ok=True)
        raise
    return IngestResult(doc_id, name, len(sections), n_tokens,
                        sum(s.ocr for s in sections), _unit(stored))
