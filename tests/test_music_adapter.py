"""配乐这一档的核心约定（DESIGN §44，docs/music-spec.md §1）：内置来源不覆盖配乐，来源由插件登记；
注册表 / 设置表 / 能力表 / 路由前缀分派、不会出音乐的适配器抛 ``ProviderUnsupported``、``MusicRequest`` 的校验。
插件一律用测试用的示例插件 tests/fixtures/plugins/example_async（进程内的异步来源，不联网）。"""

from __future__ import annotations

import pytest

from genkit import settings as S
from genkit.adapters import REGISTRY
from genkit.adapters.base import local_token
from genkit.capabilities import lookup
from genkit.providers import MusicRequest, ProviderCommandError, ProviderUnsupported, TaskState
from genkit.router import RoutedProvider

JPG = b"\xff\xd8\xff\xe0" + b"0" * 40


def test_music_role_has_no_builtin_source():
    """开源核心不带配乐来源：表里这一档是空的，没选来源时的提示说「要装插件」，而不是「去选一个来源」。"""

    assert "music" in S.ROLES and REGISTRY["music"] == {} and S.ADAPTERS["music"] == {}
    st = S.Settings()
    assert S.validate_role(st, "music") == ["music: 没选适配器"]
    assert S.role_missing_message(st, "music") == "「配乐」目前没有可选的来源：内置来源不覆盖这一档，要装一个提供它的插件，装好后在「模型设置」里选上它"


def test_registry_settings_capabilities_and_router(tmp_path, monkeypatch, load_plugin):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    record = load_plugin("example_async")
    assert record.status == "loaded", record.message
    A = __import__(f"{record.module}.adapter", fromlist=["adapter"])
    assert REGISTRY["music"]["example_async_music"] is A.build_music
    assert S.ADAPTERS["music"]["example_async_music"]["fields"] == ["model"]
    assert S.ADAPTERS["music"]["example_async_music"]["plugin"] == "example_async"
    caps = lookup("music", "example_async_music", "demo-music")
    assert caps["known"] is True and caps["source"] == "doc"
    assert caps["counts"] == {"min": 1, "max": 2} and caps["max_reference_images"] == 2
    # 路由：music 角色 + 插件登记的前缀
    st = S.Settings(cache_dir=str(tmp_path / "cache"))
    st.role("music").adapter = "example_async_music"
    assert S.role_missing_message(st, "music") is None
    provider = RoutedProvider(st)
    sub = provider.submit_music(MusicRequest(name="bgm", prompt="x"))
    assert sub.task_id.startswith("exasync-mus:") and sub.status is TaskState.PENDING
    done = provider.get_task(sub.task_id, "music")
    assert done.status is TaskState.FINISHED and done.output_urls[0].startswith("file:")
    assert provider.submit(MusicRequest(name="bgm", prompt="x")).task_id.startswith("exasync-mus:")


def test_music_references_are_encoded_not_sent_as_paths(tmp_path, load_plugin):
    record = load_plugin("example_async")
    A = __import__(f"{record.module}.adapter", fromlist=["adapter"])
    ref = tmp_path / "cover.jpg"
    ref.write_bytes(JPG)
    adapter = A.build_music({"model": "demo-music"}, cache_dir=tmp_path / "cache")
    adapter.submit_music(MusicRequest(name="bgm", prompt="按这张画的气质写一段配乐", reference_asset_ids=(local_token(ref),)))
    state = (tmp_path / "cache" / A.STATE_FILE).read_text(encoding="utf-8")
    assert "local:" not in state and "cover.jpg" not in state and "asset-" in state
    bad = tmp_path / "cover.webp"
    bad.write_bytes(JPG)
    with pytest.raises(ProviderCommandError, match="jpg"):
        adapter.submit_music(MusicRequest(name="bgm", prompt="x", reference_asset_ids=(local_token(bad),)))


def test_unsupported_adapter_says_so(tmp_path, load_plugin):
    """不会出音乐的适配器（生图 / 视频）抛 ProviderUnsupported，而不是一个莫名其妙的 AttributeError。"""

    record = load_plugin("example_async")
    A = __import__(f"{record.module}.adapter", fromlist=["adapter"])
    images = A.build_images({"model": "x"}, cache_dir=tmp_path)
    with pytest.raises(ProviderUnsupported) as info:
        images.submit_music(MusicRequest(name="bgm", prompt="x"))
    assert "配乐" in str(info.value)
    st = S.Settings()
    st.role("music").adapter = "example_async_music"
    provider = RoutedProvider(st, adapters={"music": A.build_video({"model": "x"}, cache_dir=tmp_path)})
    with pytest.raises(ProviderUnsupported):
        provider.submit_music(MusicRequest(name="bgm", prompt="x"))


def test_music_request_validates_count_and_duration():
    with pytest.raises(ValueError):
        MusicRequest(name="bgm", prompt="x", count=5)
    with pytest.raises(ValueError):
        MusicRequest(name="bgm", prompt="x", count=0)
    with pytest.raises(ValueError):
        MusicRequest(name="bgm", prompt="x", duration=0)
    with pytest.raises(ValueError):
        MusicRequest(name="bgm", prompt="  ")
    assert MusicRequest(name="bgm", prompt="x", count=4).count == 4
