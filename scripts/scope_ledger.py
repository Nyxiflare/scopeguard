#!/usr/bin/env python3
"""
scope_ledger.py - ScopeGuard's record of agreed scope, change requests and money, per client project.

Every command takes CLIENT and an optional --project NAME (default "main"), so one client can have
several independent projects, each with its own agreement, revision rounds and money.

Commands
  init CLIENT --rate 50 [--unit hour|day] [--currency USD] [--price 600] [--revisions 2]
  agreement CLIENT --file scope.json [--note TEXT] [--approved-by NAME] [--evidence TEXT] [--absorbs ID ...]
        First call stores version 1 (the original agreement). Later calls add an amendment as a new
        version; earlier versions are never changed. ("baseline" is an alias.)
  round CLIENT [--note TEXT]                     record one batch of client feedback (a revision round)
  log CLIENT --request TEXT --verdict included|revision|grey|extra [--clause TEXT]
        [--hours N | --days N | --fixed AMOUNT] [--buffer PCT] [--courtesy] [--allow-over-cap] [--note TEXT]
  update CLIENT ID [--verdict V --reason TEXT] [--quote draft|sent|accepted|rejected|expired]
        [--work not_started|in_progress|done] [--invoice not_invoiced|invoiced|partially_paid|paid|overdue]
        [--advance] [--courtesy | --no-courtesy] [--approved-by NAME] [--evidence TEXT]
        [--hours N | --days N | --fixed AMOUNT] [--note TEXT]
  change-order CLIENT ID [ID ...] [--timeline TEXT] [--out FILE.md]
  report CLIENT [--out FILE.md]
  explain CLIENT                                 how every total is calculated, item by item
  history CLIENT                                 the audit trail of every recorded event
  list
  restore CLIENT                                 go back to the state before the last change
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
INVOICED = ("invoiced", "partially_paid", "paid", "overdue")
# Invoice states only move forward; going back needs --correction.
INVOICE_RANK = {"n/a": -1, "not_invoiced": 0, "invoiced": 1, "overdue": 2, "partially_paid": 2, "paid": 3}
AGREEMENT_STATUS = ("agreed", "proposed")
UNITS = ("hour", "day")
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥", "AUD": "A$", "CAD": "C$",
           "KRW": "₩", "BRL": "R$", "MXN": "MX$", "SGD": "S$", "NZD": "NZ$", "PHP": "₱", "NGN": "₦"}
# Decimal places per currency (ISO 4217 minor units). Anything not listed uses 2.
MINOR_UNITS = {"JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "UGX": 0, "PYG": 0, "XOF": 0, "XAF": 0,
               "IDR": 2, "BHD": 3, "KWD": 3, "OMR": 3, "JOD": 3, "TND": 3, "IQD": 3, "LYD": 3}
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


def minor_units(currency: str) -> int:
    return MINOR_UNITS.get(currency.upper(), 2)


def money(value: Decimal, currency: str) -> str:
    places = minor_units(currency)
    q = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = f"{q:,}"
    if places and text.endswith("." + "0" * places):
        text = text[: -(places + 1)]
    sym = SYMBOLS.get(currency.upper())
    return f"{sym}{text}" if sym else f"{text} {currency.upper()}"


def dec(value, name: str, allow_zero: bool = True) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        fail(f"{name} must be a number, got '{value}'.")
    if not d.is_finite() or d < 0 or (not allow_zero and d == 0):
        fail(f"{name} must be a {'positive' if not allow_zero else 'non-negative'} number, got '{value}'.")
    return d


def count(value, name: str):
    """A revision limit: None or a whole number >= 0."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            if isinstance(value, str) and value.strip().isdigit():
                return int(value)
        except ValueError:
            pass
        fail(f"{name} must be a whole number (0 or more), got '{value}'.")
    if value < 0:
        fail(f"{name} can't be negative.")
    return value


def hours_per_day(value) -> float:
    d = dec(value, "--hours-per-day", allow_zero=False)
    if d > 24:
        fail("--hours-per-day can't be more than 24.")
    return float(d)


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    if not s:
        fail("Names need at least one letter or number.")
    return s


