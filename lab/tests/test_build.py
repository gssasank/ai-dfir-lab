"""bootstrap/build.py: release settings for the disk image, the test build for
the stand-in kit, and the student command written into README.md."""

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

import helpers

SPEC = importlib.util.spec_from_file_location("bootstrap_build",
                                              helpers.REPO / "bootstrap" / "build.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)

STANDIN = "b" * 64


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfir-build-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_release_has_only_the_real_kit_and_the_image(self):
        rel = build.read_release(build.HERE / "release.conf")
        self.assertEqual(rel["CASE_KIT_SHA256"].split(),
                         ["86a32199fc9d506afb785f4f6705be84484f510027dc12d79533fbd1fa4107ed"])
        self.assertEqual(rel["IMAGE_ZIP_SHA256"],
                         "efe06d12388dbc000fa4ae306746ddaca3893a6cdbd55311b52f5833e717acd9")
        self.assertEqual(rel["IMAGE_ZIP_SIZE"], "4836649413")
        self.assertEqual(rel["IMAGE_MOUNT_POINT"], "/mnt/dc01")
        self.assertEqual((rel["REPO_OWNER"], rel["VERSION"]), ("gssasank", "1.1.0"))

    def test_kit_comes_from_the_same_release_as_the_script(self):
        rel = build.read_release(build.HERE / "release.conf")
        self.assertEqual(rel["CASE_KIT_URL"],
                         "https://raw.githubusercontent.com/gssasank/ai-dfir-lab/v1.1.0/"
                         "kit/szechuan-kit-0.1.0.zip")
        self.assertTrue(build.student_command(rel, "0" * 64).startswith(
            "curl -fsSLO https://raw.githubusercontent.com/gssasank/ai-dfir-lab/v1.1.0/"))
        self.assertTrue((helpers.REPO / rel["CASE_KIT_REPO_PATH"]).is_file()
                        or not (helpers.REPO / ".git").exists())
        rel2, out, _, _ = build.build(self.tmp)
        text = out.read_text()
        self.assertIn("R_CASE_KIT_URL='%s'" % rel["CASE_KIT_URL"], text)
        self.assertIn('DFIR_LAB_KIT_URL:-$R_CASE_KIT_URL', text)

    def test_lab_conf_gets_the_disk_settings(self):
        rel = build.read_release(build.HERE / "release.conf")
        conf = dict(build.payload_members(rel))["lab.conf.in"].decode()
        self.assertIn("image = /cases/szechuan/disk/DC01.E01", conf)
        self.assertIn("ewf_mount = /mnt/ewf_dc01", conf)
        self.assertIn("zip_name = DC01-E01.zip", conf)
        self.assertIn("20200918_0347_CDrive.E02:73f87df9", conf)
        self.assertIn("ai_report = ai-report.md", conf)

    def test_payload_carries_the_mount_helper_executable(self):
        rel = build.read_release(build.HERE / "release.conf")
        names = dict(build.payload_members(rel))
        self.assertIn("lab/libexec/disk-mount", names)
        self.assertIn("lab/libexec/disk-mount", build.EXECUTABLE)
        self.assertIn("case-overlay/ai-report.md", names)

    def test_test_build_never_in_dist(self):
        with self.assertRaises(build.BuildError):
            build.build(build.DIST, [STANDIN])

    def test_test_build_accepts_the_extra_kit(self):
        rel, out, digest, _ = build.build(self.tmp, [STANDIN])
        text = out.read_text()
        self.assertIn("R_CASE_KIT_SHA256='86a32199fc9d506afb785f4f6705be84484f510027dc12d79533fbd1fa4107ed %s'"
                      % STANDIN, text)
        rel2, out2, digest2, _ = build.build(self.tmp / "again", [STANDIN])
        self.assertEqual(digest, digest2)

    def test_readme_command_between_markers(self):
        readme = self.tmp / "README.md"
        readme.write_text("# Title\n\n<!-- BEGIN STUDENT COMMAND -->\n```\nold\n```\n"
                          "<!-- END STUDENT COMMAND -->\n\nAfter.\n")
        self.assertTrue(build.write_readme_command("curl x && bash bootstrap.sh", readme))
        text = readme.read_text()
        self.assertIn("<!-- BEGIN STUDENT COMMAND -->\n```\ncurl x && bash bootstrap.sh\n```\n"
                      "<!-- END STUDENT COMMAND -->\n\nAfter.\n", text)
        self.assertFalse(build.write_readme_command("curl x && bash bootstrap.sh", readme))
        readme.write_text("no markers\n")
        with self.assertRaises(build.BuildError):
            build.write_readme_command("x", readme)

    def test_repository_readme_has_the_built_command(self):
        rel = build.read_release(build.HERE / "release.conf")
        digest = (helpers.REPO / "dist" / "bootstrap.sh.sha256").read_text().split()[0]
        self.assertIn(build.student_command(rel, digest),
                      (helpers.REPO / "README.md").read_text())
        self.assertIn("/v1.1.0/dist/bootstrap.sh", build.student_command(rel, digest))


if __name__ == "__main__":
    unittest.main()
