#requires -Version 7
<#
.SYNOPSIS
  End-to-end test of dist/bootstrap.sh on fresh SIFT VMs.

.DESCRIPTION
  1. Builds dist/bootstrap.sh and the stand-in case kit (unless -NoBuild), makes a
     fresh linked clone, puts the stand-in kit in ~/Downloads, serves bootstrap.sh
     over HTTP from this host on the VM's NAT network and runs the real one-line
     command (download, hash check, run) in the guest, as the student, in the
     desktop session's environment, in an interactive bash that read ~/.bashrc.
  2. Asserts exit code 0 and SETUP COMPLETE; collects the log.
  3. (mock) Starts the mock gateway in the guest, points lab.conf at it, stores the
     mock key with `dfir-lab setup-key --from-stdin`, runs `dfir-lab check --json`
     and asserts all eleven checks PASS (check 10 runs the installed Claude Code).
  4. First interactive start in a pseudo-terminal: `dfir-lab start` must reach the
     Claude Code prompt with no theme, trust, login or key-approval screen, with
     Protocol SIFT's settings present (its Stop hook must run).
  5. Protocol SIFT files installed (compared with a fresh clone at the pinned
     commit), merged settings contain its entries and ours, and the guard hook
     blocks a direct read of the key file through real Claude Code.
  6. Second run changes nothing (listing with hashes before and after). Runs right
     after step 2, before steps 3-5 change the settings on purpose.
  7. Protocol SIFT reinstalled over our settings; `dfir-lab start` repairs them.
  8. Second clone without a kit: INCOMPLETE, non-zero, tells the student to
     download the kit; after adding the kit a rerun completes. Then --uninstall.
  9. Timing, megabytes received by the guest, disk use.
  10. Logs to build\vmtest\<RunName>\; clones deleted in all cases unless -KeepVm.

  In mock mode the guest maps the real gateway's host name to 127.0.0.1, so the
  guest never contacts it.

  -Gateway real -KeyFile <path> (use -RunName gw-<x>): no mock; uses the real gateway settings and a key
  read from a file on this host (copied into the guest as a file, stored with
  setup-key --from-stdin, then deleted). The key never appears in console output
  or collected files: every guest text is redacted and the collected files are
  scanned for it.

.EXAMPLE
  ./Test-Bootstrap.ps1
  ./Test-Bootstrap.ps1 -RunName bootstrap-try2 -KeepVm
  ./Test-Bootstrap.ps1 -Gateway real -KeyFile C:\secure\duke-key.txt
#>
param(
    # bootstrap-<x> for mock runs, gw-<x> for runs against the real gateway.
    [ValidatePattern('^(bootstrap|gw)-[A-Za-z0-9._-]{1,40}$')]
    [string]$RunName = ('bootstrap-' + (Get-Date -Format 'MMdd-HHmmss')),
    [ValidateSet('mock', 'real')][string]$Gateway = 'mock',
    [string]$KeyFile,
    [string]$KitPath,          # default: build\bootstrap\szechuan-kit-standin.zip
    [switch]$KeepVm,
    [switch]$NoBuild,
    [switch]$SkipNegative,
    [string]$HostAddress = '192.168.172.1',
    [int]$Port = 8471,
    [int]$BootstrapTimeoutSec = 2700
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'VmTest.Common.ps1')

$Repo = Split-Path $PSScriptRoot
$OutDir = Join-Path $Repo "build\vmtest\$RunName"
$GuestDir = '/tmp/dfir-test'
$CaseId = 'szechuan'
# Resolve a relative -KitPath / -KeyFile against PowerShell's current location.
# ([IO.Path]::GetFullPath uses the process directory, which differs: the first
# real-gateway run failed on that.)
$KitHost = if ($KitPath) { $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($KitPath) } else { Join-Path $Repo 'build\bootstrap\szechuan-kit-standin.zip' }
if ($KeyFile) { $KeyFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($KeyFile) }
$KitName = [IO.Path]::GetFileName($KitHost)
$VmA = "$RunName-a"
$VmB = "$RunName-b"
$Created = [System.Collections.Generic.List[string]]::new()
$Results = [System.Collections.Generic.List[object]]::new()
$Secret = $null
$Server = $null
$Metrics = [ordered]@{}

