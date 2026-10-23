; AFIF Coder installer (Inno Setup 6)
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6E1F3B2A-AF1F-4C0D-9E2B-AF1F7EC0DE01}
AppName=AFIF Coder
AppVersion={#AppVersion}
AppVerName=AFIF Coder {#AppVersion}
AppPublisher=AFIF-TECH
AppComments=Local AI coding assistant. Runs entirely on this PC.
DefaultDirName={autopf}\AFIF-TECH\AFIF Coder
DefaultGroupName=AFIF-TECH
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
WizardImageFile=wizard.bmp,wizard@2x.bmp
WizardSmallImageFile=small.bmp,small@2x.bmp
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\AFIF-Coder.exe
UninstallDisplayName=AFIF Coder
OutputDir=..\dist
OutputBaseFilename=AFIF-Coder-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
LicenseFile=license.txt
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nAFIF Coder is a coding assistant with its own built-in AFIF Engine. Local models run entirely on this PC, need no account and have no usage limits. You can also connect Claude, Gemini or DeepSeek with your own API key.%n%nAfter installing, AFIF Coder will offer to download a local model (1-18 GB, once).

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\AFIF-Coder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\engine\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\AFIF Coder"; Filename: "{app}\AFIF-Coder.exe"; Comment: "Local AI coding assistant"
Name: "{autodesktop}\AFIF Coder"; Filename: "{app}\AFIF-Coder.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AFIF-Coder.exe"; Description: "Start AFIF Coder"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im llama-server.exe /t"; Flags: runhidden; RunOnceId: "StopEngine"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\AFIF-Coder');
    if DirExists(DataDir) and (not UninstallSilent) then
      if MsgBox('Also delete your downloaded models and settings?' + #13#10 + #13#10 +
         'Choose No to keep them, so a reinstall works without downloading again.' + #13#10 + DataDir,
         mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
