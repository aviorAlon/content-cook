"""把运行时的英文守卫文案翻译成用户能看懂的一句话。

运行时（storyvideo.runtime / qc）的异常文案是给开发者和命令行用的，措辞是状态机
内部的词（"frame QC must be accepted before video submission"）。界面层不重写
底层异常——那是跟 CLI 共享的——只在接口边界把最常见的几十条翻成人话，原文保留
在后面括号里，方便真出问题时贴给开发者。没匹配到的原样返回。

术语对照（界面统一用右边的词）：
  prepare / freeze → 开始制作     revision → 新版本     retake → 重拍
  QC / gate → 审片                assembly → 成片       amend → 保存（后期块）
"""

from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"frame QC must be accepted before video submission"), "先把首帧审过（通过或跳过）再生成视频"),
    (re.compile(r"video QC must be accepted before assembly"), "先把所有视频审过再合成成片"),
    (re.compile(r"all frame and video QC items must be accepted before assembly"), "首帧和视频都审过之后才能合成成片"),
    (re.compile(r"episode source changed after the run was frozen in generation-affecting fields \((.+?)\); create an explicit revision"),
     r"开始制作之后改到了会影响生成的字段（\1）——这种改动要开一个新版本"),
    (re.compile(r"episode source changed after the run was frozen; create an explicit revision"),
     "开始制作之后项目内容被改过，与冻结时不一致——开一个新版本，或者把改动撤回"),
    (re.compile(r"frozen episode snapshot changed on disk"), "冻结快照文件被外部改过，账本对不上——不要手动编辑 manifests 目录"),
    # 账本有 run、冻结快照不在：整个输出目录被搬走/删掉了（不是谁改了快照）
    (re.compile(r"ledger has a run for (\S+) but its frozen snapshot is gone; restore the output directory or use a new id"),
     r"这个 id（\1）之前开始制作过，输出目录已不在，账本里还留着记录——从归档恢复、把目录搬回来，或换一个 id"),
    (re.compile(r"another process owns the production lock: (.+)"), r"另一个进程正在操作这个项目（\1）——等它结束，或者关掉多开的窗口"),
    (re.compile(r"cannot retake (\S+) while its state is ([A-Za-z_]+)"), r"\1 现在处于「\2」状态，还不能重拍——等它生成完或失败后再重拍"),
    (re.compile(r"cannot retake non-terminal job: (\S+)"), r"\1 还在生成中，不能重拍——等它结束"),
    (re.compile(r"another retake is still pending: (.+)"), r"上一次重拍还没完成（\1）——先重新生成它们"),
    (re.compile(r"finish the pending retake before recording another QC decision: (.+)"), r"还有待重拍的镜次（\1），先重新生成它们再审别的"),
    (re.compile(r"(\w+) already has a different pending retake"), r"这一道审片已经有别的镜在等重拍，先处理完它们"),
    (re.compile(r"(\w+)/(\S+) has not passed technical QC"), r"\2 的文件没通过技术检查（分辨率/时长/文件有效性），先重新生成"),
    # review 拒绝时带着 _technical_qc_current 算出的具体原因（括号里）：按原因分流，别一律说「文件变了」
    (re.compile(r"(\w+) artifacts changed; run technical QC again \(\w+ QC has no technical result for: (.+)\)"),
     r"这些镜还没有生成结果，没法审：\2"),
    (re.compile(r"(\w+) artifacts changed; run technical QC again \(\w+ QC has no item ledger\)"), "这一道还没有可审的产物——先生成"),
    (re.compile(r"(\w+) artifacts changed; run technical QC again \((\S+) is (\w+), not succeeded\)"), r"\2 还没生成完（现在是「\3」），生成完再审"),
    (re.compile(r"(\w+) artifacts changed; run technical QC again \(\w+/(\S+) artifact is unavailable: .+\)"),
     r"\2 的文件不见了——重新生成，或把文件放回来后点「生成」重新做技术检查，不花钱"),
    (re.compile(r"(\w+) artifacts changed; run technical QC again"),
     r"产物文件变了，需要重新走一遍技术检查——点「生成首帧 / 生成视频」会重新检查已有文件，不花钱"),
    (re.compile(r"unknown QC gate: (.+)"), r"没有这道审片闸门：\1（只有 frames / videos / final）"),
    (re.compile(r"unsupported QC decision: (.+)"), r"不支持的审片裁决：\1（只能是 pass / retake / edit_rescue / redesign / waive）"),
    (re.compile(r"final artifacts cannot be retaken; use edit_rescue or redesign"),
     "成片不能直接记重拍——回镜头页重拍出问题的镜次，或记 edit_rescue / redesign"),
    (re.compile(r"shot selection must not be empty"), "没有选中任何镜次"),
    (re.compile(r"unsupported retake disposition: (\S+?)（可选：(.+?)）"), r"重拍处置「\1」不在可选范围（可选：\2）"),
    (re.compile(r"unsupported retake defect: (\S+?)（可选：(.+?)）"), r"重拍缺陷「\1」不在可选范围（可选：\2）"),
    (re.compile(r"media artifact does not exist: (?:.*[\\/])?([^\\/]+)$"), r"文件不见了：\1——重新生成，或重新导入已有产物"),
    (re.compile(r"shot (\S+)\.lines\[(\d+)\]\.en must be a non-empty string"), r"双语字幕要求每句台词都有英文：镜 \1 第 \2 句缺 en"),
    (re.compile(r"run_id must be a 1-128 character slug containing only letters, digits, dot, underscore, and hyphen"),
     "项目 id 只能用英文、数字、下划线、点、短横线，且首尾得是英文或数字"),
    (re.compile(r"(\w+) technical QC is incomplete"), r"技术检查还没跑完，稍后再审"),
    (re.compile(r"(\w+) gate has no technically validated artifacts"), r"这一道还没有可审的产物——先生成"),
    (re.compile(r"(\w+) gate has no reviewed artifacts"), r"这一道还没有审过的产物"),
    (re.compile(r"unknown (\w+) QC items: (.+)"), r"这些镜次不在待审列表里：\2"),
    (re.compile(r"unknown shot ids: (.+)"), r"没有这些镜次：\1"),
    (re.compile(r"(\w+) requires a reason"), r"「\1」需要写原因"),
    (re.compile(r"retake intent requires a reviewer and reason"), "重拍需要写原因"),
    (re.compile(r"retry only accepts definitive failed jobs: (.+)"), r"只有明确失败的任务才能重提：\1"),
    (re.compile(r"no failed jobs were selected for retry"), "没有失败的任务可以重提"),
    (re.compile(r"legacy artifacts exist without durable StoryVideo state"), "本地已有生成过的文件但账本里没登记——先点「导入已有产物」"),
    (re.compile(r"run storyvideo import-legacy explicitly before using the new runner"), "本地已有生成过的文件但账本里没登记——先点「导入已有产物」"),
    (re.compile(r"legacy import requires explicit confirmation of existing artifacts"), "导入已有产物需要确认"),
    (re.compile(r"submission rejected for (\S+): (.+)"), r"\1 被服务商拒绝：\2"),
    (re.compile(r"submission outcome for (\S+) is unknown; refusing retry: (.+)"),
     r"\1 提交后没拿到结果（\2）——不确定服务商是否收到，不会自动重提；去服务商控制台对账后再重提"),
    (re.compile(r"failed to upload dependency for (\w+)/(\S+): (.+)"), r"\2 的参考图没能编码/上传：\3"),
    (re.compile(r"failed to download/validate (\S+): (.+)"), r"\1 的结果下载或校验失败：\2"),
    (re.compile(r"could not poll (\S+): (.+)"), r"查询 \1 的进度失败：\2"),
    (re.compile(r"submission outcome is unknown for (\S+)"), r"\1 提交后没拿到结果，不确定服务商是否收到——在账本里对账后再重提"),
    (re.compile(r"video job has no persisted first-frame asset: (\S+)"), r"\1 还没有可用的首帧，先生成首帧"),
    (re.compile(r"missing rendered shot: (.+)"), r"缺少视频文件：\1"),
    # spec.normalize_spec：字幕语言是双语 / 英文时每句台词都要有 en（改字幕块时撞到的多半是这条，指向的却是台词字段）
    (re.compile(r"shot (\S+)\.lines\[(\d+)\]\.(en|zh) must be a non-empty string"),
     r"镜 \1 的 lines[\2] 缺 \3 文本——字幕语言要求每句台词都有这一栏：双语要 zh 和 en，英文要 en，中文要 zh"),
    # spec.normalize_spec：说话人不在人物表里 / 说话方式不是认识的值（DRAMA-11）
    (re.compile(r"shot (\S+)\.lines\[(\d+)\]\.who is not in cast: (.+)"),
     r"镜 \1 的 lines[\2] 的说话人「\3」不在人物表里——从人物表里选一个（用 id 或角色名），旁白写 narrator"),
    (re.compile(r"shot (\S+)\.speech_mode must be one of: (.+)"),
     r"镜 \1 的 speech_mode 只能是 \2，留空 = 画内说话"),
    (re.compile(r"assembly failed: (.+)"), r"成片拼接失败：\1"),
    (re.compile(r"local assembly preflight failed: (.+)"), r"成片拼接前检查失败：\1"),
    (re.compile(r"local media command failed: (.+)"), r"本地 ffmpeg 命令失败：\1"),
    (re.compile(r"UI font unavailable: (.+)"), r"插卡需要的中文字体不可用：\1"),
    (re.compile(r"a generation provider is required"), "生成模型没配置——去「模型设置」"),
    (re.compile(r"(\w+) 模型未配置：(.+)"), r"\1 模型未配置：\2"),
    # 项目文件找不到 / 读不了 / 不是合法 JSON：原文带本机绝对路径，人话只留文件名，原文不附（见 _NO_ECHO）
    (re.compile(r"episode spec not found: (?:.*[\\/])?([^\\/]+)$"), r"找不到项目文件：\1"),
    (re.compile(r"invalid JSON in episode spec (?:.*[\\/])?([^\\/]+?) at line (\d+), column (\d+)"),
     r"项目文件不是合法的 JSON：\1 第 \2 行第 \3 列"),
    (re.compile(r"could not read episode spec (?:.*[\\/])?([^\\/]+?): (.+)"), r"读不了项目文件 \1：\2"),
    (re.compile(r"output_root must end with the exact episode id"), "output_root 必须以项目 id 结尾"),
]


