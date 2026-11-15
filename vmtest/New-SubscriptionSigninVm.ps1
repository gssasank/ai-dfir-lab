#requires -Version 7
<#
.SYNOPSIS
  Prepares a lab VM for a person to sign in with their own Claude subscription: a linked
  clone (default name "sub-signin"), started WITH its VMware console window, set up with the
  release setup script (dist/bootstrap.sh), the real case kit and the real disk image, and
  switched to the subscription route. Nobody signs in here.

.DESCRIPTION
  1. Linked clone of the base VM's pristine snapshot under E:\dfir-lab-vms\runs\<Name>\,
     started with the graphical console ("vmrun start ... gui") so its window is on this
     machine's desktop.
  2. The disk image zip reaches the guest through a READ-ONLY shared folder and is copied into
     ~/Downloads, as a student's download would be. Nothing from it is run. The shared folder
     is removed afterwards.
  3. dist/bootstrap.sh and the kit are served over HTTP on the VMnet8 address and the student
     one-liner is run as the student in the desktop session (DFIR_LAB_KIT_URL points at the
     served kit while the repository is private). Must end SETUP COMPLETE.
  4. `dfir-lab use subscription` (non-interactive: it switches the settings and stores the
     route, then stops at the sign-in, which needs a person). The zip in ~/Downloads is
     deleted (the lab uses the unpacked copy).
  5. Prints what to click and type in the VM. The VM is left running.

  No Duke key is stored. Verify-SubscriptionRoute.ps1 stores one (-KeyFile) when it switches
  back to the Duke route.

.EXAMPLE
  ./New-SubscriptionSigninVm.ps1
#>
param(
    [ValidatePattern('^sub-[A-Za-z0-9._-]{1,40}$')][string]$Name = 'sub-signin',
    [string]$ImagePath,
    [string]$KitPath,
    [string]$HostAddress = '192.168.172.1',
    [int]$Port = 8473,
    [int]$TimeoutSec = 2700
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'VmTest.Common.ps1')

$Repo = Split-Path $PSScriptRoot
$OutDir = Join-Path $Repo "build\vmtest\$Name-setup-$(Get-Date -Format 'MMdd-HHmmss')"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Transcript = Join-Path $OutDir 'console.txt'
$ImageHost = if ($ImagePath) { $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ImagePath) } else { Join-Path $Repo 'evidence\raw\DC01-E01.zip' }
$KitHost = if ($KitPath) { $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($KitPath) } else { Join-Path $Repo 'kit\szechuan-kit-0.1.0.zip' }
$ImageName = [IO.Path]::GetFileName($ImageHost)
$KitName = [IO.Path]::GetFileName($KitHost)
$GuestDir = '/tmp/dfir-test'
$Share = '/mnt/dfir-raw'

function Say([string]$Text, [string]$Color = 'Gray') { Write-Host $Text -ForegroundColor $Color; Add-Content -LiteralPath $Transcript -Value $Text }
function G([string]$Cmd, [switch]$Sudo, [int]$Timeout = 900) {
    $a = @{ Vm = $Name; Command = $Cmd; TimeoutSec = $Timeout }
    if ($Sudo) { $a.Sudo = $true }
    try { $t = & (Join-Path $PSScriptRoot 'Invoke-Guest.ps1') @a 2>&1 | Out-String; $rc = $LASTEXITCODE }
    catch { $t = "guest operation failed: $($_.Exception.Message)"; $rc = 125 }
    [pscustomobject]@{ Rc = $rc; Text = $t.TrimEnd() }
}
function ToGuest([string]$Source, [string]$Dest) {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetFileName($Source) + '.' + [guid]::NewGuid().ToString('N').Substring(0, 6))
    [IO.File]::WriteAllText($tmp, ((Get-Content -LiteralPath $Source -Raw) -replace "`r`n", "`n"))
    try { & (Join-Path $PSScriptRoot 'Copy-ToGuest.ps1') -Vm $Name -Source $tmp -Destination $Dest } finally { Remove-Item -LiteralPath $tmp -Force }
}

