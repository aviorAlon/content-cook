"""打包脚本的静态检查：测试环境里不一定有 Inno Setup / macOS，至少保证脚本之间引用一致、版本号能被正确读出、
包里只放运行要用的东西（白名单、测试排除、打包后自检），pip 的 sdist 也不带测试。"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_packaging_{name}", DESKTOP / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(DESKTOP))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(DESKTOP))
    return module


MANIFEST = _load("bundle_manifest")
VERIFY = _load("verify_bundle")


def _version_from_source() -> str:
    text = (ROOT / "src" / "genkit" / "version.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def _tracked(prefix: str) -> list[str] | None:
    """git 看得见的文件（已跟踪 + 未忽略的新文件；没有 git / 不是检出目录就返回 None，调用方跳过这条比对）。"""

    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                              "--", prefix], capture_output=True, check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return sorted({p for p in out.decode("utf-8").split("\0") if p and (ROOT / p).is_file()})


def test_spec_version_regex_matches_package_version():
    from genkit.version import __version__

    spec = (DESKTOP / "storystudio.spec").read_text(encoding="utf-8")
    # spec 里用的正则必须和这里一样，否则 .app 的版本号会是空串或文档字符串
    assert r'__version__\s*=\s*"([^"]+)"' in spec
    assert _version_from_source() == __version__


def test_installer_references_existing_paths():
    iss = (DESKTOP / "installer.iss").read_text(encoding="utf-8")
    assert "#ifndef AppVersion" in iss, "版本号要能由命令行 /DAppVersion 覆盖"
    assert 'Source: "..\\dist\\StoryStudio\\*"' in iss
    assert "skipifsourcedoesntexist" in iss, "ffmpeg 目录是可选的"
    # 卸载不能碰用户目录
    assert "{userdocs}" not in iss and "{%USERPROFILE}" not in iss
    for name in ("build_windows.ps1", "build_macos.sh", "entitlements.plist", "bundle_manifest.py", "verify_bundle.py"):
        assert (DESKTOP / name).exists(), name


def test_build_scripts_pass_version_to_inno():
    ps1 = (DESKTOP / "build_windows.ps1").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "/DAppVersion=" in ps1
    assert "/DAppVersion=" in ci
    assert "desktop\\installer.iss" in ci


def test_spec_excludes_heavy_optional_packages():
    spec = (DESKTOP / "storystudio.spec").read_text(encoding="utf-8")
    for pkg in ("faster_whisper", "torch", "ctranslate2", "onnxruntime", "numpy"):
        assert f'"{pkg}"' in spec, f"{pkg} 应在 excludes 里（ASR 闸按需安装到系统 Python）"
    # 插卡渲染要 PIL，不能被排除
    assert '"PIL"' not in spec and '"pillow"' not in spec


# ---------------------------------------------------------------------------- 只带运行要用的东西


def test_spec_excludes_test_frameworks_and_test_code():
    """测试不进安装包：测试框架、测试客户端、测试代码本身都排除，数据里也没有 tests/ 目录。"""

    spec = (DESKTOP / "storystudio.spec").read_text(encoding="utf-8")
    block = spec[spec.index("_NO_SHIP = ["):spec.index("]", spec.index("_NO_SHIP = ["))]
    for name in ("tests", "conftest", "pytest", "_pytest", "pluggy", "fastapi.testclient", "starlette.testclient"):
        assert f'"{name}"' in block, name
    assert "*_NO_SHIP" in spec, "_NO_SHIP 要同时进 excludes"
    assert "hiddenimports = [m for m in hiddenimports if _ships(m)]" in spec, "collect_submodules 收来的也要滤一遍"
    assert '"tests"' not in spec.replace(block, "") and "ROOT / \"tests\"" not in spec


def test_spec_takes_skills_and_examples_from_the_whitelist_only():
    """skills/ 与 examples/ 逐个文件带（desktop/bundle_manifest.py），不整目录带：开发机上临时丢进去的文件不会进包。"""

    spec = (DESKTOP / "storystudio.spec").read_text(encoding="utf-8")
    assert "datas += _runtime_datas(ROOT)" in spec and "datas += _static_datas(ROOT)" in spec
    assert "from bundle_manifest import datas as _runtime_datas, static_datas as _static_datas" in spec
    for folder in ("skills", "examples"):
        assert f'ROOT / "{folder}"' not in spec, f"{folder}/ 不能再按目录进 datas"
    entries = MANIFEST.datas(ROOT)
    assert len(entries) == len(MANIFEST.RUNTIME_DATA) == len(set(MANIFEST.RUNTIME_DATA))
    for (source, dest), rel in zip(entries, MANIFEST.RUNTIME_DATA):
        assert Path(source).is_file(), rel
        assert rel.split("/")[0] in MANIFEST.WHITELISTED_DIRS and dest == rel.rsplit("/", 1)[0], rel


def test_spec_takes_only_the_built_app_and_listed_top_level_static_files():
    """前端静态目录不整个带：vite 只清空 static/app/，static/ 顶层丢进去的截图、草稿、旧产物会跟着进包。"""

    spec = (DESKTOP / "storystudio.spec").read_text(encoding="utf-8")
    assert '"webui" / "static"), "webui/static")' not in spec, "static/ 不能再整个目录进 datas"
    entries = MANIFEST.static_datas(ROOT)
    static = ROOT / MANIFEST.STATIC_SOURCE
    assert entries[0] == (str(static / "app"), "webui/static/app")
    assert {Path(s).name for s, dest in entries[1:]} == {n for n in MANIFEST.STATIC_TOP if (static / n).is_file()}
    assert all(dest == "webui/static" and Path(s).is_file() for s, dest in entries[1:])
    # 仓库里 static/ 顶层跟踪的文件都在白名单里（app/ 由 vite 管）
    tracked = _tracked(MANIFEST.STATIC_SOURCE)
    if tracked is not None:
        top = {p.split("/")[-1] for p in tracked if p.count("/") == 3}
        assert top <= set(MANIFEST.STATIC_TOP), sorted(top - set(MANIFEST.STATIC_TOP))


def test_whitelist_matches_what_the_repo_tracks():
    """仓库里 skills/ 与 examples/ 下的文件恰好是白名单（skills/README.md 是给人看的说明，不进包）：
    多了没列的会悄悄不进包，列了没有的打包直接失败。"""

    skills, examples = _tracked("skills"), _tracked("examples")
    if skills is None or examples is None:
        pytest.skip("不是 git 检出目录")
    files = {p for p in skills + examples if p != "skills/README.md"}
    assert files == set(MANIFEST.RUNTIME_DATA), (sorted(files - set(MANIFEST.RUNTIME_DATA)),
                                                 sorted(set(MANIFEST.RUNTIME_DATA) - files))


def test_every_whitelisted_skill_file_is_read_by_code():
    """白名单里的每份数据都真有代码在读——没人读的文件不该随包分发。"""

    from genkit.adapters.comfyui import BUILTIN_NAMES

    source = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src").rglob("*.py"))
    for rel in MANIFEST.RUNTIME_DATA:
        if not rel.startswith("skills/"):
            continue
        name = rel.rsplit("/", 1)[1]
        if rel.startswith("skills/local-models/workflows/"):
            assert name.split(".")[0] in BUILTIN_NAMES, rel
        else:
            assert f'"{name}"' in source, rel
    # 内置工作流每份都配了 .map.json——只有工作流没有映射，适配器不知道提示词该往哪填
    for name in BUILTIN_NAMES:
        assert f"skills/local-models/workflows/{name}.json" in MANIFEST.RUNTIME_DATA, name
        assert f"skills/local-models/workflows/{name}.map.json" in MANIFEST.RUNTIME_DATA, name


def test_card_data_files_are_whitelisted():
    """题材卡 / 形态卡 / 工艺清单 / 风格卡 / 配乐预设 / 真人感清单是运行时读的 JSON，不进包就是对应功能打不开。"""

    for rel in ("skills/ai-short-film/scripts/camera_library.json", "skills/ai-short-film/scripts/style_library.json",
                "skills/ai-short-film/scripts/genre_cards.json", "skills/ai-short-film/scripts/form_cards.json",
                "skills/ai-short-film/scripts/genre_camera_pairing.json", "skills/ai-short-film/scripts/craft_checklist.json",
                "skills/ai-image/scripts/style_cards.json", "skills/ai-music/scripts/music_briefs.json",
                "skills/ai-podcast/scripts/humanlike_checklist.json"):
        assert rel in MANIFEST.RUNTIME_DATA, rel
    spec = (DESKTOP / "storystudio.spec").read_text(encoding="utf-8")
    for pkg in ("imagecraft", "music", "podcast", "clip", "script"):
        assert f'collect_submodules("{pkg}")' in spec, pkg


def test_third_party_notices_ship_with_the_bundle():
    """随包的题材卡等数据取自 MIT 项目：许可要求版权与许可声明跟着副本走，所以声明文件按白名单逐个进包，
    包内路径跟仓库相同；docs/ 下别的文档不进包。"""

    assert "docs/third-party-notices.md" in MANIFEST.NOTICE_FILES and "LICENSE" in MANIFEST.NOTICE_FILES
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License") and "Copyright (c) 2026 avioralon" in license_text
    assert dict(MANIFEST.notice_datas(ROOT))[str(ROOT / "LICENSE")] == "."   # 进包根目录，不是叫 LICENSE 的目录
    assert len(MANIFEST.NOTICE_FILES) == len(set(MANIFEST.NOTICE_FILES))
    assert not set(MANIFEST.NOTICE_FILES) & set(MANIFEST.RUNTIME_DATA)
    assert MANIFEST.BUNDLED_FILES == MANIFEST.RUNTIME_DATA + MANIFEST.NOTICE_FILES
    for (source, dest), rel in zip(MANIFEST.notice_datas(ROOT), MANIFEST.NOTICE_FILES):
        assert Path(source).is_file() and dest == (rel.rsplit("/", 1)[0] if "/" in rel else "."), rel
        assert "/" not in rel or rel.split("/")[0] in MANIFEST.WHITELISTED_DIRS, rel   # 根目录只放 LICENSE 这类许可文件
    spec = (DESKTOP / "storystudio.spec").read_text(encoding="utf-8")
    assert "from bundle_manifest import notice_datas as _notice_datas" in spec and "datas += _notice_datas(ROOT)" in spec
    assert 'ROOT / "docs"' not in spec, "docs/ 不能按目录进 datas"
    # 声明里是上游许可原文：版权行 + 许可条款（MIT 要求两样都在）
    notices = (ROOT / "docs" / "third-party-notices.md").read_text(encoding="utf-8")
    assert "https://github.com/zenstory-ai/drama-skills" in notices
    assert "Copyright (c) 2026 drama-skills contributors" in notices
    assert "The above copyright notice and this permission notice shall be included in all" in notices
    # 声明文件跟随包数据的出处说法对得上：取自 drama-skills 的题材卡在白名单里，声明也在
    assert "skills/ai-short-film/scripts/genre_cards.json" in MANIFEST.RUNTIME_DATA
    assert "genre_cards.json" in notices


def test_every_module_default_example_is_whitelisted():
    """每个模块「复制示例」默认指到的文件都要随包：不进包就是按钮 404。"""

    from webui.routers.clip import DEFAULT_EXAMPLE
    from webui.routers.imageset import EXAMPLE_DEFAULTS
    from webui.routers.music import EXAMPLE_DEFAULT as MUSIC_EXAMPLE
    from webui.routers.podcast import EXAMPLE_DEFAULT as PODCAST_EXAMPLE
    from webui.routers.script import EXAMPLE_DEFAULT as SCRIPT_EXAMPLE

    wanted = {("clip", DEFAULT_EXAMPLE), ("music", MUSIC_EXAMPLE), ("podcast", PODCAST_EXAMPLE),
              ("script", SCRIPT_EXAMPLE), ("picturebook", "fox_three_socks"), ("episodes", "first_episode")}
    wanted |= set(EXAMPLE_DEFAULTS.items())
    for module, name in sorted(wanted):
        rel = f"examples/{module}/{name}.json"
        assert rel in MANIFEST.RUNTIME_DATA and (ROOT / rel).is_file(), rel


def test_spec_lists_every_source_package_as_hidden_import():
    """src/ 下每个顶层包都要在 hiddenimports 里逐个列出来——新加一个模块包（script、music…）只改 pyproject
    是不够的：wheel 靠 [tool.setuptools.packages.find] 自动发现，PyInstaller 这一处是手写的，漏了就是桌面包里少一个模块。
    只有 studio 例外：它是 Analysis 的入口脚本本身，PyInstaller 自己会分析进去。"""

    spec = (DESKTOP / "storystudio.spec").read_text(encoding="utf-8")
    packages = sorted(p.name for p in (ROOT / "src").iterdir() if (p / "__init__.py").is_file())
    assert len(packages) > 5, packages
    for name in packages:
        if name == "studio":
            continue
        assert f'collect_submodules("{name}")' in spec, name


def test_build_scripts_and_ci_verify_the_bundle():
    """打完包必须跑自检，没过就不出安装包 / 不上传产物。CI 上自检排在冒烟之后：安装包与产物就是那一刻 dist 里的东西。"""

    ps1 = (DESKTOP / "build_windows.ps1").read_text(encoding="utf-8")
    sh = (DESKTOP / "build_macos.sh").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python desktop/verify_bundle.py dist/StoryStudio --ffmpeg desktop/ffmpeg" in ps1 and "$LASTEXITCODE" in ps1
    assert ps1.index("verify_bundle.py") < ps1.index("ISCC.exe"), "自检要在 Inno Setup 之前"
    assert 'desktop/verify_bundle.py "$APP"' in sh and sh.index("verify_bundle.py") < sh.index("hdiutil")
    assert "python desktop/verify_bundle.py dist/StoryStudio --ffmpeg desktop/ffmpeg" in ci
    check = ci.index("verify_bundle.py")
    assert ci.rindex("StoryStudio.exe -ArgumentList") < check < ci.index("ISCC.exe") < ci.index("upload-artifact"), \
        "自检要在两次冒烟之后、Inno Setup 与上传产物之前"
    assert "secrets.BUNDLE_DENYLIST" in ci, "私有词表只从 secret 传进来"


def test_installer_extra_ffmpeg_folder_is_ignored_and_checked():
    """Inno Setup 会把 desktop/ffmpeg/ 整个带进安装包：它不入库，打包前也要过自检。"""

    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^/desktop/ffmpeg/$", ignore, re.M)
    iss = (DESKTOP / "installer.iss").read_text(encoding="utf-8")
    assert 'Source: "ffmpeg\\*"' in iss, "installer.iss 带 ffmpeg 的方式变了，自检的 --ffmpeg 要跟着改"


# ---------------------------------------------------------------------------- 打包后自检（desktop/verify_bundle.py）


def _fake_dist(tmp_path: Path) -> Path:
    """最小的 PyInstaller 单文件夹形状：_internal/base_library.zip + 白名单里的数据文件与许可声明 + 前端静态文件。"""

    dist = tmp_path / "StoryStudio"
    internal = dist / "_internal"
    internal.mkdir(parents=True)
    with zipfile.ZipFile(internal / "base_library.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("os.pyc", b"compiled stdlib")
    for rel in MANIFEST.BUNDLED_FILES:
        (internal / rel).parent.mkdir(parents=True, exist_ok=True)
        (internal / rel).write_text("{}", encoding="utf-8")
    static = internal / "webui" / "static"
    (static / "app" / "assets").mkdir(parents=True)
    for name in MANIFEST.STATIC_TOP:
        (static / name).write_text("<html></html>", encoding="utf-8")
    (static / "app" / "index.html").write_text("<html></html>", encoding="utf-8")
    (static / "app" / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    return dist


def _needles(paths=(), words=()):
    return VERIFY.Needles(paths=tuple(paths), words=tuple((f"词表第 {i} 条", w) for i, w in enumerate(words, 1)))


def test_verify_accepts_a_clean_bundle(tmp_path):
    assert VERIFY.check_files(_fake_dist(tmp_path), _needles(["/nowhere/at/all"], ["zzword"])) == []


@pytest.mark.parametrize("rel,needle", [
    ("_internal/tests/test_api.py", "测试"),
    ("_internal/somepkg/test_helpers.pyc", "测试文件"),
    ("_internal/conftest.py", "测试文件"),
    ("_internal/skills/ai-short-film/scripts/draft_notes.md", "白名单"),
    ("_internal/examples/clip/my_real_project.json", "白名单"),
    ("_internal/docs/DESIGN.md", "白名单"),
    ("_internal/docs/notes/draft.md", "白名单"),
    ("_internal/webui/static/stray.png", "静态文件"),
    ("_internal/webui/static/old/bundle.js", "静态文件"),
    ("_internal/.env", "本机数据"),
    ("_internal/settings.json", "本机数据"),
    ("_internal/ledger.db", "本机数据"),
    ("_internal/logs/server.txt", "日志目录"),
    ("_internal/content/story_video/EP.json", "数据目录"),
    ("_internal/plugins/x/__init__.py", "插件"),
    ("_internal/storystudio_plugin_x-1.0.dist-info/METADATA", "插件"),
    ("_internal/storystudio-plugin-x/adapter.py", "插件"),
])
def test_verify_rejects_tests_stray_data_and_local_files(tmp_path, rel, needle):
    dist = _fake_dist(tmp_path)
    (dist / rel).parent.mkdir(parents=True, exist_ok=True)
    (dist / rel).write_text("x", encoding="utf-8")
    errors = VERIFY.check_files(dist, _needles())
    assert errors and any(needle in e for e in errors), errors


def test_verify_rejects_missing_whitelisted_file(tmp_path):
    dist = _fake_dist(tmp_path)
    (dist / "_internal" / MANIFEST.RUNTIME_DATA[0]).unlink()
    assert any("没进包" in e for e in VERIFY.check_files(dist, _needles()))


def test_verify_accepts_the_notices_and_rejects_a_bundle_without_them(tmp_path):
    """许可声明是白名单的一部分：在包里不算多余文件，少了跟少了数据文件一样让构建失败。"""

    dist = _fake_dist(tmp_path)
    assert VERIFY.check_files(dist, _needles()) == []
    for rel in MANIFEST.NOTICE_FILES:
        (dist / "_internal" / rel).unlink()
    errors = VERIFY.check_files(dist, _needles())
    assert errors and all("没进包" in e for e in errors), errors
    assert {rel for rel in MANIFEST.NOTICE_FILES if any(rel in e for e in errors)} == set(MANIFEST.NOTICE_FILES)


def test_verify_finds_build_machine_paths_in_bytes_and_zip_members(tmp_path):
    dist = _fake_dist(tmp_path)
    machine = str(tmp_path / "builder-home")
    needles = _needles(sorted(VERIFY._path_variants(machine)))
    (dist / "_internal" / "plain.txt").write_text(f"built at {machine.upper()}", encoding="utf-8")
    (dist / "_internal" / "wide.dll").write_bytes(("x" + machine).encode("utf-16-le"))
    with zipfile.ZipFile(dist / "_internal" / "extra.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mod.pyc", machine.encode("utf-8") * 50)
    errors = VERIFY.check_files(dist, needles)
    for name in ("plain.txt", "wide.dll", "extra.zip!mod.pyc"):
        assert any(name in e for e in errors), (name, errors)


def test_verify_never_prints_private_words(tmp_path):
    """CI 日志是公开的，secret 只按整串打码：命中的私有词只报「词表第几条」，词本身不进输出。"""

    dist = _fake_dist(tmp_path)
    (dist / "_internal" / "webui" / "static" / "app" / "assets" / "index-abc123.js").write_text(
        "var brand = 'ZZWORD'; // 甲乙", encoding="utf-8")
    (dist / "_internal" / "examples" / "clip" / "city_morning.json").write_bytes("zzword".encode("utf-16-le"))
    errors = VERIFY.check_files(dist, _needles(words=["zzword", "甲乙"]))
    assert len(errors) == 3, errors
    assert all("zzword" not in e.lower() and "甲乙" not in e for e in errors), errors
    assert any("词表第 1 条" in e and "index-abc123.js" in e for e in errors)
    assert any("词表第 2 条" in e for e in errors) and any("city_morning.json" in e for e in errors)


def test_verify_checks_private_words_only_in_first_party_content(tmp_path):
    """第三方库的字节里什么词都可能有（名称表、词典、清单）：私有词只查本项目自己的文件，
    ASCII 词按整词匹配；打包机路径照旧全包都查。"""

    dist = _fake_dist(tmp_path)
    internal = dist / "_internal"
    (internal / "thirdparty.pyd").write_bytes(b"NAME TABLE: ZZWORD")
    (internal / "somelib").mkdir()
    (internal / "somelib" / "providers.txt").write_text("zzword", encoding="utf-8")
    (internal / "webui" / "static" / "app" / "assets" / "index-abc123.js").write_text("xzzwordx zzwords", encoding="utf-8")
    assert VERIFY.check_files(dist, _needles(words=["zzword"])) == []
    (internal / "genkit").mkdir()
    (internal / "genkit" / "data.json").write_text('{"k": "zzword_x"}', encoding="utf-8")
    errors = VERIFY.check_files(dist, _needles(words=["zzword"]), first_party={"genkit"})
    assert len(errors) == 1 and "genkit/data.json" in errors[0], errors
    machine = str(tmp_path / "builder-home")
    (internal / "somelib" / "providers.txt").write_text(machine, encoding="utf-8")
    errors = VERIFY.check_files(dist, _needles(VERIFY._path_variants(machine)), first_party=set())
    assert len(errors) == 1 and "somelib/providers.txt" in errors[0], errors


def test_verify_needles_come_from_the_environment_not_from_the_repo(monkeypatch, tmp_path):
    """打包机路径运行时从环境里取；私有词只从 BUNDLE_DENYLIST / 未入库的 .release-denylist 来，仓库里不写。"""

    home = tmp_path / "home" / "builder"
    plugins = tmp_path / "private" / "plugin-src"
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("STORYSTUDIO_HOME", raising=False)
    monkeypatch.delenv("STORYSTUDIO_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("STORYSTUDIO_PLUGIN_PATH", str(plugins))
    # 一行一个（CI secret 的存法），也认「|」；两个字的中文词要查，太短的 ASCII 词不查
    monkeypatch.setenv("BUNDLE_DENYLIST", "alpha-word\nab|甲乙\n beta word \n")
    needles = VERIFY.build_needles(tmp_path / "repo")
    paths = {p.lower() for p in needles.paths}
    assert str(home).lower() in paths and str(tmp_path / "repo").lower() in paths and str(plugins).lower() in paths
    words = dict((w, label) for label, w in needles.words)
    assert set(words) == {"alpha-word", "甲乙", "beta word"} and needles.skipped == 1
    assert words["甲乙"] == "BUNDLE_DENYLIST 第 3 条"
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / ".release-denylist").write_text("# 注释\ngamma-word\n", encoding="utf-8")
    labels = {w: label for label, w in VERIFY.build_needles(tmp_path / "repo").words}
    assert labels["gamma-word"] == ".release-denylist 第 2 行"
    monkeypatch.setenv("CI", "true")                  # CI 上主目录是公共 runner 账户，不查
    assert str(home).lower() not in {p.lower() for p in VERIFY.build_needles(tmp_path / "repo").paths}
    assert VERIFY._deep_enough("/root") is False and VERIFY._deep_enough("C:\\Users\\x") is True
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/.release-denylist" in ignore


def test_verify_needles_include_the_main_checkout_of_a_worktree():
    """在 git worktree（或副本）里打包时，主检出目录的路径也要查：本机的可编辑安装多半指着它。"""

    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("没有 git")
    if out.returncode != 0 or not out.stdout.strip():
        pytest.skip("不是 git 检出目录")
    main_checkout = str(Path(out.stdout.strip()).parent).lower()
    assert main_checkout in {p.lower() for p in VERIFY.build_needles(ROOT).paths}


@pytest.mark.parametrize("module,bad", [
    ("tests.test_webui", True), ("conftest", True), ("pkg.conftest", True), ("pkg.test_helpers", True),
    ("_pytest.outcomes", True), ("pytest", True), ("anyio.pytest_plugin", True), ("fastapi.testclient", True),
    ("pkg.tests.helpers", True),
    ("jinja2.tests", False), ("unittest.mock", False), ("webui.app", False), ("storyvideo.contract", False),
])
def test_verify_test_module_names(module, bad):
    assert bool(VERIFY.TEST_MODULE.search(module)) is bad


@pytest.mark.parametrize("module,bad", [
    ("storystudio_plugin_x", True), ("storystudio_plugin_x.adapter", True), ("storystudio_plugin", True),
    ("webui.plugins", False), ("genkit.plugins", False), ("storystudio", False),
])
def test_verify_plugin_module_names(module, bad):
    """插件加载器给插件起的模块名前缀：出现在 PYZ 里就是把某个插件打进了包。"""

    assert bool(VERIFY.PLUGIN_MODULE.search(module)) is bad


def test_verify_checks_the_installer_ffmpeg_folder(tmp_path):
    needles = _needles([str(tmp_path / "builder-home")])
    folder = tmp_path / "ffmpeg"
    assert VERIFY.check_ffmpeg(folder, needles) == []                     # 没有这个目录：跳过
    folder.mkdir()
    for name in ("ffmpeg.exe", "ffprobe.exe", "avcodec-61.dll", "LICENSE.txt", "README.txt"):
        (folder / name).write_bytes(b"MZ")
    assert VERIFY.check_ffmpeg(folder, needles) == []
    (folder / "notes.md").write_text("x", encoding="utf-8")
    (folder / "ffmpeg.exe").write_bytes(str(tmp_path / "builder-home").encode("utf-8"))
    errors = VERIFY.check_ffmpeg(folder, needles)
    assert any("notes.md" in e for e in errors) and any("ffmpeg.exe" in e and "打包机路径" in e for e in errors)


# ---------------------------------------------------------------------------- pip 的 sdist / 元数据


def test_sdist_manifest_prunes_tests_and_non_package_dirs():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    for folder in ("tests", "frontend", "docs", "desktop", "scripts", "examples", "skills"):
        assert re.search(rf"^prune {folder}$", manifest, re.M), folder


def test_sdist_contains_no_tests(tmp_path, monkeypatch):
    """setuptools 默认会把 tests/test*.py 塞进 sdist；MANIFEST.in 把它们剪掉。真打一个 sdist 看。

    在临时副本里打（带一个假测试文件，剪枝规则才真被用上），不往检出目录里写 egg-info。"""

    build_meta = pytest.importorskip("setuptools.build_meta")
    proj = tmp_path / "proj"
    shutil.copytree(ROOT / "src", proj / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "*.pyc"))
    for name in ("pyproject.toml", "README.md", "MANIFEST.in"):
        shutil.copy2(ROOT / name, proj / name)
    (proj / "tests").mkdir()
    (proj / "tests" / "test_dummy.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (proj / "tests" / "conftest.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(proj)
    name = build_meta.build_sdist(str(tmp_path / "out"))
    with tarfile.open(tmp_path / "out" / name) as archive:
        names = archive.getnames()
    top = {n.split("/", 2)[1] for n in names if n.count("/") >= 1}
    assert "src" in top and "pyproject.toml" in top
    assert not {"tests", "frontend", "docs", "desktop", "examples", "skills"} & top, sorted(top)
    assert not [n for n in names if re.search(r"(^|/)(test_[^/]*|conftest)\.py$", n)]


def test_pyproject_metadata_has_no_personal_contact():
    tomllib = pytest.importorskip("tomllib")

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for person in project.get("authors", []) + project.get("maintainers", []):
        assert "email" not in person, person


# ---------------------------------------------------------------------------- 名字 / 图标


def test_macos_bundle_only_on_darwin():
    spec = (DESKTOP / "storystudio.spec").read_text(encoding="utf-8")
    assert 'if _sys.platform == "darwin":' in spec
    assert "StoryStudio.app" in spec


def test_installer_and_bundle_names_follow_branding():
    """产品名唯一来源是 webui/branding.py（DESIGN §38）：安装包的 AppName（开始菜单 / 桌面快捷方式 / 卸载项）与
    mac .app 的显示名都得跟它；改名时曾漏过这两处，用户装完看到的快捷方式名跟窗口标题对不上。"""

    from webui.branding import PRODUCT_NAME

    iss = (DESKTOP / "installer.iss").read_text(encoding="utf-8")
    m = re.search(r'#define AppName "([^"]+)"', iss)
    assert m and m.group(1) == PRODUCT_NAME, m.group(0) if m else "installer.iss 没有 #define AppName"
    assert "#ifndef AppName" in iss, "AppName 应像 AppVersion 一样可由 /DAppName= 覆盖"
    spec = (DESKTOP / "storystudio.spec").read_text(encoding="utf-8")
    assert "from webui.branding import PRODUCT_NAME" in spec and '"CFBundleDisplayName": _product_name' in spec
    assert PRODUCT_NAME in spec, "spec 里读不到 branding 时的兜底字面量也要是新名字"
    for path in DESKTOP.iterdir():
        if path.suffix in (".iss", ".spec", ".md", ".ps1", ".sh", ".py", ".plist"):
            assert "AI 短剧工作台" not in path.read_text(encoding="utf-8"), path.name
    # 旧版单页界面（还在的话）的标题同一个根因
    legacy_path = ROOT / "src" / "webui" / "static" / "index.html"
    if legacy_path.is_file():
        legacy = legacy_path.read_text(encoding="utf-8")
        assert "AI 短剧工作台" not in legacy and f"<title>{PRODUCT_NAME}</title>" in legacy


def test_app_icon_is_wired_everywhere():
    """exe / 安装包 / 开始菜单 / .app / 浏览器标签都要有应用图标：以前 spec 没传 icon=（PyInstaller 默认图标）、
    仓库没有 .ico、/favicon.ico 404。图标由 desktop/make_icon.py 画，两个文件都进仓库。"""

    from PIL import Image

    ico, icns = DESKTOP / "storystudio.ico", DESKTOP / "storystudio.icns"
    assert ico.is_file() and icns.is_file() and (DESKTOP / "make_icon.py").is_file()
    with Image.open(ico) as img:
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(img.info.get("sizes") or ()), img.info
    spec = (DESKTOP / "storystudio.spec").read_text(encoding="utf-8")
    assert "icon=ICON_ICO" in spec and "icon=ICON_ICNS" in spec, "EXE 与 BUNDLE 都要传 icon="
    assert '(ICON_ICO, "desktop")' in spec, "图标要随包进 desktop/，/favicon.ico 路由从那里读"
    iss = (DESKTOP / "installer.iss").read_text(encoding="utf-8")
    assert "SetupIconFile=storystudio.ico" in iss
    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'rel="icon"' in index and "/favicon.ico" in index
    desktop = (ROOT / "src" / "studio" / "desktop.py").read_text(encoding="utf-8")
    assert "webview.start(icon=" in desktop, "开发态 sys.executable 是 python.exe，窗口图标要自己指"
    # 浏览器模式的默认请求：不再 404
    from fastapi.testclient import TestClient

    from webui.app import app

    r = TestClient(app).get("/favicon.ico")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/") and r.content[:4] == b"\x00\x00\x01\x00"