def md(text) -> str:
    """Make user text safe inside a Markdown table cell or line."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def log_event(led: dict, event: str, detail: str) -> None:
    led.setdefault("events", []).append({"at": now(), "event": event, "detail": detail})


# --------------------------------------------------------------------------- validation

def _is_money(v) -> bool:
    if v is None:
        return True
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return False
    return d.is_finite() and d >= 0


def validate(led: dict, where: str) -> None:
    """Check the whole record, including references between its parts. Lists every problem found."""
    p: list[str] = []
    for key, kind in (("client", str), ("project", str), ("currency", str), ("rate", str),
                      ("rate_unit", str), ("agreement", list), ("rounds", list), ("requests", list)):
        if not isinstance(led.get(key), kind):
            p.append(f"'{key}' is missing or not a {kind.__name__}")
    if p:
        fail(f"{where} doesn't look like a valid ScopeGuard record: " + "; ".join(p))
    if led["rate_unit"] not in UNITS:
        p.append("rate_unit must be hour or day")
    if not _is_money(led["rate"]):
        p.append("rate is not a valid amount")
    try:
        hpd = Decimal(str(led.get("hours_per_day", 8)))
        if not hpd.is_finite() or hpd <= 0 or hpd > 24:
            p.append("hours_per_day must be between 0 and 24")
    except InvalidOperation:
        p.append("hours_per_day is not a number")

    versions = []
    for i, v in enumerate(led["agreement"], 1):
        if not isinstance(v, dict):
            p.append(f"agreement entry {i} is not an object")
            continue
        if v.get("version") != i:
            p.append(f"agreement versions must run 1, 2, 3… (entry {i} says {v.get('version')})")
        if not _is_money(v.get("price")):
            p.append(f"agreement v{i} has an invalid price")
        rc = v.get("revisions_included")
        if rc is not None and (isinstance(rc, bool) or not isinstance(rc, int) or rc < 0):
            p.append(f"agreement v{i} has an invalid revision limit")
        if not isinstance(v.get("scope", {}), dict):
            p.append(f"agreement v{i} scope is not an object")
        if v.get("status", "agreed") not in AGREEMENT_STATUS:
            p.append(f"agreement v{i} has an unknown status '{v.get('status')}'")
        if i == 1 and v.get("status", "agreed") != "agreed":
            p.append("agreement v1 (the original) must be agreed")
        versions.append(i)
    vset = set(versions) | {0}

    rset = set()
    for i, r in enumerate(led["rounds"], 1):
        if not isinstance(r, dict) or r.get("round") != i:
            p.append(f"revision rounds must run 1, 2, 3… (entry {i})")
            continue
        if r.get("agreement_version", 0) not in vset:
            p.append(f"round {i} points to agreement v{r.get('agreement_version')}, which doesn't exist")
        rset.add(i)

    co_numbers = set()
    co_members: dict[int, int] = {}
    for i, co in enumerate(led.get("change_orders", []), 1):
        if not isinstance(co, dict) or co.get("number") != i:
            p.append(f"change orders must be numbered 1, 2, 3… (entry {i})")
            continue
        co_numbers.add(i)
        for rid in co.get("items", []):
            if rid in co_members:
                p.append(f"request {rid} is on change orders {co_members[rid]} and {i}")
            co_members[rid] = i

    ids = set()
    for r in led["requests"]:
        if not isinstance(r, dict) or not isinstance(r.get("id"), int):
            p.append("a request has no numeric id")
            continue
        rid = r["id"]
        if rid in ids:
            p.append(f"request id {rid} appears twice")
        ids.add(rid)
        if r.get("verdict") not in VERDICTS:
            p.append(f"request {rid} has an unknown verdict '{r.get('verdict')}'")
        for field, allowed in (("quote", QUOTE), ("work", WORK), ("invoice", INVOICE)):
            if r.get(field) not in allowed:
                p.append(f"request {rid} has an unknown {field} state '{r.get(field)}'")
        if not _is_money(r.get("cost")) or not _is_money(r.get("approved_amount")):
            p.append(f"request {rid} has an invalid cost or approved amount")
        if r.get("agreement_version", 0) not in vset:
            p.append(f"request {rid} points to agreement v{r.get('agreement_version')}, which doesn't exist")
        if r.get("round") is not None and r["round"] not in rset:
            p.append(f"request {rid} points to round {r['round']}, which doesn't exist")
        if r.get("absorbed_in") is not None and r["absorbed_in"] not in vset - {0}:
            p.append(f"request {rid} is absorbed in agreement v{r['absorbed_in']}, which doesn't exist")
        if r.get("change_order") is not None and r["change_order"] not in co_numbers:
            p.append(f"request {rid} points to change order {r['change_order']}, which doesn't exist")
        if r.get("invoice") in INVOICED and (r.get("quote") != "accepted" or r.get("complimentary")):
            p.append(f"request {rid} is invoiced but not an accepted, chargeable extra")
    for rid in co_members:
        if rid not in ids:
            p.append(f"a change order lists request {rid}, which doesn't exist")
    for v in led["agreement"]:
        for rid in v.get("absorbs", []) if isinstance(v, dict) else []:
            if rid not in ids:
                p.append(f"agreement v{v.get('version')} absorbs request {rid}, which doesn't exist")
    if p:
        fail(f"{where} has {len(p)} problem(s): " + "; ".join(p[:8]) + ("…" if len(p) > 8 else ""))


# --------------------------------------------------------------------------- storage

def migrate_v1(old: dict, project: str) -> dict:
    """Convert a version-1 record (single status, overwritten baseline) to schema 2."""
    led = {
        "schema": SCHEMA, "client": old["client"], "project": old.get("project") or project,
        "currency": old.get("currency", "USD"), "rate": old["rate"], "rate_unit": old.get("rate_unit", "hour"),
        "hours_per_day": old.get("hours_per_day", 8), "agreement": [], "rounds": [], "requests": [],
        "change_orders": [], "events": [], "created": old.get("created", now()), "rev": 0,
    }
    scope = {k: v for k, v in (old.get("baseline") or {}).items() if k in SCOPE_KEYS}
    led["agreement"].append({
        "version": 1, "effective": (old.get("created") or today())[:10], "note": "original (migrated)",
        "source": (old.get("baseline") or {}).get("source", ""), "approved_by": "", "evidence": "",
        "price": old.get("original_price"), "revisions_included": old.get("revisions_included"),
        "scope": scope, "absorbs": [], "status": "agreed",
    })
    for n in range(1, int(old.get("revisions_used", 0)) + 1):
        led["rounds"].append({"round": n, "date": "", "note": "migrated", "agreement_version": 1,
                              "cap": old.get("revisions_included")})
    for r in old.get("requests", []):
        is_extra = r.get("verdict") in ("extra", "grey")
        new = {"id": r["id"], "date": r.get("date", ""), "request": r.get("request", ""),
               "verdict": r.get("verdict", "grey"), "clause": r.get("clause", ""), "cost": r.get("cost"),
               "priced_as": r.get("priced_as", ""), "agreement_version": 1, "round": r.get("round"),
               "over_cap": False, "quote": "draft" if is_extra else "n/a", "work": "not_started",
               "invoice": "not_invoiced" if is_extra else "n/a",
               "complimentary": False, "approval": {}, "note": r.get("note", "")}
        if new["round"] is not None and new["round"] > len(led["rounds"]):
            new["round"] = None
        for k, v in OLD_STATUS.get(r.get("status", ""), {}).items():
            new[k] = v
        if new["complimentary"]:
            new["invoice"] = "n/a"
        led["requests"].append(new)
    log_event(led, "migrated", "upgraded from the first record format")
    return led


def upgrade_v2(led: dict) -> dict:
    """Fill fields added in v2.1 to records written by v2.0, without changing any recorded fact."""
    caps = {v["version"]: v.get("revisions_included") for v in led.get("agreement", []) if isinstance(v, dict)}
    for r in led.get("rounds", []):
        if isinstance(r, dict) and "agreement_version" not in r:
            r["agreement_version"] = 1 if caps else 0
            r["cap"] = caps.get(1)
    for v in led.get("agreement", []):
        if isinstance(v, dict):
            v.setdefault("absorbs", [])
            v.setdefault("status", "agreed")  # before v2.2 every saved version governed
    led.setdefault("change_orders", [])
    led.setdefault("events", [])
    return led


class Lock:
    """A simple lock file so two commands can't write the same record at once."""

    def __init__(self, p: Path):
        self.lock = p.with_suffix(".json.lock")

    def __enter__(self):
        self.lock.parent.mkdir(parents=True, exist_ok=True)
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


def atomic_write(p: Path, data: dict) -> None:
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


