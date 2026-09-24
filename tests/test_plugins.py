"""插件机制（genkit.plugins，插件接口 v1）：加载、登记进核心的几张表、撤回；坏插件不拦启动；
插件没装上时设置页 / 健康检查 / 路由 / 在飞任务都说人话、不丢东西。

替身插件在 tests/fixtures/plugins/：``example_async``（进程内的异步来源，生图 / 视频 / 配乐三档）和几个故意坏掉的。
核心测试默认不加载任何插件（tests/conftest.py 设 STORYSTUDIO_PLUGINS=off），这里全部显式加载、测完撤回。
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import adapters as adapters_mod
from genkit import capabilities as caps_mod
from genkit import plugins
from genkit import settings as S
from genkit.plugin_sdk import ProviderSetupRequired
from genkit.providers import ImageRequest, MusicRequest, ProviderCommandError, ProviderNotConfigured, ProviderUnavailable, TaskState, VideoRequest
from genkit import router as genkit_router
from genkit.router import RoutedProvider
from storyvideo.runtime import StoryVideoError, StoryVideoRunner
from tests.conftest import PLUGIN_FIXTURES
from tests.test_adapter_contracts import run_contract_case
from tests.test_storyvideo_runtime import fake_media, write_episode  # noqa: F401 - fake_media 是夹具
from webui import humanize, i18n
from webui.app import app

client = TestClient(app)
EN = {"X-StoryStudio-Lang": "en"}
EXAMPLE = ("example_async_images", "example_async_video", "example_async_music")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """临时的用户目录；插件加载开关打开（conftest 默认关着），「插件没装上」的话才是装插件那一句而不是「已关闭」。
    默认目录（临时目录下的 plugins）是空的、环境变量不指别处，所以打开也不会真的加载什么。"""

    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_PLUGINS", "on")
    monkeypatch.delenv("STORYSTUDIO_PLUGIN_PATH", raising=False)
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    for role in ("TEXT", "IMAGE", "VOICE", "VIDEO", "MUSIC"):
        for key in ("ADAPTER", "BASE_URL", "API_KEY", "MODEL", "VOICE"):
            monkeypatch.delenv(f"STORYSTUDIO_{role}_{key}", raising=False)
    return tmp_path / "home"


@pytest.fixture
def project(home, tmp_path, monkeypatch):
    """接口写项目文件的地方指到临时目录（配乐项目、回收站），走真实的 build_provider()。"""

    from webui import deps
    from webui.routers import music as music_router

    root = tmp_path / "project"
    (root / "content" / "music").mkdir(parents=True)
    monkeypatch.setattr(deps, "PROJECT_ROOT", root)
    monkeypatch.setattr(music_router, "PROVIDER_FACTORY", None)
    return root


def _tables() -> dict:
    return copy.deepcopy({
        "registry": {r: sorted(v) for r, v in adapters_mod.REGISTRY.items()},
        "adapters": S.ADAPTERS,
        "caps": sorted(caps_mod.CAPABILITIES),
        "local": sorted(caps_mod.LOCAL_ADAPTERS),
        "prefixes": dict(adapters_mod.ASYNC_PREFIXES),
    })


def _save(home: Path, roles: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(json.dumps({"roles": roles}, ensure_ascii=False), encoding="utf-8")


def _write_plugin(folder: Path, body: str, manifest: dict | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text(body, encoding="utf-8")
    if manifest is not None:
        (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------- 加载与撤回


def test_example_plugin_registers_everything_and_unloads_cleanly(load_plugin):
    before = _tables()
    generation = plugins.generation()
    record = load_plugin("example_async")
    assert record.status == "loaded" and record.version == "0.1.0" and record.message == ""
    assert record.adapters == {"image": ["example_async_images"], "video": ["example_async_video"], "music": ["example_async_music"]}
    assert record.prefixes == ["exasync-img:", "exasync-vid:", "exasync-mus:"]
    assert plugins.generation() > generation
    for role, adapter_id in zip(("image", "video", "music"), EXAMPLE):
        assert adapter_id in adapters_mod.REGISTRY[role]
        meta = S.ADAPTERS[role][adapter_id]
        assert meta["plugin"] == "example_async" and meta["fields"] == ["model"] and meta["defaults"]["model"].startswith("demo-")
        assert S.adapter_spec(role, adapter_id) is meta
    assert adapters_mod.ASYNC_PREFIXES["exasync-vid:"] == "example_async_video"
    assert adapters_mod.adapter_for_task_id("exasync-mus:0001") == ("exasync-mus:", "example_async_music")
    assert ("music", "example_async_music", "") in caps_mod.CAPABILITIES
    assert plugins.owner_of_adapter("video", "example_async_video") is record
    assert plugins.owner_of_prefix("exasync-img:") is record
    public = record.public()
    assert public["name"] == "example_async" and public["status"] == "loaded" and "module" not in public
    plugins.unload_all()
    assert _tables() == before
    assert plugins.records() == [] and adapters_mod.adapter_for_task_id("exasync-mus:0001") is None


def test_contract_fixtures_of_the_example_plugin(tmp_path, load_plugin):
    assert load_plugin("example_async").status == "loaded"
    cases = sorted((PLUGIN_FIXTURES / "example_async" / "contract").glob("*.json"))
    assert [c.stem for c in cases] == ["example_async_images", "example_async_music", "example_async_video"]
    for n, case in enumerate(cases):
        folder = tmp_path / str(n)
        folder.mkdir()
        run_contract_case(json.loads(case.read_text(encoding="utf-8")), folder)


def test_routed_provider_submits_polls_and_downloads_through_the_plugin(tmp_path, home, load_plugin):
    assert load_plugin("example_async").status == "loaded"
    st = S.Settings(cache_dir=str(tmp_path / "cache"))
    for role, adapter_id in zip(("image", "video", "music"), EXAMPLE):
        st.role(role).adapter = adapter_id
        assert S.validate_role(st, role) == [] and S.role_missing_message(st, role) is None
    st.role("video").extra = {"polls_before_done": 1}
    provider = RoutedProvider(st)
    sub = provider.submit(ImageRequest(name="S1", prompt="窗前", model="@image"))
    assert sub.task_id == "exasync-img:0001" and sub.status is TaskState.PENDING
    assert provider.submission_source(ImageRequest(name="S1", prompt="窗前", model="@image")) == ("example_async_images", "demo-image")
    snap = provider.get_task(sub.task_id, "image")
    assert snap.status is TaskState.FINISHED
    out = provider.download(snap.output_urls[0], tmp_path / "out" / "S1.png")
    assert out.read_bytes().startswith(b"\x89PNG")
    video = provider.submit(VideoRequest(name="S1", prompt="转头", duration=4, model="@video"))
    assert provider.get_task(video.task_id, "video").status is TaskState.PROCESSING
    assert provider.get_task(video.task_id, "video").status is TaskState.FINISHED
    assert provider.submit(MusicRequest(name="bgm", prompt="摇篮曲")).task_id.startswith("exasync-mus:")
    # 「测试连接」走适配器自己的 ping
    _save(home, {"image": {"adapter": "example_async_images"}})
    assert genkit_router.test_role("image") == "example-async ready; using demo-image"


def test_settings_page_lists_plugin_sources_and_translates_them(home, load_plugin):
    assert load_plugin("example_async").status == "loaded"
    body = client.get("/api/settings").json()
    row = body["adapters"]["image"]["example_async_images"]
    assert row["plugin"] == "example_async" and row["label"] == "示例异步生图（测试替身，不联网）"
    assert "openai_images" in body["adapters"]["image"] and "example_async_music" in body["adapters"]["music"]
    assert [p["name"] for p in body["plugins"]] == ["example_async"] and body["unavailable"] == {}
    assert body["plugin_dir"].endswith("plugins")
    en = client.get("/api/settings", headers=EN).json()
    assert en["adapters"]["image"]["example_async_images"]["label"] == "Example async images (test double, offline)"
    assert en["adapters"]["music"]["example_async_music"]["hint"] == "Text to music; reference images must be jpg / png"
    # PUT 接受插件来源
    r = client.put("/api/settings", json={"roles": {"music": {"adapter": "example_async_music", "model": "demo-music"}}})
    assert r.status_code == 200 and r.json()["status"]["music"] == [] and r.json()["unavailable"] == {}
    health = client.get("/api/health").json()
    assert health["plugins"][0]["status"] == "loaded" and health["roles"]["music"]["configured"] is True
    assert health["roles"]["music"]["sources"] == 1 and health["roles"]["music"]["unavailable"] == ""


def test_plugin_i18n_and_failure_hints_merge_and_leave_with_the_plugin(load_plugin):
    text = "找不到示例命令 demo-cli：示例来源需要本机装好它"
    i18n._compiled = None
    assert i18n.tr(text, "en") == text
    assert humanize.classify_failure(f"submission rejected for frame-S1: {text}")["kind"] == "other"
    assert load_plugin("example_async").status == "loaded"
    assert i18n.tr(text, "en") == "Example command demo-cli not found: the example source needs it installed on this machine"
    assert humanize.classify_failure(f"submission rejected for frame-S1: {text}")["kind"] == "config"
    assert humanize.classify_failure("QuotaExhausted: 示例来源的额度用完了：x")["kind"] == "quota"
    # 核心文案不受影响
    assert i18n.tr("未配置", "en") == "not configured"
    plugins.unload_all()
    assert i18n.tr(text, "en") == text
    assert humanize.classify_failure(f"submission rejected for frame-S1: {text}")["kind"] == "other"


def test_core_exact_translations_win_over_plugin_ones(tmp_path):
    folder = _write_plugin(tmp_path / "hijack", (
        "STORYSTUDIO_API = 1\n"
        "def register(api):\n"
        "    api.add_i18n(exact={'未配置': 'HIJACKED', '只在插件里的一句': 'plugin-only line'})\n"))
    try:
        assert plugins.load_from(folder).status == "loaded"
        assert i18n.tr("未配置", "en") == "not configured"
        assert i18n.tr("只在插件里的一句", "en") == "plugin-only line"
    finally:
        plugins.unload_all()


def test_setup_required_is_a_config_failure_and_gateway_down_stays_timeout(tmp_path, load_plugin):
    assert load_plugin("example_async").status == "loaded"
    st = S.Settings(cache_dir=str(tmp_path / "cache"))
    st.role("image").adapter = "example_async_images"
    st.role("image").extra = {"missing_tool": "demo-cli"}
    with pytest.raises(ProviderSetupRequired) as info:
        RoutedProvider(st).submit(ImageRequest(name="S1", prompt="x", model="@image"))
    assert isinstance(info.value, ProviderUnavailable)
    assert humanize.classify_failure(f"{type(info.value).__name__}: {info.value}")["kind"] == "config"
    assert humanize.classify_failure("ProviderUnavailable: gateway down")["kind"] == "timeout"


# ---------------------------------------------------------------------------- 坏插件


@pytest.mark.parametrize("name, needle", [
    ("broken_import", "插件 broken_import 加载失败：ModuleNotFoundError"),
    ("broken_register", "插件 broken_register 加载失败：RuntimeError: register blew up halfway"),
    ("conflict_builtin", "插件 conflict_builtin 想登记的来源 openai_images 已经存在（内置），整个插件没有加载"),
    ("api_mismatch", "插件 api_mismatch 需要插件接口 v2，本程序提供 v1——更新插件或程序"),
    ("bad_prefix", "插件 bad_prefix 加载失败：task prefix 'file:'"),
    ("unanchored_i18n", "插件 unanchored_i18n 加载失败：i18n pattern must be anchored"),
    ("system_exit", "插件 system_exit 加载失败：SystemExit: 3"),
])
def test_broken_plugins_are_refused_whole_and_explained(name, needle, load_plugin, home):
    before = _tables()
    record = load_plugin(name)
    assert record.status == "failed" and needle in record.message, record.message
    assert record.error
    assert _tables() == before                                      # 核心表里一条都没留下（登记一半的也撤回）
    assert not any(k.startswith(record.module) for k in sys.modules)
    health = client.get("/api/health")
    assert health.status_code == 200
    row = health.json()["plugins"][0]
    assert row["name"] == name and row["status"] == "failed" and needle in row["message"]
    en = client.get("/api/health", headers=EN).json()["plugins"][0]["message"]
    assert en.startswith("Plugin ") and "加载失败" not in en and "需要插件接口" not in en, en


def test_a_conflicting_plugin_leaves_the_builtin_source_working(load_plugin):
    builtin = adapters_mod.REGISTRY["image"]["openai_images"]
    assert load_plugin("conflict_builtin").status == "failed"
    assert adapters_mod.REGISTRY["image"]["openai_images"] is builtin and "plugin" not in S.ADAPTERS["image"]["openai_images"]
    assert "conflict_extra_images" not in adapters_mod.REGISTRY["image"]


def test_two_plugins_cannot_claim_the_same_source_or_prefix(tmp_path, load_plugin):
    assert load_plugin("example_async").status == "loaded"
    same_id = _write_plugin(tmp_path / "copycat", (
        "STORYSTUDIO_API = 1\n"
        "def register(api):\n"
        "    api.add_adapter('image', 'example_async_images', lambda c, **k: None, label='x')\n"))
    record = plugins.load_from(same_id)
    assert record.status == "failed" and "已经存在（插件 example_async）" in record.message
    overlap = _write_plugin(tmp_path / "overlap", (
        "STORYSTUDIO_API = 1\n"
        "def register(api):\n"
        "    api.add_adapter('video', 'overlap_video', lambda c, **k: None, label='x')\n"
        "    api.add_async_prefix('exasync-vid-x:', 'overlap_video')\n"))
    assert plugins.load_from(overlap).status == "loaded"          # 前缀只以冒号结尾，不同的前缀互不遮挡：合法
    clash = _write_plugin(tmp_path / "clash", (
        "STORYSTUDIO_API = 1\n"
        "def register(api):\n"
        "    api.add_adapter('video', 'clash_video', lambda c, **k: None, label='x')\n"
        "    api.add_async_prefix('exasync-img:', 'clash_video')\n"))
    record = plugins.load_from(clash)
    assert record.status == "failed" and "clashes with existing prefix 'exasync-img:'" in record.message
    assert "clash_video" not in adapters_mod.REGISTRY["video"] and adapters_mod.ASYNC_PREFIXES["exasync-img:"] == "example_async_images"


# ---------------------------------------------------------------------------- 在哪里找、开关


def test_discovery_switch_path_and_disabled_folders(tmp_path, monkeypatch):
    parent = tmp_path / "many"
    _write_plugin(parent / "alpha", "STORYSTUDIO_API = 1\ndef register(api):\n    pass\n")
    _write_plugin(parent / "beta.disabled", "STORYSTUDIO_API = 1\ndef register(api):\n    pass\n")
    _write_plugin(parent / "_private", "STORYSTUDIO_API = 1\ndef register(api):\n    pass\n")
    (parent / "data").mkdir()                                     # 随手放的数据目录：不算插件
    (parent / "tools").mkdir()
    (parent / "tools" / "scan.py").write_text("print('not a plugin')\n", encoding="utf-8")
    (parent / "zeta").mkdir()                                     # 只有清单、缺 __init__.py：算插件，报清楚缺什么
    (parent / "zeta" / "plugin.json").write_text(json.dumps({"name": "zeta"}), encoding="utf-8")
    single = _write_plugin(tmp_path / "gamma", "STORYSTUDIO_API = 1\n__version__ = '2.0'\ndef register(api):\n    pass\n")
    dup = _write_plugin(tmp_path / "again" / "alpha", "STORYSTUDIO_API = 1\ndef register(api):\n    pass\n")
    _write_plugin(tmp_path / "home" / "plugins" / "delta", "STORYSTUDIO_API = 1\ndef register(api):\n    pass\n")
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_PLUGIN_PATH", os.pathsep.join(str(p) for p in (parent, single, dup.parent)))
    try:
        plugins.reset()
        plugins.ensure_loaded()                       # conftest 关着：一个都不加载
        assert plugins.records() == []
        monkeypatch.setenv("STORYSTUDIO_PLUGINS", "on")
        plugins.reset()
        plugins.ensure_loaded()
        rows = [(r.name, r.status) for r in plugins.records()]
        # 默认目录（STORYSTUDIO_HOME/plugins）在前，环境变量里的按顺序；.disabled / _ 开头的跳过；同名只认第一个；
        # 既没有 __init__.py 也没有 plugin.json 的子目录不进记录
        assert rows == [("delta", "loaded"), ("alpha", "loaded"), ("zeta", "failed"), ("gamma", "loaded"), ("alpha", "skipped")]
        assert "zeta 的目录里没有 __init__.py" in plugins.records()[2].message
        assert plugins.records()[3].version == "2.0"
        assert "已经从" in plugins.records()[4].message
        plugins.ensure_loaded()                       # 幂等
        assert len(plugins.records()) == 5
    finally:
        plugins.reset()


def test_plugins_are_never_read_from_settings_or_the_project_root(tmp_path, monkeypatch, home):
    """设置文件里写个 plugins 键、项目数据根里放个 plugins 目录都不算：只认用户主目录与环境变量。"""

    project = tmp_path / "project"
    _write_plugin(project / "plugins" / "sneaky", "STORYSTUDIO_API = 1\ndef register(api):\n    raise SystemExit('should not load')\n")
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(json.dumps({"plugins": [str(project / "plugins")], "roles": {}}), encoding="utf-8")
    monkeypatch.setenv("STORYSTUDIO_PROJECT_ROOT", str(project))
    monkeypatch.setenv("STORYSTUDIO_PLUGINS", "on")
    monkeypatch.delenv("STORYSTUDIO_PLUGIN_PATH", raising=False)
    try:
        plugins.reset()
        plugins.ensure_loaded()
        assert plugins.records() == []
        assert plugins.plugin_dirs() == [home / "plugins"]
    finally:
        plugins.reset()


# ---------------------------------------------------------------------------- 插件没装上


def test_missing_plugin_roles_explain_themselves(home, project):
    _save(home, {"image": {"adapter": "example_async_images", "model": "demo-image", "extra": {"k": "v"}},
                 "music": {"adapter": "example_async_music"}})
    st = S.load_settings()
    assert st.role("image").adapter == "example_async_images" and st.role("image").extra == {"k": "v"}   # 读设置不改写
    assert S.validate_role(st, "image") == ["image: 来源 example_async_images 没有装上"]
    message = S.role_missing_message(st, "image")
    assert message.startswith("「生图」的来源 example_async_images 现在用不了：它不是内置来源，提供它的插件没有加载——把插件放进 ")
    assert message.endswith("后重启程序，或去「模型设置」给「生图」换一个来源")
    # 这句会进账本和 400：目录只给不带本机用户名的写法
    assert "把插件放进 <STORYSTUDIO_HOME>/plugins 后重启程序" in message and str(home) not in message
    with pytest.raises(ProviderNotConfigured, match="现在用不了"):
        RoutedProvider(st).adapter("image")
    body = client.get("/api/settings").json()
    assert body["unavailable"]["image"]["adapter"] == "example_async_images" and "现在用不了" in body["unavailable"]["image"]["message"]
    assert body["status"]["image"] == ["image: 来源 example_async_images 没有装上"]
    assert "example_async_images" not in body["adapters"]["image"]
    health = client.get("/api/health").json()["roles"]
    assert health["image"]["configured"] is False and "现在用不了" in health["image"]["unavailable"]
    assert health["music"]["sources"] == 0
    en = client.get("/api/settings", headers=EN).json()
    assert en["unavailable"]["image"]["message"].startswith("The image source example_async_images is unavailable")
    assert en["status"]["image"] == ["image: source example_async_images is not installed"]
    en_health = client.get("/api/health", headers=EN).json()["roles"]["music"]["unavailable"]
    assert en_health.startswith("The music source example_async_music is unavailable")
    # 生成类接口在起任务之前 400，话里说清是插件没装上
    sid = client.post("/api/music", json={"title": "摇篮曲", "brief": {"usage": "睡前", "genre": "摇篮曲"}}).json()["id"]
    try:
        r = client.post(f"/api/music/{sid}/generate", json={"count": 1})
        assert r.status_code == 400 and r.json()["detail"].startswith("「配乐」的来源 example_async_music 现在用不了")
    finally:
        client.delete(f"/api/music/{sid}")
    # 设置文件原样：读、查、拦都不改写它
    assert json.loads((home / "settings.json").read_text(encoding="utf-8"))["roles"]["image"]["adapter"] == "example_async_images"


def test_missing_plugin_reason_comes_from_the_failed_plugin_manifest(home, load_plugin):
    assert load_plugin("broken_import").status == "failed"
    _save(home, {"video": {"adapter": "broken_video"}})
    message = S.role_missing_message(S.load_settings(), "video")
    assert message.startswith("「视频」的来源 broken_video 现在用不了：提供它的插件没能加载（插件 broken_import 加载失败：ModuleNotFoundError: ")
    assert message.endswith("）——修好插件后重启程序，或去「模型设置」给「视频」换一个来源") and "把插件放进" not in message
    en = client.get("/api/settings", headers=EN).json()["unavailable"]["video"]["message"]
    assert not any("一" <= ch <= "鿿" for ch in en), en
    assert en.startswith("The video source broken_video is unavailable: the plugin that provides it failed to load (Plugin broken_import")
    with pytest.raises(ProviderUnavailable, match="前缀 broken-vid:）（插件 broken_import 加载失败"):
        RoutedProvider(S.load_settings()).get_task("broken-vid:123", "video")


def test_put_keeps_a_stored_missing_source_and_refuses_new_unknown_ones(home):
    _save(home, {"image": {"adapter": "example_async_images", "model": "demo-image", "api_key": "sk-keep-this-key-1234"}})
    r = client.put("/api/settings", json={"roles": {"image": {"adapter": "example_async_images", "model": "demo-image-2"}}})
    assert r.status_code == 200, r.text
    on_disk = json.loads((home / "settings.json").read_text(encoding="utf-8"))["roles"]["image"]
    assert on_disk["adapter"] == "example_async_images" and on_disk["model"] == "demo-image-2"
    assert on_disk["api_key"] == "sk-keep-this-key-1234"                   # 来源没变：密钥不清
    assert r.json()["unavailable"]["image"]["adapter"] == "example_async_images"
    bad = client.put("/api/settings", json={"roles": {"image": {"adapter": "other_missing_images"}}})
    assert bad.status_code == 400
    assert bad.json()["detail"] == "「生图」没有来源 other_missing_images：它不是内置来源，提供它的插件也没加载——没有保存"
    en = client.put("/api/settings", json={"roles": {"image": {"adapter": "other_missing_images"}}}, headers=EN)
    assert en.json()["detail"].startswith("The image role has no source other_missing_images")
    on_disk = json.loads((home / "settings.json").read_text(encoding="utf-8"))["roles"]["image"]
    assert on_disk["adapter"] == "example_async_images"


def test_unregistered_task_prefix_says_which_source_is_missing(tmp_path, home, monkeypatch):
    st = S.Settings(cache_dir=str(tmp_path / "cache"))
    provider = RoutedProvider(st)
    with pytest.raises(ProviderUnavailable) as info:
        provider.get_task("exasync-vid:0001", "video")
    assert str(info.value) == ("任务 exasync-vid:0001 是一个没加载的来源提交的（前缀 exasync-vid:），查不了进度——"
                               "装好提供它的插件并重启后再刷新；任务留在账本里，不会被当成失败")
    assert i18n.tr(str(info.value), "en").startswith("Task exasync-vid:0001 was submitted by a source that is not loaded")
    # 插件加载关着（--no-plugins）：括号里说清是关掉了，而不是没装
    monkeypatch.setenv("STORYSTUDIO_PLUGINS", "off")
    with pytest.raises(ProviderUnavailable) as info:
        provider.get_task("exasync-vid:0001", "video")
    assert "（前缀 exasync-vid:）（插件加载已关闭：STORYSTUDIO_PLUGINS=off 或桌面启动器的 --no-plugins）" in str(info.value)
    en = i18n.tr(str(info.value), "en")
    assert "plugin loading is turned off" in en and not any("一" <= ch <= "鿿" for ch in en), en
    with pytest.raises(ProviderCommandError, match="unknown task id scheme"):
        provider.get_task("no-scheme-at-all", "video")


def test_in_flight_task_waits_while_its_plugin_is_missing(tmp_path, home, fake_media):
    """在飞任务的来源插件没加载：轮询报清楚原因，任务保持在飞（不记失败、账本不动）；装回插件后接着查、落地。"""

    path = write_episode(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["cast"]["su"].pop("asset")
    value["image_params"] = {"model": "@image"}
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    st = S.Settings(cache_dir=str(tmp_path / "cache"))
    st.role("image").adapter = "example_async_images"
    st.role("image").extra = {"polls_before_done": 2}
    folder = PLUGIN_FIXTURES / "example_async"
    try:
        assert plugins.load_from(folder).status == "loaded"
        with StoryVideoRunner(tmp_path, provider=RoutedProvider(st)) as runner:
            runner.run("EP99", wait=False)
            job = runner.store.get_job("EP99", "frame-S1")
            assert job["task_id"] == "exasync-img:0001" and job["status"] == "processing"
            assert (job["provider"], job["model"]) == ("example_async_images", "demo-image")
        plugins.unload_all()
        with StoryVideoRunner(tmp_path, provider=RoutedProvider(st)) as runner:
            with pytest.raises(StoryVideoError, match="没加载的来源提交的") as info:
                runner.poll("EP99")
            assert "查询 frame-S1 的进度失败" in humanize.humanize(str(info.value))
            job = runner.store.get_job("EP99", "frame-S1")
            assert job["status"] == "processing" and not job.get("error") and job["task_id"] == "exasync-img:0001"
            assert runner.store.snapshot("EP99")["ambiguous_submissions"] == []
        assert plugins.load_from(folder).status == "loaded"
        with StoryVideoRunner(tmp_path, provider=RoutedProvider(st)) as runner:
            for _ in range(3):
                runner.poll("EP99")
                if runner.store.get_job("EP99", "frame-S1")["status"] == "succeeded":
                    break
            assert runner.store.get_job("EP99", "frame-S1")["status"] == "succeeded"
    finally:
        plugins.unload_all()


def test_music_generate_says_the_role_needs_a_plugin(project):
    body = {"title": "摇篮曲", "brief": {"usage": "睡前", "genre": "摇篮曲"}, "duration": 60, "count": 1}
    sid = client.post("/api/music", json=body).json()["id"]
    try:
        r = client.post(f"/api/music/{sid}/generate", json={"count": 1})
        assert r.status_code == 400
        assert r.json()["detail"] == "「配乐」目前没有可选的来源：内置来源不覆盖这一档，要装一个提供它的插件，装好后在「模型设置」里选上它"
        en = client.post(f"/api/music/{sid}/generate", json={"count": 1}, headers=EN).json()["detail"]
        assert en.startswith("There is no music source to choose yet")
    finally:
        client.delete(f"/api/music/{sid}")


def test_disabled_plugins_say_so_instead_of_asking_to_install(home, monkeypatch):
    """插件加载关着（STORYSTUDIO_PLUGINS=off / --no-plugins）：不叫人「把插件放进目录」，说清是关掉了。"""

    monkeypatch.setenv("STORYSTUDIO_PLUGINS", "off")
    _save(home, {"image": {"adapter": "example_async_images"}})
    message = S.role_missing_message(S.load_settings(), "image")
    assert message == ("「生图」的来源 example_async_images 现在用不了：它不是内置来源，提供它的插件没有加载"
                       "（插件加载已关闭：STORYSTUDIO_PLUGINS=off 或桌面启动器的 --no-plugins）——去掉这个开关后重启程序，"
                       "或去「模型设置」给「生图」换一个来源")
    en = client.get("/api/settings", headers=EN).json()["unavailable"]["image"]["message"]
    assert en == ("The image source example_async_images is unavailable: it is not built in and the plugin that provides it "
                  "is not loaded (plugin loading is turned off: STORYSTUDIO_PLUGINS=off or the desktop launcher's --no-plugins) — "
                  "remove that switch and restart the program, or pick another image source in Model settings")


def test_long_or_path_bearing_failure_reasons_are_capped_and_portable(tmp_path, home):
    """嵌进整句的插件失败原因：截到 200 字（整句尾巴不被「测试连接」的截断吃掉，英文规则照样命中）；
    用户目录下的路径收成不带用户名的写法。"""

    loud = _write_plugin(tmp_path / "loud", "STORYSTUDIO_API = 1\ndef register(api):\n    raise RuntimeError('x' * 2000)\n",
                         manifest={"provides": {"adapters": {"image": ["loud_images"]}}})
    bare = home / "plugins" / "bare"
    bare.mkdir(parents=True)
    (bare / "plugin.json").write_text(json.dumps({"provides": {"adapters": {"video": ["bare_video"]}}}), encoding="utf-8")
    try:
        loud_record = plugins.load_from(loud)
        assert loud_record.status == "failed" and len(loud_record.message) > plugins.REASON_LIMIT
        record = plugins.load_from(bare)
        assert record.status == "failed" and str(bare) in record.message       # 记录里是原样（本机界面看）
        assert len(plugins.failure_reason(loud_record)) == plugins.REASON_LIMIT
        _save(home, {"image": {"adapter": "loud_images"}, "video": {"adapter": "bare_video"}})
        st = S.load_settings()
        message = S.role_missing_message(st, "image")
        assert "…）——修好插件后重启程序，或去「模型设置」给「生图」换一个来源" in message and len(message) < 600
        en = i18n.tr(message, "en")
        assert en.startswith("The image source loud_images is unavailable: the plugin that provides it failed to load "
                             "(Plugin loud failed to load: RuntimeError: xxx") and en.endswith("or pick another image source in Model settings")
        video = S.role_missing_message(st, "video")
        assert "插件 bare 的目录里没有 __init__.py：<STORYSTUDIO_HOME>/plugins/bare）" in video and str(home) not in video
    finally:
        plugins.unload_all()


def test_portable_text_hides_the_home_folder(tmp_path, monkeypatch):
    fake_home = tmp_path / "Users" / "someone"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.delenv("STORYSTUDIO_HOME", raising=False)
    assert plugins.display_dir() == "~/.storystudio/plugins"
    text = f"插件 demo 的目录里没有 __init__.py：{fake_home / 'plugins' / 'demo'}"
    assert plugins.portable_text(text) == "插件 demo 的目录里没有 __init__.py：~/plugins/demo"
    sibling = str(tmp_path / "Users" / "someone-else" / "x")                   # 只是前缀相同的别的目录：不动
    assert plugins.portable_text(sibling) == sibling
    other = str(tmp_path / "elsewhere" / "demo")
    assert plugins.portable_text(f"（{other}）") == f"（{other}）"
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "data" / "ss"))
    assert plugins.display_dir() == "<STORYSTUDIO_HOME>/plugins"


def test_history_protocols_keep_match_remote_optional():
    """只有 list_tasks 的提供方照样算「会查历史」；身份规则 match_remote 是另一个可选能力（插件接口里两个都有）。"""

    from genkit import plugin_sdk

    class HistoryOnly:
        def list_tasks(self, **kwargs):
            return ()

    class HistoryAndRule(HistoryOnly):
        def match_remote(self, item, *, expected_name, task_type, task_id=None):
            return True

    assert isinstance(HistoryOnly(), plugin_sdk.TaskHistoryProvider)
    assert not isinstance(HistoryOnly(), plugin_sdk.RemoteIdentityMatcher)
    assert isinstance(HistoryAndRule(), plugin_sdk.TaskHistoryProvider)
    assert isinstance(HistoryAndRule(), plugin_sdk.RemoteIdentityMatcher)
