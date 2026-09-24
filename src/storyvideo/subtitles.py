"""字幕样式：一份默认值、一套校验、可选字体清单——装配（assembly）和后期合成（post_layers）都从这里取，
起草（scriptgen）和示例的默认也用这一份，界面的「字幕样式」卡改的就是 ``spec.subtitle``。

原则：够做视频字幕就行——字体、字号、颜色、描边、位置、底色带；不做花样。``subtitle`` 是后期块
（不进任何付费请求），制作中也能改，改完重跑装配/后期合成即可。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

DEFAULT_STYLE: dict[str, Any] = {
    "language": "zh",               # zh / en / bilingual
    "position": "bottom",           # bottom / top
    "display": "speech",            # speech（只在说话时段显示）/ full_shot（整镜显示）
    "font_path": "",                # 空 = 自动找系统中文字体
    "chinese_font_size": 46,        # 以 1080 宽为基准，装配时按实际宽度缩放
    "english_font_size": 64,
    "chinese_color": "0xFFFFFF",
    "english_color": "0xFFD84D",
    "outline_color": "0x111111",
    "outline_width": 4,
    "band_alpha": 0.22,             # 字幕后面的半透明底色带，0 = 不要
    "safe_bottom_percent": 7,       # 底部安全边距（画面高度百分比）
    "top_margin_ratio": 0.12,       # position=top 时距顶
    "max_lines": 2,
    "render_mode": "programmatic_only",
}

LANGUAGES = ("zh", "en", "bilingual")
POSITIONS = ("bottom", "top")
DISPLAYS = ("speech", "full_shot")
# render_mode 是声明：programmatic_only = 程序烧字幕（默认）；none / off = 这一集声明不要字幕（forms 按它查形态卡的
# 「字幕常开」要求）。以前任何字符串都能存，写错了一律当「开」，形态检查也就看不出来
RENDER_MODES = ("programmatic_only", "none", "off")
_COLOR = re.compile(r"(?:0x|#)?([0-9A-Fa-f]{6})\Z")

# 常见的中文/通用字体，按平台列；界面的字体下拉只列真实存在的
FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("C:/Windows/Fonts/msyhbd.ttc", "微软雅黑 粗体"),
    ("C:/Windows/Fonts/msyh.ttc", "微软雅黑"),
    ("C:/Windows/Fonts/simhei.ttf", "黑体"),
    ("C:/Windows/Fonts/simsun.ttc", "宋体"),
    ("C:/Windows/Fonts/simkai.ttf", "楷体"),
    ("C:/Windows/Fonts/STXIHEI.TTF", "华文细黑"),
    ("C:/Windows/Fonts/STZHONGS.TTF", "华文中宋"),
    ("C:/Windows/Fonts/arialbd.ttf", "Arial Bold"),
    ("/System/Library/Fonts/PingFang.ttc", "苹方"),
    ("/System/Library/Fonts/STHeiti Medium.ttc", "华文黑体"),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", "宋体"),
    ("/System/Library/Fonts/Supplemental/Kaiti.ttc", "楷体"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "Noto Sans CJK Bold"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK"),
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", "Noto Serif CJK"),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "文泉驿微米黑"),
)


def normalize_color(value: Any, default: str) -> str:
    match = _COLOR.fullmatch(str(value or default).strip())
    if match is None:
        match = _COLOR.fullmatch(default)
    return "0x" + match.group(1).upper()  # type: ignore[union-attr]


def css_color(value: Any, default: str = "0xFFFFFF") -> str:
    return "#" + normalize_color(value, default)[2:]


def effective_style(subtitle: Mapping[str, Any] | None) -> dict[str, Any]:
    """spec.subtitle 合上默认值；不校验，只补齐（给装配/合成/界面预览用）。"""

    style = dict(DEFAULT_STYLE)
    for key, value in (subtitle or {}).items():
        if value is not None:
            style[key] = value
    for key, default in (("chinese_color", DEFAULT_STYLE["chinese_color"]), ("english_color", DEFAULT_STYLE["english_color"]),
                         ("outline_color", DEFAULT_STYLE["outline_color"])):
        style[key] = normalize_color(style.get(key), default)
    return style


def validate(subtitle: Any) -> list[str]:
    """界面保存前的形状校验，返回问题清单（空 = 通过）。"""

    if subtitle is None:
        return []
    if not isinstance(subtitle, Mapping):
        return ["subtitle 必须是对象"]
    problems: list[str] = []
    if "language" in subtitle and subtitle["language"] not in LANGUAGES:
        problems.append("字幕语言只能是 zh / en / bilingual")
    if "position" in subtitle and subtitle["position"] not in POSITIONS:
        problems.append("字幕位置只能是 bottom / top")
    if "display" in subtitle and subtitle["display"] not in DISPLAYS:
        problems.append("字幕显示方式只能是 speech / full_shot")
    if "render_mode" in subtitle and subtitle["render_mode"] not in RENDER_MODES:
        problems.append(f"字幕 render_mode 只能是 {' / '.join(RENDER_MODES)}，现在是「{subtitle['render_mode']}」")
    for key, lo, hi in (("chinese_font_size", 12, 300), ("english_font_size", 12, 300), ("outline_width", 0, 20),
                        ("safe_bottom_percent", 0, 40), ("max_lines", 1, 4)):
        if key in subtitle:
            try:
                number = float(subtitle[key])
            except (TypeError, ValueError):
                problems.append(f"{key} 要是数字")
                continue
            if not lo <= number <= hi:
                problems.append(f"{key} 要在 {lo} 到 {hi} 之间")
    for key in ("band_alpha", "top_margin_ratio"):
        if key in subtitle:
            try:
                number = float(subtitle[key])
            except (TypeError, ValueError):
                problems.append(f"{key} 要是数字")
                continue
            if not 0 <= number <= (1 if key == "band_alpha" else 0.7):
                problems.append(f"{key} 要在 0 到 {1 if key == 'band_alpha' else 0.7} 之间")
    for key in ("chinese_color", "english_color", "outline_color"):
        if key in subtitle and subtitle[key] not in (None, "") and _COLOR.fullmatch(str(subtitle[key]).strip()) is None:
            problems.append(f"{key} 要是 #RRGGBB 或 0xRRGGBB")
    font = subtitle.get("font_path")
    if font and not Path(str(font)).expanduser().is_file():
        problems.append(f"字体文件不存在：{font}")
    return problems


def available_fonts() -> list[dict[str, str]]:
    """本机真实存在的候选字体，给界面的字体下拉。"""

    out: list[dict[str, str]] = []
    for path, label in FONT_CANDIDATES:
        if Path(path).is_file():
            out.append({"path": path, "label": label})
    return out


def resolve_font(subtitle: Mapping[str, Any] | None, fallback: str | None = None) -> str | None:
    """spec 指定的字体优先；否则候选表里第一个存在的；再否则 fallback。返回路径或 None。"""

    configured = str((subtitle or {}).get("font_path") or (subtitle or {}).get("fontfile") or "").strip()
    if configured and Path(configured).expanduser().is_file():
        return str(Path(configured).expanduser().resolve())
    for path, _label in FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    return fallback


__all__ = ["DEFAULT_STYLE", "LANGUAGES", "POSITIONS", "DISPLAYS", "FONT_CANDIDATES", "normalize_color", "css_color",
           "effective_style", "validate", "available_fonts", "resolve_font"]
