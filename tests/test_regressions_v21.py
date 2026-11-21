"""Regression tests for the v2.1 hardening review. Each test names the defect it locks down."""
import json
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from test_scope_ledger import SCRIPT, Base, sl


class V21(Base):
    def setup_project(self, price="600", revisions="2"):
        self.run_cmd("init", "Bloom", "--rate", "50", "--price", price, "--revisions", revisions)

    def file_state(self):
        p = Path(self.d) / "bloom" / "main.json"
        return p.read_bytes()

    # --- change orders -----------------------------------------------------------------------
    def test_grey_needs_reclassification_before_change_order(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Social sizes", "--verdict", "grey", "--hours", "2")
        self.assertIn("grey area", self.fails("change-order", "Bloom", "1"))
        self.assertIn("--reason", self.fails("update", "Bloom", "1", "--verdict", "extra"))
        self.run_cmd("update", "Bloom", "1", "--verdict", "extra", "--reason", "client confirmed not included")
        self.assertIn("Total:** $100", self.run_cmd("change-order", "Bloom", "1"))
        self.assertIn("Reclassified grey -> extra", self.rec()["requests"][0]["note"])

    def test_complimentary_over_cap_revision_not_on_change_order(self):
        self.setup_project(revisions="1")
        self.run_cmd("round", "Bloom")
        self.run_cmd("round", "Bloom")
        self.run_cmd("log", "Bloom", "--request", "Tweak", "--verdict", "revision", "--courtesy", "--hours", "1")
        self.assertIn("courtesy", self.fails("change-order", "Bloom", "1"))

    def test_chargeable_over_cap_revision_allowed_on_change_order(self):
        self.setup_project(revisions="1")
        self.run_cmd("round", "Bloom")
        self.run_cmd("round", "Bloom")
        self.run_cmd("log", "Bloom", "--request", "Round 2 tweak", "--verdict", "revision", "--allow-over-cap", "--hours", "1")
        self.assertIn("Total:** $50", self.run_cmd("change-order", "Bloom", "1"))

    def test_duplicate_ids_rejected(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        self.assertIn("more than once", self.fails("change-order", "Bloom", "1", "1"))
        self.assertEqual(self.rec()["change_orders"], [])

    def test_rejected_and_expired_quotes_rejected(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "A", "--verdict", "extra", "--hours", "1")
        self.run_cmd("log", "Bloom", "--request", "B", "--verdict", "extra", "--hours", "1")
        self.run_cmd("update", "Bloom", "1", "--quote", "rejected")
        self.run_cmd("update", "Bloom", "2", "--quote", "expired")
        self.assertIn("was rejected", self.fails("change-order", "Bloom", "1"))
        self.assertIn("was expired", self.fails("change-order", "Bloom", "2"))

    def test_item_cannot_be_on_two_change_orders(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        self.run_cmd("change-order", "Bloom", "1")
        self.assertIn("already on change order 1", self.fails("change-order", "Bloom", "1"))
        self.assertEqual(len(self.rec()["change_orders"]), 1)

    def test_separate_approvals_stay_separate(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        self.run_cmd("log", "Bloom", "--request", "Mug", "--verdict", "extra", "--hours", "1")
        self.run_cmd("log", "Bloom", "--request", "Sign", "--verdict", "extra", "--hours", "2")
        self.run_cmd("update", "Bloom", "1", "--quote", "accepted", "--approved-by", "Priya", "--evidence", "WhatsApp")
        self.run_cmd("update", "Bloom", "2", "--quote", "accepted", "--approved-by", "Raj", "--evidence", "email")
        co = self.run_cmd("change-order", "Bloom", "1", "2", "3")
        self.assertIn("| Priya, ", co)
        self.assertIn("| Raj, ", co)
        self.assertIn("| awaiting approval |", co)
        self.assertIn("**Approval of #3:**", co)
        self.assertNotIn("#1, ", co.split("Approval of")[-1])

    def test_failed_export_records_nothing(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        before = self.file_state()
        bad = str(Path(self.d) / "no-such-folder" / "co.md")
        self.assertIn("Nothing was recorded", self.fails("change-order", "Bloom", "1", "--out", bad))
        self.assertEqual(self.file_state(), before)

    # --- absorption --------------------------------------------------------------------------
    def amend(self, *extra):
        f = Path(self.d) / "amend.json"
        f.write_text(json.dumps({"deliverables": ["3 concepts", "Box"], "price": 750}))
        return ["agreement", "Bloom", "--file", str(f), "--note", "Added box", *extra]

    def test_draft_extra_cannot_be_absorbed_and_ledger_unchanged(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        before = self.file_state()
        self.assertIn("only accepted, chargeable extras", self.fails(*self.amend("--absorbs", "1")))
        self.assertIn("No request #9", self.fails(*self.amend("--absorbs", "9")))
        self.assertEqual(self.file_state(), before, "a failed amendment must leave the record untouched")

    def test_cannot_absorb_twice_or_duplicate(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        self.run_cmd("update", "Bloom", "1", "--quote", "accepted", "--approved-by", "Priya")
        self.assertIn("more than once", self.fails(*self.amend("--absorbs", "1", "1")))
        self.run_cmd(*self.amend("--absorbs", "1"))
        self.assertIn("already part of agreement v2", self.fails(*self.amend("--absorbs", "1")))

    # --- revision rounds and amendments ------------------------------------------------------
    def test_round_history_survives_cap_change(self):
        self.setup_project(revisions="1")
        self.run_cmd("round", "Bloom")
        self.run_cmd("log", "Bloom", "--request", "Pink", "--verdict", "revision")
        f = Path(self.d) / "amend.json"
        f.write_text(json.dumps({"revisions_included": 3}))
        note = self.run_cmd("agreement", "Bloom", "--file", str(f), "--note", "More rounds",
                            "--approved-by", "Priya", "--evidence", "email")
        self.assertIn("keep the limit they had", note)
        self.run_cmd("round", "Bloom")
        self.run_cmd("log", "Bloom", "--request", "Font", "--verdict", "revision")
        r = self.rec()
        self.assertEqual([(x["agreement_version"], x["cap"]) for x in r["rounds"]], [(1, 1), (2, 3)])
        self.assertEqual(r["requests"][1]["agreement_version"], 2)
        self.assertNotIn("over the limit", self.run_cmd("report", "Bloom"))

    def test_old_round_keeps_its_agreement_version(self):
        self.setup_project(revisions="2")
        self.run_cmd("round", "Bloom")
        f = Path(self.d) / "amend.json"
        f.write_text(json.dumps({"revisions_included": 1, "price": 650}))
        self.run_cmd("agreement", "Bloom", "--file", str(f), "--note", "Fewer rounds", "--approved-by", "Priya")
        out = self.run_cmd("log", "Bloom", "--request", "Late note from round 1", "--verdict", "revision")
        self.assertIn("judged against v1", out)
        self.assertEqual(self.rec()["requests"][0]["agreement_version"], 1)

    def test_negative_revision_limit_rejected(self):
        self.fails("init", "Bloom", "--rate", "50", "--revisions", "-1")
        self.setup_project()
        f = Path(self.d) / "bad.json"
        f.write_text(json.dumps({"revisions_included": -2}))
        self.assertIn("can't be negative", self.fails("agreement", "Bloom", "--file", str(f), "--note", "x"))

    # --- integrity ---------------------------------------------------------------------------
    def test_restore_cannot_be_overwritten_by_stale_command(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        stale = sl.Store(self.d).load("Bloom", "main")
        self.run_cmd("restore", "Bloom")
        with self.assertRaises(SystemExit) as e:
            sl.Store(self.d).save(stale)
        self.assertIn("changed by another command", str(e.exception))
        self.assertEqual(len(self.rec()["requests"]), 0)
        self.assertEqual(self.rec()["events"][-1]["event"], "restored")

    def test_simultaneous_init_only_one_wins(self):
        cmd = [sys.executable, str(SCRIPT), "--data-dir", self.d, "init", "Race Co", "--rate"]
        procs = [subprocess.Popen(cmd + [rate], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for rate in ("50", "60", "70", "80")]
        for p in procs:
            p.communicate(timeout=30)
        codes = [p.returncode for p in procs]
        self.assertEqual(codes.count(0), 1, f"exactly one init should succeed, got exit codes {codes}")
        self.assertEqual(self.rec("Race Co")["rev"], 1)

    def test_invalid_hours_per_day(self):
        for bad in ("0", "-1", "nan", "inf", "25", "abc"):
            self.fails("init", f"H{bad.replace('-', 'm')}", "--rate", "50", "--hours-per-day", bad)
            self.fails("quote", "--rate", "50", "--days", "1", "--hours-per-day", bad)

    def test_nan_and_infinity_rejected(self):
        for bad in ("nan", "inf", "-inf"):
            self.fails("quote", "--rate", bad, "--hours", "1")
            self.fails("quote", "--rate", "50", "--hours", bad)

    def test_invalid_nested_records_give_useful_errors(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        p = Path(self.d) / "bloom" / "main.json"
        good = json.loads(p.read_text(encoding="utf-8"))
        cases = [
            (lambda d: d["requests"][0].update(quote="maybe"), "unknown quote state"),
            (lambda d: d["agreement"][0].update(version=3), "versions must run"),
            (lambda d: d["requests"][0].update(round=7), "round 7"),
            (lambda d: d["requests"][0].update(cost="NaN"), "invalid cost"),
            (lambda d: d["requests"][0].update(invoice="paid"), "invoiced but not an accepted"),
            (lambda d: d.update(change_orders=[{"number": 1, "items": [1]}, {"number": 2, "items": [1]}]),
             "is on change orders 1 and 2"),
        ]
        for mutate, expected in cases:
            d = json.loads(json.dumps(good))
            mutate(d)
            p.write_text(json.dumps(d), encoding="utf-8")
            msg = self.fails("report", "Bloom")
            self.assertIn(expected, msg)
            self.assertNotIn("Traceback", msg)

    # --- money, invoices and output ----------------------------------------------------------
    def test_invoice_needs_finished_accepted_chargeable_work(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        self.run_cmd("log", "Bloom", "--request", "Sticker", "--verdict", "extra", "--fixed", "20", "--courtesy")
        self.run_cmd("update", "Bloom", "1", "--quote", "accepted", "--approved-by", "Priya")
        self.assertIn("isn't marked done", self.fails("update", "Bloom", "1", "--invoice", "invoiced"))
        self.run_cmd("update", "Bloom", "1", "--invoice", "invoiced", "--advance")
        self.assertIn("can't be invoiced", self.fails("update", "Bloom", "2", "--invoice", "invoiced"))
        self.assertIn("price can't change", self.fails("update", "Bloom", "1", "--hours", "5"))

    def test_currency_minor_units(self):
        self.assertIn("1,500.125 KWD", self.run_cmd("quote", "--rate", "500.0417", "--currency", "KWD", "--hours", "3"))
        self.assertIn("¥4,501  (3h at ¥1,500/hour)", self.run_cmd("quote", "--rate", "1500.4", "--currency", "JPY", "--hours", "3"))
        self.assertIn("$10.50", self.run_cmd("quote", "--rate", "10.5", "--hours", "1"))

    def test_markdown_escaping(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Logo | with pipe\nand newline", "--verdict", "extra", "--hours", "1")
        rep = self.run_cmd("report", "Bloom")
        row = [l for l in rep.splitlines() if l.startswith("| 1 |") and "Logo" in l][0]
        self.assertIn("Logo \\| with pipe and newline", row)
        co = self.run_cmd("change-order", "Bloom", "1")
        self.assertIn("Logo \\| with pipe and newline", co)

    def test_explain_and_history(self):
        self.setup_project()
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        self.run_cmd("update", "Bloom", "1", "--quote", "accepted", "--approved-by", "Priya")
        ex = self.run_cmd("explain", "Bloom")
        self.assertIn("+ #1 Box: $150", ex)
        self.assertIn("= Project total: $750", ex)
        self.assertIn("Accepted, not invoiced yet: $150", ex)
        hist = self.run_cmd("history", "Bloom")
        for event in ("project created", "request logged", "request updated"):
            self.assertIn(event, hist)

    def test_similar_client_names_kept_apart(self):
        self.run_cmd("init", "A&B Studio", "--rate", "50")
        self.assertIn("share a folder", self.fails("init", "A B Studio", "--project", "Other", "--rate", "50"))
        self.run_cmd("init", "a&b studio", "--project", "Second", "--rate", "50")

    def test_v20_record_gets_round_snapshots(self):
        """Records written by v2.0 (rounds without agreement versions) load and keep their history."""
        self.setup_project()
        self.run_cmd("round", "Bloom")
        p = Path(self.d) / "bloom" / "main.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        for r in d["rounds"]:
            r.pop("agreement_version"), r.pop("cap")
        d.pop("events")
        p.write_text(json.dumps(d), encoding="utf-8")
        self.run_cmd("log", "Bloom", "--request", "Pink", "--verdict", "revision")
        r = self.rec()
        self.assertEqual((r["rounds"][0]["agreement_version"], r["rounds"][0]["cap"]), (1, 2))


if __name__ == "__main__":
    import unittest
    unittest.main()
