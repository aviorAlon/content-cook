"""后端文案的语言层：中文是源文案，英文按字典（精确匹配）+ 模板规则（正则捕获变量）翻。

前端每个请求带 ``X-StoryStudio-Lang``（zh / en），中间件把它放进 contextvar；``tr()`` 在接口边界翻译
用户可见的字符串——下一步、能力检查、失败原因、错误翻译层的输出、HTTPException 的 detail。
中文时 ``tr`` 原样返回，零成本；英文缺翻译也原样返回中文（宁可露出中文，不露出 key 或空串）。

规则表在 ``i18n_rules.py``（纯数据）：``EXACT`` 精确表、``PATTERNS`` [(中文正则, 英文替换)]。
"""

from __future__ import annotations

import contextvars
import re
from typing import Any, Iterable, Mapping

LANGS = ("zh", "en")
_current: contextvars.ContextVar[str] = contextvars.ContextVar("storystudio_lang", default="zh")
_compiled: list[tuple[re.Pattern[str], str]] | None = None
_exact: dict[str, str] | None = None      # 有插件精确条目时的合并表；没有就直接用 i18n_rules.EXACT
_compiled_generation = -1


def get_lang() -> str:
    return _current.get()


def set_lang(lang: str) -> None:
    _current.set(lang if lang in LANGS else "zh")


def lang_from_headers(headers: Mapping[str, str]) -> str:
    explicit = str(headers.get("x-storystudio-lang") or "").strip().lower()
    if explicit in LANGS:
        return explicit
    accept = str(headers.get("accept-language") or "").strip().lower()
    return "en" if accept.startswith("en") else "zh"


def _rules() -> tuple[dict[str, str], list[tuple[re.Pattern[str], str]]]:
    """核心规则 + 已加载插件的规则。插件的整句规则（都是 ^…$ 锚定的）排在前面，免得被核心的 (\\S+) 兜底规则抢先；
    精确表核心优先，插件改不动核心已有的条目。插件登记 / 撤回（generation 变了）或 ``_compiled = None`` 都会重建。"""

    global _compiled, _exact, _compiled_generation
    from genkit import plugins

    from . import i18n_rules

    plugins.ensure_loaded()
    generation = plugins.generation()
    if _compiled is None or _compiled_generation != generation:
        plugin_exact = plugins.i18n_exact()
        _exact = {**plugin_exact, **i18n_rules.EXACT} if plugin_exact else None
        _compiled = [(re.compile(pat), rep) for pat, rep in (*plugins.i18n_patterns(), *i18n_rules.PATTERNS)]
        _compiled_generation = generation
    return (_exact if _exact is not None else i18n_rules.EXACT), _compiled


_HAN = re.compile(r"[\u4e00-\u9fff]")
# 引号里的是用户内容（角色名、台词、文件名）——判断「翻干净了没有」时不算
_QUOTED = re.compile(r"「[^」]*」|《[^》]*》|\"[^\"]*\"")
# 任务记录把失败原因存成「异常类名: 人话」（tasks.py / runtime 的 job.error，humanize.classify_failure 按类名分类，
# 前端 failureLine 把类名剥掉只显示人话）；规则表都是按整句锚定的，类名留着就一条也命不中
_EXC_PREFIX = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Busy)[\w.]*):\s*(.+)$", re.DOTALL)
_GROUP_REF = re.compile(r"\\(?:(\d+)|g<(\d+)>)")


def _cjk_outside_quotes(text: str) -> bool:
    return bool(_HAN.search(_QUOTED.sub("", text)))


def _split_trailing_paren(text: str) -> tuple[str, str] | None:
    """「人话（原文）」形状：拆成 (人话, 括号里的原文)。括号按配对找——原文里自己也可能带括号
    （「提交后没拿到结果（image 模型未配置：…（在「模型设置」里填好再生成））」），按最后一个「（」切会切在里层，
    输出就成了「(…）」这种半个括号。"""

    if not text.endswith("）"):
        return None
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        ch = text[i]
        if ch == "）":
            depth += 1
        elif ch == "（":
            depth -= 1
            if depth == 0:
                return text[:i], text[i + 1:-1]
    return None


def _expand(match: re.Match[str], replacement: str) -> str:
    """按规则展开；捕获组里还剩中文（路由前缀套着的里层人话、「X 失败：<原因>」的原因）就把那一组单独再翻一遍。
    引号里的中文是用户内容，不算没翻干净。"""

    try:
        out = match.expand(replacement)
    except (re.error, IndexError):
        return replacement
    if not _cjk_outside_quotes(out):
        return out
    groups = match.groups()
    inner = {i: _tr_en(g) for i, g in enumerate(groups, 1) if g and _HAN.search(g)}
    if all(inner[i] == groups[i - 1] for i in inner):
        return out

    def ref(m: re.Match[str]) -> str:
        i = int(m.group(1) or m.group(2))
        if not 0 < i <= len(groups):
            return m.group(0)
        return inner.get(i) or groups[i - 1] or ""

    return _GROUP_REF.sub(ref, replacement)


