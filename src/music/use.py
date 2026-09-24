"""投放（契约 docs/music-spec.md §3）——这个模块存在的理由。

把当前采用的那条拷进目标项目的素材目录，再写进目标项目的配乐层：

* ``drama``：拷进 ``content/assets/<剧集>/music/``，追加到该剧集的 ``post_graphics.music``——**走
  ``webui/routers/post.py`` 的 ``put_post_layers``**（后期块校验 + 冻结后的 amend 都在那条路上），
  不自己写 spec 文件；
* ``picturebook``：拷进 ``content/assets/<绘本>/music/``，写进绘本的 ``music.file``（走
  ``picturebook.spec.save_book`` 的归一化）；
* ``podcast``：拷进 ``content/assets/<播客>/music/``，写进播客的 ``music.<slot>``——槽只认
  ``intro / bed / outro``（片头 / 垫乐 / 片尾），不给就是 ``bed``；写回前过一遍 ``podcast.spec.normalize``
  再 ``save``，不绕过。

成功后往配乐项目的 ``used_in`` 追一条，并把目标项目的名字带回去，前端提示「已放进《…》的配乐层」
（播客按槽说：「已放进《…》的垫乐 / 片头 / 片尾」）。拷贝而不是引用：删掉配乐项目不会让别人的成片突然少一条轨。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Mapping

from genkit.ids import ID_RE

from . import spec as S


class MusicUseError(RuntimeError):
    """投放失败（没有可投放的、目标不存在、目标不接受这次改动）。"""


class UnknownTarget(MusicUseError):
    """目标项目不存在（路由翻成 404）。"""


# 播客三个槽的中文名（前端下拉与成功提示都用它；槽 id 以 ``podcast.spec.MUSIC_SLOTS`` 为准）
from podcast.spec import MUSIC_SLOT_LABELS as PODCAST_SLOT_LABELS   # 槽名与中文名只在播客模块定义一次
PODCAST_DEFAULT_SLOT = "bed"


def _copy_into(source: Path, project_root: Path, target_id: str) -> str:
    """拷进 ``content/assets/<目标>/music/``，文件名带内容摘要——同一条重复投放不会堆出一堆副本。
    返回项目内的相对路径（posix）。"""

    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()[:8]
    music_dir = Path(project_root) / S.ASSETS_DIR / target_id / "music"
    # 兜底：目标 id 在 use_music 入口已经过 ID_RE，这里再确认拼出来的目录没跑出 content/assets/——
    # 拷文件是这个模块唯一会写别人目录的动作，不能靠上游一道检查
    assets_root = (Path(project_root) / S.ASSETS_DIR).resolve(strict=False)
    try:
        music_dir.resolve(strict=False).relative_to(assets_root)
    except ValueError as exc:
        raise MusicUseError(f"没有这个目标项目：{target_id}") from exc
    music_dir.mkdir(parents=True, exist_ok=True)
    target = music_dir / f"{source.stem}-{digest}{source.suffix.lower()}"
    if not target.is_file():
        target.write_bytes(data)
    return target.relative_to(Path(project_root)).as_posix()


# ---------------------------------------------------------------------------- 目标清单


def list_targets(project_root: Path) -> dict[str, list[dict[str, Any]]]:
    """可投放的项目：短剧剧集 + 绘本 + 播客。读不出来的文件不跳过、按 id 当标题列出（一个坏文件不该让下拉框空掉）。
    播客行多带 ``slots``：已经有文件的槽名列表（前端在槽下拉里标「已有，会覆盖」）。"""

    from picturebook import spec as PB
    from podcast import spec as PC

    drama: list[dict[str, Any]] = []
    story_dir = Path(project_root) / "content" / "story_video"
    if story_dir.is_dir():
        for path in sorted(story_dir.glob("*.json")):
            if path.stem.endswith(".screenplay"):
                continue
            title = _peek(path, ("title_zh", "title", "title_en"))
            tracks = _peek_music_count(path)
            drama.append({"module": "drama", "id": path.stem, "title": title or path.stem, "tracks": tracks})
    books: list[dict[str, Any]] = []
    book_dir = PB.books_dir(Path(project_root))
    if book_dir.is_dir():
        for path in sorted(book_dir.glob("*.json")):
            title = _peek(path, ("title_zh", "title_en", "title"))
            has = bool(_peek_book_music(path))
            books.append({"module": "picturebook", "id": path.stem, "title": title or path.stem, "tracks": 1 if has else 0})
    podcasts: list[dict[str, Any]] = []
    podcast_dir = PC.content_dir(Path(project_root))
    if podcast_dir.is_dir():
        for path in sorted(podcast_dir.glob("*.json")):
            title = _peek(path, ("title",))
            slots = _peek_podcast_slots(path)
            podcasts.append({"module": "podcast", "id": path.stem, "title": title or path.stem, "tracks": len(slots), "slots": slots})
    return {"drama": drama, "picturebook": books, "podcast": podcasts}


def _read(path: Path) -> dict[str, Any]:
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _peek(path: Path, keys: tuple[str, ...]) -> str:
    data = _read(path)
    for key in keys:
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def _peek_music_count(path: Path) -> int:
    graphics = _read(path).get("post_graphics")
    tracks = graphics.get("music") if isinstance(graphics, dict) else None
    return len(tracks) if isinstance(tracks, list) else 0


def _peek_book_music(path: Path) -> Any:
    music = _read(path).get("music")
    return (music or {}).get("file") if isinstance(music, dict) else music


def _peek_podcast_slots(path: Path) -> list[str]:
    """播客里已经有文件的槽（按 intro / bed / outro 的固定顺序）。"""

    from podcast import spec as PC

    music = _read(path).get("music")
    if not isinstance(music, dict):
        return []
    return [slot for slot in PC.MUSIC_SLOTS if str(music.get(slot) or "").strip()]


def target_title(project_root: Path, module: str, target_id: str) -> str:
    for row in list_targets(project_root).get(module) or ():
        if row["id"] == target_id:
            return str(row["title"])
    return target_id


# ---------------------------------------------------------------------------- 投放


def use_music(spec: dict[str, Any], *, module: str, target_id: str, slot: Any = None,
              project_root: Path | None = None) -> dict[str, Any]:
    """把 ``spec`` 当前采用的那条放进目标项目的配乐层。就地往 ``used_in`` 追一条（调用方负责写盘）。"""

    module = str(module or "").strip()
    if module not in S.USE_MODULES:
        raise MusicUseError(f"module 只能是 {' / '.join(S.USE_MODULES)}，收到「{module}」")
    target_id = str(target_id or "").strip()
    if not target_id:
        raise MusicUseError("target 不能为空——先选一个目标项目")
    # 三条路径统一先过 id 正则：目标 id 会直接拼进 content/assets/<id>/ 与 content/<模块>/<id>.json，
    # 「../picturebook/xx」这种只要能拼出一个存在的 .json 就会先把音频拷进别的模块目录再被拒绝
    if not ID_RE.fullmatch(target_id):
        raise UnknownTarget(f"没有这个目标项目：{target_id}")
    source = S.current_path(spec)
    if source is None:
        raise MusicUseError("还没有采用的配乐——先生成，再在历次里点「采用」")
    root = Path(project_root or spec["project_root"])
    if module == "drama":
        result = _use_in_drama(source, root, target_id, slot)
    elif module == "podcast":
        result = _use_in_podcast(source, root, target_id, slot)
    else:
        result = _use_in_picturebook(source, root, target_id)
    entry = S.record_use(spec, module=module, target_id=target_id, slot=result.get("slot") or "")
    return {**result, "module": module, "target": target_id, "used": entry}


def _use_in_drama(source: Path, root: Path, episode_id: str, slot: Any) -> dict[str, Any]:
    """走 ``routers/post.py`` 的 ``put_post_layers``：后期块校验、冻结后的 amend 都在那条路上。"""

    from fastapi import HTTPException

    from webui.routers import post as post_router

    path = post_router.PROJECT_ROOT / "content" / "story_video" / f"{episode_id}.json"
    if not path.is_file():
        raise UnknownTarget(f"没有这个短剧项目：{episode_id}")
    # 目标读得出来、slot 合法，都确认了再拷文件：请求会被拒的话，不该在目标的素材目录里先留下一份 mp3
    try:
        post_router._load(episode_id)
    except HTTPException as exc:
        # 校验原文可能带本机绝对路径（invalid JSON in episode spec C:\…\EP.json …），只留项目内的相对写法
        raise MusicUseError(f"目标项目读不出来：{_strip_root(str(exc.detail), post_router.PROJECT_ROOT)}") from exc
    start = _seconds(slot)
    relative = _copy_into(source, post_router.PROJECT_ROOT, episode_id)
    raw = post_router.read_json_bom(path)
    graphics = dict(raw.get("post_graphics") or {})
    tracks = [t for t in (graphics.get("music") or []) if isinstance(t, dict)]
    track: dict[str, Any] = {"file": relative, "label": _label(source)}
    if start is not None:
        track["start"] = start
    tracks = [t for t in tracks if str(t.get("file") or "") != relative]
    tracks.append(track)
    graphics["music"] = tracks
    try:
        saved = post_router.put_post_layers(episode_id, post_router.PostLayersEdit(post_graphics=graphics))
    except HTTPException as exc:
        if exc.status_code == 404:
            raise UnknownTarget(f"没有这个短剧项目：{episode_id}") from exc
        # 里层已经带了「改动没通过校验，没有写盘：」前缀，去掉它——套两层前缀既啰嗦又翻不动
        detail = str(exc.detail).removeprefix("改动没通过校验，没有写盘：")
        raise MusicUseError(f"目标项目没接受这条配乐：{detail}") from exc
    return {"file": relative, "slot": "" if start is None else str(start), "track": track,
            "title": _peek(path, ("title_zh", "title", "title_en")) or episode_id,
            "tracks": len(tracks), "post_layers": saved}


def _use_in_picturebook(source: Path, root: Path, book_id: str) -> dict[str, Any]:
    from picturebook import spec as PB

    if not PB.book_path(root, book_id).is_file():
        raise UnknownTarget(f"没有这本绘本：{book_id}")
    try:
        book = PB.load_book(book_id, project_root=root)
    except PB.BookSpecError as exc:
        raise MusicUseError(f"目标绘本读不出来：{exc}") from exc
    relative = _copy_into(source, root, book_id)
    music = dict(book.get("music") or {})
    music["file"] = relative
    book["music"] = music
    try:
        PB.save_book(book, project_root=root)
    except PB.BookSpecError as exc:
        raise MusicUseError(f"目标绘本没接受这条配乐：{exc}") from exc
    return {"file": relative, "slot": "", "track": music, "title": _book_title(book), "tracks": 1}


def _book_title(book: Mapping[str, Any]) -> str:
    return str(book.get("title_zh") or book.get("title_en") or book.get("id") or "")


def _use_in_podcast(source: Path, root: Path, podcast_id: str, slot: Any) -> dict[str, Any]:
    """写进播客的 ``music.<slot>``：读 → 改一个槽 → 整份再过 ``podcast.spec.normalize`` → ``save``。
    槽名不对在拷贝之前就拒绝，目标文件与素材目录都不动。"""

    from genkit.ids import ID_RE
    from podcast import spec as PC

    if not ID_RE.fullmatch(podcast_id) or not PC.spec_path(root, podcast_id).is_file():
        raise UnknownTarget(f"没有这期播客：{podcast_id}")
    slot_name = _podcast_slot(slot)
    try:
        podcast = PC.load(podcast_id, project_root=root)
    except PC.PodcastSpecError as exc:
        raise MusicUseError(f"目标播客读不出来：{exc}") from exc
    relative = _copy_into(source, root, podcast_id)
    raw = PC.to_disk(podcast)
    music = dict(raw.get("music") or {})
    music[slot_name] = relative
    raw["music"] = music
    try:
        saved = PC.normalize(raw, project_root=root, spec_id=podcast_id)
    except PC.PodcastSpecError as exc:
        raise MusicUseError(f"目标播客没接受这条配乐：{exc}") from exc
    PC.save(saved)
    filled = [s for s in PC.MUSIC_SLOTS if saved["music"].get(s)]
    return {"file": relative, "slot": slot_name, "slot_label": PODCAST_SLOT_LABELS[slot_name], "track": dict(saved["music"]),
            "title": str(saved.get("title") or podcast_id), "tracks": len(filled), "slots": filled}


def _podcast_slot(slot: Any) -> str:
    """播客的 ``slot`` 是槽名：空 → 垫乐（bed）；别的只认 intro / bed / outro。"""

    from podcast import spec as PC

    if slot is None or (isinstance(slot, str) and not slot.strip()):
        return PODCAST_DEFAULT_SLOT
    # 接口层的 slot 是 float | str：JSON 里的 12 会变成 12.0，报错时按用户写的样子说
    text = str(int(slot)) if isinstance(slot, float) and slot.is_integer() else str(slot).strip()
    if text not in PC.MUSIC_SLOTS:
        choices = " / ".join(f"{s}（{PODCAST_SLOT_LABELS[s]}）" for s in PC.MUSIC_SLOTS)
        raise MusicUseError(f"播客的 slot 是槽名，只能是 {choices}，收到「{text}」")
    return text


def _label(source: Path) -> str:
    return source.stem


def _strip_root(text: str, root: Path) -> str:
    """报错原文里的项目根绝对路径去掉（解析前后两种写法都去），界面上只看到项目内的相对路径。"""

    for prefix in dict.fromkeys((str(Path(root)), str(Path(root).resolve()))):
        text = text.replace(prefix + os.sep, "").replace(prefix, "")
    return text


def _seconds(slot: Any) -> float | None:
    """``slot`` 可选：短剧的起始秒。空 / 认不出 → None（= 从头开始）。"""

    if slot is None or (isinstance(slot, str) and not slot.strip()):
        return None
    if isinstance(slot, bool):
        raise MusicUseError("slot 要是秒数（0 到 3600 之间的数）")
    try:
        value = float(slot)
    except (TypeError, ValueError) as exc:
        raise MusicUseError("slot 要是秒数（0 到 3600 之间的数）") from exc
    if not 0 <= value <= 3600:
        raise MusicUseError("slot 要是秒数（0 到 3600 之间的数）")
    return round(value, 3)


__all__ = ["MusicUseError", "UnknownTarget", "PODCAST_SLOT_LABELS", "PODCAST_DEFAULT_SLOT", "list_targets", "target_title", "use_music"]
