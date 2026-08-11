$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".venv")) {
    py -3.12 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
& .\.venv\Scripts\pyinstaller.exe `
    --noconfirm `
    --clean `
    --windowed `
    --name BeyondPack `
    --collect-all msal `
    --add-data "src\beyondpack\resources\sample-products.json;beyondpack\resources" `
    --paths src `
    beyondpack_entry.py

Write-Host "Build complete: dist\BeyondPack\BeyondPack.exe"
