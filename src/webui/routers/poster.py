"""海报接口（DESIGN §36）：项目成型时出一张关键画面 + 本地叠标题。

* ``GET  /api/episodes/storyvideo/{id}/poster``            当前海报、历史、建议标题/副标题、可用字体、有首帧的镜、提示词
* ``POST .../poster/generate``  ``{source: model|frame, shot_id?, title?, subtitle?, font?}``
    - ``model``：**真实调一次生图接口**（后台任务 kind ``poster``，跟对比试跑一样不进账本），返回 ``{task_id, cost_note}``
    - ``frame``：拿某一镜的首帧（没有首帧就抽视频第一帧）当底图，不花钱，同步返回成品
* ``POST .../poster/render``    ``{title, subtitle, font}``  用最新底图重排文字，不再调模型
* ``GET  .../poster/file/{name}``  流 PNG；只接受 07_poster 里的纯文件名

产物与规则在 ``storyvideo.poster``。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from genkit.providers import ImageRequest, ProviderError, TaskState, TaskType
from genkit.router import build_provider
from storyvideo import poster as P
from storyvideo import subtitles

from .. import tasks
from ..deps import require_roles
from . import post as post_router

router = APIRouter(tags=["poster"])

SOURCES = ("model", "frame")

# 异步来源（提交后只拿到远端任务 id 的来源）在后台任务里轮询，跟绘本 / 图片集 / 单条视频同一口径：一张图最多等 20 分钟。
# 测试把这两个常量调小（run_model_poster 不把它们写死在默认参数里，就是为了能 monkeypatch）。
MAX_WAIT_SECONDS = 20 * 60
POLL_SECONDS = 5.0


def _file_url(episode_id: str, name: str | None) -> str | None:
    return f"/api/episodes/storyvideo/{episode_id}/poster/file/{name}" if name else None


def _entry_payload(episode_id: str, entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    return {
        "url": _file_url(episode_id, entry.get("poster")),
        "base_url": _file_url(episode_id, entry.get("base")),
        "poster": entry.get("poster"),
        "base": entry.get("base"),
        "created_at": entry.get("created_at"),
        "title": entry.get("title") or "",
        "subtitle": entry.get("subtitle") or "",
        "font": entry.get("font") or "",
        "layout": entry.get("layout") or "auto",
        "layout_used": entry.get("layout_used") or "",
        "kicker": entry.get("kicker") or "",
        "source": entry.get("source") or "",
        "shot_id": entry.get("shot_id"),
        "model": entry.get("model"),
        "cost_note": entry.get("cost_note"),
    }


def _frames_available(spec: dict[str, Any]) -> list[str]:
    root = Path(str(spec["output_root"]))
    return [str(s["id"]) for s in spec.get("shots", ()) if isinstance(s, dict)
            and ((root / "01_frames" / f"{s['id']}.png").is_file() or (root / "02_video" / f"{s['id']}.mp4").is_file())]


def poster_payload(episode_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    prompt = P.poster_prompt(spec)
    model = P.poster_request_data(spec)["model"]
    return {
        "id": episode_id,
        "latest": _entry_payload(episode_id, P.latest(spec)),
        "history": [_entry_payload(episode_id, e) for e in P.history(spec)],
        "suggested_title": P.suggested_title(spec),
        "suggested_subtitle": P.suggested_subtitle(spec),
        "suggested_kicker": P.suggested_kicker(spec),
        "layouts": list(P.LAYOUTS),
        "subtitle_max": P.SUBTITLE_MAX_CHARS,
        "fonts": subtitles.available_fonts(),
        "default_font": subtitles.resolve_font(spec.get("subtitle")) or "",
        "shots_with_frames": _frames_available(spec),
        "model": model,
        "cost_note": P.cost_note(model),
        "prompt": prompt["prompt"],
        "negative": prompt["negative"],
        "ffmpeg_ok": P.ffmpeg_available(),
        "busy_task": tasks.active_task_id("poster", episode_id),
    }


@router.get("/api/episodes/storyvideo/{episode_id}/poster")
def get_poster(episode_id: str) -> dict[str, Any]:
    _path, spec = post_router._load(episode_id)
    return poster_payload(episode_id, spec)


class GenerateRequest(BaseModel):
    source: str = "model"
    shot_id: str | None = None
    title: str | None = None
    subtitle: str | None = None
    font: str | None = None
    layout: str = "auto"
    kicker: str | None = None
    style_card: str | None = None          # 风格卡（DESIGN §41），可空；只影响这一次的关键画面提示词


class RenderRequest(BaseModel):
    title: str
    subtitle: str | None = None
    font: str | None = None
    layout: str = "auto"
    kicker: str | None = None


def _text_fields(spec: dict[str, Any], req: GenerateRequest | RenderRequest) -> tuple[str, str, str]:
    title = " ".join(str(req.title or "").split()) or P.suggested_title(spec)
    if not title:
        raise HTTPException(400, "海报标题不能为空")
    subtitle = P.clean_subtitle(req.subtitle) if req.subtitle is not None else P.suggested_subtitle(spec)
    font = str(req.font or "").strip()
    if font and not Path(font).expanduser().is_file():
        raise HTTPException(400, f"字体文件不存在：{font}")
    return title, subtitle, font


def _layout_fields(spec: dict[str, Any], req: GenerateRequest | RenderRequest) -> tuple[str, str]:
    """(布局, 小标)：布局只认 auto / top / bottom；小标 None = 用建议值，空串 = 不要。"""

    layout = str(req.layout or "auto").strip().lower()
    if layout not in P.LAYOUTS:
        raise HTTPException(400, f"海报布局只认 auto / top / bottom，给的是 {req.layout!r}")
    kicker = " ".join(str(req.kicker).split()) if req.kicker is not None else P.suggested_kicker(spec)
    return layout, kicker


def _style_card_field(req: GenerateRequest) -> str:
    """风格卡（DESIGN §41）：空 = 用项目自己的视觉风格骨架；填了要认识这张卡。"""

    from imagecraft.cards import find_card

    wanted = str(req.style_card or "").strip()
    if not wanted:
        return ""
    card = find_card(wanted)
    if card is None:
        raise HTTPException(404, f"没有这张风格卡：{wanted}")
    return str(card["id"])


def _finish(spec: dict[str, Any], *, base: Path, paths: dict[str, Any], title: str, subtitle: str, font: str,
            source: str, shot_id: str | None = None, model: str | None = None, prompt: str | None = None,
            cost: str | None = None, layout: str = "auto", kicker: str = "") -> dict[str, Any]:
    """底图就绪后：叠字 → 记 poster.json。叠字失败抛 ``PosterError``（中文原因）：
    接口层转 425，后台任务里原样进任务记录。"""

    P.render_poster(base, paths["poster"], title=title, subtitle=subtitle or None, font=font or None,
                    style=spec.get("subtitle"), layout=layout, kicker=kicker or None)
    entry = {
        "stamp": paths["stamp"], "base": base.name, "poster": paths["poster"].name,
        "title": title, "subtitle": subtitle, "font": font or (subtitles.resolve_font(spec.get("subtitle")) or ""),
        "layout": layout, "layout_used": getattr(P.render_poster, "last_layout", layout), "kicker": kicker,
        "source": source, "shot_id": shot_id, "model": model, "prompt": prompt, "cost_note": cost,
        "created_at": time.time(),
    }
    return P.record(spec, entry)


def _note(**fields: Any) -> None:
    """往自己这条后台任务记录里写一行进度（不在任务线程里时什么也不做）。"""

    task_id = tasks.current_task_id()
    if task_id:
        tasks.note(task_id, **fields)


def _wait_for_result(provider: Any, task_id: str, *, max_wait: float, poll: float,
                     sleep: Callable[[float], None], note: Callable[..., None] | None) -> tuple[str, ...]:
    """异步来源：轮询到完成 / 失败 / 超时。每轮写一行进度，界面轮询任务时能看到「还在等」。"""

    started = time.monotonic()
    rounds = 0
    while True:
        snapshot = provider.get_task(task_id, TaskType.IMAGE)
        if snapshot.status is TaskState.FINISHED:
            if not snapshot.output_urls:
                raise P.PosterError("生图来源说完成了，但没有给结果地址")
            return tuple(snapshot.output_urls)
        if snapshot.status is TaskState.FAILED:
            raise P.PosterError(snapshot.error_message or "生图来源返回失败")
        rounds += 1
        if note:
            note(current="poster", waiting_rounds=rounds)
        if time.monotonic() - started >= max_wait:
            raise P.PosterError(f"等了 {int(max_wait // 60)} 分钟还没出图（远端任务 {task_id}），先放弃这一张")
        sleep(poll)


def run_model_poster(episode_id: str, *, title: str, subtitle: str, font: str, provider: Any = None,
                     layout: str = "auto", kicker: str = "", style_card: str = "",
                     max_wait: float | None = None, poll: float | None = None,
                     sleep: Callable[[float], None] = time.sleep,
                     note: Callable[..., None] | None = _note) -> dict[str, Any]:
    """后台任务体：一次生图请求 → 底图 → 叠字。

    同步出图的来源（网关返回 base64 / ``file:`` 任务）直接落盘；异步来源（返回远端任务 id 的）在这个任务里轮询，
    最多等 ``MAX_WAIT_SECONDS``（20 分钟），每轮往任务记录里写一行进度。失败 / 超时抛 ``PosterError``
    （人话原因，后台任务原样记进任务记录），并且不留下半张底图或半张成品。"""

    from storyvideo.spec import load_spec

    from ..deps import PROJECT_ROOT

    spec = load_spec(episode_id, project_root=PROJECT_ROOT)
    data = P.poster_request_data(spec, style_card=style_card or None)
    request = ImageRequest(
        name=data["name"], prompt=data["prompt"], negative_prompt=data["negative_prompt"], model=data["model"],
        aspect_ratio=data["aspect_ratio"], image_size=data["image_size"], quality=data["quality"],
        reference_asset_ids=tuple(data["reference_asset_ids"]), workspace_id=data["workspace_id"],
    )
    provider = provider or build_provider()
    paths = P.poster_paths(spec)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    base: Path = paths["base"]
    if note:
        note(current="poster", progress="1/1")
    try:
        submission = provider.submit_image(request)
        if submission.status is TaskState.FAILED or not submission.task_id:
            raise P.PosterError(submission.error_message or "生图来源没有返回结果")
        if submission.status is TaskState.FINISHED and submission.output_urls:
            urls = tuple(submission.output_urls)
        else:
            urls = _wait_for_result(provider, submission.task_id, sleep=sleep, note=note,
                                    max_wait=MAX_WAIT_SECONDS if max_wait is None else max_wait,
                                    poll=POLL_SECONDS if poll is None else poll)
        provider.download(urls[0], base)
        if not base.is_file() or base.stat().st_size == 0:
            raise P.PosterError("下载下来的图是空文件")
    except (ProviderError, P.PosterError, OSError, ValueError) as exc:
        base.unlink(missing_ok=True)                       # 半张底图不留在 07_poster 里
        if isinstance(exc, P.PosterError):
            raise
        raise P.PosterError(f"海报底图生成失败：{exc}") from exc
    try:
        entry = _finish(spec, base=base, paths=paths, title=title, subtitle=subtitle, font=font,
                        source="model", model=data["model"], prompt=data["prompt"], cost=P.cost_note(data["model"]),
                        layout=layout, kicker=kicker)
    except P.PosterError:
        base.unlink(missing_ok=True)                       # 叠字失败：底图也不留（没有任何一条记录指向它）
        paths["poster"].unlink(missing_ok=True)
        raise
    return {"entry": entry, "poster": poster_payload(episode_id, spec)}


@router.post("/api/episodes/storyvideo/{episode_id}/poster/generate")
def generate_poster(episode_id: str, req: GenerateRequest) -> dict[str, Any]:
    _path, spec = post_router._load(episode_id)
    source = str(req.source or "").strip()
    if source not in SOURCES:
        raise HTTPException(400, "source 只能是 model（用模型生成关键画面）/ frame（用某一镜首帧）")
    title, subtitle, font = _text_fields(spec, req)
    layout, kicker = _layout_fields(spec, req)
    card = _style_card_field(req)

    if source == "model":
        require_roles(build_provider(), "image")   # 起任务前先查生图角色，没配就 400、不留失败任务
        try:
            task_id = tasks.start("poster", episode_id,
                                  lambda: run_model_poster(episode_id, title=title, subtitle=subtitle, font=font,
                                                           layout=layout, kicker=kicker, style_card=card),
                                  params={"title": title, "subtitle": subtitle, "font": font, "style_card": card})
        except tasks.TaskBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        model = P.poster_request_data(spec, style_card=card or None)["model"]
        return {"task_id": task_id, "model": model, "cost_note": P.cost_note(model), "style_card": card}

    shot_id = str(req.shot_id or "").strip()
    if not shot_id or not any(str(s.get("id")) == shot_id for s in spec.get("shots", ()) if isinstance(s, dict)):
        raise HTTPException(404, f"没有这一镜：{shot_id or '(空)'}")
    root = Path(str(spec["output_root"]))
    frame = root / "01_frames" / f"{shot_id}.png"
    video = root / "02_video" / f"{shot_id}.mp4"
    paths = P.poster_paths(spec)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    try:
        if frame.is_file():
            import shutil

            shutil.copyfile(frame, paths["base"])
        elif video.is_file():
            P.extract_first_frame(video, paths["base"])
        else:
            raise HTTPException(425, f"这一镜还没有首帧或视频，没法当海报底图：{shot_id}")
        entry = _finish(spec, base=paths["base"], paths=paths, title=title, subtitle=subtitle, font=font,
                        source="frame", shot_id=shot_id, prompt=None, cost=None, layout=layout, kicker=kicker)
    except P.PosterError as exc:
        raise HTTPException(425, str(exc)) from exc
    return {"entry": _entry_payload(episode_id, entry), **poster_payload(episode_id, spec)}


@router.post("/api/episodes/storyvideo/{episode_id}/poster/render")
def render_poster(episode_id: str, req: RenderRequest) -> dict[str, Any]:
    """只重排文字：最新底图不动，不调模型。"""

    _path, spec = post_router._load(episode_id)
    title, subtitle, font = _text_fields(spec, req)
    layout, kicker = _layout_fields(spec, req)
    base = P.latest_base_path(spec)
    if base is None:
        raise HTTPException(425, "还没有海报底图，先生成一张（用模型或用某一镜首帧）")
    previous = P.latest(spec) or {}
    paths = P.poster_paths(spec)
    try:
        entry = _finish(spec, base=base, paths=paths, title=title, subtitle=subtitle, font=font,
                        source=str(previous.get("source") or "render"), shot_id=previous.get("shot_id"),
                        model=previous.get("model"), prompt=previous.get("prompt"), cost=None, layout=layout, kicker=kicker)
    except P.PosterError as exc:
        raise HTTPException(425, str(exc)) from exc
    return {"entry": _entry_payload(episode_id, entry), **poster_payload(episode_id, spec)}


@router.get("/api/episodes/storyvideo/{episode_id}/poster/file/{name}")
def poster_file(episode_id: str, name: str) -> FileResponse:
    if "/" in name or "\\" in name or name.startswith(".") or not name.endswith(".png") or name != Path(name).name:
        raise HTTPException(404, "没有这个文件")
    _path, spec = post_router._load(episode_id)
    path = P.poster_dir(spec) / name
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "没有这个文件")
    return FileResponse(path, media_type="image/png")
