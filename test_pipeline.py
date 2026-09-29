"""Tests with a fake model: no API key or network needed.

    python -m pytest test_pipeline.py
"""

import json

import pipeline


def fake_llm(replies):
    """A stand-in model that returns scripted replies in order."""
    queue = list(replies)
    prompts = []

    def call(prompt, schema):
        assert schema["type"] == "object"
        prompts.append(prompt)
        return queue.pop(0)

    call.prompts = prompts
    return call


PAGE = "Halden is headquartered in Rotterdam, the Netherlands.\nBank-grade security you can count on."


def test_html_cleanup_drops_scripts_but_keeps_visible_text():
    text = pipeline.html_to_text("<nav>Home</nav><script>track()</script><p>We use AES-256.</p>")
    assert "track()" not in text
    assert "We use AES-256." in text and "Home" in text


def test_facts_with_invented_quotes_are_dropped():
    reply = json.dumps({"facts": [
        {"topic": "location", "fact": "HQ in the Netherlands", "quote": "headquartered in Rotterdam, the Netherlands"},
        {"topic": "certification", "fact": "ISO 27001 certified", "quote": "We are ISO 27001 certified"},
    ]})
    notes = pipeline.extract_facts(fake_llm([reply]), {"about.html": PAGE})
    assert [n["topic"] for n in notes] == ["location"]
    assert notes[0]["id"] == "N1" and notes[0]["source"] == "about.html"


def test_bad_json_is_retried_once():
    good = json.dumps({"facts": []})
    llm = fake_llm(["Sure! Here are the facts you asked for.", f"```json\n{good}\n```"])
    assert pipeline.extract_facts(llm, {"about.html": PAGE}) == []
    assert "not valid JSON" in llm.prompts[1]


def test_answers_citing_unknown_notes_go_to_review():
    notes = [{"id": "N1", "source": "about.html", "topic": "location", "fact": "HQ in the Netherlands", "quote": "x"}]
    replies = [
        json.dumps({"status": "answered", "answer": "The Netherlands", "note_ids": ["N1"], "reason": ""}),
        json.dumps({"status": "answered", "answer": "Yes, EUR 5M", "note_ids": ["N9"], "reason": ""}),
        json.dumps({"status": "not_found", "answer": "", "note_ids": [], "reason": "No insurance info."}),
    ]
    questions = [{"id": "Q1", "text": "HQ?"}, {"id": "Q8", "text": "Insurance?"}, {"id": "Q9", "text": "Other?"}]
    bank = pipeline.build_answer_bank(fake_llm(replies), questions, notes)
    assert [a["status"] for a in bank] == ["answered", "needs_review", "not_found"]
    assert bank[0]["sources"] == ["about.html"]
    assert bank[2]["answer"] is None


def test_full_run_writes_all_three_outputs(tmp_path):
    pages = pipeline.load_saved_pages()
    questionnaire = json.loads((pipeline.ROOT / "questionnaire.json").read_text(encoding="utf-8"))
    extract = [json.dumps({"facts": []})] * len(pages)
    answers = [json.dumps({"status": "not_found", "answer": "", "note_ids": [], "reason": "No notes."})]
    replies = extract + answers * len(questionnaire["questions"])
    pipeline.run(fake_llm(replies), pages, questionnaire, out_dir=tmp_path)
    for name in ("extracted_notes.json", "answer_bank.json", "filled_questionnaire.md"):
        assert (tmp_path / name).exists()
    assert "Answered automatically:** 0 of 8" in (tmp_path / "filled_questionnaire.md").read_text(encoding="utf-8")
