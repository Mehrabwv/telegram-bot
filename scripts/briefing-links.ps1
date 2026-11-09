<#
Briefing link helper: opens the Briefing app's links in your default browser
(Opera, say) instead of Edge.

Why: Opera can't install web apps, so Briefing is installed from Edge, and an app
installed from Edge opens its links in Edge. On the computer the bot runs on, the
bot already opens them in your default browser; this helper does it on your other
computers (and on every computer once the bot moves to the Raspberry Pi).

Install (once on each computer):
    Right-click this file -> Run with PowerShell
    (or: powershell -ExecutionPolicy Bypass -File briefing-links.ps1)
Then in Briefing: Settings -> Open links -> turn on "Use the link helper".
The first link asks "Open Briefing link?": tick "Always allow" and click Open.

Remove:
    powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Briefing\briefing-links.ps1" -Uninstall

What it changes: it adds a "briefing-open:" link type for your Windows user only
(no admin rights) and copies itself to %LOCALAPPDATA%\Briefing. It only ever opens
http and https links, in whatever your default browser is.
#>
param([string]$Open, [switch]$Uninstall)

$folder = Join-Path $env:LOCALAPPDATA "Briefing"
$installed = Join-Path $folder "briefing-links.ps1"
$key = "HKCU:\Software\Classes\briefing-open"

if ($Open) {
    # "briefing-open:https%3A%2F%2F..." -> "https://..."
    try {
        $url = [uri]([uri]::UnescapeDataString(($Open -replace '^briefing-open:', '')))
    } catch {
        exit 1
    }
    if ($url.IsAbsoluteUri -and ($url.Scheme -eq "https" -or $url.Scheme -eq "http")) {
        Start-Process $url.AbsoluteUri
    }
    exit 0
}

if ($Uninstall) {
    Remove-Item -Path $key -Recurse -ErrorAction SilentlyContinue
    Remove-Item -Path $folder -Recurse -ErrorAction SilentlyContinue
    Write-Host "Briefing link helper removed."
    exit 0
}

New-Item -ItemType Directory -Force -Path $folder | Out-Null
if ($PSCommandPath -ne $installed) {
    Copy-Item -LiteralPath $PSCommandPath -Destination $installed -Force
}
New-Item -Path "$key\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path $key -Name "(default)" -Value "URL:Briefing link"
Set-ItemProperty -Path $key -Name "URL Protocol" -Value ""
# conhost --headless: no console window flashing up
$command = "conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$installed`" -Open `"%1`""
Set-ItemProperty -Path "$key\shell\open\command" -Name "(default)" -Value $command
Write-Host "Briefing link helper installed."
Write-Host "Now in Briefing: Settings -> Open links -> turn on 'Use the link helper'."
Start-Sleep -Seconds 4
