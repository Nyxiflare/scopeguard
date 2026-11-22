---
name: scopeguard
description: Protects freelancers and agencies from scope creep. It reads the actual agreement (contract, proposal, quote, SOW, email or chat where the job was agreed), turns it into a clear scope baseline, checks each new client request against it (included / grey area / extra, quoting the exact line), prices the extra work at the user's own rate, drafts a polite change-request reply, and keeps a running change log per client showing approved extras, pending quotes and work given away for free. It also checks a proposal for scope loopholes before it is sent. Use this whenever a freelancer, consultant, designer, developer, video editor, writer, marketer, photographer, UGC creator or agency mentions a client asking for "just one more" thing, extra revisions, "small tweaks", work that wasn't agreed, how to charge for changes, a change order, revision limits, or wants a proposal or contract checked, even if they never say "scope creep".
---

# ScopeGuard

Freelancers rarely lose money on one big request. They lose it on ten small ones they never priced.
ScopeGuard makes the boundary visible: what was agreed, what is new, what it costs, and how to say so
without damaging the relationship.

The judgement (reading agreements, classifying requests, writing replies) is yours. The money math
and the record keeping go through `scripts/scope_ledger.py`, so totals are exact and the history
survives between conversations.

**Running the script:** call it by its full path inside this skill's folder (the folder you were
given when this skill loaded), for example
`python "C:/Users/me/.claude/skills/scopeguard/scripts/scope_ledger.py" report "Bloom"`.
Run it *from the user's working folder* so their records land in `./scopeguard-data/` there, not
inside the skill. Below, `SG` stands for that full command.

Every record is one **client + project** (`--project NAME`, default `main`), so a client with two
jobs has two independent agreements, round counters and totals. Always pass `--project` when the
client has more than one.

| Command | Use it to |
|---|---|
| `SG init CLIENT --project P --rate 60 [--unit hour\|day] [--currency USD]` | Start a project record |
| `SG agreement CLIENT --project P --file scope.json` | Save the original agreement (version 1) |
| `SG agreement ... --file new.json --note "Added box" --approved-by NAME --evidence "email 3 Dec" [--absorbs ID]` | Add an agreed amendment as a new version (old versions are never changed) |
| `SG agreement ... --file new.json --note "Idea: 3 more rounds"` → later `SG agreement CLIENT --approve 2 --approved-by NAME --evidence "..."` | Save a *proposed* amendment, then record when the client agrees to it |
| `SG round CLIENT --project P` | Record one batch of client feedback (a revision round) |
| `SG log CLIENT --project P --request "..." --verdict extra --clause "..." --hours 3 [--buffer 15] [--courtesy]` | Record a request, its verdict, evidence and price |
| `SG update CLIENT ID --quote sent\|accepted\|rejected\|expired --work in_progress\|done --invoice invoiced\|paid\|overdue --approved-by NAME --evidence "..."` | Record what happened next |
| `SG update CLIENT ID --verdict extra --reason "client confirmed it wasn't included"` | Settle a grey area (or correct a verdict) with the reason on record |
| `SG change-order CLIENT ID [ID ...] --timeline "+2 days" --out co-1.md` | A formal change order for confirmed extras, with per-item approvals |
| `SG report CLIENT --project P [--out report.md]` | Agreement history, rounds, money by state, change log |
| `SG explain CLIENT --project P` / `SG history CLIENT --project P` | How every total is calculated / the audit trail |
| `SG list` / `SG restore CLIENT --project P` | All projects at a glance / go back to the state before the last change |
| `SG quote --rate 60 --hours 3` | One-off price without a record |

## Pick the mode

- **A. A client just asked for something.** This is the main one. Go through steps 1–5.
- **B. Check my proposal or contract before I send it.** Run the loophole check (see "Proposal check").
- **C. Set up tracking for a new project.** Steps 1 and 5 only.
- **D. "How much have I given away?" / "where are we with this client?"** Run `report`.

If you can't tell, ask one short question. Otherwise go.

## 1. Build the scope baseline from the agreement

Everything rests on this, so do it carefully and only once per project. The agreement can be any
shape: a signed contract, a PDF proposal, a quote, an email thread, a WhatsApp export, or the user
describing what was agreed. Use what exists. If there's truly nothing written, say so plainly
(that is the biggest risk) and build the baseline from the user's description, marked as unwritten.

Extract:
- **Deliverables, with quantities and formats** (e.g. "3 logo concepts", "5-page site", "one 60-second video, 16:9").
- **Revisions:** how many rounds, and what counts as one. If it's not defined, note it.
- **Exclusions:** anything stated as not included.
- **Timeline and approval gates:** deadlines, sign-off stages, what gets locked after approval.
- **Client responsibilities:** content, assets, access, feedback turnaround.
- **Price and payment terms.**
- **Silent on:** the things the agreement doesn't address that commonly cause disputes in this
  profession (see `references/professions.md`). These become grey areas later, so list them now.

Show the baseline back as a compact table and ask the user to confirm or correct it once. A wrong
baseline makes every later verdict wrong. Then save it with `SG agreement` as version 1:

