# ScopeGuard v2.1: improvement audit for v3

- **Date:** 2026-11-21
- **Code audited:** `main` at `6ee2b7b` (v2.1)
- **Method:**
  - Read `scripts/scope_ledger.py`, `SKILL.md`, `references/` and `evals/` directly.
  - Reproduced each suspected defect with a probe script against the real ledger, in a temporary data folder. The script isn't part of the repo.
  - No source code or tests were changed for this audit.

Every finding has one label:

| Label | Meaning |
|---|---|
| **Verified** | Reproduced against the current code; the observed output is quoted |
| **Recommendation** | Not a bug, but a gap that matters for trust or for paying customers |
| **Hypothesis** | Plausible, not yet tested |

---

## Summary

v2.1's record keeping is sound: atomic writes, version checks, locking, deep validation and 42 passing tests. The remaining risks fall in three places:

1. **The `update` command can still change money-relevant facts without the safeguards the rest of the ledger has.** A grey item can be accepted directly. An accepted price can change after approval. Invoice states can be walked backwards.
2. **Amendments govern the moment they're saved,** even when nobody has approved them.
3. **The classification output** doesn't yet separate what the agreement *says* from what Claude *infers*, and has no field for missing information.

None of these needs new features. They need tighter rules on existing commands, and one new field in the output format.

---

## Findings

### F-01 · Critical · Verified: grey items can be accepted and counted as revenue without being settled
- **Where:** `scripts/scope_ledger.py`, `cmd_update` (quote handling) and `totals`
- **Reproduce:**
  ```
  log P1 --request "Social sizes" --verdict grey --hours 2
  update P1 1 --quote accepted --approved-by X
  -> OK: #1 $100: grey, quote accepted ...
  totals: accepted = 100
  ```
- **Expected:** a grey item can't move to `accepted` until it's reclassified as extra with a reason. This is the same rule `change-order` already enforces.
- **Actual:** it's accepted and counted in "Accepted by the client (billable)" and in the project total. The v2.1 grey-area protection only covers change orders.
- **Fix:** in `cmd_update`, refuse `--quote accepted` (and `--status approved`) while `verdict == "grey"`, pointing to `--verdict extra --reason`. Add the same rule to `validate()` so a hand-edited record is caught too.
- **Regression test:** a grey item refuses `--quote accepted`; after reclassification it's allowed; a record holding a grey + accepted item fails validation.
- **Before a paid launch:** yes.

### F-02 · High · Verified: an accepted extra can be repriced, and the old approval still appears to cover the new price
- **Where:** `cmd_update`, the price branch (`--hours/--days/--fixed`)
- **Reproduce:** accept #1 at $150 (approved by Priya), then `update P2 1 --hours 5` gives `$250, quote accepted, approval {'by': 'Priya'}`.
- **Expected:** the client approved $150. A new price needs a new approval, or at least has to be flagged.
- **Actual:** the price changes silently, and the approval now appears to cover $250. This is exactly the "accepted for €150, recorded €180" case the review warns about.
- **Fix:** store `approved_amount` when the quote becomes `accepted`. Repricing an accepted item sets the quote back to `sent` and clears the approval, unless `--reapproved-by` is given. Let `explain` and `report` flag any item where `cost != approved_amount`.
- **Regression test:** repricing an accepted item moves it to `sent` and drops it from the accepted total; `explain` shows the mismatch for records edited by hand.
- **Before a paid launch:** yes.

### F-03 · High · Verified: invoice history can be rewound and the item repriced
- **Where:** `cmd_update`, invoice and price handling
- **Reproduce:** set the invoice to `paid`, then `--invoice not_invoiced` (OK), then `--hours 9` (OK). The cost goes from $150 to $450 and only the event log shows it.
- **Expected:** once an item has been invoiced, its price is frozen for good. Going back to an earlier invoice state is an explicit, logged correction.
- **Actual:** the freeze only applies while the item is *currently* invoiced.
- **Fix:** record `ever_invoiced` (or the invoiced amount). Refuse backward invoice moves (paid → not_invoiced, invoiced → not_invoiced) unless `--correction --reason` is given. Keep the price frozen after the first invoice; corrections become a new adjustment item.
- **Regression test:** a backward move without `--correction` is refused; repricing after any invoice is refused; a correction is logged with its reason.
- **Before a paid launch:** yes.

### F-04 · Medium · Verified: reclassifying an accepted extra loses or keeps the wrong state
- **Where:** `cmd_update`, the `--verdict` branch
- **Reproduce:**
  - Accepted extra → `--verdict included`: the cost and quote are wiped, while the approval stays on record.
  - Accepted extra → `--verdict grey`: the quote stays `accepted`, so the $150 is still counted as accepted revenue (same root cause as F-01).
