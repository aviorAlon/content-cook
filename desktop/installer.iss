; Inno Setup 脚本：把 PyInstaller 的单文件夹 dist/StoryStudio 打成 setup.exe。
; 用法（先 python -m PyInstaller desktop/storystudio.spec）：
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" desktop\installer.iss
; 产物：dist/installer/StoryStudio-<版本>-setup.exe
; 版本号从 src/genkit/version.py 读（见 build_windows.ps1 传入 /DAppVersion=...）；直接跑 ISCC 时用下面的默认值。
; 产品名的唯一来源是 src/webui/branding.py 的 PRODUCT_NAME（DESIGN §38）：这里的默认值必须与它一字不差
; （tests/test_packaging_files.py 守着），也可用 /DAppName= 覆盖。不走命令行传中文：PowerShell 5.1 的控制台代码页会把它弄坏。

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef AppName
  #define AppName "Content Cook · 内容创作工作台"
#endif
#define AppExe "StoryStudio.exe"
#define AppPublisher "avioralon"

[Setup]
AppId={{8E1F3C4A-6B2D-4F0E-9C51-STORYSTUDIO01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\StoryStudio
DefaultGroupName={#AppName}
; 安装包本身的图标；开始菜单 / 桌面 / 卸载项的图标跟 exe（PyInstaller 用 desktop/storystudio.ico 嵌进去）
SetupIconFile=storystudio.ico
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist\installer
OutputBaseFilename=StoryStudio-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
; 用户数据不在安装目录：项目在 ~/StoryStudio，设置在 ~/.storystudio（见 desktop/README.md）

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "..\dist\StoryStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 可选：把 ffmpeg.exe / ffprobe.exe 放进 desktop\ffmpeg\ 再打包，程序会自动把 {app}\ffmpeg 加进 PATH
Source: "ffmpeg\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion recursesubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "现在启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只删程序目录里的缓存，不碰用户目录（项目、设置、密钥都在用户目录）
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