class Store:
    def __init__(self, data_dir: str | None):
        self.dir = Path(data_dir) if data_dir else Path.cwd() / "scopeguard-data"

    def path(self, client: str, project: str) -> Path:
        return self.dir / slug(client) / f"{slug(project)}.json"

    def _read(self, p: Path) -> dict:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            bak = p.with_suffix(".json.bak")
            msg = f"{p} is damaged (line {e.lineno}: {e.msg})."
            if bak.is_file():
                msg += " The previous good copy can be put back with:  restore CLIENT --project NAME"
            fail(msg)
        if not isinstance(data, dict):
            fail(f"{p} doesn't look like a valid ScopeGuard record (it isn't a JSON object).")
        return data

    def load(self, client: str, project: str) -> dict:
        p = self.path(client, project)
        if not p.is_file():
            legacy = self.dir / f"{slug(client)}.json"
            if legacy.is_file() and slug(project) == "main":
                with Lock(p):
                    led = migrate_v1(self._read(legacy), project)
                    validate(led, str(legacy))
                    p.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(p, led)
                    legacy.rename(legacy.with_suffix(".json.v1-migrated"))
                out(f"Note: moved the old record for {client} to {p} (new format).")
            else:
                others = sorted(x.stem for x in (self.dir / slug(client)).glob("*.json")) \
                    if (self.dir / slug(client)).is_dir() else []
                hint = f" Projects on file: {', '.join(others)}." if others else ""
                fail(f"No record for '{client}' / project '{project}'.{hint} "
                     f"Start one with:  init \"{client}\" --project \"{project}\" --rate YOUR_RATE")
        led = self._read(p)
        led = upgrade_v2(led) if led.get("schema") == SCHEMA else migrate_v1(led, project)
        validate(led, str(p))
        led["_path"] = str(p)
        led["_loaded_rev"] = led.get("rev", 0)
        return led

    def create(self, led: dict, force: bool) -> Path:
        """Create a new record. Checks for an existing one under the lock, so two inits can't both win."""
        p = self.path(led["client"], led["project"])
        with Lock(p):
            if p.exists():
                if not force:
                    fail(f"{led['client']} / {led['project']} already has a record ({p}). To start over, add --force "
                         "(a backup copy is kept), or use a different --project name.")
                backup = p.with_name(f"{p.stem}.reset-{dt.datetime.now():%Y%m%d-%H%M%S}.json.bak")
                shutil.copy2(p, backup)
                out(f"Backed up the old record to {backup}")
                led["events"].append({"at": now(), "event": "reset", "detail": f"started over; old record in {backup.name}"})
            led["rev"] = 1
            led["updated"] = now()
            validate(led, "the new record")
            atomic_write(p, led)
        return p

    def save(self, led: dict) -> Path:
        p = Path(led.pop("_path"))
        loaded_rev = led.pop("_loaded_rev", None)
        with Lock(p):
            if p.is_file():
                current = self._read(p)
                if loaded_rev is not None and current.get("rev", 0) != loaded_rev:
                    fail(f"{p} was changed by another command while this one ran. Nothing was saved; run it again.")
                shutil.copy2(p, p.with_suffix(".json.bak"))
            led["rev"] = int(led.get("rev", 0)) + 1
            led["updated"] = now()
            validate(led, "the new record")
            atomic_write(p, led)
        return p


# --------------------------------------------------------------------------- helpers

def current_agreement(led: dict) -> dict:
    """The agreement that governs the project now: the latest version the client agreed to.
    Proposed amendments are kept on record but don't change any verdict, limit or total."""
    for v in reversed(led["agreement"]):
        if v.get("status", "agreed") == "agreed":
            return v
    return {"version": 0, "price": None, "revisions_included": None, "scope": {}, "source": ""}


def proposed_versions(led: dict) -> list:
    newest = current_agreement(led)["version"]
    return [v for v in led["agreement"] if v.get("status") == "proposed" and v["version"] > newest]


def version_of(led: dict, n: int) -> dict:
    return led["agreement"][n - 1] if n else current_agreement(led)


def round_money(value: Decimal, currency: str) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-minor_units(currency)), rounding=ROUND_HALF_UP)


def cost_of(led: dict, hours=None, days=None, fixed=None, buffer=None):
    """Price a request. The stored amount is rounded to the currency's minor units, so displayed
    line items always add up to displayed totals."""
    cost, how = _raw_cost(led, hours, days, fixed, buffer)
    return (round_money(cost, led["currency"]) if cost is not None else None), how


def _raw_cost(led: dict, hours=None, days=None, fixed=None, buffer=None):
    if fixed is not None:
        return dec(fixed, "--fixed"), "fixed price"
    if hours is None and days is None:
        return None, "not priced"
    rate = Decimal(led["rate"])
    hpd = Decimal(str(hours_per_day(led.get("hours_per_day", 8))))
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


def is_extra(r: dict) -> bool:
    """Confirmed extra work: an 'extra' verdict or a revision beyond the agreed rounds."""
    return r["verdict"] == "extra" or bool(r.get("over_cap"))


def billable(r: dict) -> bool:
    return (r["verdict"] in ("extra", "grey") or r.get("over_cap")) and not r.get("complimentary")


def extra_like(r: dict) -> bool:
    """Work that sits outside the agreed price: extra, grey, or a revision beyond the limit."""
    return r["verdict"] in ("extra", "grey") or bool(r.get("over_cap"))


def counts_as_accepted(r: dict) -> bool:
    """Accepted, chargeable extra work. A grey item never counts, even if marked accepted."""
    return billable(r) and r["quote"] == "accepted" and r["verdict"] != "grey"


def review_flags(led: dict) -> list:
    """Inconsistencies worth a human look. Reported, never silently fixed."""
    cur = led["currency"]
    flags = []
    for r in led["requests"]:
        tag = f"#{r['id']} {r['request']}"
        if r["verdict"] == "grey" and r["quote"] == "accepted":
            flags.append(f"{tag} is a grey area but marked accepted. Settle it (update {r['id']} --verdict extra "
                         "--reason ...) or reset the quote; it isn't counted as revenue until then.")
        if not extra_like(r) and not r.get("complimentary") and (r["quote"] != "n/a" or r["invoice"] != "n/a"):
            flags.append(f"{tag} is {r['verdict']} work but has a quote/invoice state; it's covered by the agreement.")
        if r["quote"] == "accepted" and r.get("approved_amount") is not None and r.get("cost") is not None \
                and Decimal(r["approved_amount"]) != Decimal(r["cost"]):
            flags.append(f"{tag} was approved at {money(Decimal(r['approved_amount']), cur)} but is recorded at "
                         f"{money(Decimal(r['cost']), cur)}. Review required.")
    for v in proposed_versions(led):
        flags.append(f"Agreement v{v['version']} ({v.get('note', '')}) is proposed, not agreed: it doesn't govern yet.")
    return flags


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
        elif r["verdict"] == "grey" and r["quote"] in ("draft", "sent", "accepted"):
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


def approval_text(r: dict) -> str:
    ap = r.get("approval") or {}
    if not ap.get("by"):
        return "awaiting approval"
    return md(", ".join(x for x in (ap.get("by"), ap.get("date"), ap.get("evidence")) if x))


# --------------------------------------------------------------------------- commands

