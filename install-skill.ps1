# Install the csdn-publish skill into the WorkBuddy user-level skills folder,
# and write this tool's real path into the installed SKILL.md.
param(
    [string]$Dest = (Join-Path $env:USERPROFILE ".workbuddy\skills")
)

$ErrorActionPreference = "Stop"

$toolDir = $PSScriptRoot
if (-not $toolDir) { $toolDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$src = Join-Path $toolDir "skills\csdn-publish"

if (-not (Test-Path $src)) {
    Write-Host "[X] Skill source folder not found: $src"
    exit 1
}

$target = Join-Path $Dest "csdn-publish"
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -Path (Join-Path $src "*") -Destination $target -Recurse -Force

$skill = Join-Path $target "SKILL.md"
$text = [System.IO.File]::ReadAllText($skill, [System.Text.Encoding]::UTF8)
$text = $text.Replace("__TOOL_DIR__", $toolDir)
[System.IO.File]::WriteAllText($skill, $text, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "[OK] Skill installed to : $target"
Write-Host "[OK] Tool path written into SKILL.md : $toolDir"
Write-Host ""
Write-Host "If WorkBuddy does not pick it up, copy the folder"
Write-Host "  skills\csdn-publish"
Write-Host "into whatever skills directory your agent actually uses."
