param(
    [switch]$Clean,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$version = (Get-Content (Join-Path $projectRoot 'VERSION') -Raw).Trim()
$distRoot = Join-Path $projectRoot 'dist\windows'
$buildRoot = Join-Path $projectRoot 'build\windows'
$releaseRoot = Join-Path $projectRoot 'release\windows'
$portableDir = Join-Path $distRoot 'OliRobotManager'
$portableZip = Join-Path $releaseRoot "OliRobotManager-Windows-x64-v$version.zip"

if ($Clean) {
    if (Test-Path $buildRoot) {
        Remove-Item $buildRoot -Recurse -Force
    }
    if (Test-Path $distRoot) {
        Remove-Item $distRoot -Recurse -Force
    }
    if (Test-Path $releaseRoot) {
        Remove-Item $releaseRoot -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null

python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $distRoot `
    --workpath $buildRoot `
    OliRobotManager.spec

if (Test-Path $portableZip) {
    Remove-Item $portableZip -Force
}
Compress-Archive -Path $portableDir -DestinationPath $portableZip -CompressionLevel Optimal
Write-Host "Windows portable release completed: $portableZip"

if ($SkipInstaller) {
    exit 0
}

$isccCandidates = @(
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
)

$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Warning "Inno Setup 6 was not found. Portable build is ready: $portableZip"
    Write-Warning 'Install Inno Setup 6 and rerun this script to generate an installer.'
    exit 0
}

& $iscc `
    "/DMyAppVersion=$version" `
    "/DMyAppOutputDir=$releaseRoot" `
    "/DMyAppOutputBaseFilename=OliRobotManager-Windows-x64-Setup-v$version" `
    (Join-Path $projectRoot 'installer\OliRobotManager.iss')
Write-Host "Windows installer completed: $releaseRoot\OliRobotManager-Windows-x64-Setup-v$version.exe"