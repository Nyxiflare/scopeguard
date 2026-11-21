#!/usr/bin/env python3
"""
scope_ledger.py - ScopeGuard's record of agreed scope, change requests and money, per client project.

Every command takes CLIENT and an optional --project NAME (default "main"), so one client can have
several independent projects, each with its own agreement, revision rounds and money.

Commands
  init CLIENT --rate 50 [--unit hour|day] [--currency USD] [--price 600] [--revisions 2]
  agreement CLIENT --file scope.json [--note TEXT] [--approved-by NAME] [--evidence TEXT]
        First call stores version 1 (the original agreement). Later calls add an amendment as a new
        version; earlier versions are never changed. ("baseline" is an alias.)
  round CLIENT [--note TEXT]                     record one batch of client feedback (a revision round)
  log CLIENT --request TEXT --verdict included|revision|grey|extra [--clause TEXT]
        [--hours N | --days N | --fixed AMOUNT] [--buffer PCT] [--courtesy] [--allow-over-cap] [--note TEXT]
  update CLIENT ID [--quote draft|sent|accepted|rejected|expired] [--work not_started|in_progress|done]
        [--invoice not_invoiced|invoiced|partially_paid|paid|overdue] [--courtesy | --no-courtesy]
        [--approved-by NAME] [--evidence TEXT] [--hours N | --days N | --fixed AMOUNT] [--note TEXT]
  change-order CLIENT ID [ID ...] [--timeline TEXT] [--out FILE.md]
  report CLIENT [--out FILE.md]
  list
  quote --rate 50 (--hours N | --days N) [--unit hour|day] [--buffer PCT] [--currency USD]

Records live in ./scopeguard-data/<client>/<project>.json (or --data-dir), with a .bak copy of the
previous state. The script makes no network requests. Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

SCHEMA = 2
VERDICTS = ("included", "revision", "grey", "extra")
QUOTE = ("draft", "sent", "accepted", "rejected", "expired", "n/a")
WORK = ("not_started", "in_progress", "done")
INVOICE = ("not_invoiced", "invoiced", "partially_paid", "paid", "overdue", "n/a")
UNITS = ("hour", "day")
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥", "AUD": "A$", "CAD": "C$"}
SCOPE_KEYS = ("deliverables", "exclusions", "revision_definition", "deadline", "payment_terms",
              "client_responsibilities", "approval_gates", "silent_on", "notes")
# v1 single "status" -> v2 tracks (also accepted on the command line for convenience)
OLD_STATUS = {"pending": {"quote": "sent"}, "approved": {"quote": "accepted"},
              "free": {"quote": "n/a", "complimentary": True}, "declined": {"quote": "rejected"},
              "done": {"work": "done"}}


class LedgerError(SystemExit):
    pass


def fail(msg: str):
    raise LedgerError(msg)


def out(s: str = "") -> None:
    print(s, flush=True)


def today() -> str:
    return dt.date.today().isoformat()


def now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def money(value: Decimal, currency: str) -> str:
    q = value.quantize(Decimal("1") if currency == "JPY" else Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{q:,}"
    if text.endswith(".00"):
        text = text[:-3]
    sym = SYMBOLS.get(currency.upper())
    return f"{sym}{text}" if sym else f"{text} {currency.upper()}"


def dec(value, name: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        fail(f"{name} must be a number, got '{value}'.")
    if not d.is_finite() or d < 0:
        fail(f"{name} must be a positive number.")
    return d


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    if not s:
        fail("Names need at least one letter or number.")
    return s


# --------------------------------------------------------------------------- storage

def validate(led: dict, where: str) -> None:
    problems = []
    for key, kind in (("client", str), ("project", str), ("currency", str), ("rate", str),
                      ("rate_unit", str), ("agreement", list), ("rounds", list), ("requests", list)):
        if not isinstance(led.get(key), kind):
            problems.append(f"'{key}' missing or not a {kind.__name__}")
    if led.get("rate_unit") not in UNITS:
        problems.append("rate_unit must be hour or day")
    ids = set()
    for r in led.get("requests", []) if isinstance(led.get("requests"), list) else []:
        if not isinstance(r, dict) or "id" not in r:
            problems.append("a request has no id")
            continue
        if r["id"] in ids:
            problems.append(f"request id {r['id']} appears twice")
        ids.add(r["id"])
        if r.get("verdict") not in VERDICTS:
            problems.append(f"request {r['id']} has an unknown verdict")
    if problems:
        fail(f"{where} doesn't look like a valid ScopeGuard record: " + "; ".join(problems[:5]))


def migrate_v1(old: dict, project: str) -> dict:
    """Convert a version-1 record (single status, overwritten baseline) to schema 2."""
    led = {
        "schema": SCHEMA, "client": old["client"], "project": old.get("project") or project,
        "currency": old.get("currency", "USD"), "rate": old["rate"], "rate_unit": old.get("rate_unit", "hour"),
        "hours_per_day": old.get("hours_per_day", 8), "agreement": [], "rounds": [], "requests": [],
        "change_orders": [], "created": old.get("created", now()), "rev": 0,
    }
    scope = {k: v for k, v in (old.get("baseline") or {}).items() if k in SCOPE_KEYS}
    led["agreement"].append({
        "version": 1, "effective": (old.get("created") or today())[:10], "note": "original (migrated)",
        "source": (old.get("baseline") or {}).get("source", ""), "approved_by": "", "evidence": "",
        "price": old.get("original_price"), "revisions_included": old.get("revisions_included"), "scope": scope,
    })
    for n in range(1, int(old.get("revisions_used", 0)) + 1):
        led["rounds"].append({"round": n, "date": "", "note": "migrated"})
    for r in old.get("requests", []):
        new = {"id": r["id"], "date": r.get("date", ""), "request": r.get("request", ""),
               "verdict": r.get("verdict", "grey"), "clause": r.get("clause", ""), "cost": r.get("cost"),
               "priced_as": r.get("priced_as", ""), "agreement_version": 1, "round": r.get("round"),
               "quote": "draft" if r.get("verdict") in ("extra", "grey") else "n/a", "work": "not_started",
               "invoice": "not_invoiced" if r.get("verdict") in ("extra", "grey") else "n/a",
               "complimentary": False, "approval": {}, "note": r.get("note", "")}
        for k, v in OLD_STATUS.get(r.get("status", ""), {}).items():
            new[k] = v
        if new["complimentary"]:
            new["invoice"] = "n/a"
        led["requests"].append(new)
    return led


class Store:
    def __init__(self, data_dir: str | None):
        self.dir = Path(data_dir) if data_dir else Path.cwd() / "scopeguard-data"

    def path(self, client: str, project: str) -> Path:
        return self.dir / slug(client) / f"{slug(project)}.json"

    def _read(self, p: Path) -> dict:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            bak = p.with_suffix(".json.bak")
            msg = f"{p} is damaged (line {e.lineno}: {e.msg})."
            if bak.is_file():
                msg += f" The previous good copy is {bak}. Restore it with:  restore CLIENT --project NAME"
            fail(msg)

    def load(self, client: str, project: str) -> dict:
        p = self.path(client, project)
        if not p.is_file():
            legacy = self.dir / f"{slug(client)}.json"
            if legacy.is_file() and slug(project) == "main":
                led = migrate_v1(self._read(legacy), project)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(led, indent=2, ensure_ascii=False), encoding="utf-8")
                legacy.rename(legacy.with_suffix(".json.v1-migrated"))
                out(f"Note: moved the old record for {client} to {p} (new format).")
            else:
                others = sorted(x.stem for x in (self.dir / slug(client)).glob("*.json")) \
                    if (self.dir / slug(client)).is_dir() else []
                hint = f" Projects on file: {', '.join(others)}." if others else ""
                fail(f"No record for '{client}' / project '{project}'.{hint} "
                     f"Start one with:  init \"{client}\" --project \"{project}\" --rate YOUR_RATE")
        led = self._read(p)
        if led.get("schema") != SCHEMA:
            led = migrate_v1(led, project)
        validate(led, str(p))
        led["_path"] = str(p)
        led["_loaded_rev"] = led.get("rev", 0)
        return led

    def save(self, led: dict) -> Path:
        p = Path(led.pop("_path", "")) if led.get("_path") else self.path(led["client"], led["project"])
        loaded_rev = led.pop("_loaded_rev", None)
        p.parent.mkdir(parents=True, exist_ok=True)
        with Lock(p):
            if p.is_file():
                current = self._read(p)
                if loaded_rev is not None and current.get("rev", 0) != loaded_rev:
                    fail(f"{p} was changed by another command while this one ran. Nothing was saved; run it again.")
                shutil.copy2(p, p.with_suffix(".json.bak"))
            led["rev"] = int(led.get("rev", 0)) + 1
            led["updated"] = now()
            validate(led, "the new record")
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(led, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
        return p


class Lock:
    """A simple lock file so two commands can't write the same record at once."""

    def __init__(self, p: Path):
        self.lock = p.with_suffix(".json.lock")

    def __enter__(self):
        deadline = time.time() + 5
        while True:
            try:
                fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.lock.stat().st_mtime > 600:  # stale lock from a crash
                        self.lock.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    fail(f"Another ScopeGuard command is writing this record ({self.lock}). Try again in a moment.")
                time.sleep(0.1)

    def __exit__(self, *exc):
        try:
            self.lock.unlink()
        except FileNotFoundError:
            pass


