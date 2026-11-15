"""dfir-lab mount / unmount / mount --status, the disk part of check 3, and
the disk mount in dfir-lab start.

The real root helper (libexec/disk-mount) needs root, FUSE and the E01; it is
tested in a VM by vmtest/Test-Bootstrap.ps1. Here a fake helper "mounts" by
writing a mountinfo file and a fake /sys/block, and by creating the probe file
under the mount point.
"""

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

import helpers
from dfir_lab import casekit, disk
from dfir_lab.config import Config
from dfir_lab.errors import LabError

FAKE_HELPER = textwrap.dedent(r'''
    import os, sys
    from pathlib import Path
    action, image, ewf, mnt = sys.argv[1:5]
    mode = os.environ.get("FAKE_DISK_MODE", "ok")
    info = Path(os.environ["DFIR_LAB_MOUNTINFO"])
    sysb = Path(os.environ["DFIR_LAB_SYS_BLOCK"])
    with open(os.environ["FAKE_DISK_LOG"], "a") as fh:
        fh.write(action + "\n")
    def result(code, text):
        print("RESULT %d %s" % (code, text)); sys.exit(code)
    if mode == "password":
        print("sudo: a password is required"); sys.exit(1)
    if action == "mount":
        if mode == "inuse":
            result(21, "%s is already in use (1 mount(s): /dev/sdb1 ext4;)" % mnt)
        opts = "ro,nosuid,nodev,relatime" if mode == "unsafe" else "ro,nosuid,nodev,noexec,relatime"
        info.write_text(
            "184 29 0:53 / %s rw,nosuid,nodev,relatime shared:5 - fuse /dev/fuse rw,allow_other\n"
            "671 29 7:9 / %s %s shared:6 - fuseblk /dev/loop9 ro,allow_other\n" % (ewf, mnt, opts))
        (sysb / "loop9" / "loop").mkdir(parents=True, exist_ok=True)
        (sysb / "loop9" / "loop" / "backing_file").write_text(ewf + "/ewf1\n")
        (sysb / "loop9" / "ro").write_text("1\n")
        p = Path(mnt) / "Windows" / "System32" / "config"
        p.mkdir(parents=True, exist_ok=True)
        (p / "SYSTEM").write_bytes(b"regf" + b"\0" * 60)
        result(0, "mounted read-only at %s" % mnt)
    if mode == "busy":
        result(30, "%s is busy" % mnt)
    if not info.read_text().strip():
        result(11, "nothing was mounted")
    info.write_text("")
    import shutil
    shutil.rmtree(Path(mnt) / "Windows", ignore_errors=True)
    result(0, "unmounted")
''')


class DiskTestCase(helpers.LabTestCase):
    start_mock = False

    def setUp(self):
        super().setUp()
        self.diskdir = self.case / "disk"
        self.diskdir.mkdir()
        segs = []
        for name, data in (("LONG_NAME.E01", b"segment one\n" * 100),
                           ("LONG_NAME.E02", b"segment two\n" * 50),
                           ("LONG_NAME.E01.txt", b"acquisition log\n")):
            (self.diskdir / name).write_bytes(data)
            os.chmod(self.diskdir / name, 0o444)
            segs.append("%s:%s" % (name, hashlib.sha256(data).hexdigest()))
        os.symlink("LONG_NAME.E01", self.diskdir / "DC01.E01")
        os.symlink("LONG_NAME.E02", self.diskdir / "DC01.E02")
        self.mnt = self.tmp / "mnt" / "dc01"
        self.ewf = self.tmp / "mnt" / "ewf_dc01"
        self.mnt.mkdir(parents=True)
        self.ewf.mkdir()
        self.mountinfo = self.tmp / "mountinfo"
        self.mountinfo.write_text("")
        self.sysblock = self.tmp / "sysblock"
        self.sysblock.mkdir()
        self.helper = self.tmp / "fake-disk-mount.py"
        self.helper.write_text(FAKE_HELPER)
        self.helper_log = self.tmp / "helper.log"
        self.segments = " ".join(segs)
        self.disk_conf()
        os.environ.update(self.disk_env())

    def disk_conf(self, extra=None):
        values = {("disk", "image"): str(self.diskdir / "DC01.E01"),
                  ("disk", "segments"): self.segments,
                  ("disk", "mount_point"): str(self.mnt),
                  ("disk", "ewf_mount"): str(self.ewf),
                  ("disk", "zip_name"): "DC01-E01.zip"}
        values.update(extra or {})
        self.write_conf(values)

    def disk_env(self, **extra):
        e = {"DFIR_LAB_MOUNTINFO": str(self.mountinfo), "DFIR_LAB_SYS_BLOCK": str(self.sysblock),
             "DFIR_LAB_DISK_HELPER": str(self.helper), "DFIR_LAB_SUDO": sys.executable,
             "FAKE_DISK_LOG": str(self.helper_log)}
        e.update(extra)
        return e

    def run_cli(self, *args, env=None, **kw):
        e = self.disk_env()
        e.update(env or {})
        return super().run_cli(*args, env=e, **kw)

    def helper_calls(self):
        return self.helper_log.read_text().split() if self.helper_log.exists() else []

    def ds(self):
        return disk.settings(Config())