if ($Gateway -eq 'real') {
    if (-not $KeyFile -or -not (Test-Path -LiteralPath $KeyFile)) { throw "-Gateway real needs -KeyFile <existing file>" }
    $Secret = (Get-Content -LiteralPath $KeyFile -Raw).Trim()
    if ($Secret.Length -lt 8) { throw "The key file looks empty" }
}
if (Test-Path -LiteralPath $OutDir) { throw "Output folder already exists: $OutDir (choose another -RunName)" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Transcript = Join-Path $OutDir 'console.txt'
$TmpDir = Join-Path ([IO.Path]::GetTempPath()) "dfir-bootstrap-test-$([guid]::NewGuid().ToString('N').Substring(0,8))"
New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

function Protect([string]$Text) {
    # Redacts the real key from any text before it is shown or saved.
    if ($null -eq $Text) { return '' }
    if ($Secret) { return $Text.Replace($Secret, '[REDACTED-KEY]') }
    return $Text
}

function Say([string]$Text, [string]$Color = 'Gray') {
    $t = Protect $Text
    Write-Host $t -ForegroundColor $Color
    Add-Content -LiteralPath $Transcript -Value $t
}

function G {
    # Runs bash in a guest as sansforensics. Returns @{Rc; Text}. Never throws.
    param([string]$Vm, [string]$Cmd, [switch]$Sudo, [int]$Timeout = 900, [string]$Save)
    $args2 = @{ Vm = $Vm; Command = $Cmd; TimeoutSec = $Timeout }
    if ($Sudo) { $args2.Sudo = $true }
    try {
        $text = & (Join-Path $PSScriptRoot 'Invoke-Guest.ps1') @args2 2>&1 | Out-String
        $rc = $LASTEXITCODE
    } catch {
        # e.g. vmtoolsd restarting while the setup upgrades open-vm-tools
        $text = "guest operation failed: $($_.Exception.Message)"; $rc = 125
    }
    $text = Protect $text
    if ($Save) {
        $p = Join-Path $OutDir $Save
        New-Item -ItemType Directory -Force -Path (Split-Path $p) | Out-Null
        [IO.File]::WriteAllText($p, $text)
    }
    [pscustomobject]@{ Rc = $rc; Text = $text.TrimEnd() }
}

function ToGuest([string]$Vm, [string]$Source, [string]$Dest, [switch]$Lf) {
    $src = $Source
    if ($Lf) {
        $src = Join-Path $TmpDir ([IO.Path]::GetFileName($Source))
        [IO.File]::WriteAllText($src, ((Get-Content -LiteralPath $Source -Raw) -replace "`r`n", "`n"))
    }
    & (Join-Path $PSScriptRoot 'Copy-ToGuest.ps1') -Vm $Vm -Source $src -Destination $Dest
}

function FromGuest([string]$Vm, [string]$Source, [string]$Dest) {
    try { & (Join-Path $PSScriptRoot 'Copy-FromGuest.ps1') -Vm $Vm -Source $Source -Destination $Dest; return $true }
    catch { Say "  (could not copy $Source from $Vm`: $_)" 'DarkYellow'; return $false }
}

function Check([bool]$Cond, [string]$What) {
    if ($Cond) { Say "    PASS  $What" 'Green' } else { Say "    FAIL  $What" 'Red'; $script:StepFailures++ }
}

function Invoke-Step([string]$Id, [string]$Title, [scriptblock]$Body, [switch]$Critical) {
    Say ''
    Say "=== Step $Id`: $Title ===" 'Cyan'
    $script:StepFailures = 0
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $err = $null
    try { & $Body } catch { $err = $_; Say "    ERROR $($_.Exception.Message)" 'Red'; $script:StepFailures++ }
    $status = if ($script:StepFailures -eq 0) { 'PASS' } else { 'FAIL' }
    $Results.Add([pscustomobject]@{ Step = $Id; Title = $Title; Status = $status
                                    Seconds = [int]$sw.Elapsed.TotalSeconds
                                    Error = $(if ($err) { Protect $err.Exception.Message } else { '' }) })
    Say "=== Step $Id`: $status ($([int]$sw.Elapsed.TotalSeconds) s) ===" $(if ($status -eq 'PASS') { 'Green' } else { 'Red' })
    if ($Critical -and $status -ne 'PASS') { throw "critical step $Id failed" }
}

function Start-FileServer([string]$Address, [int]$Port, [hashtable]$Files) {
    # Minimal HTTP/1.1 file server on the VMnet8 address (no admin rights, no URL ACL).
    $blobs = @{}
    foreach ($k in $Files.Keys) { $blobs[$k] = [IO.File]::ReadAllBytes($Files[$k]) }
    $state = [hashtable]::Synchronized(@{ Log = [System.Collections.ArrayList]::new(); Stop = $false })
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Parse($Address), $Port)
    $listener.Start()
    $job = Start-ThreadJob -ArgumentList $listener, $blobs, $state -ScriptBlock {
        param($l, $blobs, $state)
        while (-not $state.Stop) {
            try { $c = $l.AcceptTcpClient() } catch { break }
            try {
                $s = $c.GetStream(); $s.ReadTimeout = 10000
                $reader = [IO.StreamReader]::new($s, [Text.Encoding]::ASCII, $false, 4096, $true)
                $req = $reader.ReadLine()
                while ($true) { $h = $reader.ReadLine(); if ($null -eq $h -or $h -eq '') { break } }
                $path = ($req -split ' ')[1]
                if ($blobs.ContainsKey($path)) { $body = $blobs[$path]; $status = '200 OK' }
                else { $body = [Text.Encoding]::ASCII.GetBytes("not found`n"); $status = '404 Not Found' }
                $hdr = [Text.Encoding]::ASCII.GetBytes("HTTP/1.1 $status`r`nContent-Type: text/plain`r`nContent-Length: $($body.Length)`r`nConnection: close`r`n`r`n")
                $s.Write($hdr, 0, $hdr.Length); $s.Write($body, 0, $body.Length); $s.Flush()
                [void]$state.Log.Add("$(Get-Date -Format o) $($c.Client.RemoteEndPoint) $req -> $status, $($body.Length) bytes")
            } catch { [void]$state.Log.Add("$(Get-Date -Format o) error: $($_.Exception.Message)") }
            finally { $c.Close() }
        }
    }
    [pscustomobject]@{ Listener = $listener; Job = $job; State = $state }
}

function Stop-FileServer($Srv) {
    if (-not $Srv) { return }
    $Srv.State.Stop = $true
    try { $Srv.Listener.Stop() } catch { }
    Wait-Job $Srv.Job -Timeout 5 | Out-Null
    Remove-Job $Srv.Job -Force -ErrorAction SilentlyContinue
    [IO.File]::WriteAllLines((Join-Path $OutDir 'http-server.log'), [string[]]$Srv.State.Log)
}

function Read-KV([string]$Text) {
    $h = @{}
    foreach ($line in ($Text -split "`n")) { if ($line -match '^(\w+)=(.*)$') { $h[$Matches[1]] = $Matches[2].Trim() } }
    $h
}

