"""视频提示词方言：同一份镜头事实（入口状态、唯一动作、台词、运镜、音效、锁、出口状态），按目标模型的
官方提示词语法重排。内容权威仍是 spec，方言只管"怎么写"。

- ``generic``：现在的散文式一段话（老项目不变；提示词金丝雀守的就是它）。
- 连续性锁（``cast.<id>.locks``，prompts.lock_clause）在三种方言里都紧挨在「锁」段之前，原样带进正文。
- 揭示保护（``shot.withhold``，prompts.withhold_clause）的排除句在三种方言里都接在表演句之后、文字政策句之前。
- 形态卡运动预算句（``spec.form``，forms.motion_budget_sentence）紧跟在揭示句之后；没写 form 的 spec 没有这一句。
- ``minimax-h3``：MiniMax H3 官方三段结构——``integrated_multimodal_description: [Shot 1] …`` /
  ``overall_soundscape: …`` / ``non_diegetic_music: N/A``；说话人用稳定 ID ``(S1)``，中文台词写成
  ``<d>[Chinese] 逐字台词</d>``，不在台词前加外语暖场；字幕禁令写成直接指令。
- ``seedance``：豆包 Seedance 的中文语法——对白 ``{角色用中文说："逐字台词"}``、音效 ``<声音>``、
  默认"整段视频不加字幕"。

选哪一种由 ``video_params.prompt_dialect`` 决定：``auto`` 按生效的视频模型判断；缺省（老项目）= ``generic``。
参考：zenstory drama-skills 的 minimax-h3.md / seedance-2.0.md（2026-09-08 融合）。
"""

from __future__ import annotations

from typing import Any, Mapping

DIALECTS = ("generic", "minimax-h3", "seedance")


def dialect_for_model(model: str | None) -> str:
    low = str(model or "").lower()
    if "minimax-h3" in low or low == "minimax-h3" or "hailuo-3" in low or "/h3" in low:
        return "minimax-h3"
    if "seedance" in low:
        return "seedance"
    return "generic"


def resolve_dialect(spec: Mapping[str, Any], settings: Any = None) -> str:
    """spec 显式写了就用；``auto`` 看 video_params.model，``@video`` 再看模型设置；没写 = generic。"""

    params = spec.get("video_params") or {}
    wanted = str(params.get("prompt_dialect") or "").strip().lower()
    if wanted in DIALECTS:
        return wanted
    if wanted != "auto":
        return "generic"
    model = str(params.get("model") or "").strip()
    if not model or model.startswith("@"):
        try:
            from genkit import settings as settings_mod

            current = settings or settings_mod.load_settings()
            cfg = current.role("video")
            model = settings_mod.effective(cfg, "video").model if cfg.adapter else ""
        except Exception:  # noqa: BLE001 - 读不到设置就按通用写
            model = ""
    return dialect_for_model(model)


# ---------------------------------------------------------------------------- 渲染


def _speaker_ids(spec: Mapping[str, Any]) -> dict[str, str]:
    """说话人 ID 按 cast 里角色（有 name 的）出现顺序编号：S1、S2…，整集稳定。"""

    ids: dict[str, str] = {}
    for key, member in (spec.get("cast") or {}).items():
        if isinstance(member, Mapping) and member.get("name"):
            ids[str(key)] = f"S{len(ids) + 1}"
    return ids


def _join(parts: list[str], sep: str) -> str:
    """按句拼接：去掉各段自带的句末标点再用 sep 连，避免「。。」。"""

    cleaned = [p.strip().rstrip("。.;； ") for p in parts if p and p.strip()]
    return sep.join(c for c in cleaned if c)