# --------------------------------------------------------------------------- helpers

def current_agreement(led: dict) -> dict:
    if not led["agreement"]:
        return {"version": 0, "price": None, "revisions_included": None, "scope": {}}
    return led["agreement"][-1]


def cap_of(led: dict):
    return current_agreement(led).get("revisions_included")


def cost_of(led: dict, hours=None, days=None, fixed=None, buffer=None):
    if fixed is not None:
        return dec(fixed, "--fixed"), "fixed price"
    if hours is None and days is None:
        return None, "not priced"
    rate = Decimal(led["rate"])
    hpd = Decimal(str(led.get("hours_per_day", 8)))
    unit = led["rate_unit"]
    if hours is not None:
        qty = dec(hours, "--hours")
        units, how = (qty if unit == "hour" else qty / hpd), f"{qty}h"
    else:
        qty = dec(days, "--days")
        units, how = (qty if unit == "day" else qty * hpd), f"{qty} day(s)"
    cost = units * rate
    if buffer:
        b = dec(buffer, "--buffer")
        cost *= Decimal(1) + b / Decimal(100)
        how += f" + {b}% buffer"
    return cost, f"{how} at {money(rate, led['currency'])}/{unit}"


def find(led: dict, rid: int) -> dict:
    for r in led["requests"]:
        if r["id"] == rid:
            return r
    fail(f"No request #{rid} in {led['client']} / {led['project']}.")


