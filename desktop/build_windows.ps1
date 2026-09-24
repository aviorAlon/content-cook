# Windows 一键打包：前端构建（如有 npm）→ PyInstaller → 包内容自检 → Inno Setup（如已安装）。
# 用法：powershell -ExecutionPolicy Bypass -File desktop/build_windows.ps1
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$version = (python -c "import sys; sys.path.insert(0,'src'); from genkit.version import __version__; print(__version__)").Trim()
Write-Host "== StoryStudio $version"

if (Get-Command npm -ErrorAction SilentlyContinue) {
  Write-Host "== frontend build"
  Push-Location frontend
  if (-not (Test-Path node_modules)) { npm ci --no-audit --no-fund }
  npm run build
  Pop-Location
} else {
  Write-Host "npm 不在 PATH，跳过前端构建（用仓库里已提交的 src/webui/static/app）"
}

Write-Host "== PyInstaller"
# 中间产物（build/，里面的 .toc / xref 记着本机的绝对路径）放到临时目录，不留在仓库里
$work = Join-Path ([System.IO.Path]::GetTempPath()) "storystudio-pyi-build"
python -m PyInstaller desktop/storystudio.spec --noconfirm --workpath $work
if (-not (Test-Path "dist/StoryStudio/StoryStudio.exe")) { throw "PyInstaller 没有产出 dist/StoryStudio/StoryStudio.exe" }

# 包里不许有测试、白名单外的数据文件、本机数据、插件和打包机自己的路径（见 desktop/verify_bundle.py 文件头）；
# Inno Setup 另外带的 desktop/ffmpeg/（可选）一起查
Write-Host "== 包内容自检"
python desktop/verify_bundle.py dist/StoryStudio --ffmpeg desktop/ffmpeg
if ($LASTEXITCODE -ne 0) { throw "包内容自检没过：按上面的清单修掉再打包（安装包没有生成）" }

$iscc = @("C:\Program Files (x86)\Inno Setup 6\ISCC.exe", "C:\Program Files\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
  Write-Host "== Inno Setup"
  & $iscc "/DAppVersion=$version" "desktop\installer.iss"
  Get-ChildItem dist/installer/*.exe | ForEach-Object { Write-Host "安装包：$($_.FullName)  $([math]::Round($_.Length/1MB,1)) MB" }
} else {
  Write-Host "没找到 Inno Setup（choco install innosetup），只产出了单文件夹 dist/StoryStudio"
}
