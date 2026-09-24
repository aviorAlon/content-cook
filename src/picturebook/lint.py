"""绘本文本静态检查（契约 §3；判据是儿童绘本写作的经验规则）。

告警是一句话：``page P03: 影响——怎么改`` 或 ``全书：影响——怎么改``，接口层拆成问题卡（webui/problems.py）。
「重复结构」不是错误：没有母题只给提示（``notes``），不进 warnings——示例书零告警的标准只看 warnings。
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from typing import Any, Mapping

# 分级：(正文页中文字数上限, 下限, 总页数下限, 总页数上限)
AGE_RULES: dict[str, tuple[int, int, int, int]] = {
    "2-4": (20, 0, 8, 10),
    "4-6": (40, 20, 10, 14),
    "6-8": (70, 40, 12, 16),
}
HOOK_GAP = 3                     # 连续 3 个正文页没钩就告警（隔一两页就该有一个）
SCARY_WORDS = ("怪物", "鬼", "尖叫", "血", "恐怖", "黑影", "怪声", "monster", "ghost", "scream", "blood", "shadow")
CONNECTIVES = ("然后", "接着", "之后", "随后", "紧接着", "最后", " then ", " after that", " afterwards")
TEXT_IN_SCENE = ("写着", "标题", "字")
PLURAL_PRONOUNS = ("它们", "他们", "她们", "大家", "动物们", "朋友们", "小伙伴们", "everyone", "everybody", " they ", "the animals", "all of them")
CALM_WORDS = ("晚安", "睡", "抱", "回家")
VOCAB_MIN = 3
_HAN = re.compile(r"[一-鿿]")
_ONOMATOPOEIA_CHARS = "咚哗啦汪嘎呼沙叮滴答扑通啪咔嘻哈嘟吱呱咕嗡噗哒砰呜嘘喵咩哞叽啾嗒嗖嘭"
_ZH_ONOMATOPOEIA = re.compile(rf"([{_ONOMATOPOEIA_CHARS}])\1{{1,2}}|哗啦|叮咚|扑通|滴答|咕噜|噼啪|嘀嗒")
_EN_ONOMATOPOEIA = re.compile(
    r"\b(knock,? knock(?:,? knock)?|splash|boom|tick[- ]tock|woof|quack|meow|moo|baa|zzz+|thud|bang|ding|"
    r"whoosh|swish|pitter[- ]patter|drip|clap|tap tap|hop hop|ha ha|shh+)\b", re.IGNORECASE)
_NUMERALS = re.compile(r"[0-9０-９一二三四五六七八九十两俩]+")
_PUNCT = re.compile(r"[\s，。！？；：、,.!?;:'\"“”‘’…—\-()（）《》「」]+")
_TEMPLATE_RATIO = 0.8


def _page_label(page_id: str, impact: str, fix: str) -> str:
    return f"page {page_id}: {impact}——{fix}"


def _book_label(impact: str, fix: str) -> str:
    return f"全书：{impact}——{fix}"


def zh_char_count(text: str) -> int:
    """只数汉字（标点、空格、数字不算）。"""

    return len(_HAN.findall(text or ""))


def onomatopoeia_groups(text: str) -> list[str]:
    """一段文字里的拟声词组（去重、归一化成小写）。"""

    found: list[str] = []
    for match in _ZH_ONOMATOPOEIA.finditer(text or ""):
        token = match.group(0)
        key = token[0] * 2 if len(set(token)) == 1 else token
        if key not in found:
            found.append(key)
    for match in _EN_ONOMATOPOEIA.finditer(text or ""):
        key = re.sub(r"[\s,\-]+", " ", match.group(0).lower()).strip()
        key = re.sub(r"^(knock)( knock)+$", "knock knock", key)
        if key not in found:
            found.append(key)
    return found


def sentence_template(text: str, names: tuple[str, ...] = ()) -> str:
    """句式模板：去掉数字、角色名、标点。"""

    out = _NUMERALS.sub("", text or "")
    for name in sorted(names, key=len, reverse=True):
        if name:
            out = out.replace(name, "")
    return _PUNCT.sub("", out).strip().lower()


def repetition_clusters(spec: Mapping[str, Any]) -> list[list[str]]:
    """模板相同（或相似度 ≥ 0.8）的中文页聚成一簇，返回 ≥3 页的簇（每簇 = 页 id 列表）。"""

    names = tuple(str(m.get("name") or "") for m in (spec.get("characters") or {}).values())
    names += tuple(str(m.get("name") or "") for m in (spec.get("scenes") or {}).values())
    rows: list[tuple[str, str]] = []
    for page in spec.get("pages") or ():
        template = sentence_template(str(page.get("zh") or page.get("en") or ""), names)
        if len(template) >= 2:
            rows.append((str(page["id"]), template))
    clusters: list[list[tuple[str, str]]] = []
    for pid, template in rows:
        for cluster in clusters:
            anchor = cluster[0][1]
            if template == anchor or difflib.SequenceMatcher(None, template, anchor).ratio() >= _TEMPLATE_RATIO:
                cluster.append((pid, template))
                break
        else:
            clusters.append([(pid, template)])
    return [[pid for pid, _ in c] for c in clusters if len(c) >= 3]


def vocab_hits(spec: Mapping[str, Any]) -> dict[str, int]:
    """education.vocab 每个条目（「袜子/sock」）在全书 zh+en 里出现的次数。"""

    zh_all = "\n".join(str(p.get("zh") or "") for p in spec.get("pages") or ())
    en_all = "\n".join(str(p.get("en") or "") for p in spec.get("pages") or ()).lower()
    out: dict[str, int] = {}
    for entry in (spec.get("education") or {}).get("vocab") or ():
        entry = str(entry).strip()
        if not entry:
            continue
        total = 0
        for term in [t.strip() for t in entry.split("/") if t.strip()]:
            if _HAN.search(term):
                total += zh_all.count(term)
            else:
                total += len(re.findall(rf"\b{re.escape(term.lower())}s?\b", en_all))
        out[entry] = total
    return out


def lint_book(spec: Mapping[str, Any]) -> list[str]:
    """契约 §3 的全部告警（不含「重复结构」提示）。"""

    warnings: list[str] = []
    pages = [p for p in spec.get("pages") or () if isinstance(p, Mapping)]
    if not pages:
        return ["全书：还没有分页——先起草或手填分页表"]
    mode = str(spec.get("bilingual_mode") or "sentence_alternate")
    age = str(spec.get("age_band") or "2-4")
    max_chars, min_chars, min_pages, max_pages = AGE_RULES.get(age, AGE_RULES["2-4"])

    # 分级：页数（含封面封底）、字数（只查正文页）
    if not min_pages <= len(pages) <= max_pages:
        warnings.append(_book_label(
            f"{age} 岁的书要 {min_pages}–{max_pages} 页（含封面封底），现在是 {len(pages)} 页",
            "增删正文页到这个范围"))
    for page in pages:
        if page.get("kind") != "page" or mode == "en_only":
            continue
        count = zh_char_count(str(page.get("zh") or ""))
        if count > max_chars:
            warnings.append(_page_label(str(page["id"]), f"{age} 岁每页中文不超过 {max_chars} 字，这页 {count} 字",
                                        "拆成两页，或删掉修饰词只留主谓宾"))
        elif min_chars and count < min_chars:
            warnings.append(_page_label(str(page["id"]), f"{age} 岁每页中文要 {min_chars}–{max_chars} 字，这页只有 {count} 字",
                                        "补一句动作或感受，或跟相邻页合并"))

    # 翻页钩（封面是书名页，不计入「连续几页没钩」；结尾页另查）
    gap = 0
    for page in pages[:-1]:
        if page.get("kind") == "cover":
            continue
        if str(page.get("hook") or "").strip():
            gap = 0
        else:
            gap += 1
            if gap == HOOK_GAP:
                warnings.append(_page_label(str(page["id"]), f"连续 {HOOK_GAP} 页没有翻页钩",
                                            "隔一两页就在页尾留一个小问题或没做完的事，翻过去再揭晓"))
                gap = 0
    last = pages[-1]
    if str(last.get("hook") or "").strip():
        warnings.append(_page_label(str(last["id"]), "结尾页留了翻页钩", "最后一页不再吊胃口，让故事安稳落地"))
    for page in pages:
        hook = str(page.get("hook") or "")
        hit = next((w for w in SCARY_WORDS if w in hook.lower()), None)
        if hit:
            warnings.append(_page_label(str(page["id"]), f"翻页钩里有恐怖词「{hit}」",
                                        "换成让孩子好奇而不是害怕的问题（比如「它会飘到哪儿去？」）"))

    # KB 方向
    for prev, page in zip(pages, pages[1:]):
        if (prev.get("kb") or {}).get("dir") and (prev.get("kb") or {}).get("dir") == (page.get("kb") or {}).get("dir"):
            warnings.append(_page_label(str(page["id"]), f"KB 方向跟上一页一样（{page['kb']['dir']}），连着两页往一个方向动，看着发晕",
                                        "换个方向：push / pull / left / right / up / down 交替"))

    # 拟声词：中英是同一组的两种写法（呼呼 / whoosh），按语言分别数，取多的那边
    book_zh: list[str] = []
    book_en: list[str] = []
    for page in pages:
        zh_groups = onomatopoeia_groups(str(page.get("zh") or ""))
        en_groups = onomatopoeia_groups(str(page.get("en") or ""))
        for g in zh_groups:
            if g not in book_zh:
                book_zh.append(g)
        for g in en_groups:
            if g not in book_en:
                book_en.append(g)
        groups = zh_groups if len(zh_groups) >= len(en_groups) else en_groups
        if len(groups) > 1:
            warnings.append(_page_label(str(page["id"]), f"一页里有 {len(groups)} 组拟声词（{'、'.join(groups)}）",
                                        "一页只留一组，其余写成动作"))
    book_groups = book_zh if len(book_zh) >= len(book_en) else book_en
    if len(book_groups) > 4:
        warnings.append(_book_label(f"全书用了 {len(book_groups)} 组拟声词（{'、'.join(book_groups)}）",
                                    "全书来回用固定的几组，最多 4 组"))

    # 一页一个动作 / 插画无字 / 群像点名
    for page in pages:
        scene = str(page.get("scene") or "")
        padded = f" {scene.lower()} "
        hit = next((w for w in CONNECTIVES if w in padded), None)
        if hit:
            warnings.append(_page_label(str(page["id"]), f"画面描述里有「{hit.strip()}」，插画里要同时画好几个动作",
                                        "每页只画一个瞬间：留一个动作，其余挪到下一页"))
        hit = next((w for w in TEXT_IN_SCENE if w in scene), None)
        if hit:
            warnings.append(_page_label(str(page["id"]), f"画面描述里出现「{hit}」，插画里不能有文字",
                                        "删掉要画出来的字：插画本身不带字，字幕由程序另外叠上去"))
        if len(page.get("characters") or ()) >= 2:
            hit = next((w for w in PLURAL_PRONOUNS if w in padded), None)
            if hit:
                warnings.append(_page_label(str(page["id"]), f"群像页用了指代词「{hit.strip()}」，插画模型可能画出没写的角色",
                                            "把每个角色的名字和位置都写出来"))

    # 结尾安稳
    if mode != "en_only":
        ending = str(last.get("zh") or "")
        if not any(w in ending for w in CALM_WORDS):
            warnings.append(_page_label(str(last["id"]), f"结尾不够安稳：{age} 岁的书最后一页中文里没有 晚安 / 睡 / 抱 / 回家 之一",
                                        "最后一句写到入睡或到家，读完能踏实合上书"))

    # 教育点
    for entry, hits in vocab_hits(spec).items():
        if hits < VOCAB_MIN:
            warnings.append(_book_label(f"目标词「{entry}」全书只出现 {hits} 次（要 ≥{VOCAB_MIN} 次）",
                                        "把它放进重复句式里，每次重复都带上"))
    return warnings


def lint_notes(spec: Mapping[str, Any]) -> list[str]:
    """不是错误的提示：重复结构母题。"""

    if not spec.get("pages"):
        return []
    clusters = repetition_clusters(spec)
    if clusters:
        return [f"全书：重复母题 {len(clusters)} 组（{'；'.join('/'.join(c) for c in clusters)}）——最后一次出现时记得换个结果"]
    return ["全书：没有出现 ≥3 次的重复句式——小孩子喜欢提前猜到下一句：可以让一句话在三页里原样出现，最后一次换个结果"]


def lint_stats(spec: Mapping[str, Any]) -> dict[str, Any]:
    pages = spec.get("pages") or []
    return {
        "pages": len(pages),
        "body_pages": sum(1 for p in pages if p.get("kind") == "page"),
        "hooks": sum(1 for p in pages if str(p.get("hook") or "").strip()),
        "zh_chars": [zh_char_count(str(p.get("zh") or "")) for p in pages],
        "onomatopoeia": Counter(g for p in pages for g in onomatopoeia_groups(f"{p.get('zh') or ''}\n{p.get('en') or ''}")),
        "vocab": vocab_hits(spec),
        "repetition": repetition_clusters(spec),
    }


def lint_report(spec: Mapping[str, Any]) -> dict[str, Any]:
    stats = lint_stats(spec)
    stats["onomatopoeia"] = dict(stats["onomatopoeia"])
    return {"warnings": lint_book(spec), "notes": lint_notes(spec), "stats": stats}


__all__ = ["AGE_RULES", "lint_book", "lint_notes", "lint_report", "lint_stats", "zh_char_count", "onomatopoeia_groups",
           "sentence_template", "repetition_clusters", "vocab_hits"]
