#requires -Version 7
<#
.SYNOPSIS
  Copies one host file into a running guest.
.EXAMPLE
  ./Copy-ToGuest.ps1 -Vm run01 -Source .\setup.sh -Destination /tmp/setup.sh
#>
param(
    [Parameter(Mandatory)][string]$Vm,           # run name or full .vmx path
    [Parameter(Mandatory)][string]$Source,       # host file
    [Parameter(Mandatory)][string]$Destination   # absolute guest path (file)
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'VmTest.Common.ps1')
$vmx = Resolve-Vmx $Vm
$src = (Resolve-Path -LiteralPath $Source).Path
$r = Invoke-Vmrun -Guest copyFileFromHostToGuest $vmx $src $Destination
if ($r.ExitCode -ne 0) { Write-Error "copyFileFromHostToGuest failed ($($r.ExitCode)): $($r.Output)" }
