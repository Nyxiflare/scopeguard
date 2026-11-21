"""Offline tests for scope_ledger.py. Run from the skill folder:  python -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scope_ledger.py"
sys.path.insert(0, str(SCRIPT.parent))
import scope_ledger as sl  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sl.main(["--data-dir", self.d, *args])
        return buf.getvalue()

    def fails(self, *args) -> str:
        with self.assertRaises(SystemExit) as e:
            self.run_cmd(*args)
        return str(e.exception)

    def rec(self, client="Bloom", project="main"):
        return json.loads((Path(self.d) / sl.slug(client) / f"{sl.slug(project)}.json").read_text(encoding="utf-8"))

    def agreement(self, client="Bloom", project="main", note=None, **scope):
        f = Path(self.d) / f"scope-{len(os.listdir(self.d))}.json"
        f.write_text(json.dumps(scope))
        args = ["agreement", client, "--project", project, "--file", str(f)]
        if note:
            args += ["--note", note, "--approved-by", "Priya", "--evidence", "email 3 Dec"]
        return self.run_cmd(*args)


class LogoAcceptance(Base):
    """The README example: the logo proposal and Priya's five requests at $50/hour."""

    def test_logo_job(self):
        self.run_cmd("init", "Bloom", "--rate", "50")
        self.agreement(deliverables=["3 logo concepts", "Final SVG, PNG, PDF"], revisions_included=2,
                       exclusions=["Brand guidelines", "Packaging design", "Website graphics"], price=600)
        self.run_cmd("round", "Bloom")
        self.run_cmd("log", "Bloom", "--request", "Softer pink", "--verdict", "revision")
        self.run_cmd("log", "Bloom", "--request", "Concept B with A's font", "--verdict", "revision")
        self.run_cmd("log", "Bloom", "--request", "Instagram + Facebook sizes", "--verdict", "grey", "--hours", "1.5")
        self.run_cmd("log", "Bloom", "--request", "Cake box layout", "--verdict", "extra",
                     "--clause", "Not included: packaging design", "--hours", "3")
        self.run_cmd("log", "Bloom", "--request", "Fix squished e", "--verdict", "included")
        r = self.rec()
        self.assertEqual([x["round"] for x in r["requests"][:2]], [1, 1], "one message = one round")
        self.assertEqual(r["requests"][3]["cost"], "150")
        self.assertIsNone(r["requests"][4]["cost"])
        t = sl.totals(r)
        self.assertEqual(t["money"]["draft"], 150)
        self.assertEqual(t["money"]["grey_open"], 75)
        rep = self.run_cmd("report", "Bloom")
        self.assertIn("Revision rounds: 1 of 2 included", rep)
        self.assertIn("Project total:** $600", rep)


class Projects(Base):
    def test_two_projects_are_independent(self):
        self.run_cmd("init", "Bloom", "--project", "Logo", "--rate", "50", "--revisions", "2")
        self.run_cmd("init", "Bloom", "--project", "Website", "--rate", "70", "--revisions", "3")
        self.run_cmd("round", "Bloom", "--project", "Logo")
        self.assertEqual(len(self.rec(project="Logo")["rounds"]), 1)
        self.assertEqual(len(self.rec(project="Website")["rounds"]), 0)
        self.assertIn("Projects on file: logo, website", self.fails("report", "Bloom"))
        out = self.run_cmd("list")
        self.assertIn("Bloom / Logo", out)
        self.assertIn("Bloom / Website", out)


