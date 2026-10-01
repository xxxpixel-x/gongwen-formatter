; Inno Setup 安装包脚本：把 PyInstaller 生成的 dist\GongwenFormatter 打成一个 setup.exe
; 本地用法：iscc packaging\installer.iss    （版本号默认 0.0.0，CI 会用 /DAppVersion=x.y.z 传入）

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8F3B6C2E-5D1A-4E7B-9C0F-2A6D4B8E1F37}
AppName=公文格式整理器
AppVersion={#AppVersion}
AppPublisher=Rory Xiao
DefaultDirName={autopf}\GongwenFormatter
DefaultGroupName=公文格式整理器
; 不需要管理员权限，装在当前用户目录下
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=公文格式整理器-安装包-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Inno Setup 6 默认会跳过“欢迎页”，而且装过一次后会跳过“选择安装位置”页。这里强制都显示
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=yes
DisableReadyPage=no
UninstallDisplayIcon={app}\GongwenFormatter.exe
SetupIconFile=icon.ico

[Languages]
#if FileExists(AddBackslash(SourcePath) + "ChineseSimplified.isl")
Name: "zh"; MessagesFile: "ChineseSimplified.isl"
#else
Name: "en"; MessagesFile: "compiler:Default.isl"
#endif

[Files]
Source: "..\dist\GongwenFormatter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\公文格式整理器"; Filename: "{app}\GongwenFormatter.exe"
Name: "{group}\卸载 公文格式整理器"; Filename: "{uninstallexe}"
; 桌面快捷方式：安装时总是创建
Name: "{autodesktop}\公文格式整理器"; Filename: "{app}\GongwenFormatter.exe"

[Run]
Filename: "{app}\GongwenFormatter.exe"; Description: "{cm:LaunchProgram,公文格式整理器}"; Flags: nowait postinstall skipifsilent
