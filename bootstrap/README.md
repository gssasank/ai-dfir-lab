# Lab setup script (`dist/bootstrap.sh`)

One self-contained bash script turns an untouched SANS SIFT Workstation VM into
a ready lab machine. Students paste one command into a terminal in the VM:

```
curl -fsSLO https://raw.githubusercontent.com/<owner>/ai-dfir-lab/v<version>/dist/bootstrap.sh && echo "<sha256>  bootstrap.sh" | sha256sum -c && bash bootstrap.sh
```

`python3 bootstrap/build.py` prints the exact command once `REPO_OWNER` is set.

## Files

| Path | What it is |
|---|---|
| `release.conf` | Every release setting: repository, version, pinned Claude Code, Protocol SIFT commit, gateway URL, model names, case id, kit file pattern, accepted kit SHA-256 values. |
| `bootstrap.sh.in` | The script template. `@@RELEASE_SETTINGS@@` is replaced by the build. |
| `lab.conf.in` | Template for `/etc/dfir-lab/lab.conf`. `@NAME@` values come from `release.conf`; `@CLAUDE_BINARY@` is filled in on the VM. |
| `case-overlay/<case id>/` | Files copied over the case kit: the case `CLAUDE.md`, `ticket.md`, `checkpoints.json`, replay recordings in `fallback/`. They ship with the script, so they can change without a new kit. |
| `checkpoint-sources/` | Clear-text checkpoint sources. Only the PLACEHOLDER one belongs here; a real one holds answers and must never be committed. |
| `build.py` | Builds `dist/bootstrap.sh` (+ `.sha256`) and the stand-in kit. |

## Build

```sh
python3 bootstrap/build.py               # dist/bootstrap.sh and dist/bootstrap.sh.sha256
python3 bootstrap/build.py --check       # build twice, confirm the same SHA-256, then build
python3 bootstrap/build.py standin-kit   # build/bootstrap/szechuan-kit-standin.zip (tests only)
```

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
2. **Packages.** Only if missing: `open-vm-tools-desktop` and `zenity`, after waiting for the apt lock and `apt-get update`. A failure is a warning. Note: installing `open-vm-tools-desktop` also upgrades `open-vm-tools` to the matching version and restarts `vmtoolsd`.
3. **Claude Code.** The official installer (`https://claude.ai/install.sh <version>`); the version is checked with `claude --version` and forced with `claude install --force <version>` if it differs. Lives at `~/.local/bin/claude`; that path goes into `lab.conf`.
4. **Protocol SIFT.** Cloned at the pinned commit into a temporary folder, commit checked, its own `install.sh` run as the student with our Claude Code on `PATH`. A marker `~/.claude/.protocol-sift-install` (commit + file hashes) makes reruns skip it. If it cannot be fetched, the setup reports INCOMPLETE.
5. **Toolkit.** `lab/install.sh` from the payload with the generated `lab.conf`, then `dfir-lab-configure-claude`, which merges the course settings into Protocol SIFT's `~/.claude/settings.json` (and does nothing when they are already there).
6. **Case kit.** `--kit PATH`, else the newest `CASE_KIT_PATTERN` file in `~/Downloads`, `~/Desktop` or `~` whose SHA-256 is in `CASE_KIT_SHA256`. The zip must hold one top-level folder with `evidence/`, `derived/`, `README.md`, `MANIFEST.sha256`, `tools.list`; no absolute paths, `..` or links. It is unpacked under `/cases/.<id>.staging` (no spaces), checked with its own `MANIFEST.sha256`, the overlay is copied on top, and it replaces `/cases/<id>/` (student folders `analysis/ reports/ exports/ .claude/` are carried over). Everything from the kit and overlay is root-owned and read-only for the student. `MANIFEST.lab.sha256` lists every kit file and every overlay file; `lab.conf` points the toolkit's manifest check at it. `.dfir-lab/` keeps the accepted zip hash, the kit file hashes and the overlay hashes, so a rerun verifies without the zip and a new overlay is applied without the kit. No kit: everything else is still done and the result is INCOMPLETE with download instructions.
7. **Desktop.** In the student's session: idle screen lock and blanking off; Chromium (`chromium_chromium.desktop`) for `http`, `https` and `text/html`; a marked block in `~/.bashrc` (`set +o noclobber`, `~/.local/bin` on `PATH`); the toolkit's desktop shortcuts checked with `gio info` for what this GNOME's desktop icons (DING 47) need to launch a `.desktop` file: content type `application/x-desktop`, `access::can-execute`, not writable by others, `~/Desktop` not writable by others, `metadata::trusted` = `true` (set with `gio set` if missing). Each is a warning if it fails.
8. **Finish.** `dfir-lab check --quick --json`: checks 1-3 must pass; a missing key (4) is the next step; an unreachable gateway (5) is a note. Summary with versions, COMPLETE or INCOMPLETE and why, log path and the next two steps.

Exit codes: 0 complete, 1 incomplete, 2 refused, 3 stopped by an error. Running it again is always safe; a run on a complete machine changes nothing and says so.

`bash bootstrap.sh --uninstall` removes the toolkit, the case files and the `.bashrc` block, restores the Claude Code settings Protocol SIFT left, and keeps Claude Code, Protocol SIFT, the key and the student's work. `--purge` removes those too.

## When the real case kit and repository owner are known

1. `release.conf`: set `REPO_OWNER`; replace `CASE_KIT_SHA256` with the real kit's SHA-256 (several values, space separated, if more than one kit version is valid); adjust `CASE_KIT_PATTERN`, `CASE_TITLE`, `MODEL_MAIN`/`MODEL_SMALL` once the gateway names are confirmed; bump `VERSION`.
2. `case-overlay/szechuan/`: replace `ticket.md`, `checkpoints.json` (built with `dfir-lab-make-checkpoints` from a source that stays out of the repository) and the recordings in `fallback/`; review `CLAUDE.md`.
3. `python3 bootstrap/build.py --check`, run `vmtest/Test-Bootstrap.ps1` with the real kit copied to `build/bootstrap/` (or adapt `$KitName`), commit `dist/bootstrap.sh`, tag `v<VERSION>`, push, and publish the printed command.
