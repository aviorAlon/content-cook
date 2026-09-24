"""发布前安全自查（2026-09-23）·文件与路径边界：删除 / 恢复只搬项目自己的目录（PB-1 / PB-2）、项目文件里的本地文件
路径只认数据目录（PB-3 / PB-4 / PB-5）、角色 / 变体 id 不能带路径分隔符（PB-6）、404 文案不带本机绝对路径（PB-8）。

所有「目录外」目标都是 tmp_path 下与数据根并列的 outside/，不碰真实数据根、不碰 ~/.storystudio、不调任何付费模型。
"""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from storyvideo.runtime import StoryVideoError, StoryVideoRunner
from storyvideo.spec import SpecError, normalize_spec, project_file
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import export as export_router
from webui.routers import post as post_router
from webui.routers import project as project_router
from webui.routers import scripts as scripts_router
from webui.routers import series as series_router

client = TestClient(app)
WINDOWS = os.name == "nt"


def _png(path: Path) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), (10, 20, 30)).save(path, "PNG")
    return path


def _png_base64() -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """数据根 = tmp/root；目录外 = tmp/outside（与数据根并列）；~ 只展开到 tmp/fakehome。"""

    root = tmp_path / "root"
    content = root / "content" / "story_video"
    content.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    for module in (deps, episodes_router, project_router, post_router, export_router, series_router, scripts_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", root)
    for module in (deps, episodes_router, scripts_router):
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content)
    monkeypatch.setattr(episodes_router, "DELETED_DIR", root / ".webui_deleted")
    # scripts 路由自己绑了一份 JOBS_DIR：不改的话存草稿会往真实数据根写 job 标记
    monkeypatch.setattr(scripts_router, "JOBS_DIR", root / ".webui_draft_cache" / "jobs")
    monkeypatch.setenv("USERPROFILE" if WINDOWS else "HOME", str(tmp_path / "fakehome"))
    (tmp_path / "fakehome").mkdir()
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "settings.json").write_text('{"roles": {}}', encoding="utf-8")
    return root, outside


def _spec(episode_id: str, **overrides) -> dict:
    data = {
        "id": episode_id,
        "output_root": f"output/story_video/{episode_id}",
        "cast": {"su": {"block": "Su"}},
        "shots": [{"id": "S1", "dur": 5, "cast": ["su"], "lines": [{"who": "su", "en": "Hi.", "zh": "嗨。"}],
                   "shot": "wide", "art": "a room", "motion": "su waves"}],
    }
    data.update(overrides)
    return data


def _write(root: Path, episode_id: str, data: dict) -> Path:
    path = root / "content" / "story_video" / f"{episode_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------- PB-1：删除时 id 不合法 = 没有这个项目


@pytest.mark.skipif(not WINDOWS, reason="反斜杠只在 Windows 上是路径分隔符")
@pytest.mark.parametrize("make_id", [
    lambda outside: "..%5C..%5C..%5Coutside%5Cvictim",
    lambda outside: str(outside / "victim").replace("\\", "%5C").replace(":", "%3A"),   # 盘符绝对路径
])
def test_delete_rejects_ids_that_leave_the_data_root(sandbox, make_id):
    root, outside = sandbox
    (outside / "victim.json").write_text(json.dumps({"id": "victim", "output_root": str(outside / "victimdir")}), encoding="utf-8")
    (outside / "victimdir").mkdir()
    (outside / "victimdir" / "marker.txt").write_text("keep me", encoding="utf-8")

    r = client.delete(f"/api/episodes/storyvideo/{make_id(outside)}")

    assert r.status_code == 404, r.text
    assert (outside / "victim.json").is_file() and (outside / "victimdir" / "marker.txt").is_file()
    assert not (root / ".webui_deleted").exists()


# ---------------------------------------------------------------------------- PB-2：删除 / 恢复只认项目自己的输出目录


