#requires -Version 7
<#
.SYNOPSIS
  Copies one guest file to the host.
.EXAMPLE
  ./Copy-FromGuest.ps1 -Vm run01 -Source /tmp/setup.log -Destination .\out\setup.log
#>
param(
    [Parameter(Mandatory)][string]$Vm,           # run name or full .vmx path
    [Parameter(Mandatory)][string]$Source,       # absolute guest path (file)
    [Parameter(Mandatory)][string]$Destination   # host file path
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'VmTest.Common.ps1')
$vmx = Resolve-Vmx $Vm
$dest = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
$r = Invoke-Vmrun -Guest copyFileFromGuestToHost $vmx $Source $dest
if ($r.ExitCode -ne 0) { Write-Error "copyFileFromGuestToHost failed ($($r.ExitCode)): $($r.Output)" }
