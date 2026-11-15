# Lab setup script (`dist/bootstrap.sh`)

One self-contained bash script turns an untouched SANS SIFT Workstation VM into
a ready lab machine. Students download the disk image into
`~/Downloads` inside the VM, then paste one command into a terminal in the VM
(the script downloads the log kit itself):

```
curl -fsSLO https://raw.githubusercontent.com/<owner>/ai-dfir-lab/v<version>/dist/bootstrap.sh && echo "<sha256>  bootstrap.sh" | sha256sum -c && bash bootstrap.sh
```

`python3 bootstrap/build.py` prints the exact command and writes it into the
repository's `README.md` between `<!-- BEGIN STUDENT COMMAND -->` and
`<!-- END STUDENT COMMAND -->`.

## Files

| Path | What it is |
|---|---|
| `release.conf` | Every release setting: repository, version, pinned Claude Code, Protocol SIFT commit, gateway URL, model names, Claude Code effort level, the model of the optional subscription route (`SUBSCRIPTION_MODEL`, default `sonnet`), case id, kit file pattern, accepted kit SHA-256 and the kit's path in this repository, and the disk image (zip name pattern, size, SHA-256, the files it holds with their SHA-256, short name, mount point). |
| `bootstrap.sh.in` | The script template. `@@RELEASE_SETTINGS@@` is replaced by the build. |
| `lab.conf.in` | Template for `/etc/dfir-lab/lab.conf`. `@NAME@` values come from `release.conf`; `@CLAUDE_BINARY@` is filled in on the VM. |
| `case-overlay/<case id>/` | Files copied over the case kit: the case `CLAUDE.md`, `ticket.md`, `ai-report.md`, `checkpoints.json`, replay recordings in `fallback/`. They ship with the script, so they can change without a new kit. |
| `checkpoint-sources/` | Clear-text checkpoint sources. Only the PLACEHOLDER one is tracked. The real one, `checkpoint-sources/real/szechuan.source.json`, holds the answers and is git-ignored; never commit it. |
| `build.py` | Builds `dist/bootstrap.sh` (+ `.sha256`), the README command, a test build, and the stand-in kit. |

## Build

```sh
python3 bootstrap/build.py               # dist/bootstrap.sh, dist/bootstrap.sh.sha256, README.md command
python3 bootstrap/build.py --check       # build twice, confirm the same SHA-256, then build
python3 bootstrap/build.py standin-kit   # build/bootstrap/szechuan-kit-standin.zip (tests only)
python3 bootstrap/build.py --test-kit-sha256 <stand-in hash> --out build/bootstrap/test-dist
                                         # TEST BUILD that also accepts the stand-in kit; refused
                                         # for dist/, never touches README.md
```

`dist/bootstrap.sh` accepts only the real kit. The mock-gateway VM test serves
the test build instead.

The payload holds `lab/` without its tests and instructor tools, the filled-in
`lab.conf.in`, the case overlay and `overlay.sha256`. Archive members have fixed
order, timestamps (2026-01-01), owners and modes; text is normalised to LF. The
output depends only on the repository contents and zlib (built and checked in
WSL Ubuntu, Python 3.12).

Rebuild the checkpoints of the overlay with the toolkit's own tool (random
salts, so do this once and commit the result, not on every build):

```sh
python3 lab/bin/dfir-lab-make-checkpoints bootstrap/checkpoint-sources/<source>.json \
    -o bootstrap/case-overlay/szechuan/checkpoints.json
```

## What the script does