- **Expected:** you can't change the verdict of an accepted item without first undoing the acceptance. Any reclassification keeps the old price in the note.
- **Fix:** refuse `--verdict` changes when `quote == accepted` unless `--quote` is reset in the same command. Write the old cost and approval into the reclassification note.
- **Regression test:** both reproduction paths are refused; the allowed path keeps the history.
- **Before a paid launch:** yes (cheap, and it shares F-01's fix).

### F-05 · High · Verified: unapproved amendments govern immediately
- **Where:** `cmd_agreement`, `cmd_round`, `cmd_log`
- **Reproduce:** with a 2-round agreement, save an amendment with `revisions_included: 5` and no `--approved-by` (just a note warning), then `round`. Result: `Revision round 1 of 5 recorded`.
- **Expected:** a proposed amendment doesn't change what governs the project until the client agrees.
- **Actual:** any saved version becomes the current agreement right away. A freelancer drafting "what if we added 3 rounds" would change all later verdicts.
- **Fix:**
  - Give agreement versions a status: `proposed` or `agreed`.
  - Amendments are `proposed` unless `--approved-by` or `--evidence` is given, plus an `agreement CLIENT --approve N` subcommand.
  - `current_agreement()` returns the latest *agreed* version.
  - The report lists proposed versions separately.
- **Regression test:** a proposed amendment doesn't change caps or verdicts; approving it does; history keeps both.
- **Before a paid launch:** yes.

### F-06 · Medium · Verified: line items don't add up to the displayed total
- **Where:** `cost_of` (stores unrounded costs), `totals`, `cmd_explain`
- **Reproduce:** rate $33.3333, three 1-hour extras accepted. `explain` lists three lines of $33.33 (= $99.99), plus $100 agreed, and shows **"= Project total: $200"**.
- **Expected:** every displayed line adds up to the displayed total. The amount a client is quoted is the amount stored.
- **Fix:** round each cost to the currency's minor units when it's recorded (using `minor_units()`) and store the rounded value. Keep the unrounded estimate in `priced_as` if wanted.
- **Regression test:** for awkward rates in USD, JPY and KWD, the sum of the displayed lines equals the displayed total.
- **Before a paid launch:** yes (money shown to clients).

### F-07 · Medium · Verified: a crash leaves a lock that blocks the record for 10 minutes
- **Where:** class `Lock`
- **Reproduce:** leave a `main.json.lock` behind (as a crashed process would), then any write gives: "Another ScopeGuard command is writing this record … Try again in a moment." It's refused after 5 s, and keeps being refused until the lock is 10 minutes old.
- **Expected:** a lock from a dead process is detected quickly, or the message says exactly how to clear it.
- **Fix:** write the PID and timestamp into the lock. On contention, if that PID no longer runs, take the lock over. Otherwise tell the user which file to delete if they're sure nothing else is running. Lower the stale age to about 60 s.
- **Regression test:** a lock holding a dead PID is taken over at once; a live lock is still respected.
- **Before a paid launch:** recommended.

### F-08 · Low · Verified: included work can be marked "accepted"
- **Where:** `cmd_update`, `validate`
- **Reproduce:** `log … --verdict included`, then `update 1 --quote accepted` (OK). The record is inconsistent, though it doesn't affect money.
- **Fix:** quote and invoice states other than `n/a` are only allowed on extra, grey and over-limit items. Enforce this in both `cmd_update` and `validate`.
- **Before a paid launch:** no, but it's a one-line fix alongside F-01.

### F-09 · Low · Verified: agreement `effective` dates aren't validated
- **Where:** `cmd_agreement`
- **Reproduce:** `{"effective": "not a date"}` is saved as-is. A future date is also treated as in force now.
- **Fix:** parse ISO dates. Warn about future dates and treat them as not yet in force (this fits F-05's proposed/agreed status).
- **Before a paid launch:** no.

### F-10 · Low · Verified: `explain` leaves out unaccepted over-limit revisions
- **Where:** `cmd_explain`, the `left_out` filter (it only checks `verdict in ("extra", "grey")`)
- **Reproduce:** an over-limit chargeable revision in draft appears in no line of `explain`.
- **Fix:** use `billable()` for the "not counted" list.
- **Before a paid launch:** no.

### F-11 · Medium · Verified (by design): there's no way to undo a restore
- **Where:** `cmd_restore`
- **Observed:** `restore` replaces the record with `.bak` and keeps the replaced file as `*.damaged-*.json.bak`. A second `restore` does the same again. Nothing lists the backup copies or puts a chosen one back, so undoing an accidental restore means renaming files by hand.
- **Fix:** add `backups CLIENT` (list copies with time, rev and request count) and `restore CLIENT --from FILE`. The existing safeguards (lock, rev bump, validation) carry over.
- **Before a paid launch:** recommended.

### F-12 · High · Recommendation: payments are states, not amounts
- **Where:** request schema; `INVOICE` states
- **Observed:** `partially_paid` exists, but no amount, invoice reference or date is stored. Outstanding balances, duplicate invoice numbers, tax and discounts can't be tracked or checked.
- **Recommendation:** keep this small.
  - Add optional `invoice_ref`, `invoiced_amount`, `paid_amount` and `due_date` per item. Reject a reused `invoice_ref` across items unless it's deliberately shared with `--same-invoice`.
  - `explain` reports outstanding = invoiced − paid, and flags paid > invoiced or invoiced ≠ accepted price.
  - Leave tax as a single optional percentage applied at display time. Don't build an invoicing system.
- **Before a paid launch:** amounts and refs yes; tax optional.

### F-13 · High · Recommendation: verdicts don't separate quoted evidence from inference
- **Where:** `SKILL.md`, steps 1–2 and the output format (columns: # · Request · Verdict · Based on · Est. · Price)
- **Observed:**
  - The skill asks for the "exact line", but the saved baseline holds Claude's paraphrased lists.
  - The output has no field for **missing information** or for a **contradiction** between clauses.
  - There's no rule for clauses that conflict, or for agreement text that's only described.
- **Recommendation:**
  - Store each scope item with its verbatim quote and location (`{"item": "3 logo concepts", "quote": "3 initial logo concepts", "where": "What you get"}`).
  - Add **"Missing information"** and **"Agreement says / ScopeGuard infers"** fields to the request-check output, without a numeric confidence score, which suggests false precision.
  - Add rules for contradictory clauses (flag both, verdict grey, ask) and for described-only agreements (every verdict provisional).
  - Together these are the "clause-level evidence report" the review proposes, built from fields that already exist.
- **Before a paid launch:** yes. This is where a false extra would come from.

### F-14 · Medium · Recommendation: no export or version comparison
- **Observed:** there are no `export` or `diff` commands. Records are plain JSON (portable), but a freelancer can't produce one human-readable bundle, and can't ask "what changed between v1 and v2?" without reading JSON.
- **Recommendation:**
  - `export CLIENT --out folder/`: the report, explain, history, every change order and the raw JSON.
  - `diff CLIENT 1 2`: deliverables and exclusions added or removed, price and revision changes.
  - Both are read-only, so the risk is low.
- **Before a paid launch:** export yes, diff recommended.

### F-15 · Medium · Hypothesis: crash in the middle of a write
- **Observed:** writes use temp file + `os.replace` (atomic on the same volume) after a `.bak` copy, so a crash should leave either the old or the new record. That's untested: there's no failure-injection test, such as killing the process between `copy2` and `os.replace`, or a disk-full case.
- **Recommendation:** add tests that patch `os.replace` and `shutil.copy2` to raise partway through, and assert the record still loads with no half-applied change. Also clean up leftover `*.json.tmp` files on the next load.

### F-16 · Medium · Recommendation: first-run experience and command surface
- **Observed:**
  - 14 subcommands plus aliases (`baseline`, `--status`).
  - Over-limit options split into `--courtesy` and `--allow-over-cap`.
  - Day-to-day use goes through Claude, which hides most of this. The rough edges are only seen when Claude's commands fail.
  - Error messages are specific and suggest the fix.
  - Only tested on Windows (Git Bash and PowerShell paths). macOS and Linux are untested (Hypothesis: no OS-specific code found apart from file locking via `O_EXCL`, which is portable).
- **Recommendation:**
  - Keep the CLI internal and let Claude stay the interface.
  - Add an `examples/` walkthrough that Claude can run as a demo ("show me how ScopeGuard works").
  - Run the tests on macOS and Linux with GitHub Actions on every push.
- **Before a paid launch:** CI on 3 operating systems, yes.

### F-17 · Low · Recommendation: README claims to tighten
- "Explains every number": true once F-06 is fixed. Until then the lines can be off by a cent.
- "Protects your records … safety when two commands run at once": true, apart from the F-07 stale-lock case.
- Add short sections on **supported environments** (Claude Code, and agents that read SKILL.md), **support and updates**, and how to **delete your data** (delete `scopeguard-data/<client>`).

---

## Top 10 improvements, ranked

| # | Change | Findings | Size |
|---|---|---|---|
| 1 | Grey can't be accepted; reclassification rules for accepted items | F-01, F-04, F-08 | Small |
| 2 | Proposed vs. agreed amendments | F-05, F-09 | Medium |
| 3 | Approved amount stored; repricing needs re-approval; mismatches flagged | F-02 | Small |
| 4 | Invoices one-way unless corrected; price frozen after any invoice | F-03 | Small |
| 5 | Costs rounded to currency minor units when stored | F-06 | Small |
| 6 | Evidence fields: verbatim quote, location, missing info, says vs. infers | F-13 | Medium (SKILL + schema) |
| 7 | Invoice ref and amounts, outstanding balance, duplicate-ref check | F-12 | Medium |
| 8 | `export` bundle and `diff` between versions | F-14 | Small–medium |
| 9 | Dead-process lock takeover; `backups` / `restore --from` | F-07, F-11 | Small |
| 10 | Failure-injection tests and CI on Windows, macOS and Linux | F-15, F-16 | Small |

Items 1–5 together are about a day's work and close every verified money-related defect.

---

## A 100-case classification evaluation framework

**Goal:** measure decision quality separately from code correctness.

**Mix (100 cases):**

| Group | Cases | What it tests |
|---|---|---|
| Clearly included | 15 | False extras (the costliest error) |
| Revisions within the limit | 10 | Revision vs. new deliverable |
| Revision limit reached or exceeded | 10 | Round counting, one batch = one round |
| Clearly extra | 15 | Missed extras |
| Silent or ambiguous agreement | 15 | Grey handling, no invented obligations |
| Freelancer's own mistake | 8 | Fixes stay free |
| Client changes their mind or reverses an approved decision | 7 | Locked decisions |
| Contradictory or badly written clauses | 8 | Flagging contradictions instead of picking one |
| Amendments (agreed and proposed) | 7 | Right version governs |
| No written agreement or only described | 5 | Provisional verdicts |

**Spread across:** at least 8 professions, 3 agreement formats (formal contract, proposal, chat thread), 4 currencies, and messages with 1–6 asks each.

**Building it independently:**
- Two people write each case's expected verdicts from the agreement alone, without running ScopeGuard.
- Disagreements are settled before the case is used, or the case's expected answer becomes grey.
- At least 30 cases come from anonymised real agreements that beta freelancers contribute.

**Running and scoring:**
- Each release runs all cases 3 times (to catch inconsistency) with blind verdicts first, as in the v2.1 run.
- Report per group:
  - accuracy
  - false-extra rate (target 0, never above 2%)
  - missed-extra rate
  - evidence accuracy (right clause cited, or "silent")
  - grey-when-unsure rate
  - run-to-run agreement
- A release blocks on any false extra in the "clearly included" or "freelancer's mistake" groups.

**Format:** keep `evals/cases.json`. Add `group`, `format`, `source` (synthetic or real-anonymised) and `also_accept`. Record each run in `evals/RESULTS.md` with the date, model and counts.

---

## Real-world freelancer testing plan

1. **Recruit five testers:** a graphic designer, a video editor, a web developer, a social media manager and a copywriter. Find them through freelancer communities, and pick people who currently have an active client project with a written agreement.
2. **Setup without help:** each gets only the README link. Record how long installation takes and where they get stuck. A screen recording is ideal, with their consent.
3. **Two weeks of real use:** they run ScopeGuard on one real project with anonymised details. Each client request goes through ScopeGuard before they reply.
4. **Collect for every request:**
   - ScopeGuard's verdict
   - the freelancer's own verdict, written before reading ScopeGuard's
   - whether they sent the drafted reply (as-is, edited, or not)
   - the client's response
   - money recovered or saved
5. **Check:** every disagreement becomes an eval case. Every financial total is checked against the freelancer's own calculation.
6. **Exit interview:** would they keep using it, what would they pay, and what nearly made them stop?
7. **Pass bar for v3.0:**
   - 4 of 5 install without help
   - 0 false extras that reached a client
   - no financial total disputed after checking
   - at least 3 testers who'd pay

---

## Commercial launch readiness checklist

- [ ] F-01 to F-06 fixed with regression tests
- [ ] 100-case evaluation run 3×, with false-extra rate ≤ 2% and 0 in the "included" groups
- [ ] 5 freelancer trials completed, results written up
- [ ] At least 3 paying pilot customers
- [ ] CI passing on Windows, macOS and Linux
- [ ] README: supported environments, limitations, data deletion, privacy, support policy
- [ ] Separate licence for any paid edition; the MIT edition stays as is
- [ ] Refund policy and support channel for paid users
- [ ] Export bundle available, so customers can keep their records without Claude
- [ ] No claim of legal protection anywhere; "not legal advice" visible

---

## Do not build yet

- A web dashboard or SaaS app: first find out whether talking to Claude is a barrier at all.
- A mobile app.
- Automatic email, Slack or WhatsApp sending. Drafts only is also a safety feature.
- A full invoicing or accounting system: track references and amounts only (F-12).
- Encryption of records: don't claim it until it's built and reviewed. Until then, document that records are plain local files.
- Numeric "confidence scores" on verdicts: use "says vs. infers" and "missing information" instead (F-13).
- More professions or reference packs before the 100-case evaluation shows where verdicts actually fail.