def billable(r: dict) -> bool:
    return (r["verdict"] in ("extra", "grey") or r.get("over_cap")) and not r.get("complimentary")


def totals(led: dict) -> dict:
    """Money per bucket for extra/grey work. Each item lands in exactly one quote bucket."""
    buckets = ("accepted", "sent", "draft", "grey_open", "rejected", "expired", "complimentary")
    m = {b: Decimal(0) for b in buckets + ("invoiced", "paid", "to_invoice", "accepted_on_top")}
    c = {b: 0 for b in buckets}
    unpriced = 0
    for r in led["requests"]:
        if r["verdict"] not in ("extra", "grey") and not r.get("complimentary") and not r.get("over_cap"):
            continue
        if r.get("complimentary"):
            b = "complimentary"
        elif r["verdict"] == "grey" and r["quote"] in ("draft", "sent"):
            b = "grey_open"
        else:
            b = r["quote"] if r["quote"] in buckets else "draft"
        c[b] += 1
        cost = Decimal(r["cost"]) if r.get("cost") else None
        if cost is None:
            unpriced += 1
            continue
        m[b] += cost
        if b == "accepted":
            if not r.get("absorbed_in"):
                m["accepted_on_top"] += cost
            if r.get("invoice") == "paid":
                m["paid"] += cost
            elif r.get("invoice") in ("invoiced", "partially_paid", "overdue"):
                m["invoiced"] += cost
            else:
                m["to_invoice"] += cost
    return {"money": m, "counts": c, "unpriced": unpriced}


# --------------------------------------------------------------------------- commands

