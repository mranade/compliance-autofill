# Compliance autofill

Companion code for the tutorial *From web pages to a filled compliance
questionnaire*. It fills out a vendor security questionnaire from the vendor's
own web pages, cites a source for every answer, and flags anything it can't
support for a human to review.

```
pages ─▶ 1. fetch ─▶ 2. extract facts (LLM, per page) ─▶ 3. answer bank (LLM, per question) ─▶ 4. filled questionnaire
```

The vendor, **Halden Logistics**, is fictional. Its three web pages in
`data/pages/` were written for this tutorial and include the kind of noise a
real site has: navigation, cookie banners, ads, job postings, and marketing
claims that sound like security commitments but aren't.

I originally built this pattern on watsonx.ai at IBM. This is an independent
rebuild with public tools and made-up data.

## Run it

Needs Python 3.10+ and an [Anthropic API key](https://console.anthropic.com/).

### In VS Code

1. Open this folder in VS Code.
2. **Terminal → Run Task → Set up project.** One time: creates `.venv`,
   installs the dependencies, and creates a `.env` file.
3. Open `.env` and paste your key after `ANTHROPIC_API_KEY=`. The file is
   ignored by git, so the key is never committed.
4. **Press F5** (Run pipeline). Progress prints in the terminal; the results
   land in `output/`.

Other tasks: **Run tests** (no key needed) and **Open filled questionnaire**.

### From a terminal

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# paste your key into .env, then:
.venv\Scripts\python.exe pipeline.py
```

```bash
# macOS / Linux
./scripts/setup.sh
# paste your key into .env, then:
.venv/bin/python pipeline.py
```

You can also set `ANTHROPIC_API_KEY` in your terminal instead of using `.env`.

Results go to `output/`:

| File | What's in it |
|---|---|
| `extracted_notes.json` | Facts pulled from each page, each with a word-for-word quote |
| `answer_bank.json` | One answer per question, with its status, the notes it used, and why |
| `filled_questionnaire.md` | The finished questionnaire, with sources and review flags |

Set `MODEL` to try a different Claude model. To run on real pages instead of
the saved ones, pass URLs: `python pipeline.py --live https://example.com/security`.
Live mode checks `robots.txt` first and skips pages that disallow it.

## How it keeps answers honest

- **Two model calls, not one.** Extraction reads one page at a time and keeps
  only facts, so the answering step works from short, clean notes instead of
  three noisy pages.
- **Structured outputs.** Each call sends a JSON schema, and the API guarantees
  the reply matches it. A parse-and-retry step is kept as a safety net.
- **Quotes are checked in code.** Every extracted fact must include a
  word-for-word quote, and a fact is dropped if that quote isn't on the page.
- **The answering step sees the evidence, not just a summary.** Each note is
  passed on with its verified quote. The first real run showed why: a fact
  summary dropped the security team's email address that its quote contained,
  and the answer claimed no address was given. That run is kept in
  `docs/sample-run/`.
- **Answers must cite notes.** An answer that doesn't cite a real note is
  marked "needs review" instead of being trusted.
- **"Not found" is a valid answer.** Several questions are deliberately
  unanswerable from these pages. The right result is a flag for a human, not a
  guess.

## Tests

```bash
python -m pytest test_pipeline.py
```

The tests use a fake model, so they need no API key or network.
