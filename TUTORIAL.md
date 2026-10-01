# From web pages to a filled compliance questionnaire

*By Mihir Ranade · September 28, 2026*

*Learn how to turn a vendor's web pages into a filled security questionnaire with two LLM calls, verified quotes, and honest "not found" answers.*

Before your company shares data with a new vendor, someone has to fill out a security questionnaire about them. Where are they headquartered? Do they have a SOC 2 report? How quickly will they tell you about a breach?

The answers are usually on the vendor's own website, scattered across an About page, a Security page, and a footer. So you open the pages, read past the cookie banners and the "book a demo" ads, copy what matters, and paste it into a form. Then you do it again for the next vendor, and the next.

In this tutorial, we'll build a Python pipeline that does the reading and drafting for you: it fills the questionnaire from the vendor's pages, cites a source for every answer, and flags what it can't support instead of guessing. On a real run it answered 5 of 8 questions automatically, and the run also exposed a subtle bug that we'll fix together in step 9.

I first built this pattern at IBM on watsonx.ai for a client in compliance research. What follows is an independent rebuild with public tools and a fictional vendor, Halden Logistics, so you can run every line yourself.

**What you'll learn:** how to split an LLM job into small, checkable steps; how to get guaranteed JSON with structured outputs; how to verify a model's evidence in code; and how to find a bug that only shows up between steps.

**Who it's for:** developers and data practitioners who know some Python and are new to building with LLMs. It takes about 20 minutes and a few cents of API usage.

## Why not just paste the pages into a model?

That works in a demo and fails in a review. A questionnaire answer is only useful if a compliance reviewer can trust it, which puts three requirements on the design:

- **Traceability.** Every answer must point to the page it came from. Otherwise you can't tell which answers came from the vendor and which the model filled in from general knowledge.
- **Noise.** Real pages are full of navigation, ads, job postings, and marketing claims. "Bank-grade security" should never become a yes to an encryption question.
- **Honest gaps.** Some questions can't be answered from a vendor's public pages. The right output for those is a flag for a human, not a confident guess.

Let's look at how the pipeline handles all three.

## How it fits together

```text
saved pages ──▶ 1. load and clean (code)
            ──▶ 2. extract facts       model call 1, one page at a time
                     └─ check in code: every quote must appear on the page
            ──▶ 3. build answer bank   model call 2, one question at a time
                     └─ check in code: every answer must cite a real note
            ──▶ 4. filled questionnaire (code), with sources and review flags
```

Each model call does one narrow job, and code verifies what it hands to the next step. We'll build it in the order shown.

## Let's get started!

You'll need Python 3.10 or newer and an [Anthropic API key](https://console.anthropic.com/). A full run makes 11 model calls, 3 extractions and 8 answers, so it costs a few cents.

### 1. Install dependencies

Clone the companion repo and run the setup script for your platform. It creates a virtual environment in `.venv` and installs the four dependencies: the Anthropic SDK, Beautiful Soup, Requests, and pytest.

```bash
git clone https://github.com/mranade/compliance-autofill.git
cd compliance-autofill

# macOS / Linux
./scripts/setup.sh

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

If you use VS Code, open the folder and run **Terminal → Run Task → Set up project** instead.

### 2. Set up your environment

The setup script creates a `.env` file from `.env.example`. Open it and paste your key. The file is listed in `.gitignore`, so the key never gets committed.

```bash
ANTHROPIC_API_KEY=sk-ant-...

