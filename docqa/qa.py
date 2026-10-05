"""Answer questions by giving the AI all documents at once ("long context")."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import asdict, dataclass

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel, Field

from . import config
from .db import Store, StoredSection
from .extract import docx_paragraph_number
from .tokens import count_tokens
from .verify import best_part, find_quote

NO_INFO = "No information"

INSTRUCTIONS = """\
You answer questions using ONLY the documents provided by the user.

Rules:
1. Use only facts stated in the documents. Never use outside knowledge, and never guess.
2. Write the answer in the same language as the user's question, even if the documents are in another language.
3. Support every factual statement with a citation. For each citation give the section_id exactly as written \
in the document markup (for example "D3-S12") and a short quote (one or two sentences) copied character for \
character from that section, in the document's original language. Do not translate, paraphrase, fix typos \
or use "..." inside quotes.
4. Put citation markers like [1], [2] in the answer text, numbered in the order of the citations list.
5. If the documents do not contain information that answers the question, set found to false, leave answer \
empty and citations empty.
6. Always fill note with one short sentence, in the language of the question, saying what the documents \
do cover that is closest to the question (or that they say nothing related). Do not mention section ids in the note.
7. Previous questions and answers in the conversation are context for follow-up questions only; \
the documents remain the only source of facts.
"""

OCR_PROMPT = """\
Transcribe all text visible in this image exactly as written, in its original language(s). \
Keep the reading order. Write tables as rows with cells separated by " | ". \
Output only the transcribed text, with no comments. If there is no text, output nothing."""


class Citation(BaseModel):
    section_id: str = Field(description='Section id from the documents, e.g. "D2-S5"')
    quote: str = Field(description="Exact text copied from that section")


class ModelAnswer(BaseModel):
    found: bool
    answer: str
    citations: list[Citation]
    note: str


class QAError(Exception):
    """A problem the user should be told about in plain words."""


@dataclass
class Source:
    number: int
    doc_id: int
    doc_name: str
    location: str  # e.g. "p. 12", '"Budget", ¶4', "line 87", "image"
    quote: str
    page_no: int | None
    ocr: bool
    section_idx: int = 0


@dataclass
class Result:
    found: bool
    text: str  # what is shown in the chat
    sources: list[Source]
    note: str
    model: str
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    dropped_citations: int = 0

    def to_payload(self) -> dict:
        d = asdict(self)
        d["sources"] = [asdict(s) for s in self.sources]
        return d

    @staticmethod
    def from_payload(d: dict) -> "Result":
        d = dict(d)
        d["sources"] = [Source(**s) for s in d.get("sources", [])]
        return Result(**d)


# ---------- prompt building ----------

def section_id(doc_id: int, idx: int) -> str:
    return f"D{doc_id}-S{idx + 1}"


def _escape(text: str) -> str:
    return text.replace("</section>", "</ section>").replace("</document>", "</ document>")


def build_documents_block(store: Store) -> str:
    """All documents, in a fixed order so OpenAI can cache this prefix."""
    parts = [
        "Here are the documents. Each <section> has an id and a location (page, heading or lines).\n"
    ]
    for doc in store.list_documents():
        parts.append(f'<document id="D{doc.id}" name="{doc.name}">')
        for s in store.sections(doc.id):
            scanned = ' scanned="true"' if s.ocr else ""
            parts.append(
                f'<section id="{section_id(doc.id, s.idx)}" location="{s.label}"{scanned}>\n'
                f"{_escape(s.text)}\n</section>"
            )
        parts.append("</document>")
    return "\n".join(parts)


def build_input(documents_block: str, history: list[tuple[str, str]], question: str) -> list[dict]:
    items: list[dict] = [{"role": "user", "content": documents_block}]
    for q, a in history[-config.HISTORY_TURNS :]:
        items.append({"role": "user", "content": q})
        items.append({"role": "assistant", "content": a})
    items.append({"role": "user", "content": question})
    return items


def estimate_cost(store: Store, choice: config.ModelChoice, question: str = "") -> float:
    tokens = store.total_tokens() + count_tokens(INSTRUCTIONS + question) + 500
    return choice.cost(tokens, 0, config.EXPECTED_OUTPUT_TOKENS)


# ---------- post-processing ----------

def _locate(sec: StoredSection, quote: str) -> str:
    if sec.paragraphs:  # Word
        para = best_part(sec.paragraphs, quote) + 1
        return f"{sec.label}, ¶{para}"
    if sec.first_line is not None:  # text file
        lines = sec.text.split("\n")
        return f"line {sec.first_line + best_part(lines, quote)}"
    return sec.label


def _renumber_markers(answer: str, mapping: dict[int, int]) -> str:
    def repl(m: re.Match) -> str:
        nums = [int(n) for n in re.findall(r"\d+", m.group(0))]
        kept = sorted({mapping[n] for n in nums if n in mapping})
        return "".join(f"[{n}]" for n in kept)

    out = re.sub(r"\[\d+(?:\s*[,–-]\s*\d+)*\]", repl, answer)
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", out).strip()


def postprocess(parsed: ModelAnswer, store: Store, model: str) -> Result:
    docs = {d.id: d for d in store.list_documents()}
    sections_cache: dict[int, list[StoredSection]] = {}
    sources: list[Source] = []
    mapping: dict[int, int] = {}
    seen: dict[tuple[int, int, str], int] = {}  # (doc, section, quote) -> number
    dropped = 0

    for i, c in enumerate(parsed.citations, start=1):
        m = re.fullmatch(r"\s*D(\d+)-S(\d+)\s*", c.section_id)
        sec = None
        if m:
            doc_id, idx = int(m.group(1)), int(m.group(2)) - 1
            if doc_id in docs:
                secs = sections_cache.setdefault(doc_id, store.sections(doc_id))
                sec = secs[idx] if 0 <= idx < len(secs) else None
        excerpt = find_quote(sec.text, c.quote) if sec is not None else None
        if excerpt is None:
            dropped += 1
            continue
        key = (sec.doc_id, sec.idx, excerpt)
        if key in seen:  # same citation twice: reuse its number
            mapping[i] = seen[key]
            continue
        number = seen[key] = len(sources) + 1
        mapping[i] = number
        sources.append(
            Source(
                number=number,
                doc_id=sec.doc_id,
                doc_name=docs[sec.doc_id].name,
                location=_locate(sec, excerpt),
                quote=excerpt,
                page_no=sec.page_no,
                ocr=sec.ocr,
                section_idx=sec.idx,
            )
        )

    note = parsed.note.strip()
    if parsed.found and parsed.answer.strip() and sources:
        text = _renumber_markers(parsed.answer, mapping)
        return Result(True, text, sources, note, model, dropped_citations=dropped)

    if parsed.found and dropped:
        # The AI produced an answer but none of its quotes could be confirmed.
        note = note or "An answer was drafted, but it could not be confirmed with exact quotes from your documents."
    text = f"{NO_INFO}." + (f" {note}" if note else "")
    return Result(False, text, [], note, model, dropped_citations=dropped)


# ---------- OpenAI calls ----------

def _friendly_error(e: Exception, model: str) -> QAError:
    if isinstance(e, AuthenticationError):
        return QAError("OpenAI rejected the API key. Check OPENAI_API_KEY in your .env file.")
    if isinstance(e, PermissionDeniedError):
        return QAError(f"Your OpenAI account doesn't have access to the model '{model}'.")
    if isinstance(e, NotFoundError):
        return QAError(
            f"The model '{model}' was not found. Change the model name in your .env file "
            "(use 'Check settings' in the sidebar to see the models your key can use)."
        )
    if isinstance(e, RateLimitError):
        if "quota" in str(e).lower():
            return QAError("Your OpenAI account is out of credit. Add billing at platform.openai.com.")
        return QAError("OpenAI is rate-limiting requests right now. Wait a minute and try again.")
    if isinstance(e, BadRequestError):
        msg = str(e)
        if "context" in msg.lower() and ("length" in msg.lower() or "window" in msg.lower()):
            return QAError("Your documents are too large for this model. Remove some documents and try again.")
        return QAError(f"OpenAI could not process the request: {msg}")
    if isinstance(e, APIConnectionError):
        return QAError("Could not connect to OpenAI. Check your internet connection.")
    if isinstance(e, APIStatusError):
        return QAError(f"OpenAI returned an error ({e.status_code}). Please try again.")
    return QAError(f"Unexpected error: {e}")


class QAService:
    def __init__(self, store: Store, client: OpenAI | None = None):
        self.store = store
        key = config.openai_api_key()
        self.client = client or (OpenAI(api_key=key) if key else None)

    def _require_client(self) -> OpenAI:
        if self.client is None:
            raise QAError("No OpenAI API key found. Add OPENAI_API_KEY to the .env file and restart the app.")
        return self.client

    def ask(self, question: str, history: list[tuple[str, str]], choice: config.ModelChoice) -> Result:
        client = self._require_client()
        docs_block = build_documents_block(self.store)
        cache_key = "docqa-" + hashlib.sha256(docs_block.encode()).hexdigest()[:16]
        try:
            resp = client.responses.parse(
                model=choice.model,
                instructions=INSTRUCTIONS,
                input=build_input(docs_block, history, question),
                text_format=ModelAnswer,
                prompt_cache_key=cache_key,
                max_output_tokens=16_000,
            )
        except Exception as e:
            raise _friendly_error(e, choice.model) from e

        parsed = resp.output_parsed
        if parsed is None:
            if resp.incomplete_details is not None:
                raise QAError("The AI ran out of room before finishing its answer. Try a narrower question.")
            raise QAError("The AI's reply could not be read. Please try again.")

        result = postprocess(parsed, self.store, choice.model)
        u = resp.usage
        if u is not None:
            cached = (u.input_tokens_details.cached_tokens or 0) if u.input_tokens_details else 0
            result.input_tokens, result.cached_tokens, result.output_tokens = (
                u.input_tokens, cached, u.output_tokens)
            result.cost = choice.cost(u.input_tokens, cached, u.output_tokens)
        return result

    def ocr(self, png: bytes) -> str:
        client = self._require_client()
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        try:
            resp = client.responses.create(
                model=config.OCR_MODEL,
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": OCR_PROMPT},
                        {"type": "input_image", "image_url": url, "detail": "high"},
                    ],
                }],
                max_output_tokens=16_000,
            )
        except Exception as e:
            raise _friendly_error(e, config.OCR_MODEL) from e
        return resp.output_text or ""

    def check_settings(self) -> tuple[bool, str]:
        """Validate the API key and configured model names."""
        try:
            client = self._require_client()
            available = {m.id for m in client.models.list()}
        except QAError as e:
            return False, str(e)
        except Exception as e:
            return False, str(_friendly_error(e, ""))
        wanted = {c.model for c in config.MODELS.values()} | {config.OCR_MODEL}
        missing = sorted(wanted - available)
        if missing:
            gpt = sorted(m for m in available if m.startswith("gpt"))
            return False, (
                f"These models aren't available to your key: {', '.join(missing)}. "
                f"Models you can use include: {', '.join(gpt[-15:])}"
            )
        return True, f"API key works. Models in use: {', '.join(sorted(wanted))}."