1. **Preflight.** Refuses to run as root. Checks the user (`sansforensics`), Ubuntu 24.04 and x86-64, otherwise asks (`--yes` answers). Needs 3 GB free. Probes `claude.ai`, `downloads.claude.ai` and `github.com` (a failure is a warning). Starts `~/dfir-lab/logs/bootstrap-<UTC>.log`.
2. **Packages and updater pop-ups.** First the Software Updater pop-ups are turned off, because refreshing the package lists makes Ubuntu's `update-notifier` open the Software Updater a few minutes later and `check-new-release-gtk` offers Ubuntu 26.04: `Prompt=never` in `/etc/update-manager/release-upgrades` (the documented setting; the release check then does nothing), a user autostart entry `~/.config/autostart/update-notifier.desktop` with `Hidden=true`, and the running `update-notifier` and any updater window it opened are stopped. Nothing is upgraded. Then, only if missing: `open-vm-tools-desktop` and `zenity`, after waiting for the apt lock and `apt-get update`. A failure is a warning. Note: installing `open-vm-tools-desktop` also upgrades `open-vm-tools` to the matching version and restarts `vmtoolsd`.
3. **Claude Code.** The official installer (`https://claude.ai/install.sh <version>`); the version is checked with `claude --version` and forced with `claude install --force <version>` if it differs. Lives at `~/.local/bin/claude`; that path goes into `lab.conf`.
4. **Protocol SIFT.** Cloned at the pinned commit into a temporary folder, commit checked, its own `install.sh` run as the student with our Claude Code on `PATH`. A marker `~/.claude/.protocol-sift-install` (commit + file hashes) makes reruns skip it. If it cannot be fetched, the setup reports INCOMPLETE.
5. **Toolkit.** `lab/install.sh` from the payload with the generated `lab.conf`, then `dfir-lab-configure-claude`, which merges the course settings into Protocol SIFT's `~/.claude/settings.json` (and does nothing when they are already there).
6. **Case kit.** `--kit PATH`, else the newest `CASE_KIT_PATTERN` file in `~/Downloads`, `~/Desktop` or `~` whose SHA-256 is in `CASE_KIT_SHA256` (the offline fallback), else the kit is downloaded from `https://raw.githubusercontent.com/<REPO_OWNER>/<REPO_NAME>/v<VERSION>/<CASE_KIT_REPO_PATH>`, the same release as the script, and accepted only if its SHA-256 is in `CASE_KIT_SHA256`. A failed download (with its HTTP status) or a mismatched one is reported plainly and leaves the setup INCOMPLETE with "run the same setup command again"; an unverified kit is never installed. An explicit `--kit` is never replaced by a download. The zip must hold one top-level folder with `evidence/`, `derived/`, `README.md`, `MANIFEST.sha256`, `tools.list`; no absolute paths, `..` or links. It is unpacked under `/cases/.<id>.staging` (no spaces), checked with its own `MANIFEST.sha256`, the overlay is copied on top, and it replaces `/cases/<id>/` (student folders `analysis/ reports/ exports/ .claude/` and the unpacked disk image `disk/` are carried over). Everything from the kit and overlay is root-owned and read-only for the student. `MANIFEST.lab.sha256` lists every kit file and every overlay file; `lab.conf` points the toolkit's manifest check at it. `.dfir-lab/` keeps the accepted zip hash, the kit file hashes and the overlay hashes, so a rerun verifies without the zip and a new overlay is applied without the kit. No kit: everything else is still done and the result is INCOMPLETE.
7. **Disk image.** It holds live malware, and the step says so in one line: it stays inside the VM. Already installed and verified (every file present, root-owned, mode 444, SHA-256 matching `IMAGE_FILES`, short links in place): skipped. Otherwise `--image PATH`, else the newest `IMAGE_ZIP_PATTERN` (`DC01-E01*.zip`) file in `~/Downloads`, `~/Desktop` or `~` with exactly `IMAGE_ZIP_SIZE` bytes and `IMAGE_ZIP_SHA256`. Free space on `/cases` is checked first (the unpacked files plus 0.5 GB; the message says how much is needed and free). The zip must hold exactly `IMAGE_ZIP_DIR/` and the listed files, no links. They are unpacked flat into `/cases/.<id>-disk.staging`, made root-owned, mode 444 in a 755 folder, verified, given short links `DC01.E01 -> 20200918_0347_CDrive.E01` and `DC01.E02 -> ...E02`, and moved to `/cases/<id>/disk/` (an empty case folder marked `.dfir-lab/` is created if the kit is not installed yet). Then the student is asked whether to delete the zip to free 4.5 GB (`[Y/n]`, only for a zip in the home folder; `--yes` or no terminal keeps it and says so). No image: everything else is still done and the result is INCOMPLETE with download instructions. Mounting is the toolkit's job (`dfir-lab mount`); the readiness check in step 9 mounts it.
8. **Desktop.** In the student's session: idle screen lock and blanking off; Chromium (`chromium_chromium.desktop`) for `http`, `https` and `text/html`; a marked block in `~/.bashrc` (`set +o noclobber`, `~/.local/bin` on `PATH`); the toolkit's desktop shortcuts checked with `gio info` for what this GNOME's desktop icons (DING 47) need to launch a `.desktop` file: content type `application/x-desktop`, `access::can-execute`, not writable by others, `~/Desktop` not writable by others, `metadata::trusted` = `true` (set with `gio set` if missing). Each is a warning if it fails.
9. **Finish.** `dfir-lab check --quick --json`: checks 1-3 must pass (check 3 also verifies the disk image, mounts it read-only and noexec at `/mnt/dc01` and reads `Windows/System32/config/SYSTEM` through the mount); a missing key (4) is the next step; an unreachable gateway (5) is a note. Summary with versions, COMPLETE or INCOMPLETE and why, log path, the next two steps and one line about the optional subscription route. If the student switched to their own Claude subscription (`dfir-lab use subscription`, stored in `~/dfir-lab/route.json`, which the script keeps), checks 4 and 5 are the sign-in and Anthropic's service, a missing sign-in is "not yet", and the next steps say so; the toolkit step writes the subscription route's settings.