class Agreements(Base):
    def test_amendments_never_overwrite(self):
        self.run_cmd("init", "Bloom", "--rate", "50")
        self.agreement(deliverables=["3 concepts"], revisions_included=2, price=600)
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        self.agreement(note="Added box design", deliverables=["3 concepts", "Box design"], price=750)
        r = self.rec()
        self.assertEqual([v["version"] for v in r["agreement"]], [1, 2])
        self.assertEqual(r["agreement"][0]["scope"]["deliverables"], ["3 concepts"])
        self.assertEqual(r["agreement"][1]["approved_by"], "Priya")
        self.assertEqual(r["agreement"][1]["revisions_included"], 2, "unchanged terms carry over")
        self.assertEqual(r["requests"][0]["agreement_version"], 1)
        self.run_cmd("log", "Bloom", "--request", "Tweak box", "--verdict", "included")
        self.assertEqual(self.rec()["requests"][1]["agreement_version"], 2)
        self.assertIn("| 2 |", self.run_cmd("report", "Bloom"))

    def test_absorbed_extra_not_counted_twice(self):
        """The acceptance-run bug: an accepted extra folded into an amendment was counted twice."""
        self.run_cmd("init", "Bloom", "--rate", "50")
        self.agreement(deliverables=["3 concepts"], exclusions=["Packaging"], revisions_included=2, price=600)
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        self.run_cmd("update", "Bloom", "1", "--quote", "accepted", "--approved-by", "Priya", "--evidence", "WhatsApp")
        co = self.run_cmd("change-order", "Bloom", "1")
        self.assertIn("Project total with these items:** $750", co)
        self.assertIn("**Approved by:** Priya", co)
        self.assertNotIn("______", co)
        f = Path(self.d) / "amend.json"
        f.write_text(json.dumps({"deliverables": ["3 concepts", "Cake box"], "price": 750}))
        warn = self.run_cmd("agreement", "Bloom", "--file", str(f), "--note", "Added box")
        self.assertIn("--absorbs", warn, "warns when the price changes with loose accepted extras")
        self.assertIn("Project total:** $900", self.run_cmd("report", "Bloom"), "without --absorbs it adds on top")
        self.run_cmd("agreement", "Bloom", "--file", str(f), "--note", "Added box (absorbs #1)", "--absorbs", "1")
        rep = self.run_cmd("report", "Bloom")
        self.assertIn("Project total:** $750", rep)
        self.assertIn("already inside the amended price", rep)
        r = self.rec()
        self.assertEqual(r["agreement"][-1]["scope"]["exclusions"], ["Packaging"], "partial amendment keeps old terms")

    def test_init_price_filled_by_first_agreement(self):
        self.run_cmd("init", "Bloom", "--rate", "50", "--price", "600", "--revisions", "2")
        self.agreement(deliverables=["3 concepts"])
        r = self.rec()
        self.assertEqual(len(r["agreement"]), 1)
        self.assertEqual(r["agreement"][0]["price"], "600")
        self.assertEqual(r["agreement"][0]["scope"]["deliverables"], ["3 concepts"])


class Statuses(Base):
    def setUp(self):
        super().setUp()
        self.run_cmd("init", "Bloom", "--rate", "50", "--price", "600")
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        self.run_cmd("log", "Bloom", "--request", "Sticker", "--verdict", "extra", "--fixed", "40", "--courtesy")

    def test_quote_work_invoice_are_separate(self):
        self.assertIn("Only accepted work can be invoiced", self.fails("update", "Bloom", "1", "--invoice", "invoiced"))
        self.run_cmd("update", "Bloom", "1", "--quote", "accepted", "--approved-by", "Priya", "--evidence", "email 3 Dec")
        t = sl.totals(self.rec())
        self.assertEqual(t["money"]["accepted"], 150)
        self.assertEqual(t["money"]["to_invoice"], 150)
        self.run_cmd("update", "Bloom", "1", "--work", "done", "--invoice", "paid")
        r = self.rec()["requests"][0]
        self.assertEqual((r["quote"], r["work"], r["invoice"]), ("accepted", "done", "paid"))
        self.assertEqual(r["approval"]["by"], "Priya")
        self.assertEqual(sl.totals(self.rec())["money"]["paid"], 150)

    def test_courtesy_counts_as_given_away(self):
        t = sl.totals(self.rec())
        self.assertEqual(t["money"]["complimentary"], 40)
        self.assertIn("Given away so far:** $40 (7% of the agreed price)", self.run_cmd("report", "Bloom"))

    def test_old_status_shortcut(self):
        self.run_cmd("update", "Bloom", "1", "--status", "approved")
        self.assertEqual(self.rec()["requests"][0]["quote"], "accepted")


class RevisionCap(Base):
    def test_cap_is_enforced(self):
        self.run_cmd("init", "Bloom", "--rate", "50", "--revisions", "1")
        self.assertIn("Record the feedback round first", self.fails("log", "Bloom", "--request", "x", "--verdict", "revision"))
        self.run_cmd("round", "Bloom")
        self.run_cmd("log", "Bloom", "--request", "a", "--verdict", "revision")
        self.assertIn("beyond the 1 included", self.run_cmd("round", "Bloom"))
        self.assertIn("extra work", self.fails("log", "Bloom", "--request", "b", "--verdict", "revision"))
        self.run_cmd("log", "Bloom", "--request", "b", "--verdict", "revision", "--courtesy", "--hours", "1")
        r = self.rec()["requests"][1]
        self.assertTrue(r["over_cap"] and r["complimentary"])
        self.assertEqual(sl.totals(self.rec())["money"]["complimentary"], 50)
        self.assertIn("1 over the limit", self.run_cmd("report", "Bloom"))


