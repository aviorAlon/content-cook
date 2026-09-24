"""出配乐（docs/music-spec.md §2）：N 条一轮、从不覆盖、请求哈希跳过、失败隔离、异步轮询与等待上限。
用假 provider（不联网、不花钱）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from genkit.providers import MusicRequest, ProviderCommandError, TaskSnapshot, TaskState, TaskSubmission, TaskType
from music import generate as G
from music import spec as S


@pytest.fixture(autouse=True)
def _no_music_source(tmp_path, monkeypatch):
    """没给 limits 的调用按「模型设置」查来源上限：设置指到一个不存在的文件、清掉配乐角色的环境变量，
    本机设置里配着哪个来源都不影响结果（= 核心上限）。"""

    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "no-settings.json"))
    for key in ("ADAPTER", "BASE_URL", "API_KEY", "MODEL", "VOICE"):
        monkeypatch.delenv(f"STORYSTUDIO_MUSIC_{key}", raising=False)


def _spec(tmp_path, **over):
    data = {"title": "睡前摇篮曲", "brief": {"usage": "睡前底噪", "genre": "摇篮曲", "mood": ["温柔"]},
            "duration": 120, "count": 2, **over}
    spec = S.normalize(data, project_root=tmp_path, spec_id="MU-1")
    S.save(spec)
    return spec


class FakeProvider:
    """按脚本回放提交结果；``download`` 真写一个小文件。"""

    def __init__(self, *, script=None, urls=None, async_rounds=0):
        self.script = list(script or [])
        self.urls = urls or "https://cdn.example.com/m.mp3"
        self.async_rounds = async_rounds
        self.requests: list[MusicRequest] = []
        self.downloads: list[tuple[str, str]] = []
        self.polls = 0

    def submit_music(self, request: MusicRequest) -> TaskSubmission:
        self.requests.append(request)
        if self.script:
            outcome = self.script.pop(0)
        else:
            outcome = "ok"
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "rejected":
            return TaskSubmission(task_id=None, task_type=TaskType.MUSIC, status=TaskState.FAILED, error_message="内容被审核拦截")
        if outcome == "async":
            return TaskSubmission(task_id="exasync-mus:t1", task_type=TaskType.MUSIC, status=TaskState.PENDING)
        return TaskSubmission(task_id="url:x", task_type=TaskType.MUSIC, status=TaskState.FINISHED, output_urls=(self.urls,))

    def get_task(self, task_id: str, task_type) -> TaskSnapshot:
        self.polls += 1
        if self.polls <= self.async_rounds:
            return TaskSnapshot(task_id=task_id, task_type=TaskType.MUSIC, status=TaskState.PROCESSING)
        return TaskSnapshot(task_id=task_id, task_type=TaskType.MUSIC, status=TaskState.FINISHED, output_urls=(self.urls,))

    def download(self, url: str, destination) -> Path:
        self.downloads.append((url, str(destination)))
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(b"ID3-fake-audio")
        return Path(destination)


def test_generates_n_tracks_and_adopts_the_first(tmp_path):
    spec = _spec(tmp_path)
    provider = FakeProvider()
    result = G.generate_music(spec, provider=provider)
    assert result["failed"] == [] and result["skipped"] == []
    assert result["generated"] == ["MU-1-1.mp3", "MU-1-2.mp3"]
    assert len(provider.requests) == 2
    assert all(r.count == 1 for r in provider.requests)          # 每次请求出一条
    assert provider.requests[0].duration == 120 and provider.requests[0].model == "@music"
    assert "睡前底噪" in provider.requests[0].prompt
    assert spec["current"] == "MU-1-1.mp3"
    assert len(spec["attempts"]) == 2 and all(a["request_hash"] for a in spec["attempts"])
    for name in result["generated"]:
        assert (S.raw_dir(spec) / name).is_file()
    # 逐条写盘：任务中途挂了也不丢
    assert len(S.load("MU-1", project_root=tmp_path)["attempts"]) == 2


def test_never_overwrites_existing_files(tmp_path):
    spec = _spec(tmp_path, count=1)
    G.generate_music(spec, provider=FakeProvider())
    first = S.raw_dir(spec) / "MU-1-1.mp3"
    (first).write_bytes(b"ORIGINAL")
    G.generate_music(spec, provider=FakeProvider(), force=True)
    assert first.read_bytes() == b"ORIGINAL"
    assert (S.raw_dir(spec) / "MU-1-2.mp3").is_file()
    assert sorted(p.name for p in S.raw_dir(spec).glob("*.mp3")) == ["MU-1-1.mp3", "MU-1-2.mp3"]


def test_hash_skip_and_force(tmp_path):
    spec = _spec(tmp_path, count=1)
    G.generate_music(spec, provider=FakeProvider())
    provider = FakeProvider()
    again = G.generate_music(spec, provider=provider)
    assert again["skipped"] == ["MU-1-1.mp3"] and again["generated"] == [] and provider.requests == []
    # force 重出
    forced = G.generate_music(spec, provider=FakeProvider(), force=True)
    assert forced["generated"] == ["MU-1-2.mp3"]
    # brief 改了 → 哈希变 → 不再跳过
    spec["brief"]["mood"] = ["紧张"]
    changed = G.generate_music(spec, provider=FakeProvider())
    assert changed["generated"] == ["MU-1-3.mp3"] and changed["skipped"] == []


def test_stale_marks_a_changed_brief(tmp_path):
    spec = _spec(tmp_path, count=1)
    G.generate_music(spec, provider=FakeProvider())
    assert G.status(spec)["stale"] is False and G.status(spec)["done"] is True
    spec["brief"]["genre"] = "史诗管弦"
    assert G.status(spec)["stale"] is True


def test_one_failure_does_not_stop_the_rest(tmp_path):
    spec = _spec(tmp_path, count=3)
    provider = FakeProvider(script=["ok", "rejected", "ok"])
    result = G.generate_music(spec, provider=provider)
    assert len(result["generated"]) == 2 and len(result["failed"]) == 1
    assert result["failed"][0]["index"] == 2 and "审核" in result["failed"][0]["error"]
    assert len(spec["attempts"]) == 3
    failed = [a for a in spec["attempts"] if a["error"]]
    assert failed[0]["file"] == ""                      # 失败的那条不占产物文件名
    assert spec["current"] == result["generated"][0]
    assert [a["file"] for a in S.ok_attempts(spec)] == result["generated"]


def test_provider_exception_is_caught_per_track(tmp_path):
    spec = _spec(tmp_path, count=2)
    provider = FakeProvider(script=[ProviderCommandError("网关炸了"), "ok"])
    result = G.generate_music(spec, provider=provider)
    assert len(result["failed"]) == 1 and "网关炸了" in result["failed"][0]["error"]
    assert result["generated"] == ["MU-1-2.mp3"]


def test_async_polling_and_timeout(tmp_path):
    spec = _spec(tmp_path, count=1)
    slept: list[float] = []
    provider = FakeProvider(script=["async"], async_rounds=2)
    result = G.generate_music(spec, provider=provider, poll=0.01, sleep=slept.append)
    assert result["generated"] == ["MU-1-1.mp3"] and provider.polls == 3 and len(slept) == 2
    # 等待上限：一直 PROCESSING → 这一条放弃，不拖死整轮
    spec2 = _spec(tmp_path, count=1)
    spec2["id"] = "MU-1"
    forever = FakeProvider(script=["async"], async_rounds=10_000)
    out = G.generate_music(spec2, provider=forever, poll=0.0, max_wait=0.0, sleep=lambda _s: None, force=True)
    assert out["generated"] == [] and "还没出配乐" in out["failed"][0]["error"]


def test_suffix_follows_the_result_url(tmp_path):
    assert G.suffix_for("https://x/y.wav?sig=1") == ".wav"
    assert G.suffix_for("https://x/y.mp3") == ".mp3"
    assert G.suffix_for("https://x/y") == ".mp3"
    spec = _spec(tmp_path, count=1)
    G.generate_music(spec, provider=FakeProvider(urls="https://cdn.example.com/a.wav?x=1"))
    assert spec["current"] == "MU-1-1.wav" and (S.raw_dir(spec) / "MU-1-1.wav").is_file()


def test_bad_count_is_refused(tmp_path):
    ok = _spec(tmp_path, count=1)
    for bad in (0, 9):
        with pytest.raises(G.MusicGenerationError):
            G.generate_music(ok, count=bad, provider=FakeProvider())
    assert ok["attempts"] == []


def test_source_limits_are_checked_before_anything_is_submitted(tmp_path):
    """来源在能力记录里登记的上限（条数 / 收不收参考图 / 最多几张）在提交之前就查，一条请求都不发。"""

    assets = tmp_path / "content" / "assets" / "MU-1"
    assets.mkdir(parents=True)
    for name in ("a.png", "b.png"):
        (assets / name).write_bytes(name.encode())
    spec = _spec(tmp_path, count=2, reference_images=["content/assets/MU-1/a.png", "content/assets/MU-1/b.png"])
    provider = FakeProvider()
    with pytest.raises(G.MusicGenerationError, match="一次只能出 1 到 1 条"):
        G.generate_music(spec, provider=provider, limits=S.limits_from_caps({"counts": {"min": 1, "max": 1}}))
    with pytest.raises(G.MusicGenerationError, match="不收参考图"):
        G.generate_music(spec, count=1, provider=provider, limits=S.limits_from_caps({"reference_images": False}))
    with pytest.raises(G.MusicGenerationError, match="参考图最多 1 张，现在有 2 张"):
        G.generate_music(spec, count=1, provider=provider, limits=S.limits_from_caps({"max_reference_images": 1}))
    assert provider.requests == [] and spec["attempts"] == []
    # 没给 limits：按「模型设置」里的配乐来源（夹具把设置指到空文件，没选来源 = 核心上限）
    assert S.source_caps() == {} and S.source_limits()["count"] == [S.COUNT_MIN, S.COUNT_MAX]
    assert len(G.generate_music(spec, count=2, provider=provider)["generated"]) == 2


def test_count_override_beats_the_project_file(tmp_path):
    spec = _spec(tmp_path, count=3)
    provider = FakeProvider()
    assert len(G.generate_music(spec, count=1, provider=provider)["generated"]) == 1
    assert len(provider.requests) == 1


def test_request_data_carries_reference_digests(tmp_path):
    assets = tmp_path / "content" / "assets" / "MU-1"
    assets.mkdir(parents=True)
    (assets / "a.png").write_bytes(b"one")
    spec = _spec(tmp_path, count=1, reference_images=["content/assets/MU-1/a.png"])
    before = G.request_hash(G.request_data(spec))
    (assets / "a.png").write_bytes(b"two")
    assert G.request_hash(G.request_data(spec)) != before
    provider = FakeProvider()
    G.generate_music(spec, provider=provider)
    assert provider.requests[0].reference_asset_ids and provider.requests[0].reference_asset_ids[0].startswith("local:")
