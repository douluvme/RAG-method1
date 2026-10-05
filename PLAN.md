# Plan: Document Q&A Web App ("Long-context, no retrieval")

## 1. What we're building

A personal web app that runs in your browser. You upload documents, and the app tells you when each one is ready. Then you ask questions in a chat window. The app answers **only from your documents** and shows the source of each answer: file name, page, and the exact quote. If the documents don't contain the answer, it replies **"No information"** with a short note on what the documents do cover.

### Your answers from the interview

| Topic | Decision |
|---|---|
| Users | Just you, so there are no accounts or logins at first |
| File types | PDF, Word (.docx), .txt / .md, and scanned PDFs and images |
| Volume | A few documents, under about 300 pages in total |
| AI provider | OpenAI (GPT) |
| Saving | Documents **and** chat history are kept between sessions |
| Sources | File + page + exact quote |
| Hosting | Runs on your computer first, built so it can move online later |
| Look and feel | Fast and simple, using a ready-made interface toolkit |
| Language | Mixed-language documents. The answer uses the language of the question. |
| No answer | "No information" plus a short note |
| Scope | Every question is checked against all uploaded documents |
| Cost | A switch in the app: "Economy" or "Best quality" |

---

## 2. How "long-context, no retrieval" works

Many document-chat tools use *retrieval*: they cut documents into small pieces and send the AI only the pieces that look relevant. That can miss the right passage.

We'll do something simpler. **For every question, the AI reads all of your documents in full.** Modern GPT models can hold hundreds of pages at once, so with fewer than 300 pages this fits easily.

```
 You upload files ──► App pulls out the text, page by page (OCR for scans)
                       │
                       ▼
               Saved on your computer (text + original files)
                       │
 You ask a question ───┤
                       ▼
   App sends to GPT:  [rules] + [ALL documents, labeled by file & page]
                      + [recent chat] + [your question]
                       │
                       ▼
   GPT returns: answer + "found / not found" + list of (file, page, quote)
                       │
                       ▼
   App CHECKS every quote really exists on that page ──► shows answer + sources
                                                    └──► or "No information"
```

**Pros:** It's simple, it doesn't miss passages scattered across documents, and it handles questions like "compare document A and B" well.
**Cons:** Each question costs more because every page is sent each time. Caching reduces this (see section 5). The approach also has a size limit, which is why the app warns you as you get close to it.

---

## 3. Recommended tech stack (plain language)

| Piece | What we'll use | What it is, in plain words | Why |
|---|---|---|---|
| Programming language | **Python** | The most common language for AI apps | Has ready-made tools for reading PDF and Word files and for calling OpenAI |
| Web interface | **Streamlit** | A toolkit that turns a Python script into a web page, with a built-in chat box, file-upload button, and progress messages | You get a clean app quickly without a separate website project |
| AI model | **OpenAI API** (GPT models with a large context window) | The service that reads your documents and writes answers. You pay per use. | It's your preference. It supports structured answers and automatic caching. |
| Reading PDFs | **PyMuPDF** | A library that pulls text out of PDFs page by page and can draw a page as an image | Gives accurate page numbers for citations and can show you the cited page |
| Reading Word files | **python-docx** | A library that reads .docx files | Word files don't store page numbers (see section 7) |
| Scanned pages and images | **OpenAI vision** (main option); **Tesseract** (free offline backup) | OCR: turning a picture of text into real text | GPT vision handles mixed languages well. Each scanned page is transcribed once, at upload, and then saved. |
| Storage | **SQLite** + a `files/` folder | A database that lives in one file on your computer. You don't install a server. | Keeps documents, extracted text, and chat history between sessions |
| Token counting | **tiktoken** | Measures how much text the AI must read | Powers the "how full is the AI's memory" meter and cost estimates |
| Secrets | A **`.env` file** | A private text file holding your OpenAI API key | Keeps your key out of the code |
| Going online later | **Streamlit Community Cloud** (free) or **Render / Railway** (a few dollars a month) + a password | Services that host the app at a private web address | A small change, not a rebuild |

