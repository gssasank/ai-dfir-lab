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
`cat ~/.config/dfir-lab/duke-api-key`). They are not a security boundary:
Claude Code runs as the same user who owns the key, and an indirect shell
command can still read it.

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
  and `ListAgents` tools are denied (a denied tool is not sent at all), and
  `CLAUDE_CODE_DISABLE_WORKFLOWS`, `_CRON`, `_BUNDLED_SKILLS`, `_ADVISOR_TOOL`,
  `_ARTIFACT` and `_TERMINAL_TITLE` are set to `1`.

`start` also marks the case folder as trusted in Claude Code's own state file
(`~/.claude.json`, or `$CLAUDE_CONFIG_DIR/.claude.json`), so the first
interactive start goes straight to the prompt. If it cannot, the banner tells
the student to choose "Yes, I trust this folder". Claude Code older than
2.1.295 is refused (the guard's `onFailure: "block"` needs it).

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
folder-trust screens.

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
```

## Install into a VM

Students import the SIFT image themselves; the course setup script runs the
installer:

```sh
sudo sh lab/install.sh --user sansforensics --conf /path/to/lab.conf
sudo sh lab/install.sh --user sansforensics --uninstall [--purge]
```

It installs the commands, four desktop shortcuts (set up the key, start, check,
offline replay), and the Claude Code settings.

`--root DIR` stages everything under `DIR` (used by the tests).

## Build checkpoints

```sh
python3 lab/bin/dfir-lab-make-checkpoints case/checkpoints.source.json -o case/checkpoints.json
```

The source holds clear-text answers. Never ship it in the student image.
