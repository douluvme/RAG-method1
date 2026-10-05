# Document Q&A

Upload documents, ask questions in a chat, and get answers taken **only** from your documents, with the file, page and exact quote for every answer. If the documents don't contain the answer, the app says **"No information"** with a short note on what they do cover.

It uses the "long-context" approach: for each question, the AI reads all of your documents in full. There's no search step that might miss the right passage. The design is described in [PLAN.md](PLAN.md).

## What it supports

- **Files:** PDF, Word (.docx), text and Markdown (.txt, .md), and images (.png, .jpg, .webp, .gif). Scanned PDF pages and images are read with OpenAI's vision model once, at upload.
- **Sources:**
  - PDF: page number, with the quote highlighted on a picture of the page.
  - Word: heading and paragraph number, e.g. `plan.docx — "Budget", ¶3`.
  - Text files: line number.
  - Images: the image itself.
- **Checked quotes:** every quote the AI gives is checked against your document, and numbers must match exactly. A quote that can't be found is removed. An answer with no checked quotes left becomes "No information".
- **Languages:** documents can be in any language, and answers come in the language of your question.
- **Saved between sessions:** documents and chats are kept in the `data/` folder.
- **Cost control:** an Economy / Best quality switch, a cost estimate per question, and an "AI memory used" meter that blocks uploads that wouldn't fit.

## Setup (one time)

You need **Python 3.10 or newer** ([python.org/downloads](https://www.python.org/downloads/)) and your OpenAI API key.

1. Download this project. On GitHub, click **Code → Download ZIP** and unzip it, or use `git clone`.
2. Open a terminal in the project folder:
   - **Mac:** Terminal
   - **Windows:** PowerShell
3. Create a private Python environment and install the app's libraries:

   **Mac / Linux**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   **Windows (PowerShell)**
   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
4. Add your API key. Copy `.env.example` to a new file named `.env`, open it in a text editor, and replace `sk-...` with your key. The `.env` file never leaves your computer and isn't uploaded to GitHub.

## Start the app

In the project folder, turn on the environment (the `activate` line from step 3), then run:

```bash
streamlit run app.py
```

Your browser opens at <http://localhost:8501>. To stop the app, press **Ctrl+C** in the terminal.

**First run:** open **🔧 Check settings** in the sidebar and click **Test API key and models**. This confirms your key works and that the model names are available to your account.

## Changing models or prices

Model names and prices are in `.env`. See `.env.example` for every option.

| Setting | Default | Meaning |
|---|---|---|
| `ECONOMY_MODEL` | `gpt-5-mini` | Cheaper model, used by default |
| `BEST_MODEL` | `gpt-5` | Stronger model for important questions |
| `OCR_MODEL` | same as Economy | Reads scanned pages and images |
| `CONTEXT_LIMIT_TOKENS` | `350000` | Maximum total size of all documents (≈ 500–700 pages of normal text) |
| `*_PRICE_*` | see `.env.example` | US dollars per 1M tokens, used only for cost estimates |

If OpenAI releases newer models, change the names in `.env` and restart the app. **Check settings** lists the models your key can use. The context limit must stay below the chosen model's context window.

## Putting it online later

1. Set `APP_PASSWORD=...` in `.env` (or in the hosting service's "secrets" settings). The app then asks for that password.
2. Deploy to [Streamlit Community Cloud](https://streamlit.io/cloud) (free) or a service like Render or Railway.
3. Add `OPENAI_API_KEY` as a secret there. Never upload your `.env` file.

On hosting services with temporary disks, uploaded documents may be erased when the app restarts. Choose a plan with a persistent disk, or set `DATA_DIR` to point at one.

## For developers

```bash
pip install -r requirements-dev.txt
python -m pytest
```

| Path | What it does |
|---|---|
| `app.py` | Streamlit interface |
| `docqa/extract.py` | Reads files into citable sections (pages, headings, line blocks) |
| `docqa/ingest.py` | Upload pipeline and size check |
| `docqa/qa.py` | Prompt, OpenAI calls (Responses API, structured outputs), citation post-processing |
| `docqa/verify.py` | Quote verification |
| `docqa/db.py` | SQLite storage |
| `docqa/config.py` | Settings |
