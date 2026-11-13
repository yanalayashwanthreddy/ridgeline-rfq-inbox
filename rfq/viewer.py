"""Local results viewer: walk one email through the system.

    python -m rfq.viewer --emails data/dev/emails --parsed parsed.json [--key data/dev/answer_key.json]

Opens http://127.0.0.1:8765. Standard library only, binds to localhost, makes no outbound calls.
Shows each email exactly as the model saw it (body, attachments as text, pictures, unreadable
notes), the lines extracted and where each came from (click a line to find it in the source),
the model's stated reason, what the code guards changed, tokens and cost, and -- with --key --
how the result compares to the answer key.

Catalog matches: each line's part (or not-in-catalog / ambiguous) with its reason, and a rep can
accept it or overturn it. Decisions go to <parsed>.rep_decisions.json, never into parsed.json:
the system's answer and the person's correction stay separate, so disagreements can be counted.

Email content is untrusted (anyone can mail the inbox): everything is rendered as escaped text,
never as the email's own HTML. The one write (POST /decide) needs a per-run token that only
pages served by this process carry, so another site open in the browser cannot post to it.
"""

from __future__ import annotations

import argparse
import hmac
import html
import json
import os
import re
import secrets
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote

from .config import ROOT
from .guards import _clean_line
from .ingest import STRIKE_L, STRIKE_R, Email, load_email
from .llm import cost_usd
from .matcher import alternatives, load_catalog
from .prompt import REQUEST_CLASSES
from .score import line_problems, pair

E = html.escape


