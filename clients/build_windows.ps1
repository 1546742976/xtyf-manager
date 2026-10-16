param([switch]$InstallDependencies)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$env:ELECTRON_CACHE = Join-Path $projectRoot '.build-tools\electron-cache'
$env:ELECTRON_BUILDER_CACHE = Join-Path $projectRoot '.build-tools\electron-builder-cache'
$env:npm_config_cache = Join-Path $projectRoot '.build-tools\npm-cache'
$env:CSC_IDENTITY_AUTO_DISCOVERY = 'false'
Copy-Item (Join-Path $PSScriptRoot 'config.json') (Join-Path $PSScriptRoot 'windows\config.json') -Force
$config = Get-Content (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
Push-Location (Join-Path $PSScriptRoot 'windows')
try {
    if ($InstallDependencies) {
        npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    }
    npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw 'Client tests failed' }
    $extraArguments = @()
    $localElectron = Join-Path $projectRoot '.build-tools\electron-dist'
    if (Test-Path (Join-Path $localElectron 'electron.exe')) { $extraArguments += "-c.electronDist=$localElectron" }
    & .\node_modules\.bin\electron-builder.cmd --win nsis --x64 --publish never "-c.extraMetadata.version=$($config.version)" @extraArguments
    if ($LASTEXITCODE -ne 0) { throw 'Windows build failed' }
} finally { Pop-Location }
