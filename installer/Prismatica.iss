; Prismatica 安装事务核心
; 由 Fluent 前端以 /VERYSILENT 模式调用，也保留直接运行时的原生回退界面。

#ifndef MyAppName
#define MyAppName "Prismatica"
#endif
#ifndef MyAppVersion
#define MyAppVersion "1.0.0"
#endif
#define MyAppVersionInfo "1.0.0.0"
#define MyAppPublisher "Hexalumina Studio"
#define MyAppURL "https://www.example.com/"
#define MyAppExeName "6DCorpusClient.exe"
#ifndef MyAppSourceDir
#define MyAppSourceDir "D:\Desktop\main.dist"
#endif
#ifndef MyCoreOutputDir
#define MyCoreOutputDir "installer\build\core"
#endif
#define MyAppAssocName MyAppName + " File"
#define MyAppAssocExt ".prf"
#define MyAppAssocKey StringChange(MyAppAssocName, " ", "") + MyAppAssocExt

[Setup]
AppId={{A0C63163-CB6A-41FC-97BC-DAB20D4DB9A4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright=Copyright (C) 2026 {#MyAppPublisher}
AppComments=中文语料分析桌面应用

SourceDir=..
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
DisableWelcomePage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
PrivilegesRequired=admin
ChangesAssociations=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
CloseApplications=yes
RestartApplications=no

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

LicenseFile=LICENSE.txt
SetupIconFile=installer\assets\PrismaticaInstaller.ico
UninstallIconFile=installer\assets\PrismaticaInstaller.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

OutputDir={#MyCoreOutputDir}
OutputBaseFilename=PrismaticaCoreSetup
Compression=lzma2/max
SolidCompression=yes
SetupLogging=yes

; 当核心被单独运行时，仍提供可用的现代明暗主题回退界面。
WizardStyle=modern dynamic zircon hidebevels includetitlebar
WizardSizePercent=115
WizardResizable=no
WizardSmallImageFile=installer\assets\installer_logo.png
WizardSmallImageFileDynamicDark=installer\assets\installer_logo.png
WizardSmallImageBackColor=#F6F8FA
WizardSmallImageBackColorDynamicDark=#202428
WizardBackColor=#F6F8FA
WizardBackColorDynamicDark=#202428

VersionInfoVersion={#MyAppVersionInfo}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装核心
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}
VersionInfoOriginalFileName=PrismaticaCoreSetup.exe

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[LangOptions]
DialogFontName=Microsoft YaHei UI
DialogFontSize=9
WelcomeFontName=Microsoft YaHei UI
WelcomeFontSize=16

[CustomMessages]
chinesesimplified.DesktopIconTask=在桌面创建快捷方式
chinesesimplified.FileAssociationTask=关联 Prismatica 项目文件（.prf）
chinesesimplified.AdditionalTasks=系统集成

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIconTask}"; GroupDescription: "{cm:AdditionalTasks}"; Flags: unchecked
Name: "fileassoc"; Description: "{cm:FileAssociationTask}"; GroupDescription: "{cm:AdditionalTasks}"; Flags: checkedonce

[Files]
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocExt}\OpenWithProgids"; ValueType: string; ValueName: "{#MyAppAssocKey}"; ValueData: ""; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}"; ValueType: string; ValueName: ""; ValueData: "{#MyAppAssocName}"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "启动 {#MyAppName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "启动 {#MyAppName}"; Tasks: desktopicon

[Code]
var
  LastProgressPercent: Integer;

function ProgressFilePath: String;
begin
  Result := ExpandConstant('{param:progressfile|}');
end;

procedure WriteProgressState(Percent: Integer; StatusText: String);
var
  TargetPath: String;
begin
  TargetPath := ProgressFilePath;
  if TargetPath = '' then
    Exit;
  SaveStringToFile(TargetPath, IntToStr(Percent) + '|' + StatusText, False);
end;

procedure InitializeWizard;
begin
  LastProgressPercent := -1;
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
var
  Percent: Integer;
  StatusText: String;
begin
  if MaxProgress <= 0 then
    Exit;
  Percent := (CurProgress * 100) div MaxProgress;
  if Percent = LastProgressPercent then
    Exit;
  LastProgressPercent := Percent;
  if Percent < 8 then
    StatusText := 'preparing-files'
  else if Percent < 94 then
    StatusText := 'installing'
  else
    StatusText := 'finishing';
  WriteProgressState(Percent, StatusText);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    WriteProgressState(1, 'preparing')
  else if CurStep = ssPostInstall then
    WriteProgressState(100, 'completed');
end;