def _tr_en(text: str) -> str:
    exact, patterns = _rules()
    if text in exact:
        return exact[text]
    m = _EXC_PREFIX.match(text)
    if m:
        tail = _tr_en(m.group(2))
        return text if tail == m.group(2) else f"{m.group(1)}: {tail}"
    # 「人话（原文）」形状：先翻括号前的人话；括号里通常是原文（保留给开发者），偶尔是固定的中文补充说明，能翻就翻。
    # 拆开翻还剩中文（人话只命中了「改动没通过校验，没有写盘：(.+)」这种只翻前缀的兜底，或括号是文案自带的补充说明）
    # 就先别急着用它——整句规则可能就在后面（风格卡那句的括号是文案的一部分），整句也没命中再退回这个半成品
    partial: str | None = None
    split = _split_trailing_paren(text)
    if split:
        head, inner = split
        translated_head = _tr_en(head)
        if translated_head != head:
            translated_inner = _tr_en(inner)
            partial = f"{translated_head} ({translated_inner})"
            if not _cjk_outside_quotes(translated_head) and not _cjk_outside_quotes(translated_inner):
                return partial
    for pattern, replacement in patterns:
        match = pattern.search(text)
        if match:
            return _expand(match, replacement)
    return partial if partial is not None else text


def tr(text: Any, lang: str | None = None) -> Any:
    """翻一条文案。非字符串原样返回；中文或没规则命中也原样返回。"""

    if not isinstance(text, str) or not text:
        return text
    if (lang or get_lang()) != "en":
        return text
    return _tr_en(text)


def tr_obj(obj: Any, keys: Iterable[str] = ("label", "hint", "message", "advice", "detail", "note", "reason", "text"),
           lang: str | None = None, *, lists: Iterable[str] = ("warnings", "issues_text", "advice"),
           saved: Iterable[str] = ()) -> Any:
    """递归翻译结构里指定键的字符串值（列表里的字符串也翻）。原地修改并返回。
    ``saved`` 里的键下面是项目文件原样（见 SAVED_KEYS）：那一整棵子树里 SAVED_RAW_KEYS 不翻。"""

    if (lang or get_lang()) != "en":
        return obj
    key_set, list_set, saved_set = set(keys), set(lists), set(saved)
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, str) and k in key_set:
                obj[k] = tr(v, "en")
            elif k in list_set and isinstance(v, (list, dict)):
                obj[k] = _tr_strings(v, keys, lists)
            elif isinstance(v, (dict, list)):
                sub = tuple(x for x in keys if x not in SAVED_RAW_KEYS) if k in saved_set else keys
                tr_obj(v, sub, "en", lists=lists, saved=saved)
    elif isinstance(obj, list):
        for item in obj:
            tr_obj(item, keys, "en", lists=lists, saved=saved)
    return obj


def _tr_strings(value: Any, keys: Iterable[str], lists: Iterable[str]) -> Any:
    """列表键下面的所有字符串都翻（含 {角色: [文案]} 这种字典）。"""

    if isinstance(value, str):
        return tr(value, "en")
    if isinstance(value, list):
        return [_tr_strings(x, keys, lists) for x in value]
    if isinstance(value, dict):
        return {k: _tr_strings(x, keys, lists) if isinstance(x, (str, list)) else tr_obj(x, keys, "en", lists=lists) for k, x in value.items()}
    return value


# JSON 响应里会承载用户可见文案的键；不含 title / text / zh / en / block 这类用户自己的内容。
# error：任务记录的失败原因（task.error / result.failed[].error / attempts[].error / items[].error）——任务中心和各模块页
# 直接把它摆给用户看；服务商返回的英文原因过 tr 没规则命中，原样不动。详情里 spec 子树下的 error 例外（见 SAVED_KEYS）
# warning：删除 / 恢复项目时「做了、但有一部分没做」的提示（侧栏弹窗原样给人看）
# unavailable：/api/health 的角色状态里「选的来源没装上」那句人话（插件没加载时）
TEXT_KEYS = ("label", "hint", "message", "advice", "detail", "note", "reason", "install_hint", "readonly_reason", "status_text",
             "cost_note", "voice_label", "error", "mix_error", "warning", "unavailable")
LIST_KEYS = ("warnings", "advice", "hints", "notes", "problems", "messages", "status")
# 详情接口里的 spec 是项目文件原样：编辑页（SetItems / ClipWrite / MusicGenerate / 各「高级」JSON 页）会把它整份 PUT 回去，
# normalize 照收。里面落盘的失败原因（items[].error / attempts[].error / first_frame.error）要是也翻了，英文界面保存一次
# 就把中文原文覆盖成英文——中文界面从此看到英文、原文找不回来（中文是源文案，英文只是显示层）。页面显示失败原因用的是
# 同一响应里的派生行（items / attempts / first_frame / status），那些照翻；spec 里的 error 原样留着。
SAVED_KEYS = ("spec",)
SAVED_RAW_KEYS = ("error",)


def translate_json(body: bytes) -> bytes | None:
    """中间件用：把 JSON 响应体里的文案键翻成英文；不是 JSON 或没变化返回 None（原样透传）。"""

    import json

    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, (dict, list)):
        return None
    tr_obj(data, TEXT_KEYS, "en", lists=LIST_KEYS, saved=SAVED_KEYS)
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


__all__ = ["LANGS", "get_lang", "set_lang", "lang_from_headers", "tr", "tr_obj", "translate_json", "TEXT_KEYS", "LIST_KEYS",
           "SAVED_KEYS", "SAVED_RAW_KEYS"]