@pytest.mark.parametrize("kind", ["absolute", "dotdot"])
def test_delete_leaves_an_outside_output_root_in_place(sandbox, kind):
    root, outside = sandbox
    foreign = outside / "absout" / "EPABS"
    foreign.mkdir(parents=True)
    (foreign / "marker.txt").write_text("keep me", encoding="utf-8")
    value = str(foreign) if kind == "absolute" else "../outside/absout/EPABS"
    _write(root, "EPABS", _spec("EPABS", output_root=value))

    r = client.delete("/api/episodes/storyvideo/EPABS")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["output_moved"] is False and "没有搬动它" in body["warning"] and value in body["warning"]
    assert (foreign / "marker.txt").is_file()
    archived = root / body["archived_to"]
    # 归档里只有项目文件（外加回收站的清单）；清单里也不能把外面那个目录记成搬进来了
    assert sorted(p.name for p in archived.iterdir()) == ["manifest.json", "spec.json"]
    manifest = json.loads((archived / "manifest.json").read_text(encoding="utf-8"))
    assert [item["to"] for item in manifest["items"]] == ["spec.json"]
    assert not (root / "content" / "story_video" / "EPABS.json").exists()


@pytest.mark.parametrize("shared", ["output/story_video", "content", "."])
def test_delete_never_moves_a_shared_directory(sandbox, shared):
    root, _outside = sandbox
    other_output = root / "output" / "story_video" / "OTHER"
    other_output.mkdir(parents=True)
    (other_output / "S1.mp4").write_bytes(b"paid")
    _write(root, "OTHER", _spec("OTHER"))
    _write(root, "EPSH", _spec("EPSH", output_root=shared))

    r = client.delete("/api/episodes/storyvideo/EPSH")

    assert r.status_code == 200 and r.json()["output_moved"] is False and r.json()["warning"]
    assert (other_output / "S1.mp4").is_file() and (root / "content" / "story_video" / "OTHER.json").is_file()


def test_delete_and_restore_still_move_the_projects_own_output(sandbox):
    root, _outside = sandbox
    own = root / "output" / "story_video" / "EPOK"
    own.mkdir(parents=True)
    (own / "S1.mp4").write_bytes(b"paid")
    _write(root, "EPOK", _spec("EPOK"))

    r = client.delete("/api/episodes/storyvideo/EPOK")
    assert r.status_code == 200 and r.json()["output_moved"] is True and "warning" not in r.json()
    assert not own.exists()
    entry = Path(r.json()["archived_to"]).name
    restored = client.post(f"/api/deleted/storyvideo/{entry}/restore")
    assert restored.status_code == 200 and restored.json()["restored"]["output"] is True
    assert (own / "S1.mp4").read_bytes() == b"paid"


@pytest.mark.parametrize("kind", ["absolute", "not_id"])
def test_restore_refuses_to_write_outside_or_into_a_shared_directory(sandbox, kind):
    root, outside = sandbox
    target = outside / "restored" / "EPR" if kind == "absolute" else root / "output" / "story_video"
    entry = root / ".webui_deleted" / "storyvideo" / "EPR__20260101T000000Z"
    (entry / "output").mkdir(parents=True)
    (entry / "output" / "marker.txt").write_text("x", encoding="utf-8")
    (entry / "spec.json").write_text(json.dumps(_spec("EPR", output_root=str(target))), encoding="utf-8")

    r = client.post("/api/deleted/storyvideo/EPR__20260101T000000Z/restore")

    assert r.status_code == 409 and "output/story_video/EPR" in r.json()["detail"], r.text
    assert not (target / "marker.txt").exists() and not (outside / "restored").exists()
    assert (entry / "spec.json").is_file() and (entry / "output" / "marker.txt").is_file()   # 归档原样不动
    assert not (root / "content" / "story_video" / "EPR.json").exists()


# ---------------------------------------------------------------------------- PB-3：锚图 / 变体锚图只认数据目录 content/ 下的文件


