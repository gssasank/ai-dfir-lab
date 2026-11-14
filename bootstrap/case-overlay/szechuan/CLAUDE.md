# Case szechuan: working rules

These rules apply in this case folder. Where they differ from the global
instructions in `~/.claude/CLAUDE.md` (Protocol SIFT), these rules win.

## Files

- Evidence is under `evidence/`, with pre-processed copies under `derived/`.
  Both are read-only. Never modify, move, rename or delete anything there.
- Write only under `analysis/`, `reports/` and `exports/` in this folder.
- Do not create files anywhere else in this folder.

## Citations

- Cite every claim with the record it rests on, as `HOST/Channel/EventRecordID`
  (for example `DC01/Security/1005`).
- A statement without such a citation is a hypothesis. Label it as one.

## Tools on this VM

- EvtxECmd: the `evtxecmd` and `EvtxECmd` wrappers fail silently on a path that
  contains a space (they print the help text, exit 0 and write nothing). Use
  paths without spaces, or run
  `dotnet /opt/zimmermantools/EvtxeCmd/EvtxECmd.dll` directly with quoted
  arguments.
- Volatility 3 is the `vol` command (`/usr/local/bin/vol`). There is no
  `vol.py` and no `/opt/volatility3-2.20.0` on this VM.
- .NET is version 9 (SDK 9.0 and runtime 9.0, `/usr/bin/dotnet`), not 6.
