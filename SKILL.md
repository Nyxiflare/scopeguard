---
name: scopeguard
description: Protects freelancers and agencies from scope creep. It reads the actual agreement (contract, proposal, quote, SOW, email or chat where the job was agreed), turns it into a clear scope baseline, checks each new client request against it (included / grey area / extra, quoting the exact line), prices the extra work at the user's own rate, drafts a polite change-request reply, and keeps a running change log per client showing approved extras, pending quotes and work given away for free. It also checks a proposal for scope loopholes before it is sent. Use this whenever a freelancer, consultant, designer, developer, video editor, writer, marketer, photographer, UGC creator or agency mentions a client asking for "just one more" thing, extra revisions, "small tweaks", work that wasn't agreed, how to charge for changes, a change order, revision limits, or wants a proposal or contract checked, even if they never say "scope creep".
---

# ScopeGuard

Freelancers rarely lose money on one big request. They lose it on ten small ones they never priced.
ScopeGuard makes the boundary visible: what was agreed, what is new, what it costs, and how to say so
without damaging the relationship.

The judgement (reading agreements, classifying requests, writing replies) is yours. The money math
and the record keeping go through `scripts/scope_ledger.py` so totals are exact and the history
survives between conversations. Paths are relative to this skill's folder; run the script from the
user's working folder so their records land in `./scopeguard-data/`.

| Command | Use it to |
|---|---|
| `init CLIENT --rate 60 [--unit hour\|day] [--currency USD] [--price 1500] [--revisions 2] [--project NAME]` | Start a client record |
| `baseline CLIENT --file baseline.json` | Save the agreed scope you extracted |
| `log CLIENT --request "..." --verdict extra --clause "..." --hours 3 [--buffer 15]` | Record a request with its verdict and price |
| `update CLIENT ID --status approved\|free\|declined\|done\|pending` | Record what happened next |
| `round CLIENT` | Count a revision round and compare it with the cap |
| `report CLIENT [--out report.md]` | Totals, new project total, money given away, change log |
| `quote --rate 60 --hours 3 [--buffer 15]` | One-off price without a record |
| `list` | All clients at a glance |

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
baseline makes every later verdict wrong. Then save it:

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

If there's no record yet, `init` it first (price and revision cap can go in either `init` or the
baseline file; the baseline wins). Ask for the user's rate if you don't know it. Never invent
a rate: a made-up number in a client message is worse than none.

## 2. Classify every request

Split the client's message into separate asks first. "Can you add a pricing page and also make the
header blue?" is two requests with possibly different verdicts.

For each one, decide:

| Verdict | Meaning | Typical signs |
|---|---|---|
| **Included** | The agreement covers it | Listed deliverable, within quantity, a fix of your own mistake |
| **Revision** | A change to something already in scope and not yet locked | Adjusting colour, copy, timing, layout of an agreed item. Counts toward the round cap |
| **Grey area** | The agreement is silent or ambiguous | Not listed, not excluded; vague wording like "website" with no page count |
| **Extra** | Clearly beyond the agreement | New deliverable, more than the agreed quantity, new format/size/platform, reversing an approved and locked decision, rework caused by the client's late or changed inputs, rounds beyond the cap |

Rules that keep this fair:
- **Quote the evidence.** Every verdict cites the exact line it's based on ("Section 3: *includes 3
  concepts*"), or says plainly "the agreement doesn't mention this". This is what makes the reply
  defensible, and it's the difference between ScopeGuard and a guess.
- **Silence means grey, not extra.** Don't stretch the agreement to charge for something it never
  addressed. Grey items get a clarifying question and, if the user wants, a price for the record.
- **Be honest when the client is right.** Bugs and defects in the user's own work, or things clearly
  listed, are included even if they're annoying. Saying so builds trust and keeps the user credible
  when they do push back.
- **Revision vs new:** changing something that exists and was agreed is a revision; adding,
  replacing, or reversing something approved is new work.
- **Counting rounds:** one batch of feedback is one round, however many revision items it holds.
  Run `round` once per client message that contains revisions (before logging them, so each
  revision is tagged with its round), unless the agreement defines a round differently. If the
  agreement doesn't define a round, note that as a gap. Notes the client sends separately a day
  later may count as a new round; flag that to the user rather than deciding it silently.
- **Fix or revision?** A defect in the user's own work (broken link, squished letter at small
  sizes, typo they introduced) is *included* and doesn't use up a round. A change of taste
  ("softer pink") is a revision.
- When a revision round goes past the cap, treat the overflow as extra.

## 3. Price the extra work

For each extra (and each grey item the user wants priced), estimate the time with a one-line reason
("~3h: two new layouts plus export"). Build the estimate from the parts: setup and understanding the
request, the actual work, one internal review, export/delivery, and client back-and-forth. Small
asks rarely take under an hour once all of that is counted. If the user has done similar work
before, ask how long it took; their history beats your guess. Give a range if it's genuinely
uncertain and use `--buffer 10`–`25` rather than a falsely precise number. Then log it, which also
calculates the cost:

```bash
python scripts/scope_ledger.py log "Acme" --request "Business card design" --verdict extra \
  --clause "Exclusions: business cards" --hours 2
```

Also state the **timeline impact** (extra days, or what moves). Clients accept a price more easily
when they can see the trade-off.

## 4. Draft the reply

Write one message that covers every item, in the user's voice. Pick the tone from
`references/reply-playbook.md` based on the relationship and the size of the ask. The core moves:

- Open warmly and stay on the same side as the client ("Happy to help with this").
- Confirm what's included and will just get done.
- For extras: what it involves, the price, the timeline impact, and a clear choice ("go ahead" or
  "keep to the original plan"). A choice reads as service; a refusal reads as conflict.
- For grey items: one specific question, not an accusation.
- For a revision cap reached: name the round number factually and offer the next round's price.
- Small, loyal, first-time extras can be done as a named favour ("happy to include this one; it's
  outside our scope, so just flagging it"). Log those as `free` so the record stays honest.

Never send anything yourself: drafts only. The user copies and sends.

## 5. Keep the record

After the user decides or the client answers, update the status (`approved`, `free`, `declined`,
`done`). Offer `report` when there are several items or the user asks how things stand. When the
"given away" total gets meaningful, say so and offer a "let's regroup on scope" message from the
playbook. That one message often recovers more money than any single quote.

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
- Client records stay on the user's machine in `./scopeguard-data/`. Suggest adding that folder to
  `.gitignore` if the working folder is a public repo, since it holds client names and prices.