def cmd_init(a, st: Store) -> int:
    p = st.path(a.client, a.project)
    if p.exists():
        if not a.force:
            fail(f"{a.client} / {a.project} already has a record ({p}). To start over, add --force "
                 "(a backup copy is kept), or use a different --project name.")
        backup = p.with_name(f"{p.stem}.reset-{dt.datetime.now():%Y%m%d-%H%M%S}.json.bak")
        shutil.copy2(p, backup)
        out(f"Backed up the old record to {backup}")
        p.unlink()
    led = {
        "schema": SCHEMA, "client": a.client, "project": a.project, "currency": a.currency.upper(),
        "rate": str(dec(a.rate, "--rate")), "rate_unit": a.unit, "hours_per_day": a.hours_per_day,
        "agreement": [], "rounds": [], "requests": [], "change_orders": [], "created": now(), "rev": 0,
    }
    if a.price is not None or a.revisions is not None:
        led["agreement"].append({"version": 1, "effective": today(), "note": "original", "source": "",
                                 "approved_by": "", "evidence": "",
                                 "price": str(dec(a.price, "--price")) if a.price is not None else None,
                                 "revisions_included": a.revisions, "scope": {}})
    path = st.save(led)
    out(f"Started {a.client} / {a.project} at {path}")
    out(f"Rate: {money(Decimal(led['rate']), led['currency'])}/{a.unit}")
    return 0


