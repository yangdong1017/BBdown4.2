; Inno Setup script for BBDown
; Generated for onedir PyInstaller output

#define MyAppName "BBDown"
#define MyAppVersion "4.2"
#define MyAppPublisher "BBDown"
#define MyAppExeName "BBDown.exe"

[Setup]
AppId={{D64422D8-381F-42FA-8F62-33A5630C4D9B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\BBDown
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/ultra64
SolidCompression=yes
OutputDir=installer_output
OutputBaseFilename=BBDown-{#MyAppVersion}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
WizardStyle=modern
DisableProgramGroupPage=auto
DisableDirPage=no
ShowLanguageDialog=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked

[Files]
Source: "dist\BBDown\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\BBDown\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\bbdown_runtime"
Type: filesandordirs; Name: "{app}\bbdown_tools"
Type: files; Name: "{app}\bbdown_gui_config.json"
Type: files; Name: "{app}\bbdown_license.json"
