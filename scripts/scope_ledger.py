#!/usr/bin/env python3
"""
scope_ledger.py - a per-client record of agreed scope, change requests and money, for ScopeGuard.

Commands
  init CLIENT --rate 50 [--unit hour|day] [--currency USD] [--price 500] [--revisions 2] [--project NAME]
  baseline CLIENT --file baseline.json      save the agreed scope Claude extracted from the agreement
  log CLIENT --request TEXT --verdict included|grey|extra|revision [--clause TEXT]
             [--hours N | --days N | --fixed AMOUNT] [--buffer PCT] [--status pending] [--note TEXT]
  update CLIENT ID --status pending|approved|free|declined|done [--hours N | --days N | --fixed AMOUNT]
  round CLIENT                              record one more revision round and compare it with the cap
  report CLIENT [--out FILE.md]             totals: approved, waiting, given away, new project total
  list                                      one line per client
  quote --rate 50 [--unit hour] (--hours N | --days N) [--buffer PCT] [--currency USD]

Data lives in ./scopeguard-data/<client>.json (or --data-dir). Nothing is sent anywhere.
Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

VERDICTS = ("included", "grey", "extra", "revision")
STATUSES = ("pending", "approved", "free", "declined", "done")
UNITS = ("hour", "day")
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥", "AUD": "A$", "CAD": "C$"}


def out(s: str = "") -> None:
    print(s, flush=True)


def money(value: Decimal, currency: str) -> str:
    q = value.quantize(Decimal("1") if currency == "JPY" else Decimal("0.01"), rounding=ROUND_HALF_UP)
    sym = SYMBOLS.get(currency.upper())
    text = f"{q:,}"
    if text.endswith(".00"):
        text = text[:-3]
    return f"{sym}{text}" if sym else f"{text} {currency.upper()}"


def dec(value, name: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        sys.exit(f"{name} must be a number, got '{value}'.")
    if d < 0:
        sys.exit(f"{name} can't be negative.")
    return d


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not s:
        sys.exit("Client name needs at least one letter or number.")
    return s


def now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


class Store:
    def __init__(self, data_dir: str | None):
        self.dir = Path(data_dir) if data_dir else Path.cwd() / "scopeguard-data"

    def path(self, client: str) -> Path:
        return self.dir / f"{slug(client)}.json"

    def load(self, client: str) -> dict:
        p = self.path(client)
        if not p.is_file():
            sys.exit(f"No record for '{client}' yet. Start one with:  init \"{client}\" --rate YOUR_RATE")
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, led: dict) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        led["updated"] = now()
        p = self.path(led["client"])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(led, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        return p


def cost_of(led: dict, hours=None, days=None, fixed=None, buffer=None) -> tuple[Decimal | None, str]:
    """Return (cost, how) for a request. Hours and days convert using hours_per_day."""
    if fixed is not None:
        return dec(fixed, "--fixed"), "fixed price"
    if hours is None and days is None:
        return None, "not priced"
    rate = Decimal(led["rate"])
    hpd = Decimal(str(led.get("hours_per_day", 8)))
    unit = led["rate_unit"]
    if hours is not None:
        qty = dec(hours, "--hours")
        units = qty if unit == "hour" else qty / hpd
        how = f"{qty}h"
    else:
        qty = dec(days, "--days")
        units = qty if unit == "day" else qty * hpd
        how = f"{qty} day(s)"
    cost = units * rate
    if buffer:
        b = dec(buffer, "--buffer")
        cost = cost * (Decimal(1) + b / Decimal(100))
        how += f" + {b}% buffer"
    return cost, f"{how} at {money(rate, led['currency'])}/{unit}"


def totals(led: dict) -> dict:
    """Money and counts for extra/grey items. Unanswered grey items are kept apart from quoted extras."""
    keys = STATUSES + ("grey_open",)
    t = {s: Decimal(0) for s in keys}
    counts = {s: 0 for s in keys}
    unpriced = 0
    for r in led["requests"]:
        if r["verdict"] not in ("extra", "grey"):
            continue
        key = "grey_open" if r["verdict"] == "grey" and r["status"] == "pending" else r["status"]
        if key == "done":
            key = "approved"
        counts[key] += 1
        if r.get("cost") is None:
            unpriced += 1
        else:
            t[key] += Decimal(r["cost"])
    return {"money": t, "counts": counts, "unpriced": unpriced}


# --------------------------------------------------------------------------- commands

def cmd_init(a, st: Store) -> int:
    p = st.path(a.client)
    if p.exists() and not a.force:
        sys.exit(f"'{a.client}' already has a record ({p}). Use --force to start over.")
    if a.unit not in UNITS:
        sys.exit("--unit must be hour or day.")
    led = {
        "client": a.client, "project": a.project or "", "currency": a.currency.upper(),
        "rate": str(dec(a.rate, "--rate")), "rate_unit": a.unit, "hours_per_day": a.hours_per_day,
        "original_price": str(dec(a.price, "--price")) if a.price is not None else None,
        "revisions_included": a.revisions, "revisions_used": 0,
        "baseline": {}, "requests": [], "created": now(),
    }
    path = st.save(led)
    out(f"Started a record for {a.client} at {path}")
    out(f"Rate: {money(Decimal(led['rate']), led['currency'])}/{a.unit}"
        + (f" | Price: {money(Decimal(led['original_price']), led['currency'])}" if led["original_price"] else "")
        + (f" | Revision rounds included: {a.revisions}" if a.revisions is not None else ""))
    return 0


BASELINE_KEYS = ("deliverables", "exclusions", "revision_definition", "deadline", "payment_terms",
                 "client_responsibilities", "approval_gates", "silent_on", "source", "notes")


def cmd_baseline(a, st: Store) -> int:
    led = st.load(a.client)
    try:
        data = json.loads(Path(a.file).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        sys.exit(f"File not found: {a.file}")
    except json.JSONDecodeError as e:
        sys.exit(f"{a.file} is not valid JSON (line {e.lineno}): {e.msg}")
    unknown = [k for k in data if k not in BASELINE_KEYS + ("revisions_included", "original_price")]
    if unknown:
        out(f"Note: ignored unknown keys {unknown}")
    led["baseline"].update({k: v for k, v in data.items() if k in BASELINE_KEYS})
    if "revisions_included" in data:
        led["revisions_included"] = data["revisions_included"]
    if data.get("original_price") is not None:
        led["original_price"] = str(dec(data["original_price"], "original_price"))
    st.save(led)
    b = led["baseline"]
    out(f"Saved the agreed scope for {led['client']}: {len(b.get('deliverables', []))} deliverable(s), "
        f"{len(b.get('exclusions', []))} exclusion(s), revision rounds: {led['revisions_included']}")
    return 0


def cmd_log(a, st: Store) -> int:
    led = st.load(a.client)
    if a.verdict not in VERDICTS:
        sys.exit(f"--verdict must be one of {', '.join(VERDICTS)}.")
    if a.status not in STATUSES:
        sys.exit(f"--status must be one of {', '.join(STATUSES)}.")
    cost, how = cost_of(led, a.hours, a.days, a.fixed, a.buffer)
    if a.verdict in ("included", "revision") and cost is not None:
        out("Note: included/revision items are part of the agreed price, so no cost is recorded.")
        cost, how = None, "covered by the agreement"
    rid = max((r["id"] for r in led["requests"]), default=0) + 1
    led["requests"].append({
        "id": rid, "date": now(), "request": a.request, "verdict": a.verdict,
        "clause": a.clause or "", "cost": str(cost) if cost is not None else None, "priced_as": how,
        "status": a.status, "note": a.note or "",
    })
    if a.verdict == "revision":
        led["requests"][-1]["round"] = max(int(led.get("revisions_used", 0)), 1)
    st.save(led)
    label = {"included": "INCLUDED", "grey": "GREY AREA", "extra": "EXTRA", "revision": "REVISION"}[a.verdict]
    out(f"#{rid} {label}: {a.request}" + (f"  -> {money(cost, led['currency'])} ({how})" if cost is not None else ""))
    return 0


def cmd_update(a, st: Store) -> int:
    led = st.load(a.client)
    if a.status and a.status not in STATUSES:
        sys.exit(f"--status must be one of {', '.join(STATUSES)}.")
    for r in led["requests"]:
        if r["id"] == a.id:
            if a.status:
                r["status"] = a.status
            if any(v is not None for v in (a.hours, a.days, a.fixed)):
                cost, how = cost_of(led, a.hours, a.days, a.fixed, a.buffer)
                r["cost"], r["priced_as"] = str(cost), how
            if a.note:
                r["note"] = a.note
            st.save(led)
            c = f" {money(Decimal(r['cost']), led['currency'])}" if r.get("cost") else ""
            out(f"#{r['id']} is now {r['status']}{c}: {r['request']}")
            return 0
    sys.exit(f"No request #{a.id} for {led['client']}.")


def cmd_round(a, st: Store) -> int:
    led = st.load(a.client)
    led["revisions_used"] = int(led.get("revisions_used", 0)) + 1
    st.save(led)
    used, cap = led["revisions_used"], led.get("revisions_included")
    if cap is None:
        out(f"Revision round {used} recorded. The agreement doesn't set a limit, which is worth fixing next time.")
    elif used <= cap:
        out(f"Revision round {used} of {cap} recorded ({cap - used} left).")
    else:
        out(f"Revision round {used} recorded. That's {used - cap} beyond the {cap} included: "
            "treat it as extra work and quote it before starting.")
    return 0


def build_report(led: dict) -> str:
    cur = led["currency"]
    t = totals(led)
    m, c = t["money"], t["counts"]
    lines = [f"# Scope report: {led['client']}" + (f" ({led['project']})" if led.get("project") else ""), ""]
    rate = f"{money(Decimal(led['rate']), cur)}/{led['rate_unit']}"
    orig = Decimal(led["original_price"]) if led.get("original_price") else None
    lines.append(f"- Rate: {rate}" + (f" | Original price: {money(orig, cur)}" if orig is not None else ""))
    cap, used = led.get("revisions_included"), led.get("revisions_used", 0)
    lines.append(f"- Revision rounds: {used}" + (f" of {cap} included" if cap is not None else " (no limit agreed)")
                 + (f" - **{used - cap} over the limit**" if cap is not None and used > cap else ""))
    lines += ["", "| Extra work | Items | Value |", "|---|---|---|"]
    for s, label in (("approved", "Approved (billable)"), ("pending", "Quoted, waiting for an answer"),
                     ("grey_open", "Grey areas, asked the client"),
                     ("free", "Done for free"), ("declined", "Declined by the client")):
        lines.append(f"| {label} | {c[s]} | {money(m[s], cur)} |")
    if t["unpriced"]:
        lines.append(f"\n{t['unpriced']} extra or grey item(s) have no price yet.")
    if orig is not None:
        lines.append(f"\n**New project total:** {money(orig + m['approved'], cur)} "
                     f"(original {money(orig, cur)} + approved extras {money(m['approved'], cur)})")
    if m["free"] > 0:
        pct = f" ({(m['free'] / orig * 100).quantize(Decimal('1'))}% of the original price)" if orig else ""
        lines.append(f"\n**Given away so far:** {money(m['free'], cur)}{pct}.")
    lines += ["", "## Change log", "", "| # | Date | Request | Verdict | Status | Value | Based on |",
              "|---|---|---|---|---|---|---|"]
    for r in led["requests"]:
        val = money(Decimal(r["cost"]), cur) if r.get("cost") else "-"
        clause = (r.get("clause") or "-").replace("|", "/")
        if len(clause) > 80:
            clause = clause[:77] + "..."
        lines.append(f"| {r['id']} | {r['date'][:10]} | {r['request'].replace('|', '/')} | "
                     f"{r['verdict'] + (' (round ' + str(r['round']) + ')' if r.get('round') else '')} | "
                     f"{r['status']} | {val} | {clause} |")
    return "\n".join(lines) + "\n"


def cmd_report(a, st: Store) -> int:
    text = build_report(st.load(a.client))
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        out(f"Report saved to {a.out}")
    out(text)
    return 0


def cmd_list(a, st: Store) -> int:
    files = sorted(st.dir.glob("*.json")) if st.dir.is_dir() else []
    if not files:
        out(f"No clients yet in {st.dir}.")
        return 0
    for f in files:
        led = json.loads(f.read_text(encoding="utf-8"))
        t = totals(led)
        cur = led["currency"]
        out(f"{led['client']}: {len(led['requests'])} request(s) | approved {money(t['money']['approved'], cur)}"
            f" | waiting {money(t['money']['pending'], cur)} | free {money(t['money']['free'], cur)}")
    return 0


def cmd_quote(a, st: Store) -> int:
    led = {"rate": str(dec(a.rate, "--rate")), "rate_unit": a.unit, "currency": a.currency.upper(),
           "hours_per_day": a.hours_per_day}
    if a.hours is None and a.days is None:
        sys.exit("Give --hours or --days.")
    cost, how = cost_of(led, a.hours, a.days, None, a.buffer)
    out(f"{money(cost, led['currency'])}  ({how})")
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="ScopeGuard change record: scope, change requests and money per client.")
    ap.add_argument("--data-dir", help="folder for client records (default ./scopeguard-data)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def priced(p):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--hours")
        g.add_argument("--days")
        g.add_argument("--fixed", help="fixed price instead of time x rate")
        p.add_argument("--buffer", help="add a percentage for uncertainty, e.g. 15")

    p = sub.add_parser("init")
    p.add_argument("client")
    p.add_argument("--rate", required=True)
    p.add_argument("--unit", default="hour", choices=UNITS)
    p.add_argument("--currency", default="USD")
    p.add_argument("--price", help="original agreed project price")
    p.add_argument("--revisions", type=int, help="revision rounds included")
    p.add_argument("--project")
    p.add_argument("--hours-per-day", type=float, default=8)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("baseline")
    p.add_argument("client")
    p.add_argument("--file", required=True)
    p = sub.add_parser("log")
    p.add_argument("client")
    p.add_argument("--request", required=True)
    p.add_argument("--verdict", required=True)
    p.add_argument("--clause")
    p.add_argument("--status", default="pending")
    p.add_argument("--note")
    priced(p)
    p = sub.add_parser("update")
    p.add_argument("client")
    p.add_argument("id", type=int)
    p.add_argument("--status")
    p.add_argument("--note")
    priced(p)
    p = sub.add_parser("round")
    p.add_argument("client")
    p = sub.add_parser("report")
    p.add_argument("client")
    p.add_argument("--out")
    sub.add_parser("list")
    p = sub.add_parser("quote")
    p.add_argument("--rate", required=True)
    p.add_argument("--unit", default="hour", choices=UNITS)
    p.add_argument("--currency", default="USD")
    p.add_argument("--hours")
    p.add_argument("--days")
    p.add_argument("--buffer")
    p.add_argument("--hours-per-day", type=float, default=8)

    a = ap.parse_args(argv)
    st = Store(a.data_dir)
    return {"init": cmd_init, "baseline": cmd_baseline, "log": cmd_log, "update": cmd_update,
            "round": cmd_round, "report": cmd_report, "list": cmd_list, "quote": cmd_quote}[a.cmd](a, st)


if __name__ == "__main__":
    sys.exit(main())
