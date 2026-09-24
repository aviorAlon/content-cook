"""音色库接口 + 绑定到角色/旁白。存储与语义见 genkit.voices。"""

from __future__ import annotations

import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from genkit import voices as V
from genkit.providers import ProviderCommandError, ProviderError
from storyvideo.spec import normalize_spec as normalize_storyvideo_spec
from storyvideo.spec import SpecError
from storyvideo.state import atomic_write_json

from ..deps import is_locked, read_json_bom
from ..humanize import humanize
from ..i18n import tr
from ..uploads import UploadContentError, accept_audio
from . import post as post_router
from . import project as project_router

router = APIRouter(tags=["voices"])


class VoiceIn(BaseModel):
    name: str | None = None
    description: str | None = None
    description_mode: str | None = None    # traits（按维度拼）/ custom（手写）
    traits: dict[str, Any] | None = None   # gender / age / timbre[] / pace / volume / accent / mood
    engine: str | None = None
    voice: str | None = None
    rate: str | None = None
    pitch: str | None = None
    tags: list[str] | None = None
    sample_text: str | None = None
    reference_audio: str | None = None     # 本地 wav/mp3/m4a 路径（音色目录相对 / 项目相对 / 绝对）；"" = 清掉（克隆状态一起归零）


class VoiceCreate(VoiceIn):
    id: str | None = None      # 不给就按名字生成


@router.get("/api/voices")
def list_voices() -> list[dict[str, Any]]:
    return V.list_profiles()


@router.post("/api/voices")
def create_voice(req: VoiceCreate) -> dict[str, Any]:
    try:
        return V.create_profile(req.id, req.model_dump(exclude={"id"}, exclude_none=True))
    except V.VoiceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/voices/{voice_id}")
def update_voice(voice_id: str, req: VoiceIn) -> dict[str, Any]:
    try:
        return V.update_profile(voice_id, req.model_dump(exclude_none=True))
    except V.VoiceError as exc:
        raise HTTPException(404 if "没有这个音色" in str(exc) else 400, str(exc)) from exc


@router.delete("/api/voices/{voice_id}")
def delete_voice(voice_id: str) -> dict[str, Any]:
    try:
        V.delete_profile(voice_id)
    except V.VoiceError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"deleted": voice_id}


@router.get("/api/voices/catalog")
def voice_catalog(engine: str = "edge", locale: str = "zh,en") -> dict[str, Any]:
    """可选音色目录。edge：edge-tts 在线表（需联网）；settings：按「模型设置」里的语音来源给；minimax：系统音色表
    （只在配了 MiniMax 密钥时可用）。响应里的 ``engines`` 是档案当前能选的引擎（minimax 按密钥出现）。"""

    engines = V.available_engines()
    try:
        if engine == "settings":
            return {**V.settings_catalog(), "engines": engines}
        if engine == "minimax":
            return {**V.minimax_catalog(), "engines": engines}
        prefixes = tuple(f"{p.strip()}-" for p in locale.split(",") if p.strip()) if locale else ()
        return {"adapter": "edge_tts", "voices": V.edge_catalog(prefixes), "note": "", "engines": engines}
    except Exception as exc:  # noqa: BLE001 - 联网失败要告诉用户，不是 500
        raise HTTPException(502, f"读取音色目录失败（需要联网）：{exc}") from exc


class SampleRequest(BaseModel):
    text: str | None = None


class TraitsIn(BaseModel):
    traits: dict[str, Any]


@router.post("/api/voices/preview-description")
def preview_description(req: TraitsIn) -> dict[str, Any]:
    """维度 → 中英两版描述的实时预览（不落盘）。"""

    try:
        return {"zh": V.compile_description(req.traits, "zh"), "en": V.compile_description(req.traits, "en")}
    except V.VoiceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/voices/draft-traits")
def draft_traits(voice: str, engine: str = "edge") -> dict[str, Any]:
    """从目录条目反推一版维度初稿（性别、口音、性格关键词）；年龄留空让人填。"""

    entry = None
    try:
        if engine == "edge":
            entry = next((v for v in V.edge_catalog(()) if v.get("voice") == voice), None)
        else:
            entry = next((v for v in V.settings_catalog().get("voices", []) if v.get("voice") == voice), None)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"读取音色目录失败（需要联网）：{exc}") from exc
    if entry is None:
        return {"traits": V.normalize_traits({}), "found": False}
    return {"traits": V.traits_from_catalog(entry), "found": True, "entry": entry}


