# 发布前安全自查（2026-09-23，对外发布前）

要给外部用户用之前，按三路做了一次安全面自查：**本机服务与打包**、**文件与路径边界**、**子进程与 ffmpeg 参数**。
三路各自在沙盒里复现（临时数据根、临时 `STORYSTUDIO_HOME` / 设置文件、「目录外」目标全是沙盒里的假文件；没调任何付费模型，
没碰 `~/.storystudio` 和真实数据根），共确认 **19 条**：blocker 1、major 9、minor 9。**19 条全部已修**，每条带回归测试；
另外顺手修了自查里记下的一个小 bug（422 校验报错遇到非 JSON 请求体变成 500）。

| 严重度 | 编号 | 面 | 问题 | 状态 |
|---|---|---|---|---|
| blocker | SEC-1 | 服务 | 默认（带令牌）启动必定失败：启动器探活不带令牌，把自己的 401 当成「端口被别的程序占着」退出 | 已修 |
| major | SEC-2 | 服务 | 不校验 Host / Origin：`--no-token` 下任意网页可经 DNS rebinding 或跨站简单 POST 操纵服务，能把已存的密钥发到外面 | 已修 |
| major | PB-1 | 文件 | 删除短剧项目不校验 id：URL 里带反斜杠能把数据目录外的文件和目录搬走 | 已修 |
| major | PB-2 | 文件 | 删除 / 恢复信任项目文件里的 output_root：数据根外（或共享目录）整个被搬进归档，恢复时又写回外面 | 已修 |
| major | PB-3 | 文件 | 角色锚图 / 变体锚图路径不设边界：绝对路径、`..`、`~`、盘符相对都能解析到数据根外，出首帧时读出来发给生图服务 | 已修 |
| major | F1 | 进程 | 插卡 id 直接当文件名：合成时 PNG 能写到数据根之外、覆盖同名文件 | 已修 |
| major | F2 | 进程 | 字幕 / 朗读字幕 / 画面文字里的 `%` 让整条字幕消失、`\` 被吞、`%{…}` 被当展开指令，合成却报成功 | 已修 |
| major | F3 | 进程 | 画面文字带单引号（it's / don't）时引号提前闭合，后半句被当成滤镜图语法，合成失败 | 已修 |
| major | F4 | 进程 | 数据根或字体路径带单引号（`C:\Users\O'Brien\…`）时，所有带字幕的合成都失败 | 已修 |
| major | F5 | 进程 | 播客混音把每句的 `-i` 和滤镜都放在命令行上，Windows 上约 150 句以上超过 32767 字符，整期混不出来 | 已修 |
| minor | SEC-3 | 服务 | 只改 base_url（角色或网关）时旧密钥保留，下一次请求就发到新地址 | 已修 |
| minor | SEC-4 | 服务 | 令牌 cookie 不按端口隔离，同机别的端口的页面能带着 cookie 发写请求；令牌比较不是常量时间 | 已修 |
| minor | SEC-5 | 服务 | `/docs`、`/redoc`、`/openapi.json` 不在令牌门后面 | 已修 |
| minor | SEC-6 | 服务 | 日志脱敏只遮顶层异常，任务记录完全不脱敏；正则漏掉 `?key=`、dict/JSON 形态和方舟 UUID 密钥 | 已修 |
| minor | PB-4 | 文件 | `video_params.reference_audio` 只校验后缀：数据根外任意音频出片时会上传给视频服务 | 已修 |
| minor | PB-5 | 文件 | 后期配乐 `post_graphics.music[].file`：存草稿接口绕过校验，数据根外的音频被混进成片 | 已修 |
| minor | PB-6 | 文件 | 角色 / 变体 id 不校验：带反斜杠时，上传锚图会把文件写到数据根外 | 已修 |
| minor | PB-8 | 文件 | 404 文案带出本机绝对路径（数据根位置、用户名） | 已修 |
| minor | F6 | 进程 | 插卡 id / 数据根路径含 `%d` 时，ffmpeg 的 image2 按序号模板找文件，合成失败、报错晦涩 | 已修 |

---

## 一、覆盖范围

### 1. 本机服务与打包（临时端口起服务，打包版 `dist/StoryStudio` 也实跑）

查过、没有问题的：

- **监听地址**：uvicorn 写死 127.0.0.1；`_free_port` / `port_in_use` 只探 127.0.0.1。
- **令牌门本身**：缺令牌、错误的头、错误的 cookie 都是 401；路径变形（`//api`、`/%61pi`、`/api/%2e%2e/`、`/API`、
  `/static/../api`、尾斜杠、`%00`）都绕不过。