foreach ($p in (Join-Path $Repo 'dist\bootstrap.sh'), $KitHost, $ImageHost) {
    if (-not (Test-Path -LiteralPath $p)) { throw "Missing: $p" }
}
$sha = ((Get-Content -LiteralPath (Join-Path $Repo 'dist\bootstrap.sh.sha256') -Raw) -split '\s+')[0]
if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Repo 'dist\bootstrap.sh')).Hash.ToLower() -ne $sha) { throw 'dist/bootstrap.sh does not match dist/bootstrap.sh.sha256' }

Say "Preparing $Name (setup script $sha)" 'Cyan'
$r = & (Join-Path $PSScriptRoot 'New-FreshVm.ps1') -Name $Name -NoStart
$vmx = $r.Vmx
$s = Invoke-Vmrun start $vmx gui
if ($s.ExitCode -ne 0) { throw "vmrun start gui failed: $($s.Output)" }
$w = Wait-GuestReady -Vmx $vmx -TimeoutSec 900
if (-not $w.Ready) { throw "Guest not ready after $($w.Seconds) s" }
Say "  $Name started with its console window; guest ready after $($w.Seconds) s"
$null = G "mkdir -p $GuestDir && chmod 755 $GuestDir"
ToGuest (Join-Path $PSScriptRoot 'guest\start-bootstrap.sh') "$GuestDir/start-bootstrap.sh"

# Disk image through a read-only shared folder, copied into ~/Downloads.
$null = Invoke-Vmrun enableSharedFolders $vmx
$a = Invoke-Vmrun addSharedFolder $vmx dfirraw (Split-Path $ImageHost)
if ($a.ExitCode -ne 0) { throw "addSharedFolder failed: $($a.Output)" }
$null = Invoke-Vmrun setSharedFolderState $vmx dfirraw (Split-Path $ImageHost) readonly
$m = G ("mkdir -p $Share && (mountpoint -q $Share || vmhgfs-fuse .host:/dfirraw $Share -o ro,allow_other) && " +
        "findmnt -rn -o OPTIONS --mountpoint $Share") -Sudo
if ($m.Text -notmatch '(^|,)ro(,|$)') { throw "read-only share not usable: $($m.Text)" }
$c = G "cp '$Share/$ImageName' ~/Downloads/ && stat -c '%s %n' ~/Downloads/$ImageName" -Timeout 1800
Say "  $($c.Text)"

