param([switch]$InstallDependencies, [switch]$Check, [string]$OutputDirectory, [ValidateSet('x64','arm64')][string]$Architecture='x64')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$desktop = Join-Path $PSScriptRoot 'desktop'
# The local default stays isolated; hosted CI runners already provide rustup
# and use their own temporary home, so avoid shadowing those installed shims.
if ($env:XTYF_USE_SYSTEM_RUST -ne '1') {
    $env:CARGO_HOME = Join-Path $projectRoot '.build-tools\cargo'
    $env:RUSTUP_HOME = Join-Path $projectRoot '.build-tools\rustup'
    $env:PATH = "$env:CARGO_HOME\bin;$env:PATH"
}
if (-not $env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR = Join-Path $projectRoot '.build-tools\tauri-target-windows' }
$env:npm_config_cache = Join-Path $projectRoot '.build-tools\npm-cache'
$env:CARGO_HTTP_MULTIPLEXING = 'true'
if ($Architecture -eq 'arm64' -and -not (Get-Command clang.exe -ErrorAction SilentlyContinue)) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    if (Test-Path $vswhere) {
        $clang = & $vswhere -latest -products '*' -find 'VC/Tools/Llvm/x64/bin/clang.exe' | Select-Object -First 1
        if ($clang) { $env:PATH = "$(Split-Path $clang -Parent);$env:PATH" }
    }
    if (-not (Get-Command clang.exe -ErrorAction SilentlyContinue)) {
        throw 'Windows ARM64 requires Visual Studio C++ Clang tools for ring; install Microsoft.VisualStudio.Component.VC.Llvm.Clang.'
    }
}
if (-not (Get-Command cargo.exe -ErrorAction SilentlyContinue)) { throw 'Rust is missing. Install the pinned Rust 1.99.1 toolchain.' }
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
$targetTriple = if ($Architecture -eq 'arm64') { 'aarch64-pc-windows-msvc' } else { 'x86_64-pc-windows-msvc' }
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
    & python.exe (Join-Path $PSScriptRoot 'build_icons.py')
    if ($LASTEXITCODE -ne 0) { throw 'Client icon generation failed' }
    & rustup.exe target add $targetTriple --toolchain 1.99.1
    if ($LASTEXITCODE -ne 0) { throw "Rust target $targetTriple is missing" }
    cargo.exe test --locked
    if ($LASTEXITCODE -ne 0) { throw 'Native client tests failed' }
    & .\node_modules\.bin\tauri.cmd build --target $targetTriple --bundles nsis -- --locked
    if ($LASTEXITCODE -ne 0) { throw 'Tauri Windows build failed' }
    $releaseDir = Join-Path $env:CARGO_TARGET_DIR "$targetTriple\release"
    $installers = @(Get-ChildItem (Join-Path $releaseDir 'bundle\nsis') -Filter "*_$($config.version)_*-setup.exe")
    if ($installers.Count -ne 1) { throw "Expected one Tauri $Architecture NSIS installer, found $($installers.Count)" }
    $target = Join-Path $OutputDirectory "XTYF-Manager-Setup-$($config.version)-$Architecture.exe"
    Copy-Item -LiteralPath $installers[0].FullName -Destination $target -Force
    $application = Join-Path $releaseDir 'xtyf-manager.exe'
    $reportName = if ($Architecture -eq 'x64') { 'windows-verification.json' } else { 'windows-verification-arm64.json' }
    $reportPath = Join-Path $OutputDirectory $reportName
    & python.exe (Join-Path $PSScriptRoot 'verify_windows.py') --installer $target --application $application --manifest '..\frontend\dist\asset-manifest.json' --version $config.version --architecture $Architecture --report $reportPath
    if ($LASTEXITCODE -ne 0) { throw 'NSIS payload verification failed' }
    $report = Get-Content $reportPath -Raw | ConvertFrom-Json
    $report | Add-Member -NotePropertyName configurationTestsPassed -NotePropertyValue $true
    $report | Add-Member -NotePropertyName codeSigned -NotePropertyValue $false
    $report | Add-Member -NotePropertyName webviewInstallMode -NotePropertyValue 'downloadBootstrapper'
    [IO.File]::WriteAllText($reportPath, ($report | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
    Write-Output "WINDOWS_BUILD_OK $($config.version) $Architecture"
} finally { Pop-Location }
