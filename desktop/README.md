# 打包成桌面客户端

依赖：Python 3.10+、`pip install -e ".[desktop]"`（含 pywebview 与 PyInstaller）。

```bash
python -m PyInstaller desktop/storystudio.spec --noconfirm
```

产物在 `dist/StoryStudio/`（Windows 约 78MB），双击 `StoryStudio.exe`（macOS/Linux 为同名可执行文件）：

* 启动器在本机随机空闲端口起服务，生成一次性访问令牌，打开一个原生窗口（pywebview）；
* **用户数据目录**（剧集 spec、output、账本）默认 `~/StoryStudio`，可用 `--project-root` 或环境变量 `STORYSTUDIO_PROJECT_ROOT` 改。打包版不写在 exe 旁边；
* 模型凭据在窗口里的「模型设置」页填写，保存在 `~/.storystudio/settings.json`；
* 日志在 `~/.storystudio/logs/storystudio.log`——打包版没有控制台，出问题先看这里；
* `ffmpeg` / `ffprobe`：把两个 exe 放进 `dist/StoryStudio/` 或它下面的 `ffmpeg/` 子目录，启动器会把这两个位置加进 PATH；也可以装到系统 PATH。缺失时界面顶部会提示并给下载链接（装配、后期层、台词识别都用它）。

无窗口模式（排障 / 无头）：

```bash
dist/StoryStudio/StoryStudio.exe --no-open --port 8756 --no-token
```

可选：台词识别闸（`storyvideo asr-qc`）需要 `pip install faster-whisper`，首次运行会下载模型；打包时默认排除（连同 ctranslate2 / numpy / onnxruntime，几百 MB）。桌面版**不能**靠"装到系统 Python + PYTHONPATH"：PyInstaller 的冻结解释器以 isolated 模式启动、忽略所有 `PYTHON*` 环境变量，exe 里也没有 pip。可行的路是设置页的「一键安装 faster-whisper」：借本机同一 major.minor、带 pip 的 Python（按 `py -3.11` → `python3.11` → `python3` → `python` 的顺序找，版本以打包用的解释器为准；设置页的手动命令写的就是找到的那一个）执行 `pip install --target ~/.storystudio/site-packages faster-whisper`，装完当场接进 `sys.path`，之后每次启动由启动器接上（`studio.desktop._load_extra_site`；目录可用环境变量 `STORYSTUDIO_EXTRA_SITE` 改）。手动装也是同一条命令，装完重启程序；用别的 Python 版本装的会被识别出来并在设置页说清楚。

## 安装包

**Windows**：`powershell -ExecutionPolicy Bypass -File desktop/build_windows.ps1`——前端构建 → PyInstaller → Inno Setup（装了 `choco install innosetup` 才会出 `dist/installer/StoryStudio-<版本>-setup.exe`，否则只有单文件夹）。脚本在 `desktop/installer.iss`：装到 Program Files，开始菜单和可选桌面快捷方式，卸载不碰用户目录。想随包带 ffmpeg，把 `ffmpeg.exe` / `ffprobe.exe` 放进 `desktop/ffmpeg/` 再打包（注意 LGPL 构建的许可证）。

**macOS**（必须在 Mac 上跑）：`bash desktop/build_macos.sh`——spec 在 darwin 下多出一个 `.app` BUNDLE，脚本再做 dmg。签名设 `CODESIGN_IDENTITY`，公证设 `NOTARY_PROFILE`（`xcrun notarytool store-credentials` 存的名字），都不设就出未签名包（Gatekeeper 会拦，用户要手动放行）。Apple Silicon 与 Intel 各打一次。

CI（`.github/workflows/ci.yml`）在打 `v*` tag 或手动触发时产出 Windows 包，先做无窗口冒烟，再做包内容自检，过了才打安装包、上传产物。

**包里只放运行要用的东西**：`skills/` 与 `examples/` 按 `desktop/bundle_manifest.py` 的白名单逐个文件带（加一个运行时要读的数据文件就在那里加一行），随包数据要求附带的第三方许可声明（`docs/third-party-notices.md`，清单里的 `NOTICE_FILES`）也逐个带、少了同样让自检失败，`docs/` 下别的文档不进包；前端静态目录只带构建产物 `app/` 与白名单里的顶层文件，测试框架与测试代码一律排除；插件只从用户目录加载，不进包。打完包 `build_windows.ps1` / `build_macos.sh` / CI 都会跑 `python desktop/verify_bundle.py <产物目录>`（Windows 再加 `--ffmpeg desktop/ffmpeg`，核对 Inno Setup 另外带的 ffmpeg 目录）：产物里出现测试、白名单外的数据文件、本机数据（`.env` / `settings.json` / `*.db` / 日志）、插件或打包机自己的绝对路径（仓库目录、主检出目录、可编辑安装目录、用户主目录、数据与插件目录）就让构建失败，不出安装包。私有敏感词不进仓库，需要时用环境变量 `BUNDLE_DENYLIST`（一行一个词，也认 `|` 分隔；CI 里是同名 secret，按一行一个词存）或仓库根目录下不入库的 `.release-denylist` 传进去；只查本项目自己的文件与模块（第三方库的字节里什么词都可能有），命中时只报「词表第几条」，不把词打进日志。PyInstaller 的中间产物（`build/` 里的 .toc / xref 记着本机绝对路径）由两个脚本放到系统临时目录，不留在仓库里。

## 版本与签名

版本号只在 `src/genkit/version.py` 改一处（pyproject 动态读取；`storystudio --version`、设置页、`/api/health` 都显示它）。分发前用各平台工具签名（Windows：signtool；macOS：codesign + notarytool），否则系统会拦截未签名应用。

## 为什么是 pywebview + PyInstaller，而不是 Electron / Tauri

见 [DESIGN.md §8.1](../DESIGN.md)。一句话：后端是 Python，换任何壳都还要打包 Python；界面三种壳下都是同一份 HTML；壳的差别只在安装包与自动更新，等需要内置自动更新时再评估 Tauri 壳。
