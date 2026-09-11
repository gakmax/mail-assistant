; 메일 도우미 installer. Per-user, never elevated.
;
; This file MUST stay UTF-8 with a BOM: Inno Setup 6 reads a .iss as the system
; ANSI codepage otherwise, and every Korean string here becomes mojibake --
; including the Start Menu and shortcut names.

#define AppId       "{1D930FAA-D82D-4F43-A3E5-7987089B1E4C}"
#define AppNameKo   "메일 도우미"
#define AppNameEn   "Hiworks Mail Assistant"
#define DirName     "HiworksMailAssistant"
#define ExeName     "MailAssistant.exe"
#define ToolsExe    "MailAssistantTools.exe"
#define RepoUrl     "https://github.com/gakmax/mail-assistant"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{#AppId}
AppName={#AppNameKo}
AppVersion={#AppVersion}
AppVerName={#AppNameKo} {#AppVersion}
VersionInfoVersion={#AppVersion}
AppPublisher={#AppNameEn}
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases

; Codex is resolved with shutil.which() from the *user's* PATH, and npm's global
; prefix lives in the user profile. An elevated install would get the
; administrator's PATH and report "Codex CLI가 없습니다" with no obvious cause,
; so the /ALLUSERS escape hatch is removed rather than merely defaulted away.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=

DefaultDirName={autopf}\{#DirName}
DefaultGroupName={#AppNameKo}
UsePreviousAppDir=yes
UsePreviousTasks=yes
DisableDirPage=yes
DisableProgramGroupPage=yes

; Same name the app creates in __main__.py, so an update waits for it to exit.
; Under /SILENT /SUPPRESSMSGBOXES a collision aborts with exit code 2, which is
; why the updater closes the app and releases the mutex before starting Setup.
AppMutex=Local\HiworksMailAssistant
SetupMutex={#DirName}Setup
CloseApplications=no

MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir=..\dist\installer
OutputBaseFilename=MailAssistant-Setup-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppNameKo}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "추가 작업:"
Name: "autostart"; Description: "Windows 로그인 시 자동으로 시작"; GroupDescription: "추가 작업:"; Flags: unchecked

[Files]
Source: "..\dist\MailAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppNameKo}"; Filename: "{app}\{#ExeName}"
Name: "{group}\{#AppNameKo} (진단)"; Filename: "{cmd}"; \
    Parameters: "/k """"{app}\{#ToolsExe}"" help"""; WorkingDir: "{app}"; \
    IconFilename: "{app}\{#ToolsExe}"
Name: "{group}\{#AppNameKo} 제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppNameKo}"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppNameKo}"; Filename: "{app}\{#ExeName}"; \
    Parameters: "--autostart"; Tasks: autostart

[Registry]
Root: HKCU; Subkey: "Software\{#DirName}"; ValueType: string; ValueName: "InstallDir"; \
    ValueData: "{app}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\{#DirName}"; ValueType: string; ValueName: "Version"; \
    ValueData: "{#AppVersion}"

[Run]
; postinstall entries are presented on the Finished page, which never appears
; under /SILENT -- so this one cannot fire during an update, with or without
; skipifsilent.
Filename: "{app}\{#ExeName}"; Description: "{cm:LaunchProgram,{#AppNameKo}}"; \
    Flags: nowait postinstall skipifsilent

; A plain entry does run silently, so the update relaunch is gated on the
; /relaunch= value the app passes instead.
Filename: "{app}\{#ExeName}"; Flags: nowait; Check: RelaunchPlain
Filename: "{app}\{#ExeName}"; Parameters: "--autostart"; Flags: nowait; Check: RelaunchAuto

[UninstallDelete]
; {app} only. User data is deliberately untouched -- see CurUninstallStepChanged.
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Code]
function RelaunchMode(): String;
begin
  Result := ExpandConstant('{param:relaunch|}');
end;

function RelaunchPlain(): Boolean;
begin
  Result := RelaunchMode = '1';
end;

function RelaunchAuto(): Boolean;
begin
  Result := RelaunchMode = 'autostart';
end;

procedure CurUninstallStepChanged(CurStep: TUninstallStep);
begin
  if (CurStep = usPostUninstall) and (not UninstallSilent) then
    MsgBox('설정·메일 기록·엑셀 파일은 지우지 않았습니다.'#13#10#13#10
         + '완전히 삭제하려면 다음을 직접 지우세요:'#13#10
         + '  · %LOCALAPPDATA%\HiworksMailAssistant 폴더'#13#10
         + '    (config.json, mail.db, status.json)'#13#10
         + '  · 자격 증명 관리자 > Windows 자격 증명 >'#13#10
         + '    HiworksMailAssistant/<메일주소>'#13#10
         + '  · 바탕화면의 메일 업무관리.xlsx',
         mbInformation, MB_OK);
end;
