import json
import unittest

import helpers
from dfir_lab import readiness
from dfir_lab.errors import UsageError

ALL_PASS = [(i, "PASS") for i in range(1, 12)]


class ReadinessCodeTests(unittest.TestCase):
    def test_format_and_determinism(self):
        c1 = readiness.compute("0.1.0", ALL_PASS, "2026-11-16")
        c2 = readiness.compute("0.1.0", ALL_PASS, "2026-11-16")
        self.assertEqual(c1, c2)
        self.assertRegex(c1, r"^DFIR-0\.1\.0-R-[0-9a-f]{10}$")

    def test_changes_with_inputs(self):
        base = readiness.compute("0.1.0", ALL_PASS, "2026-11-16")
        self.assertNotEqual(base, readiness.compute("0.1.1", ALL_PASS, "2026-11-16"))
        self.assertNotEqual(base, readiness.compute("0.1.0", ALL_PASS, "2026-11-17"))
        warn = list(ALL_PASS)
        warn[2] = (3, "WARN")
        cw = readiness.compute("0.1.0", warn, "2026-11-16")
        self.assertNotEqual(base, cw)
        self.assertIn("-R-", cw)                    # WARN still ready

    def test_not_ready(self):
        fail = list(ALL_PASS)
        fail[9] = (10, "FAIL")
        self.assertIn("-N-", readiness.compute("0.1.0", fail, "2026-11-16"))
        self.assertIn("-N-", readiness.compute("0.1.0", ALL_PASS[:5], "2026-11-16"))
        skip = list(ALL_PASS)
        skip[7] = (8, "SKIP")
        self.assertIn("-N-", readiness.compute("0.1.0", skip, "2026-11-16"))

    def test_verify(self):
        doc = {"version": "0.1.0", "date": "2026-11-16",
               "results": [{"id": i, "status": s} for i, s in ALL_PASS]}
        code = readiness.compute("0.1.0", ALL_PASS, "2026-11-16")
        self.assertTrue(readiness.verify(code, doc)[0])
        self.assertFalse(readiness.verify(code.replace("-R-", "-N-"), doc)[0])
        doc["results"][3]["status"] = "FAIL"
        self.assertFalse(readiness.verify(code, doc)[0])
        with self.assertRaises(UsageError):
            readiness.verify("not-a-code", doc)


class VerifyCodeCliTests(helpers.LabTestCase):
    start_mock = False

    def test_cli(self):
        doc = {"version": "0.1.0", "date": "2026-11-16",
               "results": [{"id": i, "status": s} for i, s in ALL_PASS]}
        p = self.tmp / "r.json"
        p.write_text(json.dumps(doc))
        code = readiness.compute("0.1.0", ALL_PASS, "2026-11-16")
        r = self.run_cli("verify-code", code, "--results", str(p))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("valid: READY", r.stdout)
        r = self.run_cli("verify-code", code[:-1] + ("0" if code[-1] != "0" else "1"),
                         "--results", str(p))
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not match", r.stdout)
        r = self.run_cli("verify-code", "garbage", "--results", str(p))
        self.assertEqual(r.returncode, 2)
        r = self.run_cli("verify-code", code, "--results", str(self.tmp / "missing.json"))
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
