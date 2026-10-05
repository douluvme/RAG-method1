"""Document Q&A: upload documents, ask questions, get answers with sources.

Start with:  streamlit run app.py
"""

from __future__ import annotations

import hmac
from pathlib import Path

import streamlit as st

from docqa import config
from docqa.db import Store
from docqa.extract import ExtractionError, render_pdf_page_with_highlight
from docqa.ingest import ingest
from docqa.qa import QAError, QAService, Result, Source, estimate_cost
from docqa.verify import best_part

st.set_page_config(page_title="Document Q&A", page_icon="📄", layout="wide")


# ---------- setup ----------

@st.cache_resource
def get_store() -> Store:
    return Store(config.DB_PATH)


@st.cache_resource
def get_service() -> QAService:
    return QAService(get_store())


def password_gate() -> None:
    expected = config.app_password()
    if not expected or st.session_state.get("authed"):
        return
    st.title("📄 Document Q&A")
    pw = st.text_input("Password", type="password")
    if pw:
        if hmac.compare_digest(pw, expected):
            st.session_state.authed = True
            st.rerun()
        st.error("Wrong password.")
    st.stop()


password_gate()
store = get_store()
service = get_service()
ss = st.session_state
ss.setdefault("uploader_key", 0)
ss.setdefault("upload_log", [])  # list of (ok: bool, message)
ss.setdefault("toasts", [])
ss.setdefault("chat_id", None)
ss.setdefault("mode", "economy")

for t in ss.toasts:
    st.toast(t)
ss.toasts = []


# ---------- upload ----------

def handle_uploads(files) -> None:
    log = []
    for f in files:
        with st.status(f"Reading **{f.name}**…", expanded=True) as status:
            bar = st.progress(0.0)
            line = st.empty()

            def progress(msg: str, frac: float) -> None:
                line.caption(msg)
                bar.progress(min(max(frac, 0.0), 1.0))

            try:
                r = ingest(store, f.name, f.getvalue(), service.ocr if service.client else None, progress)
            except (ExtractionError, QAError) as e:
                status.update(label=f"❌ {f.name}: could not be added", state="error")
                log.append((False, f"**{f.name}** could not be added. {e}"))
                continue
            except Exception as e:  # never leave the user with a stack trace
                status.update(label=f"❌ {f.name}: could not be added", state="error")
                log.append((False, f"**{f.name}** could not be added (unexpected error: {e})."))
                continue
            unit = r.unit + ("s" if r.n_sections != 1 and r.unit != "image" else "")
            size = "" if r.unit == "image" else f" ({r.n_sections} {unit})"
            scanned = f", {r.n_scanned} read from scans" if r.n_scanned and r.unit == "page" else ""
            msg = f"✅ **{f.name}** is ready{size}{scanned}."
            status.update(label=msg.replace("**", ""), state="complete")
            log.append((True, msg))
            ss.toasts.append(f"{f.name} is ready")
    ss.upload_log = log
    ss.uploader_key += 1  # clears the upload box
    st.rerun()


# ---------- sidebar ----------

with st.sidebar:
    st.header("📄 Documents")
    files = st.file_uploader(
        "Upload documents",
        type=["pdf", "docx", "txt", "md", "markdown", "png", "jpg", "jpeg", "webp", "gif"],
        accept_multiple_files=True,
        key=f"uploader_{ss.uploader_key}",
        help="PDF, Word, text/Markdown, or images. Scanned pages are read with AI vision.",
    )
    if files:
        handle_uploads(files)

    for ok, msg in ss.upload_log:
        (st.success if ok else st.error)(msg)

    docs = store.list_documents()
    used = store.total_tokens()
    limit = config.CONTEXT_LIMIT_TOKENS
    st.progress(min(used / limit, 1.0))
    pct = used / limit * 100
    meter = f"AI memory used: {used:,} of {limit:,} tokens ({pct:.0f}%)"
    if pct >= 85:
        st.warning(meter + ". You're close to the limit.")
    else:
        st.caption(meter)

    if not docs:
        st.info("No documents yet. Upload some to get started.")
    for d in docs:
        c1, c2 = st.columns([5, 1])
        badge = " · 📷 scanned" if d.has_ocr else ""
        c1.markdown(f"**{d.name}**  \n<small>{d.n_sections} part(s) · {d.n_tokens:,} tokens{badge}</small>",
                    unsafe_allow_html=True)
        if c2.button("🗑", key=f"del_doc_{d.id}", help=f"Delete {d.name}"):
            store.delete_document(d.id)
            ss.upload_log = []
            ss.toasts.append(f"Deleted {d.name}")
            st.rerun()

    st.divider()
    st.header("⚙️ Answer quality")
    ss.mode = st.radio(
        "Model",
        options=list(config.MODELS),
        format_func=lambda k: f"{config.MODELS[k].label} ({config.MODELS[k].model})",
        index=list(config.MODELS).index(ss.mode),
        label_visibility="collapsed",
    )
    if docs:
        est = estimate_cost(store, config.MODELS[ss.mode])
        st.caption(f"≈ ${est:.3f} per question (less for quick follow-ups, thanks to caching)")

    st.divider()
    st.header("💬 Chats")
    if st.button("➕ New chat", use_container_width=True):
        ss.chat_id = None
        st.rerun()
    for cid, title in store.list_chats():
        c1, c2 = st.columns([5, 1])
        label = ("▶ " if cid == ss.chat_id else "") + title
        if c1.button(label, key=f"chat_{cid}", use_container_width=True):
            ss.chat_id = cid
            st.rerun()
        if c2.button("🗑", key=f"del_chat_{cid}", help="Delete this chat"):
            store.delete_chat(cid)
            if ss.chat_id == cid:
                ss.chat_id = None
            st.rerun()

    st.divider()
    with st.expander("🔧 Check settings"):
        if not config.openai_api_key():
            st.error("No OpenAI API key. Copy .env.example to .env, paste your key, and restart.")
        if st.button("Test API key and models"):
            ok, msg = service.check_settings()
            (st.success if ok else st.error)(msg)


