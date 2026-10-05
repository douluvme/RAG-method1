# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Streamlit app that answers questions about uploaded documents (PDF, DOCX, TXT/MD, images) using **only** those documents, with a verified file/location/quote for every answer, or "No information" plus a short note. It uses OpenAI (Responses API). `PLAN.md` holds the design rationale and build status. Its first section records the owner's requirements: a single user, mixed-language documents, answers in the language of the question, and an Economy/Best quality cost switch.

## Commands

Requires Python ≥ 3.10. The system `python3` on this machine is 3.9, so create the venv with uv:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt   # runtime + pytest
.venv/bin/streamlit run app.py                                     # http://localhost:8501
.venv/bin/python -m pytest                                         # all tests
.venv/bin/python -m pytest tests/test_verify.py::test_name         # single test
```

There is no linter or build step. Settings (API key, model names, prices, `CONTEXT_LIMIT_TOKENS`, `APP_PASSWORD`, `DATA_DIR`) come from `.env` (see `.env.example`) and are read once at import time in `docqa/config.py`, so restart the server after changing them. Runtime data (SQLite DB and the original uploaded files) lives in `data/`, which is gitignored.

## Architecture

**Long context, no retrieval.** There are no embeddings and no search step. Every question sends *all* stored documents to the model. That choice drives most of the design:

- **Sections are the citation unit.** `extract.py` turns each file into `Section`s: one per PDF page, one per DOCX heading block (tables become one paragraph per row), 40-line blocks for text files, and one per image. PDF pages with almost no text but some images or drawings are treated as scanned and OCR'd once at upload through the vision model (`QAService.ocr`, passed into `ingest` as a callback). `db.py` stores sections in SQLite, along with per-section `paragraphs` (DOCX) or `first_line` (text) so that citations can be narrowed to ¶ or line numbers later.
- **Prompt layout is fixed so OpenAI prompt caching works.** `qa.build_documents_block` emits every document in id order as `<document id="D{n}"><section id="D{n}-S{k}" location="…">`, as the first user message, followed by the last `HISTORY_TURNS` Q/A pairs and the question. `prompt_cache_key` is a hash of the documents block. Changing the block's format or order invalidates the cache and breaks the section-id contract.
- **Structured output plus distrust.** The model returns `ModelAnswer {found, answer, citations[{section_id, quote}], note}` through `client.responses.parse`. `qa.postprocess` resolves each `section_id`, then checks the quote with `verify.find_quote`. This is a fuzzy match (≥ 90%) after normalizing case, whitespace, curly quotes, dashes and Unicode width; every number must match exactly, and an ellipsis splits the quote into parts that must each match. The quote **shown** to the user is the matched passage from the document, not the model's text. Unverified citations are dropped, `[n]` markers are renumbered, and if no citation survives the answer becomes "No information".
- **Size budget.** `ingest.py` rejects an upload when the total stored tokens (tiktoken `o200k_base`, plus about 20 tokens of markup per section) would exceed `CONTEXT_LIMIT_TOKENS`. The sidebar meter and the cost estimate use the same numbers.
- **UI (`app.py`)**: a single Streamlit script. Answers are saved as `messages.payload` (`Result.to_payload()`) and re-rendered from it, including sources, PDF page previews with highlights (`extract.render_pdf_page_with_highlight`) and surrounding DOCX/text context. Errors meant for the user are raised as `QAError` / `ExtractionError` with plain-language messages; OpenAI exceptions are mapped in `qa._friendly_error`.

## Tests

Tests never call OpenAI. `conftest.py` redirects `DATA_DIR`/`DB_PATH` to `tmp_path` (autouse) and builds PDF/DOCX/MD fixtures in code. Q&A tests construct `ModelAnswer` objects directly and pass them to `postprocess`, or use a fake client built from `SimpleNamespace`. Ingest without OCR by passing `ocr=None`.