class MountTableTests(DiskTestCase):
    def test_parse_mountinfo_unescapes_and_reads_options(self):
        m = disk.parse_mountinfo(
            "36 35 98:0 / /mnt/with\\040space ro,noexec,relatime shared:1 - ext4 /dev/sda1 rw\n"
            "garbage line\n")
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["mount_point"], "/mnt/with space")
        self.assertEqual(m[0]["options"], {"ro", "noexec", "relatime"})
        self.assertEqual((m[0]["fstype"], m[0]["source"]), ("ext4", "/dev/sda1"))

    def test_settings_from_lab_conf(self):
        ds = self.ds()
        self.assertEqual(ds.image, self.diskdir / "DC01.E01")
        self.assertEqual([n for n, _ in ds.segments],
                         ["LONG_NAME.E01", "LONG_NAME.E02", "LONG_NAME.E01.txt"])
        self.assertEqual(ds.raw, self.ewf / "ewf1")

    def test_no_disk_configured(self):
        self.write_conf()
        self.assertIsNone(disk.settings(Config()))
        r = self.run_cli("mount")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no disk image configured", r.stdout + r.stderr)

    def test_bad_segments_setting(self):
        self.disk_conf({("disk", "segments"): "LONG_NAME.E01:abc"})
        with self.assertRaises(LabError):
            disk.settings(Config())

    def test_status_not_mounted_then_missing(self):
        st = disk.status(self.ds())
        self.assertEqual(st.state, "not-mounted")
        self.assertTrue(st.segments_present)
        os.chmod(self.diskdir, 0o755)
        (self.diskdir / "LONG_NAME.E02").unlink()
        st = disk.status(self.ds())
        self.assertEqual(st.missing, ["LONG_NAME.E02"])

    def write_table(self, opts="ro,nosuid,nodev,noexec,relatime", source="/dev/loop9",
                    backing=None, ro="1", stacked=False):
        lines = ["184 29 0:53 / %s rw,nosuid,nodev shared:5 - fuse /dev/fuse rw" % self.ewf]
        if stacked:
            lines.append("670 29 7:8 / %s ro,nosuid,nodev,noexec shared:6 - fuseblk /dev/loop8 ro"
                         % self.mnt)
        lines.append("671 29 7:9 / %s %s shared:7 - fuseblk %s ro" % (self.mnt, opts, source))
        self.mountinfo.write_text("\n".join(lines) + "\n")
        d = self.sysblock / "loop9"
        (d / "loop").mkdir(parents=True, exist_ok=True)
        (d / "loop" / "backing_file").write_text((backing or str(self.ewf / "ewf1")) + "\n")
        (d / "ro").write_text(ro + "\n")
        p = self.mnt / "Windows" / "System32" / "config"
        p.mkdir(parents=True, exist_ok=True)
        (p / "SYSTEM").write_bytes(b"regf....")

    def test_status_mounted_and_safe(self):
        self.write_table()
        st = disk.status(self.ds())
        self.assertEqual(st.state, "mounted", st.problems)
        self.assertTrue(st.probe_ok)

    def test_status_requires_every_safe_option(self):
        for missing in disk.SAFE_OPTIONS:
            opts = ",".join(o for o in ("ro", "nosuid", "nodev", "noexec", "relatime") if o != missing)
            self.write_table(opts=opts)
            st = disk.status(self.ds())
            self.assertEqual(st.state, "problem", missing)
            self.assertIn(missing, " ".join(st.problems))

    def test_status_rejects_writable_loop_foreign_source_and_stacking(self):
        self.write_table(ro="0")
        self.assertIn("not read-only", " ".join(disk.status(self.ds()).problems))
        self.write_table(backing="/somewhere/else.raw")
        self.assertIn("not from the case's disk image", " ".join(disk.status(self.ds()).problems))
        self.write_table(source="/dev/sdb1")
        self.assertEqual(disk.status(self.ds()).state, "problem")
        self.write_table(stacked=True)
        self.assertIn("stacked", " ".join(disk.status(self.ds()).problems))

    def test_status_needs_the_probe_file(self):
        self.write_table()
        (self.mnt / "Windows" / "System32" / "config" / "SYSTEM").write_bytes(b"MZ\x90\x00")
        st = disk.status(self.ds())
        self.assertEqual(st.state, "problem")
        self.assertIn("cannot be read", " ".join(st.problems))


