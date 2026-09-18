# publish.ps1 - one-command publish of Q2/Q3 viewers to GitHub Pages
# Run from project root:  powershell -ExecutionPolicy Bypass -File publish.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot   # project root

$repo  = Join-Path $pwd "publish_repo"
$src   = Join-Path $pwd "analysis\output_review"
$files = @("q2_all_teams_viewer.html", "q3_rel_viewer.html", "q1_value_zones_viewer.html")

if (-not (Test-Path $repo)) { Write-Host "publish_repo NOT FOUND"; exit 1 }

foreach ($f in $files) {
    $s = Join-Path $src $f
    if (-not (Test-Path $s)) { Write-Host "missing source: $s"; exit 1 }
    Copy-Item $s (Join-Path $repo $f) -Force
    Write-Host "copied $f"
}

Set-Location $repo
git config user.name "dsh-publish"
git config user.email "dsh-publish@users.noreply.github.com"
$env:GIT_TERMINAL_PROMPT = "0"

git add -A
$st = git status --porcelain
if ($st) {
    git commit -m ("Update data viewers " + (Get-Date -Format "yyyy-MM-dd HH:mm"))
    git push origin main
    Write-Host "PUSHED."
} else {
    Write-Host "NO CHANGES - already up to date."
}

Write-Host ""
Write-Host "Public URLs:"
Write-Host "  Q1: https://bigfatblackwhale.github.io/DSH-Dota2/q1_value_zones_viewer.html"
Write-Host "  Q2: https://bigfatblackwhale.github.io/DSH-Dota2/q2_all_teams_viewer.html"
Write-Host "  Q3: https://bigfatblackwhale.github.io/DSH-Dota2/q3_rel_viewer.html"
