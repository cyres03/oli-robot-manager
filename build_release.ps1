param(
    [switch]$Clean,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if ($Clean) {
    if (Test-Path 'build') {
        Remove-Item 'build' -Recurse -Force
    }
    if (Test-Path 'dist') {
        Remove-Item 'dist' -Recurse -Force
    }
}

python -m PyInstaller --noconfirm OliRobotManager.spec

if ($SkipInstaller) {
    Write-Host 'Portable build completed: dist/OliRobotManager'
    exit 0
}

$isccCandidates = @(
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
)

$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Warning 'Inno Setup 6 was not found. Portable build is ready under dist/OliRobotManager.'
    Write-Warning 'Install Inno Setup 6 and rerun this script to generate an installer.'
    exit 0
}

& $iscc (Join-Path $projectRoot 'installer\OliRobotManager.iss')
Write-Host 'Installer build completed: installer\Output'