class MountCommandTests(DiskTestCase):
    def test_mount_is_idempotent(self):
        r = self.run_cli("mount")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("mounted read-only (noexec) at %s" % self.mnt, r.stdout)
        self.assertIn("never run anything from it", r.stdout)
        r = self.run_cli("mount")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("already mounted", r.stdout)
        # The second mount was decided from the mount table: the helper ran once.
        self.assertEqual(self.helper_calls(), ["mount"])

    def test_status_command_needs_no_helper(self):
        r = self.run_cli("mount", "--status")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Mounted    : no", r.stdout)
        self.assertIn("dfir-lab mount", r.stdout)
        self.run_cli("mount")
        r = self.run_cli("mount", "--status")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("read-only and noexec at %s" % self.mnt, r.stdout)
        self.assertEqual(self.helper_calls(), ["mount"])

    def test_mount_refuses_unconfirmed_safety(self):
        r = self.run_cli("mount", env={"FAKE_DISK_MODE": "unsafe"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("could not be confirmed", r.stdout + r.stderr)
        self.assertIn("noexec", r.stdout + r.stderr)

    def test_mount_point_in_use(self):
        r = self.run_cli("mount", env={"FAKE_DISK_MODE": "inuse"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("already used by another mount", r.stdout + r.stderr)
        self.assertIn("dfir-lab unmount", r.stdout + r.stderr)

    def test_foreign_mount_in_the_table_is_refused_without_the_helper(self):
        self.mountinfo.write_text("90 29 0:60 / %s rw,relatime shared:9 - tmpfs dfir-test-tmpfs rw\n"
                                  % self.mnt)
        r = self.run_cli("mount")
        self.assertEqual(r.returncode, 1)
        self.assertIn("already used by another mount (dfir-test-tmpfs, tmpfs)", r.stdout + r.stderr)
        self.assertEqual(self.helper_calls(), [])

    def test_sudo_password(self):
        r = self.run_cli("mount", env={"FAKE_DISK_MODE": "password"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("asked for a password", r.stdout + r.stderr)

    def test_missing_image_tells_what_to_download(self):
        os.chmod(self.diskdir, 0o755)
        (self.diskdir / "LONG_NAME.E01").unlink()
        r = self.run_cli("mount")
        self.assertEqual(r.returncode, 1)
        out = r.stdout + r.stderr
        self.assertIn("DC01-E01.zip", out)
        self.assertIn("~/Downloads", out)
        self.assertEqual(self.helper_calls(), [])

    def test_unmount(self):
        r = self.run_cli("unmount")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Nothing was mounted", r.stdout)
        self.assertEqual(self.helper_calls(), [])
        self.run_cli("mount")
        r = self.run_cli("unmount", env={"FAKE_DISK_MODE": "busy"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("is in use", r.stdout + r.stderr)
        self.assertIn("cd ~", r.stdout + r.stderr)
        r = self.run_cli("unmount")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("unmounted", r.stdout)
        self.assertEqual(disk.status(self.ds()).state, "not-mounted")

    def test_output_never_shows_the_partition_offset(self):
        # The partition's start sector is checkpoint 1's answer.
        for args in (("mount",), ("mount", "--status"), ("unmount",)):
            r = self.run_cli(*args)
            self.assertNotRegex((r.stdout + r.stderr).lower(), r"offset|sector")


class SegmentTests(DiskTestCase):
    def test_hashes_checked_and_cached(self):
        ds = self.ds()
        self.assertEqual(disk.verify_segments(ds), ([], []))
        with mock.patch.object(casekit, "sha256_file", side_effect=AssertionError("rehashed")):
            self.assertEqual(disk.verify_segments(ds), ([], []))
        # A changed file is hashed again and reported.
        p = self.diskdir / "LONG_NAME.E02"
        os.chmod(p, 0o644)
        p.write_bytes(b"tampered\n")
        os.chmod(p, 0o444)
        bad, _ = disk.verify_segments(ds)
        self.assertEqual(bad, ["LONG_NAME.E02"])

    def test_writable_segment_reported(self):
        os.chmod(self.diskdir / "LONG_NAME.E01", 0o644)
        _, writable = disk.verify_segments(self.ds())
        self.assertEqual(writable, ["LONG_NAME.E01"])


class CheckThreeTests(DiskTestCase):
    def check3(self, **env):
        r = self.run_cli("check", "--only", "3", "--json", "--no-gui", env=env)
        return r, json.loads(r.stdout[r.stdout.index("{"):])["results"][0]

    def test_check3_verifies_mounts_and_reads(self):
        r, res = self.check3()
        self.assertEqual(res["status"], "PASS", res)
        self.assertIn("evidence files match", res["result"])
        self.assertIn("disk image: 3 files match their SHA-256, mounted read-only and noexec",
                      res["result"])
        self.assertIn("Windows/System32/config/SYSTEM readable", res["result"])
        self.assertEqual(self.helper_calls(), ["mount"])

    def test_check3_missing_image(self):
        os.chmod(self.diskdir, 0o755)
        (self.diskdir / "LONG_NAME.E02").unlink()
        r, res = self.check3()
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("not installed", res["result"])
        self.assertIn("DC01-E01.zip", res["todo"])

    def test_check3_damaged_image(self):
        p = self.diskdir / "LONG_NAME.E01"
        os.chmod(p, 0o644)
        p.write_bytes(b"damaged")
        os.chmod(p, 0o444)
        r, res = self.check3()
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("expected SHA-256 (LONG_NAME.E01)", res["result"])

    def test_check3_mount_failure(self):
        r, res = self.check3(FAKE_DISK_MODE="unsafe")
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("could not be mounted", res["result"])

    def test_check3_without_disk_unchanged(self):
        self.write_conf()
        r, res = self.check3()
        self.assertEqual(res["status"], "PASS")
        self.assertNotIn("disk image", res["result"])


class StartMountTests(DiskTestCase):
    start_mock = True

    def test_start_mounts_first_and_shows_the_lab_files(self):
        (self.case / "ai-report.md").write_text("PRELIMINARY FINDINGS\n")
        self.disk_conf({("case", "ai_report"): "ai-report.md"})
        self.store_key()
        r = self.run_cli("start", env={"DFIR_LAB_NO_EXEC": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Mounting the disk image read-only", r.stdout)
        self.assertIn("Disk image: mounted read-only at %s" % self.mnt, r.stdout)
        self.assertIn("Incident ticket: %s" % (self.case / "ticket.md"), r.stdout)
        self.assertIn("AI report to check (worksheet Section C): %s"
                      % (self.case / "ai-report.md"), r.stdout)
        self.assertIn("/clear", r.stdout)
        self.assertEqual(self.helper_calls(), ["mount"])
        # Mounted already: start does not mount again.
        r = self.run_cli("start", env={"DFIR_LAB_NO_EXEC": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Mounting the disk image", r.stdout)
        self.assertEqual(self.helper_calls(), ["mount"])

    def test_start_continues_without_the_disk_when_nobody_can_answer(self):
        self.store_key()
        r = self.run_cli("start", env={"DFIR_LAB_NO_EXEC": "1", "FAKE_DISK_MODE": "inuse"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("already used by another mount", r.stdout + r.stderr)
        self.assertIn("Disk image: NOT MOUNTED", r.stdout)
        self.assertIn("[no-exec] would run", r.stdout)


class HelperScriptTests(helpers.LabTestCase):
    start_mock = False

    def test_helper_refuses_without_root_and_bad_arguments(self):
        helper = helpers.LAB / "libexec" / "disk-mount"
        if os.geteuid() == 0:
            self.skipTest("runs as root")
        p = subprocess.run(["bash", str(helper), "mount", "/a.E01", "/mnt/e", "/mnt/d"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("RESULT 2 must run as root", p.stdout)
        # The installer ships it executable next to the other helpers.
        self.assertIn('"$SRC/libexec/disk-mount"', (helpers.LAB / "install.sh").read_text())
