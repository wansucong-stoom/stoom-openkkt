param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$environment = Join-Path $repository '.venv'
$environmentPython = Join-Path $environment 'Scripts/python.exe'
$localConfig = Join-Path $repository 'config.local.json'
& $Python -c "import struct,sys; sys.exit(0 if sys.platform=='win32' and struct.calcsize('P')==8 and sys.version_info >= (3,11) else 1)"
if ($LASTEXITCODE -ne 0) { throw '64비트 Windows용 Python 3.11 이상이 필요합니다.' }
if (-not (Test-Path -LiteralPath $environmentPython)) {
    & $Python -m venv $environment
    if ($LASTEXITCODE -ne 0) { throw '가상환경 생성에 실패했습니다.' }
}
& $environmentPython -m pip install -e ($repository + '[mcp]')
if ($LASTEXITCODE -ne 0) { throw '의존성 설치에 실패했습니다.' }
if (-not (Test-Path -LiteralPath $localConfig)) {
    Copy-Item -LiteralPath (Join-Path $repository 'examples/config.example.json') -Destination $localConfig
}
Write-Host '로컬 설치가 완료되었습니다. 설정 화면에서 계정 폴더와 범주를 선택하세요.'
Write-Host '실행: ./.venv/Scripts/openkkt.exe gui (또는 Open-Settings.cmd를 더블 클릭하세요)'
Write-Host '대화 본문을 읽지 않았으며 MCP 호스트 설정도 변경하지 않았습니다.'
