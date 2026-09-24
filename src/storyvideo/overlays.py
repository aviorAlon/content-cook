"""交付面遮挡区（2026-09-08，融合 zenstory drama-skills 的判据，DESIGN §27）。

竖屏短视频真正给观众看的画面不是整张 9:16：平台的状态/标题栏压着顶部，点赞/评论/分享一列压着右侧，
文案与进度条压着底部。首帧里放在这些位置的可读文字（手机屏上的倒计时、合同签名栏）和烧进去的字幕，
到了平台上就被挡住。这里把"哪些位置会被挡"做成 spec 字段 + 预设 + 静态检查，不生成任何东西：

* ``delivery.overlay_preset``：``douyin`` / ``kuaishou`` / ``shipinhao`` / ``none``（预设按典型竖屏布局给的粗略比例，
  平台改版时只改这张表）。
* ``delivery.overlay_regions``：``[{label, x, y, w, h}]``，都是交付面的比例（0–1，x/y 是左上角）；写了就优先于预设。
* ``regions_for(spec)``：显式区域 → 预设区域 → 空；老项目两个字段都没有，行为逐字节不变。
* 校验（``validate``）给 ``spec.normalize_spec`` 和后期块接口共用；``position_rect`` 把可读文字的位置描述
  （「底部」「右下角」「画面中央偏上」）粗略映射成矩形，给 ``lint.check_overlays`` 用。
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

OVERLAY_KEYS = ("overlay_preset", "overlay_regions")
REGION_KEYS = ("label", "x", "y", "w", "h")

# 典型竖屏 9:16 布局的粗略比例；标签是中文（界面按语言翻）
PRESETS: dict[str, dict[str, Any]] = {
    "none": {"label": "无（不预留）", "regions": []},
    "douyin": {
        "label": "抖音（竖屏）",
        "regions": [
            {"label": "顶部状态与标题栏", "x": 0.0, "y": 0.0, "w": 1.0, "h": 0.09},
            {"label": "右侧互动栏", "x": 0.84, "y": 0.42, "w": 0.16, "h": 0.36},
            {"label": "底部文案与进度条", "x": 0.0, "y": 0.78, "w": 1.0, "h": 0.22},
        ],
    },
    "kuaishou": {
        "label": "快手（竖屏）",
        "regions": [
            {"label": "顶部导航栏", "x": 0.0, "y": 0.0, "w": 1.0, "h": 0.09},
            {"label": "右侧互动栏", "x": 0.84, "y": 0.48, "w": 0.16, "h": 0.32},
            {"label": "底部文案与评论框", "x": 0.0, "y": 0.80, "w": 1.0, "h": 0.20},
        ],
    },
    "shipinhao": {
        "label": "视频号（竖屏）",
        "regions": [
            {"label": "顶部标题栏", "x": 0.0, "y": 0.0, "w": 1.0, "h": 0.08},
            {"label": "底部文案区", "x": 0.0, "y": 0.72, "w": 0.85, "h": 0.14},
            {"label": "底部互动栏", "x": 0.0, "y": 0.86, "w": 1.0, "h": 0.14},
        ],
    },
}
PRESET_ORDER = ("none", "douyin", "kuaishou", "shipinhao")
MAX_REGIONS = 12
MIN_OVERLAP_AREA = 0.001     # 两个矩形只是擦边不算挡住


def preset_choices() -> list[dict[str, str]]:
    return [{"id": pid, "label": PRESETS[pid]["label"]} for pid in PRESET_ORDER]


def preset_regions(preset: Any) -> list[dict[str, Any]]:
    entry = PRESETS.get(str(preset or "").strip().lower())
    return [dict(r) for r in entry["regions"]] if entry else []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def clean_regions(items: Any) -> list[dict[str, Any]]:
    """合法的显式区域（宽高 > 0、都在 0–1 里、有标签）；不合法的静默跳过——lint 和预览用，校验另有 validate。"""

    out: list[dict[str, Any]] = []
    if not isinstance(items, (list, tuple)):
        return out
    for item in items:
        if not isinstance(item, Mapping):
            continue
        x, y, w, h = (_number(item.get(k)) for k in ("x", "y", "w", "h"))
        if None in (x, y, w, h) or w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > 1 + 1e-9 or y + h > 1 + 1e-9:
            continue
        out.append({"label": str(item.get("label") or "").strip() or "遮挡区", "x": x, "y": y, "w": w, "h": h})
    return out


def regions_for(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """显式区域优先，其次预设，都没有 = 空（老项目）。"""

    delivery = spec.get("delivery") if isinstance(spec, Mapping) else None
    if not isinstance(delivery, Mapping):
        return []
    explicit = clean_regions(delivery.get("overlay_regions"))
    if explicit:
        return explicit
    return preset_regions(delivery.get("overlay_preset"))


def validate(delivery: Any) -> list[str]:
    """``delivery`` 里遮挡区两个字段的形状校验，返回问题清单（空 = 通过）；别的 delivery 键不管（装配层校验）。"""

    if not isinstance(delivery, Mapping):
        return []
    problems: list[str] = []
    preset = delivery.get("overlay_preset")
    if preset is not None and preset != "":
        if not isinstance(preset, str) or preset.strip().lower() not in PRESETS:
            problems.append(f"delivery.overlay_preset 只能是 {' / '.join(PRESET_ORDER)}，现在是「{preset}」")
    regions = delivery.get("overlay_regions")
    if regions is None:
        return problems
    if not isinstance(regions, list):
        problems.append("delivery.overlay_regions 必须是数组：[{label, x, y, w, h}]，比例 0–1")
        return problems
    if len(regions) > MAX_REGIONS:
        problems.append(f"delivery.overlay_regions 最多 {MAX_REGIONS} 块，现在有 {len(regions)} 块")
    for index, item in enumerate(regions):
        prefix = f"delivery.overlay_regions[{index}]"
        if not isinstance(item, Mapping):
            problems.append(f"{prefix} 必须是对象：{{label, x, y, w, h}}")
            continue
        label = item.get("label")
        if label is not None and not isinstance(label, str):
            problems.append(f"{prefix}.label 要是文字")
        numbers: dict[str, float] = {}
        for key in ("x", "y", "w", "h"):
            number = _number(item.get(key))
            if number is None:
                problems.append(f"{prefix}.{key} 要是 0 到 1 之间的数（交付面的比例）")
            elif not 0 <= number <= 1:
                problems.append(f"{prefix}.{key} 要在 0 到 1 之间，现在是 {number:g}")
            else:
                numbers[key] = number
        for key in ("w", "h"):
            if key in numbers and numbers[key] <= 0:
                problems.append(f"{prefix}.{key} 要大于 0（宽高为 0 的区域挡不住任何东西）")
        if {"x", "w"} <= numbers.keys() and numbers["x"] + numbers["w"] > 1 + 1e-9:
            problems.append(f"{prefix} 超出交付面右边：x + w = {numbers['x'] + numbers['w']:g} > 1")
        if {"y", "h"} <= numbers.keys() and numbers["y"] + numbers["h"] > 1 + 1e-9:
            problems.append(f"{prefix} 超出交付面底边：y + h = {numbers['y'] + numbers['h']:g} > 1")
        unknown = [k for k in item if k not in REGION_KEYS]
        if unknown:
            problems.append(f"{prefix} 有不认识的字段：{'、'.join(str(k) for k in unknown)}（只认 label, x, y, w, h）")
    return problems


def payload(delivery: Mapping[str, Any] | None) -> dict[str, Any]:
    """给接口：当前预设、是否自定义、生效的区域、可选预设。"""

    delivery = delivery if isinstance(delivery, Mapping) else {}
    explicit = clean_regions(delivery.get("overlay_regions"))
    preset = str(delivery.get("overlay_preset") or "").strip().lower()
    return {
        "preset": preset if preset in PRESETS else ("none" if not preset else preset),
        "custom": bool(explicit),
        "regions": explicit or preset_regions(preset),
        "presets": preset_choices(),
    }


# ---------------------------------------------------------------- 位置描述 → 粗略矩形

_V_TOP = re.compile(r"顶部|顶端|顶|上方|上部|上沿|上边|上缘|上角|[左右]上|\btop\b|\bupper\b|\bheader\b", re.IGNORECASE)
_V_BOTTOM = re.compile(r"底部|底端|底|下方|下部|下沿|下边|下缘|下角|[左右]下|\bbottom\b|\blower\b|\bfooter\b", re.IGNORECASE)
_V_CENTER = re.compile(r"中央|中间|居中|正中|中部|中心|\bcent(?:er|re)\b|\bmiddle\b", re.IGNORECASE)
_V_UP_SHIFT = re.compile(r"偏上|靠上|upper[- ]cent(?:er|re)", re.IGNORECASE)
_V_DOWN_SHIFT = re.compile(r"偏下|靠下|lower[- ]cent(?:er|re)", re.IGNORECASE)
_V_TOP_HALF = re.compile(r"上半|top half", re.IGNORECASE)
_V_BOTTOM_HALF = re.compile(r"下半|bottom half", re.IGNORECASE)
_H_LEFT = re.compile(r"左|\bleft\b", re.IGNORECASE)
_H_RIGHT = re.compile(r"右|\bright\b", re.IGNORECASE)

Rect = tuple[float, float, float, float]   # x, y, w, h


def position_rect(position: str) -> Rect | None:
    """「底部」「右下角」「画面中央偏上」→ 粗略矩形；一个方位词都没有 → None（判断不了，不报）。
    只写了横向（「右侧」）时纵向取中段 0.2–0.8，只写了纵向时横向取 0.2–0.8——避免一句「底部」把整条右侧栏也算进去。"""

    text = str(position or "").strip()
    if not text:
        return None
    vertical: tuple[float, float] | None = None
    if _V_UP_SHIFT.search(text):
        vertical = (0.15, 0.5)
    elif _V_DOWN_SHIFT.search(text):
        vertical = (0.5, 0.85)
    elif _V_TOP_HALF.search(text):
        vertical = (0.0, 0.5)
    elif _V_BOTTOM_HALF.search(text):
        vertical = (0.5, 1.0)
    elif _V_TOP.search(text):
        vertical = (0.0, 0.3)
    elif _V_BOTTOM.search(text):
        vertical = (0.7, 1.0)
    elif _V_CENTER.search(text):
        vertical = (0.35, 0.65)
    horizontal: tuple[float, float] | None = None
    left, right = bool(_H_LEFT.search(text)), bool(_H_RIGHT.search(text))
    if left and not right:
        horizontal = (0.0, 0.3)
    elif right and not left:
        horizontal = (0.7, 1.0)
    elif _V_CENTER.search(text):
        horizontal = (0.3, 0.7)
    if vertical is None and horizontal is None:
        return None
    y0, y1 = vertical or (0.2, 0.8)
    x0, x1 = horizontal or (0.2, 0.8)
    return (x0, y0, x1 - x0, y1 - y0)


def subtitle_rect(style: Mapping[str, Any]) -> Rect:
    """字幕块的粗略位置（按 subtitles.effective_style 的位置/安全边距；双语两行更高）。
    横向取 0.12–0.84：居中两行字幕通常不碰右侧 16% 的互动栏，碰到的是顶/底的横条。"""

    height = 0.11 if str(style.get("language") or "zh") == "bilingual" else 0.07
    if str(style.get("position") or "bottom") == "top":
        y = float(style.get("top_margin_ratio") or 0.12)
    else:
        y = 1.0 - float(style.get("safe_bottom_percent") or 0) / 100.0 - height
    return (0.12, max(0.0, min(1.0 - height, y)), 0.72, height)


def overlap_area(a: Rect, b: Rect) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return w * h if w > 0 and h > 0 else 0.0


def covering(rect: Rect, regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """挡住这个矩形的遮挡区（擦边不算）。"""

    return [r for r in regions if overlap_area(rect, (r["x"], r["y"], r["w"], r["h"])) > MIN_OVERLAP_AREA]


__all__ = ["PRESETS", "PRESET_ORDER", "OVERLAY_KEYS", "REGION_KEYS", "MAX_REGIONS", "preset_choices", "preset_regions",
           "clean_regions", "regions_for", "validate", "payload", "position_rect", "subtitle_rect", "overlap_area", "covering"]