# 失败原因分类：账本里 job.error 的文本（runtime 记录时前缀异常类名；轮询到的失败是服务商原话）
def _status_code(code: int) -> str:
    """只认 HTTP 状态码的形状：HTTP 402 / HTTP/1.1 429 / HTTP Error 429 / HTTP错误429 / status=402 / status_code: 429 /
    Request failed with status code 429（axios，node 写的命令行工具）/ "code": 402 / Error code: 402 / 状态码 402。
    以前在整段文案里找裸子串 "402"：「第 1402 帧」、request_id、URL 里的任务号都会被说成「余额耗尽」、劝人去充值
    （2026-09-23 发布前加固）。前后都不能再挨着数字。

    词头不用 ``\\b``：中文字在 Python 的正则里也算「词字符」，「服务商返回HTTP 429」里「回」和「H」之间没有词边界，
    状态码整个认不出来——只要求前面不是 ASCII 字母数字（文本已经转成小写）。"""

    head = r"(?<![a-z0-9_])"
    return (rf"(?:{head}http(?:/[\d.]+)?[\s_-]*(?:error|错误|status(?:[\s_-]?code)?|状态码)?\s*[:=：]?\s*"
            rf"|{head}(?:status(?:[\s_-]?code)?|http[\s_-]?status|error[\s_-]?code)[\"']?\s*[:=：]?\s*[\"']?"
            rf"|{head}code[\"']?\s*[:=]\s*[\"']?"
            rf"|(?:状态码|错误码)\s*[:：=]?\s*)(?<!\d){code}(?!\d)")