# Serve the script and the kit, run the student one-liner in the desktop session.
$files = @{ '/dist/bootstrap.sh' = (Join-Path $Repo 'dist\bootstrap.sh'); "/kit/$KitName" = $KitHost }
$blobs = @{}; foreach ($k in $files.Keys) { $blobs[$k] = [IO.File]::ReadAllBytes($files[$k]) }
$state = [hashtable]::Synchronized(@{ Stop = $false })
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Parse($HostAddress), $Port)
$listener.Start()
$job = Start-ThreadJob -ArgumentList $listener, $blobs, $state -ScriptBlock {
    param($l, $blobs, $state)
    while (-not $state.Stop) {
        try { $cl = $l.AcceptTcpClient() } catch { break }
        try {
            $st = $cl.GetStream(); $st.ReadTimeout = 10000
            $rd = [IO.StreamReader]::new($st, [Text.Encoding]::ASCII, $false, 4096, $true)
            $req = $rd.ReadLine(); while ($true) { $h = $rd.ReadLine(); if ($null -eq $h -or $h -eq '') { break } }
            $path = ($req -split ' ')[1]
            if ($blobs.ContainsKey($path)) { $body = $blobs[$path]; $code = '200 OK' } else { $body = [Text.Encoding]::ASCII.GetBytes("not found`n"); $code = '404 Not Found' }
            $hdr = [Text.Encoding]::ASCII.GetBytes("HTTP/1.1 $code`r`nContent-Length: $($body.Length)`r`nConnection: close`r`n`r`n")
            $st.Write($hdr, 0, $hdr.Length); $st.Write($body, 0, $body.Length); $st.Flush()
        } catch { } finally { $cl.Close() }
    }
}
try {
    $cmd = "curl -fsSLO http://$HostAddress`:$Port/dist/bootstrap.sh && echo `"$sha  bootstrap.sh`" | sha256sum -c && DFIR_LAB_KIT_URL=http://$HostAddress`:$Port/kit/$KitName bash bootstrap.sh"
    $local = Join-Path ([IO.Path]::GetTempPath()) "signin-command-$([guid]::NewGuid().ToString('N').Substring(0,6)).txt"
    [IO.File]::WriteAllText($local, $cmd + "`n")
    $null = G "mkdir -p $GuestDir/setup"
    & (Join-Path $PSScriptRoot 'Copy-ToGuest.ps1') -Vm $Name -Source $local -Destination "$GuestDir/setup/command.txt"
    Remove-Item -LiteralPath $local -Force
    $st = G "bash $GuestDir/start-bootstrap.sh dfir-signin-setup $GuestDir/setup plain"
    Say "  $($st.Text)"
    $sw = [Diagnostics.Stopwatch]::StartNew(); $rc = $null
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        Start-Sleep -Seconds 15
        $p = G "cat $GuestDir/setup/rc 2>/dev/null; tail -n 1 $GuestDir/setup/screen.txt 2>/dev/null" -Timeout 120
        $lines = $p.Text -split "`n"
        if ($lines[0] -match '^\d+$') { $rc = [int]$lines[0]; break }
    }
    $screen = (G "cat $GuestDir/setup/screen.txt").Text
    [IO.File]::WriteAllText((Join-Path $OutDir 'setup-screen.txt'), $screen)
    foreach ($l in ($screen -split "`n")) { Say "  | $l" 'White' }
    if ($rc -ne 0 -or $screen -notmatch 'SETUP COMPLETE') { throw "the setup did not complete (exit $rc)" }
}
finally {
    $state.Stop = $true; try { $listener.Stop() } catch { }
    Wait-Job $job -Timeout 5 | Out-Null; Remove-Job $job -Force -ErrorAction SilentlyContinue
}

# Switch to the subscription route (the sign-in itself needs a person) and tidy up.
$u = G @'
dfir-lab use subscription --no-gui < /dev/null; echo "use-exit=$?"
echo "route=$(jq -r .route ~/dfir-lab/route.json)"
jq -r '"helper=\(.apiKeyHelper // "none")", "base_url=\(.env.ANTHROPIC_BASE_URL // "none")", "model=\(.env.ANTHROPIC_MODEL)"' ~/.claude/settings.json
echo "signed-in-file=$([ -e ~/.claude/.credentials.json ] && echo present || echo absent)"
rm -f ~/Downloads/DC01-E01.zip
dfir-lab check --quick --no-gui | sed -n '3,8p'
'@ -Timeout 600
[IO.File]::WriteAllText((Join-Path $OutDir 'switch.txt'), $u.Text)
Say ($u.Text -replace '(?m)^', '    ')
$null = G "umount $Share 2>/dev/null; rmdir $Share 2>/dev/null; rm -rf $GuestDir; true" -Sudo
$null = Invoke-Vmrun removeSharedFolder $vmx dfirraw
$null = Invoke-Vmrun disableSharedFolders $vmx
if ($u.Text -notmatch 'route=subscription' -or $u.Text -notmatch 'helper=none' -or $u.Text -notmatch 'base_url=none') { throw 'the switch to the subscription route did not take effect' }
Say ''
Say "$Name is ready and running with its window open. In the VM:" 'Green'
Say '  1. Double-click "Use my Claude subscription" on the desktop.'
Say '  2. Chromium opens a Claude sign-in page: sign in with the subscription account, click Authorize.'
Say '  3. Back in the terminal window it says "Done: Claude Code is signed in with a Claude ... subscription". Press Enter to close it.'
Say "Then, on this machine: ./Verify-SubscriptionRoute.ps1 -Vm $Name -KeyFile <Duke key file>"
Say "Log: $OutDir"
