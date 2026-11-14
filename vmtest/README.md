# vmtest: test the setup script against a fresh SIFT VM

Host-side PowerShell 7 scripts that drive VMware Workstation through `vmrun`. Guest commands run
through VMware guest operations (open-vm-tools is in the OVA) with the public SANS default login
`sansforensics` / `forensics`. No SSH, no guest changes needed.

| Script | What it does |
|---|---|
| `Import-SiftOva.ps1` | Imports `build\base\sift-2026-04-22.ova` with ovftool into `E:\dfir-lab-vms\<Name>\` and takes the `pristine` snapshot before first power-on. Refuses if the folder exists. |
| `New-FreshVm.ps1 -Name run01` | Linked clone of the base VM's `pristine` snapshot into `E:\dfir-lab-vms\runs\run01\`, boots it headless, waits until a guest operation works. `-Full` for an independent full clone, `-NoStart` to only clone. |
| `Invoke-Guest.ps1 -Vm run01 -Command '...'` / `-ScriptPath x.sh -Arguments a,b` | Runs bash in the guest; prints combined stdout+stderr; guest exit code becomes `$LASTEXITCODE` (124 = host-side timeout). `-Sudo` runs as root, `-DesktopEnv` adds the GNOME session variables (gsettings, gio), `-LoginShell` uses `bash -l`. `-OutFile` saves the output. |
| `Copy-ToGuest.ps1` / `Copy-FromGuest.ps1` | Copy one file in or out. |
| `Remove-TestVm.ps1 -Vm run01` | Powers off and deletes a test VM. Refuses anything that is not a direct child folder of `E:\dfir-lab-vms\runs\`. |
| `Invoke-Inventory.ps1 -Vm run01 -OutDir out\run01` | Runs `guest\inventory.sh` section by section and saves raw output. |
| `VmTest.Common.ps1` | Shared paths, credentials and helpers (dot-sourced). |
| `Test-Bootstrap.ps1` | End-to-end test of `dist/bootstrap.sh` on fresh clones named `<RunName>-a` / `-b` (`RunName` must start with `bootstrap-`). Serves the script over HTTP from the host's VMnet8 address (192.168.172.1, no admin rights needed), runs the real student one-liner in the guest, then the mock-gateway readiness check, a pseudo-terminal first start, Protocol SIFT and guard-hook checks, an idempotency comparison, the Protocol SIFT reinstall repair, a no-kit clone and uninstall. Logs to `build\vmtest\<RunName>\`; clones are deleted unless `-KeepVm`. `-Gateway real -KeyFile <path>` uses the real gateway (not run yet). |
| `guest/start-bootstrap.sh` | Starts a one-line command as the student in the GNOME session's environment (transient `systemd-run --user` unit, interactive bash that read `~/.bashrc`) and records screen output, exit code, bytes received and disk use. |
| `guest/installed-listing.sh` | Listing with owner, mode and SHA-256 of everything the setup installs, plus the desktop settings it changes. |
| `guest/first-start-pty.py` | First interactive `dfir-lab start` in a pseudo-terminal: no theme, trust, login or key screen; prompt answered. |

`-Vm` accepts a run name (resolved to `E:\dfir-lab-vms\runs\<name>\<name>.vmx`) or a full `.vmx` path.

## Typical setup-script test

The whole test of the setup script is one command:

```powershell
./Test-Bootstrap.ps1                       # builds, tests on two fresh clones, cleans up
./Test-Bootstrap.ps1 -RunName bootstrap-x -KeepVm   # keep the clones for poking around
```

By hand, with the building blocks:

```powershell
./New-FreshVm.ps1 -Name run01
./Copy-ToGuest.ps1 -Vm run01 -Source ..\dist\bootstrap.sh -Destination /tmp/bootstrap.sh
./Invoke-Guest.ps1 -Vm run01 -Command 'tail -n 3 ~/dfir-lab/logs/bootstrap-*.log' -TimeoutSec 60
./Copy-FromGuest.ps1 -Vm run01 -Source /home/sansforensics/some.log -Destination out\run01\some.log
./Remove-TestVm.ps1 -Vm run01
```

(Do not run `bootstrap.sh` itself through a blocking `Invoke-Guest.ps1` call: see the
`vmtoolsd` restart note below.)

## Things to know

- **Do not trust `vmrun checkToolsState` / `getGuestIPAddress` for readiness.** On this host they
  reported "installed" / "Tools not running" for 5 to 10 minutes after boot, while `vmware.log`
  showed Tools running after about 15 s and guest operations already worked. `Wait-GuestReady`
  therefore probes with `directoryExistsInGuest`; a fresh clone is ready in about 12 s.
  The IP it reports may be empty for the same reason; get it in the guest with `ip -br addr`.
- Guest commands start with the open-vm-tools service environment (`LANG=C.UTF-8`, PATH without
  `~/.local/bin`, no D-Bus session). That is not what a student's GNOME Terminal has. Use
  `-LoginShell` and/or `-DesktopEnv` to get closer. Interactive bash in the OVA has
  `set -o noclobber` (from `~/.bashrc`); `bash -l` and non-interactive scripts do not.
- Output is collected after the command finishes; there is no streaming.
- **Installing `open-vm-tools-desktop` upgrades `open-vm-tools` and restarts `vmtoolsd`.** A guest
  operation running at that moment never returns (vmrun waits until `-TimeoutSec`). Run anything
  that installs it detached from vmtoolsd, as `guest/start-bootstrap.sh` does, and poll with a
  short `-TimeoutSec`.
- The systemd user manager of the autologin session holds the full desktop environment
  (`DISPLAY`, `WAYLAND_DISPLAY`, `DBUS_SESSION_BUS_ADDRESS`), so `systemd-run --user` gives a
  command the same environment a GNOME Terminal has.
- Linked clones depend on the base VM's `pristine` snapshot. Never delete that snapshot or
  `E:\dfir-lab-vms\sift-base\`. `vmrun deleteVM` leaves a stale `snapshot0.cloneN` line in the
  base's `.vmsd`; it is harmless.
- `Remove-TestVm.ps1` never touches anything outside `E:\dfir-lab-vms\runs\`.
- The guest password in these scripts is the public SANS default. Put no other secret here.
