; Inno Setup script for the Clarity Windows installer.
; Built by packaging/build-windows.ps1 after PyInstaller has made dist\Clarity\ (one-folder build):
;   ISCC.exe /DAppVersion=3.0.0 packaging\Clarity.iss
; Output: dist\installer\Clarity-<version>-Setup.exe
;
; Installs per user by default (no administrator prompt, into %LOCALAPPDATA%\Programs\Clarity); the first page
; offers "install for all users" instead. Adds a Start menu entry, an optional desktop shortcut, an uninstaller
; in Settings > Apps, and "Open with > Clarity" for images and videos. It never takes over a file type: Windows
; only makes Clarity the default if the user chooses it.

#define AppName "Clarity"
#ifndef AppVersion
  ; used when the script is compiled from the Inno Setup window; keep in step with backend/__init__.py
  ; (build-windows.ps1 passes the version from there with /DAppVersion)
  #define AppVersion "3.0.0"
#endif
#define AppExe "Clarity.exe"
#define ProgId "Clarity.Evidence"

[Setup]
; AppId identifies the app to Windows for upgrades and uninstalling: never change it.
AppId={{38D71402-D0DE-4E25-8098-23DD5FC9BA67}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Harry Smallwood
AppPublisherURL=https://harrylsmallwood.com/clarity
AppSupportURL=https://github.com/Harry083/Forgery-Lens
AppComments=Forensic image and video enhancement, and checks for editing and AI generation. Works offline.
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist\installer
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
SetupIconFile=..\clarity.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoCompany=Harry Smallwood
VersionInfoDescription={#AppName} installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
#ifdef Sign
; build-windows.ps1 passes /DSign and /Ssigntool=... when a code-signing certificate is configured
SignTool=signtool
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "openwith"; Description: "Add Clarity to ""Open with"" for images and videos"; GroupDescription: "Explorer:"

[Files]
Source: "..\dist\Clarity\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; an upgrade replaces the bundled libraries wholesale, so files a newer build no longer ships don't linger
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Forensic image and video enhancement and authentication"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; The program as a file handler ("HKA" is HKCU for a per-user install, HKLM for all users)
Root: HKA; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: ""; ValueData: "Evidence opened in Clarity"; Flags: uninsdeletekey; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: openwith
; "Open with" entries for each type (added alongside whatever app is already the default, never replacing it)
#define OpenWith(Ext) \
  'Root: HKA; Subkey: "Software\Classes\' + Ext + '\OpenWithProgids"; ValueType: string; ValueName: "' + ProgId + '"; ValueData: ""; Flags: uninsdeletevalue; Tasks: openwith' + NewLine + \
  'Root: HKA; Subkey: "Software\Classes\Applications\' + AppExe + '\SupportedTypes"; ValueType: string; ValueName: "' + Ext + '"; ValueData: ""; Tasks: openwith'
#emit OpenWith(".jpg")
#emit OpenWith(".jpeg")
#emit OpenWith(".png")
#emit OpenWith(".tif")
#emit OpenWith(".tiff")
#emit OpenWith(".bmp")
#emit OpenWith(".webp")
#emit OpenWith(".mp4")
#emit OpenWith(".avi")
#emit OpenWith(".mov")
#emit OpenWith(".mkv")
#emit OpenWith(".m4v")
#emit OpenWith(".wmv")
#emit OpenWith(".mpg")
#emit OpenWith(".ts")

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
{ Clarity's window uses Microsoft Edge WebView2, which Windows 11 and up-to-date Windows 10 already have.
  If it's missing, say so and offer Microsoft's download page rather than failing on first launch. }
const
  WebView2Key = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Key32 = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function HasWebView2: Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, WebView2Key32, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, WebView2Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, WebView2Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if (CurStep = ssPostInstall) and not WizardSilent and not HasWebView2 then
    if MsgBox('Clarity needs the Microsoft Edge WebView2 Runtime, which isn''t installed on this PC.' + #13#10#13#10 +
              'Open Microsoft''s download page now? Choose "Evergreen Bootstrapper".', mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;
