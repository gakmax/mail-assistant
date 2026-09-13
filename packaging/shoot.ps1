#Requires -Version 5.1
<#
    Screenshots the 현황 page with headless Edge, so the visual check of a NiceGUI
    page does not need a person in front of the machine. Renders only our page —
    nothing else on the desktop is captured.

    .\packaging\shoot.ps1                      shoot the frozen dist\MailAssistantWeb
    .\packaging\shoot.ps1 -Source              shoot the source tree via .venv-spike
    .\packaging\shoot.ps1 -Path /mail          another page
    .\packaging\shoot.ps1 -Extra 'id=abc123'   extra query parameters
    .\packaging\shoot.ps1 -Width 1600          a wider viewport

    Reads the real %LOCALAPPDATA%\HiworksMailAssistant\mail.db, so the shot shows
    whatever mail is on this machine. Treat the png as mail content.
#>
[CmdletBinding()]
param([switch]$Source, [int]$Width = 1280, [int]$Height = 1000,
      [string]$Path = '/', [string]$Extra = '',
      [string]$Out = "$env:TEMP\spike-page.png",
      [string]$Edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root

if (-not (Test-Path $Edge)) { throw "Edge를 찾지 못했습니다: $Edge" }
$log = Join-Path $env:TEMP 'shoot-web.log'
Remove-Item $log, "$log.err", $Out -ErrorAction SilentlyContinue
$env:PYTHONUNBUFFERED = '1'

# -WorkingDirectory explicitly: Start-Process follows [Environment]::CurrentDirectory,
# which Set-Location does not change. PYTHONPATH because running the entry script
# directly puts packaging/ on sys.path instead of the repo root; the frozen build
# gets that from the spec's pathex.
$start = @{ PassThru = $true; WindowStyle = 'Minimized'; WorkingDirectory = $root
            RedirectStandardOutput = $log; RedirectStandardError = "$log.err" }
if ($Source) {
    $start.FilePath = (Resolve-Path '.venv-spike/Scripts/python.exe').Path
    $start.ArgumentList = (Join-Path $root 'packaging/entry_web.py')
    $env:PYTHONPATH = $root
} else {
    $start.FilePath = Join-Path (Resolve-Path 'dist/MailAssistantWeb').Path 'MailAssistantWeb.exe'
}

$process = Start-Process @start
try {
    $url = $null
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalSeconds -lt 40 -and -not $url) {
        Start-Sleep -Milliseconds 200
        if (Test-Path $log) {
            $hit = Select-String -Path $log -Pattern 'http://127\.0\.0\.1:\d+/\?t=\S+' |
                Select-Object -First 1
            if ($hit) { $url = $hit.Matches[0].Value }
        }
    }
    if (-not $url) {
        Write-Host '--- 로그 ---'
        Get-Content $log, "$log.err" -ErrorAction SilentlyContinue
        throw 'URL을 출력하지 못했습니다.'
    }
    # The server prints the root URL; -Path and -Extra aim it at one page and state.
    if ($Path -ne '/') { $url = $url -replace '^(http://[^/]+)/\?', ('$1' + $Path + '?') }
    if ($Extra) { $url = $url + '&' + $Extra }
    Write-Host "url: $url"
    # Edge reports success on stderr, which a native command turns into a terminating
    # error under ErrorActionPreference Stop.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Edge --headless=new --disable-gpu --no-first-run --hide-scrollbars `
        "--user-data-dir=$env:TEMP\spike-edge" "--window-size=$Width,$Height" `
        "--screenshot=$Out" $url 2>&1 | Out-Null
    $ErrorActionPreference = $previous
    Start-Sleep -Seconds 2
    if (-not (Test-Path $Out)) { throw 'png을 만들지 못했습니다.' }
    Write-Host ("png: {0}  {1:N0} bytes" -f $Out, (Get-Item $Out).Length)
} finally {
    if ($process -and -not $process.HasExited) { $process.Kill() }
}
