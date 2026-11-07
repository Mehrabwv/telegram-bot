# Nightly backup to GitHub: commits whatever changed in this project and pushes it.
#
# Windows runs this every night through the scheduled task
# "Telegram bot nightly GitHub push" (see the README). What it did each night is
# written to scripts\nightly-push.log, which stays on this computer.
#
# Safety: before uploading, it checks every changed file for the keys and the chat
# ID in config.py and .env. If one turns up, nothing is uploaded and the log says
# which file. Files listed in .gitignore are never uploaded.
#
# Try it without uploading:  powershell -File scripts\nightly-github-push.ps1 -DryRun

param([switch]$DryRun)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$logFile = Join-Path $PSScriptRoot 'nightly-push.log'

function Write-Log($text) {
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm') + '  ' + $text
    if ($DryRun) { Write-Output ('[dry run] ' + $text) } else { Add-Content -Path $logFile -Value $line }
}

git add -A 2>$null
$changes = @(git diff --cached --name-status)

if ($changes.Count -gt 0) {
    # The private values that must never be uploaded
    $secrets = @()
    foreach ($file in 'config.py', '.env') {
        if (Test-Path $file) {
            Select-String -Path $file -Pattern '^\s*(TELEGRAM_TOKEN|GROQ_API_KEY|NYT_API_KEY|MY_CHAT_ID)\s*=\s*["'']?([^"''\s#]+)' |
                ForEach-Object {
                    $value = $_.Matches[0].Groups[2].Value
                    if ($value.Length -ge 6) { $secrets += $value }
                }
        }
    }
    $leaks = @()
    foreach ($path in @(git diff --cached --name-only --diff-filter=ACMR)) {
        $full = Join-Path $repo $path
        if (-not (Test-Path -LiteralPath $full)) { continue }
        $text = [IO.File]::ReadAllText($full)
        foreach ($secret in $secrets) {
            if ($text.Contains($secret)) { $leaks += $path; break }
        }
    }
    if ($leaks.Count -gt 0) {
        git reset -q
        Write-Log ('NOT uploaded: a key or your chat ID is in ' + ($leaks -join ', ') +
                   '. Take it out, or add the file to .gitignore.')
        exit 1
    }

    # Commit message: the date, then each file and what happened to it
    $words = @{ 'A' = 'added'; 'M' = 'changed'; 'D' = 'deleted'; 'R' = 'renamed'; 'C' = 'copied' }
    $lines = foreach ($line in $changes) {
        $parts = $line -split "`t"
        $kind = $words[$parts[0].Substring(0, 1)]
        if (-not $kind) { $kind = 'changed' }
        '- ' + $kind + ' ' + $parts[-1]
    }
    $message = 'Nightly update, ' + (Get-Date -Format 'yyyy-MM-dd') + "`n`n" + ($lines -join "`n") + "`n"

    if ($DryRun) {
        git reset -q
        Write-Log ("would commit " + $changes.Count + " file(s) with this message:`n" + $message)
        Write-Log ('checked ' + $secrets.Count + ' private values: none found in the changed files')
        exit 0
    }

    $messageFile = Join-Path $env:TEMP 'telegram-bot-nightly-commit.txt'
    [IO.File]::WriteAllText($messageFile, $message)
    git commit -q -F $messageFile
    Remove-Item $messageFile -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) { Write-Log 'Commit failed.'; exit 1 }
}

# Upload anything not on GitHub yet (including a commit whose upload failed before)
$ahead = git rev-list --count '@{u}..HEAD' 2>$null
if ($DryRun) { Write-Log 'nothing to commit'; exit 0 }
if ($LASTEXITCODE -eq 0 -and $ahead -eq '0') { Write-Log 'Nothing changed.'; exit 0 }

$output = git push -q origin HEAD 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log ('Committed, but the upload failed (tried again tomorrow night): ' + ($output | Out-String).Trim())
    exit 1
}
Write-Log ('Uploaded ' + (git log -1 --format='%h %s'))
