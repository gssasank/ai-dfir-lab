# dfir-lab toolkit

Student toolkit for the CYBERSEC 510 AI-assisted DFIR lab. The contract is
[`docs/specs/lab-toolkit-spec.md`](../docs/specs/lab-toolkit-spec.md).
Python 3.10+ standard library only; POSIX `sh` for the installer and the key helper.

```
lab/
  bin/dfir-lab                    entry point
  bin/dfir-lab-configure-claude   merges course settings into ~/.claude/settings.json
  bin/dfir-lab-make-checkpoints   instructor tool (not installed in the student image)
  bin/dfir-lab-import-transcript  instructor tool (not installed in the student image)
  lib/dfir_lab/                   one module per command + config, redact, gateway, ui
  libexec/key-helper              Claude Code apiKeyHelper (prints the key only)
  libexec/pretool-guard           Claude Code PreToolUse hook (deters obvious reads of the key)
  libexec/disk-mount              root helper for dfir-lab mount / unmount (run through sudo)
  etc/lab.conf.example            every setting, with comments
  share/                          desktop shortcuts, Claude settings fragment, readiness sample
  install.sh                      installer / uninstaller
  tests/                          unittest suite and the sample case kit
tools/mock-gateway/               mock LiteLLM/Anthropic gateway
```

Students fill in their worksheet outside the VM and upload it to the course
site; the toolkit does not package or submit anything. The `[case] worksheet`
and `worksheet_template` settings are optional and empty by default; only when
both are set does `start` copy the template and name the worksheet in its
banner.

The guard hook and the `permissions.deny` rules are a deterrent against the
obvious, accidental read of the key by Claude Code (for example
`cat ~/.config/dfir-lab/duke-api-key`), and of a Claude subscription sign-in
(`~/.claude/.credentials.json`, `claude setup-token`, or the sign-in's token
values). They are not a security boundary: Claude Code runs as the same user
who owns both files, and an indirect shell command can still read them.

## Two routes: the Duke key (default) or the student's own Claude subscription

