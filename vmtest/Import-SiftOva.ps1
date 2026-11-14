#requires -Version 7
<#
.SYNOPSIS
  Imports the SIFT OVA into a new VM folder with ovftool and takes the "pristine" snapshot
  before the VM is ever powered on.

.DESCRIPTION
  Creates <ParentDir>\<Name>\<Name>.vmx. Refuses if that folder already exists, so it can never
  overwrite E:\dfir-lab-vms\sift-base\. The OVA is only read.

.EXAMPLE
  ./Import-SiftOva.ps1                                  # recreates E:\dfir-lab-vms\sift-base if it is missing
  ./Import-SiftOva.ps1 -Name sift-base2
#>
param(
    [string]$Ova = (Join-Path (Split-Path $PSScriptRoot) 'build\base\sift-2026-04-22.ova'),
    [string]$ParentDir = 'E:\dfir-lab-vms',
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')][string]$Name = 'sift-base'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'VmTest.Common.ps1')

$Ova = (Resolve-Path -LiteralPath $Ova).Path
$dir = Join-Path $ParentDir $Name
if (Test-Path -LiteralPath $dir) { throw "Refusing: $dir already exists" }
New-Item -ItemType Directory -Force -Path $ParentDir | Out-Null

# Given a directory target, ovftool creates <target>\<name>\<name>.vmx.
$sw = [Diagnostics.Stopwatch]::StartNew()
& $OvfToolExe --acceptAllEulas "--name=$Name" $Ova $ParentDir
if ($LASTEXITCODE -ne 0) { throw "ovftool failed with exit code $LASTEXITCODE" }
$importSec = [int]$sw.Elapsed.TotalSeconds

$vmx = Join-Path $dir "$Name.vmx"
if (-not (Test-Path -LiteralPath $vmx)) { throw "ovftool did not create $vmx" }
$r = Invoke-Vmrun snapshot $vmx pristine
if ($r.ExitCode -ne 0) { throw "snapshot failed: $($r.Output)" }

[pscustomobject]@{
    Vmx = $vmx; ImportSeconds = $importSec
    SizeGB = [math]::Round(((Get-ChildItem -LiteralPath $dir -File | Measure-Object Length -Sum).Sum / 1GB), 2)
    Snapshots = (Invoke-Vmrun listSnapshots $vmx).Output
}