@router.post("/api/voices/{voice_id}/sample")
def make_sample(voice_id: str, req: SampleRequest) -> dict[str, Any]:
    try:
        path = V.synthesize_sample(voice_id, req.text)
    except V.VoiceError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ProviderCommandError as exc:
        # 本机配置 / 请求本身的问题（engine=settings 但语音角色没配、服务商明确拒绝）：跟其余接口一样 400，
        # 502 是网关坏了的意思——minimax 那条路同样的「没配密钥」走的就是 400（FAM-7）
        raise HTTPException(400, f"试听合成失败：{humanize(exc)}") from exc
    except ProviderError as exc:
        raise HTTPException(502, f"试听合成失败：{humanize(exc)}") from exc
    return {"id": voice_id, "sample_url": f"/api/voices/{voice_id}/sample?t={path.stat().st_mtime_ns}", "bytes": path.stat().st_size}


@router.get("/api/voices/{voice_id}/sample")
def get_sample(voice_id: str) -> FileResponse:
    path = V.current_sample(voice_id)
    if path is None:
        raise HTTPException(404, "还没有试听样本，先点「试听」")
    return FileResponse(path, media_type="audio/mpeg")


# ---------------------------------------------------------------------------- 参考音频与克隆（DESIGN §33）

REFERENCE_MEDIA = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}


class ReferenceAudioUpload(BaseModel):
    filename: str
    data_base64: str


def _profile_or_404(voice_id: str) -> dict[str, Any]:
    if not V.ID_RE.fullmatch(voice_id):
        raise HTTPException(404, f"没有这个音色：{voice_id}")
    profile = V.get_profile(voice_id)
    if profile is None:
        raise HTTPException(404, f"没有这个音色：{voice_id}")
    return profile


@router.post("/api/voices/{voice_id}/reference-audio")
def upload_reference_audio(voice_id: str, req: ReferenceAudioUpload) -> dict[str, Any]:
    """参考音频（base64）存进 ``<音色目录>/reference/<音色>-<哈希>-<时间戳>.<ext>``（永不覆盖，旧的尽力清理），
    档案 ``reference_audio`` 指向它，克隆状态归零。只存文件不克隆——克隆另点。"""

    import base64

    from genkit.adapters.minimax_speech import MAX_REFERENCE_BYTES

    _profile_or_404(voice_id)
    try:
        data = base64.b64decode(req.data_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"音频数据不是合法的 base64：{exc}") from exc
    if not data:
        raise HTTPException(400, "音频为空")
    if len(data) > MAX_REFERENCE_BYTES:
        raise HTTPException(413, f"参考音频超过 {MAX_REFERENCE_BYTES // 1024 // 1024}MB")
    # 按内容认（uploads.accept_audio）：以前只看扩展名，.mp3 里塞任意字节也存下、克隆时才被 MiniMax 拒（还白等一轮）。
    # 读得出音频但格式 / 编码不在 mp3 / wav / m4a 里（AIFF、手机录音的 webm、手机录的视频…）就只取音轨转成 wav / m4a
    # ——克隆接口只收这三种；转出来也不能超过克隆接口的上限
    try:
        accepted = accept_audio(data, req.filename, allowed=tuple(REFERENCE_MEDIA), max_bytes=MAX_REFERENCE_BYTES)
    except UploadContentError as exc:
        if exc.status == 413:
            raise HTTPException(413, f"参考音频要转换格式才能用，转完会超过 {MAX_REFERENCE_BYTES // 1024 // 1024}MB"
                                     "——剪短一些（克隆只要 10 秒到 5 分钟），或先转成 mp3 再传") from exc
        raise HTTPException(exc.status, str(exc)) from exc
    digest = hashlib.sha256(data).hexdigest()[:8]
    target = V.reference_dir() / f"{voice_id}-{digest}-{int(time.time() * 1000)}{accepted.suffix}"
    target.write_bytes(accepted.data)
    try:
        profile = V.update_profile(voice_id, {"reference_audio": f"reference/{target.name}"})
    except V.VoiceError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    V.sweep_reference_audio(voice_id, keep=target)
    return {**profile, "bytes": len(accepted.data), "reference_url": f"/api/voices/{voice_id}/reference-audio?t={target.stat().st_mtime_ns}"}


