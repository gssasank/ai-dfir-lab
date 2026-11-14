# Shared settings for the vmtest scripts. Dot-source this file; do not run it directly.
# The guest credentials are the public SANS SIFT defaults, not a secret.

$script:VmwareDir   = 'C:\Program Files\VMware\VMware Workstation'
$script:VmrunExe    = Join-Path $VmwareDir 'vmrun.exe'
$script:OvfToolExe  = Join-Path $VmwareDir 'OVFTool\ovftool.exe'
$script:VmRoot      = 'E:\dfir-lab-vms'
$script:BaseVmx     = 'E:\dfir-lab-vms\sift-base\sift-base.vmx'
$script:RunsRoot    = 'E:\dfir-lab-vms\runs'
$script:GuestUser   = 'sansforensics'
$script:GuestPass   = 'forensics'

function Invoke-Vmrun {
    # Runs vmrun with the Workstation host type. Add -Guest to pass guest credentials.
    param([switch]$Guest, [Parameter(ValueFromRemainingArguments)][string[]]$VmrunArgs)
    $all = @('-T', 'ws')
    if ($Guest) { $all += @('-gu', $script:GuestUser, '-gp', $script:GuestPass) }
    $all += $VmrunArgs
    $out = & $script:VmrunExe @all 2>&1
    [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = ($out -join "`n") }
}

function Resolve-Vmx {
    # Accepts a .vmx path or a run name under E:\dfir-lab-vms\runs\.
    param([Parameter(Mandatory)][string]$VmxOrName)
    if ($VmxOrName -like '*.vmx') { return [IO.Path]::GetFullPath($VmxOrName) }
    if ($VmxOrName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
        throw "Expected a .vmx path or a run name (letters, digits, . _ -), got '$VmxOrName'"
    }
    return [IO.Path]::GetFullPath((Join-Path $script:RunsRoot "$VmxOrName\$VmxOrName.vmx"))
}

function Test-VmRunning {
    param([Parameter(Mandatory)][string]$Vmx)
    $list = (Invoke-Vmrun list).Output -split "`n" | ForEach-Object { $_.Trim() }
    return [bool]($list | Where-Object { $_ -ieq $Vmx })
}

function Wait-GuestReady {
    # Waits until a guest operation with the default login succeeds.
    # `vmrun checkToolsState` and `vmrun getGuestIPAddress` are NOT used as the signal: on this
    # host they reported "installed" / "Tools not running" for 5-10 minutes after boot although
    # vmware.log showed Tools running after ~15 s and guest operations already worked.
    # IP is filled in only if getGuestIPAddress happens to answer; it may be $null.
    param([Parameter(Mandatory)][string]$Vmx, [int]$TimeoutSec = 600)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        $probe = Invoke-Vmrun -Guest directoryExistsInGuest $Vmx /tmp
        if ($probe.Output -match 'exists') {
            $ip = (Invoke-Vmrun getGuestIPAddress $Vmx).Output.Trim()
            return [pscustomobject]@{
                Ready = $true; Seconds = [int]$sw.Elapsed.TotalSeconds
                IP = $(if ($ip -match '^\d+\.\d+\.\d+\.\d+$') { $ip } else { $null })
                ToolsState = (Invoke-Vmrun checkToolsState $Vmx).Output.Trim()
            }
        }
        Start-Sleep -Seconds 5
    }
    return [pscustomobject]@{ Ready = $false; Seconds = [int]$sw.Elapsed.TotalSeconds; IP = $null
                              ToolsState = (Invoke-Vmrun checkToolsState $Vmx).Output.Trim() }
}

function Assert-UnderRuns {
    # Throws unless $Path is strictly inside E:\dfir-lab-vms\runs\ and no part of it below
    # that root is a junction or symbolic link.
    param([Parameter(Mandatory)][string]$Path)
    $root = [IO.Path]::GetFullPath($script:RunsRoot).TrimEnd('\') + '\'
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -or $full.Length -le $root.Length) {
        throw "Refusing: '$full' is not inside $root"
    }
    $p = $full
    while ($p.Length -gt $root.Length) {
        if ((Test-Path -LiteralPath $p) -and ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing: '$p' is a junction or link"
        }
        $p = Split-Path $p
    }
    return $full
}
