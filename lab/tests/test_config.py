import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (sets sys.path)
from dfir_lab.config import REQUIRED, Config
from dfir_lab.errors import EXIT_CONFIG, ConfigError


def write(tmp, text):
    p = Path(tmp) / "lab.conf"
    p.write_text(text)
    return p


FULL = """
[gateway]
base_url = https://gw.example.edu/v1/   ; trailing /v1 tolerated
model_main = m-main
[case]
id = c1
dir = cases/c1
[claude]
extra_env =
    FOO=bar
    # comment line
    BAZ = qux
"""


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dfir-conf-")

    def test_loads_values_and_defaults(self):
        cfg = Config(write(self.tmp, FULL))
        self.assertEqual(cfg.base_url, "https://gw.example.edu")
        self.assertEqual(cfg.model_main, "m-main")
        self.assertEqual(cfg.model_small, "m-main")          # defaults to model_main
        self.assertEqual(cfg.auth_style, "bearer")
        self.assertEqual(cfg.connect_timeout, 8.0)
        self.assertEqual(cfg.lab_version, "0.1.0")
        self.assertEqual(cfg.get_int("requirements", "min_cpus"), 2)
        self.assertEqual(cfg.extra_env, {"FOO": "bar", "BAZ": "qux"})
        # Relative case dir resolves against the conf file's folder.
        self.assertEqual(cfg.case_dir, (Path(self.tmp) / "cases" / "c1").resolve())
        self.assertEqual(cfg.case_path("manifest"), cfg.case_dir / "MANIFEST.sha256")

    def test_missing_file_is_config_error(self):
        with self.assertRaises(ConfigError) as cm:
            Config(Path(self.tmp) / "nope.conf")
        self.assertEqual(cm.exception.code, EXIT_CONFIG)
        self.assertIn("nope.conf", cm.exception.what)
        self.assertTrue(cm.exception.todo)

    def test_each_missing_required_setting_is_named(self):
        for section, key in sorted(REQUIRED):
            lines = []
            for s, k in sorted(REQUIRED):
                if (s, k) != (section, key):
                    lines.append("[%s]\n%s = x\n" % (s, k))
            cfg = Config(write(self.tmp, "\n".join(lines)))
            with self.assertRaises(ConfigError) as cm:
                cfg.require_all()
            self.assertIn("[%s] %s" % (section, key), cm.exception.what)
            self.assertEqual(cm.exception.code, EXIT_CONFIG)

    def test_bad_values(self):
        cfg = Config(write(self.tmp, "[gateway]\nauth_style = token\nbase_url = not a url\n"
                                     "connect_timeout_s = soon\n[claude]\nextra_env = NOEQUALS\n"))
        for attr in ("auth_style", "base_url", "connect_timeout", "extra_env"):
            with self.assertRaises(ConfigError, msg=attr) as cm:
                getattr(cfg, attr)
            self.assertIn(str(cfg.path), cm.exception.what)

    def test_env_override_of_path(self):
        p = write(self.tmp, FULL)
        old = os.environ.get("DFIR_LAB_CONF")
        os.environ["DFIR_LAB_CONF"] = str(p)
        try:
            self.assertEqual(Config().path, p)
        finally:
            if old is None:
                os.environ.pop("DFIR_LAB_CONF")
            else:
                os.environ["DFIR_LAB_CONF"] = old


class ConfigCliTests(helpers.LabTestCase):
    start_mock = False

    def test_cli_missing_setting_exit_3(self):
        self.write_conf({("gateway", "base_url"): None})
        r = self.run_cli("check", "--quick")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("[gateway] base_url", r.stderr)
        self.assertIn("What to do:", r.stderr)
        self.assertNotIn("Traceback", r.stderr + r.stdout)

    def test_cli_missing_conf_exit_3(self):
        r = self.run_cli("check", env={"DFIR_LAB_CONF": str(self.tmp / "missing.conf")})
        self.assertEqual(r.returncode, 3)
        self.assertIn("missing", r.stderr)

    def test_usage_error_exit_2(self):
        r = self.run_cli("check", "--only", "99")
        self.assertEqual(r.returncode, 2)
        r = self.run_cli("no-such-command")
        self.assertEqual(r.returncode, 2)
        self.assertIn("dfir-lab help", r.stderr)


if __name__ == "__main__":
    unittest.main()
