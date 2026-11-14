#requires -Version 7
<#
.SYNOPSIS
  Powers off and deletes a test VM. Refuses anything outside E:\dfir-lab-vms\runs\.

.EXAMPLE
  ./Remove-TestVm.ps1 -Vm run01
  ./Remove-TestVm.ps1 -Vm E:\dfir-lab-vms\runs\run01\run01.vmx
#>
param([Parameter(Mandatory)][string]$Vm)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'VmTest.Common.ps1')

$vmx = Assert-UnderRuns (Resolve-Vmx $Vm)
$dir = Assert-UnderRuns (Split-Path $vmx)
# Only a direct child folder of the runs root may be deleted.
if ((Split-Path $dir) -ne [IO.Path]::GetFullPath($RunsRoot).TrimEnd('\')) {
    throw "Refusing: '$dir' is not a direct child of $RunsRoot"
}
if ($vmx -ieq [IO.Path]::GetFullPath($BaseVmx)) { throw "Refusing to touch the base VM" }

if (Test-Path -LiteralPath $vmx) {
    if (Test-VmRunning $vmx) {
        $r = Invoke-Vmrun stop $vmx soft
        if ($r.ExitCode -ne 0 -or (Test-VmRunning $vmx)) { $null = Invoke-Vmrun stop $vmx hard }
        $sw = [Diagnostics.Stopwatch]::StartNew()
        while ((Test-VmRunning $vmx) -and $sw.Elapsed.TotalSeconds -lt 120) { Start-Sleep 2 }
        if (Test-VmRunning $vmx) { throw "Could not power off $vmx" }
    }
    $r = Invoke-Vmrun deleteVM $vmx
    if ($r.ExitCode -ne 0) { Write-Warning "vmrun deleteVM failed ($($r.ExitCode)): $($r.Output); deleting the folder instead" }
}
if (Test-Path -LiteralPath $dir) {
    $null = Assert-UnderRuns $dir
    Remove-Item -LiteralPath $dir -Recurse -Force
}
"Removed $dir"