def render_minimax_h3(parts: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    zh = bool(parts.get("zh"))
    ids = _speaker_ids(spec)
    picture: list[str] = []
    if parts.get("camera"):
        picture.append(parts["camera"])
    if parts.get("entry"):
        picture.append(("开场：" if zh else "Opening: ") + parts["entry"])
    if parts.get("axis"):
        picture.append(parts["axis"])
    if parts.get("motion"):
        picture.append(parts["motion"])
    if parts.get("beats"):
        picture.append(parts["beats"])
    for line in parts.get("lines") or ():
        sid = ids.get(str(line.get("who") or ""), "S1")
        name = line.get("name") or line.get("who") or ("说话人" if zh else "the speaker")
        lang_tag = "Chinese" if line.get("lang", "zh") == "zh" else "English"
        manner = _join([str(line.get("tone") or ""), str(line.get("voice") or "")], "，" if zh else ", ")
        if parts.get("offscreen"):
            where = f"{name} ({sid}) 画外说话，不在画面里" if zh else f"{name} ({sid}) speaks off-camera, not in frame"
        else:
            where = f"{name} ({sid}) 对着画面里的对象开口，嘴型与台词一致" if zh else f"{name} ({sid}) speaks on camera, lips matching the words"
        manner_part = (f"，{manner}" if zh else f", {manner}") if manner else ""
        picture.append(f"{where}{manner_part}：<d>[{lang_tag}] {line['text']}</d>" if zh else f"{where}{manner_part}: <d>[{lang_tag}] {line['text']}</d>")
    if parts.get("lines"):
        picture.append("台词在开头 0.8 秒内开口，每个字口齿清楚、正常音量以上；本镜只有这几句话" if zh
                       else "The first line starts within 0.8 s; every word is articulated clearly at normal volume or louder; these are the only words in the shot")
    if parts.get("exit"):
        picture.append(("收尾：" if zh else "Ending: ") + parts["exit"])
    if parts.get("locks"):
        picture.append(parts["locks"])
    if parts.get("lock"):
        picture.append(parts["lock"])
    if parts.get("performance"):
        picture.append(parts["performance"])
    if parts.get("withhold"):
        picture.append(parts["withhold"])   # 揭示保护（DESIGN §28）：观众此刻不能知道的事实与它的载体
    if parts.get("form_budget"):
        picture.append(parts["form_budget"].rstrip("。."))   # 形态卡运动预算（DESIGN §31）；句号由 _join 统一补
    if parts.get("readable_text"):
        # 画面文字政策（IMG-07）：本镜声明了必须可读的文字，字幕禁令只禁叠加层，不再说「没有任何可读文字」
        picture.append(parts["readable_text"])
        picture.append("全程任何时刻都不生成字幕、caption 或对白文字叠加；画面里除上面指明的文字外没有其他可读文字" if zh
                       else "At no point are subtitles, captions or dialogue text overlays generated; apart from the text named above, no other readable text appears in the frame")
    else:
        picture.append("全程任何时刻都不生成字幕、caption 或对白文字叠加，画面里没有任何可读文字" if zh
                       else "At no point are subtitles, captions or dialogue text overlays generated; no readable text appears anywhere in the frame")
    body = _join(picture, "。" if zh else ". ") + ("。" if zh else ".")
    sound: list[str] = []
    if parts.get("sfx"):
        sound.append(parts["sfx"])
    sound.append("环境声只保留场景本身的底噪，没有配乐、没有旁白、没有背景人声；对白只按上面主时间线出现" if zh
                 else "Room tone only, no music, no narration, no background chatter; dialogue only as timed in the main description")
    return (
        f"integrated_multimodal_description: [Shot 1] {body}\n"
        f"overall_soundscape: {_join(sound, '；' if zh else '; ')}\n"
        f"non_diegetic_music: N/A"
    )


def render_seedance(parts: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    zh = bool(parts.get("zh"))
    out: list[str] = []
    if parts.get("entry"):
        out.append(("开场：" if zh else "Opening: ") + parts["entry"])
    if parts.get("axis"):
        out.append(parts["axis"])
    if parts.get("motion"):
        out.append(parts["motion"])
    if parts.get("beats"):
        out.append(parts["beats"])
    for line in parts.get("lines") or ():
        name = line.get("name") or line.get("who") or ("说话人" if zh else "the speaker")
        manner = _join([str(line.get("tone") or ""), str(line.get("voice") or "")], "，" if zh else ", ")
        if zh:
            who = f"画外的{name}" if parts.get("offscreen") else name
            lang = "中文" if line.get("lang", "zh") == "zh" else "英文"
            out.append(f"{{{who}用{lang}说：\"{line['text']}\"}}" + (f"（{manner}）" if manner else ""))
        else:
            who = f"{name} (off-camera)" if parts.get("offscreen") else name
            out.append(f"{{{who} says in {'Chinese' if line.get('lang', 'zh') == 'zh' else 'English'}: \"{line['text']}\"}}" + (f" ({manner})" if manner else ""))
    if parts.get("lines") and not parts.get("offscreen"):
        out.append("嘴型与台词一致，每个字口齿清楚、正常音量以上；本镜只有这几句话，没有其他对白和旁白" if zh
                   else "Lips match the words; every word is clear at normal volume or louder; these are the only words, no other dialogue or narration")
    if parts.get("camera"):
        out.append(parts["camera"])
    if parts.get("sfx"):
        out.append(f"<{parts['sfx']}>")
    if parts.get("exit"):
        out.append(("收尾：" if zh else "Ending: ") + parts["exit"])
    if parts.get("locks"):
        out.append(parts["locks"])
    if parts.get("lock"):
        out.append(parts["lock"])
    if parts.get("performance"):
        out.append(parts["performance"])
    if parts.get("withhold"):
        out.append(parts["withhold"])   # 揭示保护（DESIGN §28）
    if parts.get("form_budget"):
        out.append(parts["form_budget"])   # 形态卡运动预算（DESIGN §31）
    if parts.get("readable_text"):
        # 画面文字政策（IMG-07）：字幕禁令只禁叠加层，不与本镜允许的画面文字打架
        out.append(parts["readable_text"])
        out.append("整段视频不加字幕，也不把台词显示成屏幕上的字；除上面指明的文字外，画面里没有别的可读文字。" if zh
                   else "No captions anywhere in the video and no spoken lines shown as on-screen text; apart from the text named above, nothing else in the frame is readable.")
    else:
        out.append("整段视频不加字幕，也不把台词显示成屏幕上的字，画面里没有任何可读文字。" if zh
                   else "No captions anywhere in the video and no spoken lines shown as on-screen text; nothing in the frame is readable.")
    return " ".join(p.strip() for p in out if p and p.strip())   # 各段保留自己的标点


RENDERERS = {"minimax-h3": render_minimax_h3, "seedance": render_seedance}

__all__ = ["DIALECTS", "dialect_for_model", "resolve_dialect", "render_minimax_h3", "render_seedance", "RENDERERS"]
