"""语音：MiniMax T2A v2（``POST /t2a_v2``，同步返回 hex 音频）+ 音色克隆（``/files/upload`` → ``/voice_clone``）。

按 MiniMax 公开文档写的，还没对真实账号调用过（只用假响应测过）：
* 合成：``{"model": "speech-02-hd", "text", "stream": false, "voice_setting": {"voice_id", "speed", "vol", "pitch"},
  "audio_setting": {"format": "mp3", "sample_rate": 32000, "bitrate": 128000, "channel": 1}}``，
  返回 ``data.audio`` 是 **hex** 字符串（非 base64），``base_resp.status_code == 0`` 表示成功。
* 克隆：先 multipart 上传参考音频（``purpose=voice_clone``，mp3/m4a/wav，10 秒–5 分钟，≤ 20MB）拿 ``file.file_id``，
  再 ``POST /voice_clone`` 带 ``file_id`` 与**客户端自选**的 ``voice_id``（字母开头、≥ 8 位、含字母和数字，
  只能用字母/数字/下划线/短横线）；可带 ``text`` + ``model`` 让服务端顺手出一段试听（``demo_audio`` 地址）。
  克隆是同步的：接口返回即可用该 ``voice_id`` 合成。

密钥用 Bearer；老版接口地址要带 ``?GroupId=``，配置里 ``extra.group_id`` 有值就补上。错误分类沿用公共底座：
HTTP 层 ``raise_for_status``，业务层按 ``base_resp.status_code``（1008 余额不足 → 配额；1002 限流 → 限流，
是配额的子类、同样可重提，只是提示等一会而不是去充值；1026/1027 敏感内容 → 审核拦截；其他 → 一般错误）。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any, Mapping

from ..providers import ProviderCommandError, ProviderRateLimited, ProviderRejected, QuotaExhausted
from .base import Adapter, raise_for_status

DEFAULT_MODEL = "speech-02-hd"
DEFAULT_VOICE = "female-shaonv"
REFERENCE_SUFFIXES = (".mp3", ".m4a", ".wav")
MAX_REFERENCE_BYTES = 20 * 1024 * 1024
# 文档里的系统音色（中文向的一小部分；克隆出来的 id 由档案自己带）
SYSTEM_VOICES: tuple[tuple[str, str, str], ...] = (
    ("female-shaonv", "Female", "少女"), ("female-yujie", "Female", "御姐"), ("female-chengshu", "Female", "成熟女声"),
    ("female-tianmei", "Female", "甜美女声"), ("presenter_female", "Female", "女主持"), ("audiobook_female_1", "Female", "有声书女 1"),
    ("audiobook_female_2", "Female", "有声书女 2"), ("male-qn-qingse", "Male", "青涩青年"), ("male-qn-jingying", "Male", "精英青年"),
    ("male-qn-badao", "Male", "霸道青年"), ("male-qn-daxuesheng", "Male", "青年大学生"), ("presenter_male", "Male", "男主持"),
    ("audiobook_male_1", "Male", "有声书男 1"), ("audiobook_male_2", "Male", "有声书男 2"),
)
_QUOTA_CODES = {1008, 1039}
_RATE_LIMIT_CODES = {1002}
_MODERATION_CODES = {1026, 1027, 2037}
_VOICE_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{7,}\Z")


def clone_voice_id(slug: str, digest: str) -> str:
    """客户端自选的克隆音色 id：``ss_<slug>_<hash8>``——字母开头、含字母和数字、只用字母/数字/下划线，满足 MiniMax 的规则。"""

    clean = re.sub(r"[^A-Za-z0-9]+", "_", str(slug or "")).strip("_").lower()[:20] or "voice"
    return f"ss_{clean}_{str(digest)[:8]}"


def classify_base_resp(data: Mapping[str, Any], *, what: str) -> None:
    """``base_resp.status_code`` 非 0 就按类别抛；0 或缺失什么都不做。"""

    base = data.get("base_resp") if isinstance(data, Mapping) else None
    if not isinstance(base, Mapping):
        return
    code = base.get("status_code")
    if code in (None, 0):
        return
    msg = str(base.get("status_msg") or "")
    text = f"{what}: minimax {code}: {msg}"
    low = msg.lower()
    if code in _RATE_LIMIT_CODES or "rate limit" in low:
        raise ProviderRateLimited(text)
    if code in _QUOTA_CODES or any(k in low for k in ("insufficient", "balance", "quota", "余额")):
        raise QuotaExhausted(text)
    if code in _MODERATION_CODES or any(k in low for k in ("sensitive", "敏感", "审核", "violat")):
        raise ProviderRejected(text)
    raise ProviderCommandError(text)


def _multipart(fields: Mapping[str, str], file_field: str, filename: str, content: bytes, mime: str) -> tuple[bytes, str]:
    boundary = f"----storystudio{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {mime}\r\n\r\n".encode("utf-8") + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def speed_from_rate(rate: str | None) -> float | None:
    """edge 风格的语速（``-4%`` / ``+10%``）→ MiniMax 的 speed（0.5–2.0）；空或看不懂 → None（不传）。"""

    text = str(rate or "").strip().rstrip("%")
    if not text:
        return None
    try:
        pct = float(text)
    except ValueError:
        return None
    return round(min(2.0, max(0.5, 1.0 + pct / 100.0)), 2)


class MiniMaxSpeech(Adapter):
    name = "minimax_speech"

    def _url(self, path: str) -> str:
        url = f"{self.base_url}{path}"
        group = str((self.config.get("extra") or {}).get("group_id") or self.config.get("group_id") or "")
        return f"{url}?GroupId={group}" if group else url

    def _model(self, model: str | None) -> str:
        requested = str(model or "").strip()
        if requested and not requested.startswith("@"):
            return requested
        return self.model or DEFAULT_MODEL

    # ---- 合成
    def synthesize(self, text: str, destination: Path, *, voice: str | None = None, model: str | None = None,
                   rate: str | None = None, pitch: str | None = None) -> Path:
        chosen_voice = voice or str(self.config.get("voice") or DEFAULT_VOICE)
        voice_setting: dict[str, Any] = {"voice_id": chosen_voice}
        speed = speed_from_rate(rate)
        if speed is not None:
            voice_setting["speed"] = speed
        body = {
            "model": self._model(model),
            "text": text, "stream": False,
            "voice_setting": voice_setting,
            "audio_setting": {"format": "mp3", "sample_rate": 32000, "bitrate": 128000, "channel": 1},
        }
        response = self.http("POST", self._url("/t2a_v2"), headers={**self.auth_headers(), "Content-Type": "application/json"},
                             json_body=body, timeout=120.0)
        raise_for_status(response, what="minimax t2a")
        data = response.json()
        classify_base_resp(data, what="minimax t2a")
        audio_hex = str(((data.get("data") or {}).get("audio")) or "")
        if not audio_hex:
            raise ProviderCommandError(f"minimax t2a returned no audio: {json.dumps(data, ensure_ascii=False)[:200]}")
        try:
            payload = bytes.fromhex(audio_hex)
        except ValueError as exc:
            raise ProviderCommandError("minimax t2a: data.audio is not hex") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return destination

    # ---- 克隆
    def upload_reference(self, path: Path) -> str:
        path = Path(path)
        if not path.is_file():
            raise ProviderCommandError(f"reference audio does not exist: {path}")
        if path.suffix.lower() not in REFERENCE_SUFFIXES:
            raise ProviderCommandError(f"reference audio must be one of {', '.join(REFERENCE_SUFFIXES)}: {path.name}")
        content = path.read_bytes()
        if len(content) > MAX_REFERENCE_BYTES:
            raise ProviderCommandError(f"reference audio exceeds {MAX_REFERENCE_BYTES // 1024 // 1024}MB: {path.name}")
        body, content_type = _multipart({"purpose": "voice_clone"}, "file", path.name, content,
                                        mimetypes.guess_type(path.name)[0] or "audio/mpeg")
        response = self.http("POST", self._url("/files/upload"), headers={**self.auth_headers(), "Content-Type": content_type},
                             data=body, timeout=180.0)
        raise_for_status(response, what="minimax files/upload")
        data = response.json()
        classify_base_resp(data, what="minimax files/upload")
        file_id = (data.get("file") or {}).get("file_id")
        if file_id in (None, ""):
            raise ProviderCommandError(f"minimax files/upload returned no file_id: {json.dumps(data, ensure_ascii=False)[:200]}")
        return str(file_id)

    def clone_voice(self, reference: Path, *, voice_id: str, preview_text: str | None = None, model: str | None = None) -> dict[str, Any]:
        """上传参考音频并克隆成 ``voice_id``；返回 ``{voice_id, file_id, demo_audio}``。同步接口，返回即可合成。"""

        if not _VOICE_ID_RE.fullmatch(voice_id):
            raise ProviderCommandError(f"minimax voice_id must start with a letter, be at least 8 chars and use only letters/digits/_/-: {voice_id}")
        file_id = self.upload_reference(Path(reference))
        body: dict[str, Any] = {"file_id": int(file_id) if file_id.isdigit() else file_id, "voice_id": voice_id}
        if preview_text:
            body["text"] = preview_text[:300]
            body["model"] = self._model(model)
        response = self.http("POST", self._url("/voice_clone"), headers={**self.auth_headers(), "Content-Type": "application/json"},
                             json_body=body, timeout=180.0)
        raise_for_status(response, what="minimax voice_clone")
        data = response.json()
        classify_base_resp(data, what="minimax voice_clone")
        if data.get("input_sensitive"):
            raise ProviderRejected(f"minimax voice_clone: reference audio flagged as sensitive (type {data.get('input_sensitive_type')})")
        return {"voice_id": voice_id, "file_id": file_id, "demo_audio": str(data.get("demo_audio") or "")}

    def ping(self) -> str:
        response = self.http("GET", self._url("/get_voice"), headers=self.auth_headers(), timeout=30.0)
        if response.status in {401, 403}:
            raise_for_status(response, what="minimax auth")
        return f"reachable (HTTP {response.status}), model {self.model or DEFAULT_MODEL}"


def reference_digest(path: Path) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


def build(config: Mapping[str, Any], **kw: Any) -> MiniMaxSpeech:
    return MiniMaxSpeech(config, **kw)


__all__ = ["MiniMaxSpeech", "build", "clone_voice_id", "classify_base_resp", "speed_from_rate", "reference_digest",
           "SYSTEM_VOICES", "REFERENCE_SUFFIXES", "MAX_REFERENCE_BYTES", "DEFAULT_MODEL"]
