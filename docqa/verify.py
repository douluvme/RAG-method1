"""Check that a quote given by the AI really appears in the cited text.

When it does, we return the matching passage *from the document itself*, so the
quote shown to the user is always the document's real wording.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Minimum share of the quote's characters that must be found, in order and
# close together, in the cited text.
MATCH_THRESHOLD = 0.9

_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    "­": None,  # soft hyphen
})
_ELLIPSIS = re.compile(r"\[\.\.\.\]|\.\.\.|…")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _norm_char(ch: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", ch).translate(_TRANSLATE).casefold())


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Normalized text plus, for each normalized character, its index in `text`.

    Ignores differences that PDF extraction and the AI commonly introduce:
    width/compatibility forms, case, curly quotes, dashes, and all whitespace
    (CJK text often gets stray line breaks)."""
    out, idx = [], []
    for i, ch in enumerate(text):
        for n in _norm_char(ch):
            out.append(n)
            idx.append(i)
    return "".join(out), idx


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]


def quote_pieces(quote: str) -> list[str]:
    """A quote may skip text with an ellipsis; each piece must match on its own."""
    pieces = (normalize(p).strip("\"'.,;:") for p in _ELLIPSIS.split(quote))
    return [p for p in pieces if p]


def _match(hay: str, needle: str) -> tuple[float, int, int]:
    """(share of `needle` found in order in a compact region of `hay`, start, end)."""
    idx = hay.find(needle)
    if idx >= 0:
        return 1.0, idx, idx + len(needle)
    sm = SequenceMatcher(None, hay, needle, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size >= 3]
    if not blocks:
        return 0.0, -1, -1
    # Only count matches near the largest one, so scattered coincidences don't add up.
    best = max(blocks, key=lambda b: b.size)
    window = int(len(needle) * 1.5)
    kept = [b for b in blocks if abs(b.a - best.a) <= window]
    matched = sum(b.size for b in kept)
    return matched / len(needle), min(b.a for b in kept), max(b.a + b.size for b in kept)


def _clean_excerpt(s: str) -> str:
    s = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", s)  # re-join words hyphenated across lines
    return " ".join(s.split())


def find_quote(text: str, quote: str) -> str | None:
    """The passage of `text` matching `quote`, or None if it isn't really there.

    Every number in the quote must appear exactly in the matched passage, since
    a fuzzy match would otherwise accept e.g. "1,500" for "1,200"."""
    pieces = quote_pieces(quote)
    if not pieces:
        return None
    hay, idx = normalize_with_map(text)
    excerpts, matched, total = [], 0.0, 0
    for p in pieces:
        score, start, end = _match(hay, p)
        if score <= 0:
            return None
        span = hay[start:end]
        if any(n not in span for n in _NUMBER.findall(p)):
            return None
        matched += score * len(p)
        total += len(p)
        excerpts.append(_clean_excerpt(text[idx[start] : idx[end - 1] + 1]))
    if matched / total < MATCH_THRESHOLD:
        return None
    return " … ".join(excerpts)


def is_supported(text: str, quote: str) -> bool:
    return find_quote(text, quote) is not None


def best_part(parts: list[str], quote: str) -> int:
    """0-based index of the part (paragraph or line) where the quote starts."""
    pieces = quote_pieces(quote)
    if not pieces or not parts:
        return 0
    probe = pieces[0][:40]  # the start of the quote
    scores = [_match(normalize(p), probe)[0] if p.strip() else 0.0 for p in parts]
    return max(range(len(parts)), key=lambda i: scores[i])