- **CORS**：没有 CORSMiddleware，跨源读被浏览器挡住（缺口在写请求和 Host，见 SEC-2 / SEC-4）。
- **静态资源**：`/`、`/app`、`/static` 只有打包进来的前端代码，没有挂用户数据目录。
- **密钥打码**：`/api/settings` 的 GET / PUT 都打码；health、roles_status 不含密钥；`/api/tasks` 默认不回 traceback。
- **日志**：uvicorn 用 warning 级别、不记访问日志，`?token=` 不进日志；缺口在异常链和任务记录（SEC-6）。
- **交付包**：导出 zip 里没有绝对路径、没有设置、没有密钥；imageset / podcast 的导出按白名单拷贝。
- **打包目录**：954 个文件里没有 `.env`、`.map`、settings、日志、测试文件；内嵌 PYZ 5227 个模块逐个 grep，没有本机路径
  或用户名。

没覆盖：各模块取文件接口的目录穿越（归到「文件与路径」那一路）、macOS 构建、真实 pywebview 窗口里的 cookie 行为（按
pywebview 默认 private_mode 推断）、各家真实上游认证失败时是否回显完整密钥（没调付费接口，所以 SEC-6 定为纵深防御缺口）。

### 2. 文件与路径边界（临时端口，`--no-token`，数据根与「目录外」都在沙盒里）

查过、按预期拒绝的：

