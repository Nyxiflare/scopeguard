# ScopeGuard: a Claude skill that stops scope creep

Clients rarely ask for one big extra. They ask for ten small ones, and you never price them.

ScopeGuard reads **your actual agreement**, checks each new client request against it, prices the extra work at **your rate**, writes the reply for you, and keeps a record of every change.

> "Here's my proposal and what my client just sent. What's included and what should I charge for?"

```
| # | Request                    | Verdict             | Based on                               | Price |
|---|----------------------------|---------------------|----------------------------------------|-------|
| 1 | Softer pink                | 🔁 Revision (1 of 2) | "2 rounds of revisions"                | incl. |
| 2 | Instagram + Facebook sizes | ⚠️ Grey area         | Proposal doesn't mention social sizes  | $75 if extra |
| 3 | Try concept B with A's font| 🔁 Revision (1 of 2) | Change to the chosen concept           | incl. |
| 4 | Logo on cake-box design    | ❌ Extra             | "Not included: packaging design"       | $150  |
| 5 | Fix squished "e"           | ✅ Included          | A defect in the logo itself            | incl. |
```

…plus a friendly reply ready to send, and a running total of what's approved, waiting, and **given away for free**.

## What it does

- **Reads any agreement:** contract, proposal, quote, SOW, email or chat. It turns it into a clear scope: deliverables, revision limit, exclusions, deadlines.
- **Checks every request:** ✅ included, 🔁 revision, ⚠️ grey area, ❌ extra. **Each verdict quotes the line it's based on.**
- **Is fair:** silence in the agreement means a grey area (ask, don't charge), and fixes to your own work stay free.
- **Does the money math:** time × your rate (hourly or daily, any currency), with an optional buffer, plus the timeline impact.
- **Writes the reply:** in your voice, toned to the relationship, from a courtesy favour for a loyal client to a clear quote with a choice.
- **Keeps a change record per client:** approved extras, pending quotes, free work, revision rounds used, and the new project total.
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
- *"How much free work have I done for Acme this month?"*

Client records are saved on your machine in `./scopeguard-data/`. Nothing is sent anywhere. If that folder is inside a public repo, add it to `.gitignore`.

## Requirements

- Claude Code (or any agent that supports skills)
- Python 3.9+ (standard library only)

## Not legal advice

ScopeGuard explains what your agreement says and drafts messages. For unpaid invoices or disputes, talk to a local professional; your change log is useful evidence.

## License

MIT. See [LICENSE](LICENSE).
