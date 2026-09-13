#Requires -Version 5.1
<#
    Local reproduction of the CI build. Same steps, same order, so a broken
    release can be debugged without pushing a tag.

    .\packaging\build.ps1                 build exe + installer
    .\packaging\build.ps1 -SkipInstaller  exe only
#>
[CmdletBinding()]
param([switch]$SkipInstaller, [switch]$SkipTests)

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

$match = Select-String -Path 'mail_assistant/__init__.py' -Pattern '^__version__\s*=\s*"([^"]+)"'
if (-not $match) { throw '__version__을 찾지 못했습니다.' }
$version = $match.Matches[0].Groups[1].Value
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "__version__ 형식 오류: '$version'" }
Write-Host "메일 도우미 $version" -ForegroundColor Cyan

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt -r requirements-build.txt

if (-not $SkipTests) {
    python -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) { throw '테스트 실패' }
}

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
pyinstaller --noconfirm --clean --log-level=INFO packaging/mail_assistant.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 실패' }

# The one check that proves the frozen bundle kept its lazy imports and the
# jsonschema data files. Cheap here, expensive on the user's PC.
& 'dist\MailAssistant\MailAssistantTools.exe' selftest
if ($LASTEXITCODE -ne 0) { throw 'selftest 실패' }

$stamped = (Get-Item 'dist\MailAssistant\MailAssistant.exe').VersionInfo.FileVersion
if (-not $stamped.StartsWith($version)) { throw "버전 리소스 불일치: $stamped" }

if ($SkipInstaller) { Write-Host 'exe 완료 (설치 프로그램 생략)' -ForegroundColor Green; exit 0 }

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { throw "Inno Setup을 찾지 못했습니다: $iscc" }
& $iscc "/DAppVersion=$version" 'packaging\installer.iss'
if ($LASTEXITCODE -ne 0) { throw "ISCC 실패: $LASTEXITCODE" }

python packaging/make_manifest.py --version $version --repo gakmax/mail-assistant
Write-Host "완료: dist\installer\MailAssistant-Setup-$version.exe" -ForegroundColor Green
