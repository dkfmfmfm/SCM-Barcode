#ifndef MyAppVersion
  #define MyAppVersion "2.2.2"
#endif

#define MyAppName "BeyondPack"
#define MyAppPublisher "BEYOND EARTH Co.,Ltd."
#define MyAppExeName "BeyondPack.exe"

[Setup]
AppId={{5F70DDA1-DB53-47B2-A51E-5E7F1BC1DB80}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\BeyondPack
DefaultGroupName=BeyondPack
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=BeyondPack-{#MyAppVersion}-Windows-x64-Setup
SetupIconFile=..\build-resources\BeyondPack.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=BeyondPack barcode packing workstation installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 BeyondPack 아이콘 만들기"; GroupDescription: "바로가기:"; Flags: checkedonce

[Files]
Source: "..\dist\portable\BeyondPack\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\USER_MANUAL.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\OPERATOR_GUIDE.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\ADMIN_GUIDE.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\GOOGLE_SHEETS_SETUP.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\templates\BeyondPack_Master_Template.csv"; DestDir: "{app}\templates"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\BeyondPack"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\BeyondPack"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "BeyondPack 실행"; Flags: nowait postinstall skipifsilent