_BASE_FAILURE_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 本地配置错误（角色没配 / 缺密钥 / 缺模型 / 选的来源没装上）：一次请求都没发出去，配好再重提就行。
    # 本机缺少来源需要的程序（ProviderSetupRequired）也归这档：它排在 timeout 前面，免得说成「网络超时，多半是临时的」
    # ——不装好（或在「模型设置」里换个来源）重提多少次都一样。来源自己的措辞由插件用 add_failure_hints 补
    ("config", ("providernotconfigured", "providersetuprequired", "模型未配置", "no model configured", "没选适配器",
                "没有装上", "现在用不了")),
    # 瞬时限流（限流码 / 明说限流的字眼）：排在配额前面——它以前被一律说成「余额不足」叫人去充值（2026-09-22 审计 ADP-07）
    ("ratelimit", ("providerratelimited", r"rate[\s_-]?limit", "too many requests", "限流", "过于频繁", "太频繁", "频率超限")),
    # 余额 / 配额：只认明确的字眼和 402 状态码。裸的 insufficient / balance / credits 也不算——「insufficient memory」
    # 「white balance」是参数或显存问题，说成欠费会让人白跑一趟充值
    ("quota", ("quotaexhausted", "quota", r"insufficient[\s_-]*(?:account[\s_-]*)?(?:balance|credits?|funds?)",
               r"payment[\s_-]*required", "overdue", "arrear",
               r"(?:not enough|out of|no remaining|zero)\s+(?:\w+\s+)?credits?\b", r"\bcredits?\s+(?:exhausted|depleted|used up|insufficient)",
               r"(?:insufficient|not enough|low|zero|negative)\s+(?:account\s+)?balance",
               r"balance[\s_-]*(?:is\s+)?(?:insufficient|not[\s_-]*enough|too low|exhausted)",
               "余额", "配额", "欠费", "积分不足", "积分已用完", "积分耗尽", "额度不足", "额度已用完", "额度用完", "额度耗尽",
               _status_code(402))),
    # 裸的 429（响应体里没说是限流还是欠费）：排在配额之后——「HTTP 429: insufficient_quota」是欠费，其余按限流、稍后重试
    ("ratelimit", (_status_code(429),)),
    # 「policy」只认内容 / 使用 / 安全政策与「违反政策」：「Access denied by CORS policy」「retry policy」不是审核拦截
    # （审核类默认不给重提）；「blocked due to policy violation」「PolicyViolation」「violates our policies」是
    ("moderation", ("providerrejected", "moderation", "flagged", r"(?:content|usage|safety)[\s_-]*polic",
                    r"polic(?:y|ies)[\s_-]*violat", r"violat\w*\s+(?:\w+\s+){0,3}polic", "sensitive", "safety",
                    "违规", "敏感", "审核", "不合规")),
    ("timeout", ("providertimeout", "timeout", "timed out", "deadline", "超时", "connection", "unreachable", "providerunavailable")),
)
_failure_cache: tuple[int, list[tuple[str, re.Pattern[str]]]] | None = None