@pytest.mark.parametrize("kind", ["tilde", "absolute", "dotdot", "local", "drive_relative", "unc"])
@pytest.mark.parametrize("where", ["member", "variant"])
def test_spec_rejects_anchor_paths_outside_content(sandbox, tmp_path, kind, where):
    root, outside = sandbox
    _png(outside / "pic.png")
    _png(tmp_path / "fakehome" / "pic.png")
    asset = {"tilde": "~/pic.png", "absolute": str(outside / "pic.png"), "dotdot": "../outside/pic.png",
             "local": f"local:{outside / 'pic.png'}", "drive_relative": "C:pic.png", "unc": "\\\\127.0.0.1\\c$\\pic.png"}[kind]
    member = {"block": "Su", "asset": asset} if where == "member" else \
        {"block": "Su", "variants": {"rain": {"delta": "雨衣", "asset": asset}}}

    with pytest.raises(SpecError, match="数据目录"):
        normalize_spec(_spec("EPA", cast={"su": member}), project_root=root)


def test_spec_keeps_legit_anchors_and_legacy_asset_ids(sandbox):
    root, _outside = sandbox
    anchor = _png(root / "content" / "assets" / "EPA" / "su-1.png")
    spec = normalize_spec(_spec("EPA", cast={"su": {"block": "Su", "asset": "content/assets/EPA/su-1.png",
                                                    "variants": {"rain": {"delta": "雨衣", "asset": "content\\assets\\EPA\\su-1.png"}}},
                                             "lin": {"block": "Lin", "asset": "asset-su"}}), project_root=root)
    assert Path(spec["cast"]["su"]["asset"]) == anchor.resolve()
    assert Path(spec["cast"]["su"]["variants"]["rain"]["asset"]) == anchor.resolve()
    assert spec["cast"]["lin"]["asset"] == "asset-su"        # 历史上的远端 asset id：原样保留


def test_spec_rejects_an_existing_file_outside_content(sandbox):
    root, _outside = sandbox
    _png(root / "output" / "story_video" / "EPA" / "01_frames" / "S1.png")
    with pytest.raises(SpecError, match="指向数据目录之外"):
        normalize_spec(_spec("EPA", cast={"su": {"block": "Su", "asset": "output/story_video/EPA/01_frames/S1.png"}}),
                       project_root=root)


def test_spec_rejects_a_link_that_points_outside(sandbox):
    root, outside = sandbox
    _png(outside / "pic.png")
    link = root / "content" / "assets" / "EPA" / "link.png"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside / "pic.png")
    except OSError:
        pytest.skip("这台机器上建不了符号链接（Windows 需要开发者模式或管理员权限）")
    with pytest.raises(SpecError, match="指向数据目录之外"):
        normalize_spec(_spec("EPA", cast={"su": {"block": "Su", "asset": "content/assets/EPA/link.png"}}), project_root=root)


@pytest.mark.parametrize("value", ["~/pic.png", "C:pic.png", "//host/share/pic.png", "\\\\host\\share\\pic.png"])
def test_cast_put_rejects_home_drive_relative_and_unc(sandbox, value):
    root, _outside = sandbox
    _write(root, "EPC", _spec("EPC"))
    r = client.put("/api/episodes/storyvideo/EPC/cast/su", json={"asset": value})
    assert r.status_code == 400 and "相对路径" in r.json()["detail"], r.text


def test_draft_save_rejects_outside_anchor_and_writes_nothing(sandbox):
    root, outside = sandbox
    _png(outside / "pic.png")
    r = client.post("/api/scripts/save", json={"episode_id": "EPS", "spec": _spec("EPS", cast={"su": {"block": "Su", "asset": str(outside / "pic.png")}})})
    assert r.status_code == 400 and "cast.su.asset" in r.json()["detail"], r.text
    assert not (root / "content" / "story_video" / "EPS.json").exists()


def test_other_modules_treat_outside_assets_as_missing(sandbox, tmp_path):
    from clip import spec as clip_spec
    from imageset import spec as set_spec
    from music import spec as music_spec
    from picturebook import spec as book_spec

    root, outside = sandbox
    _png(outside / "pic.png")
    _png(tmp_path / "fakehome" / "pic.png")
    _png(root / "content" / "assets" / "X" / "ok.png")
    fake = {"project_root": str(root)}
    for module in (clip_spec, set_spec, music_spec, book_spec):
        for bad in ("~/pic.png", str(outside / "pic.png"), "../outside/pic.png", "C:pic.png"):
            assert module.resolve_asset(fake, bad) is None, (module.__name__, bad)
        assert module.resolve_asset(fake, "content/assets/X/ok.png") == (root / "content" / "assets" / "X" / "ok.png").resolve()


