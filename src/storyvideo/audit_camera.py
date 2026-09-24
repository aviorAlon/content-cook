#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回溯审计：提示词要求了什么运镜 → 成片里实际发生了什么。

**零生成成本**：只读磁盘上已有的镜次素材，不调用任何付费接口。

量法——边框带漂移 vs 中心区活动度：

    锁死机位  →  中心区活动高（人在动），边框带几乎不变
    相机运动  →  边框带同步大幅变化

所以 ``border/center`` 这个比值比任一单项都更能区分「相机动了」和「人物动了」。
不用 vidstabdetect：它输出逐帧局部运动向量，5 秒就 1.4MB，解析笨重且对
AI 生成素材的低纹理区域不稳。

用法：
    python -m storyvideo.audit_camera            # 审计全部剧集
    python -m storyvideo.audit_camera DEMO-1 DEMO-2
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VIDEO_ROOT = os.path.join(PROJECT_ROOT, "output", "story_video")
SPEC_ROOT = os.path.join(PROJECT_ROOT, "content", "story_video")

SAMPLES = 9          # 每镜抽帧数
WIDTH = 160          # 抽帧缩放宽度，够用且快
BORDER = 0.10        # 边框带占比

# 提示词里声明「不动」的说法
STATIC_PAT = re.compile(
    r"camera does not move|locked[- ]off|static camera|no pan, no tilt|"
    r"camera itself is completely static|机位不动|固定机位",
    re.I)
# 提示词里声明运镜的说法 → 归一化标签
MOVE_PATS = [
    ("push", r"push[- ]in|pushes in|dolly in|推近|推镜"),
    ("pull", r"pull[- ]back|pulls back|后拉|拉镜|后撤"),
    ("pan", r"\bpans?\b|摇镜|平移"),
    ("tilt", r"\btilts?\b|俯仰"),
    ("track", r"\btrack(s|ing)?\b|\btrucks?\b|跟拍|移镜"),
    ("orbit", r"\borbits?\b|环绕|circle around"),
    ("crane", r"\bcranes?\b|升降"),
    ("handheld", r"handheld|手持"),
    ("zoom", r"\bzooms?\b|变焦"),
    ("aerial", r"aerial|drone|航拍"),
]


def frames(path: str, count: int) -> list:
    """抽 count 帧，返回 PIL 灰度图列表。"""
    from PIL import Image

    dur = probe_duration(path)
    if dur <= 0:
        return []
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(count):
            t = dur * (i + 0.5) / count
            dst = os.path.join(tmp, f"f{i}.png")
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path,
                 "-frames:v", "1", "-vf", f"scale={WIDTH}:-2,format=gray", dst],
                capture_output=True)
            if os.path.exists(dst):
                out.append(Image.open(dst).copy())
    return out


def probe_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float((r.stdout or "0").strip())
    except ValueError:
        return 0.0


def motion_metrics(path: str) -> dict | None:
    """返回 border / center 平均绝对差（0-255）与比值。"""
    imgs = frames(path, SAMPLES)
    if len(imgs) < 3:
        return None
    w, h = imgs[0].size
    bw, bh = max(2, int(w * BORDER)), max(2, int(h * BORDER))

    def bands(im):
        px = im.load()
        border, center = [], []
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                edge = x < bw or x >= w - bw or y < bh or y >= h - bh
                (border if edge else center).append(px[x, y])
        return border, center

    base_b, base_c = bands(imgs[0])
    db, dc = [], []
    for im in imgs[1:]:
        b, c = bands(im)
        db.append(sum(abs(p - q) for p, q in zip(b, base_b)) / max(1, len(b)))
        dc.append(sum(abs(p - q) for p, q in zip(c, base_c)) / max(1, len(c)))
    border = sum(db) / len(db)
    center = sum(dc) / len(dc)
    return {"border": border, "center": center,
            "ratio": border / center if center > 0.5 else float("inf")}


