#!/usr/bin/env python3
"""提示词金丝雀（2026-09-06，借 waoowaoo 的 prompt-canary 思路）。

编译器（prompts.py）+ 镜头库 + 风格尾句一起决定付费请求里的每个字。任何一处改动都可能悄悄改掉
所有项目的提示词，而冻结 run 的 request_hash 一变就是「job already has different request metadata」。
这里把一份固定的迷你 spec（覆盖：中文/英文、有台词/无台词、画外音、ref_frame、sudden 运镜、
入口/出口状态、beats、表演锁、视觉风格）编译成黄金输出；形态卡（form_cards.json 的运动预算句与构图句）
另外按「同一镜 × 每张卡 × 中英」各编一次视频提示词（``FORM_<卡 id>_<语言>``）。测试比对，改动必须是有意的。

用法：
    python scripts/refresh_prompt_canary.py           # 只比对，打印差异
    python scripts/refresh_prompt_canary.py --update  # 接受当前输出为新黄金（确认是有意改动后再跑）
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from storyvideo.forms import form_cards  # noqa: E402
from storyvideo.prompts import (  # noqa: E402
    compile_frame_prompt,
    compile_negative_prompt,
    compile_video_prompt,
)

CANARY_DIR = ROOT / "tests" / "canary"
SPEC_PATH = CANARY_DIR / "mini_spec.json"
EXPECTED_PATH = CANARY_DIR / "expected_prompts.json"
FORM_SHOT = "S3_LINE"          # 形态卡变体用哪一镜编（普通台词镜）


def compile_all(spec: dict) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for shot in spec["shots"]:
        sid = shot["id"]
        out[sid] = {
            "frame": compile_frame_prompt(spec, sid),
            "video": compile_video_prompt(spec, sid),
            "negative": compile_negative_prompt(spec, sid),
        }
    # 形态卡只进视频提示词：用了形态卡的项目，卡的措辞一改，这些项目的请求指纹就变了
    for card in form_cards():
        for lang in ("zh", "en"):
            variant = {**spec, "form": card["id"], "prompt_language": lang}
            out[f"FORM_{card['id']}_{lang}"] = {"video": compile_video_prompt(variant, FORM_SHOT)}
    return out


def diff(expected: dict, actual: dict) -> list[str]:
    lines: list[str] = []
    for sid in sorted(set(expected) | set(actual)):
        for kind in ("frame", "video", "negative"):
            a = (expected.get(sid) or {}).get(kind, "")
            b = (actual.get(sid) or {}).get(kind, "")
            if a != b:
                lines.append(f"--- {sid}.{kind}")
                lines.extend(difflib.unified_diff(a.split("。"), b.split("。"), lineterm="", n=1))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args(argv)
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    actual = compile_all(spec)
    if args.update or not EXPECTED_PATH.exists():
        EXPECTED_PATH.write_text(json.dumps(actual, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        print(f"wrote {EXPECTED_PATH} ({len(actual)} shots)")
        return 0
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    lines = diff(expected, actual)
    if lines:
        print("\n".join(lines))
        print(f"\n{len([x for x in lines if x.startswith('---')])} prompt(s) drifted. If intended: --update")
        return 1
    print("prompt canary: no drift")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
