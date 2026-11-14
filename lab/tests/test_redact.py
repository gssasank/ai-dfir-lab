"""The key never appears in any report, log, JSON output, process
argument list or exported environment - property-style, with random keys."""

import base64
import json
import random
import re
import string
import time
import urllib.parse
import unittest
from pathlib import Path

import helpers
from dfir_lab import redact
from dfir_lab.redact import MARK, Redactor


def mangle(key, rng):
    """Ways a key ends up in text."""
    i = rng.randint(1, len(key) - 1)
    j = rng.randint(1, len(key) - 1)
    return [
        key,
        "Authorization: Bearer %s" % key,
        key[:i] + "\n" + key[i:],                 # wrapped line
        key[:i] + "  " + key[i:j] + "\t" + key[j:] if i < j else key[:i] + " " + key[i:],
        key[:i] + "\\n" + key[i:],                # JSON-escaped newline inside
        json.dumps({"k": key}),                    # JSON-escaped
        urllib.parse.quote(key, safe=""),          # URL-encoded
        repr(key),
    ]


class RedactorPropertyTests(unittest.TestCase):
    def test_random_keys_never_survive(self):
        rng = random.Random(510)
        for n in range(300):
            charset = helpers.KEY_CHARS if n % 2 else helpers.PRINTABLE_NOSPACE
            key = helpers.random_key(rng, charset=charset)
            red = Redactor([key])
            squashed = key
            for form in mangle(key, rng):
                text = "before %s after" % form
                out, count = red.redact_count(text)
                self.assertGreaterEqual(count, 1, (key, form))
                self.assertNotIn(key, out)
                self.assertNotIn(squashed, re.sub(r"\s|\\n", "", out))
                self.assertIn(MARK, out)
                bout, bcount = red.redact_bytes(text.encode())
                self.assertNotIn(key.encode(), bout)

    def test_no_key_no_change(self):
        red = Redactor([])
        self.assertEqual(red.redact("hello"), "hello")
        self.assertEqual(red.redact_bytes(b"x"), (b"x", 0))

    def test_unrelated_text_untouched(self):
        red = Redactor(["sk-abcdefghijkl"])
        self.assertEqual(red.redact("sk-abc and ghijkl"), "sk-abc and ghijkl")
        self.assertEqual(red.redact("plain base64 aGVsbG8gd29ybGQ= stays"),
                         "plain base64 aGVsbG8gd29ybGQ= stays")


def review_variants(key):
    """The exact representations from review finding H1 (probe P1)."""
    return {
        "literal": key,
        "wrapped": key[:8] + "\n" + key[8:],
        "spaces": " ".join(key),
        "escaped_newline": key[:8] + r"\n" + key[8:],
        "url": urllib.parse.quote(key, safe=""),
        "percent_all": "".join("%%%02X" % ord(c) for c in key),
        "percent_lower": urllib.parse.quote(key, safe="").replace("%2B", "%2b").replace("%2F", "%2f"),
        "json_unicode": "".join("\\u%04x" % ord(c) for c in key),
        "base64": base64.b64encode(key.encode()).decode(),
        "unicode_spaces": " ".join(key),
    }


def recover(text):
    """Try to get a key back out of redacted text: undo every encoding the
    redactor claims to handle, on the text and on each token in it."""
    candidates = [text]
    squashed = re.sub(r"[\s​-‍⁠﻿]|\\[nrt]", "", text)
    candidates.append(squashed)
    for t in list(candidates):
        candidates.append(urllib.parse.unquote(t))
        try:
            candidates.append(json.loads('"%s"' % t.replace('"', '\\"')))
        except ValueError:
            pass
    for tok in re.findall(r"[A-Za-z0-9+/=_-]{8,}", squashed):
        for dec in (base64.b64decode, base64.urlsafe_b64decode):
            for pad in ("", "=", "=="):
                try:
                    candidates.append(dec(tok + pad).decode("utf-8", "replace"))
                except (ValueError, TypeError):
                    pass
    return "\n".join(candidates)