def cmd_init(a, st: Store) -> int:
    hpd = hours_per_day(a.hours_per_day)
    revisions = count(a.revisions, "--revisions")
    client_dir = st.dir / slug(a.client)
    for f in client_dir.glob("*.json") if client_dir.is_dir() else []:
        try:
            other = json.loads(f.read_text(encoding="utf-8")).get("client", "")
        except (json.JSONDecodeError, OSError):
            continue
        if other and other.strip().lower() != a.client.strip().lower():
            fail(f"'{a.client}' would share a folder with the existing client '{other}' ({client_dir}). "
                 "Use a more distinct client name so their records stay separate.")
    led = {
        "schema": SCHEMA, "client": a.client, "project": a.project, "currency": a.currency.upper(),
        "rate": str(dec(a.rate, "--rate")), "rate_unit": a.unit, "hours_per_day": hpd,
        "agreement": [], "rounds": [], "requests": [], "change_orders": [], "events": [],
        "created": now(), "rev": 0,
    }
    if a.price is not None or revisions is not None:
        led["agreement"].append({"version": 1, "effective": today(), "note": "original", "source": "",
                                 "approved_by": "", "evidence": "",
                                 "price": str(dec(a.price, "--price")) if a.price is not None else None,
                                 "revisions_included": revisions, "scope": {}, "absorbs": [],
                                 "status": "agreed"})
    log_event(led, "project created", f"rate {money(Decimal(led['rate']), led['currency'])}/{a.unit}")
    path = st.create(led, a.force)
    out(f"Started {a.client} / {a.project} at {path}")
    out(f"Rate: {money(Decimal(led['rate']), led['currency'])}/{a.unit}")
    return 0


def check_absorbs(led: dict, absorbs: list) -> None:
    if len(set(absorbs)) != len(absorbs):
        fail("--absorbs lists the same request more than once.")
    for rid in absorbs:
        r = find(led, rid)
        if not is_extra(r) or r.get("complimentary") or not counts_as_accepted(r):
            fail(f"#{rid} can't be absorbed: only accepted, chargeable extras can be folded into an amendment "
                 f"(it is {r['verdict']}, quote {r['quote']}{', courtesy' if r.get('complimentary') else ''}).")
        if r.get("absorbed_in"):
            fail(f"#{rid} is already part of agreement v{r['absorbed_in']}.")


def put_in_force(led: dict, version: dict, prev: dict) -> None:
    """What happens when a version becomes the governing agreement."""
    absorbs = version.get("absorbs", [])
    loose = [r for r in led["requests"] if counts_as_accepted(r) and not r.get("absorbed_in") and r["id"] not in absorbs]
    if prev["version"] and prev.get("price") != version["price"] and loose and not absorbs:
        out("Note: the price changed and there are accepted extras (" + ", ".join(f"#{r['id']}" for r in loose)
            + "). If this amendment's price already includes them, record it with --absorbs ID so they aren't "
              "counted twice in the project total.")
    if prev["version"] and prev.get("revisions_included") != version["revisions_included"]:
        out(f"Note: the revision limit changes from {prev.get('revisions_included')} to {version['revisions_included']} "
            f"for rounds recorded from now on. The {len(led['rounds'])} round(s) already recorded keep the limit they had.")
    for rid in absorbs:
        find(led, rid)["absorbed_in"] = version["version"]


def cmd_agreement(a, st: Store) -> int:
    led = st.load(a.client, a.project)
    if a.approve is not None:
        return approve_amendment(a, st, led)
    if not a.file:
        fail("Give --file with the agreement terms, or --approve N to record that the client agreed to amendment N.")
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
    revisions = count(data["revisions_included"], "revisions_included") if "revisions_included" in data \
        else prev.get("revisions_included")
    effective = data.get("effective") or today()
    try:
        dt.date.fromisoformat(str(effective))
    except ValueError:
        fail(f"'effective' must be a date like 2026-12-01, got '{effective}'.")
    absorbs = list(a.absorbs or [])
    check_absorbs(led, absorbs)  # validate everything before touching the record
    if absorbs and prev["version"] == 0:
        fail("--absorbs only makes sense for an amendment, after the original agreement is saved.")

    number = len(led["agreement"]) + 1
    agreed = number == 1 or bool(a.approved_by or a.evidence)
    version = {
        "version": number, "effective": str(effective),
        "note": a.note or ("original" if number == 1 else "amendment"),
        "source": data.get("source", ""), "approved_by": a.approved_by or "", "evidence": a.evidence or "",
        "price": str(dec(price, "price")) if price is not None else None,
        "revisions_included": revisions,
        "scope": {**(prev.get("scope") or {}), **{k: v for k, v in data.items() if k in SCOPE_KEYS}},
        "absorbs": absorbs, "status": "agreed" if agreed else "proposed",
    }
    # The very first agreement can fill in a version 1 that init created with only price/revisions.
    if len(led["agreement"]) == 1 and not prev.get("scope") and not led["requests"] and not led["rounds"] and not a.note:
        version["version"], version["note"], version["status"] = 1, "original", "agreed"
        led["agreement"][0] = version
        log_event(led, "agreement saved", "version 1 (original)")
    elif agreed:
        led["agreement"].append(version)
        put_in_force(led, version, prev)
        log_event(led, "agreement saved", f"version {number} ({version['note']}), agreed"
                  + (f" by {a.approved_by}" if a.approved_by else "")
                  + (f", absorbs {', '.join('#' + str(i) for i in absorbs)}" if absorbs else ""))
    else:
        led["agreement"].append(version)
        log_event(led, "amendment proposed", f"version {number} ({version['note']}), not agreed yet")
    st.save(led)
    v = version
    out(f"Saved agreement version {v['version']} ({v['note']}) for {led['client']} / {led['project']}: "
        f"{len(v['scope'].get('deliverables', []))} deliverable(s), {len(v['scope'].get('exclusions', []))} "
        f"exclusion(s), revision rounds: {v['revisions_included']}"
        + (f", price {money(Decimal(v['price']), led['currency'])}" if v["price"] else ""))
    if v["status"] == "proposed":
        out(f"This amendment is PROPOSED: it doesn't change verdicts, limits or totals until the client agrees. "
            f"When they do:  agreement \"{led['client']}\" --project \"{led['project']}\" --approve {v['version']} "
            "--approved-by NAME --evidence \"where they agreed\"")
    return 0


