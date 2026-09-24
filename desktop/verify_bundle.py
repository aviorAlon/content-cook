"""打包后自检：dist 里只能有程序运行要用的东西。

用法（在仓库根目录）：
    python desktop/verify_bundle.py dist/StoryStudio                          # Windows / Linux 单文件夹
    python desktop/verify_bundle.py dist/StoryStudio --ffmpeg desktop/ffmpeg  # 连同安装包另外带的 ffmpeg 目录一起查
    python desktop/verify_bundle.py dist/StoryStudio.app                      # macOS

有任何一条命中就打印出来并以退出码 1 结束，构建脚本和 CI 据此让构建失败：

1. 测试进了包：路径里有 tests/ 或 test/ 目录、文件名是 test_*.py / *_test.py / conftest.py（含 .pyc），
   或者 PYZ 里有 tests / conftest / pytest / _pytest / *.pytest_plugin / *.testclient 模块；
2. 白名单以外的数据文件：skills/ examples/ docs/ 下不在 desktop/bundle_manifest.py 的 RUNTIME_DATA / NOTICE_FILES 里
   （或白名单里的没进包——少了许可声明同样算），webui/static/ 下除了前端构建产物 app/ 与 STATIC_TOP 以外的文件；
3. 本机数据进了包：.env、settings.json、*.db / *.sqlite3、*.log、logs/ 目录，或者数据目录才有的
   content/ output/ production/ .webui_deleted/ .webui_draft_cache/ local_workflows/；
4. 插件进了包：插件只从用户目录加载（<数据目录>/plugins 与 STORYSTUDIO_PLUGIN_PATH），包里出现 plugins/ 目录、
   storystudio_plugin_* 模块或 storystudio-plugin* 的目录（含 .dist-info）就是把某个插件打进去了；
5. 打包机自己的绝对路径进了包：仓库根目录、主检出目录（在 git worktree 或副本里打包时）、可编辑安装指向的目录、
   用户主目录、数据与插件目录（全部运行时从环境里取，不写死任何名字），在任何文件的字节里（utf-8 / utf-16-le，
   不分大小写），或者 PYZ 里模块的源文件名与字符串常量里；
6. 私有敏感词：不放进仓库（公开的词表等于把词公开），需要时用环境变量 BUNDLE_DENYLIST（一行一个，也认「|」分隔）
   或仓库根目录下不入库的 .release-denylist 文件（一行一个，# 开头是注释）传进来。只查第一方内容——webui / skills /
   examples / docs / desktop 与 src 下各包的数据文件、src 下各包的 PYZ 模块、入口脚本：第三方库的字节里什么词都可能有
   （各种名称表、词典、清单），查了只会误报。ASCII 词按整词匹配。命中时只报「词表第几条」，
   **不打印词本身**：CI 日志是公开的，secret 只按整串打码，拆出来的单个词会原样出现在日志里。

--ffmpeg <目录>：Inno Setup 会把 desktop/ffmpeg/ 整个带进安装包，这里核对它只有 ffmpeg / ffprobe 可执行文件、
动态库与许可证说明，并按第 5 条查打包机路径（第三方二进制不查私有词）。目录不存在就跳过。
"""
from __future__ import annotations

import argparse
import json
import marshal
import os
import re
import subprocess
import sys
import types
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from bundle_manifest import BUNDLED_FILES, STATIC_TOP, WHITELISTED_DIRS  # noqa: E402