```json
{
  "deliverables": ["3 logo concepts", "Final logo in SVG, PNG, PDF"],
  "revisions_included": 2,
  "revision_definition": "One round = one consolidated set of feedback",
  "exclusions": ["Brand guidelines", "Business cards"],
  "deadline": "2026-12-15",
  "payment_terms": "50% upfront, 50% on delivery",
  "client_responsibilities": ["Brand colours and references by Dec 1"],
  "approval_gates": ["Concept chosen -> locked"],
  "silent_on": ["Social media sizes", "Source files ownership"],
  "original_price": 500,
  "source": "proposal-acme-v2.pdf, section 3"
}
```

If there's no record yet, `init` it first. Ask for the user's rate if you don't know it. Never
invent a rate: a made-up number in a client message is worse than none. Without a rate, show the
hour estimates and leave prices blank.

**When the scope changes later** (the client approves an extra that becomes part of the project, or
both sides agree new terms), save the new terms with `SG agreement ... --note` as an amendment and
record who approved it and where (`--approved-by`, `--evidence`). An amendment saved without them is
only **proposed**: it's kept on record but changes no verdict, limit or total until
`SG agreement CLIENT --approve N --approved-by NAME --evidence "..."`. Use that for anything still
being negotiated. Never edit or replace the original;
the history of what was agreed, and when, is what protects the user in a dispute. Each logged
request remembers which agreement version it was judged against.

- The amendment file only needs **what changed**; anything it leaves out carries over from the
  previous version (deliverables, exclusions, rounds…). If a list changes, give the whole new list.
- `price` in an amendment is the **new total** for the project.
- If that new total already includes extras the client accepted earlier, add `--absorbs ID ...`
  so they aren't counted twice. The script warns when the price changes while accepted extras are
  still counted on top. Usually it's simpler to keep accepted extras as extras and only amend the
  agreement when the deliverables themselves change.
- New deliverables share the agreement's revision rounds unless the amendment says otherwise; say
  so in the reply if it matters.

## 2. Classify every request

Split the client's message into separate asks first. "Can you add a pricing page and also make the
header blue?" is two requests with possibly different verdicts.

For each one, decide:

| Verdict | Meaning | Typical signs |
|---|---|---|
| **Included** | The agreement covers it | Listed deliverable, within quantity, a fix of your own mistake |
| **Revision** | A change to something already in scope and not yet locked | Adjusting colour, copy, timing, layout of an agreed item. Counts toward the round cap |
| **Grey area** | The agreement is silent or ambiguous | Not listed, not excluded; vague wording like "website" with no page count |
| **Extra** | Clearly beyond the agreement | New deliverable, more than the agreed quantity, a new version of a deliverable whose format the agreement fixes (16:9 video → 9:16), reversing an approved and locked decision, rework caused by the client's late or changed inputs, rounds beyond the cap |

Rules that keep this fair:
- **Quote the evidence.** Every verdict cites the exact line it's based on ("Section 3: *includes 3
  concepts*"), or says plainly "the agreement doesn't mention this". This is what makes the reply
  defensible, and it's the difference between ScopeGuard and a guess.
- **Silence means grey, not extra.** Don't stretch the agreement to charge for something it never
  addressed. Grey items get a clarifying question and, if the user wants, a price for the record.
- **New format vs. new asset:** a different version of something the agreement pins down (a video
  agreed as 16:9, now wanted as 9:16) is extra. A new kind of asset the agreement never mentions
  (social profile images on a logo project, meta descriptions on a copy job) is grey: it's silence,
  not an exclusion, unless the exclusions list or a quantity limit covers it.
- **Be honest when the client is right.** Bugs and defects in the user's own work, or things clearly
  listed, are included even if they're annoying. Saying so builds trust and keeps the user credible
  when they do push back.
- **Revision vs new:** changing something that exists and was agreed is a revision; adding,
  replacing, or reversing something approved is new work.
- **Counting rounds:** one batch of feedback is one round, however many revision items it holds.
  Run `round` once per client message that contains revisions, *before* logging them, so each
  revision is tagged with its round (the script refuses a revision when no round is recorded),
  unless the agreement defines a round differently. If the
  agreement doesn't define a round, note that as a gap. Notes the client sends separately a day
  later may count as a new round; flag that to the user rather than deciding it silently.
- **Fix or revision?** A defect in the user's own work (broken link, squished letter at small
  sizes, typo they introduced) is *included* and doesn't use up a round. A change of taste
  ("softer pink") is a revision.
- When a revision round goes past the cap, its changes are extra. The script enforces this: it
  refuses `--verdict revision` beyond the cap, so log them as `extra` with a price, or with
  `--courtesy` if the user decides to give that round for free.

## 3. Price the extra work

Price every extra. For grey items, give a price labelled "if extra" when the user is asking what to
charge, so they know the stakes, and log it as `grey` (the report keeps it apart from real quotes).
For each one, estimate the time with a one-line reason
("~3h: two new layouts plus export"). Build the estimate from the parts: setup and understanding the
request, the actual work, one internal review, export/delivery, and client back-and-forth. Small
asks rarely take under an hour once all of that is counted. If the user has done similar work
before, ask how long it took; their history beats your guess. Give a range if it's genuinely
uncertain and use `--buffer 10`–`25` rather than a falsely precise number. Then log it, which also
calculates the cost:

