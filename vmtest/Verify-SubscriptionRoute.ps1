#requires -Version 7
<#
.SYNOPSIS
  After a person has signed in to Claude Code with their own Claude subscription in a lab VM
  (normally the clone "sub-signin"), proves the subscription route end to end, then switches
  the VM back to the Duke route and proves that still works.

.DESCRIPTION
  1. Sign-in: `dfir-lab use --status` and `claude auth status --json` (only loggedIn,
     authMethod and subscriptionType are kept) must show a Claude subscription sign-in.
  2. `dfir-lab check --json`: every check that applies to the subscription route passes
     (6-9 are NA), READY, readiness code ...-RS-....
  3. Two real lab questions, non-interactively, in fresh Claude Code sessions with the lab's
     settings (guest/sub-verify.py, the style of guest/dryrun.py): checkpoints 3 and 6 by
     default. Each answer is checked with `dfir-lab checkpoint`. Reports the model used, the
     wall time, turns, tokens, Claude Code's own cost estimate, and retries or rate-limit /
     usage-limit messages.
  4. `dfir-lab use duke`, then (if no key is stored yet) the key from -KeyFile is stored with
     `dfir-lab setup-key --from-stdin` and the copy in the guest is shredded; then
     `dfir-lab check --json` must pass all eleven checks against the real Duke gateway.

  The VM is left on the Duke route; the Claude sign-in stays on the VM (sign out in the VM
  with: claude auth logout).

  Credentials: this script never opens Claude Code's sign-in file
  (~/.claude/.credentials.json). Every guest text is masked for anything shaped like an
  Anthropic credential (sk-ant-...) and for the Duke key before it is shown or saved, and the
  collected files are scanned for both at the end.

  Spend: step 3 uses the signed-in subscription's own allowance (Claude Sonnet by default).
  Step 4 runs the readiness check once against the Duke gateway (a few cents at most, Haiku).

.EXAMPLE
  ./Verify-SubscriptionRoute.ps1 -Vm sub-signin -KeyFile C:\path\to\duke-key.txt
.EXAMPLE
  ./Verify-SubscriptionRoute.ps1 -Vm sub-signin -SkipDuke
#>
param(
    [string]$Vm = 'sub-signin',
    # A file holding the Duke key; needed for step 4 only when the VM has no key stored.
    [string]$KeyFile,
    [string]$Checkpoints = '3,6',
    [int]$MaxTurns = 40,
    [switch]$SkipDuke,
    [string]$OutDir
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'VmTest.Common.ps1')

$Repo = Split-Path $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $Repo ("build\vmtest\sub-verify-" + (Get-Date -Format 'MMdd-HHmmss')) }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Transcript = Join-Path $OutDir 'console.txt'
$GuestDir = '/tmp/dfir-verify'
$Vmx = Resolve-Vmx $Vm
$Secret = $null
if ($KeyFile) {
    $KeyFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($KeyFile)
    if (-not (Test-Path -LiteralPath $KeyFile)) { throw "Key file not found: $KeyFile" }
    $Secret = (Get-Content -LiteralPath $KeyFile -Raw).Trim()
}
$Results = [System.Collections.Generic.List[object]]::new()
$CredRe = 'sk-ant-[a-z]{2,8}[0-9]{0,3}-[A-Za-z0-9_\-]{8,}'

function Protect([string]$Text) {
    if ($null -eq $Text) { return '' }
    $t = [regex]::Replace($Text, $CredRe, '[REDACTED-CREDENTIAL]')
    if ($Secret) { $t = $t.Replace($Secret, '[REDACTED-KEY]') }
    return $t
}
function Say([string]$Text, [string]$Color = 'Gray') {
    $t = Protect $Text
    Write-Host $t -ForegroundColor $Color
    Add-Content -LiteralPath $Transcript -Value $t
}
function G([string]$Cmd, [int]$Timeout = 900, [string]$Save) {
    try {
        $text = & (Join-Path $PSScriptRoot 'Invoke-Guest.ps1') -Vm $Vmx -Command $Cmd -TimeoutSec $Timeout 2>&1 | Out-String
        $rc = $LASTEXITCODE
    } catch { $text = "guest operation failed: $($_.Exception.Message)"; $rc = 125 }
    $text = Protect $text
    if ($Save) { [IO.File]::WriteAllText((Join-Path $OutDir $Save), $text) }
    [pscustomobject]@{ Rc = $rc; Text = $text.TrimEnd() }
}
function Check([bool]$Cond, [string]$What) {
    if ($Cond) { Say "    PASS  $What" 'Green' } else { Say "    FAIL  $What" 'Red'; $script:StepFailures++ }
}
function Invoke-Step([string]$Id, [string]$Title, [scriptblock]$Body) {
    Say ''; Say "=== Step $Id`: $Title ===" 'Cyan'
    $script:StepFailures = 0
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try { & $Body } catch { Say "    ERROR $($_.Exception.Message)" 'Red'; $script:StepFailures++ }
    $status = if ($script:StepFailures -eq 0) { 'PASS' } else { 'FAIL' }
    $Results.Add([pscustomobject]@{ Step = $Id; Title = $Title; Status = $status; Seconds = [int]$sw.Elapsed.TotalSeconds })
    Say "=== Step $Id`: $status ===" $(if ($status -eq 'PASS') { 'Green' } else { 'Red' })
}
function Read-Json([string]$Text) {
    try { return ($Text.Substring($Text.IndexOf('{'))) | ConvertFrom-Json } catch { return $null }
}