def test_model_draft_cast_loses_its_asset_paths():
    from storyvideo.scriptgen import assemble_spec

    draft = {"cast": {"su": {"block": "Su", "asset": "C:/Users/x/Desktop/secret.png",
                             "variants": {"rain": {"delta": "雨衣", "asset": "~/y.png"}}}}, "shots": [{"id": "S1"}]}
    spec = assemble_spec("EP-X", draft, camera_profile="dark_comedy_vertical", prompt_language="zh")
    assert "asset" not in spec["cast"]["su"] and "asset" not in spec["cast"]["su"]["variants"]["rain"]
    assert spec["cast"]["su"]["block"] == "Su" and spec["cast"]["su"]["variants"]["rain"]["delta"] == "雨衣"


def test_runtime_checks_anchor_files_before_any_upload(sandbox):
    root, outside = sandbox
    runner = SimpleNamespace(project_root=root)
    good = _png(root / "content" / "assets" / "EP" / "su.png")
    StoryVideoRunner._check_anchor_files(runner, [str(good), "asset-su", "local:C:/x/proxy.png"])   # 远端 id / 令牌不查
    not_image = root / "content" / "story_video" / "EP.json"
    not_image.write_text("{}", encoding="utf-8")
    with pytest.raises(StoryVideoError, match="不是读得出来的图片"):
        StoryVideoRunner._check_anchor_files(runner, [str(not_image)])
    with pytest.raises(StoryVideoError, match="不在数据目录 content/ 里"):
        StoryVideoRunner._check_anchor_files(runner, [str(_png(outside / "pic.png"))])


# ---------------------------------------------------------------------------- PB-4：video_params.reference_audio


@pytest.mark.parametrize("value", ["~/voice.mp3", "../outside/voice.mp3", "C:voice.mp3", "absolute"])
def test_spec_rejects_reference_audio_outside_the_data_root(sandbox, value):
    root, outside = sandbox
    (outside / "voice.mp3").write_bytes(b"ID3fake")
    value = str(outside / "voice.mp3") if value == "absolute" else value
    with pytest.raises(SpecError, match="video_params.reference_audio"):
        normalize_spec(_spec("EPV", video_params={"reference_audio": value}), project_root=root)


@pytest.mark.parametrize("value", ["content/assets/EPV/ref.mp3", "output/story_video/EPV/ref.wav", "content/assets/EPV/missing.m4a"])
def test_spec_keeps_reference_audio_inside_the_data_root(sandbox, value):
    root, _outside = sandbox
    (root / "content" / "assets" / "EPV").mkdir(parents=True)
    (root / "content" / "assets" / "EPV" / "ref.mp3").write_bytes(b"ID3fake")
    (root / "output" / "story_video" / "EPV").mkdir(parents=True)
    (root / "output" / "story_video" / "EPV" / "ref.wav").write_bytes(b"RIFFfake")
    spec = normalize_spec(_spec("EPV", video_params={"reference_audio": value}), project_root=root)
    assert spec["video_params"]["reference_audio"] == value        # 存的值原样不动，请求指纹不变


def _video_job():
    return {"asset_id": "asset-frame", "attempt": 0, "request_hash": "abcdef0123456789"}


