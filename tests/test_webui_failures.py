"""失败任务：原因分类（余额/审核/超时/其他）、人话翻译、失败清单接口、重提（审核类默认跳过）、
预估里标出失败、概览下一步指向失败处理、运行时把异常类名记进账本。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit.providers import ProviderRejected, QuotaExhausted
from storyvideo.runtime import StoryVideoRunner
from webui import deps
from webui.app import app
from webui.generate import estimate_plan, failed_jobs
from webui.humanize import classify_failure, humanize
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router

client = TestClient(app)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    return tmp_path


def write_spec(root: Path) -> Path:
    spec = {
        "id": "FAIL-T", "output_root": "output/story_video/FAIL-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"a": {"name": "阿一", "block": "黑发。"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["a"], "shot": "近景", "art": "a", "motion": "m", "lines": []},
            {"id": "S2", "dur": 4, "cast": ["a"], "shot": "中景", "art": "a", "motion": "m", "lines": []},
            {"id": "S3", "dur": 4, "cast": ["a"], "shot": "远景", "art": "a", "motion": "m", "lines": []},
        ],
    }
    path = root / "content" / "story_video" / "FAIL-T.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def test_classify_failure_kinds():
    assert classify_failure("QuotaExhausted: openrouter images: HTTP 402: insufficient credits")["kind"] == "quota"
    assert classify_failure("ark images: Error: content moderation: sensitive")["kind"] == "moderation"
    assert classify_failure("ProviderRejected: openai images: flagged")["kind"] == "moderation"
    assert classify_failure("ProviderTimeout: POST https://x: timeout")["kind"] == "timeout"
    assert classify_failure("ProviderUnavailable: gateway down")["kind"] == "timeout"
    # 本机缺来源要的程序（ProviderSetupRequired，ProviderUnavailable 的子类）归「模型没配置」，不说成「多半是临时的」
    assert classify_failure("ProviderSetupRequired: example-cli not found")["kind"] == "config"
    assert classify_failure("ProviderNotConfigured: 「生图」的来源 x_images 现在用不了")["kind"] == "config"
    assert classify_failure("上游返回了未知状态：xyz")["kind"] == "other"
    assert classify_failure(None)["kind"] == "other"
    assert classify_failure("x moderation x")["retryable"] is False
    assert classify_failure("x timeout x")["retryable"] is True


@pytest.mark.parametrize("text", [
    "ProviderCommandError: example: 第 1402 帧解码失败",                               # 帧号里带 402
    "ProviderCommandError: GET https://api.example.com/v1/tasks/84029/402a: not found",  # URL 里的任务号
    "ProviderCommandError: ark video status: request_id=20250922140212ABCDEF bad",       # request_id 一长串数字
    "openrouter video: generation gen-1402abc failed",
    "ProviderCommandError: sdwebui: HTTP 500: CUDA out of memory; insufficient memory on GPU",
    "ProviderCommandError: comfyui: InvalidParameter: white balance must be auto",
    "ProviderCommandError: openai video: HTTP 403: Access denied by CORS policy",
    "ProviderCommandError: comfyui: retry policy exhausted after 3 attempts",
    "ProviderCommandError: x: GET https://cdn.example.com/402 returned 404",               # https 后面紧跟的不是状态码
])
def test_classify_failure_does_not_guess_quota_from_stray_digits_or_words(text):
    """发布前加固：以前拿裸子串 "402" / "insufficient" / "balance" / "policy" 判欠费和审核——帧号、URL、request_id、
    显存不足、白平衡参数都被说成「余额或配额不足，去充值」（审核类还会把重提按钮藏掉）。这些都该归「其他」。"""

    info = classify_failure(text)
    assert info["kind"] == "other", (text, info)
    assert info["retryable"] is True


@pytest.mark.parametrize("text, kind", [
    # 402 只认状态码的形状
    ("QuotaExhausted: openrouter images: HTTP 402: insufficient credits", "quota"),
    ('ProviderCommandError: gateway: {"code": 402, "message": "x"}', "quota"),
    ("ProviderCommandError: ark images: Error code: 402 - {}", "quota"),
    ("ProviderCommandError: x: status=402", "quota"),
    ("ProviderCommandError: 网关返回状态码 402", "quota"),
    ("ProviderCommandError: openai: You exceeded your current quota", "quota"),
    ("QuotaExhausted: tts: insufficient balance", "quota"),
    ("ProviderCommandError: example: 积分不足", "quota"),
    ("ProviderCommandError: ark images: AccountOverdueError", "quota"),
    # 429：响应体说欠费的归欠费，其余归限流（稍后重试），不再劝人充值
    ("QuotaExhausted: images: HTTP 429: insufficient_quota", "quota"),
    ("ProviderCommandError: images: HTTP 429", "ratelimit"),
    ("ProviderCommandError: ark video submit: Error code: 429 - server busy", "ratelimit"),
    ("ProviderCommandError: x: HTTP/1.1 429", "ratelimit"),
    ('ProviderCommandError: x: {"status_code": 429}', "ratelimit"),
    ("ProviderCommandError: example: 请求过于频繁，请稍后再试", "ratelimit"),
    # 中文直接贴着 HTTP（审查：中文字在正则里也算词字符，\b 在「回」和「H」之间不成立，状态码整个认不出）
    ("ProviderCommandError: 服务商返回HTTP 429", "ratelimit"),
    ("ProviderCommandError: 请求失败HTTP 402", "quota"),
    ("ProviderCommandError: HTTP错误429", "ratelimit"),
    # urllib 的「HTTP Error 429」；有的命令行工具用 axios，会报「Request failed with status code 429」
    ("ProviderCommandError: x: HTTP Error 429", "ratelimit"),
    ("ProviderCommandError: example: Request failed with status code 429", "ratelimit"),
    ("ProviderCommandError: example: Request failed with status code 402", "quota"),
    # 明确的审核字眼照旧
    ("ProviderCommandError: openai images: Your request violates our usage policies", "moderation"),
    ("ProviderCommandError: x: content_policy_violation", "moderation"),
    # 审查：「违反政策」的说法也是审核拦截（收窄 policy 时漏掉了，会亮出重提按钮、重提还是被拒）
    ("ProviderCommandError: x: request blocked due to policy violation", "moderation"),
    ("ProviderCommandError: x: PolicyViolation", "moderation"),
    ("ProviderCommandError: x: the prompt violates our policies", "moderation"),
])
def test_classify_failure_reads_status_code_shapes_and_explicit_words(text, kind):
    info = classify_failure(text)
    assert info["kind"] == kind, (text, info)
    if kind == "ratelimit":
        assert info["label"] == "被服务商限流" and "充值" not in info["advice"]


def test_openrouter_402_is_read_from_the_status_code_not_the_text():
    """OpenRouter 适配器以前也在文案里找裸 "402"：生成 id 里带 402 的普通失败被记成 QuotaExhausted（账本里就成了欠费）。"""

    from genkit.adapters.openrouter_adapters import _translate
    from genkit.providers import ProviderCommandError

    stray = _translate(RuntimeError("generation gen-1402abc failed: upstream error"), "openrouter video")
    assert type(stray) is ProviderCommandError

    class PaymentRequired(Exception):
        status_code = 402

    assert isinstance(_translate(PaymentRequired("payment needed"), "openrouter images"), QuotaExhausted)
    assert isinstance(_translate(RuntimeError("Insufficient credits"), "openrouter images"), QuotaExhausted)


def test_humanize_carries_advice_for_rejections():
    quota = humanize("submission rejected for frame-S1: QuotaExhausted: HTTP 402: insufficient credits")
    assert quota.startswith("frame-S1 被服务商拒绝") and "充值" in quota and "insufficient credits" in quota
    moderated = humanize("submission rejected for frame-S2: ProviderRejected: content moderation")
    assert "改写" in moderated
    unknown = humanize("submission outcome for frame-S3 is unknown; refusing retry: ProviderTimeout: POST x: timeout")
    assert "没拿到结果" in unknown and "不会自动重提" in unknown


def _fail_jobs(root: Path):
    with StoryVideoRunner(root) as runner:
        runner.prepare("FAIL-T")
        runner.store.mark_failed("FAIL-T", "frame-S1", "QuotaExhausted: openrouter images: HTTP 402: insufficient credits")
        runner.store.mark_failed("FAIL-T", "frame-S2", "ProviderRejected: openrouter images: input flagged by moderation")


def test_failures_listing_estimate_and_next_step(project):
    write_spec(project)
    assert client.get("/api/episodes/storyvideo/FAIL-T/failures").json() == {"items": [], "count": 0, "retryable": [], "moderation": []}
    _fail_jobs(project)

    out = client.get("/api/episodes/storyvideo/FAIL-T/failures").json()
    assert out["count"] == 2 and out["retryable"] == ["S1"] and out["moderation"] == ["S2"]
    by_shot = {i["shot_id"]: i for i in out["items"]}
    assert by_shot["S1"]["kind"] == "quota" and by_shot["S1"]["stage"] == "frames" and by_shot["S1"]["step_id"] == "frame-S1"
    assert by_shot["S2"]["kind"] == "moderation" and by_shot["S2"]["retryable"] is False and "改写" in by_shot["S2"]["advice"]

    est = client.get("/api/episodes/storyvideo/FAIL-T/generate-estimate?through=frames").json()
    assert est["failed"] == ["S1", "S2"] and est["pending_count"] == 3
    assert [i["failed"] for i in est["items"]] == [True, True, False]

    summary = client.get("/api/episodes/storyvideo/FAIL-T/summary").json()
    assert summary["next_step"]["key"] == "failed" and summary["next_step"]["shots"] == ["S1", "S2"]
    assert "内容被审核拦截" in summary["next_step"]["hint"] and "余额或配额不足" in summary["next_step"]["hint"]
    assert {f["shot_id"] for f in summary["failures"]} == {"S1", "S2"}


def test_retry_failed_skips_moderation_unless_forced(project):
    write_spec(project)
    _fail_jobs(project)
    r = client.post("/api/episodes/storyvideo/FAIL-T/retry-failed", json={}).json()
    assert r["reset"] == ["frame-S1"] and r["skipped_moderation"] == ["S2"]
    assert r["advice"] and "S2" in r["advice"][0] and "改写" in r["advice"][0]
    left = client.get("/api/episodes/storyvideo/FAIL-T/failures").json()
    assert [i["shot_id"] for i in left["items"]] == ["S2"]
    forced = client.post("/api/episodes/storyvideo/FAIL-T/retry-failed", json={"force": True}).json()
    assert forced["reset"] == ["frame-S2"] and forced["skipped_moderation"] == [] and forced["advice"] == []
    assert client.get("/api/episodes/storyvideo/FAIL-T/failures").json()["count"] == 0
    assert client.get("/api/episodes/storyvideo/FAIL-T/summary").json()["next_step"]["key"] == "frames"
    assert client.post("/api/episodes/storyvideo/FAIL-T/retry-failed", json={}).status_code == 409
    # only 过滤：不在清单里的镜不动
    _fail_jobs(project)
    r = client.post("/api/episodes/storyvideo/FAIL-T/retry-failed", json={"only": ["S3"]})
    assert r.status_code == 409


class RejectingProvider:
    name = "fake"

    def __init__(self, exc: Exception):
        self.exc = exc

    def upload_asset(self, path, *, asset_type="image", workspace_id=None):
        return f"upload-{Path(path).stem}"

    def submit(self, request):
        raise self.exc

    def get_task(self, task_id, task_type):
        raise AssertionError("not polled")

    def download(self, url, destination, **kwargs):
        raise AssertionError("not downloaded")


@pytest.mark.parametrize("exc,kind", [
    (QuotaExhausted("openrouter images: HTTP 402: insufficient credits"), "quota"),
    (ProviderRejected("openrouter images: input flagged by moderation"), "moderation"),
])
def test_runtime_records_exception_class_in_ledger(project, exc, kind):
    write_spec(project)
    with StoryVideoRunner(project, provider=RejectingProvider(exc)) as runner:
        runner.prepare("FAIL-T")
        with pytest.raises(Exception) as info:
            runner.run("FAIL-T", only=["S1"], through="frames")
        assert "submission rejected for frame-S1" in str(info.value)
        job = runner.store.get_job("FAIL-T", "frame-S1")
    assert job["status"] == "failed" and job["error"].startswith(type(exc).__name__ + ": ")
    from storyvideo.spec import load_spec

    spec = load_spec("FAIL-T", project_root=project)
    items = failed_jobs(spec)
    assert len(items) == 1 and items[0]["kind"] == kind
    assert estimate_plan(spec, through="frames", only=None)["failed"] == ["S1"]
