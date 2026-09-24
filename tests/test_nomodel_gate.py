"""没配模型时的统一拦截（发第一个 tag 前的审计：DRAMA-01 / FE-02 / DRAMA-15 / FAM-3 / PKG-5 / PC-01 / SC-1 / ADP-07）。

全部用空的模型设置（全新安装的默认状态）走真实代码路径，分三层：
1. 路由层：会发模型请求的接口在起任务之前先查角色配好没有——没配就 400 说清去「模型设置」配哪个角色，
   不起任务、不留任务记录、不锁草稿；「角色配好没有」只有 ``genkit.settings.role_missing_message`` 一处判断。
2. 运行时：「模型没配」这种本地配置错误绝不能记成「提交结果不明」把项目卡死——提交前预检（job 保持 prepared），
   兜底时归成明确失败（可原地重提）。
3. 任务体：批量任务 0 成功、全部失败时任务记 failed，任务中心不再显示「成功」。
另：HTTP 429 限流归「被服务商限流」（可重提），不再说成余额耗尽。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import settings as settings_mod
from genkit.adapters.base import HttpResponse, raise_for_status
from genkit.providers import (
    ImageRequest,
    ProviderCommandError,
    ProviderNotConfigured,
    ProviderRateLimited,
    QuotaExhausted,
)
from genkit.router import RoutedProvider
from storyvideo.runtime import StoryVideoBlocked, StoryVideoError, StoryVideoRunner
from webui import compare as C
from webui import deps, humanize, tasks
from webui.app import app
from webui.routers import clip as clip_router
from webui.routers import episodes as episodes_router
from webui.routers import imageset as imageset_router
from webui.routers import music as music_router
from webui.routers import picturebook as picturebook_router
from webui.routers import podcast as podcast_router
from webui.routers import post as post_router
from webui.routers import script as script_router
from tests.picturebook_fixtures import example_data as picturebook_example
from tests.test_genre_review import CARD_ID as GENRE_ID
from tests.test_podcast_router import body as podcast_body
from tests.test_script_router import body as script_body
from tests.test_storyvideo_runtime import FakeProvider, fake_media, write_episode  # noqa: F401 - fake_media 是夹具

client = TestClient(app)
EP = "NM-EP"


@pytest.fixture
def empty_settings(tmp_path, monkeypatch):
    """全新安装：settings.json 不存在，没有 .env 代理，没有强制后端，也没有环境变量覆盖。"""

    from storyvideo import scriptgen

    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    for name in ("SCRIPTGEN_BACKEND", "SCRIPTGEN_LLM_BASE_URL", "SCRIPTGEN_LLM_API_KEY", "SCRIPTGEN_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    # scriptgen._env 除了环境变量还读 PROJECT_ROOT/.env，开发态那就是仓库根：按 .env.example 配了 SCRIPTGEN_LLM_* 的
    # 开发机上文本模型就算「配好了」，四条「没配文本模型 400」会假失败。指到临时目录才真是「没有 .env 代理」
    monkeypatch.setattr(scriptgen, "PROJECT_ROOT", tmp_path)
    for role in settings_mod.ROLES:
        for key in ("ADAPTER", "BASE_URL", "API_KEY", "MODEL", "VOICE"):
            monkeypatch.delenv(f"STORYSTUDIO_{role.upper()}_{key}", raising=False)
    return tmp_path


@pytest.fixture
def project(empty_settings, monkeypatch):
    """所有模块的路由都指到同一个临时项目根；测试注入点全部关掉，走真实的 build_provider() / scriptgen_backend()。"""

    root = empty_settings
    content = root / "content" / "story_video"
    content.mkdir(parents=True)
    for module in (deps, episodes_router, post_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", root)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content)
    for module, names in (
        (imageset_router, ("PROVIDER_FACTORY",)),
        (picturebook_router, ("PROVIDER_FACTORY", "SYNTHESIZER", "DISPATCH")),
        (music_router, ("PROVIDER_FACTORY",)),
        (clip_router, ("PROVIDER_FACTORY",)),
        (podcast_router, ("DISPATCH",)),
        (script_router, ("DISPATCH",)),
    ):
        for name in names:
            monkeypatch.setattr(module, name, None)
    for sub in ("sticker", "portrait", "image", "picturebook", "music", "clip", "podcast", "script"):
        (root / "content" / sub).mkdir(parents=True, exist_ok=True)
    tasks._running.clear()
    return root


def write_drama(root: Path, episode_id: str = EP, **extra) -> Path:
    spec = {
        "id": episode_id, "output_root": f"output/story_video/{episode_id}", "prompt_language": "zh", "title_zh": "加班夜",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "block": "黑发。"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["lin"], "shot": "中景", "art": "a", "motion": "m",
             "lines": [{"who": "lin", "zh": "又加班。"}]},
            {"id": "S2", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m", "lines": []},
        ],
        **extra,
    }
    path = root / "content" / "story_video" / f"{episode_id}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def wait_task(task_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = client.get(f"/api/tasks/{task_id}").json()
        if item["status"] in ("succeeded", "failed"):
            return item
        time.sleep(0.02)
    raise AssertionError("task did not finish")


def tasks_of(episode_id: str) -> list[dict]:
    return client.get(f"/api/tasks?episode_id={episode_id}").json()


class BrokenImages:
    """每一张都在提交时炸：验证「全部失败」的任务要记 failed（不是配置错误，所以路由预检拦不住它）。"""

    def submit_image(self, request):
        raise ProviderCommandError("网关炸了")


class BrokenMusic:
    def submit_music(self, request):
        raise ProviderCommandError("网关炸了")


# ---------------------------------------------------------------------------- 「角色配好没有」只有一处判断


def test_role_missing_message_is_the_single_source_of_truth(empty_settings):
    st = settings_mod.Settings()
    message = settings_mod.role_missing_message(st, "image")
    assert message == "还没配置生图模型——去「模型设置」给「生图」选一个来源并填密钥（image: 没选适配器）"
    assert settings_mod.role_missing_message(st, "text").startswith("还没配置剧本文本模型")
    st.role("image").adapter = "openai_images"
    st.role("image").model = "gpt-image-1"
    assert settings_mod.role_missing_message(st, "image").endswith("（image.api_key）")
    st.role("image").api_key = "sk-x"
    assert settings_mod.role_missing_message(st, "image") is None
    # RoutedProvider 的提交前预检用的是同一处判断，抛专门的「没配模型」异常
    with pytest.raises(ProviderNotConfigured, match="video 模型未配置"):
        RoutedProvider(st).adapter("video")
    RoutedProvider(st).preflight(ImageRequest(name="x", prompt="p", model="@image"))   # 配好的角色不抛
    with pytest.raises(ProviderNotConfigured):
        RoutedProvider(settings_mod.Settings()).preflight(ImageRequest(name="x", prompt="p"))


def test_text_model_gate_reads_the_real_backend_value(empty_settings):
    """scriptgen_backend() 没配时返回 "unconfigured"（非空字符串）——bool() 判断恒真，播客与剧本工作台的 400 闸从没生效过。"""

    from storyvideo import scriptgen

    assert scriptgen.scriptgen_backend() == "unconfigured"
    assert scriptgen.text_model_available() is False
    assert podcast_router._text_model_ready() is False and script_router._text_model_ready() is False


# ---------------------------------------------------------------------------- 运行时（DRAMA-01 / FE-02）


def test_runtime_preflight_keeps_job_prepared_when_image_role_is_unconfigured(empty_settings, fake_media):
    """真实 RoutedProvider + 空设置：run() 以明确的「模型未配置」错误停下，账本里一笔提交意图都不记，项目不卡死。"""

    root = empty_settings
    write_episode(root)
    with StoryVideoRunner(root, provider=RoutedProvider(settings_mod.Settings())) as runner:
        with pytest.raises(StoryVideoError, match="image 模型未配置") as info:
            runner.run("EP99", through="frames")
        assert not isinstance(info.value, StoryVideoBlocked)
        job = runner.store.get_job("EP99", "frame-S1")
        assert job["status"] == "prepared" and job["attempt"] == 0 and job["ambiguous_submission"] is False
        snapshot = runner.store.snapshot("EP99")
        assert snapshot["ambiguous_submissions"] == [] and snapshot["run"]["status"] != "blocked_submission_unknown"
        # 再点一次还是同一句人话，不是「submission outcome is unknown」
        with pytest.raises(StoryVideoError, match="image 模型未配置"):
            runner.run("EP99", through="frames")
    # 配好模型（这里换成能提交的假来源）就能接着走
    provider = FakeProvider()
    with StoryVideoRunner(root, provider=provider) as runner:
        runner.run("EP99", through="frames")
    assert len(provider.submissions) == 1


def test_runtime_not_configured_at_submit_is_a_definite_retryable_failure(empty_settings, fake_media):
    """兜底：没有 preflight 的老式来源在 submit 时才抛「没配模型」——记成明确失败（可重提），绝不是「结果不明」。"""

    root = empty_settings
    write_episode(root)
    provider = FakeProvider(submit_error=ProviderNotConfigured("image 模型未配置：image.api_key（在「模型设置」里填好再生成）"))
    with StoryVideoRunner(root, provider=provider) as runner:
        with pytest.raises(StoryVideoError, match="模型未配置") as info:
            runner.run("EP99", through="frames")
        assert not isinstance(info.value, StoryVideoBlocked)
        job = runner.store.get_job("EP99", "frame-S1")
        assert job["status"] == "failed" and job["ambiguous_submission"] is False
        assert humanize.classify_failure(job["error"])["kind"] == "config"
        assert runner.store.snapshot("EP99")["run"]["status"] == "frames_failed"
        assert runner.retry_failed("EP99")["reset"] == ["frame-S1"]
    ok = FakeProvider()
    with StoryVideoRunner(root, provider=ok) as runner:
        runner.run("EP99", through="frames")
    assert len(ok.submissions) == 1


def test_source_missing_its_local_tool_is_a_definite_retryable_failure(empty_settings, fake_media, load_plugin):
    """只要 model 且有默认值的来源（插件常见）——「模型设置」里选上它就算配好，接口层 400 与提交前预检都拦不住；
    本机缺它要的命令行时，起子进程之前抛的 ProviderSetupRequired 必须记成明确失败（可重提），不能被当成
    「提交结果不明」把项目卡死。用示例插件的 missing_tool 模拟。"""

    assert load_plugin("example_async").status == "loaded"
    root = empty_settings
    path = write_episode(root)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["cast"]["su"].pop("asset")   # 不带参考图：只看「命令行没装」这一条
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    st = settings_mod.Settings()
    st.role("image").adapter = "example_async_images"
    st.role("image").extra = {"missing_tool": "example-tool-definitely-missing"}
    assert settings_mod.role_missing_message(st, "image") is None
    provider = RoutedProvider(st)
    with StoryVideoRunner(root, provider=provider) as runner:
        with pytest.raises(StoryVideoError, match="找不到示例命令 example-tool-definitely-missing") as info:
            runner.run("EP99", through="frames")
        assert not isinstance(info.value, StoryVideoBlocked)
        assert "「模型设置」" in humanize.humanize(info.value)     # 不是「多半是临时的，直接重提」
        job = runner.store.get_job("EP99", "frame-S1")
        assert job["status"] == "failed" and job["ambiguous_submission"] is False and job["task_id"] is None
        assert job["error"].startswith("ProviderSetupRequired: ")
        assert humanize.classify_failure(job["error"])["kind"] == "config"
        snapshot = runner.store.snapshot("EP99")
        assert snapshot["ambiguous_submissions"] == [] and snapshot["run"]["status"] == "frames_failed"
        assert runner.retry_failed("EP99")["reset"] == ["frame-S1"]
        # 还没装好就再点一次：仍是同一句人话，不是「submission outcome is unknown … reconcile」
        with pytest.raises(StoryVideoError, match="找不到示例命令") as info:
            runner.run("EP99", through="frames")
        assert not isinstance(info.value, StoryVideoBlocked)
        assert runner.retry_failed("EP99")["reset"] == ["frame-S1"]
    # 装好命令行（这里换成能提交的假来源）就能接着走
    ok = FakeProvider()
    with StoryVideoRunner(root, provider=ok) as runner:
        runner.run("EP99", through="frames")
    assert len(ok.submissions) == 1


# ---------------------------------------------------------------------------- 限流（ADP-07）


def test_http_429_is_rate_limiting_unless_the_body_says_quota():
    with pytest.raises(ProviderRateLimited):
        raise_for_status(HttpResponse(429, b'{"error": {"message": "Rate limit reached for requests, retry after 20s"}}'), what="x")
    with pytest.raises(QuotaExhausted) as info:
        raise_for_status(HttpResponse(429, b'{"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}}'), what="x")
    assert not isinstance(info.value, ProviderRateLimited)
    with pytest.raises(QuotaExhausted) as info:
        raise_for_status(HttpResponse(402, b"payment required"), what="x")
    assert not isinstance(info.value, ProviderRateLimited)

    from genkit.adapters.minimax_speech import classify_base_resp

    with pytest.raises(ProviderRateLimited):
        classify_base_resp({"base_resp": {"status_code": 1002, "status_msg": "rate limit"}}, what="tts")
    with pytest.raises(QuotaExhausted) as info:
        classify_base_resp({"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}, what="tts")
    assert not isinstance(info.value, ProviderRateLimited)

    # 展示层：限流有自己的标签与建议；裸 429 不再算余额问题
    assert humanize.classify_failure("ProviderRateLimited: images: HTTP 429: Rate limit reached")["kind"] == "ratelimit"
    assert humanize.classify_failure("images: HTTP 429: too many requests")["kind"] == "ratelimit"
    assert humanize.classify_failure("QuotaExhausted: images: HTTP 429: insufficient_quota")["kind"] == "quota"
    assert humanize.classify_failure("ProviderNotConfigured: image 模型未配置：image.api_key")["kind"] == "config"
    assert humanize.FAILURE_ADVICE["ratelimit"]["retryable"] and humanize.FAILURE_ADVICE["config"]["retryable"]


def test_openai_sdk_rate_limit_error_is_split_by_body_code():
    openai = pytest.importorskip("openai")
    import httpx

    from genkit.adapters.openai_compat import _translate

    def rate_limit(body: dict):
        response = httpx.Response(429, request=httpx.Request("POST", "https://x/v1/chat/completions"), json={"error": body})
        return openai.RateLimitError(body["message"], response=response, body=body)

    transient = _translate(rate_limit({"code": "rate_limit_exceeded", "message": "Rate limit reached for gpt-4o, retry after 2s"}), "chat")
    assert isinstance(transient, ProviderRateLimited)
    quota = _translate(rate_limit({"code": "insufficient_quota", "message": "You exceeded your current quota"}), "chat")
    assert isinstance(quota, QuotaExhausted) and not isinstance(quota, ProviderRateLimited)


def test_runtime_rate_limit_is_a_retryable_failure(empty_settings, fake_media):
    root = empty_settings
    write_episode(root)
    provider = FakeProvider(submit_error=ProviderRateLimited("images: HTTP 429: Rate limit reached, retry after 20s"))
    with StoryVideoRunner(root, provider=provider) as runner:
        with pytest.raises(StoryVideoError, match="submission rejected"):
            runner.run("EP99", through="frames")
        job = runner.store.get_job("EP99", "frame-S1")
        assert job["status"] == "failed" and job["ambiguous_submission"] is False
        info = humanize.classify_failure(job["error"])
        assert info["kind"] == "ratelimit" and info["label"] == "被服务商限流" and info["retryable"]
        assert runner.retry_failed("EP99")["reset"] == ["frame-S1"]


# ---------------------------------------------------------------------------- 短剧：生成 / 对比 / 海报 / 审稿


def test_drama_generate_is_400_and_does_not_lock_the_draft(project):
    write_drama(project)
    est = client.get(f"/api/episodes/storyvideo/{EP}/generate-estimate?through=frames").json()
    assert est["pending_count"] == 2
    r = client.post(f"/api/episodes/storyvideo/{EP}/generate", json={"through": "frames", "plan_token": est["plan_token"], "wait": False})
    assert r.status_code == 400, r.text
    assert "生图" in r.json()["detail"] and "「模型设置」" in r.json()["detail"]
    assert client.get(f"/api/episodes/storyvideo/{EP}").json()["locked"] is False
    again = client.get(f"/api/episodes/storyvideo/{EP}/generate-estimate?through=frames").json()
    assert again["pending_count"] == 2 and again["in_progress"] == [] and again["failed"] == []
    assert client.get(f"/api/episodes/storyvideo/{EP}/failures").json()["count"] == 0
    # 视频阶段：首帧还没出的要生图模型，视频本身要视频模型——同样在提交前拦
    est = client.get(f"/api/episodes/storyvideo/{EP}/generate-estimate?through=videos").json()
    r = client.post(f"/api/episodes/storyvideo/{EP}/generate", json={"through": "videos", "plan_token": est["plan_token"], "wait": False})
    assert r.status_code == 400 and "「模型设置」" in r.json()["detail"]
    assert client.get(f"/api/episodes/storyvideo/{EP}").json()["locked"] is False


def test_drama_generate_with_video_role_missing_names_the_video_role(project):
    write_drama(project)
    st = settings_mod.Settings()
    st.role("image").adapter = "openai_images"
    st.role("image").model = "gpt-image-1"
    st.role("image").api_key = "sk-x"
    settings_mod.save_settings(st)
    est = client.get(f"/api/episodes/storyvideo/{EP}/generate-estimate?through=videos").json()
    r = client.post(f"/api/episodes/storyvideo/{EP}/generate", json={"through": "videos", "plan_token": est["plan_token"], "wait": False})
    assert r.status_code == 400 and "视频" in r.json()["detail"] and "「视频」" in r.json()["detail"]


def test_compare_is_400_without_image_model_and_all_failed_marks_task_failed(project, monkeypatch):
    write_drama(project)
    url = f"/api/episodes/storyvideo/{EP}/compare/frames"
    r = client.post(url, json={"shot_id": "S1", "models": ["m1"]})
    assert r.status_code == 400 and "生图" in r.json()["detail"]
    assert tasks_of(f"{EP}:S1") == []
    # 配置没问题但每个模型都失败：任务记 failed 带原因，候选清单里仍逐行记着
    monkeypatch.setattr(C, "build_provider", lambda: BrokenImages())
    r = client.post(url, json={"shot_id": "S1", "models": ["m1", "m2"]})
    assert r.status_code == 200, r.text
    task = wait_task(r.json()["task_id"])
    assert task["status"] == "failed" and "全部失败" in task["error"] and "网关炸了" in task["error"]
    listed = client.get(f"{url}/S1").json()["candidates"]
    assert len(listed) == 2 and all(c["error"] == "网关炸了" for c in listed)


def test_poster_from_model_is_400_without_image_model(project):
    write_drama(project)
    r = client.post(f"/api/episodes/storyvideo/{EP}/poster/generate", json={"source": "model", "title": "夜色"})
    assert r.status_code == 400 and "生图" in r.json()["detail"]
    assert tasks_of(EP) == []


def test_drama_reviews_are_400_without_text_model(project):
    write_drama(project, genre=GENRE_ID)
    for path in ("craft-review", "genre-review"):
        r = client.post(f"/api/episodes/storyvideo/{EP}/{path}")
        assert r.status_code == 400 and "文本模型" in r.json()["detail"], (path, r.text)
    assert tasks_of(EP) == []


# ---------------------------------------------------------------------------- 播客 / 剧本工作台（PC-01 / SC-1）


def test_podcast_text_endpoints_are_400_without_text_model(project):
    pid = client.post("/api/podcast", json=podcast_body()).json()["id"]
    for path in ("draft", "segments/G01/redraft", "humanlike-review"):
        r = client.post(f"/api/podcast/{pid}/{path}", json={})
        assert r.status_code == 400 and "文本模型" in r.json()["detail"], (path, r.text)
    assert tasks_of(pid) == []


def test_script_lab_endpoints_are_400_without_text_model(project):
    sid = client.post("/api/script", json=script_body()).json()["id"]
    for path in ("generate", "review"):
        r = client.post(f"/api/script/{sid}/{path}", json={})
        assert r.status_code == 400 and "文本模型" in r.json()["detail"], (path, r.text)
    assert tasks_of(sid) == []


# ---------------------------------------------------------------------------- 图片集 / 图片工作台 / 绘本 / 配乐 / 单条视频（FAM-3 / PKG-5）


@pytest.mark.parametrize("module", ["sticker", "portrait", "image"])
def test_imageset_generate_is_400_without_image_model(project, module):
    sid = client.post(f"/api/{module}/examples/create", json={}).json()["id"]
    r = client.post(f"/api/{module}/{sid}/generate", json={})
    assert r.status_code == 400 and "生图" in r.json()["detail"] and "「模型设置」" in r.json()["detail"]
    assert tasks_of(sid) == []
    detail = client.get(f"/api/{module}/{sid}").json()
    assert all(not item.get("attempts") for item in detail["items"])     # 没往项目文件里记失败尝试


def test_imageset_all_failed_marks_task_failed(project, monkeypatch):
    monkeypatch.setattr(imageset_router, "PROVIDER_FACTORY", lambda: BrokenImages())
    sid = client.post("/api/image/examples/create", json={}).json()["id"]
    r = client.post(f"/api/image/{sid}/generate", json={})
    assert r.status_code == 200, r.text
    task = wait_task(r.json()["task_id"])
    assert task["status"] == "failed" and "全部失败" in task["error"] and "网关炸了" in task["error"]


def test_picturebook_pages_is_400_without_image_model_and_all_failed_is_failed(project, monkeypatch):
    bid = client.post("/api/picturebook", json=picturebook_example()).json()["id"]
    r = client.post(f"/api/picturebook/{bid}/pages/generate", json={})
    assert r.status_code == 400 and "生图" in r.json()["detail"]
    assert tasks_of(bid) == []
    monkeypatch.setattr(picturebook_router, "PROVIDER_FACTORY", lambda: BrokenImages())
    r = client.post(f"/api/picturebook/{bid}/pages/generate", json={"only": ["P01", "P02"]})
    assert r.status_code == 200, r.text
    task = wait_task(r.json()["task_id"])
    assert task["status"] == "failed" and "全部失败" in task["error"] and "网关炸了" in task["error"]


def test_picturebook_narration_gate_and_all_failed(project, monkeypatch):
    bid = client.post("/api/picturebook", json=picturebook_example()).json()["id"]
    # 中文朗读绑定一个「模型设置」来源的音色档案：语音角色没配 → 400
    v = client.post("/api/voices", json={"id": "nm-voice", "name": "设置来源", "engine": "settings", "voice": "alloy"})
    assert v.status_code == 200, v.text
    spec = client.get(f"/api/picturebook/{bid}").json()["spec"]
    spec["narration"] = {**(spec.get("narration") or {}), "zh_voice": "nm-voice"}
    assert client.put(f"/api/picturebook/{bid}", json=spec).status_code == 200
    r = client.post(f"/api/picturebook/{bid}/narration/generate", json={"languages": ["zh"]})
    assert r.status_code == 400 and "语音" in r.json()["detail"] and "「模型设置」" in r.json()["detail"]
    assert tasks_of(bid) == []
    # 合成器每一句都炸：任务记 failed；失败原因不再带异常类名前缀
    def broken(profile, text, destination):
        raise ProviderCommandError("voice 模型未配置：voice: 没选适配器（在「模型设置」里填好再生成）")

    monkeypatch.setattr(picturebook_router, "SYNTHESIZER", broken)
    r = client.post(f"/api/picturebook/{bid}/narration/generate", json={"languages": ["zh"], "only": ["P01", "P02"]})
    assert r.status_code == 200, r.text
    task = wait_task(r.json()["task_id"])
    assert task["status"] == "failed" and "全部失败" in task["error"]
    assert "ProviderCommandError" not in task["error"]


def test_music_generate_is_400_without_music_model_and_all_failed_is_failed(project, monkeypatch):
    body = {"title": "睡前摇篮曲", "brief": {"usage": "绘本睡前朗读的底噪", "genre": "摇篮曲", "mood": ["温柔"]}, "duration": 120, "count": 2}
    sid = client.post("/api/music", json=body).json()["id"]
    r = client.post(f"/api/music/{sid}/generate", json={"count": 2})
    assert r.status_code == 400 and "配乐" in r.json()["detail"] and "「模型设置」" in r.json()["detail"]
    assert tasks_of(sid) == [] and client.get(f"/api/music/{sid}").json()["attempts"] == []
    monkeypatch.setattr(music_router, "PROVIDER_FACTORY", lambda: BrokenMusic())
    r = client.post(f"/api/music/{sid}/generate", json={"count": 2})
    assert r.status_code == 200, r.text
    task = wait_task(r.json()["task_id"])
    assert task["status"] == "failed" and "全部失败" in task["error"] and "网关炸了" in task["error"]


def test_clip_first_frame_and_generate_are_400_without_models(project):
    cid = client.post("/api/clip/examples/create", json={}).json()["id"]
    r = client.post(f"/api/clip/{cid}/first-frame", json={"mode": "generate"})
    assert r.status_code == 400 and "生图" in r.json()["detail"]
    r = client.post(f"/api/clip/{cid}/generate", json={})
    assert r.status_code == 400 and "「模型设置」" in r.json()["detail"]
    assert tasks_of(cid) == []
