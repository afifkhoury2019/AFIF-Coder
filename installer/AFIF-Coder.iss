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
Compression=lzma2/max
SolidCompression=yes
LicenseFile=license.txt
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nAFIF Coder is a coding assistant with its own built-in AFIF Engine. Local models run entirely on this PC, need no account and have no usage limits. A light model (Qwen3 1.7B) is built in, so it works straight away. You can also use free cloud models (Gemini, Groq, GitHub Models, Mistral, OpenRouter) with "Free Auto", or Claude and DeepSeek with your own API key.%n%nThe full installation adds everything needed to build and clone apps: Git, GitHub CLI, Node.js, REA with Java and JADX, rclone for Google Drive, and Python with Playwright.%n%nBigger local models (2-18 GB) can be downloaded later from Models.

[Types]
Name: "full"; Description: "Full: everything for building and cloning apps (recommended)"
Name: "compact"; Description: "AFIF Coder only (chat, models and skills)"
Name: "custom"; Description: "Choose components"; Flags: iscustom

[Components]
Name: "main"; Description: "AFIF Coder, AI engine and skills"; Types: full compact custom; Flags: fixed
Name: "lite"; Description: "Built-in light model: Qwen3 1.7B (1.1 GB), works right away with no download"; Types: full compact
Name: "dev"; Description: "Developer tools: Git, GitHub CLI, Node.js, Python with Word/Excel/PowerPoint/PDF libraries"; Types: full
Name: "dev\browser"; Description: "Chromium for UI screenshots (Playwright)"; Types: full
Name: "rea"; Description: "REA reverse-engineering toolkit (REA, Java JDK 21, JADX engine)"; Types: full
Name: "drive"; Description: "Google Drive connector (rclone)"; Types: full

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\AFIF-Coder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\engine\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion
Source: "..\skills\*"; DestDir: "{app}\skills"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\models\*.gguf"; DestDir: "{app}\models"; Components: lite; Flags: ignoreversion nocompression
Source: "..\tools\THIRD-PARTY.txt"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\tools\VERSIONS.json"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\tools\git\*"; DestDir: "{app}\tools\git"; Components: dev; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\tools\gh\*"; DestDir: "{app}\tools\gh"; Components: dev; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\tools\python\*"; DestDir: "{app}\tools\python"; Components: dev; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\tools\ms-playwright\*"; DestDir: "{app}\tools\ms-playwright"; Components: dev\browser; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\tools\node\*"; DestDir: "{app}\tools\node"; Components: dev or rea; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\tools\rea\*"; DestDir: "{app}\tools\rea"; Components: rea; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\tools\jdk\*"; DestDir: "{app}\tools\jdk"; Components: rea; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\tools\jadx\*"; DestDir: "{app}\tools\jadx"; Components: rea; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\tools\rclone\*"; DestDir: "{app}\tools\rclone"; Components: drive; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\AFIF Coder"; Filename: "{app}\AFIF-Coder.exe"; Comment: "Local AI coding assistant"
Name: "{autodesktop}\AFIF Coder"; Filename: "{app}\AFIF-Coder.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AFIF-Coder.exe"; Description: "Start AFIF Coder"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im llama-server.exe /t"; Flags: runhidden; RunOnceId: "StopEngine"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\skills"
Type: filesandordirs; Name: "{app}\models"

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