def cmd_agreement(a, st: Store) -> int:
    led = st.load(a.client, a.project)
    try:
        data = json.loads(Path(a.file).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        fail(f"File not found: {a.file}")
    except json.JSONDecodeError as e:
        fail(f"{a.file} is not valid JSON (line {e.lineno}): {e.msg}")
    if not isinstance(data, dict):
        fail(f"{a.file} should contain one JSON object.")
    allowed = SCOPE_KEYS + ("revisions_included", "original_price", "price", "source", "effective")
    ignored = [k for k in data if k not in allowed]
    if ignored:
        out(f"Note: ignored unknown keys {ignored}")
    prev = current_agreement(led)
    price = data.get("price", data.get("original_price", prev.get("price")))
    version = {
        "version": prev["version"] + 1, "effective": data.get("effective") or today(),
        "note": a.note or ("original" if prev["version"] == 0 else "amendment"),
        "source": data.get("source", ""), "approved_by": a.approved_by or "", "evidence": a.evidence or "",
        "price": str(dec(price, "price")) if price is not None else None,
        "revisions_included": data.get("revisions_included", prev.get("revisions_included")),
        "scope": {**(prev.get("scope") or {}), **{k: v for k, v in data.items() if k in SCOPE_KEYS}},
        "absorbs": list(a.absorbs or []),
    }
    # The very first agreement can fill in a version 1 that init created with only price/revisions.
    if prev["version"] == 1 and not prev.get("scope") and not led["requests"] and not a.note:
        version["version"] = 1
        version["note"] = "original"
        led["agreement"][-1] = version
    else:
        if version["version"] > 1 and not (a.approved_by or a.evidence):
            out("Note: amendments are stronger with --approved-by and --evidence (who agreed, and where).")
        for rid in version["absorbs"]:
            find(led, rid)["absorbed_in"] = version["version"]
        loose = [r for r in led["requests"] if billable(r) and r["quote"] == "accepted" and not r.get("absorbed_in")]
        if version["version"] > 1 and prev.get("price") != version["price"] and loose and not version["absorbs"]:
            out("Note: the price changed and there are accepted extras (" + ", ".join(f"#{r['id']}" for r in loose)
                + "). If this amendment's price already includes them, run it with --absorbs ID so they aren't "
                  "counted twice in the project total.")
        led["agreement"].append(version)
    st.save(led)
    v = version
    out(f"Saved agreement version {v['version']} ({v['note']}) for {led['client']} / {led['project']}: "
        f"{len(v['scope'].get('deliverables', []))} deliverable(s), {len(v['scope'].get('exclusions', []))} "
        f"exclusion(s), revision rounds: {v['revisions_included']}"
        + (f", price {money(Decimal(v['price']), led['currency'])}" if v["price"] else ""))
    return 0


def cmd_round(a, st: Store) -> int:
    led = st.load(a.client, a.project)
    n = len(led["rounds"]) + 1
    led["rounds"].append({"round": n, "date": today(), "note": a.note or ""})
    st.save(led)
    cap = cap_of(led)
    if cap is None:
        out(f"Revision round {n} recorded. The agreement sets no limit on rounds; worth fixing next time.")
    elif n <= cap:
        out(f"Revision round {n} of {cap} recorded ({cap - n} left).")
    else:
        out(f"Revision round {n} recorded: {n - cap} beyond the {cap} included. Changes in this round are "
            "extra work. Log them with --verdict extra and a price (or --courtesy if the user chooses to give them).")
    return 0


def cmd_log(a, st: Store) -> int:
    led = st.load(a.client, a.project)
    if a.verdict not in VERDICTS:
        fail(f"--verdict must be one of {', '.join(VERDICTS)}.")
    agr = current_agreement(led)
    cost, how = cost_of(led, a.hours, a.days, a.fixed, a.buffer)
    rnd, over = None, False
    if a.verdict == "revision":
        if not led["rounds"]:
            fail("Record the feedback round first:  round CLIENT --project NAME  (one round per batch of feedback).")
        rnd = led["rounds"][-1]["round"]
        cap = agr.get("revisions_included")
        over = cap is not None and rnd > cap
        if over and not (a.courtesy or a.allow_over_cap):
            fail(f"Round {rnd} is beyond the {cap} revision rounds included, so this change is extra work. "
                 "Log it with --verdict extra and a price, or add --courtesy if the user chooses to do it free.")
    covered = a.verdict == "included" or (a.verdict == "revision" and not over)
    if covered and cost is not None and not a.courtesy:
        out("Note: included work and in-limit revisions are covered by the agreed price, so no cost is recorded.")
        cost, how = None, "covered by the agreement"
    is_extra = a.verdict in ("extra", "grey") or over
    rid = max((r["id"] for r in led["requests"]), default=0) + 1
    rec = {
        "id": rid, "date": now(), "request": a.request, "verdict": a.verdict, "clause": a.clause or "",
        "cost": str(cost) if cost is not None else None, "priced_as": how, "agreement_version": agr["version"],
        "round": rnd, "over_cap": over,
        "quote": "n/a" if (a.courtesy or not is_extra) else "draft", "work": "not_started",
        "invoice": "n/a" if (a.courtesy or not is_extra) else "not_invoiced",
        "complimentary": bool(a.courtesy), "approval": {}, "note": a.note or "",
    }
    led["requests"].append(rec)
    st.save(led)
    label = {"included": "INCLUDED", "grey": "GREY AREA", "extra": "EXTRA", "revision": "REVISION"}[a.verdict]
    extra_bits = []
    if rnd:
        extra_bits.append(f"round {rnd}" + (" (over the limit)" if over else ""))
    if a.courtesy:
        extra_bits.append("courtesy, no charge")
    tail = f" [{', '.join(extra_bits)}]" if extra_bits else ""
    out(f"#{rid} {label}: {a.request}{tail}" + (f" -> {money(cost, led['currency'])} ({how})" if cost is not None else ""))
    if not agr["version"]:
        out("Note: no agreement is saved yet, so this verdict isn't tied to an agreement version.")
    return 0


def cmd_update(a, st: Store) -> int:
    led = st.load(a.client, a.project)
    r = find(led, a.id)
    if a.status:
        if a.status not in OLD_STATUS:
            fail(f"--status must be one of {', '.join(OLD_STATUS)} (or use --quote/--work/--invoice).")
        for k, v in OLD_STATUS[a.status].items():
            r[k] = v
    for field, value, allowed in (("quote", a.quote, QUOTE), ("work", a.work, WORK), ("invoice", a.invoice, INVOICE)):
        if value is not None:
            if value not in allowed:
                fail(f"--{field} must be one of {', '.join(allowed)}.")
            r[field] = value
    if a.courtesy is not None:
        r["complimentary"] = a.courtesy
        if a.courtesy:
            r["quote"], r["invoice"] = "n/a", "n/a"
    if r.get("invoice") in ("invoiced", "partially_paid", "paid", "overdue") and r.get("quote") != "accepted":
        fail("Only accepted work can be invoiced. Set --quote accepted first (and record who approved it).")
    if a.approved_by or a.evidence:
        r["approval"] = {"by": a.approved_by or r.get("approval", {}).get("by", ""),
                         "evidence": a.evidence or r.get("approval", {}).get("evidence", ""), "date": today()}
    if r.get("quote") == "accepted" and not r.get("approval"):
        out("Note: record how the client approved it (--approved-by, --evidence) so there's proof later.")
    if any(v is not None for v in (a.hours, a.days, a.fixed)):
        cost, how = cost_of(led, a.hours, a.days, a.fixed, a.buffer)
        r["cost"], r["priced_as"] = str(cost), how
    if a.note:
        r["note"] = a.note
    st.save(led)
    c = f" {money(Decimal(r['cost']), led['currency'])}" if r.get("cost") else ""
    out(f"#{r['id']}{c}: quote {r['quote']}, work {r['work']}, invoice {r['invoice']}"
        + (", courtesy" if r.get("complimentary") else "") + f": {r['request']}")
    return 0


def cmd_change_order(a, st: Store) -> int:
    led = st.load(a.client, a.project)
    items = [find(led, i) for i in a.ids]
    bad = [f"#{r['id']}" for r in items if not billable(r) and not r.get("over_cap")]
    if bad:
        fail(f"{', '.join(bad)} aren't extra work, so they don't belong on a change order.")
    unpriced = [f"#{r['id']}" for r in items if not r.get("cost")]
    if unpriced:
        fail(f"Price {', '.join(unpriced)} first (update ... --hours N or --fixed AMOUNT).")
    cur = led["currency"]
    agr = current_agreement(led)
    number = len(led.get("change_orders", [])) + 1
    total = sum(Decimal(r["cost"]) for r in items)
    lines = [f"# Change order {number}: {led['project']}", "",
             f"- **Client:** {led['client']}",
             f"- **Date:** {today()}",
             f"- **Based on:** agreement version {agr['version']}" + (f", {agr['source']}" if agr.get("source") else ""),
             "", "| # | Additional work | Why it's outside the agreement | Price |", "|---|---|---|---|"]
    for r in items:
        why = (r.get("clause") or "Not covered by the agreement").replace("|", "/")
        lines.append(f"| {r['id']} | {r['request'].replace('|', '/')} | {why} | {money(Decimal(r['cost']), cur)} |")
    lines += ["", f"**Total:** {money(total, cur)}"]
    if a.timeline:
        lines.append(f"**Timeline impact:** {a.timeline}")
    if agr.get("price"):
        others = sum((Decimal(r["cost"]) for r in led["requests"]
                      if billable(r) and r["quote"] == "accepted" and not r.get("absorbed_in")
                      and r.get("cost") and r["id"] not in a.ids), Decimal(0))
        on_top = sum((Decimal(r["cost"]) for r in items if not r.get("absorbed_in")), Decimal(0))
        lines.append(f"**Project total with these items:** {money(Decimal(agr['price']) + others + on_top, cur)}")
    approved = [r for r in items if r["quote"] == "accepted" and r.get("approval", {}).get("by")]
    if len(approved) == len(items):
        ap = items[0]["approval"]
        lines += ["", "Everything else in the agreement stays the same.", "",
                  f"**Approved by:** {ap.get('by')}   **Date:** {ap.get('date', '')}"
                  + (f"   **Evidence:** {ap['evidence']}" if ap.get("evidence") else ""), ""]
    else:
        lines += ["", "Work on these items starts once this change order is approved. "
                  "Everything else in the agreement stays the same.",
                  "", "**Approved by:** ______________________   **Date:** ____________", ""]
    text = "\n".join(lines)
    led.setdefault("change_orders", []).append({"number": number, "date": today(), "items": a.ids, "total": str(total)})
    for r in items:
        if r["quote"] == "draft":
            r["quote"] = "sent"
    st.save(led)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        out(f"Change order saved to {a.out}")
    out(text)
    return 0


def build_report(led: dict) -> str:
    cur = led["currency"]
    t = totals(led)
    m, c = t["money"], t["counts"]
    agr = current_agreement(led)
    price = Decimal(agr["price"]) if agr.get("price") else None
    lines = [f"# Scope report: {led['client']} / {led['project']}", ""]
    lines.append(f"- Rate: {money(Decimal(led['rate']), cur)}/{led['rate_unit']}"
                 + (f" | Agreed price (v{agr['version']}): {money(price, cur)}" if price is not None else ""))
    cap, used = agr.get("revisions_included"), len(led["rounds"])
    lines.append(f"- Revision rounds: {used}" + (f" of {cap} included" if cap is not None else " (no limit agreed)")
                 + (f" - **{used - cap} over the limit**" if cap is not None and used > cap else ""))
    if led["agreement"]:
        lines += ["", "## Agreement history", "", "| Version | Effective | Note | Approved by | Price | Rounds |",
                  "|---|---|---|---|---|---|"]
        for v in led["agreement"]:
            pv = money(Decimal(v["price"]), cur) if v.get("price") else "-"
            lines.append(f"| {v['version']} | {v.get('effective', '')} | {v.get('note', '')} | "
                         f"{v.get('approved_by') or '-'} | {pv} | {v.get('revisions_included', '-')} |")
    lines += ["", "## Extra work", "", "| | Items | Value |", "|---|---|---|"]
    for b, label in (("accepted", "Accepted by the client (billable)"), ("sent", "Quoted, waiting for an answer"),
                     ("draft", "Priced, not sent to the client yet"), ("grey_open", "Grey areas, still to clarify"),
                     ("rejected", "Declined by the client"), ("expired", "Quotes expired"),
                     ("complimentary", "Done as a courtesy (given away)")):
        lines.append(f"| {label} | {c[b]} | {money(m[b], cur)} |")
    lines += ["", f"Accepted extras: {money(m['paid'], cur)} paid, {money(m['invoiced'], cur)} invoiced and unpaid, "
              f"**{money(m['to_invoice'], cur)} not invoiced yet**."]
    if t["unpriced"]:
        lines.append(f"{t['unpriced']} extra or grey item(s) have no price yet.")
    if price is not None:
        absorbed = m["accepted"] - m["accepted_on_top"]
        lines.append(f"\n**Project total:** {money(price + m['accepted_on_top'], cur)} "
                     f"(agreed v{agr['version']} {money(price, cur)} + accepted extras {money(m['accepted_on_top'], cur)}"
                     + (f"; {money(absorbed, cur)} of accepted extras is already inside the amended price" if absorbed else "")
                     + ")")
    if m["complimentary"] > 0:
        pct = f" ({(m['complimentary'] / price * 100).quantize(Decimal('1'))}% of the agreed price)" if price else ""
        lines.append(f"\n**Given away so far:** {money(m['complimentary'], cur)}{pct}.")
    lines += ["", "## Change log", "", "| # | Date | Request | Verdict | Agreement | Quote | Work | Invoice | Value | Based on |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in led["requests"]:
        val = money(Decimal(r["cost"]), cur) if r.get("cost") else "-"
        verdict = r["verdict"] + (f" (round {r['round']}{', over limit' if r.get('over_cap') else ''})" if r.get("round") else "")
        if r.get("complimentary"):
            verdict += ", courtesy"
        clause = (r.get("clause") or "-").replace("|", "/")
        clause = clause if len(clause) <= 70 else clause[:67] + "..."
        lines.append(f"| {r['id']} | {r['date'][:10]} | {r['request'].replace('|', '/')} | {verdict} | "
                     f"v{r.get('agreement_version', '-')} | {r['quote']} | {r['work']} | {r['invoice']} | {val} | {clause} |")
    return "\n".join(lines) + "\n"


def cmd_report(a, st: Store) -> int:
    text = build_report(st.load(a.client, a.project))
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        out(f"Report saved to {a.out}")
    out(text)
    return 0


def cmd_list(a, st: Store) -> int:
    files = sorted(st.dir.glob("*/*.json")) if st.dir.is_dir() else []
    legacy = sorted(st.dir.glob("*.json")) if st.dir.is_dir() else []
    if not files and not legacy:
        out(f"No clients yet in {st.dir}.")
        return 0
    for f in files:
        try:
            led = json.loads(f.read_text(encoding="utf-8"))
            t = totals(led)
            cur = led["currency"]
            out(f"{led['client']} / {led['project']}: {len(led['requests'])} request(s) | accepted "
                f"{money(t['money']['accepted'], cur)} | waiting {money(t['money']['sent'], cur)} | "
                f"given away {money(t['money']['complimentary'], cur)}")
        except (json.JSONDecodeError, KeyError):
            out(f"{f}: damaged, see `restore`")
    for f in legacy:
        out(f"{f.stem}: old-format record, upgraded automatically the next time it's used")
    return 0


def cmd_restore(a, st: Store) -> int:
    p = st.path(a.client, a.project)
    bak = p.with_suffix(".json.bak")
    if not bak.is_file():
        fail(f"No backup found at {bak}.")
    json.loads(bak.read_text(encoding="utf-8"))  # make sure the backup itself is readable
    if p.is_file():
        shutil.copy2(p, p.with_name(f"{p.stem}.damaged-{dt.datetime.now():%Y%m%d-%H%M%S}.json.bak"))
    shutil.copy2(bak, p)
    out(f"Restored {p} from the last good copy (the replaced file was kept alongside it).")
    return 0


def cmd_quote(a, st: Store) -> int:
    if a.hours is None and a.days is None:
        fail("Give --hours or --days.")
    led = {"rate": str(dec(a.rate, "--rate")), "rate_unit": a.unit, "currency": a.currency.upper(),
           "hours_per_day": a.hours_per_day}
    cost, how = cost_of(led, a.hours, a.days, None, a.buffer)
    out(f"{money(cost, led['currency'])}  ({how})")
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="ScopeGuard: agreed scope, change requests and money per client project.")
    ap.add_argument("--data-dir", help="folder for records (default ./scopeguard-data in the current folder)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def client(p):
        p.add_argument("client")
        p.add_argument("--project", default="main", help='project name (default "main")')

    def priced(p):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--hours")
        g.add_argument("--days")
        g.add_argument("--fixed", help="fixed price instead of time x rate")
        p.add_argument("--buffer", help="extra percentage for uncertainty, e.g. 15")

    p = sub.add_parser("init")
    client(p)
    p.add_argument("--rate", required=True)
    p.add_argument("--unit", default="hour", choices=UNITS)
    p.add_argument("--currency", default="USD")
    p.add_argument("--price")
    p.add_argument("--revisions", type=int)
    p.add_argument("--hours-per-day", type=float, default=8)
    p.add_argument("--force", action="store_true", help="start over (the old record is backed up first)")
    for name in ("agreement", "baseline"):
        p = sub.add_parser(name)
        client(p)
        p.add_argument("--file", required=True)
        p.add_argument("--note")
        p.add_argument("--approved-by")
        p.add_argument("--evidence")
        p.add_argument("--absorbs", type=int, nargs="+", help="accepted extra IDs now included in this version's price")
    p = sub.add_parser("round")
    client(p)
    p.add_argument("--note")
    p = sub.add_parser("log")
    client(p)
    p.add_argument("--request", required=True)
    p.add_argument("--verdict", required=True)
    p.add_argument("--clause")
    p.add_argument("--note")
    p.add_argument("--courtesy", action="store_true", help="the user chooses to do this extra for free")
    p.add_argument("--allow-over-cap", action="store_true", help="record an over-limit revision as-is")
    priced(p)
    p = sub.add_parser("update")
    client(p)
    p.add_argument("id", type=int)
    p.add_argument("--quote")
    p.add_argument("--work")
    p.add_argument("--invoice")
    p.add_argument("--status", help="shortcut: pending, approved, free, declined, done")
    cg = p.add_mutually_exclusive_group()
    cg.add_argument("--courtesy", dest="courtesy", action="store_true", default=None)
    cg.add_argument("--no-courtesy", dest="courtesy", action="store_false")
    p.add_argument("--approved-by")
    p.add_argument("--evidence", help="where the approval is, e.g. 'email 3 Dec 10:14'")
    p.add_argument("--note")
    priced(p)
    p = sub.add_parser("change-order")
    client(p)
    p.add_argument("ids", type=int, nargs="+")
    p.add_argument("--timeline")
    p.add_argument("--out")
    p = sub.add_parser("report")
    client(p)
    p.add_argument("--out")
    sub.add_parser("list")
    p = sub.add_parser("restore")
    client(p)
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
    handlers = {"init": cmd_init, "agreement": cmd_agreement, "baseline": cmd_agreement, "round": cmd_round,
                "log": cmd_log, "update": cmd_update, "change-order": cmd_change_order, "report": cmd_report,
                "list": cmd_list, "restore": cmd_restore, "quote": cmd_quote}
    return handlers[a.cmd](a, st)


if __name__ == "__main__":
    sys.exit(main())