def test_runtime_refuses_reference_audio_that_bypassed_normalize(sandbox):
    root, outside = sandbox
    (outside / "voice.mp3").write_bytes(b"ID3fake")
    uploads: list[Path] = []
    provider = SimpleNamespace(upload_asset=lambda path, **_kw: uploads.append(Path(path)) or "local:x")
    runner = SimpleNamespace(project_root=root, _provider=lambda: provider)
    spec = normalize_spec(_spec("EPV", video_params={"reference_audio": "content/assets/EPV/ref.mp3"}), project_root=root)
    for bad in (str(outside / "voice.mp3"), "../outside/voice.mp3", "~/voice.mp3"):
        spec["video_params"]["reference_audio"] = bad     # 手改 / 绕过归一化
        with pytest.raises(StoryVideoError, match="参考音频只能是数据目录里的相对路径"):
            StoryVideoRunner._request(runner, spec, "videos", "S1", _video_job())
    assert uploads == []
    (root / "content" / "assets" / "EPV").mkdir(parents=True)
    (root / "content" / "assets" / "EPV" / "ref.mp3").write_bytes(b"ID3fake")
    spec["video_params"]["reference_audio"] = "content/assets/EPV/ref.mp3"
    request = StoryVideoRunner._request(runner, spec, "videos", "S1", _video_job())
    assert uploads == [(root / "content" / "assets" / "EPV" / "ref.mp3").resolve()]
    assert request.extra_params["reference_audio"] == "local:x"


# ---------------------------------------------------------------------------- PB-5：配乐文件（后期层）


@pytest.mark.parametrize("value", ["absolute", "../outside/song.mp3", "~/song.mp3", "C:song.mp3", "//127.0.0.1/c$/song.mp3",
                                   "output/story_video/EPM/song.mp3"])
def test_music_tracks_mark_outside_files_missing_with_a_reason(sandbox, value):
    from storyvideo import post_layers as pl

    root, outside = sandbox
    (outside / "song.mp3").write_bytes(b"ID3fake")
    (root / "output" / "story_video" / "EPM").mkdir(parents=True)
    (root / "output" / "story_video" / "EPM" / "song.mp3").write_bytes(b"ID3fake")
    value = str(outside / "song.mp3") if value == "absolute" else value
    rows = pl.music_tracks({"post_graphics": {"music": [{"file": value, "start": 0}]}}, project_root=root)
    assert rows[0]["missing"] is True and rows[0]["path"] == "" and rows[0]["reason"]


def test_music_tracks_accept_content_files(sandbox):
    from storyvideo import post_layers as pl

    root, _outside = sandbox
    (root / "content" / "assets" / "EPM" / "music").mkdir(parents=True)
    (root / "content" / "assets" / "EPM" / "music" / "a.mp3").write_bytes(b"ID3fake")
    rows = pl.music_tracks({"post_graphics": {"music": [{"file": "content/assets/EPM/music/a.mp3"},
                                                        {"file": "content/assets/EPM/music/nope.mp3"}]}}, project_root=root)
    assert rows[0]["missing"] is False and Path(rows[0]["path"]) == (root / "content" / "assets" / "EPM" / "music" / "a.mp3").resolve()
    assert rows[1]["missing"] is True and rows[1]["reason"] == "文件不存在"


def test_draft_save_rejects_outside_music_and_writes_nothing(sandbox):
    root, outside = sandbox
    (outside / "song.mp3").write_bytes(b"ID3fake")
    for bad in (str(outside / "song.mp3"), "../outside/song.mp3"):
        r = client.post("/api/scripts/save",
                        json={"episode_id": "EPSAVE2", "spec": _spec("EPSAVE2", post_graphics={"music": [{"file": bad, "start": 0}]})})
        assert r.status_code == 400 and "music[0]" in r.json()["detail"], r.text
    assert not (root / "content" / "story_video" / "EPSAVE2.json").exists()


def test_hand_written_outside_music_is_shown_missing_and_can_be_removed(sandbox):
    root, outside = sandbox
    (outside / "song.mp3").write_bytes(b"ID3fake")
    _write(root, "EPMH", _spec("EPMH", post_graphics={"music": [{"file": str(outside / "song.mp3"), "start": 0}]}))
    got = client.get("/api/episodes/storyvideo/EPMH/post-layers")
    assert got.status_code == 200
    music = got.json()["readiness"]["music"]
    assert music[0]["missing"] is True and music[0]["reason"] == "不是数据目录里的相对路径"
    # PUT 同样的绝对路径：400；删掉这一条：照常保存
    bad = client.put("/api/episodes/storyvideo/EPMH/post-layers", json={"post_graphics": {"music": [{"file": str(outside / "song.mp3")}]}})
    assert bad.status_code == 400 and "music[0]" in bad.json()["detail"]
    ok = client.put("/api/episodes/storyvideo/EPMH/post-layers", json={"post_graphics": {"music": []}})
    assert ok.status_code == 200, ok.text


