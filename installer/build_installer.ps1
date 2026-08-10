param(
    [string]$SourceLogo = "D:\Desktop\Logo.png",
    [string]$AppDistDir = "D:\Desktop\main.dist",
    [string]$OutputDir = "D:\Desktop",
    [string]$InnoCompiler = "D:\Inno Setup 7\ISCC.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$assetsDir = Join-Path $PSScriptRoot "assets"
$buildDir = Join-Path $PSScriptRoot "build"
$coreBuildDir = Join-Path $buildDir "core"
$frontendBuildDir = Join-Path $buildDir "frontend"
$licensePath = Join-Path $projectRoot "LICENSE.txt"
$issPath = Join-Path $PSScriptRoot "Prismatica.iss"
$coreSetupPath = Join-Path $coreBuildDir "PrismaticaCoreSetup.exe"
$iconPath = Join-Path $assetsDir "PrismaticaInstaller.ico"
$logoPath = Join-Path $assetsDir "installer_logo.png"
$bundledLicensePath = Join-Path $assetsDir "LICENSE.txt"
$frontendEntry = Join-Path $PSScriptRoot "frontend\main.py"
$finalSetupPath = Join-Path $OutputDir "PrismaticaSetup.exe"

foreach ($requiredPath in @($pythonPath, $SourceLogo, $AppDistDir, $licensePath, $InnoCompiler)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Missing build input: $requiredPath"
    }
}

New-Item -ItemType Directory -Force -Path $assetsDir, $coreBuildDir, $frontendBuildDir, $OutputDir | Out-Null

$assetArguments = @(
    (Join-Path $PSScriptRoot "tools\prepare_assets.py"),
    "--source-logo", $SourceLogo,
    "--assets-dir", $assetsDir,
    "--license", $licensePath
)
& $pythonPath @assetArguments
if ($LASTEXITCODE -ne 0) { throw "Failed to prepare installer assets" }

$innoArguments = @(
    "/DMyAppSourceDir=$AppDistDir",
    "/DMyCoreOutputDir=$coreBuildDir",
    $issPath
)
& $InnoCompiler @innoArguments
if ($LASTEXITCODE -ne 0) { throw "Failed to compile the Inno backend" }
if (-not (Test-Path -LiteralPath $coreSetupPath)) { throw "Inno backend output was not created" }

$nuitkaArguments = @(
    "-m", "nuitka",
    "--onefile",
    "--standalone",
    "--assume-yes-for-downloads",
    "--mingw64",
    "--enable-plugin=pyside6",
    "--windows-console-mode=disable",
    "--windows-icon-from-ico=$iconPath",
    "--nofollow-import-to=scipy",
    "--nofollow-import-to=qframelesswindow.webengine",
    "--nofollow-import-to=PySide6.QtWebEngineCore",
    "--nofollow-import-to=PySide6.QtWebEngineWidgets",
    "--include-data-files=$coreSetupPath=backend/PrismaticaCoreSetup.exe",
    "--include-data-files=$logoPath=assets/installer_logo.png",
    "--include-data-files=$bundledLicensePath=assets/LICENSE.txt",
    "--output-dir=$frontendBuildDir",
    "--output-filename=PrismaticaSetup.exe",
    "--company-name=Hexalumina Studio",
    "--product-name=Prismatica Installer",
    "--file-description=Prismatica Fluent Installer",
    "--file-version=1.0.0.0",
    "--product-version=1.0.0",
    "--copyright=Copyright (C) 2026 Hexalumina Studio",
    "--remove-output",
    $frontendEntry
)

& $pythonPath @nuitkaArguments
if ($LASTEXITCODE -ne 0) { throw "Failed to compile the Fluent installer frontend" }

$compiledSetupPath = Join-Path $frontendBuildDir "PrismaticaSetup.exe"
if (-not (Test-Path -LiteralPath $compiledSetupPath)) { throw "Fluent installer output was not created" }
Copy-Item -LiteralPath $compiledSetupPath -Destination $finalSetupPath -Force

Write-Host "Installer created: $finalSetupPath"