@router.get("/api/voices/{voice_id}/reference-audio")
def get_reference_audio(voice_id: str) -> FileResponse:
    profile = _profile_or_404(voice_id)
    path = V.reference_audio_path(profile)
    if path is None or path.suffix.lower() not in REFERENCE_MEDIA or not path.is_file():
        raise HTTPException(404, "这个音色还没有参考音频")
    return FileResponse(path, media_type=REFERENCE_MEDIA[path.suffix.lower()])


@router.post("/api/voices/{voice_id}/clone")
def clone_voice(voice_id: str) -> dict[str, Any]:
    """把参考音频送去 MiniMax 克隆（后台任务，提交即返回）。进度看档案的 ``clone.status``：cloning → ready / failed（带原因）。"""

    from .. import tasks

    profile = _profile_or_404(voice_id)
    if not profile.get("reference_audio"):
        raise HTTPException(400, "这个音色还没有参考音频——先上传一段 10 秒到 5 分钟的清晰人声")
    if (profile.get("clone") or {}).get("status") == "cloning":
        raise HTTPException(409, "这个音色正在克隆，等它结束")
    try:
        V.build_minimax_speech()
    except V.VoiceError as exc:
        raise HTTPException(400, str(exc)) from exc

    def work() -> dict[str, Any]:
        try:
            return V.clone_voice(profile)
        except V.VoiceError:
            raise
        except Exception as exc:  # noqa: BLE001 - 原因翻成人话写回档案；任务记录也带同一句
            message = f"克隆失败：{humanize(exc)}"
            V.record_clone_error(voice_id, message)
            raise RuntimeError(message) from exc

    try:
        task_id = tasks.start("voice-clone", f"voice:{voice_id}", work)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    fresh = V.get_profile(voice_id) or profile
    return {"task_id": task_id, "id": voice_id, "clone": fresh.get("clone") or {"status": "cloning"}}


# ---------------------------------------------------------------------------- 绑定


class BindRequest(BaseModel):
    voice_id: str | None = None   # None / "" = 解绑