def test_music_path_errors_say_the_rule_and_what_to_do(sandbox):
    """配乐路径被拒时直说规则（只收 content/ 下上传的音频）和怎么办。以前套成「要是项目内的相对路径（原因）」，
    output/… 这种本来就在项目里的值被告知「要是项目内的相对路径（不在数据目录 content/ 里）」，读着自相矛盾。"""

    root, outside = sandbox
    (outside / "song.mp3").write_bytes(b"ID3fake")
    (root / "output" / "story_video" / "EPMW").mkdir(parents=True)
    (root / "output" / "story_video" / "EPMW" / "song.mp3").write_bytes(b"ID3fake")
    _write(root, "EPMW", _spec("EPMW"))
    for value, reason in ((str(outside / "song.mp3"), "不是数据目录里的相对路径"),
                          ("output/story_video/EPMW/song.mp3", "不在数据目录 content/ 里")):
        put = client.put("/api/episodes/storyvideo/EPMW/post-layers", json={"post_graphics": {"music": [{"file": value}]}})
        detail = put.json()["detail"]
        assert put.status_code == 400 and f"music[0] 的 file 用不了：{reason}——只能用上传到数据目录 content/ 下的音频" in detail
        assert "要是项目内的相对路径" not in detail
        save = client.post("/api/scripts/save",
                           json={"episode_id": "EPMW2", "spec": _spec("EPMW2", post_graphics={"music": [{"file": value}]})})
        assert save.status_code == 400 and save.json()["detail"].startswith(f"草稿里的配乐 music[0] 用不了：{reason}——")
        assert "建好项目后在「后期层」的「配乐」里上传" in save.json()["detail"]
        # 英文界面：整句翻出来，原因也翻，不剩中文、不再说 "must be a relative path inside the project"
        for r in (client.put("/api/episodes/storyvideo/EPMW/post-layers", json={"post_graphics": {"music": [{"file": value}]}},
                             headers={"X-StoryStudio-Lang": "en"}),
                  client.post("/api/scripts/save", json={"episode_id": "EPMW2", "spec": _spec("EPMW2", post_graphics={"music": [{"file": value}]})},
                              headers={"X-StoryStudio-Lang": "en"})):
            text = r.json()["detail"]
            assert "cannot be used" in text and "content/" in text and "relative path inside the project" not in text, text
            assert not any("一" <= ch <= "鿿" for ch in text), text
    assert not (root / "content" / "story_video" / "EPMW2.json").exists()


# ---------------------------------------------------------------------------- PB-6：角色 / 变体 id、锚图文件名


@pytest.mark.parametrize("key", ["..\\..\\..\\..\\outside\\evil", "a/b", "..", ".", "a@b", "a?b", "a#b", "a%b", "a\x01b"])
def test_spec_rejects_unsafe_cast_keys(sandbox, key):
    root, _outside = sandbox
    data = _spec("EPK")
    data["cast"][key] = {"block": "x"}
    with pytest.raises(SpecError, match="的 id 不能含"):
        normalize_spec(data, project_root=root)


@pytest.mark.parametrize("key", ["x\\..\\y", "a/b", ".."])
def test_spec_rejects_unsafe_variant_keys(sandbox, key):
    root, _outside = sandbox
    with pytest.raises(SpecError, match="的 id 不能含"):
        normalize_spec(_spec("EPK", cast={"su": {"block": "Su", "variants": {key: {"delta": "x"}}}}), project_root=root)


