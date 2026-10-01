; Build only through scripts/build_windows_installer.ps1. The bundle must have
; passed provenance and secret checks before it reaches this recipe.
; AppId is permanent: future versions update the same per-user installation.
#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef WindowsVersion
  #error WindowsVersion is required
#endif
#ifndef InstallerOutputDir
  #error InstallerOutputDir is required
#endif
#ifndef WebViewBootstrap
  #error WebViewBootstrap is required (Microsoft-signed Evergreen bootstrapper)
#endif

[Setup]
AppId={{3F02308E-BE0C-4D04-B24A-D5DC45D00D3E}
AppName=NexPoint ERP
AppVersion={#AppVersion}
AppVerName=NexPoint ERP {#AppVersion}
AppPublisher=NexPoint Studio
DefaultDirName={localappdata}\Programs\NexPoint ERP
DefaultGroupName=NexPoint ERP
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#InstallerOutputDir}
OutputBaseFilename=NexPointERP-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupLogging=no
VersionInfoVersion={#WindowsVersion}
VersionInfoProductVersion={#WindowsVersion}
VersionInfoTextVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoDescription=Instalador do NexPoint ERP
UninstallDisplayName=NexPoint ERP
UninstallDisplayIcon={app}\NexPointERP.exe
UninstallFilesDir={app}\uninstall
CreateUninstallRegKey=yes
Uninstallable=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
CloseApplications=yes
RestartApplications=no
AllowNoIcons=no
#ifdef AppIcon
SetupIconFile={#AppIcon}
#endif

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"; Flags: unchecked

[Files]
Source: "{#WebViewBootstrap}"; DestName: "MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\NexPoint ERP"; Filename: "{app}\NexPointERP.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\NexPoint ERP"; Filename: "{app}\NexPointERP.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\NexPointERP.exe"; Description: "Abrir NexPoint ERP"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

; Never add an UninstallDelete entry for user data, credentials, browser profiles,
; backups or %LOCALAPPDATA%\NexPoint\ERP. Inno removes only installed program files.
[Code]
function WebViewAvailable: Boolean;
var
  Version: String;
  Key: String;
begin
  Key := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := (RegQueryStringValue(HKLM32, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'))
    or (RegQueryStringValue(HKCU, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function PathsOverlap(const FirstPath, SecondPath: String): Boolean;
var
  First, Second: String;
begin
  First := Lowercase(AddBackslash(ExpandFileName(FirstPath)));
  Second := Lowercase(AddBackslash(ExpandFileName(SecondPath)));
  Result := (Pos(First, Second) = 1) or (Pos(Second, First) = 1);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
begin
  Result := '';
  { Also enforce the boundary when a support operator supplies /DIR. }
  if PathsOverlap(ExpandConstant('{app}'), ExpandConstant('{localappdata}\NexPoint\ERP')) then
    Result := 'Escolha uma pasta de instalação diferente da pasta de dados do NexPoint ERP.';
  if Result <> '' then Exit;
  if not WebViewAvailable then begin
    WizardForm.StatusLabel.Caption := 'Preparando o componente de exibição. Mantenha a internet conectada...';
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then begin
      Result := 'Não foi possível preparar o componente de exibição. Confira a internet e tente novamente.';
      Exit;
    end;
    if not WebViewAvailable then
      Result := 'O componente de exibição ainda não está disponível. Confira a internet ou contate a NexPoint.';
  end;
end;