def approve_amendment(a, st: Store, led: dict) -> int:
    n = a.approve
    if not 1 <= n <= len(led["agreement"]):
        fail(f"There's no agreement version {n}.")
    v = led["agreement"][n - 1]
    if v.get("status", "agreed") == "agreed":
        fail(f"Agreement v{n} is already agreed.")
    if not (a.approved_by or a.evidence):
        fail("Record who agreed and where: --approved-by NAME --evidence \"email 3 Dec\".")
    prev = current_agreement(led)
    if prev["version"] > n:
        fail(f"v{prev['version']} was agreed after v{n}, so v{n} can't take effect now. Save a new amendment instead.")
    check_absorbs(led, v.get("absorbs", []))
    v.update(status="agreed", approved_by=a.approved_by or v.get("approved_by", ""),
             evidence=a.evidence or v.get("evidence", ""), approved_on=today())
    put_in_force(led, v, prev)
    log_event(led, "amendment agreed", f"version {n} agreed by {v['approved_by'] or '-'} ({v['evidence'] or '-'})")
    st.save(led)
    out(f"Agreement v{n} ({v.get('note', '')}) is now agreed and governs the project.")
    return 0


def cmd_round(a, st: Store) -> int:
    led = st.load(a.client, a.project)
    agr = current_agreement(led)
    n = len(led["rounds"]) + 1
    cap = agr.get("revisions_included")
    led["rounds"].append({"round": n, "date": today(), "note": a.note or "", "agreement_version": agr["version"],
                          "cap": cap})
    log_event(led, "revision round", f"round {n} under agreement v{agr['version']} (limit {cap})")
    st.save(led)
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
    agr_version = agr["version"]
    cost, how = cost_of(led, a.hours, a.days, a.fixed, a.buffer)
    rnd, over = None, False
    if a.verdict == "revision":
        if not led["rounds"]:
            fail("Record the feedback round first:  round CLIENT --project NAME  (one round per batch of feedback).")
        last = led["rounds"][-1]
        rnd = last["round"]
        # A revision belongs to the round's agreement version and limit, not whatever is current now.
        agr_version = last.get("agreement_version", agr_version)
        if agr_version != agr["version"]:
            out(f"Note: round {rnd} was recorded under agreement v{agr_version}, so this revision is judged "
                f"against v{agr_version}. For feedback that arrived after the amendment, record a new round first.")
        cap = last.get("cap")
        over = cap is not None and rnd > cap
        if over and not (a.courtesy or a.allow_over_cap):
            fail(f"Round {rnd} is beyond the {cap} revision rounds included, so this change is extra work. "
                 "Log it with --verdict extra and a price, or add --courtesy if the user chooses to do it free.")
    covered = a.verdict == "included" or (a.verdict == "revision" and not over)
    if covered and cost is not None and not a.courtesy:
        out("Note: included work and in-limit revisions are covered by the agreed price, so no cost is recorded.")
        cost, how = None, "covered by the agreement"
    extra_like = a.verdict in ("extra", "grey") or over
    rid = max((r["id"] for r in led["requests"]), default=0) + 1
    rec = {
        "id": rid, "date": now(), "request": a.request, "verdict": a.verdict, "clause": a.clause or "",
        "cost": str(cost) if cost is not None else None, "priced_as": how, "agreement_version": agr_version,
        "round": rnd, "over_cap": over,
        "quote": "n/a" if (a.courtesy or not extra_like) else "draft", "work": "not_started",
        "invoice": "n/a" if (a.courtesy or not extra_like) else "not_invoiced",
        "complimentary": bool(a.courtesy), "approval": {}, "note": a.note or "",
    }
    led["requests"].append(rec)
    log_event(led, "request logged", f"#{rid} {a.verdict}{' (courtesy)' if a.courtesy else ''}: {a.request}"
              + (f", {money(cost, led['currency'])}" if cost is not None else ""))
    st.save(led)
    label = {"included": "INCLUDED", "grey": "GREY AREA", "extra": "EXTRA", "revision": "REVISION"}[a.verdict]
    bits = []
    if rnd:
        bits.append(f"round {rnd}" + (" (over the limit)" if over else ""))
    if a.courtesy:
        bits.append("courtesy, no charge")
    tail = f" [{', '.join(bits)}]" if bits else ""
    out(f"#{rid} {label}: {a.request}{tail}" + (f" -> {money(cost, led['currency'])} ({how})" if cost is not None else ""))
    if not agr["version"]:
        out("Note: no agreement is saved yet, so this verdict isn't tied to an agreement version.")
    return 0


