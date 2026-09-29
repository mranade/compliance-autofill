"""Fill a vendor due-diligence questionnaire from the vendor's own web pages.

    pages --> 1. fetch --> 2. extract facts (LLM #1, per page) --> 3. answer bank (LLM #2, per question) --> 4. fill

Run:
    python pipeline.py                  # uses the saved pages in data/pages/
    python pipeline.py --live URL ...   # fetches real pages instead

Needs ANTHROPIC_API_KEY. Set MODEL to change the model (default claude-sonnet-5).
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Callable

from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
PAGES_DIR = ROOT / "data" / "pages"
OUT_DIR = ROOT / "output"

# An LLM here is just "prompt + JSON schema in, text out". Keeping it that simple
# makes the pipeline easy to test with a fake model and to point at another provider.
LLM = Callable[[str, dict], str]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

EXTRACT_PROMPT = """You are helping a compliance analyst review a vendor.
Below is the text of one page from the vendor's website.

Extract every fact that could help answer a security or compliance
due-diligence questionnaire: company location, certifications and audits,
encryption, data retention, incident response, third parties that handle
data, insurance, and security contacts.

Rules:
- Ignore navigation, cookie banners, ads, promotions, job postings, and
  marketing claims that aren't concrete commitments (for example "bank-grade
  security" is not a fact).
- Each fact needs a "quote": text copied exactly, word for word, from the page.
- If the page has no relevant facts, return an empty list.

Return JSON with a "facts" list; each fact has "topic", "fact", and "quote".

Page ({source}):
<<<
{text}
>>>"""

ANSWER_PROMPT = """You are filling out a vendor due-diligence questionnaire.
Answer the question using ONLY the numbered notes below. Do not use outside
knowledge and do not guess.

Question {qid}: {question}

Notes:
{notes}

Return JSON with:
- "status": "answered", "partial", or "not_found"
- "answer": the answer, or "" if not_found
- "note_ids": the ids of the notes you used, like ["N1", "N4"]
- "reason": one sentence on why, including what is missing if partial

Use "partial" when the notes answer only part of the question.
Use "not_found" when the notes don't answer it. A vague claim is not an answer."""


