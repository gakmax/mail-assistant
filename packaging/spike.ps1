#Requires -Version 5.1
<#
    Measures what NiceGUI costs on Windows, which is the 1단계 go/no-go of UI-PLAN.md.
    The shipped build is untouched: this freezes packaging/spike_web.spec only.

    .\packaging\spike.ps1                  freeze, measure size, time a cold start
    .\packaging\spike.ps1 -Venv            install into .venv-spike, not the system python
    .\packaging\spike.ps1 -Native          also try the pywebview window
    .\packaging\spike.ps1 -SkipBuild       measure an existing dist\MailAssistantWeb
    .\packaging\spike.ps1 -Python C:\...\python.exe   when `python` is the Store stub
    -NoPause                                skip the "look at the page" prompt

    Fill the numbers it prints into the 실측 결과 table in UI-PLAN.md.
    Two things only a person can answer, so watch for them:
      * does Windows raise a firewall prompt (it must not: loopback only)
      * does the native window open and render, or does only the browser work
#>
[CmdletBinding()]
param([switch]$Native, [switch]$SkipBuild, [switch]$Venv, [switch]$NoPause,
      [string]$Python = 'python')

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

# A venv keeps a throwaway spike out of the system site-packages. Either way the
# interpreter is called by path and PyInstaller as a module, so nothing depends on
# what `pyinstaller` happens to resolve to on PATH.
$py = $Python
if ($Venv) {
    if (-not (Test-Path '.venv-spike')) { & $Python -m venv .venv-spike }
    $py = (Resolve-Path '.venv-spike/Scripts/python.exe').Path
}
Write-Host "python: $py"
& $py -V

function Show-Size($label, $path) {
    if (-not (Test-Path $path)) { Write-Host ("{0,-28} 없음" -f $label); return }
    $bytes = (Get-ChildItem -Recurse -File $path | Measure-Object -Sum Length).Sum
    Write-Host ("{0,-28} {1,8:N1} MB" -f $label, ($bytes / 1MB))
}

if (-not $SkipBuild) {
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet -r requirements.txt -r requirements-build.txt
    & $py -m pip install --quiet -r packaging/requirements-spike.txt
    Remove-Item -Recurse -Force build, dist/MailAssistantWeb -ErrorAction SilentlyContinue
    $watch = [Diagnostics.Stopwatch]::StartNew()
    & $py -m PyInstaller --noconfirm --clean --log-level=WARN packaging/spike_web.spec
    if ($LASTEXITCODE -ne 0) { throw '프리즈 실패' }
    $watch.Stop()
    Write-Host ("프리즈 시간                   {0,8:N1} 초" -f $watch.Elapsed.TotalSeconds)
}

$root = 'dist/MailAssistantWeb'
Write-Host ''
Write-Host '=== 번들 크기 ===' -ForegroundColor Cyan
Show-Size 'MailAssistantWeb 전체' $root
Show-Size '  nicegui' "$root/_internal/nicegui"
Show-Size '  nicegui/static' "$root/_internal/nicegui/static"
Show-Size '  nicegui/elements' "$root/_internal/nicegui/elements"
Show-Size '비교: 현재 출하본' 'dist/MailAssistant'

Write-Host ''
Write-Host '=== 첫 실행 ===' -ForegroundColor Cyan
$exe = Join-Path (Resolve-Path $root) 'MailAssistantWeb.exe'
$log = Join-Path $env:TEMP 'spike-web.log'
Remove-Item $log, "$log.err" -ErrorAction SilentlyContinue
# Belt and braces with the flush=True in webui.serve(): a redirected stdout is
# block-buffered, and this script reads the URL back out of the log.
$env:PYTHONUNBUFFERED = '1'
# Splatted: -ArgumentList rejects an empty string, so the switch has to add the key.
$start = @{ FilePath = $exe; PassThru = $true; WindowStyle = 'Minimized';
            RedirectStandardOutput = $log; RedirectStandardError = "$log.err" }
if ($Native) { $start.ArgumentList = '--native' }
$watch = [Diagnostics.Stopwatch]::StartNew()
$process = Start-Process @start
try {
    $url = $null
    while ($watch.Elapsed.TotalSeconds -lt 60 -and -not $url) {
        Start-Sleep -Milliseconds 200
        if (Test-Path $log) {
            $line = Select-String -Path $log -Pattern 'http://127\.0\.0\.1:\d+/\?t=\S+' |
                Select-Object -First 1
            if ($line) { $url = $line.Matches[0].Value }
        }
    }
    if (-not $url) {
        Write-Host '--- 로그 ---'; Get-Content $log, "$log.err" -ErrorAction SilentlyContinue
        throw "URL을 출력하지 못했습니다. $log 확인"
    }
    Write-Host ("URL 출력까지                  {0,8:N1} 초" -f $watch.Elapsed.TotalSeconds)

    $ready = [Diagnostics.Stopwatch]::StartNew()
    $code = 0
    while ($ready.Elapsed.TotalSeconds -lt 60 -and $code -ne 200) {
        try { $code = (Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5).StatusCode }
        catch { Start-Sleep -Milliseconds 200 }
    }
    Write-Host ("첫 200 응답까지               {0,8:N1} 초" -f $ready.Elapsed.TotalSeconds)
    Write-Host "주소: $url"

    Write-Host ''
    Write-Host '=== 바인딩 (loopback만이어야 함) ===' -ForegroundColor Cyan
    $port = ([uri]$url).Port
    netstat -ano | Select-String ":$port\s" | ForEach-Object { Write-Host "  $_" }

    if (-not $NoPause) {
        Write-Host ''
        Write-Host '브라우저로 위 주소를 열어 화면을 확인하세요. 확인이 끝나면 Enter.' -ForegroundColor Yellow
        Read-Host | Out-Null
    }
} finally {
    if ($process -and -not $process.HasExited) { $process.Kill() }
}
