param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$environment = Join-Path $repository '.venv'
$environmentPython = Join-Path $environment 'Scripts/python.exe'
$localConfig = Join-Path $repository 'config.local.json'
& $Python -c "import struct,sys; sys.exit(0 if sys.platform=='win32' and struct.calcsize('P')==8 and sys.version_info >= (3,11) else 1)"
if ($LASTEXITCODE -ne 0) { throw '64-bit Windows Python 3.11 or newer is required.' }
if (-not (Test-Path -LiteralPath $environmentPython)) {
    & $Python -m venv $environment
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
}
& $environmentPython -m pip install -e ($repository + '[mcp]')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not (Test-Path -LiteralPath $localConfig)) {
    Copy-Item -LiteralPath (Join-Path $repository 'examples/config.example.json') -Destination $localConfig
}
Write-Host 'Installed locally. Open the settings GUI to choose your profile and categories.'
Write-Host 'Run: ./.venv/Scripts/openkkt.exe gui (or double-click Open-Settings.cmd)'
Write-Host 'No conversation was read and no MCP host configuration was changed.'
