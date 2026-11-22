# ScopeGuard beta: tester guide

Thanks for testing! This takes about **15 minutes to set up**, then **about 5 minutes per client request** for 2 weeks.

Please try to install it **on your own** first. If you get stuck, write down where. That's one of the most useful things you can tell us. Stuck for more than 15 minutes? Message [contact].

> ScopeGuard explains what your agreement says and drafts messages. It's not legal advice. You decide what to send.

---

## 1. Install (about 10 minutes)

You need **Claude Code** (a paid Claude plan) and **Python 3.9+**.

1. Install Claude Code if you haven't: https://claude.com/claude-code
2. Check Python: open a terminal and run `python --version` (or `python3 --version` on macOS). If it isn't installed, get it from https://www.python.org/downloads/
3. Install ScopeGuard:
   ```
   npx skills add Nyxiflare/scopeguard
   ```
   (If `npx` isn't found, install Node.js from https://nodejs.org first.)
4. Make a folder for your ScopeGuard work (for example `Documents/scopeguard-beta`), open Claude Code in that folder, and type:
   > *Show me how ScopeGuard works using the logo example.*

   Claude should walk through the sample bakery-logo job and show a table of ✅ / 🔁 / ⚠️ / ❌ verdicts.

**Note for the log:** how long installation took, and anything confusing.

## 2. Set up your real project (about 5 minutes)

1. **Anonymise your agreement first.** Copy it into a new file, and replace:
   - client and company names → "Client A", "Company B"
   - emails, phone numbers, addresses, bank details → remove them
   - Keep the deliverables, revision limits, exclusions, deadlines and prices. That's what ScopeGuard reads.
2. In Claude Code, in your ScopeGuard folder:
   > *I'm a [your profession], I charge [rate] per [hour/day]. Here's my agreement for Client A: [paste, or give the file name]. Set up ScopeGuard for this project.*
3. Claude shows the scope it understood (deliverables, revision rounds, exclusions). **Check it carefully** and correct anything wrong. Everything depends on it.

Your records are saved on your computer in `scopeguard-data/` inside that folder. Nothing is sent to us.

## 3. Every time the client asks for something

1. Before reading ScopeGuard's answer, **write down your own verdict** in the request log (included / revision / grey / extra). This tells us whether ScopeGuard agrees with a real professional.
2. Paste the client's message (anonymised) into Claude Code:
   > *Client A just sent this: "…" What's included and what should I charge?*
3. Read ScopeGuard's verdicts and reply draft.
4. Do whatever you'd normally do: send the draft, edit it, or ignore it. Your business comes first.
5. **Add a row** to `request-log.csv` (open it in Excel, Google Sheets or Numbers).
6. When the client answers (accepts, declines, ignores), tell Claude so it updates the record, for example:
   > *Client A accepted the box design by email today.*

## 4. Filling in the request log

One row per client request. If a message has 3 asks, use 3 rows.

| Column | What to write |
|---|---|
| date | When the request came in |
| field | Your profession |
| request_summary | A short, anonymised description ("asked for a 9:16 version") |
| my_verdict | Your own call, made **before** reading ScopeGuard: included / revision / grey / extra |
| scopeguard_verdict | What ScopeGuard said |
| agree | yes / no |
| evidence_correct | Did it quote the right line of your agreement? yes / no / it said "silent" |
| price_suggested | ScopeGuard's price, if any |
| price_reasonable | yes / too high / too low |
| reply_used | sent as-is / edited / not sent |
| client_response | accepted / declined / negotiated / no reply yet |
| money_recovered | What you got paid for extra work you'd otherwise have done free |
| minutes_spent | Time you spent on this request with ScopeGuard |
| notes | Anything odd, wrong or great |

## 5. Something went wrong?

Send us:
- what you asked Claude (anonymised)
- what it said or did
- what you expected
- a screenshot, if easy

**Never send an unredacted contract or real client names.**

If ScopeGuard says your records have a problem, it suggests a fix (such as `restore`). Tell us before trying anything unusual.

## 6. At the end

We'll do a 20-minute call (or a short questionnaire if you prefer), and you send your final request log. That's it. Thank you!