Upgrading: running a newer script on a VM set up with an older one reinstalls the toolkit and keeps the key, the route, the unpacked disk image (verified, not unpacked again), the case files, checkpoint progress and the student's work.

Exit codes: 0 complete, 1 incomplete, 2 refused, 3 stopped by an error. Running it again is always safe; a run on a complete machine changes nothing and says so.

`bash bootstrap.sh --uninstall` unmounts the disk image (and stops if it is in use), removes the toolkit, the case files including the disk image, the `.bashrc` block and the updater autostart override, restores the Claude Code settings Protocol SIFT left, and keeps Claude Code, Protocol SIFT, the key and the student's work. `--purge` removes those too. `Prompt=never` stays.

## Release checklist

1. `release.conf`: `REPO_OWNER`, `VERSION` (bump for every published change), the real kit's hash only in `CASE_KIT_SHA256`, and the disk image values (measured in a SIFT VM). `MODEL_MAIN`/`MODEL_SMALL` (`claude-haiku-5-5`) and `CLAUDE_EFFORT` (`low`) were tested against the gateway on 2026-11-14.
2. `case-overlay/szechuan/`: `ticket.md`, `ai-report.md`, `CLAUDE.md`, `checkpoints.json` (built with `dfir-lab-make-checkpoints` from the git-ignored `checkpoint-sources/real/szechuan.source.json`) and the recording in `fallback/`.
3. `python3 bootstrap/build.py --check` (writes the command into `README.md`), run `vmtest/Test-Bootstrap.ps1 -Gateway real -KeyFile <file> -RunName rc-<x>` and the mock run (the mock run also tests the subscription route up to the sign-in, step S, and the upgrade from the previous release, step UP), commit `dist/bootstrap.sh`, `dist/bootstrap.sh.sha256` and `README.md`, tag `v<VERSION>`, push.

## Testing the kit download before the repository is public

While the repository is private, `raw.githubusercontent.com` answers 404 for the
kit. For tests only, the environment variable `DFIR_LAB_KIT_URL` replaces the
kit's download URL (the SHA-256 check still applies):

```sh
DFIR_LAB_KIT_URL=http://192.168.172.1:8471/kit/szechuan-kit-0.1.0.zip bash bootstrap.sh
```

`vmtest/Test-Bootstrap.ps1` serves the kit next to the script and uses this. It
is not mentioned to students. The real GitHub download can only be confirmed
once the repository is public and tag `v<VERSION>` is pushed.
