"""Local results viewer: walk one email through the system.

    python -m rfq.viewer --emails data/dev/emails --parsed parsed.json [--key data/dev/answer_key.json]

Opens http://127.0.0.1:8765. Standard library only, binds to localhost, makes no outbound calls.
Shows each email exactly as the model saw it (body, attachments as text, pictures, unreadable
notes), the lines extracted and where each came from (click a line to find it in the source),
the model's stated reason, what the code guards changed, tokens and cost, and -- with --key --
how the result compares to the answer key.

Email content is untrusted (anyone can mail the inbox): everything is rendered as escaped text,
never as the email's own HTML.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from .guards import _clean_line
from .ingest import STRIKE_L, STRIKE_R, Email, load_email
from .llm import cost_usd
from .prompt import REQUEST_CLASSES
from .score import mentions, pair

E = html.escape


class State:
    def __init__(self, emails: Path, parsed: Path, key: Path | None):
        self.dir = emails
        self.parsed = json.loads(parsed.read_text(encoding="utf-8"))
        rp = parsed.with_name(parsed.stem + ".report.json")
        self.report = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {"emails": {}}
        self.key = json.loads(key.read_text(encoding="utf-8"))["emails"] if key else None
        self._emails: dict[str, Email] = {}
        self._lock = threading.Lock()

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
            f"<td>{'<span class=warn>⚑ review</span>' if o and o['needs_review'] else ''}</td><td class=mut>{links}</td>"
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
            kk, ln, probs = k["line_items"][j], lines[i], []
            if kk.get("qty_tol") is not None and ln["qty"] != kk["qty"]:
                probs.append(f"qty key {kk['qty']}")
            if (ln["uom"] or "EA") != (kk["uom"] or "EA"):
                probs.append(f"uom key {kk['uom']}")
            if ln["source"] != kk["source"]:
                probs.append(f"source key {kk['source']}")
            probs += [f"missing '{m}'" for m in kk.get("must_mention") or [] if not mentions(ln["description"], m)]
            probs += [f"must not say '{m}'" for m in kk.get("must_not_mention") or [] if mentions(ln["description"], m)]
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
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    st = State(a.emails, a.parsed, a.key)
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