$overall = 'FAIL'
$qa = @()
try {
    Say "Verify-SubscriptionRoute on $Vmx - output in $OutDir" 'Cyan'
    if (-not (Test-VmRunning $Vmx)) { throw "The VM is not running: $Vmx" }
    $w = Wait-GuestReady -Vmx $Vmx -TimeoutSec 300
    if (-not $w.Ready) { throw 'Guest operations do not work in the VM.' }
    $null = G "mkdir -p $GuestDir && chmod 700 $GuestDir"
    $src = Join-Path $PSScriptRoot 'guest\sub-verify.py'
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "sub-verify-$([guid]::NewGuid().ToString('N').Substring(0,8)).py"
    [IO.File]::WriteAllText($tmp, ((Get-Content -LiteralPath $src -Raw) -replace "`r`n", "`n"))
    & (Join-Path $PSScriptRoot 'Copy-ToGuest.ps1') -Vm $Vmx -Source $tmp -Destination "$GuestDir/sub-verify.py"
    Remove-Item -LiteralPath $tmp -Force

    Invoke-Step '1' 'Claude Code is signed in with a Claude subscription' {
        $s = G @'
dfir-lab use --status --no-gui
~/.local/bin/claude auth status --json 2>/dev/null | jq -c '{loggedIn, authMethod, subscriptionType}'
'@ -Save 'signin.txt'
        Say ($s.Text -replace '(?m)^', '    ')
        Check ($s.Text -match 'AI access: your own Claude subscription\.') 'the VM is on the subscription route'
        Check ($s.Text -match 'Claude sign-in: signed in with a Claude .* subscription\.') 'dfir-lab says it is signed in with a subscription'
        Check ($s.Text -match '"loggedIn":true' -and $s.Text -match '"authMethod":"claude\.ai"') 'claude auth status: loggedIn, authMethod claude.ai'
        if ($s.Text -match '"subscriptionType":"([^"]*)"') { $script:Plan = $Matches[1] }
    }

    Invoke-Step '2' 'Route-aware readiness check: READY' {
        $c = G "dfir-lab check --json --no-gui > $GuestDir/check-sub.json; echo rc=`$?; cat $GuestDir/check-sub.json" -Timeout 900 -Save 'check-subscription.txt'
        $j = Read-Json $c.Text
        Check ($null -ne $j) 'check printed JSON'
        if ($j) {
            foreach ($r in $j.results) { Say ("    {0,2} {1,-17} {2,-4} {3}" -f $r.id, $r.name, $r.status, $r.result) }
            Check ($j.route -eq 'subscription') 'route: subscription'
            Check (@($j.results | Where-Object { $_.id -in 6, 7, 8, 9 -and $_.status -ne 'NA' }).Count -eq 0) 'checks 6-9 not applicable'
            Check (@($j.results | Where-Object { $_.id -notin 6, 7, 8, 9 -and $_.status -notin 'PASS', 'WARN' }).Count -eq 0) 'every applicable check passed (Claude Code task included)'
            Check ($j.ready -eq $true -and $j.code -match '-RS-[0-9a-f]{10}$') "READY, readiness code $($j.code)"
            $script:SubTokens = $j.tokens.total
        }
    }

    Invoke-Step '3' "Real lab questions (checkpoints $Checkpoints) on the subscription route" {
        $q = G "python3 $GuestDir/sub-verify.py $GuestDir/qa $Checkpoints --max-turns $MaxTurns" -Timeout 3600 -Save 'questions.txt'
        Say ($q.Text -replace '(?m)^', '    ')
        $sj = G "cat $GuestDir/qa/summary.jsonl"
        $script:qa = @($sj.Text -split "`n" | Where-Object { $_ -match '^\{' } | ForEach-Object { $_ | ConvertFrom-Json })
        foreach ($r in $script:qa) {
            $tok = 0; foreach ($p in $r.usage.PSObject.Properties) { if ($p.Value -is [int] -or $p.Value -is [long]) { $tok += $p.Value } }
            Say ("    cp{0}: {1}; model {2}; {3} s; {4} turns, {5} tool calls; tokens {6}; Claude Code's cost estimate `${7}; retries {8}; rate/usage-limit words: {9}" -f
                 $r.cp, $r.checkpoint, (($r.models + @()) -join ', '), $r.wall_s, $r.turns, $r.tool_calls, $tok,
                 $r.claude_cost_estimate_usd, $r.retries, $(if ($r.rate_limit_words_seen.Count) { $r.rate_limit_words_seen -join ', ' } else { 'none' }))
            Check ($r.exit -eq 0) "cp$($r.cp): Claude Code finished (exit $($r.exit))"
            Check ($r.checkpoint -eq 'CORRECT') "cp$($r.cp): dfir-lab checkpoint says CORRECT"
            Check (@($r.models | Where-Object { $_ -match 'sonnet' }).Count -ge 1) "cp$($r.cp): the model was Sonnet ($(($r.models + @()) -join ', '))"
        }
        Check ($script:qa.Count -eq ($Checkpoints -split ',').Count) 'every question was asked'
        $dir = Join-Path $OutDir 'qa'
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        foreach ($n in ($Checkpoints -split ',')) {
            foreach ($f in 'summary.json', 'result.txt') {
                $t = G "cat $GuestDir/qa/cp$n/$f"
                [IO.File]::WriteAllText((Join-Path $dir "cp$n-$f"), $t.Text)
            }
        }
    }

    if (-not $SkipDuke) {
        Invoke-Step '4' 'Back to the Duke route: every check passes against the real gateway' {
            $u = G 'dfir-lab use duke --no-gui; echo "rc=$?"; jq -r .route ~/dfir-lab/route.json; dfir-lab setup-key --status --no-gui > /dev/null; echo "key-stored=$?"' -Save 'use-duke.txt'
            Say ($u.Text -replace '(?m)^', '    ')
            Check ($u.Text -match 'rc=0' -and $u.Text -match '(?m)^duke$') 'dfir-lab use duke switched back'
            if ($u.Text -match 'key-stored=1') {
                if (-not $KeyFile) { throw 'No Duke key is stored in the VM; pass -KeyFile <file> (or -SkipDuke).' }
                $kf = Join-Path ([IO.Path]::GetTempPath()) ("k-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
                Copy-Item -LiteralPath $KeyFile -Destination $kf
                try { & (Join-Path $PSScriptRoot 'Copy-ToGuest.ps1') -Vm $Vmx -Source $kf -Destination "$GuestDir/k" }
                finally { Remove-Item -LiteralPath $kf -Force }
                $k = G "chmod 600 $GuestDir/k; dfir-lab setup-key --from-stdin --no-gui < $GuestDir/k; echo setup-key exit=`$?; shred -u $GuestDir/k" -Save 'setup-key.txt'
                Say ($k.Text -replace '(?m)^', '    ')
                Check ($k.Text -match 'setup-key exit=0') 'Duke key stored and tested'
            }
            $c = G "dfir-lab check --json --no-gui > $GuestDir/check-duke.json; echo rc=`$?; cat $GuestDir/check-duke.json" -Timeout 900 -Save 'check-duke.txt'
            $j = Read-Json $c.Text
            if ($j) { foreach ($r in $j.results) { Say ("    {0,2} {1,-17} {2,-4} {3}" -f $r.id, $r.name, $r.status, $r.result) } }
            Check ($null -ne $j -and $j.route -eq 'duke' -and $j.results.Count -eq 11 -and @($j.results | Where-Object status -notin 'PASS', 'WARN').Count -eq 0 -and $j.ready) "Duke route READY, code $($j.code)"
        }
    }
    $overall = if (@($Results | Where-Object Status -ne 'PASS').Count -eq 0) { 'PASS' } else { 'FAIL' }
}
catch { Say "ABORTED: $(Protect $_.Exception.Message)" 'Red' }
finally {
    $null = G "rm -rf $GuestDir"
    # Nothing collected may hold a credential or the key.
    $leaks = @(Get-ChildItem -LiteralPath $OutDir -Recurse -File | Where-Object {
        $t = [IO.File]::ReadAllText($_.FullName)
        ($t -match $CredRe) -or ($Secret -and $t.Contains($Secret)) })
    foreach ($l in $leaks) {
        [IO.File]::WriteAllText($l.FullName, (Protect ([IO.File]::ReadAllText($l.FullName))))
        $overall = 'FAIL'
    }
    Say "Credential scan of collected files: $($leaks.Count) file(s) held a credential or the key"
    Say ''; Say '=== Summary ===' 'Cyan'
    foreach ($r in $Results) { Say ("  {0,-3} {1,-5} {2,5} s  {3}" -f $r.Step, $r.Status, $r.Seconds, $r.Title) $(if ($r.Status -eq 'PASS') { 'Green' } else { 'Red' }) }
    $summary = [ordered]@{ vm = $Vmx; overall = $overall; plan = $script:Plan; steps = $Results
                           readiness_check_tokens = $script:SubTokens; questions = $script:qa }
    $summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $OutDir 'summary.json')
    Say "OVERALL: $overall   (logs in $OutDir)" $(if ($overall -eq 'PASS') { 'Green' } else { 'Red' })
}
if ($overall -ne 'PASS') { exit 1 }
exit 0
