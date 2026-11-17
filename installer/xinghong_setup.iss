; 星虹安装包（Inno Setup 6）
; ---------------------------------------------------------------------------
; 版本号与显示名不在这里手写：
;   installer\_version.iss -> #define MyAppVersion "<ux_core.APP_VERSION>"（由 build_xinghong.py 生成）
;   installer\_name.iss    -> #define MyAppName "星虹智算"（UTF-8 带 BOM，#include）
; 为什么不用 ISPP 的 FileRead：ISPP 没有该函数（实测报 “Integer expression expected”），
; 因此版本和产品名都由构建脚本生成 #include 片段，源头仍是 ux_core.APP_VERSION / PRODUCT_NAME，
; 并与 version.txt、product_name.txt、version_info_xinghong.txt 三处一致（构建时断言）。
;
; 构建前：先跑 python build_xinghong.py --build，确保 dist\XingHongPanel 存在且版本一致。
; 中文向导依赖：Inno Setup 官方安装包不含 ChineseSimplified.isl，需自己放进
;   <Inno Setup 6 安装目录>\Languages\ChineseSimplified.isl
; 来源（官方翻译页，Chinese (Simplified) 由 Zhenghan Yang 维护，标注 6.5.0+）：
;   https://jrsoftware.org/files/istrans/
; 直链（issrc 仓库 Unofficial 目录，取到的版本要与本机 ISCC 版本一致）：
;   https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/Unofficial/ChineseSimplified.isl
; 未放时 ISCC 会因找不到 MessagesFile 直接失败（不会静默退英文）。
; ---------------------------------------------------------------------------
#include "_name.iss"
#include "_version.iss"
#define MyAppExeName "XingHongPanel.exe"
#define MySourceDir AddBackslash(SourcePath) + "..\dist\XingHongPanel"

[Setup]
AppId={{8E4A1C62-3B7D-4C1E-9F52-2D7B6A0C41E8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=X.H
AppCopyright=© 2026 X.H (MIT)
; 安装范围复核（R248）：纯当前用户安装，不弹 UAC。
;  · 数据全在 %LOCALAPPDATA%\XingHongPanel + HKCU\Software\X.H\XingHongPanel，
;    升权装到 Program Files 会让「程序在别的账户下、数据在本账户下」，卸载也删不干净；
;  · {localappdata}\Programs\XingHongPanel 是 VS Code / Teams 这类当前用户应用的惯例路径；
;  · 因此删掉 PrivilegesRequiredOverridesAllowed=dialog：留着等于允许升权，
;    而上例中的路径与数据位置都不支持全机安装。
DefaultDirName={localappdata}\Programs\XingHongPanel
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=out
OutputBaseFilename={#MyAppName}-{#MyAppVersion}-setup-win64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
SignedUninstaller=no

[Languages]
; 优先用 Inno Setup 安装目录下的官方翻译；没有权限往 Program Files 写时，
; 回退到本脚本同目录的 ChineseSimplified.isl（版本须与 ISCC 匹配，见文件头注释）。
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "zhcn"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#else
Name: "zhcn"; MessagesFile: "ChineseSimplified.isl"
#endif
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 同意记录是法律行为：卸载只清 legal/accepted 与 legal/accepted_at，重装后必须重新征得同意。
; 同一注册表键下的其他值（主题、显卡、生成参数等）与 %LOCALAPPDATA% 数据一律不动。
Filename: "{sys}\reg.exe"; Parameters: "delete ""HKCU\Software\X.H\XingHongPanel\legal"" /v accepted /f"; Flags: runhidden; RunOnceId: "XhClearConsent"
Filename: "{sys}\reg.exe"; Parameters: "delete ""HKCU\Software\X.H\XingHongPanel\legal"" /v accepted_at /f"; Flags: runhidden; RunOnceId: "XhClearConsentAt"

[UninstallDelete]
; 唯一的例外：运行期异常留痕 `_errs_live.log` 由程序写在 {app}\_internal 下（安装器不建），
; 不删就会留下一个非空的 _internal，卸载后 {app} 目录消失不了。只点名这一个文件，
; 其它运行产物（graceful 留痕、metrics、输出）都在 %LOCALAPPDATA%，本段不碰。
Type: files; Name: "{app}\_internal\_errs_live.log"

; ---------------------------------------------------------------------------
; 卸载保留清单（除上面那一行外不写 [UninstallDelete]，这些路径安装器从不创建、卸载器也不碰）：
;   %LOCALAPPDATA%\XingHongPanel     运行数据 metrics.jsonl / 运行日志 / 生成记录
;   你的输出目录                      作品与放大结果
;   HKCU\Software\X.H\XingHongPanel  配置（legal/accepted 与 legal/accepted_at 除外，见上）
; 只删除：{app} 程序目录（含上面点名的运行期日志）、开始菜单项、桌面快捷方式（若创建）、
; 卸载登记项、协议同意记录。
; ---------------------------------------------------------------------------

[Messages]
zhcn.WelcomeLabel2=即将把 [name] 安装到本机。%n%n未签名说明：本安装包未做代码签名，安装过程中可能出现「Windows 已保护你的电脑」或「未知发布者」提示；这是未签名的预期现象，不代表安装包损坏。%n%n卸载保留：卸载不会删除 AppData\Local\XingHongPanel（运行数据与生成记录）、你的输出目录，以及注册表 HKCU\Software\X.H\XingHongPanel 中除协议同意记录外的配置。