class EncodedKeyTests(unittest.TestCase):
    """Review H1: encoded keys used to survive the redactor."""

    KEY = "sk-ReviewAbCdEf0123456789+/"

    def test_review_p1_variants_text_and_bytes(self):
        red = Redactor([self.KEY])
        for name, form in review_variants(self.KEY).items():
            text = "before %s after" % form
            out, n = red.redact_count(text)
            self.assertGreaterEqual(n, 1, name)
            self.assertNotIn(self.KEY, recover(out), name)
            bout, bn = red.redact_bytes(text.encode("utf-8"))
            self.assertGreaterEqual(bn, 1, name)
            self.assertNotIn(self.KEY, recover(bout.decode("utf-8")), name)

    def test_random_keys_in_every_listed_encoding(self):
        rng = random.Random(1310)
        for n in range(150):
            charset = helpers.KEY_CHARS if n % 2 else helpers.PRINTABLE_NOSPACE
            key = helpers.random_key(rng, charset=charset)
            red = Redactor([key])
            forms = list(review_variants(key).values()) + [
                base64.urlsafe_b64encode(key.encode()).decode(),
                # embedded at each alignment inside a longer base64 string
                base64.b64encode(b"user:" + key.encode()).decode(),
                base64.b64encode(b"Bearer " + key.encode() + b"\n").decode(),
                base64.b64encode(b"xy" + key.encode() + b"zz").decode(),
                "​".join(key),                                       # zero-width space
                "".join("\\u%04X" % ord(c) for c in key),                 # upper-case \u
                "".join(rng.choice([c, "%%%02x" % ord(c)]) for c in key),  # mixed escapes
                " 　".join(key),
                json.dumps({"k": key}),
            ]
            for form in forms:
                out, count = red.redact_count("x " + form + " y")
                self.assertGreaterEqual(count, 1, (key, form))
                self.assertNotIn(key, recover(out), (key, form))

    def test_wrapped_base64_block(self):
        key = helpers.random_key(random.Random(7), n=60)
        blob = base64.encodebytes(b"prefix-bytes " + key.encode() + b" trailer").decode()
        self.assertIn("\n", blob.strip())
        out, n = Redactor([key]).redact_count(blob)
        self.assertGreaterEqual(n, 1)
        self.assertNotIn(key, recover(out))

    def test_bounded_time_on_large_input(self):
        rng = random.Random(3)
        key = helpers.random_key(rng, n=60)
        red = Redactor([key])
        noise = "".join(rng.choice(string.printable) for _ in range(200_000))
        text = (noise + key + "\n") * 5          # about 1 MB
        t = time.monotonic()
        out, n = red.redact_count(text)
        elapsed = time.monotonic() - t
        self.assertEqual(n, 5)
        self.assertLess(elapsed, 5.0, "redacting 1 MB took %.2f s" % elapsed)


class RedactBeforeTruncateTests(helpers.LabTestCase):
    """Review H1 (second half): bodies and model output were cut before they
    were redacted, so a key at the cut point leaked a prefix."""

    def test_error_body_redacted_before_truncation(self):
        from dfir_lab.messages import render
        from dfir_lab.config import Config
        self.store_key()
        key = self.mock_key
        body = "x" * 280 + key + " tail"
        what, _ = render("other", Config(), status=418, body=body)
        self.assertNotIn(key[:10], what)

    def test_model_output_redacted_before_truncation(self):
        from unittest import mock
        from dfir_lab import check, gateway
        from dfir_lab.config import Config
        self.store_key()
        key = self.mock_key
        reply = {"type": "message", "content": [{"type": "text", "text": "A" * 30 + key}]}
        with mock.patch.object(gateway.Gateway, "messages", return_value=gateway.HttpResult(
                status=200, body=json.dumps(reply).encode())):
            r = check.run_checks(Config(), [7]).results[7]
        self.assertEqual(r.status, "PASS", r.result)
        self.assertNotIn(key[:8], r.result)

    def test_echoed_base64_key_not_in_json_or_report(self):
        # Review P3: a gateway echoing the base64 key in an error body.
        self.store_key()
        enc = base64.b64encode(self.mock_key.encode()).decode()

        def echo(handler, body):
            return handler._send_json(418, {"error": {"message": "Echo credential=" + enc}})
        self.server.RequestHandlerClass._messages = echo
        r = self.run_cli("check", "--only", "7", "--json")
        self.assertNotIn(enc, r.stdout + r.stderr)
        self.assertNotIn(self.mock_key, recover(r.stdout))
        self.run_cli("report-problem")
        rep = list((self.home / "dfir-lab").glob("problem-report-*.txt"))[-1].read_text()
        self.assertNotIn(enc, rep)


