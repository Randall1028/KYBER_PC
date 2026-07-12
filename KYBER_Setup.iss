; ============================================================================
; KYBER_Setup.iss  --  Inno Setup script for the KYBER installer.
;
; Produces a single KYBER_Setup_<version>.exe that installs the app, lets the
; user choose the location, drops Start-Menu (and optional desktop) shortcuts,
; and registers a proper uninstaller in Add/Remove Programs.
;
; Build:
;   Open this file in the Inno Setup Compiler and click Build > Compile
;   (or run:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" KYBER_Setup.iss )
;   Output lands in .\installer_output\
;
; What it does NOT include: the ~5 GB runtime + models. Those are downloaded on
; first run into %LocalAppData%\KYBER (see config.py / provisioning.py), which
; keeps this installer tiny and lets the app be installed anywhere -- even a
; read-only spot like Program Files -- without write-permission problems.
; ============================================================================

#define MyAppName "KYBER"
; Version comes from the single-source VERSION file (same one the app reads),
; so the installer and the in-app footer can never drift. Bump VERSION only.
#define MyAppVersion Trim(FileRead(FileOpen("VERSION")))
#define MyAppPublisher "KYBER Project"
#define MyAppExeName "KYBER.exe"
#define MyAppDescription "Kinetic Yammering and Behavioral Engine Routines"

[Setup]
; AppId uniquely identifies KYBER for upgrades/uninstall -- keep it constant
; across versions (regenerate only if you ever fork into a separate product).
AppId={{B7E4C2A1-9D3F-4E62-A5C8-1F0D6B2E4A77}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments={#MyAppDescription}
; Default install location: per-user Programs by default (no admin prompt), but
; the user can pick "all users" -> Program Files, or Browse anywhere. The app
; keeps its data in AppData regardless, so any location works.
DefaultDirName={autopf}\KYBER
DefaultGroupName=KYBER
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=KYBER_Setup_{#MyAppVersion}
SetupIconFile=kyber.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; The entire one-folder PyInstaller output (KYBER.exe + _internal + bundled
; assets). recursesubdirs pulls in _internal; ignoreversion so our own DLLs
; always overwrite on reinstall/upgrade.
Source: "dist\KYBER\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\KYBER"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall KYBER"; Filename: "{uninstallexe}"
Name: "{autodesktop}\KYBER"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch KYBER"; Flags: nowait postinstall skipifsilent

; ----------------------------------------------------------------------------
; WebView2 (pywebview's renderer): built into Windows 11 and most updated
; Windows 10, so the alpha assumes it's present. If a tester ever gets a blank
; window, it's missing -- bundle Microsoft's Evergreen bootstrapper
; (MicrosoftEdgeWebview2Setup.exe) next to this script and enable the line
; below to install it silently. Left off for now to keep the installer small.
;
; [Files]
; Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
; [Run]
; Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; Flags: waituntilterminated
; ----------------------------------------------------------------------------

[UninstallDelete]
; The app's data (runtime, models, settings) lives in %LocalAppData%\KYBER and
; is deliberately LEFT IN PLACE on uninstall, so a reinstall doesn't re-download
; ~5 GB and keeps the user's tuned personalities/sound profiles. To make
; uninstall wipe it too, uncomment the next line.
; Type: filesandordirs; Name: "{localappdata}\KYBER"