TEST_DIRS = {"tests", "test"}
TEST_FILE = re.compile(r"^(test_.*|.*_test|conftest)\.pyc?$", re.IGNORECASE)
# 模块名：顶层 tests / test / conftest 包、任何 conftest / test_* / *_test 模块、tests 子包、测试框架与测试客户端。
# 第三方库里叫 tests / test 的**单个模块**不算（jinja2.tests 是模板的 is-测试函数，运行时要用）。
TEST_MODULE = re.compile(
    r"^(tests?|conftest|pytest|_pytest|pluggy|iniconfig)(\.|$)"
    r"|(^|\.)(conftest|test_[^.]*|[^.]*_test|pytest_plugin|testclient)$"
    r"|(^|\.)tests\."
)
# 插件加载器给插件起的模块名前缀、插件目录 / 发行包的名字前缀（通用名字，不含任何具体插件）
PLUGIN_MODULE = re.compile(r"^storystudio_plugin(_|\.|$)")
PLUGIN_DIR = re.compile(r"^storystudio[-_]plugin", re.IGNORECASE)
LOCAL_FILES = {".env", "settings.json", ".dev-settings.json"}
LOCAL_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log"}
LOCAL_DIRS = {"logs"}
# 数据目录（STORYSTUDIO_PROJECT_ROOT）里才有的东西：出现在包的数据根下面就是把一个真实数据目录打进去了
DATA_ROOT_DIRS = {"content", "output", "production", ".webui_deleted", ".webui_draft_cache", "local_workflows", ".storystudio"}
# 数据根下面属于本项目自己的目录（私有词只在这些地方查）；src 下的各个包在运行时再补进来
FIRST_PARTY_DATA = {"webui", "skills", "examples", "docs", "desktop"}
# desktop/ffmpeg/ 里允许的文件
FFMPEG_ALLOWED = re.compile(r"^(ffmpeg|ffprobe)(\.exe)?$|\.dll$|^(license|readme|copying)", re.IGNORECASE)
MAX_REPORT = 200


class Needles(NamedTuple):
    """要在包里找的串。

    ``paths``：打包机的绝对路径（原样大小写，比较时不分大小写），命中时原样打印——那是本机自己的路径。
    ``words``：私有词，``(出处, 词)``；只查第一方内容，命中时只打印出处（「BUNDLE_DENYLIST 第 2 条」）。
    ``skipped``：太短、没拿来查的私有词条数（ASCII 少于 3 个字符、其它少于 2 个字）。
    """

    paths: tuple[str, ...] = ()
    words: tuple[tuple[str, str], ...] = ()
    skipped: int = 0


# ---------------------------------------------------------------------------- 要找的串


def _path_variants(path: str) -> set[str]:
    """同一个路径的几种写法：原样、正斜杠、反斜杠。"""

    path = path.strip().rstrip("\\/")
    return {v for v in (path, path.replace("\\", "/"), path.replace("/", "\\")) if v}


def _deep_enough(path: str) -> bool:
    """至少两级（C:\\Users\\x、/home/x）才拿来查：/root 这种太短的串在二进制里会误报。"""

    parts = [p for p in re.split(r"[\\/]+", path) if p and not p.endswith(":")]
    return len(parts) >= 2


def _on_ci() -> bool:
    return any(os.environ.get(name, "").lower() in ("1", "true") for name in ("CI", "GITHUB_ACTIONS"))


def _main_checkout(repo: Path) -> str | None:
    """在 git worktree（或 git 看得见的副本）里打包时，主检出目录的路径：本机的可编辑安装多半指着它。"""

    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    common = out.stdout.strip()
    return str(Path(common).parent) if out.returncode == 0 and common else None


def _editable_install() -> str | None:
    """``pip install -e`` 装的 storystudio 指向的目录（direct_url.json 里的 file:// 地址）。"""

    try:
        from importlib.metadata import PackageNotFoundError, distribution
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        raw = distribution("storystudio").read_text("direct_url.json")
    except (ImportError, PackageNotFoundError, OSError):
        return None
    try:
        url = str(json.loads(raw or "{}").get("url") or "")
    except ValueError:
        return None
    return url2pathname(urlparse(url).path) if url.startswith("file:") else None


def _keep_word(word: str) -> bool:
    """ASCII 词至少 3 个字符；含非 ASCII 字符的词（中文等）至少 2 个字——两个汉字的名字很常见。"""

    return len(word) >= 3 or (len(word) >= 2 and not word.isascii())