# Optional: a different Claude model.
# MODEL=claude-sonnet-5
```

The pipeline reads `.env` at startup. If the key is missing, it stops with a message saying where to put it, instead of failing deep inside an API call.

### 3. Meet the sample data

To keep the tutorial reproducible, we work from three saved pages for Halden Logistics in `data/pages/`, instead of scraping a live site. Every reader gets the same input and the same answers to check against.

| Page | What's on it | Noise the pipeline must ignore |
| --- | --- | --- |
| about.html | Headquarters, offices, company history, including an outdated line about starting SOC 2 in 2023 | Cookie banner, a 40%-off demo ad, and "Bank-grade security you can count on" |
| security.html | SOC 2 audit, encryption, retention, breach notice, a vague line about "industry-leading partners," a security mailbox | An e-book ad |
| careers.html | Office locations | Job postings, a referral bonus, "military-grade protection" |

The questionnaire in `questionnaire.json` has 8 questions. Five can be answered from the pages. Three can't be fully answered, on purpose: the subprocessors are never named, the security contact has a mailbox but no named person, and cyber insurance isn't mentioned anywhere. Those three test whether the pipeline guesses.

### 4. Load and clean the pages

Start with the cheap, deterministic cleanup, done in code. Scripts and styles are never useful, so we remove them. Whether a sentence is a real commitment or a marketing line is a judgment call, so that text stays in, and the model decides in the next step.

```python
def html_to_text(html: str) -> str:
    """Cheap, deterministic cleanup in code; judgment calls are left to the model."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)
```

Then load every saved page into a dictionary of page name to clean text:

```python
def load_saved_pages(folder: Path = PAGES_DIR) -> dict[str, str]:
    return {p.name: html_to_text(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.html"))}
```

### 5. Extract facts, with evidence

The first model call reads one page and returns only the facts a compliance analyst would care about. Here are the key rules in the prompt:

```text
- Ignore navigation, cookie banners, ads, promotions, job postings, and
  marketing claims that aren't concrete commitments (for example "bank-grade
  security" is not a fact).
- Each fact needs a "quote": text copied exactly, word for word, from the page.
```

Next, we make the reply's shape a guarantee rather than a hope. With structured outputs, the call passes a JSON schema, and the API returns JSON that matches it: a list of facts, each with a `topic`, a `fact`, and a `quote`.

```python
resp = client.messages.create(
    model=model,
    max_tokens=2000,
    messages=[{"role": "user", "content": prompt}],
    output_config={"format": {"type": "json_schema", "schema": schema}},
)
```

Finally, we check the model's evidence in code. A fact survives only if its quote actually appears on the page, ignoring differences in whitespace and case:

```python
for fact in result.get("facts", []):
    # Keep a fact only if its quote really appears on the page. This is a
    # cheap check that catches facts the model invented or paraphrased.
    if normalize(fact.get("quote", "")) not in normalize(text):
        print(f"  dropped a fact from {source}: quote not found on the page")
        continue
    notes.append({"id": f"N{len(notes) + 1}", "source": source, **fact})
```

On the real run, this step kept 16 facts across the three pages. Neither "bank-grade" nor "military-grade" became a fact.

### 6. Build the answer bank

The second model call answers one question at a time, using only the numbered notes from step 5. It returns a status (`answered`, `partial`, or `not_found`), the answer, the ids of the notes it used, and a one-line reason. The prompt draws the line explicitly:

```text
Use "partial" when the notes answer only part of the question.
Use "not_found" when the notes don't answer it. A vague claim is not an answer.
```

Then code enforces one rule the model can't talk its way around: an answer must cite at least one note that really exists.

```python
status = result.get("status", "not_found")
cited = [i for i in result.get("note_ids", []) if i in known_ids]
# An answer that cites no real note can't be trusted: send it to a human.
if status in ("answered", "partial") and not cited:
    status = "needs_review"
```

This step also handled the outdated 2023 line from the About page. The notes held both "we began our SOC 2 journey" in 2023 and a completed Type II audit for 2025, and the answer used the specific, current one. Because every answer carries its note ids, a reviewer can check that choice in seconds.

### 7. Fill the questionnaire and run the pipeline

The last step is plain code: write each answer with its source, and put a clear flag on anything a human should review. The run function ties the four steps together and saves each stage's output to `output/`, so you can inspect the handoffs as well as the result.

```python
def run(llm: LLM, pages: dict[str, str], questionnaire: dict, out_dir: Path = OUT_DIR) -> list[dict]:
    out_dir.mkdir(exist_ok=True)

    print(f"Step 2: extracting facts from {len(pages)} page(s)...")
    notes = extract_facts(llm, pages)
    (out_dir / "extracted_notes.json").write_text(json.dumps(notes, indent=2), encoding="utf-8")
    print(f"  kept {len(notes)} fact(s)")

    print(f"Step 3: answering {len(questionnaire['questions'])} question(s)...")
    bank = build_answer_bank(llm, questionnaire["questions"], notes)
    (out_dir / "answer_bank.json").write_text(json.dumps(bank, indent=2), encoding="utf-8")

    print("Step 4: filling the questionnaire...")
    filled = fill_questionnaire(questionnaire["title"], questionnaire["vendor"], bank)
    (out_dir / "filled_questionnaire.md").write_text(filled, encoding="utf-8")
    print(f"Done. See {out_dir / 'filled_questionnaire.md'}")
    return bank
```

Now run it. In VS Code, press **F5**; from a terminal:

```bash
# macOS / Linux
.venv/bin/python pipeline.py

# Windows (PowerShell)
.venv\Scripts\python.exe pipeline.py
```

It prints each step as it goes, plus a line for any fact dropped by the quote check, and writes three files to `output/`: `extracted_notes.json`, `answer_bank.json`, and `filled_questionnaire.md`.

Want to check the pipeline before spending anything? The tests swap in a stand-in model that returns scripted replies, so they need no API key or network. In VS Code, run **Terminal → Run Task → Run tests**; from a terminal:

```bash
.venv/bin/python -m pytest test_pipeline.py
```

### 8. Review the results

Open `output/filled_questionnaire.md`. Here is what the first real run produced:

| # | Question | Result | Answer | Source |
| --- | --- | --- | --- | --- |
| Q1 | Headquarters country | Answered | Rotterdam, the Netherlands | about.html |
| Q2 | SOC 2 report | Answered | Type II, Jan 1 to Dec 31, 2025, report under NDA | security.html |
| Q3 | Data retention after contract ends | Answered | 24 months | security.html |
| Q4 | Breach notification timeline | Answered | Within 72 hours of confirmation | security.html |
| Q5 | Subprocessors | Partial | Cloud and infrastructure partners exist, but none are named | security.html |
| Q6 | Encryption at rest | Answered | AES-256 | security.html |
| Q7 | Named security contact | Partial | Says no email address was given (wrong, see step 9) | security.html |
| Q8 | Cyber insurance | Not found | None | none |

Five of eight were answered automatically, each with a source. Q5 is a good partial: the page admits partners exist but never names them, and the model said exactly that instead of treating "industry-leading partners" as an answer. Q8 was correctly left for a human.

One subtle point on Q2: the answer calls the report "current," but the page only gives the audit period. "Current" is the model's own inference. It's reasonable for a 2025 audit read in 2026, but it's the kind of word a reviewer should notice.

Q7 is the interesting one.

### 9. Fix a detail lost in the handoff

The Security page says, plainly: "Email our security team at security@halden.example." Yet the Q7 answer said the notes gave no email address.

The pipeline had the address the whole time. It was lost in the handoff between steps. Step 5 extracted this note:

```json
{
  "id": "N14",
  "fact": "Security issues can be reported via email to the security team.",
  "quote": "Found a security issue? Email our security team at security@halden.example."
}
```

The quote, the part code verified, has the address. The fact, the model's own summary of it, dropped it. And step 6 only ever saw the facts. Every check passed, and the one detail the question asked for was summarized away.

The safety net still did its job: Q7 came back as partial and flagged for review, not confidently wrong. But a reviewer would have had to find the address themselves.

The fix is small: hand the answering step the verified quote along with the fact.

```python
# Pass the verified quote along with the fact. The fact is the model's own
# summary and can drop details (an early run summarized away the security
# team's email address); the quote is the page's exact words.
notes_text = "\n".join(
    f"[{n['id']}] ({n['source']}) {n['fact']}\n    Quote: \"{' '.join(n['quote'].split())}\""
    for n in notes
) or "(no notes)"
```

To check that this fix is what made the difference, replay the first run's exact notes, including the N14 summary that dropped the address, through the fixed answering step. Q7 now comes back with the address every time (3 of 3 runs), and it stays partial because the page still names no person:

```text
Partial: Security issues can be reported via email to security@halden.example, and the
team aims to respond within two business days. No specific named individual designated
as the security contact is provided.
```

A regression test now checks that the answering prompt contains the exact quote, so this can't quietly come back. The first run's outputs, bug included, are saved in `docs/sample-run/` so you can compare them with your own.

Expect your own run to differ in small ways, because the model doesn't extract the same facts every time. On a second full run, extraction kept 15 facts instead of 16. It skipped the vague "industry-leading partners" line, so Q5 came back as not found instead of partial. Both results are safe, since each one flags Q5 for a human, but it's a reminder that one run is an anecdote. Measuring results over many runs is the natural next step.

### 10. Try your own vendor

Once the sample run makes sense, point the pipeline at real pages. The `--live` flag fetches each URL, checks `robots.txt` first, and skips pages that disallow automated access.

```bash
.venv/bin/python pipeline.py --live https://example.com/about https://example.com/security
```

You can also swap in your own questions by editing `questionnaire.json`, or pass a different file with `--questionnaire`. Keep a few questions you know the pages can't answer. They're the quickest way to see whether the pipeline guesses.

## Conclusion

We built a pipeline that turns a vendor's web pages into a filled security questionnaire a reviewer can actually trust: every answer cites its source, and every gap is flagged instead of guessed. The ideas carry over to any LLM pipeline:

- **Split the job so each call does one thing.** Reading noisy pages and answering precise questions are different tasks, and separating them keeps each prompt short and each output checkable.
- **Make the model show its evidence, then check it in code.** A required quote costs one schema field and one line of code, and it turns "trust the model" into "verify the model."
- **Let the API enforce the format.** Structured outputs remove a whole class of parsing bugs.
- **Treat "not found" as a correct answer.** A pipeline that flags what it can't support is more useful than one that fills every box.
- **Watch the handoffs, not just the steps.** In our first run every check passed and an answer was still wrong, because a summary replaced the evidence between steps.

Natural next steps are measuring accuracy across many vendors instead of one, and adding a small review screen where an analyst approves or corrects each flagged answer. The full code, tests, and the first run's outputs are in the [companion repo on GitHub](https://github.com/mranade/compliance-autofill).

## Meet the author

**Mihir Ranade** is a solutions engineer who builds AI proofs of concept and production agents. At IBM, he led client-facing generative AI proofs of concept on watsonx.ai for enterprise accounts in healthcare and technology, and he has since built an AI agent that runs in production for industrial cooling optimization. He holds a B.S. in Computer Science from Purdue University. Connect with him on [LinkedIn](https://www.linkedin.com/in/mihir-ranade-88251b19a).
