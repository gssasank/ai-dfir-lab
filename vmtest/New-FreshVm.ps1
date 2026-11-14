#requires -Version 7
<#
.SYNOPSIS
  Creates a new test VM from the base VM's "pristine" snapshot under E:\dfir-lab-vms\runs\<Name>\
  and boots it headless.

.DESCRIPTION
  Default is a linked clone (seconds to create, a few hundred MB on disk; it depends on the base
  VM's pristine snapshot, which must not be deleted while clones exist). -Full makes an
  independent full clone (about 18 GB, several minutes).
  The base VM itself is never started or changed.
  Writes an object with Name, Vmx, IP, ReadySeconds and ToolsState.

.EXAMPLE
  ./New-FreshVm.ps1 -Name run01
  ./Invoke-Guest.ps1 -Vm run01 -Command 'cat /etc/os-release'
  ./Remove-TestVm.ps1 -Vm run01
#>
param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')][string]$Name,
    [string]$Snapshot = 'pristine',
    [switch]$Full,
    [switch]$NoStart,
    [int]$TimeoutSec = 900
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'VmTest.Common.ps1')

if (-not (Test-Path -LiteralPath $BaseVmx)) { throw "Base VM not found: $BaseVmx" }
$dir = Assert-UnderRuns (Join-Path $RunsRoot $Name)
if (Test-Path -LiteralPath $dir) { throw "Already exists: $dir (remove it with Remove-TestVm.ps1 first)" }
New-Item -ItemType Directory -Force -Path $RunsRoot | Out-Null
$vmx = Join-Path $dir "$Name.vmx"

$type = if ($Full) { 'full' } else { 'linked' }
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = Invoke-Vmrun clone $BaseVmx $vmx $type "-snapshot=$Snapshot" "-cloneName=$Name"
if ($r.ExitCode -ne 0) { throw "vmrun clone failed ($($r.ExitCode)): $($r.Output)" }
$cloneSec = [int]$sw.Elapsed.TotalSeconds

$result = [ordered]@{ Name = $Name; Vmx = $vmx; CloneType = $type; CloneSeconds = $cloneSec
                      IP = $null; ReadySeconds = $null; ToolsState = $null }
if (-not $NoStart) {
    $r = Invoke-Vmrun start $vmx nogui
    if ($r.ExitCode -ne 0) { throw "vmrun start failed ($($r.ExitCode)): $($r.Output)" }
    $w = Wait-GuestReady -Vmx $vmx -TimeoutSec $TimeoutSec
    if (-not $w.Ready) { throw "Guest not ready after $($w.Seconds) s (tools state: $($w.ToolsState)). VM left running: $vmx" }
    $result.IP = $w.IP; $result.ReadySeconds = $w.Seconds; $result.ToolsState = $w.ToolsState
}
[pscustomobject]$result
