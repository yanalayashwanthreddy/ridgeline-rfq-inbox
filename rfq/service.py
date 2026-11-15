"""Service mode: a long-running worker with a durable queue and a memory of past emails.

    python -m rfq serve  --state DIR [--once] [--poll 5]   process what arrives in DIR/intake/
    python -m rfq status --state DIR                        queue health
    python -m rfq export --state DIR --out parsed.json      every result, in the README's format

`parse` handles one inbox at a time. Production is a stream: about 3,000 emails a day arrive by
webhook, the model provider sometimes goes down, mail systems deliver the same message twice, and
a revision arrives days after its request. This module is that production shape:

  intake   The webhook's stand-in is a folder: anything that writes DIR/intake/<name>.eml (write
           a temp name, then rename) hands over an email. Each one is stored once, durably, in
           DIR/raw/, and queued. Redelivery is free: the same bytes or the same Message-ID is
           recognised and never processed twice.
  queue    One SQLite row per email (DIR/rfq.db): queued -> done, or retry -> ... -> dead. A worker
           claims a row atomically, so several workers can share one queue.
  outages  If the model can't be reached, the email is NOT dumped on a person: it waits and is
           retried with backoff (1 min ... 2 h, ~4 h in all, longer than a typical outage). Only
           then does it go to the dead-letter folder, flagged for a person.
  memory   Each finished email keeps its headers, attachment fingerprints and result. A new email
           is linked against that history, so a revision or a resend from last week is found.
  same     The work per email is the same code as `parse` (ingest -> model -> guards -> links ->
           attention -> catalog), so the dev inbox through the service gives a parsed.json
           byte-identical to the batch one.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from email import policy
from email.parser import BytesHeaderParser
from pathlib import Path

from .attention import add_attention
from .guards import fallback
from .ingest import Email, Part
from .llm import LLM, cost_usd
from .matcher import load_catalog, match_all
from .pipeline import process_one
from .threads import link

BACKOFF = [60, 300, 900, 1800, 3600, 7200]   # seconds between attempts while the model is unreachable
MAX_ATTEMPTS = len(BACKOFF) + 1              # ~4 h of trying, then a person
HISTORY_DAYS = 90                            # how far back a new email looks for its thread

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,   -- arrival order
  id           TEXT UNIQUE NOT NULL,                -- the key in parsed.json
  sha256       TEXT UNIQUE NOT NULL,                -- of the raw bytes: redelivery of the same bytes
  message_id   TEXT,                                -- redelivery of the same message
  status       TEXT NOT NULL,                       -- queued | working | retry | done | dead
  attempts     INTEGER NOT NULL DEFAULT 0,
  next_at      REAL NOT NULL DEFAULT 0,
  received_at  REAL NOT NULL,
  done_at      REAL,
  last_error   TEXT,
  -- memory for thread linking: enough of the email to link against, without re-reading it
  ts REAL, sender TEXT, subject TEXT, in_reply_to TEXT, refs TEXT, parts TEXT,
  record TEXT, calls INTEGER, cost REAL
);
CREATE INDEX IF NOT EXISTS emails_msgid ON emails(message_id);
CREATE INDEX IF NOT EXISTS emails_due ON emails(status, next_at);
CREATE INDEX IF NOT EXISTS emails_ts ON emails(ts);
CREATE TABLE IF NOT EXISTS events (at REAL, id TEXT, event TEXT, detail TEXT);
"""


