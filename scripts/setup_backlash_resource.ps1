param(
    [string]$Tag = "backlash-resource-v1",
    [string]$Repository = "cyres03/oli-robot-manager"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$resourceDirectory = Join-Path $projectRoot "resources\backlash"
$targetPath = Join-Path $resourceDirectory "backlash_install.zip"
$expectedSha256 = "BACC27196221226AFE5339F3A47C9E492C565327DA0E454A7B223370E32A58EE"

function Test-BacklashResource {
    if (-not (Test-Path $targetPath)) {
        return $false
    }
    $actualSha256 = (Get-FileHash $targetPath -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($actualSha256 -ne $expectedSha256) {
        Write-Warning "Backlash resource checksum mismatch. Expected $expectedSha256, actual $actualSha256."
        return $false
    }
    return $true
}

if (Test-Path $targetPath) {
    if (Test-BacklashResource) {
        Write-Host "Backlash resource already exists and is verified: $targetPath"
        exit 0
    }
    Remove-Item $targetPath -Force
    Write-Warning "Invalid Backlash resource was removed; downloading a verified copy."
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

if (-not (Test-BacklashResource)) {
    Remove-Item $targetPath -Force -ErrorAction SilentlyContinue
    throw "Downloaded Backlash resource failed checksum verification."
}

Write-Host "Backlash resource downloaded and verified: $targetPath"