"""Service mode: durable intake, redelivery, outages held in the queue, dead-letter, and memory
across runs. A fake model (switchable off) and a fake clock make "two hours later" instant.
The real-model check (dev inbox through the service == the batch parsed.json, byte for byte)
is the last test."""

from __future__ import annotations

import json
import shutil
from email.message import EmailMessage
from pathlib import Path

from rfq.llm import CallResult, ModelUnavailable
from rfq.service import BACKOFF, MAX_ATTEMPTS, Service

ROOT = Path(__file__).resolve().parents[1]


def eml(msgid, subject, body, date, sender="Pat <pat@acme.example>", reply_to=None) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"], m["Message-ID"] = sender, "rfq@ridgeline-supply.example", subject, date, msgid
    if reply_to:
        m["In-Reply-To"] = m["References"] = reply_to
    m.set_content(body)
    return m.as_bytes()


class Fake:
    """Reads 'N x item' lines; a body with 'make #1' is a revision. Can be switched off."""
    model, effort = "fake", "none"

    def __init__(self):
        self.up, self.calls = True, 0

    def request(self, instructions, content, schema):
        return {"text": "\n".join(c.get("text", "") for c in content)}

    def call(self, req, label=""):
        if not self.up:
            raise ModelUnavailable("provider down")
        self.calls += 1
        body = req["text"].split("=== BODY ===", 1)[-1]
        rev = "make #1" in body
        lines = [ln.strip() for ln in body.splitlines() if " x " in ln and ln.strip()[:1].isdigit()]
        data = {"classification_reason": "test", "classification": "rfq_revision" if rev else "rfq",
                "needs_review": False, "review_reason": None,
                "line_items": [{"raw": ln, "description": ln.split(" x ", 1)[1], "qty": int(ln.split(" x ")[0]),
                                "uom": None, "source": "body"} for ln in lines]}
        usage = {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1, "reasoning_tokens": 0}
        return CallResult(data, usage, 0.0, False, 0.0, key=label)


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def svc(tmp_path, llm=None, clock=None):
    return Service(tmp_path / "state", llm or Fake(), None, clock or Clock())


def drop(s: Service, name: str, data: bytes) -> None:
    (s.state / "intake" / name).write_bytes(data)


A = eml("<a@acme>", "RFQ pump 3", "Please quote:\n4 x 2in gate valve\n8 x 2in flange\n", "Mon, 02 Nov 2026 09:00:00 -0600")


def test_an_email_dropped_in_intake_is_stored_processed_and_written(tmp_path):
    s = svc(tmp_path)
    drop(s, "A.eml", A)
    assert s.intake() == {"queued": 1, "redelivered": 0}
    assert s.work() == {"done": 1, "retry": 0, "dead": 0}
    assert (s.state / "raw" / "A.eml").exists() and not (s.state / "intake" / "A.eml").exists()
    rec = json.loads((s.state / "out" / "A.json").read_text(encoding="utf-8"))
    assert [ln["qty"] for ln in rec["line_items"]] == [4, 8]


def test_redelivery_costs_nothing(tmp_path):
    llm = Fake()
    s = svc(tmp_path, llm)
    drop(s, "A.eml", A)
    s.intake(), s.work()
    drop(s, "A-again.eml", A)  # same bytes
    drop(s, "A-relay.eml", A.replace(b"Mon, 02 Nov", b"Mon, 02 Nov").replace(b"Please quote", b"Pls quote"))  # same Message-ID
    assert s.intake() == {"queued": 0, "redelivered": 2}
    assert s.work()["done"] == 0 and llm.calls == 1


def test_a_revision_next_week_finds_its_request_in_history(tmp_path):
    s = svc(tmp_path)
    drop(s, "A.eml", A)
    s.intake(), s.work()
    s2 = Service(tmp_path / "state", Fake(), None, Clock())  # a new worker process, days later
    drop(s2, "B.eml", eml("<b@acme>", "Re: RFQ pump 3", "make #1 six\n6 x 2in gate valve\n8 x 2in flange\n",
                          "Mon, 09 Nov 2026 09:00:00 -0600", reply_to="<a@acme>"))
    s2.intake(), s2.work()
    rec = json.loads((s2.state / "out" / "B.json").read_text(encoding="utf-8"))
    assert rec["classification"] == "rfq_revision" and rec["revises"] == "A"
    assert not rec["needs_review"], "the original is in history, so this revision is anchored"


def test_a_resend_next_week_is_a_duplicate_of_history(tmp_path):
    s = svc(tmp_path)
    drop(s, "A.eml", A)
    s.intake(), s.work()
    drop(s, "A2.eml", eml("<a2@acme>", "RFQ pump 3", "Please quote:\n4 x 2in gate valve\n8 x 2in flange\n",
                          "Mon, 09 Nov 2026 10:00:00 -0600"))
    s.intake(), s.work()
    rec = json.loads((s.state / "out" / "A2.json").read_text(encoding="utf-8"))
    assert rec["duplicate_of"] == "A" and rec["line_items"] == []


def test_an_outage_waits_in_the_queue_instead_of_going_to_a_person(tmp_path):
    llm, clock = Fake(), Clock()
    s = svc(tmp_path, llm, clock)
    llm.up = False
    drop(s, "A.eml", A)
    s.intake()
    assert s.work() == {"done": 0, "retry": 1, "dead": 0}
    assert not (s.state / "out" / "A.json").exists(), "no premature 'a person must read this'"
    assert s.work() == {"done": 0, "retry": 0, "dead": 0}, "not due yet: backoff"
    llm.up, clock.t = True, clock.t + BACKOFF[0]  # the provider is back
    assert s.work() == {"done": 1, "retry": 0, "dead": 0}
    assert s.status()["by_status"] == {"done": 1}


def test_a_long_outage_ends_in_the_dead_letter_flagged_for_a_person(tmp_path):
    llm, clock = Fake(), Clock()
    s = svc(tmp_path, llm, clock)
    llm.up = False
    drop(s, "A.eml", A)
    s.intake()
    for _ in range(MAX_ATTEMPTS):
        s.work()
        clock.t += max(BACKOFF)
    rec = json.loads((s.state / "out" / "A.json").read_text(encoding="utf-8"))
    assert rec["needs_review"] and "a person must read this" in rec["review_reason"]
    assert (s.state / "dead" / "A.eml").exists() and s.status()["dead_letter"] == ["A"]


def test_a_worker_that_died_mid_email_is_recovered(tmp_path):
    s = svc(tmp_path)
    drop(s, "A.eml", A)
    s.intake()
    s._claim()  # claimed, then the process "dies"
    s2 = Service(tmp_path / "state", Fake(), None, Clock())
    assert s2.work()["done"] == 1


def test_the_dev_inbox_through_the_service_equals_the_batch_run(tmp_path):
    from rfq.llm import LLM

    s = Service(tmp_path / "state", LLM("gpt-5.4-mini-2026-03-17", "medium", ROOT / "cache", None),
                ROOT / "data" / "catalog.csv")
    for f in sorted((ROOT / "data" / "dev" / "emails").glob("*.eml")):
        shutil.copy(f, s.state / "intake" / f.name)
    s.intake()
    assert s.work() == {"done": 24, "retry": 0, "dead": 0}
    s.export(tmp_path / "export.json")
    assert (tmp_path / "export.json").read_bytes() == (ROOT / "parsed.json").read_bytes()
