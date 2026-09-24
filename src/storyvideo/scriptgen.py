"""剧本/分镜起草：把一段「大致方向」交给文本生成，产出一份未落盘的 spec.json
草稿（cast + shots + 全局提示词字段），人在 UI 里看过、改过、确认过之后才会
真的存成新项目。

文本模型按这个顺序选（``scriptgen_backend``，``SCRIPTGEN_BACKEND`` 可强制指定）：

- ``settings``：「模型设置」里配好了文本角色，就用它（优先）。
- ``openai_proxy``：没配文本角色、但 .env 里有 ``SCRIPTGEN_LLM_BASE_URL``/``SCRIPTGEN_LLM_API_KEY``，
  就直接打这个 OpenAI 兼容的 ``/chat/completions`` 接口。
- ``unconfigured``：两样都没有，直接报错，让人去「模型设置」里配。

这一步本身也会真花钱/真耗用量，所以刻意不做「自动重试/自动多次生成挑最好的」
这类会偷偷多花钱的智能——每次点按钮就是一次调用，调用几次由人决定。

跟镜头库/时长线判共用同一套校验（``storyvideo.prompts``/``storyvideo.preview``），
草稿产出的 camera_move/camera_combo 只能是白名单里的真实 id——系统提示词里
把白名单原样列给模型，而不是靠事后校验失败了再重试，减少无谓的重复调用。
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

import requests

from .prompts import camera_library, find_camera_profile

from genkit.paths import code_root as _code_root, data_root as _data_root

PROJECT_ROOT = _data_root()
STYLE_LIBRARY_PATH = (
    _code_root() / "skills" / "ai-short-film" / "scripts" / "style_library.json"
)
# 题材卡（DESIGN §26）：从 drama-skills 的 12 张题材卡蒸馏，给起草模型定压力来源/场面颗粒/集尾钩子。
GENRE_CARDS_PATH = (
    _code_root() / "skills" / "ai-short-film" / "scripts" / "genre_cards.json"
)

# 起草成本上限的默认值（美元，SCRIPTGEN_MAX_BUDGET_USD 可覆盖），由 scriptgen_max_budget_usd() 读取。
# 注意：目前没有调用方按它拦截请求，它只是 DESIGN §45 / docs/local-models-spec.md 约定好的取值接口。
DEFAULT_MAX_BUDGET_USD = "2.0"
# 文本模型单次请求的超时（秒）：「模型设置」里的文本角色和 .env 代理共用，SCRIPTGEN_PROXY_TIMEOUT_SECONDS 可覆盖；
# 真卡住了多半是接口本身有问题，不该一直等。
DEFAULT_PROXY_TIMEOUT_SECONDS = 180


class ScriptGenError(Exception):
    """调用失败，或没有按要求给出结构化草稿。"""


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    return _read_dotenv(PROJECT_ROOT / ".env").get(name) or None


def text_role_is_local() -> bool:
    """剧本文本模型配在本机吗（Ollama / LM Studio / vLLM / llama.cpp，或任何指到 127.0.0.1 的兼容接口）。
    设置读不出来就按「不是本地」——宁可保留成本闸，也不能把付费来源当免费的放行。"""

    from genkit.local import role_local

    return role_local("text")


def scriptgen_max_budget_usd() -> str:
    """起草的成本上限（美元）。本地文本模型不花钱，这条闸对它不设限——返回空串表示「不设上限」
    （DESIGN §45）。调用方判断真假即可，不要直接 float()。"""

    if text_role_is_local():
        return ""
    return _env("SCRIPTGEN_MAX_BUDGET_USD") or DEFAULT_MAX_BUDGET_USD


def llm_proxy_base_url() -> str | None:
    return _env("SCRIPTGEN_LLM_BASE_URL")


def llm_proxy_api_key() -> str | None:
    return _env("SCRIPTGEN_LLM_API_KEY")


def llm_proxy_model() -> str | None:
    return _env("SCRIPTGEN_LLM_MODEL")


def llm_proxy_timeout_seconds() -> int:
    try:
        return int(_env("SCRIPTGEN_PROXY_TIMEOUT_SECONDS") or DEFAULT_PROXY_TIMEOUT_SECONDS)
    except ValueError:
        return DEFAULT_PROXY_TIMEOUT_SECONDS


def scriptgen_backend() -> str:
    """``settings``（模型设置里的文本角色，优先）→ ``openai_proxy``（.env 代理）→ ``unconfigured``。

    可用 ``SCRIPTGEN_BACKEND`` 强制指定。
    """

    forced = _env("SCRIPTGEN_BACKEND")
    if forced:
        return forced
    try:
        from genkit import settings as _settings

        if not _settings.validate_role(_settings.load_settings(), "text"):
            return "settings"
    except Exception:  # noqa: BLE001 - 设置文件坏了也不该让起草功能整个消失
        pass
    if llm_proxy_base_url() and llm_proxy_api_key():
        return "openai_proxy"
    return "unconfigured"


def text_model_available() -> bool:
    """起草 / 审稿之前的闸门：有没有能用的文本模型（设置里的文本角色，或 .env 代理）。

    ``scriptgen_backend()`` 返回的是三态字符串，没配时是 ``"unconfigured"``——**非空**，``bool()`` 恒真。播客与
    剧本工作台曾写成 ``bool(scriptgen_backend())``，文档承诺的「没配文本模型 400」从写下第一天起就没生效过
    （2026-09-22 审计 PC-01 / SC-1）；所有路由都改问这里，别再抄错。
    """

    return scriptgen_backend() != "unconfigured"


_LINE_PROPS = {
    "who": {"type": "string", "description": "说话人在 cast 里的 id；旁白用 narrator"},
    "zh": {"type": "string", "description": "这一句台词/旁白的中文文本"},
    "subtitle_start_seconds": {"type": "number"},
    "subtitle_end_seconds": {"type": "number"},
}

DRAFT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "title_zh", "logline", "frame_style", "scene_anchor", "camera", "neg", "cast", "shots",
    ],
    "properties": {
        "title_zh": {"type": "string"},
        "title_en": {"type": "string"},
        "logline": {"type": "string", "description": "一到三句话的故事梗概"},
        "frame_style": {
            "type": "string",
            "description": "全片统一的画面风格描述，会拼进每一张首帧提示词的开头",
        },
        "scene_anchor": {
            "type": "string",
            "description": "构图/人物站位/画面下方留白等全局规则，会拼进每一张首帧提示词",
        },
        "camera": {
            "type": "string",
            "description": "整体运镜/景别哲学的一段散文描述，会拼进每一张首帧提示词",
        },
        "neg": {"type": "string", "description": "全局负向提示词，逗号分隔的短语"},
        "video_lock": {"type": "string", "description": "视频锁定角色/环境一致性的规则，留空用系统默认"},
        "video_tail_lock": {"type": "string", "description": "视频收尾风格锁定，留空用系统默认"},
        # 单集契约（DESIGN §32）：写镜之前先立的承诺，不进提示词，事后由 lint 核对正文有没有兑现
        "episode_contract": {
            "type": "object",
            "description": "单集契约：本集的承诺。写成可核对的事实，不写气氛词",
            "properties": {
                "premise": {"type": "string", "description": "一句话前提：进入状态 + 谁现在要什么"},
                "hook": {"type": "string", "description": "开场钩子：观众能立即理解的未稳定局面，前两镜就要看得见"},
                "cliffhanger": {"type": "string", "description": "集尾出去的压力：某人已经决定做什么 / 某个后果已经启动，落在最后一镜"},
                "must_pay_off": {"type": "array", "items": {"type": "string"}, "description": "本集自己必须兑现的局部结果（每条一件事）"},
                "carry_over": {"type": "array", "items": {"type": "string"}, "description": "留给下一集的未决事实：谁知道了什么、谁拿着什么、哪个危险已经开始"},
            },
        },
        "cast": {
            "type": "object",
            "description": "角色/场景/道具的 id -> 定义，id 用简短英文 slug",
            "additionalProperties": {
                "type": "object",
                "required": ["block"],
                "properties": {
                    "anchor_prompt": {
                        "type": "string",
                        "description": "首次定形该角色/场景用的完整独立提示词（turnaround 或环境 plate）",
                    },
                    "block": {
                        "type": "string",
                        "description": "后续每一镜引用该角色/场景时复用的简短一致性描述",
                    },
                },
            },
        },
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "dur", "cast", "shot", "art", "motion", "lines"],
                "properties": {
                    "id": {"type": "string", "description": "简短英文 slug，如 S01_XXX"},
                    # 故意不在 schema 层加 minimum/maximum：硬约束会让信息量大的镜次在这个字段上
                    # 反复重试，反而更慢、更容易超时。范围要求放在系统提示词里当软指导，越界了就走
                    # 「返回草稿+标红警告，人手改一个数字」这条路，不指望 schema 硬卡替我们把关。
                    "dur": {"type": "integer", "description": "整数秒，须让本镜台词字数/4.5 ≤ dur"},
                    "cast": {"type": "array", "items": {"type": "string"}, "description": "本镜出现的 cast id"},
                    "shot": {"type": "string", "description": "镜头/构图/机位描述"},
                    "art": {"type": "string", "description": "画面美术/光线/氛围描述，供首帧生成用"},
                    "motion": {
                        "type": "string",
                        "description": "本镜动作+台词的时间线描述，供视频生成用；台词要原文引用一次",
                    },
                    "lines": {"type": "array", "items": {"type": "object", "properties": _LINE_PROPS}},
                    "camera_move": {"type": "string", "description": "从系统提示词的白名单里选一个 id；不用就留空"},
                    "camera_combo": {"type": "string", "description": "从白名单选一个组合 id；与 camera_move 二选一"},
                    "neg_extra": {"type": "string", "description": "本镜专属负向提示词，补充不覆盖全局 neg"},
                    "sfx": {"type": "string", "description": "本镜音效，独立于 motion 散文之外"},
                    "ref_frame": {"type": "string", "description": "若本镜首帧要以另一镜首帧为参考图，填那一镜 id"},
                },
            },
        },
    },
}


@functools.lru_cache(maxsize=1)
def style_library() -> dict[str, Any]:
    """风格/模板骨架库（neg / frame_style / scene_anchor 三块该覆盖什么的经验清单）。

    没有这个文件时不报错，只是起草时少了风格指导——不该因为一个可选的质量
    增强文件缺失就整个功能打不开。数据与用法说明见 skills/ai-short-film/scripts/
    style_library.json 的 ``_note`` / ``_usage``。
    """

    if not STYLE_LIBRARY_PATH.is_file():
        return {}
    with open(STYLE_LIBRARY_PATH, encoding="utf-8") as handle:
        return json.load(handle)


@functools.lru_cache(maxsize=1)
def genre_cards() -> list[dict[str, Any]]:
    """题材卡列表（skills/ai-short-film/scripts/genre_cards.json 的 ``cards``）。

    跟风格库一样是可选的质量增强：文件缺了只是起草时没有题材指导，不该让
    整个起草功能打不开。只保留同时有 id 和 name 的卡。
    """

    if not GENRE_CARDS_PATH.is_file():
        return []
    with open(GENRE_CARDS_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    cards = data.get("cards") if isinstance(data, dict) else data
    return [c for c in (cards or ()) if isinstance(c, dict) and c.get("id") and c.get("name")]


def find_genre(name_or_id: str | None) -> dict[str, Any] | None:
    """按 id（不分大小写）、中文名或别名找题材卡；找不到返回 None。"""

    key = str(name_or_id or "").strip()
    if not key:
        return None
    low = key.lower()
    for card in genre_cards():
        if low == str(card["id"]).lower() or key == card["name"] or key in (card.get("aliases") or ()):
            return card
    return None


def _genre_section(genre: str | None) -> str:
    """把一张题材卡压成系统提示词里的一段紧凑要点。没给题材返回空串；

    给了但找不到直接 ValueError——用户明确选了一个题材，静默忽略等于骗他
    「按这个题材写了」。
    """

    if genre is None or not str(genre).strip():
        return ""
    card = find_genre(genre)
    if card is None:
        raise ValueError(f"不认识的题材：{genre}")
    phases = card.get("phases") or {}
    lines = [
        f"题材卡：{card['name']}",
        f"- 核心：{card.get('core', '')}",
        f"- 压力来源：{card.get('pressure', '')}",
        f"- 人物策略与信息权限：{card.get('strategies', '')}",
        f"- 观众回报：{card.get('payoff', '')}",
        f"- 场面颗粒：{'、'.join(card.get('grains') or ())}",
        f"- 集尾钩子：{'；'.join(card.get('hooks') or ())}",
        f"- 前期：{phases.get('early', '')}",
        f"- 中期：{phases.get('mid', '')}",
        f"- 后期：{phases.get('late', '')}",
        f"- 常见坑：{'；'.join(card.get('pitfalls') or ())}",
    ]
    drift = card.get("disallowed_drift")
    if drift:
        lines.append(f"- 不能漂成：{'；'.join(drift)}")
    lines.append(
        "按这张卡定压力来源、场面颗粒和集尾钩子；不要漂移到别的题材；"
        "卡里的内容不出现在交付物里，只用来指导写作"
    )
    return "\n".join(lines) + "\n"


def _style_guidance_text(camera_profile: str) -> str:
    library = style_library()
    if not library:
        return ""
    profile_key = camera_profile if camera_profile in library.get("profiles", ()) else "none"
    skeleton = library.get("frame_style_skeletons", {}).get(profile_key)
    checklist = library.get("scene_anchor_checklists", {}).get(profile_key)
    neg_pool = library.get("neg_pool", ())

    parts = ["风格/模板骨架参考（结构指导，不是照抄的模板）："]
    if skeleton:
        structure = "、".join(skeleton.get("structure_cn", ()))
        parts.append(f"- frame_style 建议覆盖：{structure}")
        guardrail = skeleton.get("guardrail_cn") or skeleton.get("guardrail_en")
        if guardrail:
            parts.append(f"- frame_style 收尾可复用这句防伪写实警戒语：{guardrail}")
    if checklist:
        must = [c["cn"] for c in checklist if c.get("universal")]
        optional = [c["cn"] for c in checklist if not c.get("universal")]
        if must:
            parts.append(f"- scene_anchor 必须覆盖：{'；'.join(must)}")
        if optional:
            parts.append(f"- scene_anchor 视故事需要覆盖：{'；'.join(optional)}")
    always_terms = [
        n["cn"] for n in neg_pool if n.get("tier") in ("universal", "near_universal", "common")
    ]
    genre_terms = [n for n in neg_pool if n.get("tier") == "genre_dependent"]
    if always_terms:
        parts.append(f"- neg 默认都带上这些（除非故事明确不需要）：{'、'.join(always_terms)}")
    for n in genre_terms:
        parts.append(f"- neg 视基调决定要不要加「{n['cn']}」：{n.get('note', '')}")
    return "\n".join(parts) + "\n"


def _camera_allowlist_text(camera_profile: str) -> str:
    library = camera_library()
    # profiles → legacy_profiles：旧项目续写/重起草仍能拿到它原来那套白名单；报错时只列当前产线
    profile = find_camera_profile(camera_profile, library)
    if profile is None:
        known = ", ".join(sorted(library.get("profiles", {}).keys()))
        raise ScriptGenError(f"未知的 camera_profile: {camera_profile!r}，已知：{known}")
    shots_by_id = {s["id"]: s for s in library.get("shots", ())}
    combos_by_id = {c["id"]: c for c in library.get("combos_within_shot", ())}
    move_lines = [
        f"- {mid}（{shots_by_id[mid]['cn']}）" for mid in (profile.get("allow") or ()) if mid in shots_by_id
    ]
    combo_lines = [
        f"- {cid}（{combos_by_id[cid]['cn']}）" for cid in (profile.get("combos") or ()) if cid in combos_by_id
    ]
    cap = profile.get("max_moves_per_episode")
    parts = [f"camera_profile={camera_profile!r} 的运镜白名单（camera_move 只能从这里选，不用就留空）："]
    parts.extend(move_lines or ["（本 profile 无单镜运镜可选）"])
    parts.append("camera_combo 白名单（与 camera_move 二选一，同样只能从这里选）：")
    parts.extend(combo_lines or ["（本 profile 无组合运镜可选）"])
    if isinstance(cap, int):
        parts.append(f"全集运镜镜次上限：{cap} 镜——运镜是重音，不是每镜都要有。")
    return "\n".join(parts)


# 单集契约（DESIGN §32）：先立承诺再写镜。它不进任何生成提示词，只用来事后核对正文（lint.check_contract）。
CONTRACT_RULE = (
    "12. 先立单集契约（episode_contract）再写镜：premise 一句话前提（进入状态 + 谁现在要什么）；hook 是开场"
    "钩子——观众能立即理解的未稳定局面（不是一句神秘台词），前两镜就要看得见；cliffhanger 是集尾出去的压力——"
    "写成「某人已经决定做什么 / 某个后果已经启动」，落在最后一镜；must_pay_off 列本集自己必须兑现的局部结果"
    "（开放结尾不等于没结果）；carry_over 列留给下一集的未决事实（谁知道了什么、谁拿着什么、哪个危险已经开始）。"
    "五项都写成可核对的事实，不写「关系更紧张」这类气氛词；正文要真的兑现契约里的承诺。"
)


def _form_section(form: str | None) -> str:
    """把一张形态卡压成系统提示词里的一段（DESIGN §31）：默认值 + 必带项 + 运动预算。没给形态返回空串；

    给了但找不到直接 ValueError——同题材卡，用户明确选了，静默忽略等于骗他。
    """

    if form is None or not str(form).strip():
        return ""
    from .forms import find_form

    card = find_form(form)
    if card is None:
        raise ValueError(f"不认识的形态：{form}")
    d = card.get("defaults") or {}
    span = list(d.get("shot_seconds") or ())
    lines = [f"形态卡：{card['name']}"]
    lines.append(f"- 画幅与分辨率：{d.get('aspect_ratio', '')} {d.get('resolution', '')}".rstrip())
    if len(span) == 2:
        rhythm = f"- 单镜时长：{span[0]:g}–{span[1]:g} 秒"
        if d.get("max_shots_per_minute"):
            rhythm += f"，每分钟不超过 {d['max_shots_per_minute']} 镜"
        lines.append(rhythm)
    if d.get("hook_seconds"):
        lines.append(f"- 钩子窗口：前 {d['hook_seconds']:g} 秒内必须有开口的台词或看得见的动作节拍")
    lines.append("- 字幕：" + ("常开（后期烧录，正文里不要写字幕）" if d.get("subtitles") else "可选（不烧字幕时台词要靠表演能听懂）"))
    must = [str(m).strip() for m in (card.get("must_have") or ()) if str(m).strip()]
    if must:
        lines.append("- 必带项：" + "；".join(must))
    if card.get("motion_budget"):
        lines.append(f"- 运动预算：{card['motion_budget']}")
    lines.append(
        "按这张卡定画幅、镜次节奏和必带项；运动预算句会自动进每一镜的视频提示词，motion 里不用重复写；"
        "卡里的内容不出现在交付物里，只用来指导写作。"
    )
    return "\n".join(lines)


def build_system_prompt(
    *, camera_profile: str, prompt_language: str, genre: str | None = None, form: str | None = None
) -> str:
    """``genre`` 是题材卡的 id/中文名/别名；给了就在末尾追加「题材卡」一段；``form`` 是形态卡的 id/名/别名，

    给了就再追加「形态卡」一段（在题材卡之后）。都不给时输出跟以前逐字节一样（提示词金丝雀和已有项目的
    request_hash 不受影响）。
    """

    lang_rule = (
        "本项目使用中文提示词：frame_style/scene_anchor/camera/neg/"
        "video_lock/video_tail_lock、cast 里的 anchor_prompt/block、shot 里的 shot/art/motion/"
        "neg_extra/sfx 全部用中文自然语言撰写。台词本来就是中文，不用另外翻译。"
        if prompt_language.startswith("zh")
        else "本项目沿用英文提示词：以上所有字段全部用英文撰写；lines.zh 仍然是给字幕用的中文台词原文。"
    )
    genre_section = _genre_section(genre)
    base = (
        "你是一部竖屏短剧的分镜/提示词编剧。你的输出必须严格符合给定的 JSON Schema，"
        "只返回这一份结构化数据，不要输出别的文字。\n\n"
        "硬规则（都是实际生产里踩过的坑、写进规范里的教训，不是空泛的建议）：\n"
        "1. 时长要按字数实算，不能按段落感觉估：中文台词按每秒 4.5 字换算，"
        "本镜所有台词字数之和 / 4.5 必须 ≤ 这一镜的 dur；旁白/纯环境镜头自行判断合理秒数。"
        "另外 dur 必须是 4 到 15 之间的整数（含 4 和 15）——这是当前默认视频模型的硬限制，"
        "超出这个范围的镜次会在校验时被拒绝。信息量大、需要更长时间交代的内容，"
        "拆成多个 4-15 秒的镜次，不要指望单镜超时长。\n"
        "2. 生成模型画不出可读文字：画面描述里不要要求出现任何可读的字、数字、logo、"
        "水印、招牌刻字——凡是要给观众看的文字/数字，一律留白，交给后期程序化叠加。\n"
        "3. 负向词只能压制细节，压不住整个物体的类别：如果某样东西容易被模型理解成"
        "别的东西（比如会被画成真实品牌车标、会被画成小孩、会被画成实心不透明），"
        "不要指望靠 neg_extra 堵住，直接把那个物体本身的材质/形状/颜色/年龄写具体、写死。\n"
        "4. 程度词模型不吃：不要写「更透明」「更小声」这种相对形容词，要写成可检查的"
        "物理事实，比如「窗外的路灯必须透过半透明的窗帘清晰可见」。\n"
        "5. 通用词会被字面理解：避免「孩子」「车」「房子」这类通称，写清楚具体年龄/"
        "型号/材质，否则可能被理解成幼儿、真实品牌汽车等意料之外的东西。\n"
        "6. 同一个事实只在一处声明：不要在 shot/art/motion 三个字段里对同一件事"
        "给出会互相矛盾的描述（比如一处说黄昏一处说深夜）。\n"
        "7. 音效写进 sfx 字段，不要塞进 motion 的散文描述里。\n"
        "8. IP 合规自检：不出现任何真实品牌标志/车标/商标，不影射任何真实公众人物"
        "或已有作品角色的可辨认长相。\n"
        "9. cast 里每个角色/场景给一份 anchor_prompt（独立定形用）和一份 block"
        "（后续镜次复用，保持外观一致），block 要能反复粘贴到不同镜次里保持描述一致。\n"
        "10. dur 不能图省事整除：不同镜次按内容需要给不同时长，别把总时长平均分成"
        "几段一样长的镜次——那是流水线感的直接来源，专业项目的镜次时长几乎不会整齐划一。"
        "同理，大多数镜次的台词字数应该接近（但不超过）dur×4.5 这个上限，把这一镜的"
        "时间基本用满；明显低于一半（比如 10 秒的镜只写 2 秒台词）应该是极少数、"
        "有意为之的沉默/反应镜，不是常态——如果一集里大半镜次都在留白，说明台词写得太省了。\n"
        "11. 台词要具体化、戏剧化，不要写成主题陈述句：「我很生气」「我不在乎」这种直接"
        "点题的句子是偷懒，观众要的是具体场景里的具体交锋——带数字、带引用、带升级"
        "（这句话比上一句更进一步/更扎心/更意外），不是把主题翻译成台词。每一组对话"
        "要么推进冲突要么揭示信息，不要停在互相宣告身份/立场就结束。\n"
        f"{CONTRACT_RULE}\n\n"
        f"{lang_rule}\n\n"
        f"{_style_guidance_text(camera_profile)}\n"
        f"{_camera_allowlist_text(camera_profile)}\n"
    )
    form_section = _form_section(form)
    out = base
    if genre_section:
        out = f"{out}\n{genre_section}"
    if form_section:
        out = f"{out}\n{form_section}"
    return out


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _call_openai_proxy(
    system_prompt: str, user_prompt: str, *, base_url: str, api_key: str, model: str,
    json_schema: dict[str, Any],
) -> tuple[dict[str, Any], float | None, int | None]:
    """打一个 OpenAI 兼容的 ``/chat/completions``。

    没有硬性 schema 校验：靠提示词里显式给 schema + JSON 模式，解析失败会明确报错而不是猜。
    """

    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    schema_text = json.dumps(json_schema, ensure_ascii=False, indent=2)
    full_system = (
        f"{system_prompt}\n\n"
        "只输出一个 JSON 对象，严格符合以下 JSON Schema，不要用代码块包裹，"
        f"不要输出 schema 之外的任何文字：\n{schema_text}\n"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": full_system},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    timeout = llm_proxy_timeout_seconds()
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise ScriptGenError(f"LLM 代理调用超时（{timeout}s）：{exc}") from exc
    except requests.RequestException as exc:
        raise ScriptGenError(f"LLM 代理调用失败（网络层）：{exc}") from exc
    if resp.status_code != 200:
        raise ScriptGenError(f"LLM 代理返回 {resp.status_code}：{resp.text[:800]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise ScriptGenError(f"LLM 代理返回的不是合法 JSON：{resp.text[:800]}") from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ScriptGenError(f"LLM 代理返回结构不是预期的 chat.completions 格式：{data}") from exc
    try:
        structured = json.loads(_strip_code_fence(content))
    except json.JSONDecodeError as exc:
        raise ScriptGenError(f"LLM 代理返回的 content 不是合法 JSON：{content[:800]}") from exc
    usage = data.get("usage") or {}
    total_tokens = usage.get("total_tokens")
    # 代理接口一般不直接报美元花费，只报 token 数；没有单价换算表就不硬编造成本。
    return structured, None, total_tokens


def _dispatch(
    system_prompt: str, user_prompt: str, *, json_schema: dict[str, Any]
) -> tuple[dict[str, Any], float | None, int | None, str]:
    """统一的后端选择+调用，起草整集和重写单镜共用——避免两处各写一遍

    「设置优先、其次代理、都没有就明确报错」这套判断逻辑走岔。
    """

    backend = scriptgen_backend()
    if backend == "settings":
        # 用户在「模型设置」里配的文本模型（官方 openai SDK，任何 OpenAI 兼容网关/OpenRouter）
        from genkit.router import build_provider

        chat = build_provider().text()
        schema_hint = json.dumps(json_schema, ensure_ascii=False)
        structured, meta = chat.complete_json(
            system_prompt + "\n\n只输出一个 JSON 对象，结构必须符合这个 JSON Schema：\n" + schema_hint,
            user_prompt,
            timeout=llm_proxy_timeout_seconds(),
        )
        usage = meta.get("usage") or {}
        tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        return structured, None, tokens, backend
    if backend == "openai_proxy":
        base_url = llm_proxy_base_url()
        api_key = llm_proxy_api_key()
        model = llm_proxy_model()
        if not base_url or not api_key:
            raise ScriptGenError(
                "SCRIPTGEN_BACKEND=openai_proxy 但没配全 SCRIPTGEN_LLM_BASE_URL / "
                "SCRIPTGEN_LLM_API_KEY，在 .env 里补上。"
            )
        if not model:
            raise ScriptGenError("没配 SCRIPTGEN_LLM_MODEL——代理暴露的模型 id 是什么，在 .env 里写清楚。")
        structured, cost_usd, usage_tokens = _call_openai_proxy(
            system_prompt, user_prompt, base_url=base_url, api_key=api_key, model=model,
            json_schema=json_schema,
        )
        return structured, cost_usd, usage_tokens, backend
    raise ScriptGenError(
        "还没有可用的剧本文本模型：在「模型设置」里给「剧本文本」选一个来源并填密钥"
        "（或在 .env 里配 SCRIPTGEN_LLM_BASE_URL / SCRIPTGEN_LLM_API_KEY / SCRIPTGEN_LLM_MODEL）。"
    )


def generate_draft(
    *,
    episode_id: str,
    direction: str,
    target_seconds: float,
    camera_profile: str,
    prompt_language: str,
    cast_hint: str | None,
    title: str | None = None,
    genre: str | None = None,
    form: str | None = None,
    screenplay: dict[str, Any] | None = None,
    series_handoff: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], float | None, int | None, str]:
    """返回 ``(draft, cost_usd, usage_tokens, backend)``。

    ``genre`` / ``form`` 给了但不是任何题材卡 / 形态卡的 id/名/别名时抛 ``ValueError``（不是
    ScriptGenError——这是调用方参数错，不是模型调用失败）。

    ``screenplay`` 给了就是两步起草的第二步（DESIGN §32）：用户提示词里带上整份剧本，要求按剧本拆镜、不改剧情、
    不加角色；不给就是原来的一步起草，提示词一个字不变。``series_handoff`` 是系列记忆里上一集的条目
    （``series.previous_entry``），给了就注入「上一集交接」段。

    ``cost_usd`` 是 None 时代表这条后端没直接报美元花费（目前只有代理这条
    会这样，改用 ``usage_tokens``），不是没花钱——只是没法精确报数字，调用方
    该老实展示"没有精确成本"，不该编一个假数字出来。
    """

    system_prompt = build_system_prompt(
        camera_profile=camera_profile, prompt_language=prompt_language, genre=genre, form=form,
    )
    user_parts = [
        f"项目 id：{episode_id}",
        f"目标总时长：约 {target_seconds:.0f} 秒（各镜 dur 之和应接近这个数，允许小幅误差）",
        f"故事方向：\n{direction.strip()}",
    ]
    if title and title.strip():
        user_parts.insert(1, f"剧名：{title.strip()}（title_zh 沿用这个名字，不要另起）")
    if cast_hint and cast_hint.strip():
        user_parts.append(f"角色/关系提示：\n{cast_hint.strip()}")
    if series_handoff:
        user_parts.append(_handoff_section(series_handoff))
    if screenplay:
        user_parts.append(_screenplay_section(screenplay))
    user_prompt = "\n\n".join(user_parts)
    return _dispatch(system_prompt, user_prompt, json_schema=DRAFT_JSON_SCHEMA)


# ---- 剧本层（DESIGN §32）：两步起草的第一步。先出一份人能读的剧本（场次 / 节拍 / 对白 / 契约），
# 人看过改过之后再按它拆镜。取 drama-skills「剧本层」的判据：每场都要改变信息、权力、关系或物理状态；
# 对白在争取、回避、试探、逼迫；场尾有可交接的状态——不取它的 Markdown 语法，剧本就是一份 JSON。

SCREENPLAY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "logline", "contract", "cast", "scenes"],
    "properties": {
        "title": {"type": "string", "description": "剧名"},
        "logline": {"type": "string", "description": "一到三句话的故事梗概"},
        "contract": DRAFT_JSON_SCHEMA["properties"]["episode_contract"],
        "cast": {
            "type": "array",
            "description": "人物表：每个角色一条",
            "items": {
                "type": "object",
                "required": ["id", "name", "role"],
                "properties": {
                    "id": {"type": "string", "description": "简短英文 slug，拆镜时沿用作 cast id"},
                    "name": {"type": "string", "description": "角色名"},
                    "role": {"type": "string", "description": "他在本集要什么、怕失去什么、惯用什么策略（一两句）"},
                    "look": {"type": "string", "description": "外形一句话（年龄、体态、发型、穿着），拆镜时定形用"},
                },
            },
        },
        "scenes": {
            "type": "array",
            "description": "场次，按时间顺序",
            "items": {
                "type": "object",
                "required": ["id", "heading", "beats", "dialogue"],
                "properties": {
                    "id": {"type": "string", "description": "简短英文 slug，如 SC01"},
                    "heading": {"type": "string", "description": "场景标题：内/外 · 地点 · 时间/天气"},
                    "beats": {
                        "type": "array", "items": {"type": "string"},
                        "description": "本场的因果节拍，每条一件可见的事（谁做了什么），按顺序；场尾一条写出去的状态",
                    },
                    "dialogue": {
                        "type": "array",
                        "description": "本场对白，按顺序；纯动作场留空数组",
                        "items": {
                            "type": "object",
                            "required": ["who", "zh"],
                            "properties": {
                                "who": {"type": "string", "description": "说话人的 cast id；旁白用 narrator"},
                                "zh": {"type": "string", "description": "中文台词"},
                                "en": {"type": "string", "description": "英文台词（可选）"},
                                "cue": {"type": "string", "description": "表演提示（可选，一两个词）"},
                            },
                        },
                    },
                    "change": {"type": "string", "description": "这一场改变了什么（信息 / 权力 / 关系 / 物理状态 / 风险），一句"},
                },
            },
        },
    },
}


def build_screenplay_system_prompt(
    *, prompt_language: str, genre: str | None = None, form: str | None = None
) -> str:
    """剧本层的系统提示词：只管故事，不管镜头/提示词/运镜——那是第二步的事。题材卡段与整集起草共用；
    形态卡段（``_form_section``，DESIGN §31）存在时也追加。"""

    genre_section = _genre_section(genre)
    form_fn = globals().get("_form_section")
    form_section = form_fn(form) if callable(form_fn) else ""
    lang_line = (
        "剧本用中文写；台词写在 zh。"
        if prompt_language.startswith("zh")
        else "剧本正文（heading / beats / change / look / role）用英文写；台词 zh 仍是中文原文，en 给英文。"
    )
    base = (
        "你是一部竖屏短剧的编剧。现在只写剧本，不写镜头、不写提示词、不写运镜——那是下一步拆镜的事。"
        "输出必须严格符合给定的 JSON Schema，只返回这一份结构化数据，不要输出别的文字。\n\n"
        "写作判据（都是短剧真踩过的坑）：\n"
        "1. 先立单集契约（contract）再写场：premise 一句话前提；hook 是开场钩子——观众能立即理解的未稳定局面"
        "（某人正在执行高代价行动、一个看似正常的事实与证据矛盾、有时限的目标已经开始），不是一句神秘台词；"
        "cliffhanger 写成「某人已经决定做什么 / 某个后果已经启动」，不是「他是谁」；must_pay_off 列本集自己必须兑现"
        "的局部结果——开放结尾与没结果不是一回事；carry_over 列留给下一集的事实：谁知道了什么、谁拿着什么、哪个危险已经开始。\n"
        "2. 每一场都要改变信息、权力、关系、情绪或物理状态；改不了的场删掉或合并。场尾（beats 最后一条）写出去的状态："
        "人在哪、拿着什么、刚决定了什么。\n"
        "3. 节拍写成可见的事（谁做了什么），不写功能（「他不再讲道理」不算，「他把打了一半的辩解删掉，只发出一条链接」才算）；"
        "相邻节拍之间要读得出「所以」或「可是」；两拍之间只剩「接着」，就是少了一个人物的选择或对手的反制。\n"
        "4. 对白必须在争取、回避、试探、逼迫或重新定义关系；带数字、带引用、带升级，不写主题陈述句，不解释观众已经看见的事。\n"
        "5. 反对力量要有自己的目标和真能阻挡的筹码（写成它做过或会做的事），不是属性。\n"
        "6. 竖屏短剧一集的体量：按目标时长控制场次与台词量（中文台词每秒约 4.5 字，动作也占时间），不要写成长片。\n"
        "7. 不出现真实品牌、真实公众人物、已有作品角色的可辨认长相。\n\n"
        f"{lang_line}\n"
    )
    for section in (genre_section, form_section):
        if section:
            base = f"{base}\n{section}"
    return base


def generate_screenplay(
    *,
    episode_id: str,
    direction: str,
    target_seconds: float,
    prompt_language: str,
    cast_hint: str | None,
    title: str | None = None,
    genre: str | None = None,
    form: str | None = None,
    series_handoff: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], float | None, int | None, str]:
    """两步起草的第一步：返回 ``(screenplay, cost_usd, usage_tokens, backend)``。跟 ``generate_draft`` 一样，
    题材/形态不认识抛 ``ValueError``；模型调用失败抛 ``ScriptGenError``。"""

    system_prompt = build_screenplay_system_prompt(prompt_language=prompt_language, genre=genre, form=form)
    user_parts = [
        f"项目 id：{episode_id}",
        f"目标总时长：约 {target_seconds:.0f} 秒（剧本体量按这个来）",
        f"故事方向：\n{direction.strip()}",
    ]
    if title and title.strip():
        user_parts.insert(1, f"剧名：{title.strip()}（title 沿用这个名字，不要另起）")
    if cast_hint and cast_hint.strip():
        user_parts.append(f"角色/关系提示：\n{cast_hint.strip()}")
    if series_handoff:
        user_parts.append(_handoff_section(series_handoff))
    user_prompt = "\n\n".join(user_parts)
    screenplay, cost_usd, usage_tokens, backend = _dispatch(
        system_prompt, user_prompt, json_schema=SCREENPLAY_JSON_SCHEMA,
    )
    if not isinstance(screenplay, dict) or not isinstance(screenplay.get("scenes"), list):
        raise ScriptGenError("模型没有按剧本结构返回（缺 scenes 列表）——再试一次，或换文本模型")
    if title and title.strip():
        screenplay["title"] = title.strip()
    return screenplay, cost_usd, usage_tokens, backend


def screenplay_text(screenplay: dict[str, Any]) -> str:
    """把剧本 JSON 排成人能读的纯文本（进第二步提示词，也给界面预览）。"""

    lines: list[str] = []
    if screenplay.get("title"):
        lines.append(f"《{screenplay['title']}》")
    if screenplay.get("logline"):
        lines.append(f"梗概：{screenplay['logline']}")
    contract = screenplay.get("contract")
    if isinstance(contract, dict):
        for key, label in (("premise", "前提"), ("hook", "开场钩子"), ("cliffhanger", "集尾")):
            if contract.get(key):
                lines.append(f"{label}：{contract[key]}")
        for key, label in (("must_pay_off", "本集必须兑现"), ("carry_over", "留给下一集")):
            items = [str(x) for x in (contract.get(key) or ()) if str(x).strip()]
            if items:
                lines.append(f"{label}：" + "；".join(items))
    cast = screenplay.get("cast")
    if isinstance(cast, list) and cast:
        lines.append("人物：")
        for member in cast:
            if not isinstance(member, dict):
                continue
            row = f"- {member.get('id', '')}（{member.get('name', '')}）：{member.get('role', '')}"
            if member.get("look"):
                row += f"｜外形：{member['look']}"
            lines.append(row)
    for scene in screenplay.get("scenes") or ():
        if not isinstance(scene, dict):
            continue
        lines.append("")
        lines.append(f"## {scene.get('id', '')} {scene.get('heading', '')}".rstrip())
        for beat in scene.get("beats") or ():
            lines.append(f"- {beat}")
        for line in scene.get("dialogue") or ():
            if not isinstance(line, dict):
                continue
            who = line.get("who", "")
            cue = f"（{line['cue']}）" if line.get("cue") else ""
            text = line.get("zh") or line.get("en") or ""
            lines.append(f"{who}{cue}：{text}")
        if scene.get("change"):
            lines.append(f"（这一场改变了：{scene['change']}）")
    return "\n".join(lines).strip()


def _screenplay_section(screenplay: dict[str, Any]) -> str:
    cast_ids = [str(m.get("id")) for m in (screenplay.get("cast") or ()) if isinstance(m, dict) and m.get("id")]
    ids = "、".join(cast_ids) if cast_ids else "（剧本没列人物表）"
    return (
        "剧本（两步起草的第一步已经定稿，人看过了）：按这个剧本拆镜，不要改剧情、不要加角色、不要删场次；"
        "台词按剧本原文分到各镜（可以拆句，不要改词）；cast 的 id 沿用剧本人物表的 id"
        f"（{ids}），场景/道具可以另起 id；episode_contract 照抄剧本的 contract。\n\n"
        f"{screenplay_text(screenplay)}"
    )


def _handoff_section(entry: dict[str, Any]) -> str:
    from .series import handoff_section

    return handoff_section(entry)


SHOT_JSON_SCHEMA: dict[str, Any] = DRAFT_JSON_SCHEMA["properties"]["shots"]["items"]


def build_shot_system_prompt(*, camera_profile: str, prompt_language: str, cast_ids: list[str]) -> str:
    """跟整集起草用同一套硬规则，但额外锁死"只改这一镜，不许发明新角色/场景"——

    单镜重写最容易犯的错就是顺手引入一个 cast 里没有的新角色，一改就跟其它
    镜次的人物设定对不上。
    """

    base = build_system_prompt(camera_profile=camera_profile, prompt_language=prompt_language)
    cast_list = "、".join(cast_ids) if cast_ids else "（这一集目前没有 cast）"
    return (
        f"{base}\n"
        "现在的任务不是起草整集，是只重写其中一镜。额外规则：\n"
        f"- cast 字段只能从这个现有列表里选，不许发明新 id：{cast_list}\n"
        "- 不要改动跟其它镜次相关的时间线连续性假设（比如上一镜结束的姿势、道具状态）"
        "，除非人给的反馈明确要求改。\n"
        "- 只输出这一镜的完整新版本（一个符合 shot schema 的 JSON 对象），"
        "不要输出其它镜次、不要输出整集的字段。\n"
    )


def regenerate_shot(
    *,
    spec: dict[str, Any],
    shot_id: str,
    feedback: str | None,
) -> tuple[dict[str, Any], float | None, int | None, str]:
    """只重写草稿里的一镜，其它镜次/全局字段原样不动。返回新的单镜 JSON。"""

    shots = spec.get("shots") or []
    current = next((s for s in shots if isinstance(s, dict) and s.get("id") == shot_id), None)
    if current is None:
        raise ScriptGenError(f"草稿里没有这一镜：{shot_id!r}")

    camera_profile = str(spec.get("camera_profile") or "")
    prompt_language = str(spec.get("prompt_language") or "zh")
    cast_ids = list((spec.get("cast") or {}).keys())
    system_prompt = build_shot_system_prompt(
        camera_profile=camera_profile, prompt_language=prompt_language, cast_ids=cast_ids,
    )

    neighbours = []
    idx = shots.index(current)
    for offset in (-1, 1):
        n = idx + offset
        if 0 <= n < len(shots) and isinstance(shots[n], dict):
            neighbours.append(
                f"{shots[n].get('id')}: {shots[n].get('shot', '')[:120]}"
            )

    user_parts = [
        f"全局风格（frame_style）：{spec.get('frame_style', '')}",
        f"全局构图规则（scene_anchor）：{spec.get('scene_anchor', '')}",
        f"整体运镜哲学（camera）：{spec.get('camera', '')}",
        f"这一镜（{shot_id}）目前的内容：\n{json.dumps(current, ensure_ascii=False, indent=2)}",
    ]
    if neighbours:
        user_parts.append("相邻镜次（仅供参考衔接，不要改它们）：\n" + "\n".join(neighbours))
    user_parts.append(f"需要怎么改：{(feedback or '').strip() or '在保持人物/风格一致的前提下自由改进这一镜。'}")
    user_prompt = "\n\n".join(user_parts)

    new_shot, cost_usd, usage_tokens, backend = _dispatch(
        system_prompt, user_prompt, json_schema=SHOT_JSON_SCHEMA,
    )
    new_shot["id"] = shot_id  # 防止模型手滑改了 id，跟原镜次对不上
    return new_shot, cost_usd, usage_tokens, backend


# 模型写 "@image" / "@video"：用「模型设置」里该角色配置的模型（适配器的 pick_model
# 约定）。AI 起草出来的新项目不再写死某个厂商的模型 id——用户换来源不用改 spec。
_DEFAULT_IMAGE_PARAMS = {
    "model": "@image",
    "aspect_ratio": "9:16",
    "image_size": "2K",
}
_DEFAULT_VIDEO_PARAMS = {
    "model": "@video",
    "resolution": "1080p",
    "aspect_ratio": "9:16",
    "generate_audio": True,
    "prompt_dialect": "auto",   # 按生效的视频模型选提示词语法（minimax-h3 / seedance / generic）
}
from .subtitles import DEFAULT_STYLE as _DEFAULT_SUBTITLE  # noqa: E402 - 字幕默认只此一处


def _without_model_assets(cast: Any) -> Any:
    """模型起草的人物表不带锚图路径：锚图只能是用户上传的文件（content/assets/…），模型写的 asset / 变体 asset
    是凭空编的路径，最坏是一条指向数据目录外的路径（2026-09-23 发布前自查 PB-3）。形状不对的原样交给校验去报。"""

    if not isinstance(cast, dict):
        return cast
    out: dict[str, Any] = {}
    for key, member in cast.items():
        if isinstance(member, dict):
            member = {k: v for k, v in member.items() if k != "asset"}
            variants = member.get("variants")
            if isinstance(variants, dict):
                member["variants"] = {vk: ({k: v for k, v in variant.items() if k != "asset"} if isinstance(variant, dict) else variant)
                                      for vk, variant in variants.items()}
        out[key] = member
    return out


def assemble_spec(
    episode_id: str,
    draft: dict[str, Any],
    *,
    camera_profile: str,
    prompt_language: str,
    workspace_id: str | None = None,
    form: str | None = None,
    genre: str | None = None,
) -> dict[str, Any]:
    """把模型草稿（cast+shots+若干全局提示词字段）拼成一份可校验的 spec.json 结构。

    机械/项目管线字段（output_root、image_params/video_params 默认值、subtitle
    规格）是这里填的，不是模型的活——模型只管创作内容，格式/账本这类跟具体
    这一集内容无关的字段用统一的默认值，减少草稿里出错的面。``workspace_id``
    只有调用方明确给了才写进 spec（多数来源用不到）。

    ``form``（形态卡，DESIGN §31）给了就写进 ``spec.form``（统一成卡 id），画幅 / 分辨率由卡的默认值补——
    通用默认里这几个键先让位，再由 ``forms.apply_form_defaults`` 只补缺省的键；不给 form 时结构与以前一样。

    ``genre``（题材卡）给了就写进 ``spec.genre``（统一成卡 id），事后「按题材审稿」（DESIGN §49）才有东西可对照；
    不认识抛 ``ValueError``；不给时结构与以前一样。
    """

    genre_id: str | None = None
    if genre is not None and str(genre).strip():
        genre_card = find_genre(genre)
        if genre_card is None:
            raise ValueError(f"不认识的题材：{genre}")
        genre_id = str(genre_card["id"])
    image_params = dict(_DEFAULT_IMAGE_PARAMS)
    video_params = dict(_DEFAULT_VIDEO_PARAMS)
    form_id: str | None = None
    if form is not None and str(form).strip():
        from .forms import _IMAGE_KEYS, _VIDEO_KEYS, find_form

        card = find_form(form)
        if card is None:
            raise ValueError(f"不认识的形态：{form}")
        form_id = str(card["id"])
        for key in _IMAGE_KEYS:
            image_params.pop(key, None)
        for key in _VIDEO_KEYS:
            video_params.pop(key, None)
    spec: dict[str, Any] = {
        "id": episode_id,
        "output_root": f"output/story_video/{episode_id}",
        "camera_profile": camera_profile,
        "prompt_language": prompt_language,
        "title_zh": draft.get("title_zh", ""),
        "title_en": draft.get("title_en", ""),
        "logline": draft.get("logline", ""),
        "frame_style": draft.get("frame_style", ""),
        "scene_anchor": draft.get("scene_anchor", ""),
        "camera": draft.get("camera", ""),
        "neg": draft.get("neg", ""),
        "image_params": image_params,
        "video_params": video_params,
        "subtitle": dict(_DEFAULT_SUBTITLE),
        "cast": _without_model_assets(draft.get("cast") or {}),
        "shots": draft.get("shots") or [],
    }
    if form_id:
        from .forms import apply_form_defaults

        spec["form"] = form_id
        apply_form_defaults(spec)
    if genre_id:
        spec["genre"] = genre_id
    if workspace_id:
        spec["workspace_id"] = workspace_id
    # 单集契约（DESIGN §32）：模型给了就带上（宽松清洗，救不回来的丢掉）；不进提示词
    from .contract import coerce_contract

    contract = coerce_contract(draft.get("episode_contract"))
    if contract:
        spec["episode_contract"] = contract
    if draft.get("video_lock"):
        spec["video_lock"] = draft["video_lock"]
    if draft.get("video_tail_lock"):
        spec["video_tail_lock"] = draft["video_tail_lock"]
    for shot in spec["shots"]:
        if isinstance(shot, dict):
            shot.setdefault("generation_role", "source_clip")
            shot.setdefault("request_type", "first_frame")
    return spec


def total_duration_warning(spec: dict[str, Any], target_seconds: float) -> list[str]:
    total = sum(float(s.get("dur") or 0) for s in spec.get("shots", ()) if isinstance(s, dict))
    if target_seconds <= 0:
        return []
    ratio = abs(total - target_seconds) / target_seconds
    if ratio > 0.2:
        return [
            f"草稿总时长 {total:.0f}s，跟目标 {target_seconds:.0f}s 差了 {ratio * 100:.0f}%——"
            "可以重新起草，或者人工增减镜次。"
        ]
    return []


__all__ = [
    "ScriptGenError",
    "DRAFT_JSON_SCHEMA",
    "SHOT_JSON_SCHEMA",
    "scriptgen_backend",
    "build_system_prompt",
    "generate_draft",
    "regenerate_shot",
    "assemble_spec",
    "total_duration_warning",
    "SCREENPLAY_JSON_SCHEMA",
    "build_screenplay_system_prompt",
    "generate_screenplay",
    "screenplay_text",
]
