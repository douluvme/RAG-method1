"""Storage for documents, their extracted text, and chats (one SQLite file)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .extract import Section

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    n_sections INTEGER NOT NULL,
    n_tokens INTEGER NOT NULL,
    has_ocr INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    label TEXT NOT NULL,
    text TEXT NOT NULL,
    page_no INTEGER,
    paragraphs TEXT,
    first_line INTEGER,
    ocr INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS sections_doc ON sections(doc_id, idx);
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DocRow:
    id: int
    name: str
    stored_path: str
    n_sections: int
    n_tokens: int
    has_ocr: bool
    created_at: str


@dataclass
class StoredSection:
    doc_id: int
    idx: int
    label: str
    text: str
    page_no: int | None
    paragraphs: list[str]
    first_line: int | None
    ocr: bool


@dataclass
class Message:
    id: int
    role: str  # "user" or "assistant"
    content: str
    payload: dict


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ----- documents -----

    def add_document(self, name: str, stored_path: Path, sections: list[Section], n_tokens: int) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO documents (name, stored_path, n_sections, n_tokens, has_ocr, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (name, str(stored_path), len(sections), n_tokens,
                 int(any(s.ocr for s in sections)), _now()),
            )
            doc_id = cur.lastrowid
            c.executemany(
                "INSERT INTO sections (doc_id, idx, label, text, page_no, paragraphs, first_line, ocr)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (doc_id, i, s.label, s.text, s.page_no,
                     json.dumps(s.paragraphs, ensure_ascii=False) if s.paragraphs else None,
                     s.first_line, int(s.ocr))
                    for i, s in enumerate(sections)
                ],
            )
            return doc_id

    def list_documents(self) -> list[DocRow]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM documents ORDER BY id").fetchall()
        return [DocRow(**{**dict(r), "has_ocr": bool(r["has_ocr"])}) for r in rows]

    def get_document(self, doc_id: int) -> DocRow | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return DocRow(**{**dict(r), "has_ocr": bool(r["has_ocr"])}) if r else None

    def delete_document(self, doc_id: int) -> None:
        doc = self.get_document(doc_id)
        with self._conn() as c:
            c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        if doc:
            Path(doc.stored_path).unlink(missing_ok=True)

    def sections(self, doc_id: int) -> list[StoredSection]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM sections WHERE doc_id = ? ORDER BY idx", (doc_id,)
            ).fetchall()
        return [
            StoredSection(
                doc_id=r["doc_id"], idx=r["idx"], label=r["label"], text=r["text"],
                page_no=r["page_no"],
                paragraphs=json.loads(r["paragraphs"]) if r["paragraphs"] else [],
                first_line=r["first_line"], ocr=bool(r["ocr"]),
            )
            for r in rows
        ]

    def total_tokens(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COALESCE(SUM(n_tokens), 0) FROM documents").fetchone()[0]

    # ----- chats -----

    def create_chat(self, title: str = "New chat") -> int:
        with self._conn() as c:
            return c.execute(
                "INSERT INTO chats (title, created_at) VALUES (?, ?)", (title, _now())
            ).lastrowid

    def list_chats(self) -> list[tuple[int, str]]:
        with self._conn() as c:
            rows = c.execute("SELECT id, title FROM chats ORDER BY id DESC").fetchall()
        return [(r["id"], r["title"]) for r in rows]

    def rename_chat(self, chat_id: int, title: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))

    def delete_chat(self, chat_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM chats WHERE id = ?", (chat_id,))

    def add_message(self, chat_id: int, role: str, content: str, payload: dict | None = None) -> int:
        with self._conn() as c:
            return c.execute(
                "INSERT INTO messages (chat_id, role, content, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, role, content, json.dumps(payload or {}, ensure_ascii=False), _now()),
            ).lastrowid

    def messages(self, chat_id: int) -> list[Message]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,)
            ).fetchall()
        return [
            Message(id=r["id"], role=r["role"], content=r["content"],
                    payload=json.loads(r["payload"]) if r["payload"] else {})
            for r in rows
        ]