def cmd_update(a, st: Store) -> int:
    led = st.load(a.client, a.project)
    r = find(led, a.id)
    cur = led["currency"]
    changes = []
    was_accepted = r["quote"] == "accepted"
    ever_invoiced = bool(r.get("ever_invoiced")) or r.get("invoice") in INVOICED

    # 1. Verdict (settling a grey area, or a correction)
    if a.verdict:
        if a.verdict not in VERDICTS:
            fail(f"--verdict must be one of {', '.join(VERDICTS)}.")
        if a.verdict == "revision":
            fail("To record a revision, log it after a round. --verdict on update is for settling grey areas "
                 "(grey -> extra or included) or correcting a verdict.")
        if not a.reason:
            fail("Say why the verdict changes with --reason (e.g. 'client confirmed social sizes weren't included').")
        if r.get("change_order") or r.get("absorbed_in") or ever_invoiced:
            fail(f"#{r['id']} is already on a change order, in an amendment, or invoiced; its verdict can't change now.")
        if was_accepted:
            fail(f"#{r['id']} is accepted by the client. Undo that first (update {r['id']} --quote sent, or "
                 "--quote rejected), then change the verdict.")
        old = r["verdict"]
        was = f"{old}, {money(Decimal(r['cost']), cur)}" if r.get("cost") else old
        r["verdict"] = a.verdict
        if a.verdict in ("extra", "grey") and r["quote"] == "n/a" and not r.get("complimentary"):
            r["quote"], r["invoice"] = "draft", "not_invoiced"
        if a.verdict == "included":
            r["quote"], r["invoice"], r["cost"], r["priced_as"] = "n/a", "n/a", None, "covered by the agreement"
        r["note"] = (r.get("note", "") + f" Reclassified {old} -> {a.verdict} (was {was}): {a.reason}").strip()
        changes.append(f"verdict {old} -> {a.verdict} ({a.reason})")

    # 2. Price, judged against the state before this command
    if any(v is not None for v in (a.hours, a.days, a.fixed)):
        if ever_invoiced:
            fail("This item has been invoiced, so its price is frozen. Log a correction as a new item instead.")
        cost, how = cost_of(led, a.hours, a.days, a.fixed, a.buffer)
        old_cost = r.get("cost")
        r["cost"], r["priced_as"] = str(cost), how
        changes.append(f"price {money(cost, cur)}")
        if was_accepted and r.get("approved_amount") != str(cost) and a.quote != "accepted":
            if a.reapproved_by:
                r["approval"] = {"by": a.reapproved_by, "evidence": a.evidence or "", "date": today()}
                r["approved_amount"] = str(cost)
                changes.append(f"re-approved by {a.reapproved_by}")
            else:
                r.setdefault("previous_approvals", []).append({**(r.get("approval") or {}),
                                                               "amount": r.get("approved_amount")})
                r["approval"], r["approved_amount"], r["quote"] = {}, None, "sent"
                prior = money(Decimal(old_cost), cur) if old_cost else "an earlier price"
                out(f"Note: #{r['id']} was approved at {prior}. The new price {money(cost, cur)} needs the client's OK "
                    "again, so the quote is back to 'sent'. If they already agreed, add --reapproved-by NAME.")
                changes.append("quote back to sent (price changed after approval)")

    # 3. States
    if a.status:
        if a.status not in OLD_STATUS:
            fail(f"--status must be one of {', '.join(OLD_STATUS)} (or use --quote/--work/--invoice).")
        for k, v in OLD_STATUS[a.status].items():
            r[k] = v
        changes.append(f"status {a.status}")
    for field, value, allowed in (("quote", a.quote, QUOTE), ("work", a.work, WORK)):
        if value is not None:
            if value not in allowed:
                fail(f"--{field} must be one of {', '.join(allowed)}.")
            r[field] = value
            changes.append(f"{field} {value}")
    if a.invoice is not None:
        if a.invoice not in INVOICE:
            fail(f"--invoice must be one of {', '.join(INVOICE)}.")
        old_inv = r.get("invoice", "not_invoiced")
        if INVOICE_RANK.get(a.invoice, 0) < INVOICE_RANK.get(old_inv, 0):
            if not a.correction:
                fail(f"Invoice states only move forward ({old_inv} -> {a.invoice} goes back). If this corrects a "
                     "mistake, add --correction --reason \"...\".")
            if not a.reason:
                fail("A correction needs --reason.")
            changes.append(f"invoice correction {old_inv} -> {a.invoice} ({a.reason})")
        else:
            changes.append(f"invoice {a.invoice}")
        r["invoice"] = a.invoice
        if a.invoice in INVOICED:
            r["ever_invoiced"] = True
    if a.courtesy is not None:
        if a.courtesy and ever_invoiced:
            fail("This item has been invoiced, so it can't become a courtesy.")
        r["complimentary"] = a.courtesy
        if a.courtesy:
            r["quote"], r["invoice"] = "n/a", "n/a"
        changes.append("courtesy" if a.courtesy else "no longer courtesy")

    # 4. Approval
    if a.approved_by or a.evidence:
        r["approval"] = {"by": a.approved_by or r.get("approval", {}).get("by", ""),
                         "evidence": a.evidence or r.get("approval", {}).get("evidence", ""), "date": today()}
        changes.append(f"approval {r['approval']['by']} ({r['approval']['evidence']})")
    if r["quote"] == "accepted" and not was_accepted:
        r["approved_amount"] = r.get("cost")  # the price the client agreed to
        if r.get("cost") is None:
            out("Note: accepted without a price. Add one (--hours/--fixed) and record the client's OK for it.")

    # 5. Rules on the final state (nothing is saved if one fails)
    if r["verdict"] == "grey" and r["quote"] == "accepted":
        fail(f"#{r['id']} is still a grey area. Settle it with the client first: update {r['id']} --verdict extra "
             "--reason \"...\" (or included), then record the acceptance.")
    if r.get("complimentary") and (r["quote"] not in ("n/a",) or r["invoice"] not in ("n/a",)):
        fail("A courtesy item has no quote or invoice. Use --no-courtesy first if it should be charged.")
    if not extra_like(r) and not r.get("complimentary") and (r["quote"] != "n/a" or r["invoice"] != "n/a"):
        fail(f"#{r['id']} is {r['verdict']} work, covered by the agreed price, so it has no quote or invoice.")
    invoice_changing = a.invoice is not None or a.status is not None or a.verdict is not None
    if r.get("invoice") in INVOICED and invoice_changing:
        if not billable(r):
            fail("Courtesy, included and in-limit revision work can't be invoiced.")
        if r.get("quote") != "accepted":
            fail("Only accepted work can be invoiced. Set --quote accepted first (and record who approved it).")
        if r.get("work") != "done" and not a.advance:
            fail("The work isn't marked done. Invoice after --work done, or add --advance for an agreed upfront invoice.")
    if r["quote"] == "accepted" and not (r.get("approval") or {}).get("by"):
        out("Note: record how the client approved it (--approved-by, --evidence) so there's proof later.")
    if a.note:
        r["note"] = a.note
    if changes:
        log_event(led, "request updated", f"#{r['id']}: " + "; ".join(changes))
    st.save(led)
    c = f" {money(Decimal(r['cost']), cur)}" if r.get("cost") else ""
    out(f"#{r['id']}{c}: {r['verdict']}, quote {r['quote']}, work {r['work']}, invoice {r['invoice']}"
        + (", courtesy" if r.get("complimentary") else "") + f": {r['request']}")
    return 0