class Service:
    def __init__(self, state: Path, llm: LLM, catalog: Path | None, clock=time.time):
        self.state, self.llm, self.clock = state, llm, clock
        for d in ("intake", "raw", "out", "dead"):
            (state / d).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(state / "rfq.db", timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.catalog = load_catalog(catalog) if catalog and catalog.is_file() else None
        # a worker that died mid-email left its row 'working': put it back in the queue. (One
        # worker per database here; several workers would need a lease with an expiry instead.)
        stuck = self.db.execute("UPDATE emails SET status='retry', next_at=0 WHERE status='working'").rowcount
        if stuck:
            self._event("-", "recovered", f"{stuck} email(s) left mid-processing by a stopped worker")

    # ------------------------------------------------------------------ intake

    def intake(self) -> dict:
        """Move every email in intake/ into durable storage and the queue (or drop a redelivery)."""
        seen = {"queued": 0, "redelivered": 0}
        for f in sorted((self.state / "intake").glob("*.eml")):
            data = f.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            try:
                msgid = (BytesHeaderParser(policy=policy.default).parsebytes(data).get("Message-ID") or "").strip() or None
            except Exception:
                msgid = None
            dup = self.db.execute("SELECT id FROM emails WHERE sha256=? OR (message_id IS NOT NULL AND message_id=?)",
                                  (sha, msgid)).fetchone()
            if dup:
                self._event(dup["id"], "redelivered", f"{f.name}: already received (same bytes or Message-ID)")
                f.unlink()
                seen["redelivered"] += 1
                continue
            eid = self._new_id(f.stem, sha)
            tmp = self.state / "raw" / f"{eid}.eml.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, self.state / "raw" / f"{eid}.eml")  # durable before we let go of intake
            self.db.execute("INSERT INTO emails(id, sha256, message_id, status, received_at) VALUES (?,?,?, 'queued', ?)",
                            (eid, sha, msgid, self.clock()))
            self._event(eid, "queued", f.name)
            f.unlink()
            seen["queued"] += 1
        return seen

    def _new_id(self, stem: str, sha: str) -> str:
        stem = re.sub(r"[^\w.-]+", "_", stem)[:80] or "email"
        if not self.db.execute("SELECT 1 FROM emails WHERE id=?", (stem,)).fetchone():
            return stem
        return f"{stem}-{sha[:8]}"  # a different email that happens to share a file name

    # ------------------------------------------------------------------ work

    def work(self) -> dict:
        """Process every queued or due-for-retry email once, oldest first."""
        done = {"done": 0, "retry": 0, "dead": 0}
        while True:
            row = self._claim()
            if row is None:
                return done
            done[self._process(row)] += 1

    def _claim(self):
        """Atomically take the oldest due row (safe with several workers on one database)."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM emails WHERE status IN ('queued','retry') AND next_at <= ? "
                                  "ORDER BY seq LIMIT 1", (self.clock(),)).fetchone()
            if row is not None:
                self.db.execute("UPDATE emails SET status='working' WHERE id=?", (row["id"],))
            self.db.execute("COMMIT")
            return row
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _process(self, row) -> str:
        eid, path = row["id"], self.state / "raw" / f"{row['id']}.eml"
        attempts = row["attempts"] + 1
        em, record, stats = process_one(path, self.llm)
        if stats.get("fallback") and not stats.get("traceback"):  # the model couldn't be reached
            if attempts < MAX_ATTEMPTS:
                wait = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
                self.db.execute("UPDATE emails SET status='retry', attempts=?, next_at=?, last_error=? WHERE id=?",
                                (attempts, self.clock() + wait, stats["fallback"], eid))
                self._event(eid, "retry", f"attempt {attempts}: {stats['fallback']}; next in {wait}s")
                return "retry"
            record = fallback(em, f"model unavailable after {attempts} attempts: {stats['fallback']}")
            shutil.copy2(path, self.state / "dead" / path.name)
            self._finish(eid, em, record, stats, attempts, "dead")
            self._event(eid, "dead", "handed to a person after retries")
            return "dead"

        # memory: link against earlier emails (copies, so history is never rewritten), then the
        # same post-processing as the batch run
        emails, out = self._history(em)
        emails[eid], out[eid] = em, record
        link(emails, out)
        record = out[eid]
        add_attention({eid: em}, {eid: record}, {eid: stats})
        if self.catalog is not None:
            match_all({eid: record}, self.catalog)
        self._finish(eid, em, record, stats, attempts, "done")
        self._event(eid, "done", f"{record['classification']}, {len(record['line_items'])} line(s)")
        return "done"

    def _finish(self, eid, em: Email, record: dict, stats: dict, attempts: int, status: str) -> None:
        parts = [{"sha256": p.sha256, "inline": p.inline, "kind": p.kind, "mime": p.mime, "filename": p.filename}
                 for p in em.parts]
        cost = cost_usd(self.llm.model, stats["usage"]) if stats.get("usage") else None
        (self.state / "out" / f"{eid}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                                        encoding="utf-8", newline="\n")
        self.db.execute(
            "UPDATE emails SET status=?, attempts=?, done_at=?, last_error=NULL, ts=?, sender=?, subject=?, "
            "in_reply_to=?, refs=?, parts=?, record=?, calls=?, cost=? WHERE id=?",
            (status, attempts, self.clock(), em.timestamp, em.sender, em.subject, em.in_reply_to,
             json.dumps(em.references), json.dumps(parts), json.dumps(record, ensure_ascii=False),
             len(stats.get("cache_keys") or []), cost, eid))

    def _history(self, em: Email) -> tuple[dict, dict]:
        since = (em.timestamp or self.clock()) - HISTORY_DAYS * 86400
        emails, out = {}, {}
        for r in self.db.execute("SELECT * FROM emails WHERE status='done' AND (ts IS NULL OR ts >= ?)", (since,)):
            emails[r["id"]] = Email(
                id=r["id"], sender=r["sender"] or "", subject=r["subject"] or "", timestamp=r["ts"],
                message_id=r["message_id"], in_reply_to=r["in_reply_to"], references=json.loads(r["refs"] or "[]"),
                parts=[Part(filename=p["filename"], mime=p["mime"], size=0, sha256=p["sha256"], inline=p["inline"],
                            kind=p["kind"]) for p in json.loads(r["parts"] or "[]")])
            out[r["id"]] = copy.deepcopy(json.loads(r["record"]))
        return emails, out

    def _event(self, eid: str, event: str, detail: str) -> None:
        self.db.execute("INSERT INTO events VALUES (?,?,?,?)", (self.clock(), eid, event, detail))

    # ------------------------------------------------------------------ views

    def status(self) -> dict:
        counts = {r["status"]: r["n"] for r in self.db.execute("SELECT status, COUNT(*) n FROM emails GROUP BY status")}
        oldest = self.db.execute("SELECT MIN(received_at) t FROM emails WHERE status IN ('queued','retry','working')").fetchone()["t"]
        cost = self.db.execute("SELECT COALESCE(SUM(cost),0) c, COALESCE(SUM(calls),0) k FROM emails").fetchone()
        review = self.db.execute("SELECT COUNT(*) n FROM emails WHERE status IN ('done','dead') "
                                 "AND json_extract(record,'$.needs_review')").fetchone()["n"]
        dead = [r["id"] for r in self.db.execute("SELECT id FROM emails WHERE status='dead' ORDER BY seq")]
        redelivered = self.db.execute("SELECT COUNT(*) n FROM events WHERE event='redelivered'").fetchone()["n"]
        return {"by_status": counts, "oldest_waiting_seconds": round(self.clock() - oldest) if oldest else 0,
                "needs_review": review, "dead_letter": dead, "redeliveries_ignored": redelivered,
                "model_calls": cost["k"], "cost_usd": round(cost["c"], 5)}

    def export(self, out_path: Path) -> int:
        recs = {r["id"]: json.loads(r["record"]) for r in self.db.execute(
            "SELECT id, record FROM emails WHERE status IN ('done','dead') ORDER BY id")}
        out_path.write_text(json.dumps(recs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return len(recs)

    def serve(self, once: bool = False, poll: float = 5.0) -> None:
        while True:
            got, done = self.intake(), self.work()
            if got["queued"] or got["redelivered"] or any(done.values()):
                print(f"intake {got} | processed {done} | {self.status()['by_status']}", flush=True)
            if once:
                return
            time.sleep(poll)
