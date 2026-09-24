"""插画生成（契约 §6 pages/generate）：请求哈希跳过、从不覆盖（P01-1 / P01-2）、指针、异步轮询、超时、失败隔离。"""

from __future__ import annotations

import shutil
from pathlib import Path

from genkit.providers import TaskSnapshot, TaskState, TaskSubmission, TaskType, path_to_file_url
from picturebook import pages as PG
from tests.picturebook_fixtures import png_bytes, small_book


class FakeProvider:
    """同步型：submit 直接 FINISHED 带 file: 任务 id（跟网关 base64 出图一样）。"""

    def __init__(self, cache: Path, *, fail_pages: tuple[str, ...] = (), async_rounds: int = 0):
        self.cache = cache
        self.fail_pages = fail_pages
        self.async_rounds = async_rounds
        self.requests = []
        self.polls = 0

    def submit_image(self, request):
        self.requests.append(request)
        page = request.name.rsplit("-", 1)[-1]
        if page in self.fail_pages:
            return TaskSubmission(task_id=None, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message="余额不足")
        path = self.cache / f"{request.name}-{len(self.requests)}.png"
        path.write_bytes(png_bytes((len(self.requests) * 20 % 255, 80, 120)))
        if self.async_rounds:
            return TaskSubmission(task_id=f"remote:{path}", task_type=TaskType.IMAGE, status=TaskState.PROCESSING)
        return TaskSubmission(task_id=f"file:{path}", task_type=TaskType.IMAGE, status=TaskState.FINISHED)

    def get_task(self, task_id, task_type):
        self.polls += 1
        path = Path(task_id.split(":", 1)[1])
        if task_id.startswith("remote:") and self.polls < self.async_rounds:
            return TaskSnapshot(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.PROCESSING)
        return TaskSnapshot(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.FINISHED, output_urls=(path_to_file_url(path),))

    def download(self, url, destination, **kw):
        src = Path(url.replace("file:///", "").replace("file://", ""))
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, destination)
        return Path(destination)


def test_generate_writes_attempts_and_skips_unchanged(tmp_path):
    spec = small_book(tmp_path)
    provider = FakeProvider(tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    result = PG.generate_pages(spec, provider=provider)
    assert result["generated"] == ["P01", "P02", "P03"] and result["skipped"] == [] and result["failed"] == []
    pages_dir = tmp_path / "output" / "picturebook" / "PB-S-1" / "05_pages"
    assert sorted(p.name for p in pages_dir.glob("*.png")) == ["P01-1.png", "P02-1.png", "P03-1.png"]
    assert PG.page_image_path(spec, "P01").name == "P01-1.png"
    manifest = PG.load_manifest(spec)
    assert manifest["pages"]["P01"]["current"] == "P01-1.png" and len(manifest["pages"]["P01"]["attempts"]) == 1
    assert provider.requests[0].aspect_ratio == "3:4" and provider.requests[0].prompt.startswith(spec["style_anchor"])

    # 请求没变 → 全部跳过；force → 新文件 P01-2.png，旧的不动
    again = PG.generate_pages(spec, provider=provider)
    assert again["skipped"] == ["P01", "P02", "P03"] and len(provider.requests) == 3
    forced = PG.generate_pages(spec, only=["P01"], force=True, provider=provider)
    assert forced["generated"] == ["P01"]
    assert (pages_dir / "P01-1.png").is_file() and (pages_dir / "P01-2.png").is_file()
    assert PG.page_image_path(spec, "P01").name == "P01-2.png"

    # 改了画面 → 只重出那一页
    spec["pages"][1]["scene"] = "风吹起晾衣绳上的袜子，小狐狸伸手去够，中景"
    changed = PG.generate_pages(spec, provider=provider)
    assert changed["generated"] == ["P02"] and changed["skipped"] == ["P01", "P03"]
    status = PG.page_status(spec)
    assert status["P02"]["done"] and status["P02"]["attempts"] == 2 and status["P02"]["stale"] is False


def test_async_provider_polls_until_finished(tmp_path):
    spec = small_book(tmp_path)
    provider = FakeProvider(tmp_path / "cache", async_rounds=3)
    (tmp_path / "cache").mkdir()
    slept: list[float] = []
    result = PG.generate_pages(spec, only=["P01"], provider=provider, poll=0.01, sleep=slept.append)
    assert result["generated"] == ["P01"] and provider.polls >= 3 and len(slept) >= 2


def test_async_timeout_gives_up_this_page(tmp_path, monkeypatch):
    spec = small_book(tmp_path)
    provider = FakeProvider(tmp_path / "cache", async_rounds=10_000)
    (tmp_path / "cache").mkdir()
    clock = iter([0.0, 0.0, 0.0, 10_000.0, 10_000.0, 10_000.0])
    monkeypatch.setattr(PG.time, "monotonic", lambda: next(clock, 10_000.0))
    result = PG.generate_pages(spec, only=["P01"], provider=provider, max_wait=1200, poll=0.0, sleep=lambda _: None)
    assert result["generated"] == [] and result["failed"][0]["page"] == "P01" and "20 分钟" in result["failed"][0]["error"]
    assert PG.page_image_path(spec, "P01") is None and PG.page_status(spec)["P01"]["error"]


def test_failed_page_does_not_block_others(tmp_path):
    spec = small_book(tmp_path)
    provider = FakeProvider(tmp_path / "cache", fail_pages=("P02",))
    (tmp_path / "cache").mkdir()
    result = PG.generate_pages(spec, provider=provider)
    assert result["generated"] == ["P01", "P03"] and result["failed"] == [{"page": "P02", "error": "余额不足"}]
    assert PG.load_manifest(spec)["pages"]["P02"]["error"] == "余额不足" and "current" not in PG.load_manifest(spec)["pages"]["P02"]


def test_manual_png_fallback_and_unknown_page(tmp_path):
    spec = small_book(tmp_path)
    pages_dir = tmp_path / "output" / "picturebook" / "PB-S-1" / "05_pages"
    pages_dir.mkdir(parents=True)
    (pages_dir / "P02.png").write_bytes(png_bytes())
    assert PG.page_image_path(spec, "P02").name == "P02.png" and PG.page_image_path(spec, "P01") is None
    try:
        PG.generate_pages(spec, only=["P07"], provider=FakeProvider(tmp_path))
    except PG.PageGenerationError as exc:
        assert "没有这些页：P07" in str(exc)
    else:
        raise AssertionError("expected PageGenerationError")