class ReplayRedactionTests(helpers.LabTestCase):
    """Review H2: replay printed a key split over two output lines."""
    start_mock = False

    def test_split_key_in_player(self):
        from dfir_lab import replay
        key = "sk-ReviewAbCdEf0123456789+/"
        red_reset = redact.reset(key)
        self.assertTrue(red_reset.active)
        out = []
        replay.Player([{"type": "tool_result", "output": key[:10] + "\n" + key[10:]}],
                      input_fn=lambda: "q", output_fn=lambda s="", **k: out.append(s)).run()
        self.assertNotIn(key, "".join("".join(out).split()))

    def test_long_assistant_text_wrapped(self):
        from dfir_lab import replay
        key = helpers.random_key(n=120)
        redact.reset(key)
        lines = replay.render_record({"type": "assistant_text",
                                      "text": "Key: " + key + " and then" + " more words" * 20})
        self.assertGreater(len(lines), 1)
        self.assertNotIn(key, "".join(lines))
        self.assertNotIn(key[:40], "\n".join(lines))

    def test_split_key_via_cli(self):
        # Review P3, exactly: the recording's tool output holds K[:10] + "\n" + K[10:].
        self.store_key()
        key = self.mock_key
        (self.case / "fallback").chmod(0o755)
        (self.case / "fallback" / "demo.jsonl").write_text(
            json.dumps({"type": "tool_result", "output": key[:10] + "\n" + key[10:]}) + "\n")
        r = self.run_cli("replay", "demo", input="q\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(key, "".join(r.stdout.split()))


class SourceDisciplineTests(unittest.TestCase):
    """All student-facing output goes through ui.out/err (which redact)."""

    def test_no_bare_print_or_stdout_writes(self):
        pkg = helpers.LAB / "lib" / "dfir_lab"
        exempt = {"ui.py", "make_checkpoints.py", "import_transcript.py"}  # instructor tools
        offenders = []
        for p in pkg.glob("*.py"):
            if p.name in exempt:
                continue
            text = p.read_text()
            for pat in (r"(?<![\w.])print\(", r"sys\.stdout\.write", r"sys\.stderr\.write"):
                if re.search(pat, text):
                    offenders.append("%s: %s" % (p.name, pat))
        self.assertEqual(offenders, [])


class EndToEndRedactionTests(helpers.LabTestCase):
    """Store a random key that the mock accepts, put it into tool output and
    the worksheet, run every command, then search everything written."""

    def test_key_never_written_anywhere(self):
        rng = random.Random()
        for attempt in range(2):
            key = helpers.random_key(rng)
            self.server.state.key = key
            self.mock_key = key
            r = self.store_key(key)
            outputs = [r.stdout + r.stderr]
            # The key leaks into the student's work: analysis output and worksheet.
            (self.case / "analysis").mkdir(exist_ok=True)
            (self.case / "analysis" / ("tool-output-%d.txt" % attempt)).write_text(
                "env dump\nANTHROPIC_API_KEY=%s\nwrapped: %s\n%s\n" % (key, key[:7], key[7:]))
            (self.case / "worksheet.md").write_text(
                (self.case / "worksheet.template.md").read_text() + "\nFinding: key %s\n" % key)
            for fault in ("none", "wrong-key", "server-error", "upstream-auth", "cooldown"):
                self.set_fault(fault)
                for args in (["check"], ["check", "--json"], ["setup-key", "--status"],
                             ["report-problem"], ["progress"], ["checkpoint", "1", key],
                             ["hint", "1"], ["version"], ["help"]):
                    res = self.run_cli(*args)
                    outputs.append(res.stdout + res.stderr)
            self.set_fault("none")
            for out in outputs:
                self.assertNoKey(out, key)
            # Every file the toolkit wrote (reports, logs, progress).
            state = self.home / "dfir-lab"
            self.assertNoKey(self.all_text_under(state), key)
            # The fake Claude Code's argv and environment.
            if self.fake_log.exists():
                self.assertNoKey(self.fake_log.read_text(), key)
            # The mock gateway's request log.
            self.assertNoKey(self.mock_log.read_text(), key)
            redact.reset()


if __name__ == "__main__":
    unittest.main()
