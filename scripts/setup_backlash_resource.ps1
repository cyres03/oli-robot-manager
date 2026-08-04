param(
    [string]$Tag = "backlash-resource-v1",
    [string]$Repository = "cyres03/oli-robot-manager"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$resourceDirectory = Join-Path $projectRoot "resources\backlash"
$targetPath = Join-Path $resourceDirectory "backlash_install.zip"

if (Test-Path $targetPath) {
    Write-Host "Backlash resource already exists: $targetPath"
    exit 0
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is required. Install it and sign in before running this script."
}

New-Item -ItemType Directory -Path $resourceDirectory -Force | Out-Null
Push-Location $projectRoot
try {
    gh release download $Tag `
        --repo $Repository `
        --pattern "backlash_install.zip" `
        --dir $resourceDirectory `
        --clobber
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to download Backlash resource from release $Tag."
    }
} finally {
    Pop-Location
}

Write-Host "Backlash resource downloaded: $targetPath"