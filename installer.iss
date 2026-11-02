; Instalador de ModHub (Inno Setup 6). Lo compila construir.bat -> dist\ModHub-Setup.exe
#define AppVersion "0.5.0"

[Setup]
AppId={{8F1C3E2A-6B1D-4C8E-9A57-2D7B5E4F1A90}
AppName=ModHub
AppVersion={#AppVersion}
AppVerName=ModHub {#AppVersion}
AppPublisher=ModHub
DefaultDirName={localappdata}\Programs\ModHub
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=ModHub-Setup
SetupIconFile=modhub\ui\assets\modhub.ico
UninstallDisplayIcon={app}\ModHub.exe
UninstallDisplayName=ModHub — Gestor universal de mods
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\ModHub\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ModHub"; Filename: "{app}\ModHub.exe"
Name: "{autodesktop}\ModHub"; Filename: "{app}\ModHub.exe"; Tasks: desktopicon

[Registry]
; Enlaces modhub:// (los nxm:// de Nexus se activan desde Ajustes, para no quitárselos a Vortex sin preguntar)
Root: HKCU; Subkey: "Software\Classes\modhub"; ValueType: string; ValueName: ""; ValueData: "URL:ModHub Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\modhub"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\modhub\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\ModHub.exe"",0"
Root: HKCU; Subkey: "Software\Classes\modhub\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ModHub.exe"" ""%1"""

[Run]
Filename: "{app}\ModHub.exe"; Description: "{cm:LaunchProgram,ModHub}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