# Structured outputs: the API guarantees the reply matches these schemas.
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "fact": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["topic", "fact", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "partial", "not_found"]},
        "answer": {"type": "string"},
        "note_ids": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["status", "answer", "note_ids", "reason"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Talking to the model
# ---------------------------------------------------------------------------

def claude_llm() -> LLM:
    from anthropic import Anthropic

    client = Anthropic()  # reads ANTHROPIC_API_KEY
    model = os.environ.get("MODEL", "claude-sonnet-5")

    def call(prompt: str, schema: dict) -> str:
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    return call


def parse_json(text: str) -> dict:
    """Pull the JSON object out of a reply, tolerating ```json fences."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in the reply")
    return json.loads(match.group(0))


def ask_json(llm: LLM, prompt: str, schema: dict) -> dict:
    """Ask for JSON matching a schema; if the reply doesn't parse, retry once.

    With structured outputs the retry should never fire. It's a safety net for
    providers or models that don't enforce schemas.
    """
    reply = llm(prompt, schema)
    try:
        return parse_json(reply)
    except (ValueError, json.JSONDecodeError) as err:
        retry = f"{prompt}\n\nYour previous reply was not valid JSON ({err}). Return only the JSON."
        try:
            return parse_json(llm(retry, schema))
        except (ValueError, json.JSONDecodeError) as err2:
            raise RuntimeError(f"Model did not return valid JSON after a retry: {err2}") from err2


# ---------------------------------------------------------------------------
# Step 1: fetch
# ---------------------------------------------------------------------------

def html_to_text(html: str) -> str:
    """Cheap, deterministic cleanup in code; judgment calls are left to the model."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def load_saved_pages(folder: Path = PAGES_DIR) -> dict[str, str]:
    return {p.name: html_to_text(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.html"))}


def fetch_live_pages(urls: list[str]) -> dict[str, str]:
    import urllib.robotparser
    from urllib.parse import urljoin

    import requests

    agent = "compliance-autofill-tutorial/1.0"
    pages = {}
    for url in urls:
        robots = urllib.robotparser.RobotFileParser(urljoin(url, "/robots.txt"))
        robots.read()
        if not robots.can_fetch(agent, url):
            print(f"  skipping {url}: disallowed by robots.txt")
            continue
        resp = requests.get(url, headers={"User-Agent": agent}, timeout=20)
        resp.raise_for_status()
        pages[url] = html_to_text(resp.text)
    return pages


# ---------------------------------------------------------------------------
# Step 2: extract facts from each page
# ---------------------------------------------------------------------------

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def extract_facts(llm: LLM, pages: dict[str, str]) -> list[dict]:
    notes = []
    for source, text in pages.items():
        result = ask_json(llm, EXTRACT_PROMPT.format(source=source, text=text), EXTRACT_SCHEMA)
        for fact in result.get("facts", []):
            # Keep a fact only if its quote really appears on the page. This is a
            # cheap check that catches facts the model invented or paraphrased.
            if normalize(fact.get("quote", "")) not in normalize(text):
                print(f"  dropped a fact from {source}: quote not found on the page")
                continue
            notes.append({"id": f"N{len(notes) + 1}", "source": source, **fact})
    return notes


# ---------------------------------------------------------------------------
# Step 3: build the answer bank
# ---------------------------------------------------------------------------

def build_answer_bank(llm: LLM, questions: list[dict], notes: list[dict]) -> list[dict]:
    # Pass the verified quote along with the fact. The fact is the model's own
    # summary and can drop details (an early run summarized away the security
    # team's email address); the quote is the page's exact words.
    notes_text = "\n".join(
        f"[{n['id']}] ({n['source']}) {n['fact']}\n    Quote: \"{' '.join(n['quote'].split())}\""
        for n in notes
    ) or "(no notes)"
    known_ids = {n["id"] for n in notes}
    bank = []
    for q in questions:
        result = ask_json(llm, ANSWER_PROMPT.format(qid=q["id"], question=q["text"], notes=notes_text), ANSWER_SCHEMA)
        status = result.get("status", "not_found")
        cited = [i for i in result.get("note_ids", []) if i in known_ids]
        # An answer that cites no real note can't be trusted: send it to a human.
        if status in ("answered", "partial") and not cited:
            status = "needs_review"
        bank.append({
            "id": q["id"],
            "question": q["text"],
            "status": status,
            "answer": (result.get("answer") or None) if status != "not_found" else None,
            "sources": sorted({n["source"] for n in notes if n["id"] in cited}),
            "note_ids": cited,
            "reason": result.get("reason", ""),
        })
    return bank


# ---------------------------------------------------------------------------
# Step 4: fill the questionnaire
# ---------------------------------------------------------------------------

LABELS = {
    "answered": "Answered",
    "partial": "Partial: needs review",
    "not_found": "Not found: needs a human",
    "needs_review": "Unsupported: needs review",
}


def fill_questionnaire(title: str, vendor: str, bank: list[dict]) -> str:
    done = sum(a["status"] == "answered" for a in bank)
    lines = [f"# {title}", "", f"**Vendor:** {vendor}  ",
             f"**Answered automatically:** {done} of {len(bank)}. Everything else is flagged for review.", ""]
    for a in bank:
        lines.append(f"## {a['id']}. {a['question']}")
        lines.append(f"**{LABELS.get(a['status'], a['status'])}**  ")
        if a["answer"]:
            lines.append(a["answer"] + "  ")
        if a["sources"]:
            lines.append(f"_Source: {', '.join(a['sources'])}_  ")
        if a["status"] != "answered" and a["reason"]:
            lines.append(f"_Why: {a['reason']}_")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Run it
# ---------------------------------------------------------------------------

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


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=value lines from .env into the environment (without overriding real env vars)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def main() -> None:
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Put it in the .env file in this folder "
            "(ANTHROPIC_API_KEY=sk-ant-...) or set it in your terminal, then run again."
        )
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", nargs="+", metavar="URL", help="fetch these pages instead of the saved ones")
    parser.add_argument("--questionnaire", default=str(ROOT / "questionnaire.json"))
    args = parser.parse_args()

    questionnaire = json.loads(Path(args.questionnaire).read_text(encoding="utf-8"))
    print("Step 1: loading pages...")
    pages = fetch_live_pages(args.live) if args.live else load_saved_pages()
    run(claude_llm(), pages, questionnaire)


if __name__ == "__main__":
    main()
