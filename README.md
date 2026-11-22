# ScopeGuard: a Claude skill that stops scope creep

Clients rarely ask for one big extra. They ask for ten small ones, and you never price them.

ScopeGuard reads **your actual agreement**, checks each new client request against it, prices the extra work at **your rate**, writes the reply for you, and keeps a record of every change.

> "I'm a logo designer, I charge $50/hour. Here's my proposal and what my client just sent. What's included and what should I charge for?"

Result for the [sample job](examples/logo-project/), using the $50/hour rate the user gave:

| # | Request | Verdict | Based on | Price |
|---|---|---|---|---|
| 1 | Softer pink | 🔁 Revision (round 1 of 2) | "2 rounds of revisions on the chosen concept" | included |
| 2 | Instagram + Facebook sizes | ⚠️ Grey area | The proposal doesn't mention social sizes | $75 *if* extra (1.5h) |
| 3 | Concept B with concept A's font | 🔁 Revision (same round) | A change to the chosen concept | included |
| 4 | Logo on the cake-box design | ❌ Extra | "Not included: packaging design" | $150 (3h) |
| 5 | Fix the squished "e" | ✅ Included | A defect in the logo itself, so it's free | included |

…plus a friendly reply ready to send, an optional formal change order, and a running record of what's accepted, invoiced, paid and **given away for free**. Without a rate, ScopeGuard shows hour estimates and leaves prices blank. It never makes up a rate.

## What it does

- **Reads any agreement:** contract, proposal, quote, SOW, email or chat. It turns it into a clear scope: deliverables, revision limit, exclusions, deadlines.
- **Checks every request:** ✅ included, 🔁 revision, ⚠️ grey area, ❌ extra. **Each verdict quotes the line it's based on.**
- **Is fair:** silence in the agreement means a grey area (ask, don't charge), and fixes to your own work stay free.
- **Enforces revision limits:** one batch of feedback is one round, and changes past the agreed rounds become extra work.
- **Does the money math:** time × your rate (hourly or daily, any currency), with an optional buffer and the timeline impact.
- **Writes the reply:** in your voice, toned to the relationship, from a courtesy favour for a loyal client to a clear quote with a choice.
- **Creates change orders:** a formal document with the extra items, why each is outside the agreement, the price, the new project total, and each item's own approval. Grey areas must be settled first, and no item can be billed on two change orders.
- **Keeps the full history:**
  - several projects per client
  - versioned agreements, where amendments never overwrite the original
  - revision rounds that keep the limit they were recorded under
  - separate tracks for quote, work and invoice, so "accepted" never gets mistaken for "paid"
  - who approved each change, and where
  - an audit trail of every event (`history`)
- **Guards the money:** grey areas can't be counted as revenue until settled, a changed price needs the client's OK again, invoices only move forward (corrections are logged), invoiced prices are frozen, amendments only take effect once the client agrees, and every stored amount is rounded the way your currency is, so line items always add up.
- **Explains every number:** `explain` shows exactly which items make up the project total, what's paid, invoiced, not yet billed, and given away.
- **Protects your records:** a backup before every change, `restore`, safety when two commands run at once, checks that catch a damaged or edited record, and correct decimal places for each currency.
- **Checks proposals before you send them:** flags "unlimited revisions", "minor tweaks", "until satisfied", missing exclusions and missing change-request clauses, with replacement wording.
- **Knows your field:** designers, developers, video editors, writers, social media managers, UGC creators, photographers and consultants.

## Install

```bash
npx skills add Nyxiflare/scopeguard
```

Or copy this folder to `~/.claude/skills/scopeguard`.

## Use it

Just talk to Claude:

- *"My client wants a 4th revision and a mobile version. Here's our contract."*
- *"Check my proposal for scope loopholes before I send it."*
- *"Make a change order for the extras Acme approved."*
- *"How much free work have I done for Acme on the website project?"*

## Your data

- Client records are saved as plain JSON on your machine in `./scopeguard-data/<client>/<project>.json`, with a backup of the previous state. The ScopeGuard script makes no network requests.
- Documents you share with Claude are processed by the AI provider like any other conversation. For confidential contracts, remove details that aren't needed (bank details, personal addresses) first.
- If your working folder is a public repo, add `scopeguard-data/` to `.gitignore`.

## Known limitations

- **Verdicts are Claude's judgement, checked against your agreement.** The unit tests prove the record keeping and money math, not the classifications. Those are measured separately with the cases in `evals/`, and ScopeGuard hasn't yet been tried by real freelancers on real projects.
- It reads the agreement you give it. If the real deal lives in a call or a chat you didn't share, the verdicts can't reflect it.
- One person per record: it isn't built for a team editing the same project at once from different computers.
- No invoicing or payment integration; it tracks invoice states you record.
- Not legal advice (see below).

## Requirements

- Claude Code (or any agent that supports skills)
- Python 3.9+ (standard library only)

## Tests

```bash
python -m unittest discover -s tests
```

`evals/` holds hand-checked agreement-and-message cases with the expected verdicts, including the sample job above as the acceptance test.

## Not legal advice

ScopeGuard explains what your agreement says and drafts messages. For unpaid invoices or disputes, talk to a local professional; your change log and change orders are useful evidence.

## License

MIT. See [LICENSE](LICENSE). This open edition is free to use, change and share.