def _private_words(repo: Path) -> tuple[list[tuple[str, str]], int]:
    words: list[tuple[str, str]] = []
    skipped = 0
    env = [w.strip() for w in re.split(r"[|\r\n]+", os.environ.get("BUNDLE_DENYLIST", "")) if w.strip()]
    sources = [(f"BUNDLE_DENYLIST 第 {i} 条", w) for i, w in enumerate(env, 1)]
    deny_file = repo / ".release-denylist"
    if deny_file.is_file():
        for i, line in enumerate(deny_file.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip() and not line.lstrip().startswith("#"):
                sources.append((f".release-denylist 第 {i} 行", line.strip()))
    seen: set[str] = set()
    for label, word in sources:
        if not _keep_word(word):
            skipped += 1
        elif word.lower() not in seen:
            seen.add(word.lower())
            words.append((label, word))
    return words, skipped


def build_needles(repo: Path = REPO) -> Needles:
    """打包机的绝对路径 + 可选的私有词表。

    CI 上不查用户主目录：那是公共 runner 的通用账户，不是谁的个人信息，而且第三方预编译包（Rust / C 扩展）
    常带着它们自己在同一种 runner 上构建时的主目录路径，查了只会误报。仓库根目录照查。"""

    candidates = [str(repo.resolve()), _main_checkout(repo), _editable_install()]
    for env in ("STORYSTUDIO_HOME", "STORYSTUDIO_PROJECT_ROOT"):
        candidates.append(os.environ.get(env))
    candidates += os.environ.get("STORYSTUDIO_PLUGIN_PATH", "").split(os.pathsep)
    if not _on_ci():
        for env in ("USERPROFILE", "HOME"):
            candidates.append(os.environ.get(env))
        try:
            candidates.append(str(Path.home()))
        except RuntimeError:
            pass
    paths: set[str] = set()
    for c in candidates:
        if c and c.strip() and _deep_enough(c):
            paths |= _path_variants(c)
    words, skipped = _private_words(repo)
    return Needles(paths=tuple(sorted(paths)), words=tuple(words), skipped=skipped)


# ---------------------------------------------------------------------------- 匹配


def _variants(text: str) -> tuple[bytes, bytes]:
    # 两边都用 bytes.lower()（只动 ASCII 字母），utf-16 里的中文码元被同样变换，比较仍然一致
    return text.encode("utf-8").lower(), text.encode("utf-16-le").lower()


def _path_hits(low: bytes, paths: tuple[str, ...]) -> list[str]:
    hits = []
    for needle in paths:
        u8, u16 = _variants(needle)
        if u8 in low or u16 in low:
            hits.append(needle)
    return sorted({h.lower(): h for h in hits}.values())


def _word_pattern(word: str) -> re.Pattern[bytes]:
    u8, u16 = _variants(word)
    if word.isascii():                                   # ASCII 词按整词：前后不能紧挨字母或数字
        return re.compile(rb"(?<![a-z0-9])" + re.escape(u8) + rb"(?![a-z0-9])"
                          + rb"|(?<![a-z0-9]\x00)" + re.escape(u16) + rb"(?![a-z0-9]\x00)")
    return re.compile(re.escape(u8) + b"|" + re.escape(u16))


def _word_hits(low: bytes, words: tuple[tuple[str, str], ...]) -> list[str]:
    return [label for label, word in words if _word_pattern(word).search(low)]


def _report(shown: str, low: bytes, needles: Needles, *, words: bool) -> list[str]:
    errors = [f"打包机路径出现在 {shown}：{p!r}" for p in _path_hits(low, needles.paths)]
    if words:
        errors += [f"私有词（{label}）出现在 {shown}" for label in _word_hits(low, needles.words)]
    return errors


def _check_zip(path: Path, shown: str, needles: Needles, *, words: bool) -> list[str]:
    import zipfile

    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    errors += _report(f"{shown}!{info.filename}", archive.read(info).lower(), needles, words=words)
    except zipfile.BadZipFile:
        pass
    return errors


# ---------------------------------------------------------------------------- 文件


def first_party_packages(repo: Path = REPO) -> set[str]:
    try:
        return {p.name for p in (repo / "src").iterdir() if (p / "__init__.py").is_file()}
    except OSError:
        return set()


def data_roots(dist: Path) -> list[Path]:
    """PyInstaller 的数据根：放 base_library.zip 的那一层（Windows 是 _internal，mac 是 Contents/Resources 等）。"""

    return sorted({p.parent for p in dist.rglob("base_library.zip")})


def _static_ok(rest: tuple[str, ...]) -> bool:
    """webui/static/ 下：构建产物 app/ 里的都行，顶层只认 STATIC_TOP。"""

    return (rest[0] == "app" and len(rest) > 1) or (len(rest) == 1 and rest[0] in STATIC_TOP)


def check_files(dist: Path, needles: Needles, first_party: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    own = FIRST_PARTY_DATA | (first_party_packages() if first_party is None else set(first_party))
    roots = data_roots(dist)
    if not roots:
        errors.append(f"{dist}: 找不到 base_library.zip——这不是 PyInstaller 的产物目录？")
    for path in sorted(p for p in dist.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = path.relative_to(dist)
        parts = [p.lower() for p in rel.parts]
        shown = rel.as_posix()
        if TEST_DIRS & set(parts[:-1]):
            errors.append(f"测试目录进了包：{shown}")
        if TEST_FILE.match(path.name):
            errors.append(f"测试文件进了包：{shown}")
        if path.name.lower() in LOCAL_FILES or path.name.lower().startswith(".env.") or path.suffix.lower() in LOCAL_SUFFIXES:
            errors.append(f"本机数据文件进了包：{shown}")
        if LOCAL_DIRS & set(parts[:-1]):
            errors.append(f"日志目录进了包：{shown}")
        if any(PLUGIN_DIR.match(p) for p in parts):
            errors.append(f"插件进了包：{shown}（插件只从用户目录加载，不随安装包）")
        first_party_file = False
        for root in roots:
            try:
                inner = path.relative_to(root).parts
            except ValueError:
                continue
            if len(inner) > 1 and inner[0] == "plugins":
                errors.append(f"插件目录进了包：{shown}（插件只从用户目录加载，不随安装包）")
            if len(inner) > 1 and inner[0] in DATA_ROOT_DIRS:
                errors.append(f"数据目录里的东西进了包：{shown}")
            if len(inner) > 1 and inner[0] in WHITELISTED_DIRS and "/".join(inner) not in BUNDLED_FILES:
                errors.append(f"不在白名单里的数据文件：{shown}（要随包就加进 desktop/bundle_manifest.py）")
            if len(inner) > 2 and inner[:2] == ("webui", "static") and not _static_ok(inner[2:]):
                errors.append(f"不在白名单里的静态文件：{shown}（webui/static/ 下只带构建产物 app/ 与 {' / '.join(STATIC_TOP)}）")
            if len(inner) > 1 and inner[0] in own:
                first_party_file = True
        if needles.paths or (first_party_file and needles.words):
            low = path.read_bytes().lower()
            errors += _report(shown, low, needles, words=first_party_file)
            if path.suffix.lower() == ".zip":        # base_library.zip 这类：成员是压缩的，解开再查
                errors += _check_zip(path, shown, needles, words=first_party_file)
    for root in roots:
        for rel in BUNDLED_FILES:
            if not (root / rel).is_file():
                errors.append(f"白名单里的文件没进包：{(root / rel).relative_to(dist).as_posix()}")
    return errors


def check_ffmpeg(folder: Path, needles: Needles) -> list[str]:
    """安装包另外带的 desktop/ffmpeg/：只许 ffmpeg / ffprobe、动态库、许可证说明；查打包机路径（第三方二进制不查私有词）。"""

    folder = Path(folder)
    if not folder.is_dir():
        return []
    errors: list[str] = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        shown = f"{folder.name}/{path.relative_to(folder).as_posix()}"
        if not FFMPEG_ALLOWED.search(path.name):
            errors.append(f"ffmpeg 目录里有不该进安装包的文件：{shown}（只放 ffmpeg / ffprobe、dll 与许可证说明）")
        if needles.paths:
            errors += _report(shown, path.read_bytes().lower(), needles, words=False)
    return errors


# ---------------------------------------------------------------------------- PYZ


def _strings(code: types.CodeType, out: list[str]) -> None:
    out.append(code.co_filename)
    for const in code.co_consts:
        if isinstance(const, str):
            out.append(const)
        elif isinstance(const, types.CodeType):
            _strings(const, out)


def _executables(dist: Path) -> list[Path]:
    names = ("StoryStudio.exe", "StoryStudio")
    found = [p for p in dist.rglob("*") if p.is_file() and p.name in names]
    return sorted(found)


def check_pyz(dist: Path, needles: Needles, first_party: set[str] | None = None) -> list[str]:
    """PYZ 里的模块是逐个压缩的，直接 grep 字节查不到：解开来看模块名、源文件名和字符串常量。"""

    try:
        from PyInstaller.archive.readers import (
            PKG_ITEM_PYMODULE, PKG_ITEM_PYPACKAGE, PKG_ITEM_PYSOURCE, PKG_ITEM_PYZ, CArchiveReader,
        )
    except ImportError:
        return ["装的 PyInstaller 里没有 archive.readers，查不了 PYZ——升级 PyInstaller（>= 6）"]
    own = first_party_packages() if first_party is None else set(first_party)
    errors: list[str] = []
    exes = _executables(dist)
    if not exes:
        return [f"{dist}: 找不到 StoryStudio 可执行文件"]
    for exe in exes:
        try:
            pkg = CArchiveReader(str(exe))
        except Exception as exc:  # noqa: BLE001 - 不是 PyInstaller 的 exe（比如 .app 里的别的东西）就跳过
            errors.append(f"{exe.name}: 读不出内嵌归档（{type(exc).__name__}: {exc}）")
            continue
        blobs: list[tuple[str, object, bool]] = []
        for name, entry in pkg.toc.items():
            typecode = entry[-1]
            if typecode == PKG_ITEM_PYZ:
                pyz = pkg.open_embedded_archive(name)
                for module in pyz.toc:
                    if TEST_MODULE.search(module):
                        errors.append(f"测试模块进了包：{module}")
                    if PLUGIN_MODULE.search(module):
                        errors.append(f"插件模块进了包：{module}（插件只从用户目录加载，不随安装包）")
                    try:
                        blobs.append((module, pyz.extract(module), module.split(".")[0] in own))
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"PYZ 里的 {module} 解不开（{type(exc).__name__}）")
            elif typecode in (PKG_ITEM_PYSOURCE, PKG_ITEM_PYMODULE, PKG_ITEM_PYPACKAGE):
                # 入口脚本（PYSOURCE、不是 PyInstaller 自己的 pyi* 引导 / 运行时钩子）算第一方
                ours = typecode == PKG_ITEM_PYSOURCE and not name.startswith("pyi")
                try:
                    blobs.append((name, marshal.loads(pkg.extract(name)), ours))
                except Exception:  # noqa: BLE001 - 解不开的脚本条目只查字节
                    blobs.append((name, pkg.extract(name), ours))
        for module, blob, ours in blobs:
            texts: list[str] = []
            if isinstance(blob, types.CodeType):
                _strings(blob, texts)
                if re.match(r"^([A-Za-z]:[\\/]|/)", blob.co_filename or ""):
                    errors.append(f"{module}: 源文件名是打包机的绝对路径 {blob.co_filename!r}")
            elif isinstance(blob, (bytes, bytearray)):
                texts.append(bytes(blob).decode("utf-8", "ignore"))
            low = "\n".join(texts).encode("utf-8", "ignore").lower()
            errors += _report(f"PYZ 模块 {module}", low, needles, words=ours)
    return errors


# ---------------------------------------------------------------------------- 入口


def verify(dist: Path, needles: Needles | None = None, *, ffmpeg: Path | None = None) -> list[str]:
    dist = Path(dist)
    if not dist.exists():
        return [f"{dist} 不存在——先跑 PyInstaller"]
    needles = build_needles() if needles is None else needles
    errors = check_files(dist, needles) + check_pyz(dist, needles)
    if ffmpeg is not None:
        errors += check_ffmpeg(ffmpeg, needles)
    return errors


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):      # CI 的管道可能是 cp1252：中文打不出来也不能让自检自己崩掉
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="打包后自检：dist 里只能有程序运行要用的东西")
    parser.add_argument("dist", nargs="?", default=str(REPO / "dist" / "StoryStudio"))
    parser.add_argument("--ffmpeg", default=None, help="安装包另外带的 ffmpeg 目录（不存在就跳过）")
    args = parser.parse_args(argv)
    dist = Path(args.dist)
    needles = build_needles()
    if needles.skipped:
        print(f"私有词表里有 {needles.skipped} 条太短，没有拿来查（ASCII 至少 3 个字符，中文等至少 2 个字）")
    errors = verify(dist, needles, ffmpeg=Path(args.ffmpeg) if args.ffmpeg else None)
    if errors:
        for line in errors[:MAX_REPORT]:
            print(line)
        if len(errors) > MAX_REPORT:
            print(f"……另有 {len(errors) - MAX_REPORT} 条")
        print(f"包内容自检没过：{len(errors)} 条（{dist}）")
        return 1
    print(f"包内容自检通过：{dist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
