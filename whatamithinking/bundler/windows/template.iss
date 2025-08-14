; these two vars exist for use in the custom scripts. they are not really used
; by this template for anything else
#define bundler_appdata "{code:GetDataDir}\{{ installer_publisher }}\{{ installer_title }}"
#define bundler_app "{app}"

[Setup]
AppId={{ installer_title }}
; add lock to prevent multiple installers at the same time for the same program
{% raw %}SetupMutex=SetupMutex{#SetupSetting("AppId")}{% endraw %}
AppName={{ installer_title }}
AppVersion={{ installer_version }}
AppVerName={{ installer_title }} {{ installer_version }}
AppPublisher={{ installer_publisher }}
DefaultDirName={{ '{autopf64}' if installer_bitness == 64 else '{autopf32}' }}\{{ installer_publisher }}\{{ installer_title }}
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired={{ 'admin' if installer_elevated else 'lowest' }}
OutputDir={{ installer_distpath }}
OutputBaseFilename={{ installer_filename.stem }}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UsePreviousAppDir=no
RestartApplications=yes
CloseApplications=yes
UninstallDisplayIcon={uninstallexe}
{% if installer_icon_filepath -%}
; set custom icon for the installer exe itself, if givenl otherwise uses inno setup icon
SetupIconFile={{ installer_icon_filepath }}
{%- endif %}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]

[Files]
{%- for script in installer_scripts %}
Source: "{{ script.filepath }}"; DestDir: "{{ '{app}\\scripts\\' + script.name }}"; Flags: ignoreversion
{%- for supporting_filepath in script.supporting_filepaths %}
Source: "{{ supporting_filepath }}"; DestDir: "{{ '{app}\\scripts\\' + script.name }}"; Flags: ignoreversion
{%- endfor %}
{%- endfor %}
{%- for src, dst, recursive in installer_paths %}
Source: "{{ src / '*' if recursive and src.is_dir() else src }}"; DestDir: "{{ dst }}"; Flags: recursesubdirs ignoreversion
{%- endfor %}
; NOTE: Don't use "Flags: ignoreversion" on any shared system files. keep for all files or updates 
; dont seem to be made when app already installed

[Dirs]
; create folder where we will store all the app data. lock access to data if always run as admin
Name: "{code:GetDataDir}"; Permissions: "users-full";

[UninstallDelete]
; be sure to wipe out all the program files from the last install beforehand. data files are not affected
Type: filesandordirs; Name: "{app}"

[Icons]
{%- for shortcut in installer_shortcuts %}
{% if shortcut.appdir -%}
Name: "{{ shortcut.target.with_name(shortcut.title) }}"; Filename: "{{ shortcut.target }}"; Parameters: "{{ shortcut.params }}"; Comment: "{{ shortcut.description }}";
{%- endif %}
{% if shortcut.desktop -%}
Name: "{autodesktop}\{{ shortcut.title }}"; Filename: "{{ shortcut.target }}"; Parameters: "{{ shortcut.params }}"; Comment: "{{ shortcut.description }}";
{%- endif %}
{% if shortcut.startmenu -%}
Name: "{autoprograms}\{{ shortcut.title }}"; Filename: "{{ shortcut.target }}"; Parameters: "{{ shortcut.params }}"; Comment: "{{ shortcut.description }}";
{%- endif %}
{% if shortcut.startup -%}
Name: "{autostartup}\{{ shortcut.title }}"; Filename: "{{ shortcut.target }}"; Parameters: "{{ shortcut.params }}"; Comment: "{{ shortcut.description }}";
{%- endif %}
{%- endfor %}

