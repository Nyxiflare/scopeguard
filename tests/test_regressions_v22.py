"""Regression tests for the v3 audit findings fixed in v2.2. Each test replays the audit's reproduction."""
import json
from decimal import Decimal
from pathlib import Path

from test_scope_ledger import Base, sl


class V22(Base):
    def setup_project(self, price="600", revisions="2", rate="50"):
        self.run_cmd("init", "Bloom", "--rate", rate, "--price", price, "--revisions", revisions)

    def accepted_box(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        self.run_cmd("update", "Bloom", "1", "--quote", "accepted", "--approved-by", "Priya", "--evidence", "WhatsApp")

    def raw(self):
        return (Path(self.d) / "bloom" / "main.json").read_bytes()

    # F-01: grey items can't be accepted until settled
    def test_f01_grey_cannot_be_accepted(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Social sizes", "--verdict", "grey", "--hours", "2")
        before = self.raw()
        self.assertIn("still a grey area", self.fails("update", "Bloom", "1", "--quote", "accepted", "--approved-by", "X"))
        self.assertIn("still a grey area", self.fails("update", "Bloom", "1", "--status", "approved"))
        self.assertEqual(self.raw(), before)
        self.run_cmd("update", "Bloom", "1", "--verdict", "extra", "--reason", "client confirmed extra")
        self.run_cmd("update", "Bloom", "1", "--quote", "accepted", "--approved-by", "X")
        self.assertEqual(sl.totals(self.rec())["money"]["accepted"], 100)

    def test_f01_hand_edited_grey_accepted_is_flagged_not_counted(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Social sizes", "--verdict", "grey", "--hours", "2")
        p = Path(self.d) / "bloom" / "main.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["requests"][0]["quote"] = "accepted"
        p.write_text(json.dumps(d), encoding="utf-8")
        rep = self.run_cmd("report", "Bloom")
        self.assertIn("Needs review", rep)
        self.assertIn("grey area but marked accepted", rep)
        self.assertIn("Project total:** $600", rep, "a grey item is never counted as revenue")

    # F-02: repricing after approval needs re-approval
    def test_f02_reprice_after_approval_goes_back_to_sent(self):
        self.accepted_box()
        out = self.run_cmd("update", "Bloom", "1", "--hours", "5")
        self.assertIn("needs the client's OK again", out)
        r = self.rec()["requests"][0]
        self.assertEqual((r["quote"], r["approval"], r["approved_amount"]), ("sent", {}, None))
        self.assertEqual(r["previous_approvals"][0]["by"], "Priya")
        self.assertEqual(Decimal(r["previous_approvals"][0]["amount"]), Decimal("150"))
        self.assertEqual(sl.totals(self.rec())["money"]["accepted"], 0)

    def test_f02_reapproved_price_is_kept(self):
        self.accepted_box()
        self.run_cmd("update", "Bloom", "1", "--hours", "5", "--reapproved-by", "Priya", "--evidence", "call 4 Dec")
        r = self.rec()["requests"][0]
        self.assertEqual(r["quote"], "accepted")
        self.assertEqual(Decimal(r["approved_amount"]), Decimal("250"))

    def test_f02_mismatch_is_flagged(self):
        self.accepted_box()
        p = Path(self.d) / "bloom" / "main.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["requests"][0]["cost"] = "180"
        p.write_text(json.dumps(d), encoding="utf-8")
        self.assertIn("approved at $150 but is recorded at $180", self.run_cmd("explain", "Bloom"))

    # F-03: invoices move one way and freeze the price
    def test_f03_invoice_cannot_be_rewound_or_repriced(self):
        self.accepted_box()
        self.run_cmd("update", "Bloom", "1", "--work", "done", "--invoice", "paid")
        self.assertIn("only move forward", self.fails("update", "Bloom", "1", "--invoice", "not_invoiced"))
        self.assertIn("needs --reason", self.fails("update", "Bloom", "1", "--invoice", "not_invoiced", "--correction"))
        self.run_cmd("update", "Bloom", "1", "--invoice", "invoiced", "--correction", "--reason", "payment bounced")
        self.assertIn("price is frozen", self.fails("update", "Bloom", "1", "--hours", "9"))
        self.run_cmd("update", "Bloom", "1", "--invoice", "not_invoiced", "--correction", "--reason", "sent by mistake")
        self.assertIn("price is frozen", self.fails("update", "Bloom", "1", "--hours", "9"),
                      "still frozen after being invoiced once")
        self.assertIn("invoice correction", self.run_cmd("history", "Bloom"))

    # F-04: an accepted item's verdict can't change without undoing the acceptance
    def test_f04_accepted_item_verdict_change(self):
        self.accepted_box()
        self.assertIn("Undo that first", self.fails("update", "Bloom", "1", "--verdict", "included", "--reason", "oops"))
        self.assertIn("Undo that first", self.fails("update", "Bloom", "1", "--verdict", "grey", "--reason", "unsure"))
        self.run_cmd("update", "Bloom", "1", "--quote", "sent")
        self.run_cmd("update", "Bloom", "1", "--verdict", "included", "--reason", "was in the proposal after all")
        self.assertIn("(was extra, $150)", self.rec()["requests"][0]["note"])

    # F-05: proposed amendments don't govern until agreed
    def test_f05_proposed_amendment_does_not_govern(self):
        self.setup_project()
        f = Path(self.d) / "a.json"
        f.write_text(json.dumps({"revisions_included": 5, "price": 900}))
        out = self.run_cmd("agreement", "Bloom", "--file", str(f), "--note", "draft idea")
        self.assertIn("PROPOSED", out)
        self.assertIn("Revision round 1 of 2", self.run_cmd("round", "Bloom"))
        rep = self.run_cmd("report", "Bloom")
        self.assertIn("Project total:** $600", rep)
        self.assertIn("is proposed, not agreed", rep)
        self.assertIn("Record who agreed", self.fails("agreement", "Bloom", "--approve", "2"))
        self.run_cmd("agreement", "Bloom", "--approve", "2", "--approved-by", "Priya", "--evidence", "email 5 Dec")
        self.assertIn("Revision round 2 of 5", self.run_cmd("round", "Bloom"))
        self.assertIn("Project total:** $900", self.run_cmd("report", "Bloom"))
        self.assertEqual(self.rec()["rounds"][0]["cap"], 2, "the earlier round keeps its limit")

    def test_f05_proposed_absorbs_apply_only_on_approval(self):
        self.accepted_box()
        f = Path(self.d) / "a.json"
        f.write_text(json.dumps({"deliverables": ["3 concepts", "Box"], "price": 750}))
        self.run_cmd("agreement", "Bloom", "--file", str(f), "--note", "Add box", "--absorbs", "1")
        self.assertIsNone(self.rec()["requests"][0].get("absorbed_in"))
        self.assertIn("Project total:** $750", self.run_cmd("report", "Bloom"))
        self.run_cmd("agreement", "Bloom", "--approve", "2", "--approved-by", "Priya")
        self.assertEqual(self.rec()["requests"][0]["absorbed_in"], 2)
        self.assertIn("Project total:** $750", self.run_cmd("report", "Bloom"))

    def test_f05_older_proposal_cannot_be_approved_after_a_newer_agreement(self):
        self.setup_project()
        f = Path(self.d) / "a.json"
        f.write_text(json.dumps({"price": 700}))
        self.run_cmd("agreement", "Bloom", "--file", str(f), "--note", "idea A")
        self.run_cmd("agreement", "Bloom", "--file", str(f), "--note", "deal B", "--approved-by", "Priya")
        self.assertIn("can't take effect now", self.fails("agreement", "Bloom", "--approve", "2", "--approved-by", "Priya"))

    def test_f05_existing_versions_stay_agreed(self):
        """Records saved before v2.2 have no status; they keep governing as they did."""
        self.setup_project()
        f = Path(self.d) / "a.json"
        f.write_text(json.dumps({"price": 800}))
        self.run_cmd("agreement", "Bloom", "--file", str(f), "--note", "old style", "--approved-by", "Priya")
        p = Path(self.d) / "bloom" / "main.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        for v in d["agreement"]:
            v.pop("status", None)
        p.write_text(json.dumps(d), encoding="utf-8")
        self.assertIn("Project total:** $800", self.run_cmd("report", "Bloom"))

    # F-06: line items add up to the total
    def test_f06_line_items_add_up(self):
        for cur, rate in (("USD", "33.3333"), ("JPY", "333.4"), ("KWD", "10.33337")):
            client = f"Round {cur}"
            self.run_cmd("init", client, "--rate", rate, "--price", "100", "--currency", cur)
            for i in range(3):
                self.run_cmd("log", client, "--request", f"Item {i}", "--verdict", "extra", "--hours", "1")
                self.run_cmd("update", client, str(i + 1), "--quote", "accepted", "--approved-by", "A")
            r = self.rec(client)
            stored = [Decimal(x["cost"]) for x in r["requests"]]
            self.assertEqual(sum(stored), sl.totals(r)["money"]["accepted"])
            for c in stored:
                self.assertEqual(c, sl.round_money(c, cur), f"{cur} cost stored unrounded")
        self.assertIn("= Project total: $199.99", self.run_cmd("explain", "Round USD"))

    # F-08: included work has no quote or invoice
    def test_f08_included_cannot_be_accepted(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Fix", "--verdict", "included")
        self.assertIn("covered by the agreed price", self.fails("update", "Bloom", "1", "--quote", "accepted"))

    # F-09: effective dates must be dates
    def test_f09_effective_date_validated(self):
        self.setup_project()
        f = Path(self.d) / "a.json"
        f.write_text(json.dumps({"effective": "not a date"}))
        self.assertIn("must be a date", self.fails("agreement", "Bloom", "--file", str(f), "--note", "x", "--approved-by", "A"))

    # F-10: explain lists unaccepted over-limit revisions
    def test_f10_explain_lists_over_cap_drafts(self):
        self.run_cmd("init", "Bloom", "--rate", "50", "--revisions", "0")
        self.run_cmd("round", "Bloom")
        self.run_cmd("log", "Bloom", "--request", "Tweak", "--verdict", "revision", "--allow-over-cap", "--hours", "1")
        self.assertIn("#1 (revision, draft)", self.run_cmd("explain", "Bloom"))


if __name__ == "__main__":
    import unittest
    unittest.main()