def test_spec_still_accepts_chinese_and_dotted_keys(sandbox):
    root, _outside = sandbox
    spec = normalize_spec(_spec("EPK", cast={"su": {"block": "Su", "variants": {"lin.v2": {"delta": "x"}}}, "林小满": {"block": "林"}}),
                          project_root=root)
    assert "林小满" in spec["cast"] and "lin.v2" in spec["cast"]["su"]["variants"]


def test_draft_save_rejects_a_backslash_cast_key(sandbox):
    root, _outside = sandbox
    data = _spec("EPKS")
    data["cast"]["..\\..\\evil"] = {"block": "x"}
    r = client.post("/api/scripts/save", json={"episode_id": "EPKS", "spec": data})
    assert r.status_code == 400 and "的 id 不能含" in r.json()["detail"], r.text


def test_anchor_upload_file_name_stays_inside_the_assets_dir(sandbox):
    root, _outside = sandbox
    _write(root, "EPU", _spec("EPU"))
    relative, *_ = project_router._store_asset("EPU", "..\\..\\..\\evil", project_router.AssetUpload(filename="a.png", data_base64=_png_base64()))
    target = (root / relative).resolve()
    assert target.parent == (root / "content" / "assets" / "EPU").resolve() and target.name.startswith("evil-")
    relative, *_ = project_router._store_asset("EPU", "林小满", project_router.AssetUpload(filename="a.png", data_base64=_png_base64()))
    assert Path(relative).name.startswith("asset-") and Path(relative).parent.as_posix() == "content/assets/EPU"


# ---------------------------------------------------------------------------- PB-8：404 文案不带本机绝对路径


def _no_local_paths(text: str, root: Path) -> bool:
    return all(form not in text for form in (str(root), str(root.resolve()), root.as_posix(), str(Path.home())))


def test_not_found_details_are_uniform_and_carry_no_local_paths(sandbox):
    root, _outside = sandbox
    _write(root, "EPABS", _spec("EPABS", output_root="../outside/EPABS"))
    (root / "content" / "story_video" / "EPBAD.json").write_text('{"id": "EPBAD", ', encoding="utf-8")
    routes = ("", "/summary", "/cast", "/post-layers", "/export")
    details: dict[str, set[str]] = {}
    for episode_id in ("NOPE", "EPABS", "EPBAD", "..%5C..%5Cx.json" if WINDOWS else "..%2F..%2Fx.json"):
        seen = set()
        for suffix in routes:
            r = client.get(f"/api/episodes/storyvideo/{episode_id}{suffix}")
            assert r.status_code == 404, (episode_id, suffix, r.text)
            detail = r.json()["detail"]
            assert _no_local_paths(detail, root), (episode_id, suffix, detail)
            seen.add(detail)
        details[episode_id] = seen
    assert details["NOPE"] == {"没有这个项目：storyvideo/NOPE"}
    assert details["EPABS"] == {"项目文件里的 output_root 指向数据目录之外，已拒绝读取——改成数据目录内的相对路径"}
    assert all("项目文件不是合法的 JSON：EPBAD.json" in d for d in details["EPBAD"])
    # 列表里坏项目的 error 同样不带绝对路径
    listed = {e["id"]: e for e in client.get("/api/episodes").json()}
    assert listed["EPABS"]["status"] == "error" and _no_local_paths(listed["EPABS"]["error"], root)
    assert listed["EPBAD"]["status"] == "error" and _no_local_paths(listed["EPBAD"]["error"], root)


def test_redact_local_paths_keeps_paths_relative_to_the_data_root(sandbox):
    root, _outside = sandbox
    text = f"could not read {root / 'content' / 'x.json'} and {root} and {Path.home() / 'y'}"
    out = deps.redact_local_paths(text)
    assert _no_local_paths(out, root)
    assert "content" in out and "<数据目录>" in out and "~" in out


def test_project_file_only_looks_at_the_literal_text_for_unc(sandbox):
    """UNC 路径按字面拒，不去碰文件系统（不会对内网主机发起 SMB 连接）。"""

    root, _outside = sandbox
    with pytest.raises(SpecError):
        project_file("\\\\10.255.255.1\\share\\x.png", root, field="asset")
