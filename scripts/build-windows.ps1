[CmdletBinding()]
param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".venv")) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3.12 -m venv .venv
    } else {
        python -m venv .venv
    }
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller

$mode = if ($OneFile) { "onefile" } else { "portable" }
$arguments = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--noupx",
    "--name", "BeyondPack",
    "--collect-all", "msal",
    "--add-data", "src\beyondpack\resources\sample-products.json;beyondpack\resources",
    "--paths", "src",
    "--distpath", "dist\$mode",
    "--workpath", "build\$mode"
)
if ($OneFile) {
    $arguments += "--onefile"
}
$arguments += "beyondpack_entry.py"

& .\.venv\Scripts\pyinstaller.exe @arguments

if ($OneFile) {
    $outputPath = "dist\onefile\BeyondPack.exe"
} else {
    $outputPath = "dist\portable\BeyondPack\BeyondPack.exe"
}
if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
    throw "Build output was not created: $outputPath"
}
Write-Host "Build complete: $outputPath"
