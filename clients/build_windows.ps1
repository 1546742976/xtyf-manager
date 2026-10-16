param([switch]$InstallDependencies, [switch]$Check, [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$desktop = Join-Path $PSScriptRoot 'desktop'
$env:CARGO_HOME = Join-Path $projectRoot '.build-tools\cargo'
$env:RUSTUP_HOME = Join-Path $projectRoot '.build-tools\rustup'
$env:CARGO_TARGET_DIR = Join-Path $projectRoot '.build-tools\tauri-target-windows'
$env:PATH = "$env:CARGO_HOME\bin;$env:PATH"
$env:npm_config_cache = Join-Path $projectRoot '.build-tools\npm-cache'
$env:CARGO_HTTP_MULTIPLEXING = 'true'
if (-not (Test-Path "$env:CARGO_HOME\bin\cargo.exe")) { throw 'Rust is missing. Install the pinned toolchain in .build-tools/cargo and .build-tools/rustup.' }
& cargo.exe --version
if ($LASTEXITCODE -ne 0) { throw 'Rust toolchain is not ready' }
if ($Check) {
    if (-not (Test-Path "$desktop\node_modules\.bin\tauri.cmd")) { throw 'Run build_windows.ps1 -InstallDependencies first.' }
    & python.exe (Join-Path $PSScriptRoot 'verify_windows.py') --check
    if ($LASTEXITCODE -ne 0) { throw 'NSIS payload extractor is not ready' }
    Write-Output 'WINDOWS_TOOLS_OK'
    exit 0
}
$config = Get-Content (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'dist\clients\windows' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
Push-Location $desktop
try {
    if ($InstallDependencies) {
        npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Tauri CLI installation failed' }
        & python.exe (Join-Path $PSScriptRoot 'verify_windows.py') --setup
        if ($LASTEXITCODE -ne 0) { throw 'NSIS payload extractor setup failed' }
    }
    if (-not (Test-Path '..\frontend\dist\asset-manifest.json')) { throw 'Run clients/build_frontend.py before building the desktop app.' }
    cargo.exe test --locked
    if ($LASTEXITCODE -ne 0) { throw 'Native client tests failed' }
    & .\node_modules\.bin\tauri.cmd build --bundles nsis -- --locked
    if ($LASTEXITCODE -ne 0) { throw 'Tauri Windows build failed' }
    $installer = Get-ChildItem "$env:CARGO_TARGET_DIR\release\bundle\nsis\*_$($config.version)_x64-setup.exe" | Select-Object -First 1
    if (-not $installer) { throw 'Expected Tauri NSIS installer missing' }
    $target = Join-Path $OutputDirectory "XTYF-Manager-Setup-$($config.version)-x64.exe"
    Copy-Item -LiteralPath $installer.FullName -Destination $target -Force
    $application = Join-Path $env:CARGO_TARGET_DIR 'release\xtyf-manager.exe'
    $reportPath = Join-Path $OutputDirectory 'windows-verification.json'
    & python.exe (Join-Path $PSScriptRoot 'verify_windows.py') --installer $target --application $application --manifest '..\frontend\dist\asset-manifest.json' --version $config.version --report $reportPath
    if ($LASTEXITCODE -ne 0) { throw 'NSIS payload verification failed' }
    $report = Get-Content $reportPath -Raw | ConvertFrom-Json
    $report | Add-Member -NotePropertyName configurationTestsPassed -NotePropertyValue $true
    $report | Add-Member -NotePropertyName codeSigned -NotePropertyValue $false
    $report | Add-Member -NotePropertyName webviewInstallMode -NotePropertyValue 'downloadBootstrapper'
    [IO.File]::WriteAllText($reportPath, ($report | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
    Write-Output "WINDOWS_BUILD_OK $($config.version)"
} finally { Pop-Location }
