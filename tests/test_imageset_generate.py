"""出图任务（DESIGN §40，契约 §2 ``generate.py``）：逐张出、请求哈希跳过、force 重出、单张失败不拖累整批、
attempts 追加 + ``current`` 指针、从不覆盖。

用假来源（同步 / 异步两种），不联网、不花钱。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from genkit.providers import ProviderError, TaskSnapshot, TaskState, TaskSubmission, TaskType, path_to_file_url
from imageset import generate as G
from imageset import spec as S
from imageset import templates as T


def png_bytes(color=(120, 60, 80)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


class FakeProvider:
    """同步型：submit 直接 FINISHED 带 ``file:`` 任务 id。``fail_items`` 里的图提交即失败。"""

    def __init__(self, cache: Path, *, fail_items: tuple[str, ...] = (), async_rounds: int = 0, raise_items: tuple[str, ...] = ()):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.fail_items = fail_items
        self.raise_items = raise_items
        self.async_rounds = async_rounds
        self.requests = []
        self.polls = 0

    def _item(self, name: str) -> str:
        return name.rsplit("-", 1)[-1]

    def submit_image(self, request):
        self.requests.append(request)
        item = self._item(request.name)
        if item in self.raise_items:
            raise ProviderError("网关连不上")
        if item in self.fail_items:
            return TaskSubmission(task_id=None, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message="余额不足")
        path = self.cache / f"{request.name}-{len(self.requests)}.png"
        path.write_bytes(png_bytes((len(self.requests) * 30 % 255, 90, 130)))
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


def build(tmp_path, *, module: str = "sticker", template: str = "emoji_pack", options=None, **extra) -> dict:
    subject = {"kind": "character", "name": "小满", "block": "齐肩黑发"} if module == "sticker" else \
        {"kind": "photo", "name": "宝宝", "block": "", "fidelity": 88}
    data = {"title": "出图测试", "template": template, "subject": subject,
            "options": options or ({"expressions": ["开心", "哭", "生气"]} if module == "sticker" else {"scenes": ["小马甲", "全家福"]})}
    data.update(extra)
    spec = S.normalize(data, module=module, project_root=tmp_path, spec_id=f"{S.ID_PREFIX[module]}-G-1")
    spec["items"] = T.expand_items(spec)
    S.save(spec)
    return spec


def raw_files(spec) -> list[str]:
    return sorted(p.name for p in S.raw_dir(spec).glob("*.png"))


# ---------------------------------------------------------------------------- 逐张出图


@pytest.mark.parametrize("module,template", [("sticker", "emoji_pack"), ("portrait", "baby_100")])
def test_generates_every_todo_item(module, template, tmp_path):
    spec = build(tmp_path, module=module, template=template)
    provider = FakeProvider(tmp_path / "cache")
    ids = S.item_ids(spec)
    result = G.generate_set(spec, provider=provider)
    assert result == {"generated": ids, "skipped": [], "failed": []}
    assert raw_files(spec) == [f"{i}-1.png" for i in ids]
    for item in spec["items"]:
        assert item["current"] == f"{item['id']}-1.png" and item["status"] == "done" and item["error"] is None
        assert len(item["attempts"]) == 1
        attempt = item["attempts"][0]
        assert attempt["file"] == item["current"] and attempt["request_hash"] == item["request_hash"]
        assert attempt["model"] == "@image" and attempt["error"] is None and attempt["task_id"]
        assert S.current_path(spec, item) is not None
    # 请求形状：提示词 / 负面 / 画幅都来自 items
    assert len(provider.requests) == len(ids)
    first = provider.requests[0]
    assert first.name == f"{spec['id']}-I01" and first.prompt == spec["items"][0]["prompt"]
    assert first.negative_prompt == spec["items"][0]["negative"] and first.aspect_ratio == spec["items"][0]["aspect_ratio"]
    assert first.image_size == "2K" and first.reference_asset_ids == ()
    # 逐张写盘：任务中途挂了也不丢
    assert S.load(module, spec["id"], project_root=tmp_path)["items"][0]["current"] == "I01-1.png"


def test_only_limits_the_batch_and_unknown_ids_are_refused(tmp_path):
    spec = build(tmp_path)
    provider = FakeProvider(tmp_path / "cache")
    assert G.generate_set(spec, only=["I02"], provider=provider)["generated"] == ["I02"]
    assert raw_files(spec) == ["I02-1.png"] and len(provider.requests) == 1
    with pytest.raises(G.GenerationError, match="没有这些图：I09"):
        G.generate_set(spec, only=["I09"], provider=provider)
    empty = S.normalize({"title": "空", "template": "emoji_pack", "subject": {"name": "小满"}, "options": {"expressions": ["开心"]}},
                        module="sticker", project_root=tmp_path, spec_id="ST-G-2")
    with pytest.raises(G.GenerationError, match="还没有展开清单"):
        G.generate_set(empty, provider=provider)


def test_empty_prompt_is_reported_not_submitted(tmp_path):
    spec = build(tmp_path)
    spec["items"][1]["prompt"] = ""
    provider = FakeProvider(tmp_path / "cache")
    result = G.generate_set(spec, provider=provider)
    assert result["generated"] == ["I01", "I03"] and result["failed"] == [{"item": "I02", "error": "提示词为空"}]
    assert len(provider.requests) == 2


# ---------------------------------------------------------------------------- 跳过 / force / 从不覆盖


def test_unchanged_request_with_file_is_skipped_and_force_regenerates(tmp_path):
    spec = build(tmp_path)
    provider = FakeProvider(tmp_path / "cache")
    G.generate_set(spec, provider=provider)
    assert len(provider.requests) == 3

    again = G.generate_set(spec, provider=provider)
    assert again["skipped"] == ["I01", "I02", "I03"] and again["generated"] == []
    assert len(provider.requests) == 3                                      # 一次请求都没多发

    forced = G.generate_set(spec, only=["I01"], force=True, provider=provider)
    assert forced["generated"] == ["I01"] and len(provider.requests) == 4
    assert (S.raw_dir(spec) / "I01-1.png").is_file() and (S.raw_dir(spec) / "I01-2.png").is_file()
    assert spec["items"][0]["current"] == "I01-2.png" and len(spec["items"][0]["attempts"]) == 2
    assert [a["file"] for a in spec["items"][0]["attempts"]] == ["I01-1.png", "I01-2.png"]

    # 提示词改了 → 哈希变 → 只重出那一张
    spec["items"][1]["prompt"] += "，手里多一杯奶茶"
    changed = G.generate_set(spec, provider=provider)
    assert changed["generated"] == ["I02"] and sorted(changed["skipped"]) == ["I01", "I03"]
    assert spec["items"][1]["current"] == "I02-2.png"


def test_missing_file_is_regenerated_even_when_hash_matches(tmp_path):
    spec = build(tmp_path, options={"expressions": ["开心"]})
    provider = FakeProvider(tmp_path / "cache")
    G.generate_set(spec, provider=provider)
    (S.raw_dir(spec) / "I01-1.png").unlink()
    result = G.generate_set(spec, provider=provider)
    assert result["generated"] == ["I01"] and spec["items"][0]["current"] == "I01-2.png"


def test_never_overwrites_a_file_that_already_sits_there(tmp_path):
    """attempts 的编号被占了（人手放了一个同名文件）就顺延，不覆盖。"""

    spec = build(tmp_path, options={"expressions": ["开心"]})
    S.raw_dir(spec).mkdir(parents=True, exist_ok=True)
    (S.raw_dir(spec) / "I01-1.png").write_bytes(b"placed by hand")
    provider = FakeProvider(tmp_path / "cache")
    G.generate_set(spec, provider=provider)
    assert (S.raw_dir(spec) / "I01-1.png").read_bytes() == b"placed by hand"
    assert spec["items"][0]["current"] == "I01-2.png" and (S.raw_dir(spec) / "I01-2.png").is_file()


def test_request_hash_covers_prompt_model_aspect_and_reference_content(tmp_path):
    spec = build(tmp_path, options={"expressions": ["开心"]})
    item = spec["items"][0]
    base = G.request_hash(G.request_data(spec, item))
    assert G.request_hash(G.request_data(spec, item)) == base
    spec["image_params"]["model"] = "doubao-seedream-4"
    assert G.request_hash(G.request_data(spec, item)) != base
    spec["image_params"]["model"] = "@image"
    item["aspect_ratio"] = "3:4"
    assert G.request_hash(G.request_data(spec, item)) != base
    item["aspect_ratio"] = "1:1"
    assert G.request_hash(G.request_data(spec, item)) == base
    # 参考图换了内容（路径不变）也算变
    assets = tmp_path / "content" / "assets" / spec["id"]
    assets.mkdir(parents=True)
    (assets / "ref.png").write_bytes(b"one")
    spec["subject"]["assets"] = [f"content/assets/{spec['id']}/ref.png"]
    with_ref = G.request_hash(G.request_data(spec, item))
    assert with_ref != base
    (assets / "ref.png").write_bytes(b"two")
    assert G.request_hash(G.request_data(spec, item)) != with_ref


def test_reference_assets_reach_the_request(tmp_path):
    spec = build(tmp_path, options={"expressions": ["开心"]})
    assets = tmp_path / "content" / "assets" / spec["id"]
    assets.mkdir(parents=True)
    (assets / "ref.png").write_bytes(png_bytes())
    spec["subject"]["assets"] = [f"content/assets/{spec['id']}/ref.png", "content/assets/nope.png"]
    provider = FakeProvider(tmp_path / "cache")
    G.generate_set(spec, provider=provider)
    ids = provider.requests[0].reference_asset_ids
    assert len(ids) == 1 and str(assets / "ref.png") in ids[0]      # 不存在的那张不进请求


# ---------------------------------------------------------------------------- 失败隔离


@pytest.mark.parametrize("provider_kwargs,reason", [({"fail_items": ("I02",)}, "余额不足"), ({"raise_items": ("I02",)}, "网关连不上")])
def test_one_failing_item_does_not_stop_the_batch(provider_kwargs, reason, tmp_path):
    spec = build(tmp_path)
    provider = FakeProvider(tmp_path / "cache", **provider_kwargs)
    result = G.generate_set(spec, provider=provider)
    assert result["generated"] == ["I01", "I03"] and result["skipped"] == []
    assert [f["item"] for f in result["failed"]] == ["I02"] and reason in result["failed"][0]["error"]
    bad = S.item_by_id(spec, "I02")
    assert bad["status"] == "failed" and reason in bad["error"] and bad["current"] is None
    assert len(bad["attempts"]) == 1 and reason in bad["attempts"][0]["error"]
    assert raw_files(spec) == ["I01-1.png", "I03-1.png"]
    # 失败也写盘了：重新读回来还在
    reloaded = S.load("sticker", spec["id"], project_root=tmp_path)
    assert S.item_by_id(reloaded, "I02")["status"] == "failed"
    assert G.item_status(reloaded, S.item_by_id(reloaded, "I02"))["status"] == "failed"
    # 修好之后重出：老的失败记录留着，新 attempt 追加
    provider.fail_items = provider.raise_items = ()
    fixed = G.generate_set(spec, only=["I02"], provider=provider)
    assert fixed["generated"] == ["I02"]
    assert len(S.item_by_id(spec, "I02")["attempts"]) == 2 and S.item_by_id(spec, "I02")["error"] is None
    assert S.item_by_id(spec, "I02")["current"] == "I02-2.png"


def test_empty_download_is_a_failure(tmp_path):
    spec = build(tmp_path, options={"expressions": ["开心"]})
    provider = FakeProvider(tmp_path / "cache")

    def empty_download(url, destination, **kw):
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(b"")
        return Path(destination)

    provider.download = empty_download
    result = G.generate_set(spec, provider=provider)
    assert result["failed"][0]["item"] == "I01" and "空文件" in result["failed"][0]["error"]


# ---------------------------------------------------------------------------- 异步来源


def test_async_provider_is_polled_until_finished(tmp_path):
    spec = build(tmp_path, options={"expressions": ["开心"]})
    provider = FakeProvider(tmp_path / "cache", async_rounds=3)
    slept: list[float] = []
    result = G.generate_set(spec, provider=provider, poll=0.01, sleep=slept.append)
    assert result["generated"] == ["I01"] and provider.polls >= 3 and slept
    assert (S.raw_dir(spec) / "I01-1.png").is_file()


def test_async_timeout_gives_up_on_that_item_only(tmp_path):
    spec = build(tmp_path)
    provider = FakeProvider(tmp_path / "cache", async_rounds=10_000)
    result = G.generate_set(spec, provider=provider, max_wait=0, poll=0, sleep=lambda s: None)
    assert result["generated"] == [] and [f["item"] for f in result["failed"]] == ["I01", "I02", "I03"]
    assert "分钟还没出图" in result["failed"][0]["error"]
    assert G.MAX_WAIT_SECONDS == 20 * 60 and G.POLL_SECONDS == 5.0


def test_finished_without_urls_is_a_failure(tmp_path):
    spec = build(tmp_path, options={"expressions": ["开心"]})
    provider = FakeProvider(tmp_path / "cache", async_rounds=1)
    provider.get_task = lambda task_id, task_type: TaskSnapshot(task_id=task_id, task_type=TaskType.IMAGE,
                                                                status=TaskState.FINISHED, output_urls=())
    result = G.generate_set(spec, provider=provider, poll=0, sleep=lambda s: None)
    assert "没有给结果地址" in result["failed"][0]["error"]
    provider2 = FakeProvider(tmp_path / "cache2", async_rounds=1)
    provider2.get_task = lambda task_id, task_type: TaskSnapshot(task_id=task_id, task_type=TaskType.IMAGE,
                                                                 status=TaskState.FAILED, error_message="内容被拦截")
    spec2 = build(tmp_path / "b", options={"expressions": ["开心"]})
    result2 = G.generate_set(spec2, provider=provider2, poll=0, sleep=lambda s: None)
    assert "内容被拦截" in result2["failed"][0]["error"]


# ---------------------------------------------------------------------------- 状态


def test_item_status_reports_done_stale_and_attempts(tmp_path):
    spec = build(tmp_path, options={"expressions": ["开心"]})
    item = spec["items"][0]
    assert G.item_status(spec, item) == {"done": False, "file": None, "attempts": 0, "stale": False, "error": None, "status": "todo"}
    G.generate_set(spec, provider=FakeProvider(tmp_path / "cache"))
    status = G.item_status(spec, spec["items"][0])
    assert status["done"] and status["file"] == "I01-1.png" and status["attempts"] == 1 and status["stale"] is False
    spec["items"][0]["prompt"] += "，加一顶帽子"
    assert G.item_status(spec, spec["items"][0])["stale"] is True