- **下载 / 预览接口**：短剧首帧 / 视频 / 成片、单条视频、配乐、后期配乐、导出包下载，逐个试 `..%5C`、`..%2F`、`C:\`、
  UNC、`%0A`、`%00`、300 字符的名字，全部 404、没有泄露；绘本、图片集、播客、对比、海报、音色对比按代码核对，是同一套
  「只收文件名 + 符号链接检查」。
- **上传**：锚图、变体锚图、单条视频首帧、配乐参考图、绘本素材、播客音乐、后期配乐、音色参考音频，文件名都过 slugify
  或摘要、按真实格式嗅探，上传的文件名逃不出去（锚图上传的 stem 来自角色 / 变体 id，那一处漏了，见 PB-6）。
- **归档与恢复**：恢复接口的条目名有正则；从示例建项目拦了名字穿越。
- **zip**：只有打包、没有解包；拷进导出包的文件都过 `_inside(output_root)` 加符号链接检查。
- **字体路径**：`fonts.ui` 指向文本文件时报错只带路径和「坏文件」，不回文件内容。

没覆盖：指向外面的符号链接 / 目录联接只按代码核对（Windows 建符号链接要额外权限；本轮的回归测试在建得了链接的机器上会真跑）；
PB-6 最后一步「真写到外面」按代码路径确认，没实际执行；PB-3 的 `~` 绕过只在短剧锚图上真跑过，单条视频 / 配乐 / 绘本 /
图片集共用同一个解析函数，按代码确认；出首帧 / 出片那一步的真实上传没跑（会花钱）。

### 3. 子进程与 ffmpeg 参数（ffmpeg 6.0、Windows 10、Python 3.11）

- **子进程调用**：全仓没有 `shell=True`、`os.system`、`os.popen`，全部列表传参；逐个看过 assembly / post_layers / 播客
  混音 / 媒体探测 / 首帧代理 / contact sheet / 尾帧 / 海报 / 启动器的 netstat 与 tasklist / pip 探测 / icacls。
- **滤镜里拼了用户内容的地方**：drawtext 的 text / textfile（F2 / F3 / F4）、fontfile 路径（F4）有问题；concat 清单、
  角标和插卡文字（走 Pillow 画 PNG）、颜色和数值都正常；镜号、页号、环节 / 主播 id 有正则约束，插卡 id 没有（F1 / F6）。
- **实跑**：后期合成 11 组输入、装配版 1 次、绘本 1 次、播客 20 句和 300 句各一次，TTS 全用本地正弦波替身。
- **edge-tts / faster-whisper**：正文做了 XML 转义、声线参数有库自带校验；识别只传已校验镜号拼出的路径，没有注入面。

没覆盖：minimax / OpenAI 兼容 / ComfyUI / SD WebUI 这些 HTTP 适配器、前端、macOS / Linux 与 ffmpeg 7.x（drawtext 的展开
语义与 6.0 相同，没实测）。

---

## 二、每条发现与修法

### SEC-1（blocker）默认启动必定失败

- **根因**：`desktop.main` 先生成令牌放进环境变量，再用不带令牌的 `urlopen` 探 `/api/health`；自己的服务回 401，
  `_probe_identity` 把一切 HTTPError 当 `"foreign"`，启动器以退出码 1 结束。打包版没有控制台，用户什么也看不到。
  所有冒烟测试都带 `--no-token`，令牌门从没被真正走过。
- **修法**（`src/studio/desktop.py`）：`_probe_identity(base_url, token)` / `_wait_ready(url, timeout, token)` 带上
  `X-StoryStudio-Token` 头；`main` 把自己的令牌交给探活。`/api/health` 仍在令牌门后面（它会回数据根、设置路径）。
  `--no-token` 时往 stderr 和日志醒目地提示一句。CI 的打包冒烟加一步：不带 `--no-token` 起 exe，确认进程还活着、
  不带令牌打 `/api/health` 是 401。
- **测试**：`tests/test_security_local_service.py` 里真起一个带令牌门的 uvicorn，带令牌探活是 `ok`、不带是 `foreign`；
  `main` 把令牌交给探活；`--no-token` 有提示。另用默认方式在临时端口上真起服务，启动器正常打印地址、没有退出。

### SEC-2（major）不校验 Host / Origin

- **根因**：`local_token_guard` 是入口唯一的守卫，令牌为空时直接放行；不查 Host（uvicorn 接受任何 Host，DNS rebinding
  之后网页对整个接口同源读写），写请求不查 Origin / Sec-Fetch-Site（无 body 的 POST 不触发预检）。叠加「只改地址保留旧
  密钥」（SEC-3）和「测试连接用已存的密钥打当前地址」，能把密钥发到外面。
- **修法**（`src/webui/app.py`）：令牌开没开都先查来路——
  - Host 的主机名不是 `127.0.0.1` / `localhost` / `::1`（开发态另认 TestClient 的 `testserver`，打包版不认）→ 403，
    覆盖 `/`、`/app`、`/static`、`/api`：rebinding 的页面连界面都加载不了；
  - `/api/*`（和接口文档）请求带 `Sec-Fetch-Site: cross-site / same-site` → 403；带了 Origin 但不对 → 403。Origin 分两档：
    开着令牌（打包版默认）要跟本服务的源逐字相同（不认 Fetch Metadata 的老浏览器上，127.0.0.1 别的端口的页面带着 cookie
    只能靠这一条挡，见 SEC-4）；没开令牌（开发态）只要求是回环地址的 http(s) 源（`null`、扩展页、`file://`、借名域名照样 403）。
  没带这两个头的（curl、启动器、同源 GET）照常放行。开发态分两档是因为 vite 开发代理：`vite.config.js` 里的字符串写法
  `proxy: {'/api': 'http://127.0.0.1:8765'}` 会被 vite 展开成 `changeOrigin: true`，**只改 Host、不改 Origin**——后端看到的是
  `Host: 127.0.0.1:8765`、`Origin: http://localhost:5173`，要求逐字相同的话开发态所有写请求都 403（审查时实测发现，初版文档
  写成了「vite 不改 Host」，不对）。
- **测试**：rebinding Host、`127.0.0.1.evil.com`、跨站 POST（带 / 不带 Sec-Fetch-Site、`Origin: null`）都是 403 且设置文件
  没变；同源、vite 代理（Host 是后端、Origin 是 `localhost:5173`）、`[::1]`、地址栏直开都是 200；没开令牌时非回环 Origin
  （含 `null`、`chrome-extension://`、`file://`、`localhost.evil.example`）403；开着令牌时 vite 那种形状、别的端口的 Origin 都 403。
  真起服务实测同样结果，英文界面下 403 / 401 文案有英文；另用 vite 8.2.2（同样的字符串写法代理）真起开发服务器转发到临时端口上的
  后端，浏览器形状的同源 POST 200、跨站 POST 403。

### SEC-3（minor）只改地址时旧密钥跟过去

- **根因**：`update_settings` 只在适配器变了时清密钥；表单总是回传打码值（= 保留），所以同一个 OpenAI 兼容适配器把
  地址改到别的主机，旧密钥写盘、下一次请求以 Bearer 头发到新主机。网关分支完全不看地址变化。
- **修法**（`src/genkit/settings.py`）：密钥跟着「适配器 + 地址的来源（scheme、主机、端口）」走。按**生效地址**比（空地址
  补适配器默认值），来源变了且这次没带新明文密钥 → 清空；只改路径（加减 `/v1`、尾斜杠、主机大小写）、留空退回的默认地址
  仍是原主机 → 保留。网关同一条规则。设置页（`Settings.vue`）保存后发现密钥被清，提示「换了地址，旧密钥不会发到新地址——
  请重新填写密钥」（网关卡片同样）。
- **取舍**：自己挂反向代理、换了代理地址又想沿用同一把 key 的人要重新粘贴一次，提示里说清楚了。
- **测试**：本进程里起一个「对方服务器」，打码值 + 新地址保存后点「测试连接」，对方收不到旧密钥、盘上密钥为空；同源改动
  保留；换主机 / 端口 / 协议清掉，同时带新密钥的写入新密钥；默认地址算原主机；网关换地址清掉。前端 vitest 覆盖两处提示。

### SEC-4（minor）cookie 不按端口隔离、令牌比较不是常量时间

- **根因**：cookie 名固定是 `storystudio_token`，cookie 不分端口——同机两个实例互相覆盖；`SameSite=Strict` 只挡跨站，
  127.0.0.1 上别的端口的页面对浏览器是同站，照样带 cookie；中间件不看 Sec-Fetch-Site / Origin。令牌用 `==` 比。
- **修法**（`src/webui/app.py`）：cookie 名带服务真正在听的端口（取 socket，不信 Host）：`storystudio_token_<端口>`；
  same-site / 别的源的 `/api` 请求按 SEC-2 的来路检查拦下；令牌用 `secrets.compare_digest` 按字节比（非 ASCII 的乱填令牌
  是 401 不是 500）。老名字的 cookie 不再认——启动器每次都带 `?token=` 打开，会种上新 cookie。
- **测试**：cookie 名带端口、两个端口名字不同；带 cookie 的 same-site / 别的端口 Origin 请求 403；非 ASCII 令牌 401。

### SEC-5（minor）接口文档不在令牌门后面

- **修法**（`src/webui/app.py`）：`/openapi.json`、`/docs`、`/redoc`、`/docs/oauth2-redirect` 跟 `/api` 一样过令牌门和来路
  检查；打包版（`sys._MEIPASS`）直接不挂这几个路由（也省得 swagger 页从 CDN 拉脚本进本机源）。开发态 `--no-token` 不变。
- **测试**：带令牌门时四个路径都是 401，拿到 cookie 后 200；不设令牌时 200。

### SEC-6（minor）日志与任务记录脱敏

- **根因**：`MaskingFilter` 只改最外层异常的 `args[0]`，适配器 `raise … from exc` 包的上游原话在 `__cause__` 里原样进堆栈；
  而且它改的是调用方还在用的异常对象。任务记录的 error / traceback 直接落盘、不脱敏（失败文案还叫用户「把这条任务记录
  发给开发者」）。密钥正则要求键名后面紧跟 `=`/`:`，漏了 dict / JSON 的 repr、`api key:`、`?key=`、`AIza…`、JWT。
- **修法**：`genkit/logmask.py` 新增 `MaskingFormatter`（格式化后整条再遮，含整条异常链；`formatException` 的缓存也是遮过的），
  桌面日志和开发态控制台都用它；`MaskingFilter` 只改消息、不再改异常对象。`webui/tasks.py` 落盘前先 `mask` 再截断（先截
  可能把密钥切成两半漏遮）。正则补上查询串、dict/JSON 形态、`api key:`、`AIza…`、JWT；**不**遮单独出现的 UUID（远端任务
  id、素材 id 排障要用）。
- **测试**：用 `desktop._setup_logging` 真写一次日志，整条异常链里没有密钥、调用方手里的异常原文不变；任务记录的 error 和
  traceback 都不含密钥；六种密钥形态被遮，任务 id / 路径 / `cache_key=` / 单独的 UUID 不被误遮。

另：422 校验报错的 `errors` 里带着请求体原文（`input`），text/plain / 表单体时是 bytes，JSONResponse 序列化不了，整个请求
变成 500（设置页的请求体里还有密钥）。现在去掉 `input`、其余过 `jsonable_encoder`，返回正常的 422。

### PB-1（major）删除项目不校验 id

- **根因**：`delete_episode` 从不 `is_valid_id`，URL 里的 id 直接拼进四处路径（项目文件、归档目录、剧本、锚图目录）；
  Windows 上解码出来的 `%5C` 是路径分隔符。别的接口要么查 id、要么走 `resolve_spec_path` 的边界。
- **修法**（`routers/episodes.py`）：pipeline 检查之后立即 `is_valid_id`，不合法就是「没有这个项目」（404）。
- **测试**（仅 Windows）：`..%5C..%5C..%5Coutside%5Cvictim` 和盘符绝对路径两种 id 都 404，目录外的文件、目录原样在，
  归档目录都没建。真起服务实测同样 404。

### PB-2（major）删除 / 恢复信任 output_root

- **根因**：删除和恢复都直接用项目文件里的 `output_root`，没有 `normalize_spec` 那两条规则（在数据根里、以项目 id 结尾）。
  一份从别的数据根拷来的项目文件（列表里已经标「解析失败」）点删除，会把数据根外面那个目录整个搬进归档；写成
  `output/story_video` / `content` 的会把别的项目一起搬走；恢复时再原样写回外面。
- **修法**（`routers/episodes.py`）：新增 `_own_output_root`，只认「数据根里、以 id 结尾、不在归档目录里」的路径。删除时
  不合格就不搬，只归档项目文件，返回 `output_moved: false` 和 warning；恢复时归档里有 `output/` 而目标不合格 → 409、什么都
  不动，文案告诉人把 spec.json 的 output_root 改成 `output/story_video/<id>`。侧栏删除和「高级」页归档都把 warning 弹出来
  （以前只报成功）；`warning` 进了界面翻译的文案键。
- **测试**：绝对路径 / `../` 的 output_root 删除后外面目录原样、归档里只有 spec.json；`output/story_video`、`content`、`.`
  都不会被搬；正常项目照旧整套搬走并能恢复；越界 / 不以 id 结尾的恢复 409、归档原样。前端 vitest 覆盖两处弹窗。

### PB-3（major）锚图路径不设边界

- **根因**：`_resolve_local_asset` 做 `expanduser`、绝对路径原样接受、相对路径拼完不查边界；`local:` 值原样保留，出首帧时
  经 `to_data_url` 读出来发给生图服务。`normalize_asset_path`（PUT 角色）漏了 `~` 和 `C:x.png` 这种盘符相对写法。
- **修法**：
  - `storyvideo/spec.py` 新增 `project_file`：只收数据目录内的相对路径——`local:`、`~`、绝对路径、盘符、UNC、`..` 按字面拒
    （不碰文件系统，UNC 不会去探）；文件存在时 resolve 之后再核边界（挡指向外面的符号链接）；不存在的相对串当「没有这个
    文件」，历史上的远端 asset id 照旧保留。
  - 短剧角色 / 变体锚图只认 `content/` 下的文件（跟 GET …/asset 的读出侧同一条边界），越界直接 SpecError（跟 prop_ref /
    output_root 一样），文案说清是哪个字段、怎么改。
  - 单条视频 / 配乐 / 绘本 / 图片集的 `resolve_asset` 越界时当「没有这个文件」（跟播客一样），范围是 `content/` 和 `output/`。
  - `normalize_asset_path` 补上 `~`、盘符相对、UNC。
  - 出首帧前（`runtime._request`）再核一遍本地锚图：在 `content/` 里、是读得出来的图片，否则提交前报错，不上传、不花钱。
  - 模型起草的人物表去掉 `asset` / 变体 `asset`（锚图只能是用户上传的文件）。
- **取舍**：手改项目文件写了越界锚图的项目会打不开（列表里标出是哪个字段）。界面、上传、起草都产不出这种值，第一次发布前
  也没有老数据要兼容（界面上传的锚图全在 `content/assets/…`）；换来的是出图 / 海报 / 多模型对比三处都读不到数据目录外
  的文件，不用在每个消费方各守一遍。
- **测试**：六种写法（`~`、绝对、`..`、`local:`、`C:x`、UNC）× 成员 / 变体都被拒；合法锚图解析成绝对路径、远端 id 原样；
  `output/` 下的已存在文件、指向外面的符号链接被拒；PUT 拒 `~`、`C:x`、UNC；存草稿 400 且不写盘；四个模块越界当缺文件；
  起草结果不带 asset；出首帧前的锚图检查。

### PB-4（minor）reference_audio 不设边界

- **修法**：`normalize_spec` 对 `video_params.reference_audio` 过 `project_file`（范围是整个数据目录，生成出来的音频在
  `output/` 下也能用），存的值原样不动、请求指纹不变；运行时不再 `expanduser`、不认绝对路径，解析后不在数据目录里就在
  上传前报错（兜手改 / 绕过归一化）。
- **测试**：`~`、`../`、`C:x`、绝对路径被拒；`content/`、`output/` 下的（文件在不在都行）保留；绕过归一化塞进绝对路径 /
  `../` / `~` 时运行时报错且没有上传，合法路径照常上传。

### PB-5（minor）后期配乐绕过校验

- **根因**：「配乐必须是项目内相对路径」只在后期页 PUT 里查（还漏了 `~`、盘符相对、符号链接）；存草稿、从示例建项目、
  手改都绕得过；真正读文件的 `music_tracks` 做 `expanduser`、接受绝对路径。
- **修法**：`post_layers.music_file_problem` 一条规则（只收 `content/` 下的相对路径，先按字面拒、再 resolve 核边界），
  `music_tracks` 读文件前先过它，用不了的条目 `missing=True` 并带 `reason`（后期页就绪检查照着说是什么原因，不再一律
  「文件不存在」）；PUT 校验、存草稿、从示例建项目都用它。没放进 `normalize_spec`：那样整个后期页会 404，用户就没法在界面里
  删掉那一条。报错直说规则和怎么办：「music[i] 的 file 用不了：<原因>——只能用上传到数据目录 content/ 下的音频，在「配乐」里
  重新上传」（存草稿那条改说删掉这一条、建好项目后在「后期层」的「配乐」里上传）。初版套成「要是项目内的相对路径（原因）」，
  `output/…` 这种本来就在项目里的值读着自相矛盾（审查意见）。
- **测试**：绝对、`../`、`~`、`C:x`、UNC、`output/` 下的文件都标缺并带原因；存草稿 400 且不写盘；手写越界路径的项目后期页
  照常打开、显示原因，PUT 同样的值 400，删掉这一条能保存；PUT / 存草稿的中英文文案（英文整句翻、原因也翻、不剩中文）。

### PB-6（minor）角色 / 变体 id 不校验

- **根因**：界面新建的成员走 slug 校验，但 `normalize_spec` 只要求 id 非空；锚图上传 `_store_asset` 拿原样的 id 拼文件名，
  也不核边界——id 里的反斜杠在 Windows 上把文件写出 `content/assets/<项目>/`。
- **修法**：`_store_asset` 的 stem 过 `slugify`（中文 id 压成 `asset`，靠内容摘要区分），再核一次目标在锚图目录里；
  `normalize_spec` 只拦不安全的写法（`/ \ ? # % @`、控制字符、`.`、`..`），**不**收紧成英文 slug——每次打开项目都过这里，
  「林小满」这类中文 id 的项目会打不开。
- **测试**：九种不安全的成员 id、三种变体 id 被拒；中文 id 和 `lin.v2` 照收；存草稿 400；`..\..\..\evil` 的锚图落在锚图目录里、
  中文 id 的文件名是 `asset-<摘要>`。

### PB-8（minor）404 文案带出本机绝对路径

- **修法**：`deps.spec_not_found_detail` 把「id 越界」也当「没有这个项目」，项目文件里的路径字段越界只说是哪个字段；其余先把
  本机路径收成相对写法（数据目录下的去前缀、数据目录本身写 `<数据目录>`、主目录写 `~`）再过 humanize。humanize 给「找不到 /
  读不了 / 不是合法 JSON」三种项目文件错误只留文件名，不附带绝对路径的原文。导出、后期、对比、修订、系列交接几个路由改用
  同一个函数（以前是英文原文带路径）；项目列表里坏项目的 error 同样处理。
- **测试**：`NOPE`、output_root 越界、半截 JSON、`..\..\x.json` 四种项目在详情 / 概要 / 角色 / 后期 / 导出五个接口上都是 404、
  都不带数据根和主目录路径，`NOPE` 五处文案完全一致；列表同样。

### F1（major）插卡 id 当文件名

- **根因**：`build_timeline` 用 `08_ui/<id>.png`；id 是自由文本，`../`、盘符、绝对路径让 `render_ui_card` 把 PNG 写到外面、
  覆盖同名文件；Windows 保留字符报原始 OSError；NTFS 不分大小写，`UI_A` / `ui_a` 写成同一个文件；`%` 进 image2 输入（F6）。
- **修法**：卡图按时间线顺序编号起名（`ui_000.png`…），id 不再影响任何路径；插卡预览的缓存文件名只用内容摘要。写盘前的
  校验照样拦 `/ \ : * ? " < > | %`、控制字符和超过 64 字的 id（中文照收），撞名按不分大小写比（含镜号）；后期页的 id 输入框
  下面即时提示同一条规则。
- **测试**：六种 id（含 `../`、盘符、大小写撞名）的卡图全在 08_ui 里、各自一个文件；十四种坏 id 被拦、中文 / 带空格照收；
  前端 vitest 覆盖即时提示。

### F2 / F3 / F4（major）drawtext 与滤镜路径的转义

- **根因**：drawtext 默认 `expansion=normal`，把文字当模板（`%` 报 Stray % 后这条不画但 ffmpeg 退出 0、`\` 被吞、`%{pts}`
  被展开）；画面文字走 `text='…'` 加一层自己的转义，而滤镜图层在引号里把反斜杠原样照抄、遇到下一个 `'` 就闭合，`it's`
  把引号提前闭合，后半句被当滤镜图语法；`_filter_path` 同样把 `'` 写成引号里的 `\'`，数据根 / 字体路径带单引号时所有带
  字幕的合成失败。
- **修法**：装配版 / 后期版 / 绘本的每一个用户文字 drawtext 都加 `expansion=none`；画面文字也写成 UTF-8 文本文件走
  `textfile=`，删掉 `_esc_text`；`_filter_path` 里 `:` 和 `\` 带转义留在引号里，`'` 先闭合引号、在引号外写 `\\\'`、再重开
  引号——一处改动覆盖所有调用点。
- **测试**：所有用户文字 drawtext 都带 `expansion=none`（装配 / 后期 / 绘本）；真 ffmpeg：含 `%` 的台词字幕画出来了；
  画面文字 `it's`、`don't, stop`、`100%`、`%{pts}`、反斜杠、`'quoted', [x]; a:b` 一次合成成功、文本文件内容逐字相同、画面上
  有字；数据根在 `O'Brien [x], 100%; y=z` 下合成成功且字幕画出来了。原有几条钉死 `text='你好'` / `08_ui/<id>.png` 的测试
  改成新写法。

### F5（major）播客混音命令行超长

- **根因**：`mix_command` 每句一个 `-i`（70–110 字符）加一段 `aformat…adelay`（约 90 字符）、垫乐压低表达式每句再加约 28
  字符，全在一条命令行上；约 150 句超过 CreateProcess 的 32767 字符，`_run` 把 WinError 206 报成「跑不起 ffmpeg」。另外每句
  都从 0 秒铺到片尾，九十分钟一期的 CPU 时间超过固定的十分钟超时。
- **修法**（`src/podcast/audio.py` 的 `_mix_to`，与同轮「合成路径」那组的改法合并成一版）：句子超过
  `storyvideo.segmented.MIX_GROUP`（64）句时先按时间顺序每组预混成中间文件（WavPack 32 位浮点，无损、叠加过满幅也不削顶；
  落点按采样数算，与一遍混逐采样一致），最后那条命令只接中间文件、片头片尾和垫乐；它的滤镜图（垫乐压低段每句一项）写进
  脚本文件走 `-filter_complex_script`。成品先写临时文件、成功才原子替换。超时按节目长度放宽（至少十分钟、节目时长的一半）；
  WinError 206 / E2BIG 说人话。中间文件和滤镜脚本放在 `08_mix/.mix-*` 临时目录，混完（成败都算）删掉；删不掉不算失败
  （`ignore_cleanup_errors=True`，跟后期合成的临时目录一样）：Windows 上杀毒 / 索引进程偶尔短暂占着刚写出的中间文件，
  那时成品已经写好，要是抛出来，章节 / 文字稿 / 节目单就不写了、任务报「没预料到的错误」，用户会去重混。这样留下的残留
  下一次混音开始前顺手清掉（同一期同时只会有一个混音任务）。
- **测试**：九百句、数据根路径很长时每条命令远低于 30000 字符、每次 amix 不超过 MIX_GROUP 路、垫乐压低段只在脚本里；
  小节目一遍；100 句是两组预混 + 一遍总混、临时目录删掉、目录里只剩成品；真 ffmpeg 混 120 句，成品时长与时间线差不到
  0.15 秒；（仅 Windows）混音途中一直开着临时目录里的一个文件，`mix()` 照样成功、混音记录照写，下一次混音把残留清掉
  （修之前抛 `PermissionError: [WinError 32]`）。每句落点逐采样一致见 `tests/test_render_segmented.py`。

### F6（minor）路径里的 %d 被 image2 当序号模板

- **修法**：后期版的插卡 / 角标 PNG 输入、绘本的插画输入都加 `-f image2 -pattern_type none`；插卡文件名不再用 id（F1）。
  绘本插画另外按**真实内容**指定解码器（`-c:v png / mjpeg / webp / gif / bmp / tiff`，Pillow 认格式；认不出就什么都不加，
  交给 ffmpeg 自己探测）：强制 image2 以后 ffmpeg 只按扩展名挑解码器，而插画一律按 `.png` 落盘（下载结果原样写字节），不少生图服务
  （如 Seedream）给的其实是 JPEG、有的是 WebP——只加 `-f image2` 的话 png 解码器报 `Invalid PNG signature`，`-loop 1` 让它不停重试，
  装配卡到二十分钟超时才失败（审查时发现的回归：真实出图结果里按 `.png` 落盘的插画多数其实是 JPEG）。插卡和角标
  是 Pillow 自己画的真 PNG，不受影响。
- **测试**：插卡那一路命令带 `-pattern_type none`；真 ffmpeg：数据根在 `shows%d` 下带插卡合成成功（修之前报「Could find
  no file with path … and index in the range 0-4」）；绘本插画按内容选解码器（PNG / JPEG / WebP 各一，认不出的不加参数）；
  真 ffmpeg：数据根在 `books%d` 下、三页插画分别是 JPEG / WebP / PNG 内容的 `.png`，`assemble_book` 成功、封面帧是插画的颜色
  （只加 `-f image2` 时 60 秒超时失败）。

---

## 三、验证

- 修之前先复现：三路自查留下的复现用例（路径 13 条、参数 7 条、服务 10 条）修前全部失败；本轮新写的回归测试也在修之前的
  代码上跑过一遍（`git archive HEAD` 导出一份原样代码），`tests/test_security_path_boundary.py` 与
  `tests/test_security_local_service.py` 共 95 条失败、`tests/test_security_ffmpeg_params.py` 27 条失败（剩下通过的是
  「合法请求照常可用」「不误遮排障 id」这类对照组，和一条真 ffmpeg 的播客回归）。
- 修之后（含下面审查意见的补修）：新增三个后端测试文件、三个前端测试文件全部通过；后端全量 pytest 2987 条、前端全量
  vitest 353 条通过。
- 在一个临时端口上用默认方式（带令牌）真起服务逐条打了一遍：启动器正常打印地址不退出；不带令牌 401、带头 / 带 cookie 200、
  cookie 名是 `storystudio_token_<端口>`；外站 Host、跨站 POST、别的端口带 cookie 的 POST 都是 403，同源 POST 200；接口文档
  不带令牌 401；`..%5C` 删除 404；英文界面下 401 / 403 文案有英文。服务用完已停。

### 审查意见与补修

修完之后又过了一轮代码审查，4 条意见逐条核实，全部属实、都已补修并带回归测试（修前先复现、修后再跑一遍）：

| 严重度 | 意见 | 核实 | 处理 |
|---|---|---|---|
| blocker | F6 给绘本插画加了 `-f image2 -pattern_type none` 之后，内容是 JPEG / WebP 的 `.png` 插画读不出来，装配卡到超时 | 裸 ffmpeg 6.0 复现（`Invalid PNG signature` 刷屏、不退出）；回归测试在只加 `-f image2` 的代码上 60 秒超时失败 | 插画按真实内容指定解码器，见 F6 |
| major | 来路检查要求 Origin 与 Host 逐字相同，vite 开发代理（字符串写法 = `changeOrigin: true`，只改 Host）下开发态所有写请求 403；文档说 vite 不改 Host 不对 | 读 vite 源码确认字符串写法展开成 `changeOrigin: true`、Origin 只在 WebSocket 时改写；审查者真起 vite 8.2.2 转发实测 403，修前的回归用例同样 403；修后真起 vite 转发，同源 POST 200 | 没开令牌时 Origin 只要求是回环地址，开着令牌仍逐字比，见 SEC-2；文档改正 |
| minor | 播客混音的临时目录删不掉（Windows 上被占着）会让已经混好的一期报失败、漏写混音记录 | 测试里开着临时目录里的一个文件，修前抛 `PermissionError: [WinError 32]` | `ignore_cleanup_errors=True`，残留下次混音前清掉，见 F5 |
| minor | 配乐报错「要是项目内的相对路径（不是数据目录里的相对路径）」自相矛盾，英文同样别扭 | 接口实测文案属实 | 改成直说规则和怎么办，中英文都改，见 PB-5 |

## 四、没做的 / 留给后续

- 镜号只按原样查重，`S1` 和 `s1` 在 NTFS 上会写成同一个 `02_video/S1.mp4`。在 `normalize_spec` 里拒会让已有项目打不开；
  合并时补成了 lint 提醒（`storyvideo.lint.check_shot_id_case`，走项目详情 / prepare 的告警通道，中英文都有）。
- 音色库档案的参考音频（`genkit.voices` 的 `reference_audio`）可以写本机任意位置的音频：这是有意的功能（DESIGN §33，手填
  本机路径），音色库存在用户自己的 `~/.storystudio/voices.json` 里、没有导入入口，接口又已经在令牌与来路检查后面，维持现状。
- 角色页（`Cast.vue`）拼接口路径时对角色 / 变体 id 做 `encodeURIComponent`：合并时补上了。
- 改名之前按 id 起名的旧卡图（`08_ui/<id>.png`）不会自动清理，只占一点磁盘。
- `_run` 没有扫描 ffmpeg 的 stderr 找「Stray %」这类非致命错误：`expansion=none` 之后用户文字已经触发不了它，属于可选的
  纵深防御。
