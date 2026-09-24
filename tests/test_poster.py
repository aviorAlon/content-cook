"""海报（DESIGN §36）：提示词内容、ffmpeg 叠字、接口（首帧来源 / 模型来源 / 只重排 / 路径穿越）、封面与交付包联动。"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit.providers import TaskSnapshot, TaskState, TaskSubmission, TaskType, path_to_file_url
from storyvideo import poster as P
from storyvideo.prompts import NO_TEXT_TAIL_CN, NO_TEXT_TAIL_EN
from storyvideo.spec import load_spec
from webui import deps
from webui import export as export_lib
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import export as export_router
from webui.routers import post as post_router
from webui.routers import poster as poster_router
from webui.routers import project as project_router

client = TestClient(app)
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="needs ffmpeg + ffprobe on PATH")


def spec_dict(**extra) -> dict:
    spec = {
        "id": "PST-T", "output_root": "output/story_video/PST-T", "prompt_language": "zh",
        "title_zh": "夜色下的第七封信", "title_en": "The Seventh Letter", "logline": "一封烧掉的信回到了她手里",
        "frame_style": "竖版动画剧集画面。", "scene_anchor": "深夜老公寓，台灯暖光。", "camera": "z", "neg": "低画质",
        "subtitle": {"language": "zh"},
        "cast": {
            "lin": {"name": "林小满", "block": "林小满：黑发，藏蓝风衣。", "asset": "content/assets/lin.png",
                    "variants": {"wet": {"label": "淋湿", "delta": "头发湿透。"}}},
            "chen": {"name": "陈默", "block": "陈默：灰白鬓角，黑大衣。"},
            "old": {"name": "老周", "block": "老周：矮胖，围裙。"},
            "room": {"block": "老公寓客厅：木地板，旧沙发。"},
        },
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["lin@wet", "room"], "shot": "中景", "art": "林小满推门进来，手里攥着一封烧焦的信。", "motion": "m", "lines": []},
            {"id": "S2", "dur": 4, "cast": ["lin", "chen", "room"], "shot": "近景", "art": "陈默背光站着。", "motion": "m", "lines": []},
            {"id": "S3", "dur": 4, "cast": ["chen", "old"], "shot": "近景", "art": "老周端茶。", "motion": "m", "lines": []},
        ],
    }
    spec.update(extra)
    return spec


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, export_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    (tmp_path / "content" / "assets").mkdir()
    (tmp_path / "content" / "assets" / "lin.png").write_bytes(png_bytes())
    return tmp_path


def write_spec(root: Path, **extra) -> Path:
    path = root / "content" / "story_video" / "PST-T.json"
    path.write_text(json.dumps(spec_dict(**extra), ensure_ascii=False), encoding="utf-8")
    return path


def png_bytes(color=(40, 60, 160), size=(360, 640)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def lavfi_png(path: Path, color: str = "0x2244aa", size: str = "720x1280") -> Path:
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}",
                    "-frames:v", "1", "-update", "1", str(path)], check=True)
    return path


def probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def wait_task(task_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = client.get(f"/api/tasks/{task_id}").json()
        if item["status"] in ("succeeded", "failed"):
            return item
        time.sleep(0.05)
    raise AssertionError("task did not finish")


class FakeProvider:
    def __init__(self, cache: Path, fail: bool = False):
        self.cache = cache
        self.fail = fail
        self.requests = []

    def submit_image(self, request):
        self.requests.append(request)
        if self.fail:
            return TaskSubmission(task_id=None, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message="余额不足")
        path = self.cache / "poster-base.png"
        path.write_bytes(png_bytes((120, 30, 30)))
        return TaskSubmission(task_id=f"file:{path}", task_type=TaskType.IMAGE, status=TaskState.FINISHED)

    def get_task(self, task_id, task_type):
        path = Path(task_id[len("file:"):])
        return TaskSnapshot(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.FINISHED, output_urls=(path_to_file_url(path),))

    def download(self, url, destination, **kw):
        src = Path(url.replace("file:///", "").replace("file://", ""))
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, destination)
        return Path(destination)


class AsyncFakeProvider:
    """异步来源（提交后只拿到远端任务 id 的来源）：前 ``rounds`` 轮还在跑，之后完成 / 失败。"""

    def __init__(self, cache: Path, rounds: int = 2, outcome: str = "finished", error: str = "内容被拦截"):
        self.cache = cache
        self.rounds = rounds
        self.outcome = outcome
        self.error = error
        self.requests = []
        self.polls = 0

    def submit_image(self, request):
        self.requests.append(request)
        return TaskSubmission(task_id="exasync-img:t1", task_type=TaskType.IMAGE, status=TaskState.PROCESSING)

    def get_task(self, task_id, task_type):
        self.polls += 1
        if self.polls <= self.rounds:
            return TaskSnapshot(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.PROCESSING)
        if self.outcome == "failed":
            return TaskSnapshot(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message=self.error)
        path = self.cache / "async-base.png"
        path.write_bytes(png_bytes((30, 90, 40)))
        return TaskSnapshot(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.FINISHED,
                            output_urls=(path_to_file_url(path),))

    download = FakeProvider.download


# ---------------------------------------------------------------------------- 提示词


def test_poster_prompt_zh_picks_present_cast_hook_and_forbids_text():
    spec = spec_dict()
    out = P.poster_prompt(spec)
    assert out["language"] == "zh" and out["prompt"] == out["zh"]
    # 出镜最多的两个：林（S1、S2，变体不分开算）、陈（S2、S3）；老周只 1 镜；场景块没有 name 不入选
    assert out["cast"] == ["lin", "chen"]
    assert "林小满：黑发，藏蓝风衣。" in out["zh"] and "陈默：灰白鬓角" in out["zh"]
    assert "老周" not in out["zh"] and "老公寓客厅：木地板" not in out["zh"]
    assert "头发湿透" not in out["zh"]   # 变体差异不进海报
    assert out["zh"].startswith("竖版动画剧集画面。深夜老公寓，台灯暖光。")
    assert "林小满推门进来，手里攥着一封烧焦的信。" in out["zh"]   # 没有契约时钩子 = 第一镜 art
    assert "竖版电影海报" in out["zh"] and "上三分之一" in out["zh"]
    assert out["zh"].endswith(NO_TEXT_TAIL_CN)
    assert "夜色下的第七封信" not in out["zh"] and "The Seventh Letter" not in out["zh"]   # 标题绝不进提示词
    assert out["negative"].startswith("低画质,") and "水印" in out["negative"]
    assert P.poster_reference_asset_ids(spec) == ("content/assets/lin.png",)


def test_poster_prompt_en_and_contract_hook_and_base_shot():
    spec = spec_dict(prompt_language="en", episode_contract={"hook": "A burnt letter comes back to her.", "premise": "She wants to know who kept it."})
    out = P.poster_prompt(spec)
    assert out["language"] == "en" and out["prompt"] == out["en"]
    assert "A burnt letter comes back to her." in out["en"] and "vertical movie poster" in out["en"]
    assert out["en"].rstrip().endswith(NO_TEXT_TAIL_EN.strip())
    assert "text,letters" in out["negative"]
    # 指定底图镜时钩子换成那一镜的 art
    assert "老周端茶" in P.poster_prompt(spec, base_shot="S3")["prompt"]
    # 副标题建议：契约前提 > logline > 英文片名
    assert P.suggested_subtitle(spec) == "She wants to know who kept it."
    assert P.suggested_subtitle(spec_dict()) == "一封烧掉的信回到了她手里"
    assert P.suggested_subtitle(spec_dict(logline="")) == "The Seventh Letter"
    assert P.suggested_title(spec_dict()) == "夜色下的第七封信"


def test_poster_request_data_uses_image_params():
    data = P.poster_request_data(spec_dict(image_params={"model": "doubao-seedream-4", "image_size": "1K"}))
    assert data["aspect_ratio"] == "9:16" and data["image_size"] == "1K" and data["quality"] is None
    assert data["model"] == "doubao-seedream-4" and data["name"] == "PST-T-poster"
    assert P.poster_request_data(spec_dict())["quality"] == "high"


# ---------------------------------------------------------------------------- 叠字


@needs_ffmpeg
def test_render_poster_outputs_1080x1920_and_never_overwrites(tmp_path):
    base = lavfi_png(tmp_path / "base.png")
    out = P.render_poster(base, tmp_path / "poster.png", title="夜色下的第七封信", subtitle="她以为烧掉的信，今天回到了她手里")
    assert out.is_file() and probe_size(out) == (1080, 1920)
    assert not list(tmp_path.glob("*.txt")) and not list(tmp_path.glob(".*.tmp.png"))   # 临时文件清掉
    # 横图也补成竖版
    wide = P.render_poster(lavfi_png(tmp_path / "wide.png", size="1280x720"), tmp_path / "poster-wide.png", title="Long English Title Here")
    assert probe_size(wide) == (1080, 1920)
    with pytest.raises(P.PosterError, match="不覆盖"):
        P.render_poster(base, tmp_path / "poster.png", title="x")
    with pytest.raises(P.PosterError, match="不能为空"):
        P.render_poster(base, tmp_path / "poster2.png", title="   ")
    with pytest.raises(P.PosterError, match="底图不存在"):
        P.render_poster(tmp_path / "nope.png", tmp_path / "poster3.png", title="x")


def test_render_poster_is_pillow_only_and_cover_crops(tmp_path, monkeypatch):
    """叠字不再依赖 ffmpeg；底图满幅裁切不补黑边（四角都是底图色，不是补边色）。"""

    from PIL import Image

    base = tmp_path / "base.png"
    base.write_bytes(png_bytes(color=(40, 60, 160), size=(400, 600)))      # 2:3，比 9:16 宽
    monkeypatch.setattr(P.shutil, "which", lambda name: None)
    out = P.render_poster(base, tmp_path / "poster.png", title="加班夜的许愿", subtitle="招财猫真的开口了", kicker="第 1 集", layout="bottom")
    im = Image.open(out)
    assert im.size == (1080, 1920) and P.render_poster.last_layout == "bottom"
    # 顶部（没压暗的一端）四角是底图蓝，不是黑边
    r, g, b = im.getpixel((10, 10))
    assert b > 120 and r < 80
    # 下方文字块里有近白的标题像素、有金色小标像素
    band = im.crop((0, 1350, 1080, 1900)).getcolors(maxcolors=1_000_000)
    assert any(c[0] > 230 and c[1] > 225 and c[2] > 210 for _, c in band)
    assert any(c[0] > 190 and 150 < c[1] < 200 and c[2] < 140 for _, c in band)
    with pytest.raises(P.PosterError, match="auto / top / bottom"):
        P.render_poster(base, tmp_path / "poster2.png", title="x", layout="left")
    (tmp_path / "v.mp4").write_bytes(b"x")
    with pytest.raises(P.PosterError, match="没找到 ffmpeg"):
        P.extract_first_frame(tmp_path / "v.mp4", tmp_path / "f.png")


def test_pick_layout_prefers_quiet_end_and_subtitle_cleanup(tmp_path):
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (1080, 1920), (20, 24, 40))
    draw = ImageDraw.Draw(im)
    for i in range(0, 1080, 12):                       # 上半密集条纹（忙），下半安静
        draw.line((i, 0, i, 800), fill=(230, 230, 230), width=4)
    assert P.pick_layout(im) == "bottom"
    im2 = im.transpose(Image.FLIP_TOP_BOTTOM)
    assert P.pick_layout(im2) == "top" and P.pick_layout(im2, "bottom") == "bottom"
    assert P.clean_subtitle("深夜加班的林小满对着桌上的招财猫许愿涨工资，招财猫真的开口了——结果只帮她涨了一块钱") == "深夜加班的林小满对着桌上的招财猫许愿涨工资"
    assert P.clean_subtitle("  一句  很短的话 ") == "一句 很短的话"
    assert P.clean_subtitle("没有任何标点的一长串副标题文字一直写到超过二十四个字为止再多一些") == "没有任何标点的一长串副标题文字一直写到超过二十…"
    assert P.suggested_kicker({"series": {"id": "s", "episode_no": 3}, "prompt_language": "zh"}) == "第 3 集"
    assert P.suggested_kicker({"series": {"id": "s", "episode_no": 3}, "prompt_language": "en"}) == "Episode 3"
    assert P.suggested_kicker({"form": "vertical_drama", "prompt_language": "zh"}) == "竖屏短剧" and P.suggested_kicker({}) == ""


def test_poster_paths_and_manifest_latest_wins(tmp_path):
    spec = {"output_root": str(tmp_path / "out")}
    a = P.poster_paths(spec, stamp=100)
    assert a["base"].name == "base-100.png" and a["poster"].name == "poster-100.png" and a["manifest"].name == "poster.json"
    a["dir"].mkdir(parents=True)
    a["poster"].write_bytes(b"x")
    b = P.poster_paths(spec, stamp=100)   # 同一毫秒戳已被占用 → 顺延，不覆盖
    assert b["stamp"] == 101
    P.record(spec, {"poster": "poster-100.png", "base": "base-100.png", "created_at": 1})
    P.record(spec, {"poster": "poster-101.png", "base": "base-101.png", "created_at": 2})   # 文件不存在 → 不算
    assert P.latest(spec)["poster"] == "poster-100.png"
    b["poster"].write_bytes(b"y")
    assert P.latest(spec)["poster"] == "poster-101.png" and [e["poster"] for e in P.history(spec)] == ["poster-101.png", "poster-100.png"]
    assert P.latest_poster_path(spec) == b["poster"]
    assert P.latest_base_path(spec) is None   # 底图文件都不在


# ---------------------------------------------------------------------------- 接口


@needs_ffmpeg
def test_frame_source_end_to_end_then_render_only_and_cover_and_export(project):
    write_spec(project)
    out = project / "output" / "story_video" / "PST-T"
    (out / "01_frames").mkdir(parents=True)
    lavfi_png(out / "01_frames" / "S2.png", color="0x226633")
    (out / f"PST-T_final.mp4").write_bytes(b"final")

    info = client.get("/api/episodes/storyvideo/PST-T/poster").json()
    assert info["latest"] is None and info["suggested_title"] == "夜色下的第七封信" and info["shots_with_frames"] == ["S2"]
    assert info["ffmpeg_ok"] and "竖版电影海报" in info["prompt"] and isinstance(info["fonts"], list)

    bad = client.post("/api/episodes/storyvideo/PST-T/poster/generate", json={"source": "frame", "shot_id": "S1"})
    assert bad.status_code == 425 and "没法当海报底图" in bad.json()["detail"]
    assert client.post("/api/episodes/storyvideo/PST-T/poster/generate", json={"source": "frame", "shot_id": "S9"}).status_code == 404
    assert client.post("/api/episodes/storyvideo/PST-T/poster/generate", json={"source": "x"}).status_code == 400
    assert client.post("/api/episodes/storyvideo/PST-T/poster/render", json={"title": "x"}).status_code == 425   # 还没底图

    assert client.post("/api/episodes/storyvideo/PST-T/poster/generate", json={"source": "frame", "shot_id": "S2", "layout": "left"}).status_code == 400
    resp = client.post("/api/episodes/storyvideo/PST-T/poster/generate", json={"source": "frame", "shot_id": "S2", "subtitle": "", "layout": "top", "kicker": "第 1 集"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    entry = body["entry"]
    assert entry["source"] == "frame" and entry["shot_id"] == "S2" and entry["title"] == "夜色下的第七封信" and entry["subtitle"] == ""
    assert entry["layout"] == "top" and entry["layout_used"] == "top" and entry["kicker"] == "第 1 集"
    assert info["layouts"] == ["auto", "top", "bottom"] and info["subtitle_max"] == 24 and "suggested_kicker" in info
    assert body["latest"]["poster"] == entry["poster"] and len(body["history"]) == 1
    poster_file = out / "07_poster" / entry["poster"]
    assert poster_file.is_file() and probe_size(poster_file) == (1080, 1920)
    assert (out / "07_poster" / entry["base"]).read_bytes() == (out / "01_frames" / "S2.png").read_bytes()
    manifest = json.loads((out / "07_poster" / "poster.json").read_text(encoding="utf-8"))
    assert manifest["entries"][0]["poster"] == entry["poster"] and manifest["entries"][0]["prompt"] is None

    # 文件端点：正常取图；路径穿越 / 非 png / 不存在 → 404
    got = client.get(entry["url"])
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    for name in ("..%2Fposter.json", "poster.json", ".hidden.png", "nope.png", "..%5C..%5Cx.png"):
        assert client.get(f"/api/episodes/storyvideo/PST-T/poster/file/{name}").status_code == 404, name
    assert not (out / "07_poster" / "..").resolve().joinpath("PST-T.json").exists()

    # 只重排文字：新成品、底图复用、老文件不动
    before = poster_file.read_bytes()
    resp = client.post("/api/episodes/storyvideo/PST-T/poster/render", json={"title": "第七封信", "subtitle": "副标题"})
    assert resp.status_code == 200, resp.text
    entry2 = resp.json()["entry"]
    assert entry2["poster"] != entry["poster"] and entry2["base"] == entry["base"] and entry2["source"] == "frame"
    assert poster_file.read_bytes() == before and (out / "07_poster" / entry2["poster"]).is_file()
    assert [h["poster"] for h in resp.json()["history"]] == [entry2["poster"], entry["poster"]]

    # 封面优先海报（侧栏列表 + 概览）；下一步提示不再提海报
    listed = next(e for e in client.get("/api/episodes").json() if e["id"] == "PST-T")
    assert listed["cover_url"] == entry2["url"]
    summary = client.get("/api/episodes/storyvideo/PST-T/summary").json()
    assert summary["cover_url"] == entry2["url"] and summary["poster"] == {"exists": True, "title": "第七封信", "hint": ""}

    # 交付包带最新海报
    spec = load_spec("PST-T", project_root=project)
    dest = out / "09_export" / "PST-T-20260908-1200"
    result = export_lib.build_package(spec, json.loads((project / "content" / "story_video" / "PST-T.json").read_text(encoding="utf-8")), dest)
    assert result["poster"] is True and (dest / "poster.png").read_bytes() == (out / "07_poster" / entry2["poster"]).read_bytes()
    pkg = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert pkg["poster"] == {"source": entry2["poster"], "file": "poster.png", "title": "第七封信", "made_from": "frame"}
    assert any(f["path"] == "poster.png" for f in pkg["files"])


@needs_ffmpeg
def test_model_source_runs_as_task_with_fake_provider(project, monkeypatch, tmp_path):
    write_spec(project)
    cache = tmp_path / "cache"
    cache.mkdir()
    fake = FakeProvider(cache)
    monkeypatch.setattr(poster_router, "build_provider", lambda: fake)
    resp = client.post("/api/episodes/storyvideo/PST-T/poster/generate", json={"source": "model", "title": "夜色", "subtitle": "一句话", "font": ""})
    assert resp.status_code == 200, resp.text
    assert resp.json()["cost_note"] == "1 次生图请求（gpt-image-2）" and resp.json()["task_id"]
    task = wait_task(resp.json()["task_id"])
    assert task["status"] == "succeeded", task.get("error")
    assert task["kind"] == "poster"
    assert len(fake.requests) == 1   # 恰好一次生图请求
    req = fake.requests[0]
    # 参考图跟首帧请求同一来源：规范化后的 spec 把本地锚图解析成绝对路径，适配器负责上传 / 转 data URL
    assert req.aspect_ratio == "9:16" and req.name == "PST-T-poster"
    assert req.reference_asset_ids == (str((project / "content" / "assets" / "lin.png").resolve()),)
    assert req.workspace_id is None
    assert "画面中绝对不出现任何文字" in req.prompt and "夜色" not in req.prompt
    entry = task["result"]["entry"]
    assert entry["source"] == "model" and entry["model"] == "gpt-image-2" and entry["prompt"] == req.prompt and entry["cost_note"]
    out = project / "output" / "story_video" / "PST-T" / "07_poster"
    assert (out / entry["poster"]).is_file() and probe_size(out / entry["poster"]) == (1080, 1920)
    info = client.get("/api/episodes/storyvideo/PST-T/poster").json()
    assert info["latest"]["title"] == "夜色" and info["latest"]["subtitle"] == "一句话" and info["latest"]["source"] == "model"

    # 失败的来源：任务 failed，人话原因，不留半成品
    fake.fail = True
    task = wait_task(client.post("/api/episodes/storyvideo/PST-T/poster/generate", json={"source": "model"}).json()["task_id"])
    assert task["status"] == "failed" and task["error"] == "余额不足"   # 人话原因，不裹 HTTPException、不带异常类名
    assert task["status"] == "failed" and task["error"] == "余额不足"   # 人话原因，不裹 HTTPException，也不带异常类名
    assert task["error_type"] == "PosterError"
    assert len(client.get("/api/episodes/storyvideo/PST-T/poster").json()["history"]) == 1


def test_async_source_is_polled_until_finished(project, monkeypatch, tmp_path):
    """异步来源（2026-09-10）：提交拿到远端任务 id → 在后台任务里轮询 → 完成后下载、叠字。每轮写一行进度。"""

    from PIL import Image

    write_spec(project)
    cache = tmp_path / "cache"
    cache.mkdir()
    fake = AsyncFakeProvider(cache, rounds=2)
    assert poster_router.MAX_WAIT_SECONDS == 20 * 60 and poster_router.POLL_SECONDS == 5.0   # 跟绘本 / 图片集同一口径
    monkeypatch.setattr(poster_router, "build_provider", lambda: fake)
    monkeypatch.setattr(poster_router, "POLL_SECONDS", 0.01)
    resp = client.post("/api/episodes/storyvideo/PST-T/poster/generate", json={"source": "model", "title": "夜色", "subtitle": "一句话"})
    assert resp.status_code == 200, resp.text
    task = wait_task(resp.json()["task_id"])
    assert task["status"] == "succeeded", task.get("error")
    assert fake.polls == 3 and len(fake.requests) == 1          # processing → processing → finished，只提交一次
    assert task["current"] == "poster" and task["waiting_rounds"] >= 2   # 界面轮询任务时看得到「还在等」
    entry = task["result"]["entry"]
    out = project / "output" / "story_video" / "PST-T" / "07_poster"
    assert (out / entry["base"]).is_file() and Image.open(out / entry["poster"]).size == (1080, 1920)
    assert entry["source"] == "model" and entry["model"] == "gpt-image-2"
    assert client.get("/api/episodes/storyvideo/PST-T/poster").json()["latest"]["title"] == "夜色"


def test_async_timeout_and_remote_failure_leave_no_files(project, monkeypatch, tmp_path):
    """等到上限 / 远端报失败：任务 failed 带人话原因，07_poster 里不留半张底图或成品。"""

    write_spec(project)
    cache = tmp_path / "cache"
    cache.mkdir()
    out = project / "output" / "story_video" / "PST-T" / "07_poster"
    forever = AsyncFakeProvider(cache, rounds=10_000)
    monkeypatch.setattr(poster_router, "build_provider", lambda: forever)
    monkeypatch.setattr(poster_router, "MAX_WAIT_SECONDS", 0)    # 上限是模块常量，测试调小它
    monkeypatch.setattr(poster_router, "POLL_SECONDS", 0)
    task = wait_task(client.post("/api/episodes/storyvideo/PST-T/poster/generate", json={"source": "model"}).json()["task_id"])
    assert task["status"] == "failed"
    assert "分钟还没出图" in task["error"] and "exasync-img:t1" in task["error"]
    assert forever.polls == 1 and not list(out.glob("*.png"))
    assert client.get("/api/episodes/storyvideo/PST-T/poster").json()["latest"] is None

    broken = AsyncFakeProvider(cache, rounds=0, outcome="failed")   # 第一次查就是失败
    monkeypatch.setattr(poster_router, "build_provider", lambda: broken)
    task = wait_task(client.post("/api/episodes/storyvideo/PST-T/poster/generate", json={"source": "model"}).json()["task_id"])
    assert task["status"] == "failed" and task["error"] == "内容被拦截"
    assert task["status"] == "failed" and task["error"] == "内容被拦截" and task["error_type"] == "PosterError"
    assert not list(out.glob("*.png")) and not (out / "poster.json").exists()


def test_summary_poster_hint_after_final_passes(project, monkeypatch):
    """成片通过、还没有海报：next_step 的文案不变（「成片已通过，可以导出」），提醒放在独立的 poster.hint 里。"""

    write_spec(project)
    out = project / "output" / "story_video" / "PST-T"
    (out / "manifests").mkdir(parents=True)
    (out / "manifests" / "episode.snapshot.json").write_text("{}", encoding="utf-8")
    (out / "01_frames").mkdir(); (out / "02_video").mkdir()
    for sid in ("S1", "S2", "S3"):
        (out / "01_frames" / f"{sid}.png").write_bytes(png_bytes())
        (out / "02_video" / f"{sid}.mp4").write_bytes(b"v")
    (out / "PST-T_final.mp4").write_bytes(b"final")
    decisions = {sid: {"decision": "pass"} for sid in ("S1", "S2", "S3")}
    (out / "manifests" / "qc.json").write_text(json.dumps({
        "schema_version": 1, "updated_at": "2026-09-08T00:00:00Z",
        "gates": {"frames": {"status": "accepted", "items": decisions}, "videos": {"status": "accepted", "items": decisions},
                  "final": {"status": "accepted", "items": {"final": {"decision": "pass"}}}},
    }), encoding="utf-8")
    from webui import generate as G
    from webui.routers import project as project_router

    for name in ("failed_jobs", "inflight_shot_ids", "tail_pending"):
        monkeypatch.setattr(G, name, lambda spec, *a, **k: [])
    # 这里的项目是手写出来的「已完成」状态：有快照、有产物，但没有账本（真项目一定有）。概览会把
    # 「产物在、账本里没有这个 run」当成要重新登记（DRAMA-12），这条用例只关心海报提醒，直接给一份一致的冻结状态
    monkeypatch.setattr(project_router, "freeze_state",
                        lambda spec: {"locked": True, "run": True, "snapshot": True, "mismatch": None})
    s = client.get("/api/episodes/storyvideo/PST-T/summary").json()
    assert s["next_step"] == {"key": "done", "label": "已完成", "hint": "成片已通过，可以导出"}
    assert s["poster"] == {"exists": False, "title": "", "hint": "还没有海报——成片页可以生成一张，交付包会带上"}
    assert s["cover_url"].endswith("/artifact/frame/S1")
    # 英文界面：poster.hint 走同一套 JSON 文案翻译
    en = client.get("/api/episodes/storyvideo/PST-T/summary", headers={"X-StoryStudio-Lang": "en"}).json()
    assert en["poster"]["hint"].startswith("No poster yet") and en["next_step"]["hint"] == "The final cut passed; ready to export"