# 否定语境：必须在匹配运镜词之前剔除，否则「no pan, no tilt, no zoom」会被
# 当成「声明了摇镜、俯仰、变焦」。第一版就是这么把好几个锁死机位的
# 镜次全判成 move 的——**否定词和肯定词用的是同一批词汇**。
NEGATION_PAT = re.compile(
    r"\b(no|not|never|without)\s+[a-z][a-z\- ]{0,24}"
    r"|(?:the )?camera does not move[^.]*\."
    r"|camera itself is completely static[^.]*\."
    r"|不推不拉不摇不移|禁[止]?[推拉摇移运]镜?",
    re.I)


def strip_negations(text: str) -> str:
    """把否定片段整段抹掉，只留真正被要求的动作。"""
    return NEGATION_PAT.sub(" ", text)


def requested(spec: dict, shot: dict) -> tuple[str, list[str]]:
    """从提示词里读出「声明了什么」。返回 (static|move|none, 标签列表)。"""
    text = " ".join(str(shot.get(k) or "") for k in ("motion", "art", "shot", "lock"))
    text += " " + str(spec.get("camera") or "") + " " + str(spec.get("video_lock") or "")
    declared_static = bool(STATIC_PAT.search(text))
    positive = strip_negations(text)
    tags = [name for name, pat in MOVE_PATS if re.search(pat, positive, re.I)]
    # 明确声明的 camera_move 字段优先
    if shot.get("camera_move"):
        return "move", [str(shot["camera_move"])]
    if declared_static and not tags:
        return "static", []
    if tags:
        return "move", tags
    return "none", []


def audit(episode_ids: list[str]) -> list[dict]:
    rows = []
    for ep in episode_ids:
        spec_path = os.path.join(SPEC_ROOT, f"{ep}.json")
        vid_dir = os.path.join(VIDEO_ROOT, ep, "02_video")
        if not (os.path.exists(spec_path) and os.path.isdir(vid_dir)):
            continue
        with open(spec_path, encoding="utf-8") as fh:
            spec = json.load(fh)
        for shot in spec.get("shots", ()):
            sid = str(shot.get("id") or "")
            path = os.path.join(vid_dir, f"{sid}.mp4")
            if not os.path.exists(path):
                continue
            m = motion_metrics(path)
            if not m:
                continue
            kind, tags = requested(spec, shot)
            rows.append({"ep": ep, "shot": sid, "declared": kind,
                         "tags": tags, **m})
            print(f"  {ep:22} {sid:4} 声明={kind:6} {','.join(tags) or '-':18} "
                  f"border={m['border']:5.1f} center={m['center']:5.1f} "
                  f"ratio={m['ratio']:.2f}", flush=True)
    return rows


def main(argv: list[str]) -> None:
    if argv:
        eps = argv
    else:
        eps = sorted(
            d for d in os.listdir(VIDEO_ROOT)
            if os.path.isdir(os.path.join(VIDEO_ROOT, d, "02_video")))
    print(f"审计 {len(eps)} 个剧集（零生成成本，只读本地素材）\n")
    rows = audit(eps)
    if not rows:
        print("没有可审计的镜次")
        return

    print(f"\n{'=' * 72}\n汇总：声明 vs 实测\n{'=' * 72}")
    for kind in ("static", "move", "none"):
        group = [r for r in rows if r["declared"] == kind]
        if not group:
            continue
        finite = [r["ratio"] for r in group if r["ratio"] != float("inf")]
        avg_ratio = sum(finite) / len(finite) if finite else float("inf")
        print(f"  声明「{kind}」{len(group):3} 镜  "
              f"border 均值 {sum(r['border'] for r in group)/len(group):5.1f}  "
              f"ratio 均值 {avg_ratio:5.2f}")

    static_rows = [r for r in rows if r["declared"] == "static"]
    if static_rows:
        static_rows.sort(key=lambda r: -r["ratio"])
        print(f"\n声明「机位不动」却漂移最大的 8 镜（ratio 越高越像相机在动）：")
        for r in static_rows[:8]:
            print(f"  {r['ep']:22} {r['shot']:4} ratio={r['ratio']:5.2f} "
                  f"border={r['border']:5.1f} center={r['center']:5.1f}")

    dest = os.path.join(VIDEO_ROOT, "_camera_audit.json")
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    print(f"\n明细 → {dest}")


if __name__ == "__main__":
    main(sys.argv[1:])