**What you'll need:** an OpenAI account with an API key and billing set up, and Python installed on your computer. The setup steps will be written out for you.

---

## 4. Features and screens

**Left sidebar: Document library**
- An **Upload** button that accepts several files at once (PDF, DOCX, TXT, MD, PNG/JPG).
- Live progress for each file, e.g. "Reading contract.pdf… page 7/24… OCR on scanned page 8…", then **"✅ contract.pdf is ready (24 pages)"** with a pop-up notice. If a file fails, a clear ❌ message explains why (encrypted, empty, unsupported).
- A list of documents with page count and a 🗑 delete button for each.
- A **context meter**, e.g. "Using 142k of 400k tokens (≈ 210 pages)". It turns yellow near the limit and blocks uploads that wouldn't fit.
- A **Model switch**, Economy or Best quality, with an estimated cost per question.
- A **Chats** list: start a new chat or reopen an earlier one.

**Main area: Chat**
- A chat box at the bottom and the conversation above it. Follow-up questions work, e.g. "what about section 5?"
- Each answer has a **Sources** section underneath:
  - 📄 `contract.pdf — p. 12` › *"The term of this agreement is three (3) years…"*
  - Clicking a source expands it to show the quote highlighted on an image of that page (PDFs), or the surrounding paragraph (Word and text files).
- If nothing relevant is found: **"No information.** The documents cover the lease terms and payment schedule, but not insurance requirements."

---

## 5. Key design details (how we keep answers honest)

1. **Labeled documents.** Every page goes to the AI wrapped in a label, e.g. `[DOC 2: contract.pdf | PAGE 12] …text…`. This lets the AI say exactly where each fact came from.
2. **Strict instructions.** The AI is told to use only the provided documents and never outside knowledge. Every claim must come with a quote copied word for word. It must answer in the language of the question, and if the documents don't answer the question it must set "found = false" and write a one-sentence note about what they *do* cover.
3. **Structured replies.** Using OpenAI's "Structured Outputs" feature, the AI replies in a fixed format: `found`, `answer`, `citations[{doc, page, quote}]`, `note`. The app can then read the reply reliably instead of guessing.
4. **Quote verification (the safety net).** The app checks each quote against the stored text of that page. Small differences in spacing and punctuation are tolerated. Quotes that can't be matched are removed. **If an answer has no verified quotes left, the app shows "No information"** rather than an unsupported answer.
5. **Cost control.**
   - Documents are always sent in the same order at the start of the message. OpenAI then **automatically caches** them, and repeat questions on the same documents cost much less.
   - The Economy/Best switch lets you choose a cheaper or a stronger GPT model for each question.
   - Model names live in one settings file, so updating to newer models later is a one-line change.
6. **Chat memory.** The last few exchanges are included so follow-up questions make sense. The documents themselves are not repeated in the history.

---

## 6. Build steps (milestones)

Each step ends with something you can try.

| # | Milestone | You can… |
|---|---|---|
| 0 | **Setup**: project skeleton, `.env` for your API key, a one-command start (`streamlit run app.py`), README with setup steps | Open an empty app in your browser |
| 1 | **Upload & library**: text PDFs, DOCX, TXT/MD; page-by-page text extraction; saving to SQLite; progress and "ready" messages; delete | Upload files, see them listed, see "ready", and find them still there after a restart |
| 2 | **Ask questions**: long-context prompt, structured replies, answers in the question's language, "No information + note" | Ask questions and get answers, or "No information" |
| 3 | **Sources**: quote verification, the Sources section, expandable page preview with highlighted quote | Click a source and see the exact passage |
| 4 | **Scanned documents**: detect pages with no text, OCR with GPT vision (Tesseract optional), image uploads | Upload a scanned PDF or a photo and ask about it |
| 5 | **Comfort features**: saved chat sessions, Economy/Best switch, context meter, cost estimate, size limit checks | Manage chats and costs |
| 6 | **Quality check**: a test set of about 20 questions on sample documents, including ones that *should* return "No information" and some multi-language cases | Trust the results, backed by measured accuracy |
| 7 | **Ready for online**: password gate, hosting config, deployment guide | Optionally put it online with a private address |