function New-Clone([string]$Name) {
    Say "  creating linked clone $Name"
    $r = & (Join-Path $PSScriptRoot 'New-FreshVm.ps1') -Name $Name
    $Created.Add($Name)
    Say "  $Name ready after $($r.ReadySeconds) s"
    $r2 = G $Name "mkdir -p $GuestDir && chmod 755 $GuestDir && echo ok"
    if ($r2.Rc -ne 0) { throw "guest not usable: $($r2.Text)" }
    foreach ($f in 'start-bootstrap.sh', 'installed-listing.sh', 'first-start-pty.py') {
        ToGuest $Name (Join-Path $PSScriptRoot "guest\$f") "$GuestDir/$f" -Lf
    }
    if ($Gateway -eq 'mock') {
        # The guest must never contact the real gateway during this test.
        $r3 = G $Name "printf '127.0.0.1 litellm.oit.duke.edu\n::1 litellm.oit.duke.edu\n' >> /etc/hosts && getent hosts litellm.oit.duke.edu" -Sudo
        Say "  guest /etc/hosts: $($r3.Text -replace "`n", '; ')"
    }
}

function Invoke-OneLiner([string]$Vm, [string]$Tag) {
    # Runs the student command in the guest and waits for it. Returns a result object.
    $gout = "$GuestDir/$Tag"
    $local = Join-Path $TmpDir "command-$Tag.txt"
    [IO.File]::WriteAllText($local, $script:StudentCommand + "`n")
    $null = G $Vm "mkdir -p $gout"
    ToGuest $Vm $local "$gout/command.txt"
    $st = G $Vm "bash $GuestDir/start-bootstrap.sh dfir-$Tag $gout"
    Say "  $($st.Text)"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $rc = $null
    while ($sw.Elapsed.TotalSeconds -lt $BootstrapTimeoutSec) {
        Start-Sleep -Seconds 10
        $p = G $Vm "cat $gout/rc 2>/dev/null; tail -n 1 $gout/screen.txt 2>/dev/null" -Timeout 120
        $lines = $p.Text -split "`n"
        if ($lines[0] -match '^\d+$') { $rc = [int]$lines[0]; break }
        if ([int]$sw.Elapsed.TotalSeconds % 60 -lt 10) { Say "    ... $([int]$sw.Elapsed.TotalSeconds) s: $($lines[-1])" 'DarkGray' }
    }
    if ($null -eq $rc) { throw "the setup did not finish within $BootstrapTimeoutSec s" }
    $dir = Join-Path $OutDir "$Vm\$Tag"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    foreach ($f in 'screen.txt', 'before.txt', 'after.txt', 'command.txt') { $null = FromGuest $Vm "$gout/$f" (Join-Path $dir $f) }
    $screen = Protect (Get-Content -LiteralPath (Join-Path $dir 'screen.txt') -Raw -ErrorAction SilentlyContinue)
    [IO.File]::WriteAllText((Join-Path $dir 'screen.txt'), $screen)
    $b = Read-KV (Get-Content -LiteralPath (Join-Path $dir 'before.txt') -Raw)
    $a = Read-KV (Get-Content -LiteralPath (Join-Path $dir 'after.txt') -Raw)
    $res = [pscustomobject]@{
        Rc = $rc; Screen = $screen; Dir = $dir
        Seconds = [int64]$a.epoch - [int64]$b.epoch
        RxMB = [math]::Round(([int64]$a.rx - [int64]$b.rx) / 1MB, 1)
        DiskMB = [math]::Round(([int64]$a.df_used - [int64]$b.df_used) / 1MB, 1)
    }
    Say "  exit code $rc after $($res.Seconds) s; guest received $($res.RxMB) MB; disk use +$($res.DiskMB) MB"
    Say "  ---- what the student sees (screen.txt) ----" 'DarkCyan'
    foreach ($l in ($screen -split "`n")) {
        if ($l -match 'cannot set terminal process group|no job control in this shell') { continue }
        Say "  | $l" 'White'
    }
    Say "  ---- end of screen ----" 'DarkCyan'
    $res
}

function Collect([string]$Vm) {
    $tgz = "/tmp/dfir-test-collect-$Vm.tgz"
    $null = G $Vm ("cd / && tar -czf $tgz --ignore-failed-read --exclude=tmp/dfir-test/mock/key " +
                   "--exclude=tmp/dfir-test/psift-ref --exclude=tmp/dfir-test/k " +
                   "home/sansforensics/dfir-lab/logs home/sansforensics/dfir-lab/readiness tmp/dfir-test " +
                   "2>/dev/null; ls -l $tgz") -Sudo
    $null = G $Vm "chmod 644 $tgz" -Sudo
    $dest = Join-Path $OutDir "$Vm\collected.tgz"
    if (FromGuest $Vm $tgz $dest) {
        $x = Join-Path $OutDir "$Vm\collected"
        New-Item -ItemType Directory -Force -Path $x | Out-Null
        tar -xzf $dest -C $x 2>$null
        Remove-Item -LiteralPath $dest -Force
    }
}

function Jq-Settings([string]$Vm, [string]$BaseUrl, [string]$Save) {
    # Prints name=true/false for each entry the merged settings must contain.
    $q = @'
f=$HOME/.claude/settings.json
jq -r --arg url "@URL@" '
  "psift_default_mode=\(.permissions.defaultMode == "acceptEdits")",
  "psift_stop_hook=\([.hooks.Stop[]?.hooks[]?.command] | any(contains("forensic_audit.log")))",
  "psift_allow_bash_star=\((.permissions.allow // []) | index("Bash(*)") != null)",
  "psift_deny_curl=\((.permissions.deny // []) | index("Bash(curl:*)") != null)",
  "ours_api_key_helper=\((.apiKeyHelper // "") | endswith("key-helper"))",
  "ours_guard_hook=\([.hooks.PreToolUse[]?.hooks[]?.command] | any(contains("pretool-guard")))",
  "ours_deny_key_dir=\((.permissions.deny // []) | index("Read(~/.config/dfir-lab/**)") != null)",
  "ours_deny_websearch=\((.permissions.deny // []) | index("WebSearch") != null)",
  "ours_base_url=\(.env.ANTHROPIC_BASE_URL == $url)",
  "ours_marketplace_off=\(.env.CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL == "1")",
  "ours_is_demo=\(.env.IS_DEMO == "1")",
  "ours_effort_low=\(.env.CLAUDE_CODE_EFFORT_LEVEL == "low")",
  "ours_model_haiku=\(.env.ANTHROPIC_MODEL == "claude-haiku-5-5" or .env.ANTHROPIC_MODEL == "mock-main")",
  "ours_deny_unused_tools=\((.permissions.deny // []) | index("Workflow") != null and index("ScheduleWakeup") != null)"' "$f"
echo "psift_settings_local=$([ -f $HOME/.claude/settings.local.json ] && echo true || echo false)"
'@ -replace '@URL@', $BaseUrl
    G $Vm $q -Save $Save
}

# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

$overall = 'FAIL'
try {
    Say "Test-Bootstrap $RunName (gateway: $Gateway) - output in $OutDir" 'Cyan'

    Invoke-Step '0' 'Build dist/bootstrap.sh and the stand-in kit' -Critical {
        if (-not $NoBuild) {
            $wslRepo = '/mnt/' + $Repo.Substring(0, 1).ToLower() + ($Repo.Substring(2) -replace '\\', '/')
            $b = wsl.exe -d Ubuntu -- bash -c "cd '$wslRepo' && python3 bootstrap/build.py standin-kit && python3 bootstrap/build.py --check" 2>&1 | Out-String
            Say ($b.TrimEnd() -replace '(?m)^', '  ')
            Check ($LASTEXITCODE -eq 0) 'build.py standin-kit and build.py --check succeeded'
            Check ($b -match 'Deterministic: yes') 'two builds give the same SHA-256'
        }
        $script:Sha = ((Get-Content -LiteralPath (Join-Path $Repo 'dist\bootstrap.sh.sha256') -Raw) -split '\s+')[0]
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Repo 'dist\bootstrap.sh')).Hash.ToLower()
        Check ($actual -eq $script:Sha) "dist/bootstrap.sh matches dist/bootstrap.sh.sha256 ($actual)"
        Check (Test-Path -LiteralPath $KitHost) "case kit exists ($KitHost)"
        $script:Server = Start-FileServer $HostAddress $Port @{ '/dist/bootstrap.sh' = (Join-Path $Repo 'dist\bootstrap.sh') }
        $script:StudentCommand = "curl -fsSLO http://$HostAddress`:$Port/dist/bootstrap.sh && echo `"$($script:Sha)  bootstrap.sh`" | sha256sum -c && bash bootstrap.sh"
        Say "  serving dist/bootstrap.sh on http://$HostAddress`:$Port"
        Say "  student command used: $($script:StudentCommand)"
    }

    Invoke-Step '1' 'Fresh clone, kit in ~/Downloads, run the one-line command as the student' -Critical {
        New-Clone $VmA
        ToGuest $VmA $KitHost "/home/sansforensics/Downloads/$KitName"
        $script:Run1 = Invoke-OneLiner $VmA 'run1'
    }

    Invoke-Step '2' 'Exit code 0, SETUP COMPLETE, log collected' -Critical {
        Check ($script:Run1.Rc -eq 0) "exit code is 0 (got $($script:Run1.Rc))"
        Check ($script:Run1.Screen -match 'SETUP COMPLETE') 'screen says SETUP COMPLETE'
        Check ($script:Run1.Screen -match 'bootstrap\.sh: OK') 'sha256sum -c accepted the download'
        $logs = G $VmA 'ls -1 ~/dfir-lab/logs/bootstrap-*.log'
        Check ($logs.Rc -eq 0 -and $logs.Text) "log written: $($logs.Text)"
        $script:Run1Log = ($logs.Text -split "`n")[0].Trim()
        $null = FromGuest $VmA $script:Run1Log (Join-Path $OutDir "$VmA\run1\bootstrap.log")
        # Kit and overlay changes must be detected by the toolkit's manifest check.
        $t = G $VmA @'
set -u
c=/cases/szechuan
r() { dfir-lab check --only 3 --json --no-gui | jq -r '.results[0].status + " " + .results[0].result'; }
# The first evidence file the kit's own manifest lists (works for the stand-in and the real kit).
ev=$c/$(awk '$2 ~ /^evidence\// {print $2; exit}' $c/MANIFEST.sha256)
echo "evidence file used: $ev"
echo "baseline: $(r)"
sudo cp -p "$ev" /tmp/ev.bak && printf 'x' | sudo tee -a "$ev" >/dev/null
echo "kit file changed: $(r)"
sudo cp -p /tmp/ev.bak "$ev"
sudo cp -p $c/CLAUDE.md /tmp/cl.bak && printf 'x' | sudo tee -a $c/CLAUDE.md >/dev/null
echo "overlay file changed: $(r)"
sudo cp -p /tmp/cl.bak $c/CLAUDE.md
echo "restored: $(r)"
echo "student can write evidence: $( [ -w "$ev" ] && echo yes || echo no )"
echo "student can write analysis: $( [ -w $c/analysis ] && echo yes || echo no )"
stat -c '%U:%G %a %n' $c $c/evidence "$ev" $c/CLAUDE.md $c/analysis $c/MANIFEST.lab.sha256
'@ -Save "$VmA\manifest-detection.txt"
        Say ($t.Text -replace '(?m)^', '    ')
        Check ($t.Text -match 'baseline: PASS') 'manifest check passes after install'
        Check ($t.Text -match 'kit file changed: FAIL .*changed') 'a changed kit file is detected'
        Check ($t.Text -match 'overlay file changed: FAIL .*CLAUDE\.md') 'a changed overlay file is detected'
        Check ($t.Text -match 'restored: PASS') 'manifest check passes again after restoring'
        Check ($t.Text -match 'student can write evidence: no') 'evidence is not writable by the student'
        Check ($t.Text -match 'student can write analysis: yes') 'analysis/ is writable by the student'
    }

    Invoke-Step '6' 'Second run changes nothing (run right after the first)' {
        # Wait first: in an earlier version the desktop's metadata daemon dropped the
        # shortcuts' trusted flag about a minute after the first run.
        Start-Sleep -Seconds 120
        $before = G $VmA "bash $GuestDir/installed-listing.sh $CaseId" -Save "$VmA\listing-before-run2.txt"
        $script:Run2 = Invoke-OneLiner $VmA 'run2'
        $after = G $VmA "bash $GuestDir/installed-listing.sh $CaseId" -Save "$VmA\listing-after-run2.txt"
        $lines = ($before.Text -split "`n").Count
        Check ($script:Run2.Rc -eq 0) "second run exit code 0 (got $($script:Run2.Rc))"
        Check ($script:Run2.Screen -match 'This run changed nothing') 'second run says it changed nothing'
        Check ($script:Run2.Screen -notmatch '      ok: ') 'second run reports no step as newly done'
        $diff = Compare-Object ($before.Text -split "`n") ($after.Text -split "`n")
        if ($diff) { Say (($diff | Out-String).TrimEnd() -replace '(?m)^', '    ') 'Yellow' }
        Check (-not $diff) "installed files, settings and desktop settings identical ($lines listing lines)"
    }

    if ($Gateway -eq 'mock') { $script:BaseUrl = 'http://127.0.0.1:8765' } else { $script:BaseUrl = 'https://litellm.oit.duke.edu' }

    Invoke-Step '3' "Key stored, dfir-lab check --json: all eleven checks pass ($Gateway gateway)" -Critical {
        if ($Gateway -eq 'mock') {
            ToGuest $VmA (Join-Path $Repo 'tools\mock-gateway\mock_gateway.py') "$GuestDir/mock_gateway.py" -Lf
            ToGuest $VmA (Join-Path $Repo 'tools\mock-gateway\scenarios\readiness.json') "$GuestDir/readiness.json" -Lf
            ToGuest $VmA (Join-Path $Repo 'lab\tests\fixtures\guard-probe.json') "$GuestDir/guard-probe.json" -Lf
            $m = G $VmA @'
set -u
mkdir -p /tmp/dfir-test/mock && cd /tmp/dfir-test/mock
umask 077
printf 'sk-mock-%s\n' "$(head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n')" > key
umask 022
export XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus
systemd-run --user --collect --quiet --unit=dfir-mock python3 /tmp/dfir-test/mock_gateway.py --port 8765 \
  --key-file /tmp/dfir-test/mock/key --scenario /tmp/dfir-test/readiness.json \
  --scenario /tmp/dfir-test/guard-probe.json --log /tmp/dfir-test/mock/mock.log
for i in $(seq 30); do curl -sf -o /dev/null http://127.0.0.1:8765/_mock/state && break; sleep 1; done
curl -s http://127.0.0.1:8765/_mock/state; echo
sudo sed -i -e 's#^base_url = .*#base_url = http://127.0.0.1:8765#' -e 's#^model_main = .*#model_main = mock-main#' \
  -e 's#^model_small = .*#model_small = mock-small#' /etc/dfir-lab/lab.conf
grep -E '^(base_url|model_main|model_small|binary) =' /etc/dfir-lab/lab.conf
dfir-lab-configure-claude
dfir-lab setup-key --from-stdin --no-gui < key
echo "setup-key exit=$?"
'@ -Save "$VmA\step3-setup.txt"
        } else {
            $kf = Join-Path $TmpDir 'k'
            Copy-Item -LiteralPath $KeyFile -Destination $kf
            ToGuest $VmA $kf "$GuestDir/k"
            Remove-Item -LiteralPath $kf -Force
            $m = G $VmA "chmod 600 $GuestDir/k; dfir-lab setup-key --from-stdin --no-gui < $GuestDir/k; echo setup-key exit=`$?; shred -u $GuestDir/k" -Save "$VmA\step3-setup.txt"
        }
        Say ($m.Text -replace '(?m)^', '    ')
        Check ($m.Text -match 'setup-key exit=0') 'key stored with dfir-lab setup-key --from-stdin and tested'
        $c = G $VmA "dfir-lab check --json --no-gui > $GuestDir/check-full.json; echo rc=`$?; cat $GuestDir/check-full.json" -Timeout 600 -Save "$VmA\check-full.txt"
        $json = $null
        try { $json = ($c.Text.Substring($c.Text.IndexOf('{'))) | ConvertFrom-Json } catch { }
        Check ($null -ne $json) 'check printed JSON'
        if ($json) {
            foreach ($r in $json.results) { Say ("    {0,2} {1,-17} {2,-4} {3}" -f $r.id, $r.name, $r.status, $r.result) }
            Check ($json.results.Count -eq 11) "eleven results (got $($json.results.Count))"
            Check (@($json.results | Where-Object status -ne 'PASS').Count -eq 0) 'every check is PASS'
            Check ($json.ready -eq $true) "READY, readiness code $($json.code)"
            $script:Metrics['readiness_tokens'] = $json.tokens.total
        }
    }

    Invoke-Step '4' 'First interactive start in a pseudo-terminal' {
        $pre = Jq-Settings $VmA $script:BaseUrl "$VmA\settings-before-pty.txt"
        Check ($pre.Text -match 'psift_stop_hook=true' -and $pre.Text -match 'psift_default_mode=true') "Protocol SIFT's settings are present before the start"
        $guestHasClaudeJson = G $VmA 'test -f ~/.claude.json && jq -c "{numStartups, hasCompletedOnboarding, projects: (.projects | keys)}" ~/.claude.json || echo none'
        Say "    ~/.claude.json before: $($guestHasClaudeJson.Text)"
        if ($Gateway -eq 'mock') { $prompt = 'Say hello to the lab.'; $expect = 'mock gateway' }
        else { $prompt = 'Reply with exactly the word READY-OK and nothing else.'; $expect = 'READY-OK' }
        $null = G $VmA 'rm -f /cases/szechuan/analysis/forensic_audit.log'
        $p = G $VmA "python3 $GuestDir/first-start-pty.py $GuestDir/pty '$prompt' '$expect'" -Timeout 400 -Save "$VmA\pty-result.txt"
        $null = FromGuest $VmA "$GuestDir/pty/pty-screen.txt" (Join-Path $OutDir "$VmA\pty-screen.txt")
        Say ($p.Text -replace '(?m)^', '    ')
        Check ($p.Rc -eq 0) 'dfir-lab start reached the ready prompt and answered (no theme, trust, login or key screen)'
        $hook = G $VmA 'cat /cases/szechuan/analysis/forensic_audit.log 2>/dev/null || echo MISSING'
        Check ($hook.Text -notmatch 'MISSING') "Protocol SIFT's Stop hook ran in the interactive session (analysis/forensic_audit.log: $($hook.Text -replace "`n", ' | '))"
    }

    Invoke-Step '5' 'Protocol SIFT files, merged settings, guard hook through real Claude Code' {
        $f = G $VmA @'
set -u
rm -rf /tmp/dfir-test/psift-ref
git clone --quiet https://github.com/teamdfir/protocol-sift.git /tmp/dfir-test/psift-ref
git -C /tmp/dfir-test/psift-ref -c advice.detachedHead=false checkout --quiet 40bed7a96bfd986ea048c3b2aeb9d788b2f3400c
echo "ref=$(git -C /tmp/dfir-test/psift-ref rev-parse HEAD)"
cd /tmp/dfir-test/psift-ref
for s in global/CLAUDE.md skills/*/SKILL.md; do
  d=$HOME/.claude/${s#global/}
  cmp -s "$s" "$d" && echo "same $d" || echo "DIFFERENT $d"
done
'@ -Save "$VmA\psift-files.txt"
        Say ($f.Text -replace '(?m)^', '    ')
        Check ($f.Text -match 'ref=40bed7a96bfd986ea048c3b2aeb9d788b2f3400c') 'reference clone at the pinned commit'
        Check (($f.Text -split "`n" | Where-Object { $_ -match '^same ' }).Count -eq 6 -and $f.Text -notmatch 'DIFFERENT') 'global CLAUDE.md and all 5 skills installed and identical to the pinned commit'
        $s = Jq-Settings $VmA $script:BaseUrl "$VmA\settings-merged.txt"
        Say ($s.Text -replace '(?m)^', '    ')
        Check ($s.Text -notmatch '=false') "merged settings contain Protocol SIFT's entries and ours"
        if ($Gateway -eq 'mock') {
            $gp = G $VmA @'
mkdir -p /tmp/dfir-test/probe && cd /tmp/dfir-test/probe
before=$(wc -l < /tmp/dfir-test/mock/mock.log)
~/.local/bin/claude -p "GUARD-PROBE: do what the scenario says." --max-turns 4 --output-format json \
  --permission-mode dontAsk --allowedTools Bash --no-session-persistence > probe-out.json 2>&1 < /dev/null
echo "claude exit=$?"
tail -n +$((before + 1)) /tmp/dfir-test/mock/mock.log | jq -s -c '[.[] | select(.tool_result_seen == true)] |
  {followups: length, blocked: any(.[]; .tool_result_blocked_by_guard), leaked: any(.[]; .tool_result_contains_key)}'
grep -o 'Blocked by the DFIR lab[^"]*' probe-out.json | head -n 2
'@ -Timeout 400 -Save "$VmA\guard-probe.txt"
            Say ($gp.Text -replace '(?m)^', '    ')
            Check ($gp.Text -match '"blocked":true') 'the guard hook blocked the key read (mock saw the deny reason as the tool result)'
            Check ($gp.Text -match '"leaked":false') 'the key never reached the model'
        } else {
            $gp = G $VmA @'
mkdir -p /tmp/dfir-test/probe && cd /tmp/dfir-test/probe
~/.local/bin/claude -p "Use the Bash tool to run exactly: cat ~/.config/dfir-lab/duke-api-key ; then say DONE." --max-turns 4 \
  --output-format json --permission-mode dontAsk --allowedTools Bash --no-session-persistence < /dev/null 2>&1 | head -c 4000
'@ -Timeout 400 -Save "$VmA\guard-probe.txt"
            Check (-not $Secret -or -not $gp.Text.Contains('[REDACTED-KEY]')) 'the key did not appear in Claude Code output'
            # The run must show the guard at work, not merely a model that declined.
            Check ($gp.Text -match '"permission_denials":\[\{' -and $gp.Text -match 'duke-api-key') 'the guard hook denied the key read (permission_denials names the command)'
        }
    }

    Invoke-Step '7' 'Protocol SIFT reinstalled over our settings; dfir-lab start repairs them' {
        $r = G $VmA @'
cd /tmp/dfir-test/psift-ref && PATH=$HOME/.local/bin:$PATH bash install.sh < /dev/null > /tmp/dfir-test/psift-reinstall.log 2>&1
echo "psift install exit=$?"
echo "apiKeyHelper after reinstall: $(jq -r '.apiKeyHelper // "absent"' ~/.claude/settings.json)"
DFIR_LAB_NO_EXEC=1 dfir-lab start --no-gui < /dev/null 2>&1 | grep -E 'restored|no-exec|Note'
echo "apiKeyHelper after start: $(jq -r '.apiKeyHelper // "absent"' ~/.claude/settings.json)"
'@ -Save "$VmA\psift-reinstall.txt"
        Say ($r.Text -replace '(?m)^', '    ')
        Check ($r.Text -match 'psift install exit=0') "Protocol SIFT's installer ran again"
        Check ($r.Text -match 'apiKeyHelper after reinstall: absent') 'it replaced our settings'
        Check ($r.Text -match 'have been restored') 'dfir-lab start said it restored the course settings'
        Check ($r.Text -match 'apiKeyHelper after start: .*key-helper') 'apiKeyHelper is back'
        $s = Jq-Settings $VmA $script:BaseUrl "$VmA\settings-after-repair.txt"
        Check ($s.Text -notmatch '=false') "repaired settings contain Protocol SIFT's entries and ours"
        $c10 = G $VmA "dfir-lab check --only 10 --json --no-gui | jq -r '.results[0].status + `" `" + .results[0].result'" -Timeout 400
        Say "    check 10 after repair: $($c10.Text)"
        Check ($c10.Text -match '^PASS') 'readiness check 10 (real Claude Code) passes with the repaired settings'
    }

    Invoke-Step '7b' 'Desktop evidence (screenshot, shortcut launch through GLib)' {
        $shot1 = Join-Path $OutDir "$VmA\desktop-after-setup.png"
        $r = Invoke-Vmrun -Guest captureScreen (Resolve-Vmx $VmA) $shot1
        # Informational only: a headless VM may return a blank image.
        Say "    screenshot: exit $($r.ExitCode), $shot1 ($((Get-Item -LiteralPath $shot1 -ErrorAction SilentlyContinue).Length) bytes)"
        $g = G $VmA @'
export XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus WAYLAND_DISPLAY=wayland-0 DISPLAY=:0
export XDG_DATA_DIRS=/usr/share/ubuntu:/usr/share/gnome:/usr/local/share/:/usr/share/:/var/lib/snapd/desktop
for f in ~/Desktop/dfir-lab-*.desktop; do
  printf '%s ' "$(basename "$f")"; gio info -a standard::content-type,access::can-execute,metadata::trusted,unix::mode "$f" | awk '/  (standard|access|metadata|unix)::/ {printf "%s ", $0}'; echo
done
stat -c 'Desktop folder mode %a' ~/Desktop
gsettings get org.gnome.desktop.session idle-delay; gsettings get org.gnome.desktop.screensaver lock-enabled
xdg-settings get default-web-browser; gio mime x-scheme-handler/https | head -n 1
echo "mimeapps https=$(sed -n 's/^x-scheme-handler\/https=//p' ~/.config/mimeapps.list | tr -d ';' | head -n 1)"
# Launch "Check my setup" the way DING does (GLib DesktopAppInfo), then look for its terminal.
gio launch ~/Desktop/dfir-lab-check.desktop &
sleep 20
pgrep -af 'dfir-lab check' | head -n 3
pgrep -af gnome-terminal | head -n 3
'@ -Save "$VmA\desktop-evidence.txt"
        Say ($g.Text -replace '(?m)^', '    ')
        Check (($g.Text -split "`n" | Where-Object { $_ -match 'metadata::trusted: true' }).Count -eq 4) 'all four shortcuts: trusted, x-desktop, executable'
        Check ($g.Text -match 'dfir-lab check --pause') 'launching the "Check my setup" shortcut started dfir-lab check in a terminal'
        Check ($g.Text -match 'Default application for .x-scheme-handler/https.: chromium_chromium\.desktop') 'GIO opens https links with Chromium'
        Check ($g.Text -match 'mimeapps https=chromium_chromium\.desktop') 'Chromium is the explicit default in ~/.config/mimeapps.list'
        Check ($g.Text -match 'uint32 0' -and $g.Text -match '(?m)^false$') 'idle screen blanking and lock are off'
        $shot2 = Join-Path $OutDir "$VmA\desktop-shortcut-launched.png"
        $null = Invoke-Vmrun -Guest captureScreen (Resolve-Vmx $VmA) $shot2
        $null = G $VmA "pkill -f 'dfir-lab check --pause'; true"
    }

    Invoke-Step '9' 'Timing, download size, disk use' {
        $u = G $VmA 'sudo du -sm ~/.local/share/claude ~/.claude /usr/local/lib/dfir-lab /cases/szechuan 2>/dev/null; df -h / | tail -n 1' -Save "$VmA\disk-use.txt"
        $steps = G $VmA "grep -E -- '---- step|Fetched|NEW packages|^  open-vm|newly installed' $($script:Run1Log)" -Save "$VmA\step-metrics.txt"
        $script:Metrics['run1_seconds'] = $script:Run1.Seconds
        $script:Metrics['run1_received_MB'] = $script:Run1.RxMB
        $script:Metrics['run1_disk_used_MB'] = $script:Run1.DiskMB
        if ($script:Run2) {
            $script:Metrics['run2_seconds'] = $script:Run2.Seconds
            $script:Metrics['run2_received_MB'] = $script:Run2.RxMB
        }
        Say ($steps.Text -replace '(?m)^', '    ')
        Say ($u.Text -replace '(?m)^', '    ')
        Check ($script:Run1.Seconds -gt 0) "first run: $($script:Run1.Seconds) s, $($script:Run1.RxMB) MB received, disk +$($script:Run1.DiskMB) MB"
    }

    if (-not $SkipNegative) {
        Invoke-Step '8' 'Second clone without a kit: INCOMPLETE, then complete after adding the kit' {
            New-Clone $VmB
            $n1 = Invoke-OneLiner $VmB 'nokit'
            Check ($n1.Rc -ne 0) "exit code is non-zero (got $($n1.Rc))"
            Check ($n1.Screen -match 'SETUP INCOMPLETE') 'screen says SETUP INCOMPLETE'
            Check ($n1.Screen -match 'download the case kit \(szechuan-kit-\*\.zip\)' -and $n1.Screen -match '~/Downloads') 'it tells the student to download the kit (into ~/Downloads) and run again'
            Check ($n1.Screen -match 'Claude Code\s+2\.1\.322') 'everything else was still installed'
            Check (([regex]::Matches($n1.Screen, '(?m)^   - ')).Count -eq 1) 'the kit is the only unfinished item listed'
            ToGuest $VmB $KitHost "/home/sansforensics/Downloads/$KitName"
            $n2 = Invoke-OneLiner $VmB 'withkit'
            Check ($n2.Rc -eq 0) "rerun after adding the kit: exit code 0 (got $($n2.Rc))"
            Check ($n2.Screen -match 'SETUP COMPLETE') 'rerun says SETUP COMPLETE'
            Check ($n2.Screen -match 'already done: Claude Code') 'rerun skipped what was already done'
            $script:Metrics['nokit_seconds'] = $n1.Seconds
            $script:Metrics['withkit_rerun_seconds'] = $n2.Seconds
        }

        Invoke-Step '8b' 'Uninstall (keeps Claude Code, Protocol SIFT and work), then purge' {
            if (($Results | Where-Object Step -eq '8').Status -ne 'PASS') { throw 'skipped: step 8 did not pass' }
            $u = G $VmB @'
echo notes > /cases/szechuan/analysis/my-notes.txt
bash ~/bootstrap.sh --uninstall --yes > /tmp/dfir-test/uninstall.txt 2>&1; echo "uninstall exit=$?"
cat /tmp/dfir-test/uninstall.txt
for p in /usr/local/bin/dfir-lab /etc/dfir-lab /cases/szechuan/evidence /cases/szechuan/analysis/my-notes.txt ~/.local/bin/claude ~/.claude/CLAUDE.md ~/Desktop/dfir-lab-start.desktop; do
  [ -e "$p" ] && echo "present $p" || echo "absent $p"
done
grep -c 'dfir-lab setup' ~/.bashrc
bash ~/bootstrap.sh --uninstall --purge --yes > /tmp/dfir-test/purge.txt 2>&1; echo "purge exit=$?"
for p in /cases/szechuan ~/.local/bin/claude ~/.claude ~/dfir-lab; do [ -e "$p" ] && echo "after purge present $p" || echo "after purge absent $p"; done
'@ -Save "$VmB\uninstall.txt"
            Say ($u.Text -replace '(?m)^', '    ')
            Check ($u.Text -match 'uninstall exit=0') 'uninstall exit 0'
            Check ($u.Text -match 'absent /usr/local/bin/dfir-lab' -and $u.Text -match 'absent /etc/dfir-lab' -and $u.Text -match 'absent /cases/szechuan/evidence') 'toolkit and case files removed'
            Check ($u.Text -match 'present /cases/szechuan/analysis/my-notes.txt') "student's work kept"
            Check ($u.Text -match 'present /home/sansforensics/.local/bin/claude' -and $u.Text -match 'present /home/sansforensics/.claude/CLAUDE.md') 'Claude Code and Protocol SIFT kept'
            Check ($u.Text -match '(?m)^0$') '.bashrc block removed'
            Check ($u.Text -match 'purge exit=0' -and $u.Text -notmatch 'after purge present') 'purge removed the rest'
        }
    }
    $overall = if (@($Results | Where-Object Status -ne 'PASS').Count -eq 0) { 'PASS' } else { 'FAIL' }
}
catch {
    Say "ABORTED: $(Protect $_.Exception.Message)" 'Red'
}
finally {
    Stop-FileServer $script:Server
    foreach ($vm in $Created) {
        try { Collect $vm } catch { Say "  (collect $vm failed: $_)" 'DarkYellow' }
    }
    if ($Secret) {
        $leaks = Get-ChildItem -LiteralPath $OutDir -Recurse -File | Where-Object {
            try { [IO.File]::ReadAllText($_.FullName).Contains($Secret) } catch { $false } }
        foreach ($l in $leaks) {
            [IO.File]::WriteAllText($l.FullName, ([IO.File]::ReadAllText($l.FullName)).Replace($Secret, '[REDACTED-KEY]'))
            $Results.Add([pscustomobject]@{ Step = 'leak'; Title = "key found in $($l.Name) (redacted)"; Status = 'FAIL'; Seconds = 0; Error = '' })
            $overall = 'FAIL'
        }
        Say "Key leak scan of collected files: $(@($leaks).Count) file(s) contained the key"
    }
    foreach ($vm in $Created) {
        if ($KeepVm) { Say "  -KeepVm: left $vm running" 'Yellow'; continue }
        try { $null = & (Join-Path $PSScriptRoot 'Remove-TestVm.ps1') -Vm $vm; Say "  removed clone $vm" }
        catch { Say "  could not remove $vm`: $_" 'Red' }
    }
    Remove-Item -Recurse -Force -LiteralPath $TmpDir -ErrorAction SilentlyContinue
    Say ''
    Say '=== Summary ===' 'Cyan'
    foreach ($r in $Results) {
        Say ("  {0,-4} {1,-5} {2,5} s  {3}{4}" -f $r.Step, $r.Status, $r.Seconds, $r.Title, $(if ($r.Error) { " ($($r.Error))" } else { '' })) $(if ($r.Status -eq 'PASS') { 'Green' } else { 'Red' })
    }
    foreach ($k in $Metrics.Keys) { Say ("  {0} = {1}" -f $k, $Metrics[$k]) }
    $summary = [ordered]@{ run = $RunName; gateway = $Gateway; overall = $overall; steps = $Results; metrics = $Metrics
                           bootstrap_sha256 = $script:Sha }
    $summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $OutDir 'summary.json')
    Say "OVERALL: $overall   (logs in $OutDir)" $(if ($overall -eq 'PASS') { 'Green' } else { 'Red' })
}
if ($overall -ne 'PASS') { exit 1 }
exit 0