# ---------- sources ----------

def render_source(src: Source, msg_id: int) -> None:
    title = f"[{src.number}] {src.doc_name} — {src.location}" + (" · 📷 scanned" if src.ocr else "")
    with st.expander(title):
        st.markdown("> " + src.quote.replace("\n", "\n> "))
        doc = store.get_document(src.doc_id)
        if doc is None:
            st.caption("This document has since been deleted.")
            return
        path = Path(doc.stored_path)
        if src.page_no is not None:
            if st.toggle("Show page", key=f"page_{msg_id}_{src.number}"):
                try:
                    png = render_pdf_page_with_highlight(path, src.page_no, src.quote)
                    st.image(png, caption=f"{src.doc_name}, page {src.page_no}")
                except Exception as e:
                    st.caption(f"Page preview unavailable ({e}).")
        elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            if st.toggle("Show image", key=f"img_{msg_id}_{src.number}"):
                st.image(str(path))
        else:
            secs = store.sections(src.doc_id)
            if 0 <= src.section_idx < len(secs):
                sec = secs[src.section_idx]
                parts = sec.paragraphs or sec.text.split("\n")
                i = best_part(parts, src.quote)
                context = "\n".join(parts[max(i - 1, 0): i + 2])
                st.caption("Surrounding text:")
                st.text(context)


def render_answer(result: Result, msg_id: int) -> None:
    st.markdown(result.text)
    if result.sources:
        st.markdown("**Sources**")
        for src in result.sources:
            render_source(src, msg_id)
    meta = [result.model]
    if result.input_tokens:
        meta.append(f"{result.input_tokens:,} tokens read ({result.cached_tokens:,} cached)")
    if result.cost:
        meta.append(f"${result.cost:.4f}")
    if result.dropped_citations:
        meta.append(f"{result.dropped_citations} unverifiable quote(s) removed")
    st.caption(" · ".join(meta))


# ---------- chat ----------

st.title("📄 Document Q&A")
st.caption("Ask questions about your uploaded documents. Answers come only from those documents, with sources.")

messages = store.messages(ss.chat_id) if ss.chat_id else []
if not messages:
    if docs:
        st.info(f"{len(docs)} document(s) loaded. Ask a question below.")
    else:
        st.info("👈 Upload documents in the sidebar to begin.")

for m in messages:
    with st.chat_message(m.role):
        if m.role == "assistant" and m.payload:
            render_answer(Result.from_payload(m.payload), m.id)
        else:
            st.markdown(m.content)

question = st.chat_input(
    "Ask a question about your documents" if docs else "Upload documents first",
    disabled=not docs,
)
if question:
    if ss.chat_id is None:
        ss.chat_id = store.create_chat(question[:60] + ("…" if len(question) > 60 else ""))
    history = []
    pending_q = None
    for m in messages:
        if m.role == "user":
            pending_q = m.content
        elif pending_q is not None:
            if m.payload:  # skip error messages
                history.append((pending_q, m.content))
            pending_q = None

    store.add_message(ss.chat_id, "user", question)
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Reading your documents…"):
                result = service.ask(question, history, config.MODELS[ss.mode])
        except QAError as e:
            st.error(str(e))
            store.add_message(ss.chat_id, "assistant", f"⚠️ {e}")
            st.stop()
    store.add_message(ss.chat_id, "assistant", result.text, result.to_payload())
    st.rerun()