class State:
    def __init__(self, emails: Path, parsed: Path, key: Path | None, catalog: Path | None = None):
        self.dir = emails
        self.parsed = json.loads(parsed.read_text(encoding="utf-8"))
        rp = parsed.with_name(parsed.stem + ".report.json")
        self.report = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {"emails": {}}
        self.key = json.loads(key.read_text(encoding="utf-8"))["emails"] if key else None
        self.catalog = load_catalog(catalog) if catalog and catalog.is_file() else []
        self.parts = {p.sku: p for p in self.catalog}
        self.decisions_path = parsed.with_name(parsed.stem + ".rep_decisions.json")
        self.decisions: dict = (json.loads(self.decisions_path.read_text(encoding="utf-8"))
                                if self.decisions_path.exists() else {})
        self.token = secrets.token_urlsafe(24)
        self._emails: dict[str, Email] = {}
        self._lock = threading.Lock()

    def decide(self, eid: str, idx: int, action: str, sku: str, note: str) -> None:
        ln = self.parsed[eid]["line_items"][idx]
        if action == "accept":
            rec = {"action": "accept", "match": ln.get("match"), "sku": ln.get("sku")}
        elif action == "not_in_catalog":
            rec = {"action": "overturn", "match": "not_in_catalog", "sku": None}
        elif action == "sku" and sku in self.parts:
            rec = {"action": "overturn" if sku != ln.get("sku") else "accept", "match": "matched", "sku": sku}
        elif action == "clear":
            rec = None
        else:
            raise ValueError("unknown SKU" if action == "sku" else "unknown action")
        with self._lock:
            lines = self.decisions.setdefault(eid, {})
            if rec is None:
                lines.pop(str(idx), None)
            else:
                rec.update(system={"match": ln.get("match"), "sku": ln.get("sku")}, note=note[:300],
                           at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                lines[str(idx)] = rec
            tmp = self.decisions_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.decisions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
            os.replace(tmp, self.decisions_path)

    def email(self, eid: str) -> Email:
        with self._lock:
            if eid not in self._emails:
                self._emails[eid] = load_email(self.dir / f"{eid}.eml")
            return self._emails[eid]

    def ids(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.eml"))


# ----------------------------------------------------------------------------- rendering helpers

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6a66;--line:#e4e1da;--card:#fff;--acc:#2f5d8a;--hl:#fff1a8;
--ok:#1f7a45;--bad:#b3261e;--warn:#9a6700;--chip:#efece6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#171716;--fg:#e9e7e2;--mut:#9d9a93;
--line:#2f2e2b;--card:#1f1f1d;--acc:#8fb7e0;--hl:#5a4b00;--ok:#6fcf97;--bad:#f28b82;--warn:#e3b341;--chip:#2a2927}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
header h1{font-size:16px;margin:0}main{padding:16px 20px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.03em}
.chip{display:inline-block;padding:1px 8px;border-radius:10px;background:var(--chip);font-size:12px;white-space:nowrap}
.rfq,.rfq_revision,.purchase_order{background:#d9e8f6;color:#123}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .rfq,:root:not([data-theme=light]) .rfq_revision,:root:not([data-theme=light]) .purchase_order{background:#24384d;color:#dbe9f7}}
.mut{color:var(--mut)}.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);gap:16px}
@media (max-width:900px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:14px}
.card h3{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.03em;color:var(--mut)}
pre{white-space:pre-wrap;word-break:break-word;margin:0;font:12.5px/1.5 ui-monospace,Consolas,monospace}
.src{border-radius:3px;cursor:pointer;background:color-mix(in srgb,var(--hl) 35%,transparent)}
.src.on{background:var(--hl);outline:2px solid var(--warn)}
tr.ln{cursor:pointer}tr.ln.on{background:color-mix(in srgb,var(--hl) 40%,transparent)}
.tag{font-size:11px;padding:0 5px;border-radius:4px;background:var(--bad);color:#fff}
img.att{max-width:100%;border:1px solid var(--line);border-radius:6px;margin-top:6px}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 12px;margin:0}dt{color:var(--mut)}dd{margin:0}
ul{margin:4px 0;padding-left:18px}s{color:var(--bad)}
.m{border-top:1px solid var(--line);padding:8px 0}.m:first-of-type{border-top:0}
.matched{background:#dcefe2;color:#14361f}.not_in_catalog{background:#f6dcda;color:#4a120e}.ambiguous{background:#f5e9c8;color:#3d2c00}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .matched{background:#1f3b2a;color:#c9ecd5}
:root:not([data-theme=light]) .not_in_catalog{background:#45211e;color:#f6d3cf}:root:not([data-theme=light]) .ambiguous{background:#3d3218;color:#f3e2b3}}
form.dec{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:6px}
form.dec select,form.dec input,form.dec button{font:inherit;font-size:12.5px;padding:3px 6px;border:1px solid var(--line);border-radius:5px;background:var(--card);color:var(--fg);max-width:100%}
form.dec button{cursor:pointer}form.dec button.pri{background:var(--acc);color:var(--card);border-color:var(--acc)}
"""

JS = """
function sel(i){document.querySelectorAll('.on').forEach(e=>e.classList.remove('on'));
 document.querySelectorAll('[data-l="'+i+'"]').forEach(e=>e.classList.add('on'));
 const s=document.querySelector('.src[data-l="'+i+'"]'); if(s) s.scrollIntoView({block:'center',behavior:'smooth'});}
document.addEventListener('click',e=>{const t=e.target.closest('[data-l]'); if(t) sel(t.dataset.l);});
"""


def page(title: str, body: str) -> bytes:
    return (
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{E(title)}</title>"
        f"<style>{CSS}</style></head><body>{body}<script>{JS}</script></body></html>"
    ).encode("utf-8")


def strike(s: str) -> str:
    """Escape, then show our ~~struck~~ markers as <s>."""
    out = E(s)
    return re.sub(re.escape(E(STRIKE_L)) + r"(.*?)" + re.escape(E(STRIKE_R)), r"<s>\1</s>", out)


def marked_text(text: str, items: list[tuple[int, str]]) -> str:
    """Render source text with each extracted line's raw highlighted where it occurs."""
    out = []
    pending = list(items)
    for line in text.splitlines():
        clean = _clean_line(line)
        hit = next(((i, r) for i, r in pending if r == clean), None)
        if hit:
            pending.remove(hit)
            out.append(f'<span class=src data-l="{hit[0]}">{strike(line)}</span>')
            continue
        # a clause inside a longer line (prose, or a line-item in a sentence)
        segs, rest = [], line
        for i, r in list(pending):
            k = rest.find(r)
            if r and k >= 0:
                segs.append(strike(rest[:k]) + f'<span class=src data-l="{i}">{strike(r)}</span>')
                rest = rest[k + len(r):]
                pending.remove((i, r))
        out.append("".join(segs) + strike(rest))
    return "\n".join(out)


def chip(cls: str | None) -> str:
    return f"<span class='chip {E(cls or '')}'>{E(cls or '?')}</span>"


def action_chip(o: dict | None) -> str:
    if not o or not o.get("attention"):
        return ""
    return f"<span class=warn title='{E(o['attention'])}'>➜ action</span>"


def money(x) -> str:
    return f"${x:.4f}" if isinstance(x, (int, float)) else "–"


# ----------------------------------------------------------------------------- pages


def index(st: State) -> bytes:
    rows = []
    tot = {"cls": 0, "n": 0, "paired": 0, "klines": 0, "olines": 0}
    for eid in st.ids():
        o = st.parsed.get(eid)
        em = st.email(eid)
        s = st.report["emails"].get(eid, {})
        k = st.key.get(eid) if st.key else None
        verdict = ""
        if k and o:
            pairs, extra, missed = pair(o["line_items"], k.get("line_items") or [])
            ok_cls = k["classification"] == o["classification"]
            tot["n"] += 1
            tot["cls"] += ok_cls
            tot["paired"] += len(pairs)
            tot["klines"] += len(k.get("line_items") or [])
            tot["olines"] += len(o["line_items"])
            bits = [] if ok_cls else [f"key: {k['classification']}"]
            bits += [f"{len(missed)} missed"] if missed else []
            bits += [f"{len(extra)} extra"] if extra else []
            bits += [f"review key {k.get('needs_review')}"] if bool(k.get("needs_review")) != o["needs_review"] else []
            verdict = "<span class=ok>✓</span>" if not bits else f"<span class=bad>✗ {E(', '.join(bits))}</span>"
        cost = cost_usd(st.report.get("model", ""), s["usage"]) if s.get("usage") else None
        links = " ".join(x for x in [
            f"revises {E(o['revises'])}" if o and o.get("revises") else "",
            f"dup of {E(o['duplicate_of'])}" if o and o.get("duplicate_of") else ""] if x)
        rows.append(
            f"<tr><td><a href='/e/{E(eid)}'>{E(eid)}</a></td><td>{E(_name(em.sender))}</td><td>{E(em.subject)}</td>"
            f"<td>{chip(o and o['classification'])}</td><td>{len(o['line_items']) if o else '–'}</td>"
            f"<td>{'<span class=warn>⚑ review</span>' if o and o['needs_review'] else ''}"
            f"{action_chip(o)}</td><td class=mut>{links}</td>"
            f"<td class=mut>{money(cost)}{' ↻' if s.get('coverage_retry') else ''}{' ⚠ fallback' if s.get('fallback') else ''}</td>"
            + (f"<td>{verdict}</td>" if st.key else "") + "</tr>"
        )
    t = st.report.get("totals", {})
    summary = (
        f"<span class=mut>{len(rows)} emails · model {E(str(st.report.get('model', '?')))} ({E(str(st.report.get('effort', '?')))})"
        f" · inbox cost uncached {money(t.get('cost_usd_if_uncached'))} · ~{t.get('avg_tokens_per_email', '?')} tokens/email</span>"
    )
    if st.key and tot["n"]:
        summary += (f" <span class=mut>· vs key: classes {tot['cls']}/{tot['n']}, lines recall "
                    f"{tot['paired']}/{tot['klines']}, precision {tot['paired']}/{tot['olines']}</span>")
    head = "<th>id</th><th>from</th><th>subject</th><th>class</th><th>lines</th><th></th><th>links</th><th>cost</th>"
    head += "<th>vs key</th>" if st.key else ""
    body = (f"<header><h1>RFQ inbox results</h1>{summary}</header><main><table><tr>{head}</tr>"
            f"{''.join(rows)}</table><p class=mut>↻ = code found an unaccounted-for table row and gave the model a second look.</p></main>")
    return page("RFQ inbox results", body)


def _name(sender: str) -> str:
    return re.sub(r"\s*<.*>", "", sender or "").strip('" ') or sender


def detail(st: State, eid: str) -> bytes:
    em = st.email(eid)
    o = st.parsed.get(eid) or {"classification": None, "line_items": [], "needs_review": False}
    s = st.report["emails"].get(eid, {})
    k = st.key.get(eid) if st.key else None
    lines = o["line_items"]

    by_src: dict[str, list[tuple[int, str]]] = {}
    for i, ln in enumerate(lines):
        by_src.setdefault(ln["source"], []).append((i, ln["raw"]))

    # ---- left: the email as the model saw it
    hdr = "".join(f"<dt>{E(n)}</dt><dd>{E(v)}</dd>" for n, v in [
        ("From", em.sender), ("To", em.to), ("Cc", em.cc), ("Date", em.date), ("Subject", em.subject),
        ("In-Reply-To", em.in_reply_to or ""), ("Auto-Submitted", em.auto_submitted), ("Precedence", em.precedence)] if v)
    left = [f"<div class=card><h3>Headers</h3><dl>{hdr}</dl></div>",
            f"<div class=card><h3>Body <span class=mut>(read from {E(em.body_source)})</span></h3>"
            f"<pre>{marked_text(em.body, by_src.get('body', []))}</pre></div>"]
    for pi, p in enumerate(em.parts):
        notes = "".join(f"<li>{E(n)}</li>" for n in p.notes)
        bad = " <span class=tag>unreadable</span>" if p.kind == "unreadable" else ""
        imgs = "".join(
            f"<div class=mut>{E(im.label)}</div><img class=att alt='{E(im.label)}' src='/img/{E(eid)}/{pi}/{n}'>"
            for n, im in enumerate(p.images))
        from_pic = by_src.get(p.filename, []) if p.images else []
        pic_lines = "".join(f"<li class=src data-l='{i}'>{E(r)}</li>" for i, r in from_pic)
        text = f"<pre>{marked_text(p.text, by_src.get(p.filename, []))}</pre>" if p.text else ""
        left.append(
            f"<div class=card><h3>Attachment {pi + 1}: {E(p.filename)}{bad}</h3>"
            f"<div class=mut>{E(p.mime)} · {p.size:,} bytes · {E(p.kind)}{' · inline' if p.inline else ''}</div>"
            f"{'<ul>' + notes + '</ul>' if notes else ''}{text}{imgs}"
            f"{'<div class=mut>read from this picture:</div><ul>' + pic_lines + '</ul>' if pic_lines else ''}</div>")
    if em.errors:
        left.append("<div class=card><h3>Ingest errors</h3><ul>" + "".join(f"<li>{E(x)}</li>" for x in em.errors) + "</ul></div>")

    # ---- right: the result
    verdict = {}
    missed_html = ""
    if k:
        pairs, extra, missed = pair(lines, k.get("line_items") or [])
        for i, j in pairs:
            probs = [text for _, text in line_problems(lines[i], k["line_items"][j])]
            verdict[i] = "<span class=ok>✓</span>" if not probs else f"<span class=bad>{E('; '.join(probs))}</span>"
        for i in extra:
            verdict[i] = "<span class=bad>extra (not in key)</span>"
        if missed:
            missed_html = ("<div class=card><h3 class=bad>In the key, not extracted</h3><ul>" +
                           "".join(f"<li><code>{E(k['line_items'][j]['raw'])}</code> × {E(str(k['line_items'][j]['qty']))}</li>" for j in missed) + "</ul></div>")

    rows = "".join(
        f"<tr class=ln data-l='{i}'><td class=mut>{i + 1}</td><td><code>{strike(ln['raw'])}</code></td><td>{E(ln['description'])}</td>"
        f"<td>{E(str(ln['qty']))}</td><td>{E(str(ln['uom'] or ''))}</td><td class=mut>{E(ln['source'])}</td>"
        + (f"<td>{verdict.get(i, '')}</td>" if k else "") + "</tr>"
        for i, ln in enumerate(lines))
    kcls = ""
    if k:
        same = k["classification"] == o["classification"]
        kcls = f" <span class={'ok' if same else 'bad'}>{'✓ matches key' if same else '✗ key says ' + E(k['classification'])}</span>"
    facts = [("classification", chip(o["classification"]) + kcls),
             ("model's reason", E(s.get("model_reason") or "–"))]
    if o.get("revises"):
        facts.append(("revises", f"<a href='/e/{E(o['revises'])}'>{E(o['revises'])}</a>"))
    if o.get("duplicate_of"):
        facts.append(("duplicate of", f"<a href='/e/{E(o['duplicate_of'])}'>{E(o['duplicate_of'])}</a> (later Date → lines cleared)"))
    if o["needs_review"]:
        facts.append(("needs review", f"<span class=warn>⚑ {E(o.get('review_reason') or '')}</span>"))
    elif o["classification"] in REQUEST_CLASSES:
        facts.append(("needs review", "no"))
    if s.get("fallback"):
        facts.append(("fallback", f"<span class=bad>{E(s['fallback'])}</span>"))
    if o.get("attention"):
        facts.append(("needs a person", f"<span class=warn>➜ {E(o['attention'])}</span>"))
    u = s.get("usage")
    if u:
        facts.append(("cost", f"{money(cost_usd(st.report.get('model', ''), u))} · {u['input_tokens']:,} in / {u['output_tokens']:,} out "
                              f"({u['reasoning_tokens']:,} reasoning) · {s.get('model_calls', 0)} new call(s) this run"
                              f"{' · replayed from cache' if s.get('cached') else ''}"))
    right = [f"<div class=card><h3>Decision</h3><dl>{''.join(f'<dt>{a}</dt><dd>{b}</dd>' for a, b in facts)}</dl></div>"]
    th = "<th>#</th><th>raw</th><th>description</th><th>qty</th><th>uom</th><th>source</th>" + ("<th>vs key</th>" if k else "")
    right.append(f"<div class=card><h3>Line items ({len(lines)}) <span class=mut>— click a line to find it in the source</span></h3>"
                 + (f"<table><tr>{th}</tr>{rows}</table>" if lines else "<p class=mut>none</p>") + "</div>")
    right.append(missed_html)
    if any("match" in ln for ln in lines):
        right.append(catalog_card(st, eid, lines, k))
    if s.get("coverage_retry"):
        right.append(f"<div class=card><h3>Coverage check fired</h3><p class=mut>Code found numbered rows no line accounted "
                     f"for and asked the model once more:</p><pre>{E(s['coverage_retry'])}</pre></div>")
    if s.get("warnings"):
        right.append("<div class=card><h3>Guard notes</h3><ul>" + "".join(f"<li>{E(w)}</li>" for w in s["warnings"]) + "</ul></div>")

    ids = st.ids()
    n = ids.index(eid)
    nav = (f"<a href='/'>← inbox</a> {'<a href=/e/' + E(ids[n - 1]) + '>‹ ' + E(ids[n - 1]) + '</a>' if n else ''} "
           f"{'<a href=/e/' + E(ids[n + 1]) + '>' + E(ids[n + 1]) + ' ›</a>' if n + 1 < len(ids) else ''}")
    body = (f"<header><h1>{E(eid)} · {E(em.subject)}</h1>{nav}</header><main><div class=grid>"
            f"<section>{''.join(left)}</section><section>{''.join(right)}</section></div></main>")
    return page(f"{eid} · RFQ results", body)


def catalog_card(st: State, eid: str, lines: list[dict], k: dict | None) -> str:
    """Each line's catalog decision, its reason, the key's answer, and accept / overturn."""
    key_by_line = {}
    if k:
        pairs, _, _ = pair(lines, k.get("line_items") or [])
        key_by_line = {i: k["line_items"][j] for i, j in pairs}
    out = []
    for i, ln in enumerate(lines):
        if "match" not in ln:
            continue
        part = st.parts.get(ln.get("sku") or "")
        what = f"<b>{E(ln['sku'])}</b> {E(part.description) if part else ''}" if ln.get("sku") else ""
        kk = key_by_line.get(i)
        vs = ""
        if kk and kk.get("match"):
            same = kk["match"] == ln["match"] and kk.get("sku") == ln.get("sku")
            vs = (" <span class=ok>✓ key agrees</span>" if same else
                  f" <span class=bad>✗ key: {E(kk['match'])} {E(kk.get('sku') or '')}</span>")
        d = st.decisions.get(eid, {}).get(str(i))
        dec = ""
        if d:
            verb = "accepted" if d["action"] == "accept" else "overturned to"
            dec = (f"<div class={'ok' if d['action'] == 'accept' else 'warn'}>Rep {verb} "
                   f"{E(d['match'])} {E(d.get('sku') or '')}{' — ' + E(d['note']) if d.get('note') else ''} "
                   f"<span class=mut>{E(d['at'])}</span></div>")
        # an ambiguous line with a suggestion: pre-select it, so confirming is one click
        pick = ln.get("sku") or ((ln.get("candidates") or [None])[0] if "likely" in (ln.get("match_reason") or "") else None)
        opts = "".join(
            f"<option value='{E(p.sku)}'{' selected' if p.sku == pick else ''}>{E(p.sku)} · {E(p.description)}</option>"
            for p in alternatives(ln["description"], st.catalog))
        hidden = f"<input type=hidden name=t value='{E(st.token)}'><input type=hidden name=e value='{E(eid)}'><input type=hidden name=i value='{i}'>"
        form = (f"<form class=dec method=post action=/decide>{hidden}"
                f"<button class=pri name=a value=accept>Accept</button>"
                f"<span class=mut>or</span><select name=s aria-label='pick a stocked part'><option value=''>pick a stocked part…</option>{opts}</select>"
                f"<input name=sku placeholder='or type a SKU' size=10 aria-label='SKU'>"
                f"<button name=a value=sku>Use this part</button>"
                f"<button name=a value=not_in_catalog>Not in catalog</button>"
                f"<input name=note placeholder='note (optional)' size=14 aria-label='note'>"
                + ("<button name=a value=clear>Undo</button>" if d else "") + "</form>")
        out.append(
            f"<div class=m><div><span class=mut>{i + 1}.</span> {E(ln['description'])}</div>"
            f"<div><span class='chip {E(ln['match'])}'>{E(ln['match'].replace('_', ' '))}</span> {what}{vs}</div>"
            f"<div class=mut>{E(ln.get('match_reason') or '')}</div>{dec}{form}</div>")
    n_dec = len(st.decisions.get(eid, {}))
    return (f"<div class=card><h3>Catalog matches <span class=mut>— code decides; a rep accepts or overturns "
            f"({n_dec} decided)</span></h3>{''.join(out)}</div>")


# ----------------------------------------------------------------------------- server


def make_handler(st: State):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            path = unquote(self.path.split("?")[0])
            try:
                if path == "/":
                    return self._send(200, index(st))
                m = re.fullmatch(r"/e/([\w.-]+)", path)
                if m and m.group(1) in st.ids():
                    return self._send(200, detail(st, m.group(1)))
                m = re.fullmatch(r"/img/([\w.-]+)/(\d+)/(\d+)", path)
                if m and m.group(1) in st.ids():
                    im = st.email(m.group(1)).parts[int(m.group(2))].images[int(m.group(3))]
                    return self._send(200, im.data, im.mime)
                return self._send(404, page("not found", "<main>not found</main>"))
            except Exception as e:  # show the error, keep serving
                return self._send(500, page("error", f"<main><pre>{E(type(e).__name__)}: {E(str(e))}</pre></main>"))

        def do_POST(self):
            if unquote(self.path) != "/decide":
                return self._send(404, page("not found", "<main>not found</main>"))
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 10_000:
                    return self._send(413, page("too large", "<main>request too large</main>"))
                f = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8")).items()}
                if not hmac.compare_digest(f.get("t", ""), st.token):
                    return self._send(403, page("forbidden", "<main>stale or foreign page: reload and try again</main>"))
                eid, idx = f.get("e", ""), int(f.get("i", "-1"))
                if eid not in st.parsed or not 0 <= idx < len(st.parsed[eid]["line_items"]):
                    return self._send(400, page("bad request", "<main>unknown email or line</main>"))
                sku = (f.get("sku") or f.get("s") or "").strip().upper()
                st.decide(eid, idx, f.get("a", ""), sku, f.get("note", "").strip())
                self.send_response(303)
                self.send_header("Location", f"/e/{eid}")
                self.send_header("Content-Length", "0")
                self.end_headers()
            except ValueError as e:
                return self._send(400, page("bad request", f"<main>{E(str(e))} — <a href='/'>back to the inbox</a></main>"))

        def _send(self, code, data: bytes, ctype="text/html; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    return H


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="rfq.viewer")
    ap.add_argument("--emails", type=Path, required=True)
    ap.add_argument("--parsed", type=Path, required=True)
    ap.add_argument("--key", type=Path)
    ap.add_argument("--catalog", type=Path, default=ROOT / "data" / "catalog.csv")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    st = State(a.emails, a.parsed, a.key, a.catalog)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(st))
    url = f"http://127.0.0.1:{a.port}/"
    print(f"viewer at {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