def cmd_change_order(a, st: Store) -> int:
    led = st.load(a.client, a.project)
    if len(set(a.ids)) != len(a.ids):
        fail("The same request is listed more than once.")
    items = [find(led, i) for i in a.ids]
    problems = []
    for r in items:
        tag = f"#{r['id']}"
        if r["verdict"] == "grey":
            problems.append(f"{tag} is a grey area: settle it with the client first, then "
                            f"`update {r['id']} --verdict extra --reason \"...\"`")
        elif not is_extra(r):
            problems.append(f"{tag} isn't extra work ({r['verdict']})")
        elif r.get("complimentary"):
            problems.append(f"{tag} is a courtesy, so it isn't charged")
        elif r["quote"] in ("rejected", "expired"):
            problems.append(f"{tag} was {r['quote']}; log it again if the client wants it now")
        elif r.get("change_order"):
            problems.append(f"{tag} is already on change order {r['change_order']}")
        elif r.get("absorbed_in"):
            problems.append(f"{tag} is already part of agreement v{r['absorbed_in']}")
        elif r.get("invoice") in INVOICED:
            problems.append(f"{tag} is already invoiced")
        elif not r.get("cost"):
            problems.append(f"{tag} has no price yet (update {r['id']} --hours N or --fixed AMOUNT)")
    if problems:
        fail("Can't make this change order: " + "; ".join(problems) + ".")
    cur = led["currency"]
    agr = current_agreement(led)
    number = len(led["change_orders"]) + 1
    total = sum((Decimal(r["cost"]) for r in items), Decimal(0))
    lines = [f"# Change order {number}: {md(led['project'])}", "",
             f"- **Client:** {md(led['client'])}",
             f"- **Date:** {today()}",
             f"- **Based on:** agreement version {agr['version']}" + (f", {md(agr['source'])}" if agr.get("source") else ""),
             "", "| # | Additional work | Why it's outside the agreement | Price | Approval |", "|---|---|---|---|---|"]
    for r in items:
        why = md(r.get("clause") or "Not covered by the agreement")
        lines.append(f"| {r['id']} | {md(r['request'])} | {why} | {money(Decimal(r['cost']), cur)} | {approval_text(r)} |")
    lines += ["", f"**Total:** {money(total, cur)}"]
    if a.timeline:
        lines.append(f"**Timeline impact:** {md(a.timeline)}")
    if agr.get("price"):
        others = sum((Decimal(r["cost"]) for r in led["requests"]
                      if counts_as_accepted(r) and not r.get("absorbed_in")
                      and r.get("cost") and r["id"] not in a.ids), Decimal(0))
        lines.append(f"**Project total with these items:** {money(Decimal(agr['price']) + others + total, cur)}")
    approved = [r for r in items if r["quote"] == "accepted" and (r.get("approval") or {}).get("by")]
    lines += ["", "Everything else in the agreement stays the same."]
    if len(approved) == len(items):
        lines += ["", "Each item above was approved individually, as shown in the Approval column.", ""]
    else:
        pending = [f"#{r['id']}" for r in items if r not in approved]
        lines += [f"Work on {', '.join(pending)} starts once approved below.", "",
                  f"**Approval of {', '.join(pending)}:** ______________________   **Date:** ____________", ""]
    text = "\n".join(lines)
    if a.out:  # write the document first: if that fails, the record stays unchanged
        try:
            Path(a.out).write_text(text, encoding="utf-8")
        except OSError as e:
            fail(f"Couldn't write {a.out} ({e.strerror}). Nothing was recorded.")
    led["change_orders"].append({"number": number, "date": today(), "items": list(a.ids), "total": str(total),
                                 "agreement_version": agr["version"]})
    for r in items:
        r["change_order"] = number
        if r["quote"] == "draft":
            r["quote"] = "sent"
    log_event(led, "change order", f"CO {number}: {', '.join('#' + str(i) for i in a.ids)}, {money(total, cur)}")
    st.save(led)
    if a.out:
        out(f"Change order saved to {a.out}")
    out(text)
    return 0


