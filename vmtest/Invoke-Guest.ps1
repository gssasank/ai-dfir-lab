#requires -Version 7
<#
.SYNOPSIS
  Runs a shell command or a local script inside a running guest and returns its output and exit code.

.DESCRIPTION
  Uses vmrun guest operations with the public SANS default login. The payload runs under
  /bin/bash as the guest user (or as root with -Sudo). Stdout and stderr are combined,
  copied back to the host, written to this script's output, and the guest exit code becomes
  this script's exit code ($LASTEXITCODE). Exit code 124 means the host-side timeout fired.

  Line endings of the payload are converted to LF before it is copied into the guest.

  -DesktopEnv exports the variables a GNOME Terminal in the logged-in desktop session would
  have (XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS, WAYLAND_DISPLAY, DISPLAY), so commands such
  as gsettings and gio talk to the running session.

  Without -LoginShell the payload gets the minimal environment of the open-vm-tools service
  (LANG=C.UTF-8, PATH without ~/.local/bin). -LoginShell runs it with `bash -l`, which reads
  ~/.profile like a login session does. -LoginShell is ignored with -Sudo.

.EXAMPLE
  ./Invoke-Guest.ps1 -Vm run01 -Command 'lsb_release -a'
.EXAMPLE
  ./Invoke-Guest.ps1 -Vm run01 -ScriptPath .\guest\inventory.sh -Arguments system -OutFile .\out\system.txt
.EXAMPLE
  ./Invoke-Guest.ps1 -Vm run01 -Sudo -Command 'apt-get update'
#>
[CmdletBinding(DefaultParameterSetName = 'Command')]
param(
    [Parameter(Mandatory)][string]$Vm,                                   # run name or full .vmx path
    [Parameter(Mandatory, ParameterSetName = 'Command')][string]$Command,
    [Parameter(Mandatory, ParameterSetName = 'Script')][string]$ScriptPath,
    [string[]]$Arguments = @(),
    [switch]$Sudo,
    [switch]$DesktopEnv,
    [switch]$LoginShell,
    [int]$TimeoutSec = 1800,
    [string]$OutFile,
    [switch]$KeepGuestFiles
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'VmTest.Common.ps1')
$vmx = Resolve-Vmx $Vm

function Format-ShArg([string]$s) { "'" + ($s -replace "'", "'\''") + "'" }

$id = [guid]::NewGuid().ToString('N').Substring(0, 10)
$gdir = "/tmp/vmtest-$id"
$tmp = Join-Path ([IO.Path]::GetTempPath()) "vmtest-$id"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

try {
    $payload = if ($PSCmdlet.ParameterSetName -eq 'Script') {
        Get-Content -LiteralPath $ScriptPath -Raw
    } else { $Command + "`n" }
    [IO.File]::WriteAllText((Join-Path $tmp 'payload.sh'), ($payload -replace "`r`n", "`n"))

    $argStr = ($Arguments | ForEach-Object { Format-ShArg $_ }) -join ' '
    $envLines = if ($DesktopEnv) {
        @(
            'export XDG_RUNTIME_DIR=/run/user/$(id -u)'
            'export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus'
            'export WAYLAND_DISPLAY=wayland-0 DISPLAY=:0'
        ) -join "`n"
    } else { '' }
    $run = if ($Sudo) {
        @"
if sudo -n true 2>/dev/null; then
  sudo -H bash "$gdir/payload.sh" $argStr
else
  printf '%s\n' '$GuestPass' | sudo -S -p '' -H bash "$gdir/payload.sh" $argStr
fi
"@
    } elseif ($LoginShell) { "bash -l `"$gdir/payload.sh`" $argStr" }
    else { "bash `"$gdir/payload.sh`" $argStr" }

    $runner = @"
#!/bin/bash
cd "`$HOME" 2>/dev/null || cd /
$envLines
{
$run
} > "$gdir/out.txt" 2>&1 < /dev/null
echo `$? > "$gdir/rc.txt"
"@
    [IO.File]::WriteAllText((Join-Path $tmp 'runner.sh'), ($runner -replace "`r`n", "`n"))

    # Retry while VMware Tools restarts in the guest (installing open-vm-tools-desktop
    # upgrades open-vm-tools and restarts vmtoolsd; guest operations fail meanwhile).
    function Invoke-GuestOpRetry([string[]]$OpArgs) {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            $res = Invoke-Vmrun -Guest @OpArgs
            if ($res.ExitCode -eq 0 -or $res.Output -notmatch 'Tools are not running|not running in the guest' -or
                $sw.Elapsed.TotalSeconds -gt 180) { return $res }
            Start-Sleep -Seconds 5
        }
    }
    $r = Invoke-GuestOpRetry @('createDirectoryInGuest', $vmx, $gdir)
    if ($r.ExitCode -ne 0) { throw "createDirectoryInGuest failed: $($r.Output)" }
    foreach ($f in 'payload.sh', 'runner.sh') {
        $r = Invoke-GuestOpRetry @('copyFileFromHostToGuest', $vmx, (Join-Path $tmp $f), "$gdir/$f")
        if ($r.ExitCode -ne 0) { throw "copyFileFromHostToGuest $f failed: $($r.Output)" }
    }

    # Run with a host-side timeout. vmrun blocks until the guest program exits.
    $psi = [Diagnostics.ProcessStartInfo]::new($VmrunExe)
    foreach ($a in @('-T', 'ws', '-gu', $GuestUser, '-gp', $GuestPass, 'runProgramInGuest', $vmx, '/bin/bash', "$gdir/runner.sh")) {
        $psi.ArgumentList.Add($a)
    }
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [Diagnostics.Process]::Start($psi)
    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
        $p.Kill($true)
        Write-Warning "Host-side timeout after $TimeoutSec s; the guest process may still be running."
        $rc = 124
    }
    $vmrunMsg = ($p.StandardOutput.ReadToEnd() + $p.StandardError.ReadToEnd()).Trim()

    $outHost = Join-Path $tmp 'out.txt'
    $rcHost = Join-Path $tmp 'rc.txt'
    $null = Invoke-Vmrun -Guest copyFileFromGuestToHost $vmx "$gdir/out.txt" $outHost
    $null = Invoke-Vmrun -Guest copyFileFromGuestToHost $vmx "$gdir/rc.txt" $rcHost
    if ($null -eq $rc) {
        if (Test-Path $rcHost) { $rc = [int](Get-Content $rcHost -Raw).Trim() }
        else { Write-Warning "No exit code came back from the guest. vmrun said: $vmrunMsg"; $rc = 255 }
    }
    $text = if (Test-Path $outHost) { Get-Content $outHost -Raw } else { '' }
    if ($OutFile) {
        $of = [IO.Path]::GetFullPath($OutFile)
        New-Item -ItemType Directory -Force -Path (Split-Path $of) | Out-Null
        [IO.File]::WriteAllText($of, "$text")
    }
    if ($text) { Write-Output $text.TrimEnd("`n") }

    if (-not $KeepGuestFiles) { $null = Invoke-Vmrun -Guest deleteDirectoryInGuest $vmx $gdir }
}
finally {
    Remove-Item -Recurse -Force -LiteralPath $tmp -ErrorAction SilentlyContinue
}
exit $rc
