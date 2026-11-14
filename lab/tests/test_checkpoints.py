import json
import unittest

import helpers
from dfir_lab import checkpoint, make_checkpoints
from dfir_lab.normalize import FormatProblem, normalize


class NormaliseTests(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(normalize("exact", "  7045 "), "7045")
        self.assertNotEqual(normalize("exact", "Abc"), normalize("exact", "abc"))

    def test_casefold(self):
        self.assertEqual(normalize("casefold", "  UpdateSvc   Service "), "updatesvc service")

    def test_integer(self):
        for s in ("1005", " 1,005 ", "#1005", "0x3ED", "+1005", "1_005"):
            self.assertEqual(normalize("integer", s), "1005", s)
        for bad in ("ten", "10.5", ""):
            with self.assertRaises(FormatProblem):
                normalize("integer", bad)

    def test_ip(self):
        for s in ("203.0.113.77", " 203.000.113.077 ", "203.0.113.77:3389", "[203.0.113.77]"):
            self.assertEqual(normalize("ip", s), "203.0.113.77", s)
        self.assertEqual(normalize("ip", "2001:DB8:0:0::1"), "2001:db8::1")
        self.assertEqual(normalize("ip", "[2001:db8::1]:443"), "2001:db8::1")
        with self.assertRaises(FormatProblem):
            normalize("ip", "999.1.1.1")

    def test_timestamp(self):
        want = "2020-09-19T03:21:46Z"
        for s in ("2020-09-19 03:21:46", "2020-09-19T03:21:46Z", "2020-09-19T03:21:46.123456Z",
                  "2020-09-19 03:21:46 UTC", "2020/09/19 03:21:46", "09/19/2020 03:21:46",
                  "2020-09-18T23:21:46-04:00", "2020-09-19T05:21:46+0200", "1600485706",
                  "09/19/2020 3:21:46 AM"):
            self.assertEqual(normalize("timestamp-utc-seconds", s), want, s)
        with self.assertRaises(FormatProblem):
            normalize("timestamp-utc-seconds", "yesterday")
        with self.assertRaises(FormatProblem):
            normalize("timestamp-utc-seconds", "2020-09-19")

    def test_impossible_offset_is_format_feedback(self):
        # Review L2 (P3): '+99:00' used to raise a plain ValueError.
        for s in ("2020-09-19 03:21:46+99:00", "2020-09-19 03:21:46-24:00",
                  "2020-09-19 03:21:46+05:75"):
            with self.assertRaises(FormatProblem, msg=s):
                normalize("timestamp-utc-seconds", s)
        self.assertEqual(normalize("timestamp-utc-seconds", "2020-09-19 03:21:46+23:59"),
                         "2020-09-18T03:22:46Z")

    def test_regex_extract(self):
        pat = r"(?i)([a-z]:[\\/][^\s\"']+\.exe)"
        self.assertEqual(normalize("regex-extract", "It ran C:\\Windows\\Temp\\UPD.exe as a service",
                                   pat, "casefold"), "c:\\windows\\temp\\upd.exe")
        self.assertEqual(normalize("regex-extract", "pid 4242", r"(\d+)"), "4242")
        with self.assertRaises(FormatProblem):
            normalize("regex-extract", "no path here", pat)


class BuildTests(unittest.TestCase):
    def test_build_hashes_and_never_stores_answers(self):
        src = json.loads((helpers.FIXTURE / "checkpoints.source.json").read_text())
        built = make_checkpoints.build(src)
        text = json.dumps(built)
        for cp in built["checkpoints"]:
            self.assertNotIn("answer", cp)
        # Distinctive answers do not appear anywhere (7045 is in a success message).
        for answer in ("203.0.113.77", "1005", "03:21:46", "upd.exe"):
            self.assertNotIn(answer, text)
        cp1 = built["checkpoints"][0]
        self.assertEqual(len(cp1["salt"]), 32)
        self.assertEqual(cp1["hash"], checkpoint.answer_hash(cp1["salt"], "203.0.113.77"))

    def test_build_rejects_bad_source(self):
        for src in ({"checkpoints": [{"id": 1, "normalize": "integer", "answer": "x"}]},
                    {"checkpoints": [{"id": 1, "normalize": "bogus", "answer": "1"}]},
                    {"checkpoints": [{"id": 1, "answer": "1"}, {"id": 1, "answer": "2"}]},
                    {"checkpoints": [{"id": 1, "normalize": "regex-extract", "answer": "1"}]}):
            with self.assertRaises(ValueError):
                make_checkpoints.build(src)

    def test_fixture_generated_file_matches_source(self):
        src = json.loads((helpers.FIXTURE / "checkpoints.source.json").read_text())
        gen = json.loads((helpers.FIXTURE / "checkpoints.json").read_text())
        by_id = {str(c["id"]): c for c in gen["checkpoints"]}
        for cp in src["checkpoints"]:
            g = by_id[str(cp["id"])]
            self.assertTrue(checkpoint.is_correct(g, cp["answer"]), cp["id"])
            for alt in cp.get("alternates") or []:
                self.assertTrue(checkpoint.is_correct(g, alt), (cp["id"], alt))


class CheckpointCliTests(helpers.LabTestCase):
    start_mock = False

    def test_impossible_offset_cli(self):
        # Checkpoint 3 uses timestamp-utc-seconds. Before the fix this was an
        # "unexpected" internal error with exit code 3.
        r = self.run_cli("checkpoint", "3", "2020-09-19 03:21:46+99:00")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not in the expected form", r.stdout)
        self.assertIn("not possible", r.stdout)
        self.assertNotIn("unexpected", r.stdout + r.stderr)

    def test_right_wrong_hints_progress(self):
        r = self.run_cli("checkpoint", "1", "10.0.0.1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not correct (attempt 1)", r.stdout)
        self.assertIn("dfir-lab hint 1", r.stdout)
        r = self.run_cli("checkpoint", "1", "not-an-ip")
        self.assertEqual(r.returncode, 1)
        self.assertIn("did not count", r.stdout)
        r = self.run_cli("hint", "1")
        self.assertIn("hint 1 of 2", r.stdout)
        r = self.run_cli("hint", "1")
        self.assertIn("hint 2 of 2", r.stdout)
        r = self.run_cli("hint", "1")
        self.assertIn("no more hints", r.stdout)
        r = self.run_cli("checkpoint", "1", "203.0.113.077")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CORRECT", r.stdout)
        self.assertIn("Every attempt came from that one address", r.stdout)
        # Alternate answer for checkpoint 5.
        r = self.run_cli("checkpoint", "5", "the", "binary", "was", "C:/Windows/Temp/UPD.EXE")
        self.assertEqual(r.returncode, 0, r.stdout)
        # Answer with spaces joined from several words.
        r = self.run_cli("checkpoint", "3", "2020-09-19", "03:21:46")
        self.assertEqual(r.returncode, 0, r.stdout)

        prog = helpers.read_json(self.home / "dfir-lab" / "progress.json")
        self.assertEqual(prog["case"], "sample-case")
        e1 = prog["checkpoints"]["1"]
        self.assertEqual(e1["attempts"], 2)               # the format problem did not count
        self.assertTrue(e1["solved"])
        self.assertEqual(e1["hints_used"], 2)
        self.assertEqual([h["correct"] for h in e1["history"]], [False, True])
        text = (self.home / "dfir-lab" / "progress.json").read_text()
        self.assertNotIn("203.0.113", text)                # answers are never stored
        self.assertNotIn("10.0.0.1", text)

        r = self.run_cli("progress")
        self.assertEqual(r.returncode, 0)
        self.assertIn("3 of 5 checkpoints done", r.stdout)
        self.assertRegex(r.stdout, r"1\s+Where did they come from\?\s+done\s+2\s+2/2")

    def test_unknown_checkpoint(self):
        r = self.run_cli("checkpoint", "9", "x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("The checkpoints are: 1, 2, 3, 4, 5", r.stderr)

    def test_missing_checkpoint_file(self):
        (self.case / "checkpoints.json").unlink()
        r = self.run_cli("progress")
        self.assertEqual(r.returncode, 3)
        self.assertIn("checkpoint file", r.stderr)


if __name__ == "__main__":
    unittest.main()
