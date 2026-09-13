; Inno Setup script — turns dist\GroundedOps\ into one installable .exe.
;   1. .venv\Scripts\pyinstaller.exe packaging\groundedops.spec --noconfirm
;   2. iscc packaging\installer.iss
; Output: packaging\out\GroundedOps-Setup-<version>.exe

#define AppName "GroundedOps"
#define AppVer  "16.3"
#define AppExe  "GroundedOps.exe"

[Setup]
AppName={#AppName}
AppVersion={#AppVer}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=out
OutputBaseFilename={#AppName}-Setup-{#AppVer}
Compression=lzma2/max
SolidCompression=yes
; Per-machine, because the LAN firewall rule and the optional service both
; need elevation anyway.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
WizardStyle=modern

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";        Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"
Name: "startup";     Description: "Run in the background when Windows starts (reachable from other machines on the network)"; Flags: unchecked
Name: "firewall";    Description: "Allow other machines on this network to reach it (opens TCP 8000)"; Flags: unchecked

[Registry]
; --headless: started at login there is nobody to show a browser to, and
; opening one on every boot would be an act of hostility.
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#AppName}"; \
  ValueData: """{app}\{#AppExe}"" --headless"; \
  Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""{#AppName} 8000"" dir=in action=allow protocol=TCP localport=8000"; \
  Flags: runhidden; Tasks: firewall
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "netsh"; \
  Parameters: "advfirewall firewall delete rule name=""{#AppName} 8000"""; \
  Flags: runhidden

[UninstallDelete]
; The data directory is NOT removed: it holds the indexed documents, the
; accounts and the curated answers. Uninstalling the program should not
; destroy the corpus someone spent weeks building.
Type: dirifempty; Name: "{app}"