def _failure_kinds() -> list[tuple[str, re.Pattern[str]]]:
    """分类表 = 内置关键词 + 已加载插件补的关键词（并进同一档第一次出现的那一行，排序不变）；按插件登记的代数缓存。"""

    global _failure_cache
    from genkit import plugins

    plugins.ensure_loaded()
    generation = plugins.generation()
    if _failure_cache is None or _failure_cache[0] != generation:
        extra: dict[str, list[str]] = {}
        for kind, needle in plugins.failure_hints():
            extra.setdefault(kind, []).append(needle)
        table: list[tuple[str, re.Pattern[str]]] = []
        for kind, needles in _BASE_FAILURE_KINDS:
            table.append((kind, re.compile("|".join((*needles, *extra.pop(kind, ()))), re.I)))
        _failure_cache = (generation, table)
    return _failure_cache[1]


FAILURE_ADVICE = {
    "config": {"label": "模型没配置", "advice": "去「模型设置」把这个角色配好，再点「重提失败任务」", "retryable": True},
    "ratelimit": {"label": "被服务商限流", "advice": "等几十秒后点「重提失败任务」", "retryable": True},
    "quota": {"label": "余额或配额不足", "advice": "服务商账户充值/等配额恢复后，点「重提失败任务」", "retryable": True},
    "moderation": {"label": "内容被审核拦截", "advice": "原样重提大概率还会被拦——改写这一镜的画面/台词描述后再重拍", "retryable": False},
    "timeout": {"label": "网络超时或服务不可用", "advice": "多半是临时的，直接点「重提失败任务」", "retryable": True},
    "other": {"label": "服务商报错", "advice": "看原文；参数越界的先改 spec，偶发错误直接重提", "retryable": True},
}


def classify_failure(text: str | None) -> dict:
    """job.error → {kind, label, advice, retryable}。看不出来归 other（可重提）。"""

    low = str(text or "").lower()
    for kind, pattern in _failure_kinds():
        if pattern.search(low):
            return {"kind": kind, **FAILURE_ADVICE[kind]}
    return {"kind": "other", **FAILURE_ADVICE["other"]}


# 这几条不在括号里附原文：media 那条原文带本机绝对路径（用户名、数据根），人话已经把有用的部分（文件名）说了；
# 重拍处置 / 缺陷那两条原文自带一对括号，再套一层括号界面翻译就拆不开了，而人话已经把可选项原样列出
_NO_ECHO = ("media artifact does not exist", "unsupported retake disposition", "unsupported retake defect",
            "episode spec not found", "invalid JSON in episode spec", "could not read episode spec")


def humanize(exc: BaseException | str) -> str:
    """翻译一条异常文案；匹配到的规则给人话并把原文附在括号里，否则原样返回。"""

    text = str(exc)
    for pattern, replacement in _RULES:
        match = pattern.search(text)
        if match:
            try:
                translated = match.expand(replacement)
            except (re.error, IndexError):
                translated = replacement
            if pattern.pattern.startswith("submission rejected for"):
                translated += "。" + classify_failure(text)["advice"]
            if pattern.pattern.startswith(_NO_ECHO):
                return translated
            return f"{translated}（{text}）" if translated != text else text
    return text


def classify_failure_localized(text: str | None) -> dict:
    """classify_failure 的 label/advice 按当前界面语言翻译（账本里存的仍是中文源文案）。"""

    from .i18n import tr

    info = classify_failure(text)
    return {**info, "label": tr(info["label"]), "advice": tr(info["advice"])}


__all__ = ["humanize", "classify_failure", "FAILURE_ADVICE"]