def build_report(led: dict) -> str:
    cur = led["currency"]
    t = totals(led)
    m, c = t["money"], t["counts"]
    agr = current_agreement(led)
    price = Decimal(agr["price"]) if agr.get("price") else None
    lines = [f"# Scope report: {md(led['client'])} / {md(led['project'])}", ""]
    lines.append(f"- Rate: {money(Decimal(led['rate']), cur)}/{led['rate_unit']}"
                 + (f" | Agreed price (v{agr['version']}): {money(price, cur)}" if price is not None else ""))
    used = len(led["rounds"])
    last_cap = led["rounds"][-1].get("cap") if led["rounds"] else agr.get("revisions_included")
    over = sum(1 for r in led["rounds"] if r.get("cap") is not None and r["round"] > r["cap"])
    lines.append(f"- Revision rounds: {used}" + (f" of {last_cap} included" if last_cap is not None else " (no limit agreed)")
                 + (f" - **{over} over the limit**" if over else ""))
    if led["agreement"]:
        lines += ["", "## Agreement history", "", "| Version | Status | Effective | Note | Approved by | Price | Rounds |",
                  "|---|---|---|---|---|---|---|"]
        for v in led["agreement"]:
            pv = money(Decimal(v["price"]), cur) if v.get("price") else "-"
            status = v.get("status", "agreed") + (" (governs)" if v["version"] == agr["version"] else "")
            lines.append(f"| {v['version']} | {status} | {md(v.get('effective', ''))} | {md(v.get('note', ''))} | "
                         f"{md(v.get('approved_by')) or '-'} | {pv} | {v.get('revisions_included', '-')} |")
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
                     + "). Run `explain` for the item-by-item calculation.")
    if m["complimentary"] > 0:
        pct = f" ({(m['complimentary'] / price * 100).quantize(Decimal('1'))}% of the agreed price)" if price else ""
        lines.append(f"\n**Given away so far:** {money(m['complimentary'], cur)}{pct}.")
    flags = review_flags(led)
    if flags:
        lines += ["", "## Needs review", ""] + [f"- {md(f)}" for f in flags]
    lines += ["", "## Change log", "",
              "| # | Date | Request | Verdict | Agreement | Quote | Work | Invoice | Value | Based on |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in led["requests"]:
        val = money(Decimal(r["cost"]), cur) if r.get("cost") else "-"
        verdict = r["verdict"] + (f" (round {r['round']}{', over limit' if r.get('over_cap') else ''})" if r.get("round") else "")
        if r.get("complimentary"):
            verdict += ", courtesy"
        if r.get("change_order"):
            verdict += f", CO {r['change_order']}"
        clause = md(r.get("clause") or "-")
        clause = clause if len(clause) <= 70 else clause[:67] + "..."
        lines.append(f"| {r['id']} | {r['date'][:10]} | {md(r['request'])} | {verdict} | "
                     f"v{r.get('agreement_version', '-')} | {r['quote']} | {r['work']} | {r['invoice']} | {val} | {clause} |")
    return "\n".join(lines) + "\n"


def cmd_report(a, st: Store) -> int:
    text = build_report(st.load(a.client, a.project))
    if a.out:
        try:
            Path(a.out).write_text(text, encoding="utf-8")
        except OSError as e:
            fail(f"Couldn't write {a.out} ({e.strerror}).")
        out(f"Report saved to {a.out}")
    out(text)
    return 0


def cmd_explain(a, st: Store) -> int:
    """Show exactly how each total is built from the recorded items."""
    led = st.load(a.client, a.project)
    cur = led["currency"]
    agr = current_agreement(led)
    zero = Decimal(0)
    out(f"How the totals for {led['client']} / {led['project']} are calculated")
    out("")
    price = Decimal(agr["price"]) if agr.get("price") else None
    out(f"Agreed price: {money(price, cur) if price is not None else 'not recorded'} (agreement v{agr['version']}"
        + (f", {agr.get('note')}" if agr.get("note") else "") + ")")
    on_top = [r for r in led["requests"] if counts_as_accepted(r) and not r.get("absorbed_in") and r.get("cost")]
    absorbed = [r for r in led["requests"] if r.get("absorbed_in")]
    for r in on_top:
        out(f"  + #{r['id']} {r['request']}: {money(Decimal(r['cost']), cur)} ({r.get('priced_as', '')}; approved by "
            f"{(r.get('approval') or {}).get('by') or 'not recorded'})")
    for r in absorbed:
        out(f"  (#{r['id']} {r['request']} is already inside the v{r['absorbed_in']} price, not added again)")
    total = (price or zero) + sum((Decimal(r["cost"]) for r in on_top), zero)
    out(f"  = Project total: {money(total, cur)}")
    out("")
    paid = [r for r in on_top + absorbed if r.get("invoice") == "paid"]
    billed = [r for r in on_top + absorbed if r.get("invoice") in ("invoiced", "partially_paid", "overdue")]
    unbilled = [r for r in on_top if r.get("invoice") in ("not_invoiced",)]
    for label, rows in (("Paid", paid), ("Invoiced, not fully paid", billed), ("Accepted, not invoiced yet", unbilled)):
        s = sum((Decimal(r["cost"]) for r in rows if r.get("cost")), zero)
        out(f"{label}: {money(s, cur)}" + (f"  ({', '.join('#' + str(r['id']) for r in rows)})" if rows else ""))
    free = [r for r in led["requests"] if r.get("complimentary") and r.get("cost")]
    if free:
        s = sum((Decimal(r["cost"]) for r in free), zero)
        out(f"Given away as courtesy: {money(s, cur)}  ({', '.join('#' + str(r['id']) for r in free)})")
    left_out = [r for r in led["requests"] if billable(r) and not counts_as_accepted(r)]
    if left_out:
        out("Not counted in the total (not accepted): "
            + ", ".join(f"#{r['id']} ({r['verdict']}, {r['quote']})" for r in left_out))
    flags = review_flags(led)
    if flags:
        out("")
        out("Needs review:")
        for f in flags:
            out(f"  - {f}")
    return 0


def cmd_history(a, st: Store) -> int:
    led = st.load(a.client, a.project)
    out(f"History for {led['client']} / {led['project']}")
    for e in led.get("events", []):
        out(f"{e['at'].replace('T', ' ')}  {e['event']}: {e['detail']}")
    if not led.get("events"):
        out("No events recorded yet.")
    return 0


def cmd_list(a, st: Store) -> int:
    files = sorted(st.dir.glob("*/*.json")) if st.dir.is_dir() else []
    legacy = sorted(st.dir.glob("*.json")) if st.dir.is_dir() else []
    if not files and not legacy:
        out(f"No clients yet in {st.dir}.")
        return 0
    for f in files:
        try:
            led = upgrade_v2(json.loads(f.read_text(encoding="utf-8")))
            validate(led, str(f))
            t = totals(led)
            cur = led["currency"]
            out(f"{led['client']} / {led['project']}: {len(led['requests'])} request(s) | accepted "
                f"{money(t['money']['accepted'], cur)} | waiting {money(t['money']['sent'], cur)} | "
                f"given away {money(t['money']['complimentary'], cur)}")
        except (json.JSONDecodeError, KeyError, SystemExit):
            out(f"{f}: damaged, see `restore`")
    for f in legacy:
        out(f"{f.stem}: old-format record, upgraded automatically the next time it's used")
    return 0


def cmd_restore(a, st: Store) -> int:
    p = st.path(a.client, a.project)
    bak = p.with_suffix(".json.bak")
    with Lock(p):
        if not bak.is_file():
            fail(f"No backup found at {bak}.")
        try:
            restored = json.loads(bak.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            fail(f"The backup {bak} is damaged too. Check the *.reset-*/*.damaged-* copies in {p.parent}.")
        restored = upgrade_v2(restored) if restored.get("schema") == SCHEMA else migrate_v1(restored, a.project)
        validate(restored, str(bak))
        current_rev = 0
        if p.is_file():
            try:
                current_rev = int(json.loads(p.read_text(encoding="utf-8")).get("rev", 0))
            except (json.JSONDecodeError, ValueError, TypeError):
                current_rev = 0
            shutil.copy2(p, p.with_name(f"{p.stem}.damaged-{dt.datetime.now():%Y%m%d-%H%M%S}.json.bak"))
        # A higher revision than anything in flight, so a command that loaded the old state can't overwrite this.
        restored["rev"] = max(current_rev, int(restored.get("rev", 0))) + 1
        restored["updated"] = now()
        log_event(restored, "restored", "went back to the state before the last change")
        atomic_write(p, restored)
    out(f"Restored {p} from the last good copy (the replaced file was kept alongside it).")
    return 0


def cmd_quote(a, st: Store) -> int:
    if a.hours is None and a.days is None:
        fail("Give --hours or --days.")
    led = {"rate": str(dec(a.rate, "--rate")), "rate_unit": a.unit, "currency": a.currency.upper(),
           "hours_per_day": hours_per_day(a.hours_per_day)}
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
    p.add_argument("--revisions")
    p.add_argument("--hours-per-day", default="8")
    p.add_argument("--force", action="store_true", help="start over (the old record is backed up first)")
    for name in ("agreement", "baseline"):
        p = sub.add_parser(name)
        client(p)
        p.add_argument("--file")
        p.add_argument("--approve", type=int, metavar="N", help="record that the client agreed to proposed amendment N")
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
    p.add_argument("--allow-over-cap", action="store_true", help="record an over-limit revision as chargeable")
    priced(p)
    p = sub.add_parser("update")
    client(p)
    p.add_argument("id", type=int)
    p.add_argument("--verdict", help="settle a grey area or correct a verdict (needs --reason)")
    p.add_argument("--reason")
    p.add_argument("--quote")
    p.add_argument("--work")
    p.add_argument("--invoice")
    p.add_argument("--advance", action="store_true", help="allow invoicing before the work is done (agreed upfront)")
    p.add_argument("--correction", action="store_true", help="move an invoice state backwards to fix a mistake (needs --reason)")
    p.add_argument("--reapproved-by", help="the client already agreed to the new price")
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
    for name in ("explain", "history", "restore"):
        client(sub.add_parser(name))
    sub.add_parser("list")
    p = sub.add_parser("quote")
    p.add_argument("--rate", required=True)
    p.add_argument("--unit", default="hour", choices=UNITS)
    p.add_argument("--currency", default="USD")
    p.add_argument("--hours")
    p.add_argument("--days")
    p.add_argument("--buffer")
    p.add_argument("--hours-per-day", default="8")

    a = ap.parse_args(argv)
    st = Store(a.data_dir)
    handlers = {"init": cmd_init, "agreement": cmd_agreement, "baseline": cmd_agreement, "round": cmd_round,
                "log": cmd_log, "update": cmd_update, "change-order": cmd_change_order, "report": cmd_report,
                "explain": cmd_explain, "history": cmd_history, "list": cmd_list, "restore": cmd_restore,
                "quote": cmd_quote}
    return handlers[a.cmd](a, st)


if __name__ == "__main__":
    sys.exit(main())
