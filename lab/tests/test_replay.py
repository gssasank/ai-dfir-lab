import json
import unittest

import helpers
from dfir_lab import import_transcript, replay
from dfir_lab.errors import LabError
from dfir_lab.redact import MARK, Redactor

RECS = [
    {"type": "prompt", "text": "p1"},
    {"type": "assistant_text", "text": "a2"},
    {"type": "tool_call", "tool": "Bash", "command": "ls"},
    {"type": "tool_result", "output": "\n".join("line %d" % i for i in range(1, 26)),
     "truncated": True, "note": "cut"},
    {"type": "pause", "question": "q5?"},
]


def play(records, keys, page_height=10):
    out = []
    feed = iter(keys)

    def next_key():
        try:
            return next(feed)
        except StopIteration:
            raise EOFError

    p = replay.Player(records, input_fn=next_key, output_fn=lambda s="", **k: out.append(s),
                      page_height=page_height)
    end = p.run()
    return end, "\n".join(out)


def steps_shown(text):
    import re
    return [int(m) for m in re.findall(r"--- Step (\d+) of", text)]


class PlayerTests(unittest.TestCase):
    def test_next_back_jump_quit(self):
        end, text = play(RECS, ["", "b", "b", "j 5", "j 99", "3", "q"])
        self.assertEqual(steps_shown(text), [1, 2, 1, 5, 3])
        self.assertIn("This is the first step.", text)
        self.assertIn("step number from 1 to 5", text)
        self.assertEqual(end, 2)
        self.assertIn("Replay closed at step 3 of 5", text)

    def test_paging_long_output(self):
        # Go to step 4 (25 lines + note) with page height 10: two "more" prompts.
        end, text = play(RECS, ["j 4", "", "", "q"])
        self.assertIn("line 25", text)
        self.assertEqual(text.count("-- more"), 2)
        self.assertIn("[cut]", text)
        end, text = play(RECS, ["j 4", "s", "q"])
        self.assertNotIn("line 25", text)
        self.assertIn("rest of the output skipped", text)

    def test_end_of_recording_and_eof(self):
        end, text = play(RECS, ["5", "", "x", "q"])
        self.assertIn("End of the recording", text)
        self.assertIn("Unknown command", text)
        end, text = play(RECS, [])          # EOF quits
        self.assertIn("Replay closed", text)

    def test_render_types(self):
        self.assertEqual(replay.render_record(RECS[2]), ["Bash: ls"])
        self.assertEqual(replay.render_record(RECS[4]), ["q5?"])

    def test_load_errors(self):
        tmp = helpers.Path(__import__("tempfile").mkdtemp())
        bad = tmp / "bad.jsonl"
        bad.write_text('{"type": "prompt", "text": "x"}\nnot json\n')
        with self.assertRaises(LabError):
            replay.load_recording(bad)
        bad.write_text('{"type": "video"}\n')
        with self.assertRaises(LabError):
            replay.load_recording(bad)

    def test_fixture_recording_is_valid(self):
        recs = replay.load_recording(helpers.FIXTURE / "fallback" / "demo.jsonl")
        self.assertGreaterEqual(len(recs), 5)
        self.assertEqual({r["type"] for r in recs}, set(replay.TYPES))


class ReplayCliTests(helpers.LabTestCase):
    start_mock = False

    def test_cli_with_piped_input(self):
        r = self.run_cli("replay", "--list")
        self.assertIn("demo", r.stdout)
        r = self.run_cli("replay", "demo", input="\n\nb\nj 9\nq\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Step 1 of 9", r.stdout)
        self.assertIn("Step 3 of 9", r.stdout)
        self.assertIn("Step 9 of 9", r.stdout)
        r = self.run_cli("replay", "nope")
        self.assertEqual(r.returncode, 2)


class ImportTranscriptTests(unittest.TestCase):
    def test_convert_with_redaction(self):
        key = helpers.random_key()
        lines = [
            {"type": "summary", "summary": "x"},
            {"type": "user", "message": {"role": "user", "content":
                "<system-reminder>ignore</system-reminder>Find logons. My key is " + key}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Listing logons."},
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "grep 4624 evidence/x.csv", "description": "d"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": [{"type": "text", "text": "\n".join(str(i) for i in range(300))
                              + "\nKEY=" + key}]}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Done."}]}},
        ]
        recs = import_transcript.convert([json.dumps(l) for l in lines], Redactor([key]),
                                         max_lines=50)
        self.assertEqual([r["type"] for r in recs],
                         ["prompt", "assistant_text", "tool_call", "tool_result", "assistant_text"])
        self.assertNotIn("system-reminder", recs[0]["text"])
        self.assertIn(MARK, recs[0]["text"])
        self.assertEqual(recs[2]["command"], "grep 4624 evidence/x.csv")
        self.assertTrue(recs[3]["truncated"])
        self.assertIn("50 of 301", recs[3]["note"])
        self.assertNotIn(key, json.dumps(recs))
        # Output is loadable by the player.
        tmp = helpers.Path(__import__("tempfile").mkdtemp()) / "r.jsonl"
        tmp.write_text("".join(json.dumps(r) + "\n" for r in recs))
        self.assertEqual(len(replay.load_recording(tmp)), 5)


if __name__ == "__main__":
    unittest.main()