---

## 7. Known limitations & risks

| Issue | What we'll do |
|---|---|
| **Word files have no real page numbers.** Page breaks depend on the printer and screen. | Cite by heading and paragraph number, e.g. `report.docx — "Budget" section, ¶4`. Optionally convert DOCX to PDF for true page numbers (needs LibreOffice installed). |
| **OCR mistakes** on blurry scans or handwriting | Show a "scanned, text may be imperfect" badge on those documents. GPT vision is usually strong on mixed languages. |
| **Size limit.** Too many pages won't fit in the AI's memory. | The context meter warns you and blocks uploads that don't fit. If you outgrow ~300 pages, the next step is choosing which documents a chat uses, or adding retrieval. |
| **Cost per question** grows with total pages | Caching, the Economy mode, and a cost estimate shown before you ask |
| **"Lost in the middle."** AI can overlook details in very long input. | The strict quote requirement and the test set (milestone 6) catch this |
| **Privacy.** Documents are sent to OpenAI to be read. | By default, OpenAI doesn't train on API data. Avoid uploading anything you aren't allowed to share with a third-party service. |

---

## 8. Definition of done (acceptance checks)

- [ ] Uploading a PDF, DOCX, TXT/MD, scanned PDF, and photo each ends with a clear "ready" (or error) message
- [ ] Documents and chats are still there after closing and reopening the app
- [ ] Answers come only from uploaded documents and use the question's language
- [ ] Every answer shows at least one source (file + page/section + quote), and every quote is verified
- [ ] Questions not covered by the documents get "No information" + a short note
- [ ] The Economy/Best switch works and the context meter is accurate
- [ ] The test set passes at an agreed accuracy (e.g. ≥ 90%, with 100% on the "No information" cases)

---

## 9. Follow-up answers (resolved)

| Question | Answer | Effect on the plan |
|---|---|---|
| OpenAI API account? | You already have a key | Milestone 0 only needs you to paste the key into `.env` |
| Questions per day? | About 10 (≈ 300 per month) | See the cost estimate below |
| Confidential documents? | No | Sending documents to OpenAI is fine. No extra privacy measures needed. |
| DOCX citations by section/paragraph? | Yes, that's fine | No LibreOffice needed. Word sources show as `file.docx — "Heading", ¶N` |
| Sample documents for testing? | Yes, you have some | You'll share them at milestone 6 (or earlier, to test along the way). Include at least one scanned file and one non-English file. |

### Estimated monthly cost (10 questions/day)

Per-question cost depends mostly on how many pages are loaded. Most of the cost is the AI re-reading the documents. OpenAI gives about a **90% discount on cached input**. The cache lasts minutes to about an hour, so the first question in a session pays full price and quick follow-ups are cheap.

Rough figures with a **full library of about 300 pages (about 150k tokens)**, based on published per-token prices as of October 2026:

| Mode | First question of a session | Follow-up question | Approx. per month |
|---|---|---|---|
| Economy (small GPT model) | ~$0.03 | under $0.01 | **about $1–10** |
| Best quality (flagship GPT model) | ~$0.30–1.50 | ~$0.03–0.20 | **about $20–150** |

With fewer pages loaded, costs shrink proportionally. Image transcription for scanned pages is a one-time cost at upload, roughly a cent or less per page. The app will show a live estimate before each question. Prices and model names live in one settings file and are easy to update when OpenAI changes them.

**Suggestion:** use Economy by default and switch to Best quality for hard or important questions. Also set a monthly spending limit in your OpenAI account settings as a safety net.