`dfir-lab use duke | subscription | --status` (and the desktop shortcut "Use my
Claude subscription") chooses how Claude Code reaches a model. The choice is
stored in `~/dfir-lab/route.json` (no file means the Duke route), survives
reboots and re-running the setup script, and is read by `start`, `check`,
`report-problem` and `dfir-lab-configure-claude`.

- **duke** (default): everything below, unchanged.
- **subscription**: Claude Code signed in with the student's Claude Pro, Max
  or higher plan. The settings merge removes the gateway lock (no
  `apiKeyHelper`, no `ANTHROPIC_BASE_URL`, no gateway model variables, no
  `CLAUDE_CODE_EFFORT_LEVEL`, no `IS_DEMO`, and no `ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN` or `CLAUDE_CODE_USE_*` entries at all) and sets
  `ANTHROPIC_MODEL` to `[subscription] model` (`sonnet`, the current Sonnet,
  at Claude Code's default effort; the student may use `/model`). The deny
  rules, the guard hook, the tool restrictions and Protocol SIFT's entries
  stay. Claude Code's first-run screens are skipped with
  `hasCompletedOnboarding` in `~/.claude.json` instead of `IS_DEMO` (which
  hides the account); `start` re-seeds it, because `/logout` resets it.
  `[claude] extra_env` (gateway tuning) is not applied on this route.

Switching writes the settings file in one atomic write and then the route
file; if the route file cannot be written, the previous route's settings are
put back. Either way the file holds one whole route, never half of each.
Nothing is deleted: the stored key and the sign-in stay (sign out with
`claude auth logout`). `dfir-lab use subscription` then runs Claude Code's own
`claude auth login --claudeai` in the terminal (with `BROWSER` set to the VM's
Chromium when a desktop is present) and confirms the result with
`claude auth status --json`. The toolkit never opens the sign-in file except
to mask its token values in output (`redact.py`); anything shaped like
`sk-ant-...` is masked too.

The readiness check keeps its eleven numbers on both routes. On the
subscription route: 4 is "Sign-in" (`claude auth status`: signed in with a
subscription, not expired), 5 connects to `[subscription] hosts`
(api.anthropic.com, platform.claude.com, claude.ai) directly, 6-9 are `NA`
(not applicable; they talk to the gateway with the key), and 10 is the same
real Claude Code task. READY means every applicable check passed; the
readiness code is `DFIR-<version>-RS-...` (or `NS`), with the route in its
hash. Duke-route codes are unchanged.

## What `start` sets up for Claude Code

`dfir-lab start` (and `dfir-lab-configure-claude`, which the installer runs)
merges the course settings into `~/.claude/settings.json` (or
`$CLAUDE_CONFIG_DIR/settings.json`). If that fails, `start` and readiness
check 10 stop with exit code 3 and do not run Claude Code. The merge:

- sets `apiKeyHelper`, the gateway URL and models, the effort level from
  `[claude] effort` (`CLAUDE_CODE_EFFORT_LEVEL`), `IS_DEMO=1` (skips the
  first-run theme screen),
  `CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL=1` (no clone of the
  official plugin marketplace on the first interactive start), and the deny
  rules and guard hook;
- sets `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and the
  `CLAUDE_CODE_USE_BEDROCK/VERTEX/FOUNDRY/ANTHROPIC_AWS/MANTLE` selectors to
  empty values, so Claude Code can only use the stored key; `start` also
  removes them from Claude Code's environment, and a `lab.conf` whose
  `extra_env` sets one of them is refused;
- replaces any copy of the guard hook entry with another matcher or failure
  policy;
- leaves out features the lab never uses, because the Duke gateway counts
  every token of every request (cached or not) against each key's
  tokens-per-minute limit: the `Workflow`, `ScheduleWakeup`, `ReportFindings`
  and `ListAgents` tools and the sub-agent tool `Agent` (each sub-agent sends
  its own full-size requests) are denied (a denied tool is not sent at all), and
  `CLAUDE_CODE_DISABLE_WORKFLOWS`, `_CRON`, `_BUNDLED_SKILLS`, `_ADVISOR_TOOL`,
  `_ARTIFACT` and `_TERMINAL_TITLE` are set to `1`.

`start` also marks the case folder as trusted in Claude Code's own state file
(`~/.claude.json`, or `$CLAUDE_CONFIG_DIR/.claude.json`), so the first
interactive start goes straight to the prompt. If it cannot, the banner tells
the student to choose "Yes, I trust this folder". Claude Code older than
2.1.295 is refused (the guard's `onFailure: "block"` needs it).

## The disk image (`mount`, `unmount`, `mount --status`)

The case's disk image (E01 segments under `/cases/<case>/disk/`, settings in
`[disk]` of `lab.conf`) is mounted read-only and noexec by
`libexec/disk-mount`, a root helper run through `sudo` (passwordless on the
SIFT VM): `ewfmount` -> a read-only loop device over the Windows partition
(found as the NTFS partition holding `Windows/System32/config/SYSTEM`) ->
`ntfs-3g -o ro,noexec,nodev,nosuid,show_sys_files,streams_interface=windows`
at `/mnt/dc01`. It is adapted from the tested `casekit/disk/mount-case.sh`.

- `dfir-lab mount` is idempotent ("already mounted"), refuses to put a second
  file system on a busy mount point, and confirms the result from the mount
  table (`/proc/self/mountinfo`: `ro`, `noexec`, `nodev`, `nosuid`; the loop
  device's read-only flag in `/sys/block`) and by reading the probe file. It
  never tests safety by writing to or running anything from the image, which
  holds live malware. Messages never show the partition offset.
- `dfir-lab mount --status` reads the same tables and needs no `sudo`; exit 0
  when mounted, 1 otherwise. Reading the mounted files needs no `sudo` either.
- `dfir-lab unmount` undoes every layer; it says what to close when the mount
  point is in use. Nothing is mounted after a reboot; `dfir-lab start` mounts
  the image before it starts Claude Code (if that fails, it asks whether to
  start anyway; without a terminal it starts anyway).
- Readiness check 3 also verifies the image: every file in `[disk] segments`
  present with its SHA-256 (cached in `~/dfir-lab/disk-verified.json` by
  inode, size, mtime, ctime, owner and mode), mounted, probe file readable.

## Run the tests

On Linux (or WSL), from the repository root:

```sh
python3 -m unittest discover lab/tests
```

Temporary files go to `/tmp`. Run from a Linux filesystem (not a mounted
Windows drive) so file modes are real. The end-to-end tests with real Claude
Code (`test_e2e_claude.py`) are skipped unless `claude` is on `PATH` or
`DFIR_LAB_E2E_CLAUDE=/path/to/claude` is set. They use temporary `HOME` and
`CLAUDE_CONFIG_DIR` directories and talk only to a mock on 127.0.0.1. One of
them runs `dfir-lab start` in a pseudo-terminal with a fresh configuration
and checks that Claude Code reaches its prompt without the theme or
folder-trust screens. Three more cover the subscription route with no
account (nothing signs in): check 4 and check 10 report "not signed in",
interactive Claude Code shows no first-run screen and answers "Not logged
in", and `dfir-lab start` reaches Claude Code's own sign-in step and hands the
sign-in page to a stand-in Chromium. `test_subscription.py` covers the rest of
the route with the fake Claude Code from `helpers.py`.

## Start the mock gateway

```sh
cd tools/mock-gateway
export MOCK_GATEWAY_KEY=sk-test-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')
python3 mock_gateway.py --port 8765 --log /tmp/mock.log            # Ctrl-C to stop
```

The key is read from `MOCK_GATEWAY_KEY` (or `--key-file`), never from the
command line. Choose a fault at start (`--fault rate-limit`) or while it runs:

```sh
curl -X POST -d '{"mode": "wrong-key"}' http://127.0.0.1:8765/_mock/fault
curl -X POST http://127.0.0.1:8765/_mock/reset
```

Faults: `wrong-key`, `forbidden-model`, `unknown-model-404`, `unknown-model-400`,
`rate-limit`, `budget`, `server-error`, `slow-first-byte`, `buffered-stream`,
`dropped-stream`, `cached-replay`, `no-anthropic-route`, `unsupported-param`,
`provider-reject`, `no-healthy-deployments`, `cooldown`, `upstream-auth`.
Scenarios for real Claude Code are in `tools/mock-gateway/scenarios/`
(`readiness.json` satisfies check 10). The request log never contains the
configured key.

The wrong-key, forbidden/unknown-model, rate-limit, provider-reject and
cached-replay replies copy what the real Duke gateway returned on 2026-11-14
(LiteLLM 1.108.7). The captured bodies, without the key, are in
`lab/tests/fixtures/gateway/` and are tested in `RealGatewayFixtureTests`
(`lab/tests/test_check.py`). The other faults, `budget` included, follow
LiteLLM's source and have not been seen from the real gateway. To support a
newly captured response, add it to that folder and test, and adjust
`classify()` in `lab/lib/dfir_lab/gateway.py`.

## Try each command locally

Use a throwaway home so nothing touches your real files:

```sh
W=/tmp/dfir-try; mkdir -p $W/home
cp -r lab/tests/fixtures/sample-case $W/case && chmod -R a-w $W/case/evidence
cat > $W/lab.conf <<EOF
[gateway]
base_url = http://127.0.0.1:8765
model_main = mock-main
model_small = mock-small
[case]
id = sample-case
dir = $W/case
EOF
export HOME=$W/home DFIR_LAB_CONF=$W/lab.conf CLAUDE_CONFIG_DIR=$W/claude
REPO=$PWD; D() { python3 "$REPO/lab/bin/dfir-lab" "$@"; }

echo "$MOCK_GATEWAY_KEY" | D setup-key --from-stdin   # store and test the key
D setup-key --status
python3 lab/bin/dfir-lab-configure-claude             # writes $CLAUDE_CONFIG_DIR/settings.json
D check                                              # all 11 checks (10 needs Claude Code)
D check --quick ; D check --only 7,8 --json
D checkpoint 1 203.0.113.77 ; D hint 2 ; D progress
D replay demo
D report-problem
D start                                              # starts Claude Code in $W/case
D use --status ; D use subscription ; D use duke     # switch the route (sign-in needs a terminal)
```

## Install into a VM

Students import the SIFT image themselves; the course setup script runs the
installer:

```sh
sudo sh lab/install.sh --user sansforensics --conf /path/to/lab.conf
sudo sh lab/install.sh --user sansforensics --uninstall [--purge]
```

It installs the commands, five desktop shortcuts (set up the key, start, check,
offline replay, use my Claude subscription), and the Claude Code settings for the
student's route (the Duke route unless `~/dfir-lab/route.json` says otherwise).

`--root DIR` stages everything under `DIR` (used by the tests).

## Build checkpoints

```sh
python3 lab/bin/dfir-lab-make-checkpoints case/checkpoints.source.json -o case/checkpoints.json
```

The source holds clear-text answers. Never ship it in the student image.
