"""LLM 起草分页表（契约 §6 ``jobs/draft``）。

系统提示词见 ``build_system_prompt``：输出契约 + 按页位置的写作约束 + 全书约束，篇幅数字取自 ``lint.AGE_RULES``。
调用走 ``storyvideo.scriptgen._dispatch``（设置里的文本模型 / 代理），
结果经 ``draft_to_spec`` 变成项目文件子集（页 id、kb 方向交替、结尾钩清空），再由 ``spec.normalize_book`` 把关。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from genkit.ids import id_from_name
from storyvideo.scriptgen import ScriptGenError, _dispatch

from .lint import AGE_RULES
from .spec import AGE_BANDS, BILINGUAL_MODES, KB_DIRS

STYLE_PRESETS: list[dict[str, str]] = [
    {"id": "watercolor", "name": "水彩", "name_en": "Watercolor",
     "anchor": "Soft watercolor picture-book art, rounded friendly shapes, airy pastel washes, visible paper grain, "
               "colours bleeding gently at the edges"},
    {"id": "crayon", "name": "蜡笔", "name_en": "Crayon",
     "anchor": "Children's book illustration in wax crayon, chunky hand-drawn strokes, visible crayon texture, bright but soft "
               "colours on cream paper, playful and cosy"},
    {"id": "papercut", "name": "剪纸", "name_en": "Paper cut",
     "anchor": "Layered paper-cut children's book illustration, flat coloured paper shapes with soft drop shadows, tactile "
               "cardstock texture, rounded silhouettes, warm gentle palette"},
    {"id": "flat", "name": "扁平矢量", "name_en": "Flat vector",
     "anchor": "Flat vector children's book illustration, simple geometric rounded shapes, clean edges, limited warm pastel "
               "palette, minimal shading, friendly and calm"},
]
KB_CYCLE = ("push", "left", "pull", "right", "up", "down")

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title_zh", "title_en", "education", "characters", "pages"],
    "properties": {
        "title_zh": {"type": "string"},
        "title_en": {"type": "string"},
        "education": {
            "type": "object",
            "required": ["cognition", "vocab", "emotion"],
            "properties": {"cognition": {"type": "string"}, "vocab": {"type": "array", "items": {"type": "string"}}, "emotion": {"type": "string"}},
        },
        "characters": {
            "type": "array",
            "items": {"type": "object", "required": ["id", "name", "name_en", "block"],
                      "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "name_en": {"type": "string"}, "block": {"type": "string"}}},
        },
        "scenes": {
            "type": "array",
            "items": {"type": "object", "required": ["id", "name", "block"],
                      "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "block": {"type": "string"}}},
        },
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind", "scene", "characters", "zh", "en", "hook", "education", "kb_dir"],
                "properties": {
                    "kind": {"type": "string", "enum": ["cover", "page", "back"]},
                    "scene": {"type": "string"},
                    "characters": {"type": "array", "items": {"type": "string"}},
                    "scene_ref": {"type": ["string", "null"]},
                    "zh": {"type": "string"},
                    "en": {"type": "string"},
                    "hook": {"type": "string"},
                    "education": {"type": "string"},
                    "kb_dir": {"type": "string", "enum": list(KB_DIRS)},
                },
            },
        },
    },
}

# 每个年龄段怎么写（放在提示词最后一节）：中文、副歌、英文各一句。字数与页数的数字只有一份（lint.AGE_RULES）
_BAND_GUIDE: dict[str, dict[str, str]] = {
    "2-4": {
        "zh": "一句话只讲一件事；名词和动作都挑孩子每天看得见、摸得着的东西。",
        "refrain": "可以多出现几次，孩子越熟越爱听。",
        "en": "用最常见的词，句子短到一口气念得完。",
    },
    "4-6": {
        "zh": "一页可以有两三句，句子之间能带一点因果；角色可以把自己的心情说出来。",
        "refrain": "出现三次就够，其余篇幅让情节多往前走几步。",
        "en": "以短句为主，偶尔用 and / but 把两句连起来。",
    },
    "6-8": {
        "zh": "可以写长一点、绕一点的句子；故事里可以藏一个要想一想才懂的道理，让角色做的事把它演出来，不直接讲。",
        "refrain": "不必多，情节自己的推进更要紧。",
        "en": "可以带一个 when / because 引出的从句，每页新词不超过一个。",
    },
}
_MODE_HINT = {
    "sentence_alternate": "中英逐句交替朗读（念一句中文，再念同一句的英文），所以每页 zh 和 en 的句子数要相同，一句对一句",
    "page_zh_then_en": "先念完整页中文，再念整页英文；两种语言各自通顺就好，句子数可以不一样",
    "zh_only": "只朗读中文；en 也要填（字幕和以后的英文版要用），可以写得简短",
    "en_only": "只朗读英文，en 是正文；zh 也要填（给家长看懂），可以写得简短",
}


def _page_budget(age_band: str) -> tuple[str, str]:
    """（全书页数, 正文每页中文字数）两句话，数字取自 lint.AGE_RULES。"""

    max_chars, min_chars, lo, hi = AGE_RULES.get(age_band, AGE_RULES["2-4"])
    chars = f"{min_chars}–{max_chars} 字" if min_chars else f"不超过 {max_chars} 字"
    return f"全书 {lo}–{hi} 页，封面和封底算在内", f"正文每页中文 {chars}"


def build_system_prompt(age_band: str, bilingual_mode: str) -> str:
    band = age_band if age_band in _BAND_GUIDE else "2-4"
    guide = _BAND_GUIDE[band]
    pages, chars = _page_budget(band)
    return (
        "你在写一本中英双语的有声绘本：大人或朗读音频一页一页地念，孩子边听边看插画。"
        "交付物是一张分页表（JSON，字段见 schema），程序会照着它逐页出插画、合成朗读、拼成视频。\n\n"
        "## 先填书这一级的字段\n"
        "- characters：每个角色一个小写英文 id、中文名 name、英文名 name_en；block 用一句话定下它的样子（颜色、衣服、随身带的东西），"
        "每一页的插画都照这句画，写得越具体越不走样。\n"
        "- scenes：出现两次以上的地点各写一条（id、name、一句环境描述 block），页里用 scene_ref 指向它；"
        "只出现一次的地点直接写进 scene，scene_ref 填 null。\n"
        "- education：cognition 一句话，读完孩子多知道了一样什么（比如三种颜色的名字）；vocab 列中英各 3–5 个目标词，"
        "每条写成「中文/english」；emotion 一句话，合上书时心里留下的感受。\n\n"
        "## 第一页：封面（kind=cover）\n"
        "zh / en 填中英书名；画面是主角的全身像，面朝读者。\n\n"
        "## 中间各页：正文（kind=page）\n"
        f"- {chars}。\n"
        "- scene 是插画模型的画面说明，只写看得见的东西：什么景别、什么光，谁站在哪儿、正在做的那一个动作。"
        "一页只放一个地点、一个瞬间、一个动作；两件事就拆成两页，别用「然后」「接着」连在一起。\n"
        "- 插画里不能有字：拟声词、招牌、书名都别画出来，字幕由程序另外叠上去。\n"
        "- 同一页有两个以上角色：characters 列出每一个的 id，scene 里逐个写名字和位置；"
        "不用「大家」「他们」这类总称，插画模型看到总称会自己添人。\n"
        "- kb_dir 是静态插画的缓慢运镜方向（push 推近、pull 拉远，或 left、right、up、down），前后两页不能一样。\n"
        "- hook 是这一页结尾抛出的小问题或没做完的事，翻过去才揭晓；正文页不要连着三页都没有 hook。"
        "hook 只勾起好奇、不吓人：怪物、鬼、受伤流血、黑暗里藏着东西，都不写。\n"
        "- 拟声词（呼呼、whoosh 这一类）一页最多一组。\n"
        "- education 字段写这一页落在哪个教育点上，没有就留空。\n\n"
        "## 最后一页：封底（kind=back）\n"
        "最后一页的 hook 必须是空字符串。故事在这里放下来：角色到家了、躺下了、睡着了，或者跟谁说了晚安；念完这页，孩子可以安心闭上眼睛。\n\n"
        "## 贯穿全书\n"
        f"- 篇幅：{pages}。\n"
        "- 副歌：挑一句短而好念的话，在至少三页里一字不差地出现；最后一次出现时，结果要跟前几次不一样，故事就在这里拐弯。\n"
        "- 目标词：vocab 里的每个词全书至少出现 3 次，放进副歌里最省事。\n"
        "- 拟声词：全书只用固定的几组，最多 4 组。\n"
        f"- 两种语言：{_MODE_HINT.get(bilingual_mode, _MODE_HINT['sentence_alternate'])}。"
        "en 要像英文母语的儿童书那样写，不逐字对照中文：意思一致就行，用英文孩子熟悉的说法；同一个英文句型也可以在几页里重复。\n\n"
        f"## 写给 {band} 岁\n"
        f"- 中文：{guide['zh']}\n"
        f"- 副歌：{guide['refrain']}\n"
        f"- 英文：{guide['en']}\n\n"
        "只输出 JSON，不要任何解释。"
    )


def build_user_prompt(*, direction: str, age_band: str, bilingual_mode: str, education: Mapping[str, Any] | None,
                      page_count: int | None, title: str | None) -> str:
    lo, hi = AGE_RULES.get(age_band, AGE_RULES["2-4"])[2:4]
    pages = f"总页数 {page_count} 页（含封面封底）" if page_count else f"总页数 {lo}–{hi} 页（含封面封底）"
    parts = [f"故事方向：{direction.strip()}", f"年龄段：{age_band} 岁", f"双语策略：{bilingual_mode}", pages]
    if title:
        parts.append(f"书名（中文）：{title.strip()}")
    edu = education or {}
    if edu.get("cognition"):
        parts.append(f"认知点：{edu['cognition']}")
    if edu.get("vocab"):
        parts.append("目标词：" + "、".join(str(v) for v in edu["vocab"]))
    if edu.get("emotion"):
        parts.append(f"情感点：{edu['emotion']}")
    return "\n".join(parts)


def _clean_id(raw: Any, taken: list[str], prefix: str) -> str:
    return id_from_name(str(raw or ""), taken, prefix=prefix)


def draft_to_spec(draft: Mapping[str, Any], *, age_band: str, bilingual_mode: str, style_anchor: str | None = None,
                  education: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """模型输出 → 项目文件子集（没有 id）。页 id、kind、kb 交替、结尾钩、角色引用在这里收拾干净。"""

    if not isinstance(draft, Mapping) or not isinstance(draft.get("pages"), list) or not draft["pages"]:
        raise ScriptGenError("模型没有给出分页表（pages 为空）")
    characters: dict[str, dict[str, Any]] = {}
    id_map: dict[str, str] = {}
    for member in draft.get("characters") or ():
        if not isinstance(member, Mapping):
            continue
        raw_id = str(member.get("id") or member.get("name_en") or member.get("name") or "")
        cid = _clean_id(raw_id, list(characters), "char")
        id_map[raw_id] = cid
        id_map[str(member.get("name") or "")] = cid
        characters[cid] = {"name": str(member.get("name") or cid), "name_en": str(member.get("name_en") or ""),
                           "block": str(member.get("block") or "").strip() or str(member.get("name") or cid), "asset": None}
    scenes: dict[str, dict[str, Any]] = {}
    scene_map: dict[str, str] = {}
    for scene in draft.get("scenes") or ():
        if not isinstance(scene, Mapping) or not str(scene.get("block") or "").strip():
            continue
        raw_id = str(scene.get("id") or scene.get("name") or "")
        sid = _clean_id(raw_id, list(scenes), "scene")
        scene_map[raw_id] = sid
        scene_map[str(scene.get("name") or "")] = sid
        scenes[sid] = {"name": str(scene.get("name") or sid), "block": str(scene["block"]).strip(), "asset": None}
    pages: list[dict[str, Any]] = []
    raw_pages = [p for p in draft["pages"] if isinstance(p, Mapping) and str(p.get("scene") or "").strip()]
    previous_dir = None
    for index, page in enumerate(raw_pages):
        kind = "cover" if index == 0 else ("back" if index == len(raw_pages) - 1 else "page")
        cast = []
        for ref in page.get("characters") or ():
            cid = id_map.get(str(ref)) or (str(ref) if str(ref) in characters else None)
            if cid and cid not in cast:
                cast.append(cid)
        direction = str(page.get("kb_dir") or "").strip()
        if direction not in KB_DIRS or direction == previous_dir:
            direction = next(d for d in KB_CYCLE if d != previous_dir)
        previous_dir = direction
        scene_ref = page.get("scene_ref")
        scene_ref = scene_map.get(str(scene_ref)) if scene_ref else None
        pages.append({
            "id": f"P{index + 1:02d}", "kind": kind, "scene": str(page.get("scene") or "").strip(), "characters": cast,
            "scene_ref": scene_ref if scene_ref in scenes else None,
            "zh": str(page.get("zh") or "").strip(), "en": str(page.get("en") or "").strip(),
            "hook": "" if kind == "back" else str(page.get("hook") or "").strip(),
            "education": str(page.get("education") or "").strip(),
            "kb": {"dir": direction, "scale": None}, "flip": "cut" if kind == "back" else "page_turn",
        })
    if len(pages) >= 2:
        pages[-2]["flip"] = "page_turn"
    edu_raw = draft.get("education") if isinstance(draft.get("education"), Mapping) else {}
    edu = dict(education or {})
    for key in ("cognition", "vocab", "emotion"):
        if not edu.get(key) and edu_raw.get(key):
            edu[key] = edu_raw[key]
    out: dict[str, Any] = {
        "title_zh": str(draft.get("title_zh") or "").strip(), "title_en": str(draft.get("title_en") or "").strip(),
        "age_band": age_band if age_band in AGE_BANDS else "2-4",
        "bilingual_mode": bilingual_mode if bilingual_mode in BILINGUAL_MODES else "sentence_alternate",
        "education": {"cognition": str(edu.get("cognition") or ""), "vocab": [str(v) for v in (edu.get("vocab") or [])], "emotion": str(edu.get("emotion") or "")},
        "characters": characters, "scenes": scenes, "pages": pages,
    }
    if style_anchor:
        out["style_anchor"] = style_anchor
    return out


def style_anchor_for(style: str | None) -> str | None:
    """预设 id / 中文名 → 风格锚；不是预设就当用户自己写的锚原样用。"""

    text = str(style or "").strip()
    if not text:
        return None
    for preset in STYLE_PRESETS:
        if text in (preset["id"], preset["name"], preset["name_en"]):
            return preset["anchor"]
    return text


def generate_book_draft(*, direction: str, age_band: str, bilingual_mode: str, education: Mapping[str, Any] | None = None,
                        page_count: int | None = None, style: str | None = None, title: str | None = None,
                        dispatch: Callable[..., tuple[dict[str, Any], float | None, int | None, str]] | None = None) -> dict[str, Any]:
    """返回 ``{spec, cost_usd, tokens, backend}``；spec 是项目文件子集（没有 id）。失败抛 ScriptGenError。"""

    if not str(direction or "").strip():
        raise ScriptGenError("故事方向不能为空")
    if age_band not in AGE_BANDS:
        raise ScriptGenError(f"年龄段只能是 {' / '.join(AGE_BANDS)}")
    if bilingual_mode not in BILINGUAL_MODES:
        raise ScriptGenError(f"双语策略只能是 {' / '.join(BILINGUAL_MODES)}")
    call = dispatch or _dispatch
    system_prompt = build_system_prompt(age_band, bilingual_mode)
    user_prompt = build_user_prompt(direction=direction, age_band=age_band, bilingual_mode=bilingual_mode, education=education,
                                    page_count=page_count, title=title)
    structured, cost_usd, tokens, backend = call(system_prompt, user_prompt, json_schema=DRAFT_SCHEMA)
    if isinstance(structured, str):
        try:
            structured = json.loads(structured)
        except json.JSONDecodeError as exc:
            raise ScriptGenError(f"模型返回的不是 JSON：{exc}") from exc
    spec = draft_to_spec(structured, age_band=age_band, bilingual_mode=bilingual_mode, style_anchor=style_anchor_for(style), education=education)
    return {"spec": spec, "cost_usd": cost_usd, "tokens": tokens, "backend": backend}


__all__ = ["STYLE_PRESETS", "DRAFT_SCHEMA", "KB_CYCLE", "build_system_prompt", "build_user_prompt", "draft_to_spec", "style_anchor_for",
           "generate_book_draft", "ScriptGenError"]
