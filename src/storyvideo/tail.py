"""尾帧续接（2026-09-08，DESIGN §30，融合 drama-skills「参考槽位用途」+ 我们自己的尾帧接力经验，这里是运行时实现）。

``shot.ref_tail = {"shot": "S1", "mode": "reference" | "direct"}``（裸字符串 = reference）：
源镜的**视频**过检之后，用 ffmpeg ``-sseof -0.15`` 抽它的最后一帧到 ``<output_root>/04_tail/<源镜>.png``；

* ``reference``：这张 PNG 当本镜首帧请求的**第一个参考槽位**（在角色定妆之前；同时写了 ref_frame 时取代它）；
* ``direct``：这张 PNG 直接就是本镜的首帧（拷成首帧产物，不出图、不花钱，账本记 ``source: tail:<源镜>``）。

源镜视频还没过检时，本镜的首帧（和视频）在这一轮**推迟**——预估 / 状态里给出原因「等 S1 视频过检后抽尾帧」，
下一轮自动接上。这里只放纯函数与 ffmpeg 抽帧；调度在 runtime.py。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Mapping

TAIL_MODES = ("reference", "direct")
TAIL_DIR = "04_tail"
TAIL_SEEK_SECONDS = 0.15          # -sseof -0.15：最后一帧之前一点点，避开末尾的黑场/编码残帧
TAIL_MODE_LABELS = {"reference": "作参考图", "direct": "直接当首帧"}


def tail_ref(shot: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """``(源镜 id, mode)``；没写 / 形状不对返回 None（spec 校验另有把关，这里只求不崩）。"""

    if not isinstance(shot, Mapping):
        return None
    raw = shot.get("ref_tail")
    if isinstance(raw, str):
        source = raw.strip()
        return (source, "reference") if source else None
    if isinstance(raw, Mapping):
        source = str(raw.get("shot") or "").strip()
        mode = str(raw.get("mode") or "reference").strip().lower() or "reference"
        if not source or mode not in TAIL_MODES:
            return None
        return source, mode
    return None


def tail_png(output_root: str | Path, source_shot: str) -> Path:
    return Path(output_root) / TAIL_DIR / f"{source_shot}.png"


def tail_sidecar(output_root: str | Path, source_shot: str) -> Path:
    """抽帧记录：源视频的 sha256 与尺寸——源视频重拍后据此重抽。"""

    return Path(output_root) / TAIL_DIR / f"{source_shot}.json"


def pending_reason(source_shot: str) -> str:
    return f"等 {source_shot} 视频过检后抽尾帧"


def source_video_accepted(qc: Mapping[str, Any] | None, source_shot: str) -> bool:
    """源镜视频过检 = videos 闸里这一项技术检查通过、裁决 pass / waive、且没被标 stale。"""

    if not isinstance(qc, Mapping):
        return False
    items = ((qc.get("gates") or {}).get("videos") or {}).get("items") or {}
    item = items.get(source_shot) if isinstance(items, Mapping) else None
    if not isinstance(item, Mapping):
        return False
    return (
        item.get("technical_passed") is True
        and item.get("stale") is not True
        and str(item.get("decision") or "") in {"pass", "waive"}
    )


def tail_shots(spec: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    """所有声明了 ref_tail 的镜：``{镜 id: (源镜, mode)}``，按镜次顺序。"""

    out: dict[str, tuple[str, str]] = {}
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        ref = tail_ref(shot)
        if ref is not None:
            out[str(shot.get("id"))] = ref
    return out


def tail_gated(spec: Mapping[str, Any], qc: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    """这一轮要推迟的镜：源镜视频还没过检的 ref_tail 镜。``{镜 id: {source, mode, reason}}``。"""

    out: dict[str, dict[str, str]] = {}
    for shot_id, (source, mode) in tail_shots(spec).items():
        if not source_video_accepted(qc, source):
            out[shot_id] = {"source": source, "mode": mode, "reason": pending_reason(source)}
    return out


def tail_dependents(spec: Mapping[str, Any], sources: set[str]) -> set[str]:
    """以这些镜为尾帧来源的镜（一层；调用方按需迭代）。"""

    return {shot_id for shot_id, (source, _mode) in tail_shots(spec).items() if source in sources}


def extract_tail_frame(
    video: str | Path,
    destination: str | Path,
    *,
    ffmpeg: str = "ffmpeg",
    size: tuple[int, int] | None = None,
    seek_seconds: float = TAIL_SEEK_SECONDS,
    timeout: float = 120.0,
) -> Path:
    """``ffmpeg -sseof -0.15 -i 视频 -frames:v 1 尾帧.png``；``size`` 给了就缩放到交付/首帧尺寸。

    失败抛 ``RuntimeError``（调用方包成自己的错误类型）。写到临时名再改名，不留半张图。
    """

    source = Path(video)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.stem}.partial{target.suffix}")
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-sseof", f"-{seek_seconds:g}", "-i", str(source), "-frames:v", "1", "-update", "1"]
    if size:
        width, height = int(size[0]), int(size[1])
        command += ["-vf", f"scale={width}:{height}:flags=lanczos"]
    command.append(str(partial))
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=timeout, check=False, shell=False)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"抽尾帧失败（ffmpeg）：{exc}") from exc
    if completed.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        detail = (completed.stderr or completed.stdout or "ffmpeg failed").strip()[-600:]
        raise RuntimeError(f"抽尾帧失败（ffmpeg）：{detail}")
    partial.replace(target)
    return target


__all__ = [
    "TAIL_DIR", "TAIL_MODES", "TAIL_MODE_LABELS", "TAIL_SEEK_SECONDS",
    "tail_ref", "tail_png", "tail_sidecar", "pending_reason", "source_video_accepted",
    "tail_shots", "tail_gated", "tail_dependents", "extract_tail_frame",
]