class ChangeOrders(Base):
    def test_change_order(self):
        self.run_cmd("init", "Bloom", "--rate", "50", "--price", "600")
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--clause", "Not included: packaging", "--hours", "3")
        self.run_cmd("log", "Bloom", "--request", "Fix e", "--verdict", "included")
        self.assertIn("aren't extra work", self.fails("change-order", "Bloom", "2"))
        out = self.run_cmd("change-order", "Bloom", "1", "--timeline", "+2 working days")
        self.assertIn("Change order 1", out)
        self.assertIn("Total:** $150", out)
        self.assertIn("Project total with these items:** $750", out)
        self.assertIn("Approved by:", out)
        self.assertEqual(self.rec()["requests"][0]["quote"], "sent")


class Integrity(Base):
    def test_backup_and_restore(self):
        self.run_cmd("init", "Bloom", "--rate", "50")
        self.run_cmd("log", "Bloom", "--request", "Box", "--verdict", "extra", "--hours", "3")
        p = Path(self.d) / "bloom" / "main.json"
        p.write_text("{broken", encoding="utf-8")
        self.assertIn("damaged", self.fails("report", "Bloom"))
        self.run_cmd("restore", "Bloom")
        self.assertEqual(len(self.rec()["requests"]), 0, "backup is the state before the last write")

    def test_invalid_record_rejected(self):
        p = Path(self.d) / "bloom" / "main.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"schema": 2, "client": "Bloom"}), encoding="utf-8")
        self.assertIn("doesn't look like a valid", self.fails("report", "Bloom"))

    def test_force_reset_keeps_backup(self):
        self.run_cmd("init", "Bloom", "--rate", "50")
        self.assertIn("--force", self.fails("init", "Bloom", "--rate", "60"))
        self.run_cmd("init", "Bloom", "--rate", "60", "--force")
        self.assertTrue(list((Path(self.d) / "bloom").glob("main.reset-*.json.bak")))

    def test_concurrent_change_detected(self):
        self.run_cmd("init", "Bloom", "--rate", "50")
        st = sl.Store(self.d)
        first = st.load("Bloom", "main")
        second = st.load("Bloom", "main")
        st.save(first)
        with self.assertRaises(SystemExit) as e:
            st.save(second)
        self.assertIn("changed by another command", str(e.exception))

    def test_v1_record_is_migrated(self):
        old = {"client": "Old Co", "project": "", "currency": "USD", "rate": "50", "rate_unit": "hour",
               "original_price": "500", "revisions_included": 2, "revisions_used": 1,
               "baseline": {"deliverables": ["Logo"]},
               "requests": [{"id": 1, "date": "2026-11-20", "request": "Box", "verdict": "extra",
                             "cost": "150", "status": "approved"}]}
        (Path(self.d) / "old-co.json").write_text(json.dumps(old), encoding="utf-8")
        rep = self.run_cmd("report", "Old Co")
        self.assertIn("moved the old record", rep)
        r = self.rec("Old Co")
        self.assertEqual(r["requests"][0]["quote"], "accepted")
        self.assertEqual(len(r["rounds"]), 1)
        self.assertEqual(r["agreement"][0]["scope"]["deliverables"], ["Logo"])


class Misc(Base):
    def test_quote_currency_and_day_rate(self):
        self.assertIn("₹4,500", self.run_cmd("quote", "--rate", "1500", "--currency", "INR", "--hours", "3"))
        self.run_cmd("init", "Beta", "--rate", "400", "--unit", "day")
        self.run_cmd("log", "Beta", "--request", "x", "--verdict", "extra", "--hours", "4", "--buffer", "10")
        self.assertEqual(Decimal(self.rec("Beta")["requests"][0]["cost"]), Decimal("220"))

    def test_bad_input(self):
        self.assertIn("No record", self.fails("log", "Nobody", "--request", "x", "--verdict", "extra"))
        self.fails("init", "Eps", "--rate", "-5")
        self.run_cmd("init", "Delta", "--rate", "50")
        self.fails("log", "Delta", "--request", "x", "--verdict", "maybe")

    def test_runs_from_another_folder(self):
        """Installed globally, the script is called by full path from the user's project folder."""
        with tempfile.TemporaryDirectory() as work:
            res = subprocess.run([sys.executable, str(SCRIPT), "init", "Acme", "--rate", "50"],
                                 cwd=work, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue((Path(work) / "scopeguard-data" / "acme" / "main.json").is_file())


if __name__ == "__main__":
    unittest.main()
