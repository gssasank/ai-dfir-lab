#requires -Version 7
<#
.SYNOPSIS
  Runs guest\inventory.sh section by section in a running guest and saves each section's raw
  output as <OutDir>\<section>.txt.
.EXAMPLE
  ./Invoke-Inventory.ps1 -Vm run01 -OutDir .\out\run01
  ./Invoke-Inventory.ps1 -Vm run01 -OutDir .\out\run01 -Sections system,forensic
#>
param(
    [Parameter(Mandatory)][string]$Vm,
    [Parameter(Mandatory)][string]$OutDir,
    [string[]]$Sections = @('packages-user', 'system', 'accounts', 'network', 'languages', 'ai',
                            'root', 'forensic', 'desktop', 'sift', 'packages-root', 'evtx-test')
)
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'guest\inventory.sh'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
foreach ($s in $Sections) {
    $asRoot = $s -eq 'root' -or $s -like '*-root'
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = @{ Vm = $Vm; ScriptPath = $script; Arguments = @($s); OutFile = (Join-Path $OutDir "$s.txt"); TimeoutSec = 2400 }
    if ($asRoot) { $p.Sudo = $true } else { $p.DesktopEnv = $true }
    $null = & (Join-Path $PSScriptRoot 'Invoke-Guest.ps1') @p
    $rc = $LASTEXITCODE
    "{0,-14} rc={1} seconds={2:n0} root={3}" -f $s, $rc, $sw.Elapsed.TotalSeconds, $asRoot
}
