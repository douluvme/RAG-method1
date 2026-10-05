from types import SimpleNamespace

import pytest

from docqa import config
from docqa.extract import ExtractionError
from docqa.ingest import ingest
from docqa.qa import (
    NO_INFO,
    Citation,
    ModelAnswer,
    QAError,
    QAService,
    Result,
    build_documents_block,
    postprocess,
)


def load(store, *paths):
    return [ingest(store, p.name, p.read_bytes(), ocr=None).doc_id for p in paths]


def ans(found, answer, cites, note="The documents cover the lease terms."):
    return ModelAnswer(found=found, answer=answer,
                       citations=[Citation(section_id=s, quote=q) for s, q in cites], note=note)


def test_documents_block_labels(store, sample_pdf, sample_docx):
    d1, d2 = load(store, sample_pdf, sample_docx)
    block = build_documents_block(store)
    assert f'<document id="D{d1}" name="contract.pdf">' in block
    assert f'<section id="D{d1}-S2" location="p. 2">' in block
    assert f'<section id="D{d2}-S2" location="&quot;Budget&quot;">' in block or \
        f'<section id="D{d2}-S2" location=""Budget"">' in block


def test_verified_answer_with_sources(store, sample_pdf, sample_docx, sample_txt):
    p, w, t = load(store, sample_pdf, sample_docx, sample_txt)
    parsed = ans(True, "The lease lasts three years [1]. Marketing gets 15% [2]. Meetings are Tuesdays [3].", [
        (f"D{p}-S2", "The term of this agreement is three years"),
        (f"D{w}-S2", "Marketing receives 15 percent of the budget."),
        (f"D{t}-S2", "회의는 매주 화요일 오전 10시에 열립니다."),
    ])
    r = postprocess(parsed, store, "m")
    assert r.found and r.dropped_citations == 0
    assert [(s.doc_name, s.location) for s in r.sources] == [
        ("contract.pdf", "p. 2"),
        ("plan.docx", '"Budget", ¶3'),
        ("notes.md", "line 42"),
    ]
    assert r.sources[0].page_no == 2


def test_unverifiable_citation_dropped_and_renumbered(store, sample_pdf):
    (p,) = load(store, sample_pdf)
    parsed = ans(True, "Rent is 1,500 dollars [1]. Notice is sixty days [2].", [
        (f"D{p}-S1", "The tenant shall pay rent of 1,500 dollars per month."),  # wrong number
        (f"D{p}-S3", "Either party may terminate the agreement with sixty days written notice."),
    ])
    r = postprocess(parsed, store, "m")
    assert r.found and r.dropped_citations == 1
    assert r.text == "Rent is 1,500 dollars. Notice is sixty days [1]."
    assert r.sources[0].location == "p. 3"


def test_no_information_when_nothing_verifies(store, sample_pdf):
    (p,) = load(store, sample_pdf)
    parsed = ans(True, "Pets are allowed [1].", [(f"D{p}-S1", "Pets are allowed.")])
    r = postprocess(parsed, store, "m")
    assert not r.found and r.sources == []
    assert r.text.startswith(f"{NO_INFO}.")


def test_no_information_with_note(store, sample_pdf):
    load(store, sample_pdf)
    r = postprocess(ans(False, "", [], note="문서는 임대 조건만 다룹니다."), store, "m")
    assert r.text == f"{NO_INFO}. 문서는 임대 조건만 다룹니다."


def test_bad_section_id_dropped(store, sample_pdf):
    (p,) = load(store, sample_pdf)
    parsed = ans(True, "x [1]", [(f"D{p}-S99", "Lease Agreement."), ("nonsense", "Lease Agreement.")])
    assert not postprocess(parsed, store, "m").found


def test_result_roundtrip(store, sample_pdf):
    (p,) = load(store, sample_pdf)
    r = postprocess(ans(True, "Three years [1].", [(f"D{p}-S2", "three years")]), store, "m")
    assert Result.from_payload(r.to_payload()) == r


class FakeResponses:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def parse(self, **kw):
        self.calls.append(kw)
        usage = SimpleNamespace(input_tokens=10_000, output_tokens=200,
                                input_tokens_details=SimpleNamespace(cached_tokens=8_000))
        return SimpleNamespace(output_parsed=self.parsed, usage=usage, incomplete_details=None)


def test_service_ask_sends_all_docs_and_history(store, sample_pdf, sample_docx):
    p, w = load(store, sample_pdf, sample_docx)
    fake = FakeResponses(ans(True, "Three years [1].", [(f"D{p}-S2", "three years")]))
    svc = QAService(store, client=SimpleNamespace(responses=fake))
    choice = config.MODELS["economy"]
    r = svc.ask("And the notice period?", [("How long is the lease?", "Three years [1].")], choice)

    call = fake.calls[0]
    assert call["model"] == choice.model
    docs_msg = call["input"][0]["content"]
    assert "contract.pdf" in docs_msg and "plan.docx" in docs_msg
    assert [m["role"] for m in call["input"]] == ["user", "user", "assistant", "user"]
    assert call["input"][-1]["content"] == "And the notice period?"
    assert r.cached_tokens == 8_000
    assert r.cost == pytest.approx(choice.cost(10_000, 8_000, 200))


def test_service_without_key(store, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(QAError, match="API key"):
        QAService(store).ask("q", [], config.MODELS["economy"])


def test_ingest_limits(store, sample_pdf, monkeypatch):
    load(store, sample_pdf)
    with pytest.raises(ExtractionError, match="already"):
        ingest(store, sample_pdf.name, sample_pdf.read_bytes(), ocr=None)
    monkeypatch.setattr(config, "CONTEXT_LIMIT_TOKENS", store.total_tokens() + 5)
    with pytest.raises(ExtractionError, match="too large"):
        ingest(store, "copy.pdf", sample_pdf.read_bytes(), ocr=None)
    assert len(list(config.FILES_DIR.iterdir())) == 1  # rejected file not kept


def test_persistence_and_delete(tmp_path, sample_pdf):
    from docqa.db import Store

    s1 = Store(tmp_path / "test.sqlite3")
    (p,) = load(s1, sample_pdf)
    cid = s1.create_chat("Lease")
    s1.add_message(cid, "user", "How long?")
    s2 = Store(tmp_path / "test.sqlite3")  # simulates restarting the app
    assert [d.name for d in s2.list_documents()] == ["contract.pdf"]
    assert s2.messages(cid)[0].content == "How long?"
    s2.delete_document(p)
    assert s2.list_documents() == [] and s2.total_tokens() == 0