[Run]
{#- post-install script hook #}
{%- for script in installer_scripts if script.condition == 'postinstall' %}
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\{{ script.name }}\{{ script.filepath.name }}"" {{ script.params }}"; WorkingDir: {app}; Description: "{{ script.description }}"; Flags: runhidden
{%- endfor %}

[UninstallRun]
{#- pre-uninstall script hook #}
{%- for script in installer_scripts if script.condition == 'preuninstall' %}
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\{{ script.name }}\{{ script.filepath.name }}"" {{ script.params }}"; WorkingDir: {app}; Flags: runhidden; RunOnceId: "{{ script.name }}"
{%- endfor %}

[Code]
var
  DataDirPage: TInputDirWizardPage;

// custom wizard page setup, for data dir.
// src: https://www.aronhelser.com/2010/09/inno-setup-custom-data-directory.html
procedure InitializeWizard;
begin
  DataDirPage := CreateInputDirPage(wpSelectDir,
    '{{ installer_title }} Data Directory', '',
    'Please select a location to store data.',
    False, '');
  DataDirPage.Add('');
  // set default
  DataDirPage.Values[0] := ExpandConstant('{autoappdata}\{{ installer_publisher }}\{{ installer_title }}');
end;

function GetDataDir(Param: String): String;
begin
  Result := DataDirPage.Values[0];
end;

// pre-install script hook
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  rc: Integer;
  success: Boolean;
begin
  {#- make sure powershell installed so we can run hook scripts #}
  {% if installer_scripts|length > 0 %}
  if not RegKeyExists(HKLM, 'SOFTWARE\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell') then
  begin
    Result := 'Powershell must be installed first.'
    Exit;
  end;
  {%- endif %}
  {#- have to extract script to temporary folder so we can run since this is before install actually runs #}
  {% for script in installer_scripts if script.condition == 'preinstall' %}
  ExtractTemporaryFiles('{{ "{app}\\scripts\\" + script.name + "\\*" }}')
  Log('Starting pre-install script, {{ script.name }} ({{ script.description }}), with arguments: {{ script.params }}');
  {#- this is correct. literal of app param should be used here for the temp path. #}
  success := Exec('powershell.exe', \
              ExpandConstant('-ExecutionPolicy Bypass -File "{tmp}\') + '{app}' + ExpandConstant('\scripts\{{ script.name }}\{{ script.filepath.name }}" {{ script.params }}'), \
              '', SW_HIDE, ewWaitUntilTerminated, rc)
  if (not success) or (rc <> 0) then
  begin
    Result := 'Pre-install operation, {{ script.description }}, failed with exit code, ' + inttostr(rc)
    Exit;
  end;
  Log('Completed pre-install script: {{ script.name }}');
  {% endfor %}
end;

// src: https://stackoverflow.com/questions/3304463/how-do-i-modify-the-path-environment-variable-when-running-an-inno-setup-install
procedure EnvAddPath(Path: string);
var
    Paths: string;
    RootKey: integer;
    EnvKey: string;
begin
    if IsAdmin() then
    begin
      RootKey := HKEY_LOCAL_MACHINE;
      EnvKey := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';
    end
    else
    begin 
      RootKey := HKEY_CURRENT_USER;
      EnvKey := 'Environment';
    end;
    if not RegQueryStringValue(RootKey, EnvKey, 'Path', Paths)
    then Paths := '';
    if Pos(';' + Uppercase(Path) + ';', ';' + Uppercase(Paths) + ';') > 0 then exit;
    Paths := Paths + ';'+ Path +';'
    if RegWriteStringValue(RootKey, EnvKey, 'Path', Paths)
    then Log(Format('The [%s] added to PATH: [%s]', [Path, Paths]))
    else Log(Format('Error while adding the [%s] to PATH: [%s]', [Path, Paths]));
end;

procedure EnvRemovePath(Path: string);
var
    Paths: string;
    P: Integer;
    RootKey: integer;
    EnvKey: string;
begin
    if IsAdmin() then
    begin
      RootKey := HKEY_LOCAL_MACHINE;
      EnvKey := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';
    end
    else
    begin 
      RootKey := HKEY_CURRENT_USER;
      EnvKey := 'Environment';
    end;
    if not RegQueryStringValue(RootKey, EnvKey, 'Path', Paths) then
        exit;
    // delete all entries for the path in case it was added multiple times by accident
    P := Pos(';' + Uppercase(Path) + ';', ';' + Uppercase(Paths) + ';');
    while P <> 0 do
    begin
      Delete(Paths, P - 1, Length(Path) + 1);
      P := Pos(';' + Uppercase(Path) + ';', ';' + Uppercase(Paths) + ';');
    end;
    if RegWriteStringValue(RootKey, EnvKey, 'Path', Paths)
    then Log(Format('The [%s] removed from PATH: [%s]', [Path, Paths]))
    else Log(Format('Error while removing the [%s] from PATH: [%s]', [Path, Paths]));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
    {% if installer_environment_paths %}
    if (CurStep = ssPostInstall) then
    begin
      {%- for ep in installer_environment_paths %}
      EnvAddPath(ExpandConstant('{{ ep }}'));
      {%- endfor %}
    end;
    {%- endif %}
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
    {% if installer_environment_paths %}
    if (CurUninstallStep = usPostUninstall) then
    begin
      {%- for ep in installer_environment_paths %}
      EnvRemovePath(ExpandConstant('{{ ep }}'));
      {%- endfor %}
    end;
    {%- endif %}
end;