```bash
SG log "Acme" --project "Brand identity" --request "Business card design" --verdict extra \
  --clause "Exclusions: business cards" --hours 2
```

Also state the **timeline impact** (extra days, or what moves). Clients accept a price more easily
when they can see the trade-off.

## 4. Draft the reply

Write one message that covers every item, in the user's voice. If the user wants something more
formal than a message (bigger extras, a new client, an agency), also generate a change order with
`SG change-order`. Usually it's generated *before* approval: it's what the client signs, and it
marks those quotes as sent. Each item shows its own approval (who, when, where) or "awaiting
approval", and only the unapproved items get a sign-off line, so one person's approval is never
presented as covering everything. Change orders only take confirmed extras: a grey item must be
settled with the client first and reclassified (`SG update ID --verdict extra --reason "..."`), and
courtesy, rejected, expired or already-ordered items are refused. Pick the tone from
`references/reply-playbook.md` based on the relationship and the size of the ask. The core moves:

- Open warmly and stay on the same side as the client ("Happy to help with this").
- Confirm what's included and will just get done.
- For extras: what it involves, the price, the timeline impact, and a clear choice ("go ahead" or
  "keep to the original plan"). A choice reads as service; a refusal reads as conflict.
- For grey items: one specific question, not an accusation.
- For a revision cap reached: name the round number factually and offer the next round's price.
- Small, loyal, first-time extras can be done as a named favour ("happy to include this one; it's
  outside our scope, so just flagging it"). Log those with `--courtesy` (keep the price, so the
  "given away" total stays honest).

Never send anything yourself: drafts only. The user copies and sends.

## 5. Keep the record

Each extra has three separate tracks, because an approved quote isn't money in the bank:

| Track | Values |
|---|---|
| Quote | draft → sent → accepted / rejected / expired |
| Work | not_started → in_progress → done |
| Invoice | not_invoiced → invoiced → partially_paid / paid / overdue |

Update them as things happen (`SG update`). When the client accepts, record who and where
(`--approved-by`, `--evidence "WhatsApp 3 Dec 10:14"`) so there's proof later. Only accepted,
chargeable work that's done can be invoiced (`--advance` for an agreed upfront invoice). A grey item
can't be marked accepted until it's settled, and included work never gets a quote. If the price of
an accepted extra changes, the quote goes back to "sent" because the client agreed to the old
number; use `--reapproved-by NAME` only if they've already agreed to the new one. Invoice states only
move forward; fixing a mistake needs `--correction --reason "..."`, and once an item has been
invoiced its price is frozen for good. `report` and `explain` list anything inconsistent under
"Needs review" instead of quietly fixing it. Offer `SG report` when there are several items or the user asks how
things stand; it shows accepted extras not yet invoiced, which is often money the user forgot to
bill. When the user asks *why* a total is what it is, run `SG explain`: it lists every item that
makes up the total. `SG history` shows the audit trail of everything recorded. When the
"given away" total gets meaningful, say so and offer the "let's regroup on scope" message from the
playbook.

## Output format for a request check

```
## Request check: [client], [date]

| # | Request | Verdict | Based on | Est. | Price |
|---|---|---|---|---|---|
| 1 | 4th logo concept | ❌ Extra | "3 concepts" (Sec. 3) | 2h | $120 |
| 2 | Make blue darker | 🔁 Revision (round 2 of 2) | "2 revision rounds" (Sec. 4) | - | included |
| 3 | Instagram sizes | ⚠️ Grey | Agreement doesn't mention social sizes | 1h | $60 if extra |

**Extras total:** $120 (+$60 if #3 is extra) · **Timeline:** +2 days

### Reply, ready to send
[the drafted message]
```

Use ✅ for included.

## Proposal check (before sending)

Read the draft proposal or contract against `references/scope-checklist.md`: vague phrases that
invite creep ("minor tweaks", "unlimited revisions", "until satisfied", "and similar", "as needed"),
missing pieces (revision cap and definition, exclusions list, change-request clause, approval gates,
client deadlines, kill/pause terms), and profession-specific gaps from `references/professions.md`.
For each problem, quote the line, explain the risk in one sentence, and give replacement wording.
Finish with the three fixes that matter most.

## Boundaries

- ScopeGuard explains what an agreement says and drafts messages. It is not legal advice. For unpaid
  invoices, threatened disputes, or anything heading to a lawyer or court, say so and suggest a
  local professional; the change log is useful evidence for them.
- Don't invent clauses, quotes, or rates. If the agreement isn't available, verdicts are provisional,
  and say that in the reply notes.
- The script makes no network requests and keeps records on the user's machine in
  `./scopeguard-data/`. The documents the user shares with you are still processed by the AI
  provider like any conversation, so for confidential contracts suggest removing details that
  aren't needed (personal addresses, bank details) before pasting. Suggest adding
  `scopeguard-data/` to `.gitignore` if the working folder is a public repo.
