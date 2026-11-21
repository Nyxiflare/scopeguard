"""Offline tests for scope_ledger.py. Run: python -m unittest discover -s tests"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scope_ledger as sl  # noqa: E402


def run(*args, data_dir):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = sl.main(["--data-dir", data_dir, *args])
    return code, buf.getvalue()


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self, name="Acme Co"):
        return json.loads((Path(self.d) / f"{sl.slug(name)}.json").read_text(encoding="utf-8"))

    def test_full_flow_and_totals(self):
        run("init", "Acme Co", "--rate", "60", "--price", "500", "--revisions", "2", data_dir=self.d)
        base = Path(self.d) / "b.json"
        base.write_text(json.dumps({"deliverables": ["3 logo concepts"], "exclusions": ["Business cards"]}))
        run("baseline", "Acme Co", "--file", str(base), data_dir=self.d)
        run("log", "Acme Co", "--request", "4th concept", "--verdict", "extra", "--hours", "2", data_dir=self.d)
        run("log", "Acme Co", "--request", "Business card", "--verdict", "extra", "--fixed", "80", data_dir=self.d)
        run("log", "Acme Co", "--request", "Darker blue", "--verdict", "revision", "--hours", "1", data_dir=self.d)
        run("log", "Acme Co", "--request", "Insta sizes", "--verdict", "grey", data_dir=self.d)
        run("update", "Acme Co", "1", "--status", "approved", data_dir=self.d)
        run("update", "Acme Co", "2", "--status", "free", data_dir=self.d)
        led = self.ledger()
        self.assertEqual(led["requests"][0]["cost"], "120")
        self.assertIsNone(led["requests"][2]["cost"], "revisions are covered by the price")
        self.assertEqual(led["requests"][2]["status"], "pending", "revision work is still to do")
        t = sl.totals(led)
        self.assertEqual(t["money"]["approved"], 120)
        self.assertEqual(t["money"]["free"], 80)
        self.assertEqual(t["unpriced"], 1)
        self.assertEqual(t["counts"]["grey_open"], 1, "unanswered grey items are kept apart")
        self.assertEqual(t["counts"]["pending"], 0)
        _, rep = run("report", "Acme Co", data_dir=self.d)
        self.assertIn("New project total:** $620", rep)
        self.assertIn("Given away so far:** $80 (16% of the original price)", rep)

    def test_day_rate_conversion_and_buffer(self):
        run("init", "Beta", "--rate", "400", "--unit", "day", data_dir=self.d)
        run("log", "Beta", "--request", "x", "--verdict", "extra", "--hours", "4", "--buffer", "10", data_dir=self.d)
        self.assertEqual(sl.Decimal(self.ledger("Beta")["requests"][0]["cost"]), sl.Decimal("220"))

    def test_revision_cap(self):
        run("init", "Gamma", "--rate", "50", "--revisions", "1", data_dir=self.d)
        _, a = run("round", "Gamma", data_dir=self.d)
        _, b = run("round", "Gamma", data_dir=self.d)
        self.assertIn("1 of 1", a)
        self.assertIn("beyond the 1 included", b)
        run("log", "Gamma", "--request", "tweak", "--verdict", "revision", data_dir=self.d)
        self.assertEqual(self.ledger("Gamma")["requests"][0]["round"], 2)
        _, rep = run("report", "Gamma", data_dir=self.d)
        self.assertIn("1 over the limit", rep)
        self.assertIn("revision (round 2)", rep)

    def test_grey_priced_but_open_not_in_quoted(self):
        run("init", "Theta", "--rate", "50", data_dir=self.d)
        run("log", "Theta", "--request", "box", "--verdict", "extra", "--hours", "3", data_dir=self.d)
        run("log", "Theta", "--request", "social", "--verdict", "grey", "--hours", "1.5", data_dir=self.d)
        t = sl.totals(self.ledger("Theta"))
        self.assertEqual(t["money"]["pending"], 150)
        self.assertEqual(t["money"]["grey_open"], 75)

    def test_quote_and_currency(self):
        _, o = run("quote", "--rate", "1500", "--currency", "INR", "--hours", "3", data_dir=self.d)
        self.assertIn("₹4,500", o)

    def test_errors_are_friendly(self):
        with self.assertRaises(SystemExit) as e:
            run("log", "Nobody", "--request", "x", "--verdict", "extra", data_dir=self.d)
        self.assertIn("No record", str(e.exception))
        run("init", "Delta", "--rate", "50", data_dir=self.d)
        with self.assertRaises(SystemExit):
            run("init", "Delta", "--rate", "50", data_dir=self.d)
        with self.assertRaises(SystemExit):
            run("log", "Delta", "--request", "x", "--verdict", "maybe", data_dir=self.d)
        with self.assertRaises(SystemExit):
            run("init", "Eps", "--rate", "-5", data_dir=self.d)

    def test_list(self):
        run("init", "Zeta", "--rate", "50", data_dir=self.d)
        _, o = run("list", data_dir=self.d)
        self.assertIn("Zeta: 0 request(s)", o)


if __name__ == "__main__":
    unittest.main()
