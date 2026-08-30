; PFS 数据分析 Agent — PyInstaller onedir wrapper
; Prefer packaging/build_windows.ps1. The fallback path supports manual IDE
; compilation after the documented package-dist onedir build.

#define AppName "PFS Data Analysis Agent"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppPublisher "PFS"
#define AppURL "https://github.com/Lukanytsu7551/PFS-data-analysis-agent"
#define AppExeName "PFSDataAnalysisAgent.exe"
#ifndef OnedirSource
  #define OnedirSource "..\build\w\pyinstaller-dist\PFSDataAnalysisAgent"
#endif
#ifndef InstallerOutputDir
  #define InstallerOutputDir "..\build\w\installer"
#endif
#ifndef IconFilePath
  #define IconFilePath "icon.ico"
#endif

[Setup]
AppId={{8F3A2C1D-4B5E-4F6A-9D7C-2E8F1A3B5C9D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={localappdata}\Programs\PFSDataAnalysisAgent
DefaultGroupName={#AppName}
DisableDirPage=no
AllowNoIcons=yes
OutputDir={#InstallerOutputDir}
OutputBaseFilename=PFSDataAnalysisAgent-Windows-x64
SetupIconFile={#IconFilePath}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayIcon={app}\{#AppExeName}
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Exactly one source rule: the audited PyInstaller onedir produced by P3.
Source: "{#OnedirSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent
