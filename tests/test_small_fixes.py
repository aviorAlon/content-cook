"""一批已知问题的修复：英文景别检查、换来源后的旧任务提示、账本版本与迁移、设置文件权限、
日志脱敏、OpenRouter 视频分块落盘、起草去掉 claude 命令行兜底。"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

import pytest

from genkit import logmask, settings as S
from genkit.adapters.base import Adapter
from genkit.providers import ProviderCommandError, TaskType
from genkit.router import RoutedProvider
from genkit.store import MIGRATIONS, SCHEMA_VERSION, RunStore, StoreError
from storyvideo import continuity, scriptgen


def test_framing_check_handles_english_shots():
    zh = {"shots": [{"id": "A", "shot": "中景，正面"}, {"id": "B", "shot": "她坐着"}], "cast": {}}
    assert [w for w in continuity.check_shot_design(zh) if "景别" in w] == ["shot B: shot 里读不出景别（特写/近景/中景/全景…）——相邻镜同景别硬切是顿挫的第一来源"]
    en = {"shots": [{"id": "A", "shot": "Medium shot, frontal"}, {"id": "B", "shot": "She sits at the desk"}, {"id": "C", "shot": "Close-up on her face"}], "cast": {}}
    hits = [w for w in continuity.check_shot_design(en) if "景别" in w]
    assert len(hits) == 1 and "shot B" in hits[0]


def test_router_explains_adapter_mismatch(tmp_path):
    st = S.Settings(cache_dir=str(tmp_path / "cache"))
    st.role("video").adapter = "minimax_video"; st.role("video").api_key = "k"; st.role("video").model = "MiniMax-Hailuo-02"
    provider = RoutedProvider(st, adapters={"video": type("Fake", (), {"name": "minimax_video"})()})
    with pytest.raises(ProviderCommandError, match="ark_video 提交的"):
        provider.get_task("ark:abc", TaskType.VIDEO)


def test_routed_provider_reports_submission_source(tmp_path):
    """账本要记的来源：设置里该角色的适配器 id + 把 @image / @video 别名解析成设置里的模型名；写了具体模型名就是那个名；
    角色没配就记空适配器与空模型，不抛。"""

    from genkit.providers import ImageRequest, VideoRequest

    st = S.Settings(cache_dir=str(tmp_path / "cache"))
    st.role("image").adapter = "ark_images"; st.role("image").api_key = "k"; st.role("image").model = "doubao-seedream-4-0-250828"
    provider = RoutedProvider(st)
    assert provider.submission_source(ImageRequest(name="n", prompt="p", model="@image")) == ("ark_images", "doubao-seedream-4-0-250828")
    assert provider.submission_source(ImageRequest(name="n", prompt="p", model="gpt-image-1")) == ("ark_images", "gpt-image-1")
    assert provider.submission_source(VideoRequest(name="n", prompt="p", duration=4, model="@video")) == ("", "")
    st.role("video").adapter = "minimax_video"   # 缺 api_key：装不起来，只记设置里的适配器
    assert provider.submission_source(VideoRequest(name="n", prompt="p", duration=4, model="@video")) == ("minimax_video", "")


def test_store_schema_version_and_migration_framework(tmp_path):
    path = tmp_path / "s.sqlite3"
    with RunStore(path) as store:
        assert RunStore.schema_version_of(path) == SCHEMA_VERSION
    # 来自未来的账本：拒绝，并说清楚
    conn = sqlite3.connect(str(path)); conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}"); conn.commit(); conn.close()
    with pytest.raises(StoreError, match="新版程序写的账本"):
        RunStore(path)
    with pytest.raises(StoreError):
        RunStore(path, readonly=True)
    # 缺迁移脚本的旧版本：明确报错；有脚本就逐步升
    conn = sqlite3.connect(str(path)); conn.execute("PRAGMA user_version = 0"); conn.commit(); conn.close()
    with RunStore(path):
        pass
    assert RunStore.schema_version_of(path) == SCHEMA_VERSION
    assert isinstance(MIGRATIONS, dict)


_V1_SCHEMA = """
CREATE TABLE runs (run_id TEXT PRIMARY KEY, stage_id TEXT NOT NULL, topic_id TEXT NOT NULL, input_hash TEXT NOT NULL,
    plan_hash TEXT NOT NULL, run_dir TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE jobs (run_id TEXT NOT NULL, step_id TEXT NOT NULL, kind TEXT NOT NULL, request_hash TEXT NOT NULL,
    output_path TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    task_id TEXT, remote_state TEXT, result_url TEXT, asset_id TEXT, artifact_path TEXT, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, submitted_at TEXT, finished_at TEXT,
    PRIMARY KEY (run_id, step_id), FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE);
CREATE INDEX jobs_by_run_status ON jobs(run_id, status);
CREATE UNIQUE INDEX jobs_by_remote_task ON jobs(kind, task_id) WHERE task_id IS NOT NULL;
"""


def _old_ledger(path: Path, run_dir: Path, *, user_version: int, frame_status: str = "succeeded") -> None:
    """按 schema 1 的表结构手搓一本老账本：一个 run（id = ``run_dir.name``）、一个提交过的首帧 job（没有 provider / model 两列，
    状态按 ``frame_status``，failed 时带一条错误）、一个没提交的视频 job。"""

    run_id = run_dir.name
    error = "provider failed: 厂商返回 500" if frame_status == "failed" else None
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_V1_SCHEMA)
    conn.execute("INSERT INTO runs VALUES (?, 'storyvideo', ?, 'in', 'plan', ?, 'prepared', 't', 't')", (run_id, run_id, str(run_dir)))
    conn.execute("INSERT INTO jobs (run_id, step_id, kind, request_hash, output_path, status, attempt, task_id, error, created_at, updated_at)"
                 " VALUES (?, 'frame-S1', 'image', 'h', ?, ?, 1, 'task-1', ?, 't', 't')",
                 (run_id, str(run_dir / "01_frames" / "S1.png"), frame_status, error))
    conn.execute("INSERT INTO jobs (run_id, step_id, kind, request_hash, output_path, status, created_at, updated_at)"
                 " VALUES (?, 'video-S1', 'video', 'h2', ?, 'prepared', 't', 't')", (run_id, str(run_dir / "02_video" / "S1.mp4")))
    conn.execute(f"PRAGMA user_version = {user_version}")
    conn.commit(); conn.close()


def _job_columns(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {str(row[1]) for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    finally:
        conn.close()


@pytest.mark.parametrize("user_version", [1, 0])
def test_store_migrates_old_ledger_and_records_sources(tmp_path, user_version):
    """老账本（schema 1，或标版本号之前的 0）可写打开自动补 provider / model 两列：老行为 NULL、新提交落库、
    sources_for 按 (来源, 模型) 合并计数。只读打开不迁移，照旧明说。"""

    path = tmp_path / "s.sqlite3"
    run_dir = tmp_path / "EP1"
    run_dir.mkdir()
    _old_ledger(path, run_dir, user_version=user_version)
    if user_version:
        with pytest.raises(StoreError, match="只读打开不会迁移"):
            RunStore(path, readonly=True)
    with RunStore(path) as store:
        assert RunStore.schema_version_of(path) == SCHEMA_VERSION == 2
        old = store.get_job("EP1", "frame-S1")
        assert old["provider"] is None and old["model"] is None and old["attempt"] == 1
        # 提交过但没记来源的老行：sources_for 带出来是空串，count 照数
        assert store.sources_for("EP1") == {"image": [{"provider": "", "model": "", "count": 1}]}
        # 新提交记下真实来源；重提覆盖成最近一次
        store.mark_submitting("EP1", "video-S1", provider="openai_video", model="sora-2")
        job = store.mark_submitted("EP1", "video-S1", "task-2", "finished")
        assert job["provider"] == "openai_video" and job["model"] == "sora-2"
        store.mark_succeeded("EP1", "video-S1")
        store.reset_job("EP1", "video-S1", reason="retake")
        store.mark_submitting("EP1", "video-S1", provider="minimax_video", model="MiniMax-H3")
        store.mark_submitted("EP1", "video-S1", "task-3", "pending")
        assert store.sources_for("EP1") == {
            "image": [{"provider": "", "model": "", "count": 1}],
            "video": [{"provider": "minimax_video", "model": "MiniMax-H3", "count": 1}],
        }
        # 没提交过的 job 不数；不存在的 run 是空字典；snapshot 的 job 行带两列
        store.ensure_job("EP1", "assemble", "assembly", "h3", str(run_dir / "EP1_final.mp4"))
        assert "assembly" not in store.sources_for("EP1") and store.sources_for("nope") == {}
        rows = {j["step_id"]: j for j in store.snapshot("EP1")["jobs"]}
        assert rows["video-S1"]["provider"] == "minimax_video" and rows["frame-S1"]["provider"] is None
    with RunStore(path, readonly=True) as ro:
        assert ro.sources_for("EP1")["video"][0]["model"] == "MiniMax-H3"


def test_store_migration_is_one_transaction_and_tolerates_a_concurrent_opener(tmp_path, monkeypatch):
    """迁移在一个写事务里：步骤中途抛错整体回滚（列没加、版本号没动，账本还能按老版本再开）；只加了一列的半迁移账本
    幂等补齐；两个进程同时可写打开同一本老账本（网页 + CLI，或两个 runner），后到的等前者提交后重读版本号、看到已经
    升过就跳过，不会撞上「列已存在」的裸 OperationalError。"""

    import threading
    import time

    path = tmp_path / "s.sqlite3"
    run_dir = tmp_path / "EP1"
    run_dir.mkdir()
    _old_ledger(path, run_dir, user_version=1)
    real_step = MIGRATIONS[1]

    def broken(conn):
        conn.execute("ALTER TABLE jobs ADD COLUMN provider TEXT")
        raise RuntimeError("中途断电")

    monkeypatch.setitem(MIGRATIONS, 1, broken)
    with pytest.raises(RuntimeError, match="中途断电"):
        RunStore(path)
    assert RunStore.schema_version_of(path) == 1 and "provider" not in _job_columns(path)
    # 半迁移（只有 provider 列、版本号还是 1）：真正的步骤幂等补齐
    conn = sqlite3.connect(str(path)); conn.execute("ALTER TABLE jobs ADD COLUMN provider TEXT"); conn.commit(); conn.close()
    calls: list[int] = []

    def recording(conn):
        calls.append(1)
        real_step(conn)

    monkeypatch.setitem(MIGRATIONS, 1, recording)
    with RunStore(path) as store:
        assert store.get_job("EP1", "frame-S1")["model"] is None
    assert RunStore.schema_version_of(path) == SCHEMA_VERSION and {"provider", "model"} <= _job_columns(path) and calls == [1]
    # 并发：前者是一条裸连接，拿着写锁在事务里做完整个迁移；后者 RunStore 可写打开，BEGIN IMMEDIATE 等前者提交
    calls.clear()
    other = tmp_path / "t.sqlite3"
    _old_ledger(other, run_dir, user_version=1)
    first = sqlite3.connect(str(other), isolation_level=None)
    first.execute("PRAGMA journal_mode = WAL")
    first.execute("BEGIN IMMEDIATE")
    first.execute("ALTER TABLE jobs ADD COLUMN provider TEXT")
    first.execute("ALTER TABLE jobs ADD COLUMN model TEXT")
    first.execute("PRAGMA user_version = 2")
    outcome: dict[str, object] = {}

    def late_opener():
        try:
            with RunStore(other) as late:
                outcome["sources"] = late.sources_for("EP1")
        except BaseException as exc:  # noqa: BLE001 - 断言用
            outcome["error"] = exc

    thread = threading.Thread(target=late_opener)
    thread.start()
    time.sleep(0.3)
    first.execute("COMMIT"); first.close()
    thread.join(timeout=30)
    assert not thread.is_alive() and "error" not in outcome, outcome
    assert outcome["sources"] == {"image": [{"provider": "", "model": "", "count": 1}]}
    assert RunStore.schema_version_of(other) == SCHEMA_VERSION and calls == []   # 后到者没有再跑一遍迁移步骤


def test_settings_file_is_restricted_to_owner(tmp_path):
    target = tmp_path / "settings.json"
    saved = S.save_settings(S.Settings(), target)
    assert saved.is_file()
    if os.name != "nt":
        assert oct(target.stat().st_mode & 0o777) == "0o600"
    # 函数本身在任何平台都不抛
    assert S.restrict_to_owner(target) in (True, False)


def test_log_masking_hides_keys_tokens_and_data_urls(caplog):
    assert logmask.mask("key sk-or-v1-abcdef0123456789abcdef") == "key sk-or-v1-…[masked]"
    assert logmask.mask("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc") == "Authorization: Bearer [masked]"
    assert logmask.mask("api_key=abcdefghijk&x=1") == "api_key=[masked]&x=1"
    masked = logmask.mask("img data:image/png;base64," + "A" * 200 + " tail")
    assert masked == "img data:image/png;base64,…[base64 omitted] tail"
    assert logmask.mask("nothing secret here") == "nothing secret here"
    logger = logging.getLogger("masktest")
    handler = logging.StreamHandler()
    handler.addFilter(logmask.MaskingFilter())
    logger.addHandler(handler)
    with caplog.at_level(logging.INFO, logger="masktest"):
        logger.info("posting with %s", "sk-abcdefghijklmnop")
    assert "sk-abcdefghijklmnop" not in caplog.text and "[masked]" in caplog.text
    logger.removeHandler(handler)


def test_store_stream_writes_chunks_and_dedups(tmp_path):
    adapter = Adapter({}, cache_dir=tmp_path)
    token = adapter.store_stream([b"abc", b"", b"def"], suffix=".bin", hint="job:1")
    path = Path(token[len("file:"):])
    assert path.read_bytes() == b"abcdef" and not list(tmp_path.glob("*.part"))
    again = adapter.store_stream(iter([b"abcdef"]), suffix=".bin", hint="job:1")
    assert again == token
    assert adapter.store_stream([], suffix=".bin", hint="empty") == ""


def test_openrouter_video_uses_chunked_download(tmp_path):
    from genkit.adapters.openrouter_adapters import OpenRouterVideo
    from genkit.providers import TaskState

    class Resp:
        status = "completed"

    class Content:
        def iter_bytes(self, size):
            yield b"mp4-"; yield b"bytes"

    class VG:
        def get_generation(self, job_id): return Resp()
        def get_video_content(self, job_id, index=0): return Content()

    class Client:
        video_generation = VG()

    adapter = OpenRouterVideo({"api_key": "k", "model": "minimax/hailuo-3"}, client=Client(), cache_dir=tmp_path)
    snap = adapter.get_task("openrouter:job1")
    assert snap.status is TaskState.FINISHED
    local = Path(snap.output_urls[0].replace("file:///", "").replace("file://", ""))
    assert local.read_bytes() == b"mp4-bytes"


def test_scriptgen_without_any_backend_fails_clearly(monkeypatch, tmp_path):
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "none.json"))
    for key in ("SCRIPTGEN_BACKEND", "SCRIPTGEN_LLM_BASE_URL", "SCRIPTGEN_LLM_API_KEY", "STORYSTUDIO_TEXT_ADAPTER", "STORYSTUDIO_TEXT_API_KEY", "STORYSTUDIO_TEXT_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(scriptgen, "PROJECT_ROOT", tmp_path)
    assert scriptgen.scriptgen_backend() == "unconfigured"
    with pytest.raises(scriptgen.ScriptGenError, match="模型设置"):
        scriptgen._dispatch("s", "u", json_schema={})
    assert not hasattr(scriptgen, "_call_claude_cli")


def test_sync_file_submission_carries_file_url(tmp_path):
    """同步出图只给 file: 任务 id 时，路由补上 file:// 地址；缓存文件丢了则标失败。
    同步出图（网关返回 base64）以前卡在「finished job has no result URL」。"""
    from genkit import settings as S
    from genkit.providers import ImageRequest, TaskState, TaskSubmission, TaskType
    from genkit.router import RoutedProvider

    cached = tmp_path / "frame_abc.png"
    cached.write_bytes(b"PNG")

    class SyncAdapter:
        name = "openai_images"

        def __init__(self, task_id):
            self.task_id = task_id

        def submit_image(self, request):
            return TaskSubmission(task_id=self.task_id, task_type=TaskType.IMAGE, status=TaskState.FINISHED)

    st = S.Settings()
    request = ImageRequest(name="frame", prompt="p", model="m")
    ok = RoutedProvider(st, adapters={"image": SyncAdapter(f"file:{cached}")}).submit_image(request)
    assert ok.status is TaskState.FINISHED and ok.output_urls and ok.output_urls[0].startswith("file:///")
    missing = RoutedProvider(st, adapters={"image": SyncAdapter(f"file:{tmp_path / 'gone.png'}")}).submit_image(request)
    assert missing.status is TaskState.FAILED and "cached result missing" in missing.error_message


def test_dialogue_budget_window_and_tight():
    """对白预算：可用窗口 = 声明时长 - 开口等待 - 末尾反应；快端装不下 = 装不下，慢端装不下 = 偏紧。"""
    from storyvideo.preview import check_duration_budget, shot_duration_budget

    fits = shot_duration_budget({"id": "A", "dur": 8, "lines": [{"zh": "十二个字的台词刚好说完了"}]})       # 12 字：快 2.67 / 慢 3.43，窗口 6.7
    assert fits["overflow"] is False and fits["tight"] is False and fits["window_seconds"] == 6.7
    tight = shot_duration_budget({"id": "B", "dur": 6, "lines": [{"zh": "一" * 18}]})                    # 18 字：快 4.0 / 慢 5.14，窗口 4.7
    assert tight["overflow"] is False and tight["tight"] is True
    over = shot_duration_budget({"id": "C", "dur": 5, "lines": [{"zh": "一" * 20}]})                     # 20 字：快 4.44 > 窗口 3.7
    assert over["overflow"] is True and over["tight"] is False
    silent = shot_duration_budget({"id": "D", "dur": 4, "lines": []})
    assert silent["overflow"] is False and silent["tight"] is False and silent["window_seconds"] == 4
    spec = {"shots": [{"id": "B", "dur": 6, "lines": [{"zh": "一" * 18}]}, {"id": "C", "dur": 5, "lines": [{"zh": "一" * 20}]}]}
    msgs = check_duration_budget(spec, slack_tolerance=None)
    assert any("shot B" in m and "偏紧" in m for m in msgs) and any("shot C" in m and "装不下" in m for m in msgs)