@router.post("/api/episodes/storyvideo/{episode_id}/cast/{cast_id}/voice")
def bind_cast_voice(episode_id: str, cast_id: str, req: BindRequest) -> dict[str, Any]:
    """把音色绑定给角色：描述文字拷进 cast.voice（进视频提示词），TTS 参数拷进 cast.voice_tts。
    角色声音描述是生成字段——开始制作后拒绝，跟改角色外形一样。"""

    path, spec = project_router._load(episode_id)
    raw = read_json_bom(path)
    cast = raw.get("cast") or {}
    if cast_id not in cast or not isinstance(cast[cast_id], dict):
        raise HTTPException(404, f"没有这个角色：{cast_id}")
    project_router._refuse_if_locked(spec, cast_id, f"给「{cast_id}」换声音")
    profile = None
    if req.voice_id:
        profile = V.get_profile(req.voice_id)
        if profile is None:
            raise HTTPException(404, f"音色库里没有：{req.voice_id}")
    cast[cast_id] = V.bind_cast(cast[cast_id], profile, lang=str(raw.get("prompt_language") or "zh"))
    try:
        normalize_storyvideo_spec(raw, project_root=project_router.PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(400, f"改动没通过校验，没有写盘：{exc}") from exc
    atomic_write_json(path, raw)
    fresh = project_router.load_storyvideo_spec(episode_id, project_root=project_router.PROJECT_ROOT)
    return next(c for c in project_router.cast_view(episode_id, raw, fresh) if c["id"] == cast_id)


# ---------------------------------------------------------------------------- 音色对比角色（DESIGN §29）
# 同一句台词让几个候选音色各念一遍，并排听完再绑——绑定仍走上面的 bind_cast_voice，这里不写 spec。

COMPARE_MIN, COMPARE_MAX = 2, 6
COMPARE_TIMEOUT_SECONDS = 90.0
COMPARE_DIRNAME = "09_voice_compare"
_COMPARE_NAME_RE = re.compile(r"^(?P<voice>[A-Za-z0-9_][A-Za-z0-9_.-]*)-(?P<hash>[0-9a-f]{8})-(?P<ts>\d+)\.mp3\Z")


class VoiceCompareRequest(BaseModel):
    voices: list[str]
    text: str | None = None


def _compare_dir(spec: dict[str, Any], cast_id: str) -> Path:
    root = Path(str(spec["output_root"]))
    if not root.is_absolute():
        root = project_router.PROJECT_ROOT / root
    return root / COMPARE_DIRNAME / cast_id


def first_line_of(spec: dict[str, Any], cast_id: str) -> str:
    """角色在剧本里的第一句台词：按提示词语言取 zh / en，另一种语言兜底；没有就空串。"""

    lang = str(spec.get("prompt_language") or "zh")
    order = ("en", "zh") if lang == "en" else ("zh", "en")
    for shot in spec.get("shots") or []:
        for line in shot.get("lines") or []:
            if not isinstance(line, dict) or str(line.get("who") or "") != cast_id:
                continue
            for key in order:
                text = str(line.get(key) or "").strip()
                if text:
                    return text
    return ""


def _compare_file_url(episode_id: str, cast_id: str, name: str) -> str:
    return f"/api/episodes/storyvideo/{episode_id}/cast/{cast_id}/voice-compare/file/{name}"


def _synthesize_one(profile: dict[str, Any], text: str, destination: Path) -> Path:
    if not profile.get("voice"):
        raise V.VoiceError("这个音色只有文字描述，没有 TTS 音色，无法试听——它只用于视频模型直出台词的提示词")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return V.synthesize_with_profile(profile, text, destination)


def _compare_key(profile: dict[str, Any], text: str) -> str:
    raw = "|".join([text, *(str(profile.get(k) or "") for k in ("engine", "voice", "rate", "pitch"))])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


@router.post("/api/episodes/storyvideo/{episode_id}/cast/{cast_id}/voice-compare")
def compare_cast_voices(episode_id: str, cast_id: str, req: VoiceCompareRequest) -> dict[str, Any]:
    """同一句台词、几个候选音色各合成一遍，文件放在项目 ``09_voice_compare/<角色>/`` 下（永不覆盖）。
    一个音色失败不拖累整批：失败的条目带 ``error``。不改 spec，制作中也能用。"""

    _path, spec = project_router._load(episode_id)
    cast = spec.get("cast") or {}
    if cast_id not in cast or not isinstance(cast[cast_id], dict):
        raise HTTPException(404, f"没有这个角色：{cast_id}")
    voice_ids = [v for v in (str(x or "").strip() for x in req.voices) if v]
    if len(voice_ids) != len(set(voice_ids)):
        dup = next(v for v in voice_ids if voice_ids.count(v) > 1)
        raise HTTPException(400, f"音色重复了：{dup}")
    if not COMPARE_MIN <= len(voice_ids) <= COMPARE_MAX:
        raise HTTPException(400, f"对比试听要选 {COMPARE_MIN} 到 {COMPARE_MAX} 个音色，现在是 {len(voice_ids)} 个")
    text = str(req.text or "").strip() or first_line_of(spec, cast_id)
    if not text:
        raise HTTPException(400, "这个角色没有台词，填一句试听文字")

    out_dir = _compare_dir(spec, cast_id)
    stamp = int(time.time() * 1000)
    jobs: list[tuple[str, dict[str, Any] | None, Path | None]] = []
    for vid in voice_ids:
        profile = V.get_profile(vid)
        if profile is None:
            jobs.append((vid, None, None))
        else:
            jobs.append((vid, profile, out_dir / f"{vid}-{_compare_key(profile, text)}-{stamp}.mp3"))

    items: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        futures = {vid: pool.submit(_synthesize_one, profile, text, dest) for vid, profile, dest in jobs if profile is not None and dest is not None}
        for vid, profile, dest in jobs:
            item: dict[str, Any] = {"voice_id": vid, "name": (profile.get("name") or vid) if profile else vid}
            if profile is None or dest is None:
                item["error"] = tr(f"音色库里没有：{vid}")
                items.append(item)
                continue
            try:
                futures[vid].result(timeout=COMPARE_TIMEOUT_SECONDS)
                item["file"] = dest.name
                item["url"] = _compare_file_url(episode_id, cast_id, dest.name)
                item["bytes"] = dest.stat().st_size
            except FutureTimeout:
                item["error"] = tr(f"合成超时（{int(COMPARE_TIMEOUT_SECONDS)} 秒）")
            except V.VoiceError as exc:
                item["error"] = tr(str(exc))
            except ProviderError as exc:
                item["error"] = tr(f"试听合成失败：{humanize(exc)}")
            except Exception as exc:  # noqa: BLE001 - 一个音色炸了不能让整批 500
                item["error"] = tr(f"试听合成失败：{humanize(exc)}")
            items.append(item)
    ok = [i for i in items if "file" in i]
    if ok:
        out_dir.mkdir(parents=True, exist_ok=True)
        index_path = out_dir / "index.json"
        index = read_json_bom(index_path) if index_path.is_file() else {}
        runs = list(index.get("runs") or []) if isinstance(index, dict) else []
        runs.insert(0, {"at": stamp / 1000, "text": text, "items": [{"voice_id": i["voice_id"], "name": i["name"], "file": i["file"]} for i in ok]})
        atomic_write_json(index_path, {"schema_version": 1, "runs": runs[:50]})
    return {"cast_id": cast_id, "text": text, "items": items}


@router.get("/api/episodes/storyvideo/{episode_id}/cast/{cast_id}/voice-compare")
def list_cast_voice_compare(episode_id: str, cast_id: str) -> dict[str, Any]:
    """已有的对比结果，按批次（最新在前）；文件被清掉的条目不列。"""

    _path, spec = project_router._load(episode_id)
    cast = spec.get("cast") or {}
    if cast_id not in cast or not isinstance(cast[cast_id], dict):
        raise HTTPException(404, f"没有这个角色：{cast_id}")
    out_dir = _compare_dir(spec, cast_id)
    index_path = out_dir / "index.json"
    index = read_json_bom(index_path) if index_path.is_file() else {}
    names = {p["id"]: p.get("name") or p["id"] for p in V.list_profiles()}
    runs = []
    for run in (index.get("runs") or []) if isinstance(index, dict) else []:
        items = []
        for it in run.get("items") or []:
            name = str(it.get("file") or "")
            if not _COMPARE_NAME_RE.match(name) or not (out_dir / name).is_file():
                continue
            vid = str(it.get("voice_id") or "")
            items.append({"voice_id": vid, "name": names.get(vid, it.get("name") or vid), "file": name,
                          "url": _compare_file_url(episode_id, cast_id, name), "in_library": vid in names})
        if items:
            runs.append({"at": run.get("at"), "text": run.get("text") or "", "items": items})
    return {"cast_id": cast_id, "default_text": first_line_of(spec, cast_id), "bound": str(cast[cast_id].get("voice_ref") or ""), "runs": runs}


@router.get("/api/episodes/storyvideo/{episode_id}/cast/{cast_id}/voice-compare/file/{name}")
def get_cast_voice_compare_file(episode_id: str, cast_id: str, name: str) -> FileResponse:
    if "/" in name or "\\" in name or name.startswith(".") or not _COMPARE_NAME_RE.match(name):
        raise HTTPException(404, "没有这个文件")
    _path, spec = project_router._load(episode_id)
    path = _compare_dir(spec, cast_id) / name
    if not path.is_file():
        raise HTTPException(404, "没有这个文件")
    return FileResponse(path, media_type="audio/mpeg")


@router.post("/api/episodes/storyvideo/{episode_id}/voice-over/voice")
def bind_narration_voice(episode_id: str, req: BindRequest) -> dict[str, Any]:
    """把音色绑定给旁白：写 voice_over.engine / edge。旁白是后期块，制作中也能改（走 amend）。"""

    path, _spec = project_router._load(episode_id)
    raw = read_json_bom(path)
    profile = None
    if req.voice_id:
        profile = V.get_profile(req.voice_id)
        if profile is None:
            raise HTTPException(404, f"音色库里没有：{req.voice_id}")
    try:
        vo = V.bind_voice_over(raw.get("voice_over") or {}, profile)
    except V.VoiceError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = post_router.put_post_layers(episode_id, post_router.PostLayersEdit(voice_over=vo))
    return {**result, "voice_over": vo, "locked": is_locked(_spec["output_root"])}
