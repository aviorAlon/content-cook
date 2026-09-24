"""镜头正文的静态 lint（2026-09-08，融合 zenstory drama-skills 的判据，不取它的语法）。

全部是告警不是报错，走 prepare / 项目详情页现有的告警通道。每条都给改法，不只报"有问题"。

* **点名覆盖**（drama-skills「视觉依据」）：正文点名了某个角色但本镜 ``cast`` 没列——它的外形描述和锚图
  不会进提示词；反过来 cast 里列了、正文从没提——确认它到底在不在画面里。``cast.<id>.aliases`` 可加别名，
  名字与常用词同形时可设 ``aliases: []`` 且 ``name_in_prompt: false`` 关闭这条。
* **可生成性**（VID-21）：四类高风险写法——不可见的内部状态（像是听见了什么）、否定式动作（没有接话）、
  精细手部编排（一镜三个以上手部动作）、精确拦截（挡住/截住/抢过）。"戏剧信息来自常见动作的组合与时机，
  不来自动作本身的精巧。"
* **正文卫生**（VID-22）：镜号、文件名、版本号、"同上/见上一镜"不该进提示词——执行端不认识它们；
  首帧/视频正文过长时多出的通常是连接词。
* **镜头功能**（相邻镜功能重复）：相邻两镜 ``purpose`` 相同且景别词相同 → 两镜在交代同一件事，读起来像跳切。
* **机位三栏**（production-shot-grammar §五）：有人物的镜写了景别没写机位高度/角度 → 模型自选。
* **时长留白**（VID-04）：beats 的并集明显短于 dur → "未分配的余量不会是静止画面，执行端会用未经批准的
  动作填满"；beats 相互重叠而没标 ``parallel`` → 时序写错。
* **连续性锁**（continuity-lock）：``cast.<id>.locks`` 的锁面必须原样出现在每条生效提示词（首帧 + 视频）里——
  忽略大小写、换行当空格；英文锁面不能粘在别的词上（``chipped`` 不被 ``unchipped`` 满足）；写在「不要出现」/
  ``Avoid:`` 之后的不算，出现在负向词里更是反着来。锁面过长、一个成员锁太多、范围里的镜不存在也提醒。
* **画面文字政策**（文字政策 / IMG-07）：``readable_text`` 过长（模型对长文字容易乱码）；``art`` / ``neg_extra`` 里
  的「无文字」与本镜声明的可读文字互相打架；整集提醒一次：可读文字靠出图模型渲染，中文字形不稳要核对。
* **交付面遮挡区**（DESIGN §27）：``delivery.overlay_regions`` / ``overlay_preset`` 配了平台的状态栏/互动栏/文案区，
  可读文字的 ``position`` 落在里面、或字幕块落在里面 → 「会被交付面挡住」；位置描述只做粗略映射，没方位词的不判。
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .continuity import FRAMING_WORDS, FRAMING_WORDS_EN, _parse_span, _text
from .spec import SCENE_TYPES, split_cast_ref

# ---- 可生成性词表
INFERENCE_WORDS = ("像是", "似乎", "仿佛", "好像", "意识到", "想起", "明白了", "察觉", "感觉到", "听见了什么", "神色一凛", "心里")
NEGATED_ACTION = re.compile(r"(没有|不去|不再|并未|没去|不肯|不愿)(接|看|说|动|答|回|理|碰|拿|开|按|走|抬|转|笑|哭)")
HAND_VERBS = ("握", "捏", "拿", "递", "接", "放", "抓", "翻", "撕", "拧", "系", "解", "按", "敲", "掀", "夹", "摸", "拨", "折", "掏")
INTERCEPT_WORDS = ("挡住", "截住", "抢过", "拦住", "夺过", "扑住", "接住飞来")
HAND_VERB_LIMIT = 3          # 一镜 ≤5 秒时，不同手部动词超过这个数就是精细编排
HAND_VERB_DUR = 5.0

# ---- 正文卫生
SHOT_REF = re.compile(r"\bS\d{1,3}(?:_[A-Z0-9_]+)?\b")
FILE_REF = re.compile(r"\.(png|jpe?g|mp4|webp|json)\b", re.IGNORECASE)
VERSION_REF = re.compile(r"\b[vV]\d+\b|(?:第|版本)\s*\d+\s*版")
SAME_AS_ABOVE = ("同上", "见上一镜", "同前", "如前", "同上一镜", "与上一镜相同的", "不变（见")
PROMPT_LENGTH_HINT = {"frame": 700, "video": 450}   # 字符数：drama-skills 关键帧约 500 字、视频正文 200–300 字

# ---- 镜头功能
# 按一场戏大致的推进排：先交代、再给证据和关系、然后加压出事、最后收
PURPOSES = ("establish", "detail", "power", "pressure", "impact", "shift", "reaction", "aftermath", "exit")
PURPOSE_LABELS = {"establish": "建立", "detail": "细节/证据", "power": "权力关系", "pressure": "施压", "impact": "冲击",
                  "shift": "转折", "reaction": "反应", "aftermath": "余波", "exit": "出场/收束"}

# ---- 机位三栏
HEIGHT_WORDS = ("平视", "俯拍", "仰拍", "俯视", "仰视", "低机位", "高机位", "顶视", "地平", "齐眼", "视平线", "压低", "抬高", "俯", "仰")
HEIGHT_WORDS_EN = ("eye level", "eye-level", "high angle", "low angle", "top-down", "bird", "ground level", "overhead", "worm")
ANGLE_WORDS = ("正面", "侧面", "斜侧", "背面", "后斜", "正侧", "四分之三", "过肩", "3/4", "反打")
ANGLE_WORDS_EN = ("frontal", "profile", "three-quarter", "3/4", "from behind", "over-the-shoulder", "over the shoulder", "reverse")

_CJK = re.compile(r"[一-鿿]")

# ---- 画面文字政策
READABLE_TEXT_MAX_CHARS = 24
NO_TEXT_PHRASES = ("无文字", "没有文字", "不出现文字", "不要文字", "不出现任何文字", "无任何文字", "不能有文字", "不带文字",
                   "no text", "without text", "text-free", "no readable text", "no lettering", "no writing")
PRONOUNS = re.compile(r"[她他它]|\b(she|he|they|her|him)\b", re.IGNORECASE)


def _person_cast(spec: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(k): m for k, m in (spec.get("cast") or {}).items() if isinstance(m, Mapping) and m.get("name")}


def _names_of(member: Mapping[str, Any]) -> list[str]:
    if member.get("name_in_prompt") is False:
        return []
    names = [str(member.get("name") or "").strip()]
    names += [str(a).strip() for a in (member.get("aliases") or ()) if str(a).strip()]
    return [n for n in names if len(n) >= 2]


def _shot_blob(shot: Mapping[str, Any], *keys: str) -> str:
    parts = [_text(shot.get(k)) for k in keys]
    for beat in shot.get("beats") or ():
        if isinstance(beat, Mapping):
            parts.extend(_text(beat.get(k)) for k in ("picture", "performance"))
    return " ".join(p for p in parts if p)


def check_cast_mentions(spec: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    people = _person_cast(spec)
    if not people:
        return warnings
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        sid = _text(shot.get("id"))
        listed = {split_cast_ref(c)[0] for c in (shot.get("cast") or ())}
        blob = _shot_blob(shot, "shot", "art", "motion", "entry_state", "exit_state")
        for cid, member in people.items():
            names = _names_of(member)
            mentioned = any(n in blob for n in names)
            if mentioned and cid not in listed:
                warnings.append(f"shot {sid}: 正文点名了「{member['name']}」，但本镜 cast 没有 {cid}——它的外形描述和锚图不会进提示词；加进出场角色，或改成不点名")
            elif cid in listed and names and not mentioned and blob and not PRONOUNS.search(blob):
                # 正文用"她/他"指代的算提到了；完全没有人称、也没点名才提醒
                warnings.append(f"shot {sid}: cast 列了「{member['name']}」，正文一次没提——确认它在画面里（背影、局部也要写出来），否则从出场角色里去掉")
    return warnings


def check_generability(spec: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        sid = _text(shot.get("id"))
        blob = _shot_blob(shot, "motion", "art", "entry_state", "exit_state")
        if not blob or not _CJK.search(blob):
            continue
        hits = sorted({w for w in INFERENCE_WORDS if w in blob})
        if hits:
            warnings.append(f"shot {sid}: 「{'、'.join(hits)}」是看不见的内部状态，模型只能瞎演——改成可见动作：停住、转头、手僵住、视线落到哪")
        neg = sorted({m.group(0) for m in NEGATED_ACTION.finditer(blob)})
        if neg:
            warnings.append(f"shot {sid}: 否定式动作「{'、'.join(neg)}」模型不会执行「不做」——写它此刻在做的事（继续盯着杯子、手停在半空）")
        inter = sorted({w for w in INTERCEPT_WORDS if w in blob})
        if inter:
            warnings.append(f"shot {sid}: 「{'、'.join(inter)}」这类精确拦截动作生成失败率高——换承担者、拆到相邻镜，或让动作在起点/终点已完成")
        dur = float(shot.get("dur") or 0)
        motion = _text(shot.get("motion"))
        hand_hits = [v for v in HAND_VERBS if v in motion]
        if dur and dur <= HAND_VERB_DUR and len(hand_hits) > HAND_VERB_LIMIT:
            warnings.append(f"shot {sid}: {dur:g} 秒里有 {len(hand_hits)} 个手部动作（{'、'.join(hand_hits)}）——精细编排链最容易崩手；留一个主动作，其余拆镜或写成已完成状态")
    return warnings


def check_prompt_hygiene(spec: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        sid = _text(shot.get("id"))
        blob = _shot_blob(shot, "shot", "art", "motion", "entry_state", "exit_state")
        if not blob:
            continue
        refs = sorted({m.group(0) for m in SHOT_REF.finditer(blob)})
        if refs:
            warnings.append(f"shot {sid}: 正文里有镜号「{'、'.join(refs)}」——模型不认识镜号，把那一镜的事实写出来")
        if FILE_REF.search(blob):
            warnings.append(f"shot {sid}: 正文里有文件名——参考图走出场角色/参考镜，不在文字里指路径")
        if VERSION_REF.search(blob):
            warnings.append(f"shot {sid}: 正文里有版本号——那是给人看的，不该进提示词")
        same = [w for w in SAME_AS_ABOVE if w in blob]
        if same:
            warnings.append(f"shot {sid}: 「{'、'.join(same)}」要往回翻才解释得通，模型没有上一镜——把不变的事实再写一遍（一句即可）")
        meta = [w for w in META_INSTRUCTIONS if w in blob]
        if meta:
            warnings.append(f"shot {sid}: 正文里有生产说明「{'、'.join(meta)}」——那是写给人的，模型会把它当画面内容画进去或念出来；从提示词里删掉，要交代的写进 design_notes")
    return warnings


# ---- 画面里会带字的物件（剧本工艺清单的分镜层判据，docs/script-craft-gap.md）：正文提到了，却既没声明可读文字也没写无文字
TEXT_CARRIERS = ("招牌", "门牌", "路牌", "指示牌", "告示", "标语", "横幅", "弹窗", "短信", "聊天记录", "聊天界面", "消息界面", "屏幕文字",
                 "屏幕上的字", "报纸", "合同", "字条", "便签", "便条", "信纸", "书页", "菜单", "价签", "证件", "诏令", "公告", "榜单",
                 "成绩单", "病历", "账单", "收据", "发票", "请柬", "判决书", "锦旗", "牌匾")
# 写给人看的制作备注混进了提示词（模型不分读者，会照着画或念）。本仓库自拟的四类：备注式开头、冲着模型说话、
# 还没定稿的记号、留到以后的活；命中的按这里的先后报
META_INSTRUCTIONS = ("注意：", "备注：", "注：", "旁注：",
                     "务必保持", "请模型", "让模型", "提示词里",
                     "此处待定", "待补充", "TODO",
                     "剪辑时", "后面的剧情", "留到下一版")
# 一镜里抢戏的几种运镜：同一段生成里两种运镜会互相抢，按族归类，同一镜正文里出现两族就提（族名按这里的先后报）
CAMERA_MOTION_FAMILIES = {
    "推": ("推近", "推向", "轻推", "缓推", "推镜"),
    "拉": ("后拉", "拉镜", "拉远"),
    "横摇": ("摇摄", "平移镜头", "摇镜", "摇臂", "横移"),
    "跟": ("跟镜", "跟拍", "跟随拍"),
    "环绕": ("环拍", "绕拍", "环绕"),
}
# 重话之后要切到听的人：冲击 / 转折之后不是反应 / 余波就提（观众靠听的人的反应掂量这句话有多重）
IMPACT_PURPOSES = ("impact", "shift")
REACTION_PURPOSES = ("reaction", "aftermath")
# 单镜时长上限：经验值，视频模型单次生成多在 5–10 秒内稳，再长动作容易散。
# 选了形态也照判：check_form 只看全集的平均单镜时长和每分钟镜数，不看单镜。
SHOT_SECONDS_CEILING = 10.0

# ---- 关系场景（剧本工艺清单里「情绪要让人看见」落到五种关系戏上：表白看被表白的人有没有动静、重逢看凭什么认出来、
# 原谅看两人往后算什么且亲近要两边都动、拒绝看怎么把人挡开（挡开之后不能再硬贴）、分手看有没有撤不回的举动）。
# 下面的词表是本仓库按这些判据自拟的经验词，**置信度低**：只在镜写了 scene_type 时判，命中与否都只是提示，不拦截。
# 只有 FORCED_CONTACT 与 INTIMATE_CONTACT 会把命中的词报出来（按表里的先后），其余几张只看有没有命中，顺序无关。
SCENE_TYPE_LABELS = {"confession": "表白", "reunion": "重逢", "forgiveness": "原谅", "rejection": "拒绝", "breakup": "分手"}
# 拒绝场：把人挡开的动作
BOUNDARY_ACTIONS = ("侧身", "别开", "错开", "避开", "挪开", "让开", "挡开", "推开", "抽回", "抽开", "缩回", "松开", "停手",
                    "退到", "退开", "退后", "后退", "往后退", "拉开距离", "站起", "起身", "转身", "走开", "离开")
# 拒绝之后还往上贴的动作：出现即告警，不能写成深情
FORCED_CONTACT = ("强吻", "捏住下巴", "掰过", "抓住手腕", "抓住她的手", "抓住他的手", "死死抓住", "扯住", "拽住", "强行拉",
                  "拦腰抱", "强行抱", "按在墙上", "抱紧不放", "堵住门", "堵在门口", "堵住去路", "不让走", "壁咚", "继续逼近", "步步逼近", "逼近")
# 分手场：做了就撤不回的举动
IRREVERSIBLE_ACTIONS = ("拉黑", "删掉", "删除", "摘下戒指", "戒指摘", "退婚", "离婚", "签字", "还给", "归还", "退回", "钥匙", "搬走",
                        "搬出", "箱子", "行李", "上车", "走出门", "关上门", "门关上", "关门", "转身走", "头也不回", "没回头", "不再回头")
# 原谅场：两人往后算什么
FORGIVENESS_OUTCOMES = ("先做朋友", "做朋友", "再看看", "试一段", "一次机会", "最后一次", "重新开始", "从头", "搬回", "留下", "一起走",
                        "一起回", "牵手离开", "复合", "不原谅", "不再见", "各走各", "分开走", "保持距离", "观察期")
# 原谅场：亲密接触，以及受伤的一方回应 / 两人一起靠近的写法
# 「握住」「靠在」不在这里：分镜正文里更常见的是握杯子、靠栏杆，当亲密接触会误报
INTIMATE_CONTACT = ("拥抱", "抱住", "抱在一起", "亲吻", "吻上", "吻了", "接吻", "牵手", "握住手", "握住她的手", "握住他的手", "相拥", "搂住", "贴着", "额头抵")
RETURN_TOUCH = ("反手", "也伸手", "也抱", "也握", "回握", "回抱", "抬手抱", "抬起手", "环住", "靠进", "把头靠", "主动靠", "迎上",
                "同时靠", "两人同时", "互相", "彼此", "双向")
# 重逢场：凭什么认出来的线索
RECOGNITION_CUES = ("那件", "那枚", "那条", "那块", "同款", "项链", "手表", "围巾", "旧照", "照片", "旧物", "疤", "伤痕", "口哨",
                    "声音", "熟悉的", "小动作", "习惯动作", "背影", "侧脸", "脚步停", "停住", "站住", "停步", "愣住", "认得", "认出")
# 表白场：被表白的人的动静，或写明他没接话
RESPONSE_CUES = ("点头", "摇头", "答应", "拒绝", "笑了", "笑出", "脸红", "红了脸", "眼眶", "哭", "愣住", "僵住", "别过脸", "转过头",
                 "转开", "低下头", "抬起头", "躲开", "避开", "退后", "后退", "伸手", "握住", "抱住", "推开", "转身", "停顿", "不动",
                 "没开口", "没答", "没回", "沉默", "回应")

# ---- 同一组判据的英文词表（英文正文 / 中英混写的镜）：小写短语，按词边界匹配（"back" 不会命中 "background"，
# "kiss" 不会命中 "kisses"，所以动词的常见变形要各写一条）。同样是自拟经验词，**置信度低**；短语里的空格允许换成连字符。
BOUNDARY_ACTIONS_EN = ("steps back", "step back", "stepping back", "backs away", "back away", "backing away", "pulls back", "pull back",
                       "pulls away", "pull away", "pulling away", "moves away", "move away", "turns away", "turn away", "turning away",
                       "walks away", "walk away", "walking away", "leaves the room", "leaves the apartment", "leaves him", "leaves her",
                       "lets go", "pushes him away", "pushes her away", "push him away", "push her away", "shoves him off", "shoves her off",
                       "withdraws her hand", "withdraws his hand",
                       "pulls her hand back", "pulls his hand back", "takes her hand back", "takes his hand back", "keeps her distance",
                       "keeps his distance", "puts distance", "puts space between", "sidesteps", "sidestep", "stands up", "stand up",
                       "gets up", "get up", "recoils", "recoil", "flinches away", "shrinks back", "shrinks away", "retreats", "retreat",
                       "stops his hand", "stops her hand", "holds up a hand", "hand up", "turns her head away", "turns his head away")
FORCED_CONTACT_EN = ("forces a kiss", "forced kiss", "kisses her anyway", "kisses him anyway", "grabs her wrist", "grabs his wrist",
                     "grabs her arm", "grabs his arm", "grabs her hand", "grabs his hand", "grabs her", "grabs him", "grips her wrist",
                     "grips his wrist", "seizes her", "seizes him", "won't let go", "will not let go", "refuses to let go", "doesn't let go",
                     "holds her tight", "holds him tight", "holds on tight", "pins her", "pins him", "pins her against", "pins him against",
                     "presses her against", "presses him against", "pushes her against the wall", "pushes him against the wall",
                     "blocks the door", "blocks the doorway", "blocks her way", "blocks his way", "blocks the exit", "corners her", "corners him",
                     "keeps advancing", "keeps coming closer", "closes in", "closing in", "advances on her", "advances on him",
                     "steps closer", "moves closer", "leans in anyway", "pulls her back", "pulls him back", "yanks her", "yanks him",
                     "drags her", "drags him", "won't let her leave", "won't let him leave", "doesn't let her leave", "doesn't let him leave",
                     "stops her from leaving", "stops him from leaving", "grabs her chin", "tilts her chin", "forces her to look", "kabedon")
IRREVERSIBLE_ACTIONS_EN = ("returns the key", "returns the keys", "hands back the key", "hands back the keys", "gives back the key",
                           "gives back the keys", "leaves the keys", "drops the keys", "puts the keys", "keys on the table",
                           "closes the door", "shuts the door", "door closes", "door shuts",
                           "slams the door", "doesn't look back", "does not look back", "without looking back", "never looks back",
                           "suitcase", "luggage", "packs her bag", "packs his bag", "packed bag", "boxes", "takes off the ring",
                           "takes off her ring", "takes off his ring", "removes the ring", "slides off the ring", "ring off", "blocks his number",
                           "blocks her number", "deletes", "delete", "unfollows", "moves out", "moving out", "calls off the wedding",
                           "cancels the wedding", "breaks off the engagement", "gets in the car", "gets into the car", "drives off",
                           "drives away", "walks out the door", "walks out", "divorce", "signs the papers", "signs the divorce",
                           "turns and walks away", "turns and leaves", "leaves the ring")
FORGIVENESS_OUTCOMES_EN = ("get back together", "gets back together", "back together", "keep some distance", "keep their distance",
                           "some distance", "give it time", "take it slow", "take things slow", "start over", "start again", "fresh start",
                           "go their separate ways", "separate ways", "just friends", "stay friends", "friends for now", "stays",
                           "stay the night", "moves back in", "move back in", "try again", "one more chance", "last chance", "second chance",
                           "from the beginning", "never see each other again", "won't see each other", "walk away together",
                           "leave together", "walk off together", "hand in hand", "not forgiven", "can't forgive", "doesn't forgive",
                           "won't forgive", "not yet", "let's see", "we'll see", "trial period")
INTIMATE_CONTACT_EN = ("hug", "hugs", "hugging", "embrace", "embraces", "embracing", "holds her close", "holds him close", "in his arms",
                       "in her arms", "kiss", "kisses", "kissing", "holds hands", "hold hands", "holding hands", "takes her hand",
                       "takes his hand", "holds her hand", "holds his hand", "wraps his arms", "wraps her arms", "arms around",
                       "foreheads touch", "forehead to forehead", "leans against him", "leans against her", "pressed against")
RETURN_TOUCH_EN = ("hugs back", "hugs him back", "hugs her back", "hug back", "returns the hug", "returns the embrace", "squeezes back",
                   "squeezes his hand", "squeezes her hand", "kisses back", "kisses him back", "kisses her back", "leans in", "leans into",
                   "reaches for him", "reaches for her", "reaches out", "reaches up", "steps into", "moves closer", "closes the distance",
                   "both lean", "both reach", "hold each other", "into each other's arms", "lean into each other", "each other", "one another",
                   "mutual", "at the same time", "in turn", "wraps her arms around him",
                   "wraps his arms around her", "rests her head", "rests his head", "lays her head", "lays his head", "lifts her hand",
                   "lifts his hand", "raises her hand", "raises his hand")
RECOGNITION_CUES_EN = ("recognizes", "recognises", "recognize", "recognise", "recognition", "old photo", "old photograph", "photo",
                       "photograph", "picture", "scar", "scars", "profile", "side of her face", "side of his face", "familiar", "old habit",
                       "habit", "the way he", "the way she", "same gesture", "stops in her tracks", "stops in his tracks", "stops dead",
                       "stops short", "stops walking", "freezes", "freeze", "stands still", "goes still", "same scarf", "same watch",
                       "same necklace", "scarf", "his watch", "her watch", "the watch", "wristwatch", "necklace", "bracelet", "the ring",
                       "her ring", "his ring", "keepsake", "keychain", "whistle", "hums", "humming", "that tune", "her voice", "his voice",
                       "the voice", "from behind", "silhouette", "that jacket", "that coat", "that bag", "mannerism",
                       "little gesture", "small gesture")
RESPONSE_CUES_EN = ("responds", "respond", "response", "no response", "doesn't respond", "does not respond", "doesn't answer",
                    "does not answer", "no answer", "says nothing", "silent", "silence", "nods", "nod", "shakes her head", "shakes his head",
                    "shakes head", "says yes", "says no", "accepts", "refuses", "turns him down", "turns her down", "blushes", "blush",
                    "cheeks flush", "flushes", "flushed", "smiles", "smile", "laughs", "laugh", "turns away", "turns her face",
                    "turns his face", "looks away", "looks down", "looks up", "eyes well", "eyes fill", "tears", "cries", "cry",
                    "steps back", "backs away", "freezes", "frozen", "stiffens", "goes still", "stands still", "doesn't move",
                    "does not move", "pauses", "a beat", "hesitates", "doesn't speak", "does not speak", "lips part", "reaches out",
                    "takes his hand", "takes her hand", "hugs", "embraces", "pushes him away", "pushes her away", "turns around", "dodges",
                    "avoids his eyes", "avoids her eyes", "avoids eye contact")

# ---- 拒绝点：拒绝场里第一次出现的拒绝表态（开口的话，或动作正文里写的「她说不行」）或挡开动作。有拒绝点时，
# 只有排在它之后的强行接触才算「拒绝之后」；之前的算戏的前半段。词表是自拟经验词，**置信度低**，分两档：
# 强拒绝（别碰我 / 放开 / let go / don't touch me…）在正文任何位置都算拒绝点；弱词（不要 / 我不 / 不能 / no / don't / sorry…）
# 在别的句子里太常见（「不能挣脱」「我不知道」「no sound」），只在台词里算，动作正文里要写成开口的话（「她说不要」
# 「says no」「「不行。」」）才算。这样弱词最多把拒绝点提前（多报），不会凭空造出一个靠后的拒绝点把前面的强行接触压掉；
# 但强拒绝词与挡开动作本身用在别的意思上（「转身」「离开」写的是第三人）时仍会误定位，那种情况会漏报。
REFUSAL_STRONG = ("别碰我", "放开", "松手", "别过来", "别靠近", "别这样", "不愿意", "拒绝", "不喜欢你")
REFUSAL_WEAK = ("不要", "不行", "我不", "不能", "不可以", "对不起", "算了")
REFUSAL_STRONG_EN = ("let go", "let go of me", "let me go", "don't touch me", "get off", "get off me", "back off", "leave me alone",
                     "not interested", "stop it", "please don't")
REFUSAL_WEAK_EN = ("no", "don't", "do not", "stop", "sorry", "i'm sorry", "i am sorry", "i can't", "i cannot", "i won't", "we can't",
                   "get away")
# 动作正文里的弱词要写成开口的话才算：说话动词（或开引号）+ 弱词 + 句读 / 闭引号 / 句尾（英文还接受 and / but / then）。
# 「她说我不知道」「doesn't say no」「says no one came」都不算。
# 裸的「道」不算说话动词（「知道不行」「难道不能」会误判）；英文动词组要有左词边界（"essays no" 不算 "says no"）
_SPOKEN_WEAK_ZH = re.compile(
    r"(?:(?:说|喊|吼|叫|嚷|开口|说道|喊道)[：:，,]?\s*[「『“\"']?|[「『“])\s*(?P<w>" + "|".join(map(re.escape, REFUSAL_WEAK)) + r")"
    r"\s*(?=[，。！？…；、」』”\"'）)\n]|$)")
_SPOKEN_WEAK_EN = re.compile(
    r"(?:(?<![a-z])(?:says|said|tells|told|whispers|whispered|shouts|shouted|mutters|muttered|mouths|mouthed|screams|screamed|repeats|repeated)"
    r"\s*(?:him|her)?\s*[,:]?\s*[\"']?|[\"'])\s*(?P<w>" + "|".join(sorted(map(re.escape, REFUSAL_WEAK_EN), key=len, reverse=True)) + r")"
    r"(?![a-z0-9])(?!'[a-z0-9])\s*(?=[,.;:!?…\"')\n]|$|(?:and|but|then)\b)")


def check_text_carriers(spec: Mapping[str, Any]) -> list[str]:
    """正文提到文字载体（招牌 / 弹窗 / 短信…），本镜既没声明 ``readable_text`` 也没写「无文字」→ 模型会自己编字。"""

    warnings: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping) or shot.get("readable_text"):
            continue
        sid = _text(shot.get("id"))
        blob = _shot_blob(shot, "shot", "art", "motion")
        if not blob:
            continue
        low = (blob + " " + _text(shot.get("neg_extra"))).lower()
        if any(p in low for p in NO_TEXT_PHRASES):
            continue
        hits = [w for w in TEXT_CARRIERS if w in blob]
        if hits:
            warnings.append(f"shot {sid}: 正文里有文字载体「{'、'.join(hits)}」，但没声明可读文字，也没写无文字——模型会自己编字，中文字形常乱；用 readable_text 声明该出现的字，或在美术里写明载体上无可辨文字")
    return warnings


def check_camera_motion_single(spec: Mapping[str, Any]) -> list[str]:
    """同一镜正文里写了两族以上运镜（推 + 环绕）→ 模型会二选一或来回晃。"""

    warnings: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        sid = _text(shot.get("id"))
        blob = _text(shot.get("shot")) + " " + _text(shot.get("motion"))
        families = [name for name, words in CAMERA_MOTION_FAMILIES.items() if any(w in blob for w in words)]
        if len(families) > 1:
            warnings.append(f"shot {sid}: 一镜里同时写了「{'、'.join(families)}」这几种运镜——模型会二选一或来回晃；挑一个，另一个挪到下一镜")
    return warnings


def check_reaction_after_impact(spec: Mapping[str, Any]) -> list[str]:
    """purpose 是冲击 / 转折的镜，下一镜（也写了 purpose）不是反应 / 余波 → 强信息落空。两镜都没写 purpose 就不判。"""

    warnings: list[str] = []
    shots = [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]
    for shot, nxt in zip(shots, shots[1:]):
        purpose = _text(shot.get("purpose")).lower()
        following = _text(nxt.get("purpose")).lower()
        if purpose in IMPACT_PURPOSES and following in PURPOSES and following not in REACTION_PURPOSES:
            warnings.append(
                f"shot {_text(shot.get('id'))}: 这一镜是「{PURPOSE_LABELS[purpose]}」，下一镜 {_text(nxt.get('id'))} 是「{PURPOSE_LABELS[following]}」，中间没有反应镜"
                f"——一句重话说完，观众要看到听的人身上的表演（愣住 / 手上一停 / 别开脸 / 退一步），一个形容词顶替不了；加一镜反应，或把反应写进下一镜的开头"
            )
    return warnings


def check_voice_over(spec: Mapping[str, Any]) -> list[str]:
    """旁白与对白不重叠、旁白不超出本镜（两条人声怎么分轨里能直接算出来的两条）。语速沿用 preview 的 4.5 字/秒。"""

    from .preview import CHARS_PER_SECOND, HEAD_SECONDS, shot_duration_budget

    vo = spec.get("voice_over")
    if not isinstance(vo, Mapping):
        return []
    shots = {str(s.get("id")): s for s in spec.get("shots", ()) if isinstance(s, Mapping) and s.get("id")}
    warnings: list[str] = []
    for index, line in enumerate(vo.get("lines") or (), 1):
        if not isinstance(line, Mapping):
            continue
        text = _text(line.get("zh"))
        sid = _text(line.get("shot"))
        if not text or not sid:
            continue
        shot = shots.get(sid)
        if shot is None:
            warnings.append(f"全集：旁白第 {index} 句「{text[:12]}…」指到不存在的镜 {sid}——这句不会被铺进成片；改成现有的镜号")
            continue
        try:
            at = float(line.get("at") or 0.0)
            dur = float(shot.get("dur") or 0.0)
        except (TypeError, ValueError):
            continue
        narration = round(len(text) / CHARS_PER_SECOND, 1)
        end = round(at + narration, 1)
        if dur and end > dur + 0.3:
            warnings.append(f"shot {sid}: 旁白「{text[:12]}…」≈{narration:g}s，从 {at:g}s 起会超出本镜 {dur:g}s——语速按 4.5 字/秒算，这句会盖到下一镜或被截断；缩短、提前，或挪到下一镜")
        budget = shot_duration_budget(shot)
        if budget["dialogue_chars"]:
            talk_start, talk_end = HEAD_SECONDS, round(HEAD_SECONDS + budget["speech_seconds"], 1)
            if at < talk_end and end > talk_start:
                warnings.append(f"shot {sid}: 旁白「{text[:12]}…」（{at:g}–{end:g}s）与对白（约 {talk_start:g}–{talk_end:g}s）同一时段——两条人声叠在一起谁都听不清；旁白挪到对白说完之后（≥ {talk_end:g}s），或放到没台词的镜")
    return warnings


def check_shot_id_case(spec: Mapping[str, Any]) -> list[str]:
    """镜号只差大小写（S1 / s1）：Windows 和 macOS 默认的文件系统不分大小写，两镜的首帧 / 视频写到同一个文件，
    后出的那镜把先出的盖掉（花钱换来的产物没了）。规范化时不硬拒——已有项目会打不开——在告警通道里说清楚。"""

    groups: dict[str, list[str]] = {}
    for shot in spec.get("shots", ()):
        if isinstance(shot, Mapping):
            sid = _text(shot.get("id"))
            if sid:
                groups.setdefault(sid.casefold(), []).append(sid)
    return [f"全集：镜号 {'、'.join(ids)} 只差大小写，Windows / macOS 上这几镜的首帧和视频会写到同一个文件、互相覆盖——"
            f"生成前把它们改成互不相同的镜号"
            for ids in groups.values() if len(ids) > 1]


def check_shot_duration_ceiling(spec: Mapping[str, Any]) -> list[str]:
    """单镜 dur 超过 10 秒 → 提示拆镜（选没选形态都判：``check_form`` 只看全集平均，不看单镜）。"""

    warnings: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        try:
            dur = float(shot.get("dur") or 0)
        except (TypeError, ValueError):
            continue
        if dur > SHOT_SECONDS_CEILING:
            warnings.append(f"shot {_text(shot.get('id'))}: 单镜时长 {dur:g} 秒——视频模型单次生成多在 5–10 秒内稳，再长动作容易散；拆成两镜，每镜只管一件事")
    return warnings


def _scene_parts(shot: Mapping[str, Any]) -> tuple[str, str, str]:
    """关系场景判据看的正文，分成三段：开口的话（中英）、动作（motion、出口状态、beats 的画面 / 表演）、美术。
    拼接时台词排在动作前面——台词里的拒绝表态之后的动作全算「拒绝之后」，宁可多报；弱拒绝词只在台词里直接算数
    （见 ``_rejection_point``）；美术是陈设，排最后且不参与拒绝点定位（英文美术里 "no windows" 之类的 no 太常见，
    当成拒绝表态会把前面的强行接触全压掉）。"""

    parts: list[str] = []
    for line in shot.get("lines") or ():
        if isinstance(line, Mapping):
            parts.extend(_text(line.get(k)) for k in ("zh", "en"))
    # 各段之间用换行拼：开口拒绝的判定要看到句尾（「她说不要」后面紧跟下一段时，换行就是句读）
    action = [_text(shot.get("motion")), _text(shot.get("exit_state"))]
    for beat in shot.get("beats") or ():
        if isinstance(beat, Mapping):
            action.extend(_text(beat.get(k)) for k in ("picture", "performance"))
    return "\n".join(p for p in parts if p), "\n".join(p for p in action if p), _text(shot.get("art"))


_EN_WORD_RES: dict[str, re.Pattern[str]] = {}
_LETTERS = re.compile(r"[一-鿿A-Za-z]")


def _en_word_re(word: str) -> re.Pattern[str]:
    """英文短语的词边界正则：前后不能贴着字母 / 数字，也不能贴着词内的撇号（"back" 不命中 "background"、"don" 不命中
    "don't"、"stop" 不命中 "stop's"），但直单引号引起来的词能命中（"says 'no'"）；短语内的空格可以是任意空白或连字符
    （"eye level" 也命中 "eye-level"）。"""

    pattern = _EN_WORD_RES.get(word)
    if pattern is None:
        body = r"[\s\-]+".join(re.escape(part) for part in word.split())
        pattern = re.compile(rf"(?<![a-z0-9])(?<![a-z]'){body}(?![a-z0-9])(?!'[a-z0-9])")
        _EN_WORD_RES[word] = pattern
    return pattern


def _low(text: str) -> str:
    """小写、弯引号换直引号——都是逐字符替换，位置和原文对得上（中文词照原样比对）。"""

    return text.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


def _spans(words_zh: tuple[str, ...], words_en: tuple[str, ...], low: str) -> list[tuple[int, int]]:
    """两张词表在正文里的全部出现区间（中文按子串、英文按词边界）。"""

    out: list[tuple[int, int]] = []
    for w in words_zh:
        out.extend(m.span() for m in re.finditer(re.escape(w), low))
    for w in words_en:
        out.extend(m.span() for m in _en_word_re(w).finditer(low))
    return out


def _hits(words_zh: tuple[str, ...], words_en: tuple[str, ...], low: str, after: int = -1) -> list[str]:
    """命中的词（中文表在前、各按词表顺序），被更长的命中词包含的不重复报（「逼近」被「继续逼近」包含时只留长的）；
    ``after`` ≥ 0 时只算在这个字符位置之后还出现过的词。中英混写的正文两边都查。"""

    found = [w for w in words_zh if low.find(w, after + 1) >= 0]
    found += [w for w in words_en if _en_word_re(w).search(low, after + 1)]
    return [w for w in found if not any(o != w and w in o for o in found)]


def _hit(words_zh: tuple[str, ...], words_en: tuple[str, ...], low: str) -> bool:
    return any(w in low for w in words_zh) or any(_en_word_re(w).search(low) for w in words_en)


def _rejection_point(low: str, limit: int, lines_end: int | None = None) -> int:
    """拒绝点：``low[:limit]`` 里第一次出现拒绝表态或挡开动作的字符位置，没有就是 -1。强拒绝词与挡开动作在任何位置都算；
    弱词（不要 / 我不 / no / sorry…）只在台词段（``low[:lines_end]``，缺省 = 整段 ``low[:limit]``）里直接算，动作正文里
    要写成开口的话（``_SPOKEN_WEAK_ZH`` / ``_SPOKEN_WEAK_EN``）才算。落在强行接触词里面的不算（"won't let go" 里的
    let go 不是拒绝）。这是按字符位置的粗定位，不是语义顺序——正文是台词 → 动作 → 美术拼起来的。"""

    if lines_end is None:
        lines_end = limit
    forced = _spans(FORCED_CONTACT, FORCED_CONTACT_EN, low)
    candidates = _spans(REFUSAL_STRONG + BOUNDARY_ACTIONS, REFUSAL_STRONG_EN + BOUNDARY_ACTIONS_EN, low)
    candidates += _spans(REFUSAL_WEAK, REFUSAL_WEAK_EN, low[:lines_end])
    candidates += [m.span("w") for pat in (_SPOKEN_WEAK_ZH, _SPOKEN_WEAK_EN) for m in pat.finditer(low)]
    best = -1
    for start, end in candidates:
        # 跟强行接触词有任何重叠的候选都不算（「抱紧不放开」里的「放开」尾部越界一字也不算拒绝）
        if start >= limit or any(start < fe and end > fs for fs, fe in forced):
            continue
        if best < 0 or start < best:
            best = start
    return best


def check_relation_boundaries(spec: Mapping[str, Any]) -> list[str]:
    """只对写了 ``scene_type`` 的镜判（老项目零变化）：表白场被表白的人没动静、重逢场看不出凭什么认出来、
    原谅场没交代往后算什么 / 亲近只有一方在动、拒绝场看不到挡开的动作 / 拒绝点之后还往上贴、
    分手场没有撤不回的举动。中英词表都查（见上），置信度低，全是提示。

    拒绝场的强行接触按「拒绝点」定序：正文里第一次出现拒绝表态（放开 / 别碰我 / let go…，或台词里的不要 / no）或挡开动作
    的位置；有拒绝点时只报排在它之后的强行接触（之前的算戏的前半段），没有拒绝点时同镜出现就报。定位按字符位置，
    不是语义顺序：台词拼在动作前面，所以台词里说了「不要」，动作里的强行接触都算拒绝之后；动作正文自己写了「她说不行 /
    后退」，才能给同一段动作里靠前的接触豁免。「不能挣脱」「我不知道」「no sound」这种不是开口拒绝的弱词不算拒绝点，
    所以弱词只会多报；强拒绝词与挡开动作写的是别的意思时（「离开」说的是第三人）会漏报，词表置信度低。"""

    warnings: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        kind = _text(shot.get("scene_type")).lower()
        if kind not in SCENE_TYPES:
            continue
        sid = _text(shot.get("id"))
        lines, action, art = _scene_parts(shot)
        low_lines = _low(lines)
        low_drama = low_lines + ("\n" + _low(action) if action else "")
        low = low_drama + ("\n" + _low(art) if art else "")
        if not _LETTERS.search(low):
            continue
        label = SCENE_TYPE_LABELS[kind]
        head = f"shot {sid}: 这一镜是「{label}」场景，"
        if kind == "confession":
            if not _hit(RESPONSE_CUES, RESPONSE_CUES_EN, low):
                warnings.append(head + "正文里看不出被表白的一方接没接住：对方没有任何动静，也没交代是故意不答"
                                "——成没成，观众得从对方身上看出来；给对方写一个表演动作（愣住、别过脸、点头、退后），"
                                "或把不答写成看得见的停顿（站着不动、手悬在半空）")
        elif kind == "reunion":
            if not _hit(RECOGNITION_CUES, RECOGNITION_CUES_EN, low):
                warnings.append(head + "正文里看不出是凭什么认出来的（停步 / 一件旧物 / 一道疤 / 一个老习惯 / 背影）"
                                "——观众要跟角色一起认出对方，不能靠事后解释；给认出的一瞬写一个表演动作：停步、看到旧物、看到侧脸")
        elif kind == "forgiveness":
            if not _hit(FORGIVENESS_OUTCOMES, FORGIVENESS_OUTCOMES_EN, low):
                warnings.append(head + "正文里没交代两人往后算什么（和好 / 不和好 / 先隔开一段 / 再看看）"
                                "——原谅了也不一定回到从前，观众要知道两人之后怎么处；用一句开口的话或一个表演动作把结果写明")
            intimate = _hits(INTIMATE_CONTACT, INTIMATE_CONTACT_EN, low)
            if intimate and not _hit(RETURN_TOUCH, RETURN_TOUCH_EN, low):
                warnings.append(head + f"正文里有「{'、'.join(intimate)}」，但只有一方在靠近，受伤的一方没有回应的动作"
                                "——单方面的亲密接触读起来像施压不像和解；写受伤方的回抱 / 回握，或两人同时靠近的表演")
        elif kind == "rejection":
            if not _hit(BOUNDARY_ACTIONS, BOUNDARY_ACTIONS_EN, low):
                warnings.append(head + "正文里看不到拒绝的一方怎么把人挡开（退半步 / 抽手 / 转身 / 走开）"
                                "——光嘴上说不行不够，观众得看见人往外撤；给拒绝的一方写一个表演动作（退半步、把手抽回来、侧过身让开）")
            forced = _hits(FORCED_CONTACT, FORCED_CONTACT_EN, low, after=_rejection_point(low, len(low_drama), len(low_lines)))
            if forced:
                warnings.append(head + f"正文里还有「{'、'.join(forced)}」——对方已经说了不，之后再硬贴上去就是冒犯，不能当深情写；删掉这些动作，"
                                "或让对方接受边界（松手、退开）；真要写越界，就写成冒犯的表演，镜头站在拒绝的一方这边")
        elif kind == "breakup":
            if not _hit(IRREVERSIBLE_ACTIONS, IRREVERSIBLE_ACTIONS_EN, low):
                warnings.append(head + "正文里没有一个做了就撤不回的举动（拎起行李 / 关上门 / 交回钥匙 / 把对方的东西还回去 / 头也不回）"
                                "——光吵架观众分不清是真分还是气话；把一件东西还回去、关一扇门、拎起行李，写成表演动作")
    return warnings


def check_prompt_length(spec: Mapping[str, Any]) -> list[str]:
    """编译后的首帧/视频提示词过长提示（在 prompts 可用时调用；编译失败就跳过）。"""

    try:
        from .prompts import compile_frame_prompt, compile_video_prompt
    except Exception:  # noqa: BLE001
        return []
    warnings: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        sid = _text(shot.get("id"))
        try:
            frame = compile_frame_prompt(spec, sid)
            video = compile_video_prompt(spec, sid)
        except Exception:  # noqa: BLE001 - 编译错误由别的检查报
            continue
        if len(frame) > PROMPT_LENGTH_HINT["frame"] * 2:
            warnings.append(f"shot {sid}: 首帧提示词 {len(frame)} 字——多出的通常是连接词和重复描述；风格与场景固定句已在全局，镜头里只写这一格的事实")
        if len(video) > PROMPT_LENGTH_HINT["video"] * 2:
            warnings.append(f"shot {sid}: 视频提示词 {len(video)} 字——运动段只写起点→唯一动作→终点，删掉任意一句画面没更不确定的就是多的")
    return warnings


def _framing_key(shot_text: str) -> str:
    if _CJK.search(shot_text):
        return next((w for w in FRAMING_WORDS if w in shot_text), "")
    low = shot_text.lower()
    return next((w for w in FRAMING_WORDS_EN if w in low), "")


def check_purpose(spec: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    shots = [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]
    prev: Mapping[str, Any] | None = None
    for shot in shots:
        sid = _text(shot.get("id"))
        purpose = _text(shot.get("purpose")).lower()
        if purpose and purpose not in PURPOSES:
            warnings.append(f"shot {sid}: purpose「{purpose}」不在清单里（{' / '.join(PURPOSES)}）")
        if prev is not None and purpose and purpose == _text(prev.get("purpose")).lower():
            fk, pk = _framing_key(_text(shot.get("shot"))), _framing_key(_text(prev.get("shot")))
            if fk and fk == pk:
                warnings.append(f"shot {sid}: 与上一镜 {prev.get('id')} 功能都是「{PURPOSE_LABELS.get(purpose, purpose)}」、景别都是「{fk}」——功能重复，两镜在交代同一件事，看着像一镜被拆成了两段；换景别/角度，或合并")
        prev = shot
    return warnings


def check_camera_columns(spec: Mapping[str, Any]) -> list[str]:
    """有人物的镜：写了景别但没写机位高度 → 提示（平视也要写，不写 = 模型自选）。"""

    warnings: list[str] = []
    people = _person_cast(spec)
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        sid = _text(shot.get("id"))
        if not any(split_cast_ref(c)[0] in people for c in (shot.get("cast") or ())):
            continue
        text = _text(shot.get("shot")) + " " + _text(shot.get("art"))
        if not _framing_key(_text(shot.get("shot"))):
            continue  # 景别缺失由 check_shot_design 报
        if _CJK.search(text):
            has_height = any(w in text for w in HEIGHT_WORDS)
        else:
            low = text.lower()
            has_height = any(w in low for w in HEIGHT_WORDS_EN)
        if not has_height:
            warnings.append(f"shot {sid}: 有景别没有机位高度（平视/俯拍/仰拍）——「平视」是主动选择，不写等于让模型自选；竖屏里高度变化比横屏更贵，写明")
    return warnings


def check_beat_coverage(spec: Mapping[str, Any]) -> list[str]:
    """beats 并集短于 dur 的留白、以及没标 parallel 的重叠。"""

    warnings: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping) or not shot.get("beats"):
            continue
        sid = _text(shot.get("id"))
        dur = float(shot.get("dur") or 0)
        spans: list[tuple[float, float, bool]] = []
        for beat in shot.get("beats") or ():
            if not isinstance(beat, Mapping):
                continue
            start, end = _parse_span(beat.get("t"))
            if start is None:
                continue
            spans.append((start, min(end, dur) if dur else end, bool(beat.get("parallel"))))
        if not spans:
            continue
        spans.sort()
        covered = 0.0
        cur_s, cur_e = spans[0][0], spans[0][1]
        for s, e, parallel in spans[1:]:
            if s < cur_e - 1e-6 and not parallel:
                warnings.append(f"shot {sid}: beat {s:g}-{e:g} 与前一块重叠——两件事同时发生就标 parallel: true，否则时序写错")
            if s <= cur_e:
                cur_e = max(cur_e, e)
            else:
                covered += cur_e - cur_s
                cur_s, cur_e = s, e
        covered += cur_e - cur_s
        if dur and covered < dur - 0.5:
            warnings.append(f"shot {sid}: beats 只覆盖 {covered:g}s / {dur:g}s——剩下 {dur - covered:g}s 不会是静止画面，模型会用没批准的动作填满；补一块「保持」或缩短 dur")
    return warnings


# ---- 连续性锁
LOCK_SURFACE_MAX = 40        # 锁面只留最小可辨识名词短语；再长就是把描述当锁面了
LOCKS_PER_MEMBER = 5         # "一集里的锁通常是个位数"
NEGATIVE_MARKERS = ("不要出现", "avoid:")   # 之后的文字是在说"别画它"，不算锁面在场
_ASCII_WORD = re.compile(r"[A-Za-z0-9]")


def _flat(text: str) -> str:
    """比较用：换行当空格、多空格并一、忽略大小写。"""

    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _surface_pattern(surface: str) -> re.Pattern[str]:
    """锁面逐字匹配；首/尾是 ASCII 字母数字时，紧挨的字母数字或连字符会让它落空（chipped ≠ unchipped）。"""

    flat = _flat(surface)
    head = r"(?<![A-Za-z0-9-])" if _ASCII_WORD.fullmatch(flat[:1] or " ") else ""
    tail = r"(?![A-Za-z0-9-])" if _ASCII_WORD.fullmatch(flat[-1:] or " ") else ""
    return re.compile(head + re.escape(flat) + tail)


def _positive_part(text: str) -> str:
    """「不要出现」/ ``Avoid:`` 之前的正文——之后写的是不想要的东西，锁面落在那里等于反着说。"""

    flat = _flat(text)
    cut = min((i for i in (flat.find(m) for m in NEGATIVE_MARKERS) if i >= 0), default=-1)
    return flat if cut < 0 else flat[:cut]


def surface_present(text: str, surface: str) -> bool:
    return bool(_surface_pattern(surface).search(_positive_part(text)))


def _lock_shape_ok(lock: Any) -> bool:
    if not isinstance(lock, Mapping) or not _text(lock.get("surface")):
        return False
    scope = lock.get("shots", "all")
    return scope in ("all", None) or (isinstance(scope, list) and all(isinstance(s, str) and s.strip() for s in scope))


def _missing_reason(text: str, surface: str, kind: str, sid: str) -> str:
    flat = _flat(text)
    if _surface_pattern(surface).search(flat):
        marker = next((m for m in NEGATIVE_MARKERS if m in flat), "不要出现")
        marker = "Avoid:" if marker == "avoid:" else marker
        return (f"shot {sid}: {kind}提示词里锁面「{surface}」只出现在「{marker}」之后——那是在叫模型别画它；"
                "正文里不写「不要出现」这类否定，不想要的东西写进负向词（neg / neg_extra）")
    if _flat(surface) in flat:
        return (f"shot {sid}: {kind}提示词里锁面「{surface}」粘在别的词上（前后紧挨着字母或连字符）——"
                "锁面要能作为一个完整词组读出来；改写正文让它独立成词")
    return (f"shot {sid}: {kind}提示词里没有锁面「{surface}」——外形描述或正文改写了它（少一个词、多一个逗号都算漂移）；"
            "让锁面原样出现，或改锁面本身")


def check_locks(spec: Mapping[str, Any]) -> list[str]:
    """连续性锁：形状、数量、范围、以及锁面是否真的原样落进了每条生效提示词（首帧 + 视频），负向词里不该有它。"""

    warnings: list[str] = []
    cast = spec.get("cast") or {}
    shot_ids = [_text(s.get("id")) for s in spec.get("shots", ()) if isinstance(s, Mapping)]
    has_locks = False
    for cid, member in cast.items():
        if not isinstance(member, Mapping):
            continue
        locks = member.get("locks") or ()
        if not isinstance(locks, (list, tuple)):
            warnings.append(f"cast.{cid}.locks 必须是列表：[{{\"surface\": \"锁面\", \"shots\": \"all\"}}]")
            continue
        if len(locks) > LOCKS_PER_MEMBER:
            warnings.append(f"cast.{cid} 有 {len(locks)} 把锁——锁通常是个位数，每条提示词都会被同一串名词撑满、挤掉本镜真正要执行的动作；"
                            "只锁观众看得出前后不一致、且不随剧情变化的事实（颜色、材质、形制），状态和情绪不上锁")
        for index, lock in enumerate(locks):
            if not _lock_shape_ok(lock):
                warnings.append(f"cast.{cid}.locks[{index}] 写法不对——surface 填要逐字进提示词的短语，shots 填 \"all\" 或镜号列表")
                continue
            has_locks = True
            surface = _text(lock.get("surface"))
            if len(surface) > LOCK_SURFACE_MAX:
                warnings.append(f"cast.{cid} 的锁面「{surface[:20]}…」有 {len(surface)} 字——锁面只留最小可辨识名词短语"
                                "（颜色 + 材质/形制 + 物体，如「淡蓝色粗针织羊毛毛衣」），动作、状态、数量、剧情不进锁面")
            scope = lock.get("shots", "all")
            if isinstance(scope, list):
                unknown = [s for s in scope if s not in shot_ids]
                if unknown:
                    warnings.append(f"cast.{cid} 的锁「{surface}」范围里有不存在的镜：{'、'.join(unknown)}——改成现有镜号或从范围里删掉")
    if not has_locks:
        return warnings
    try:
        from .prompts import compile_frame_prompt, compile_negative_prompt, compile_video_prompt, shot_lock_surfaces
    except Exception:  # noqa: BLE001
        return warnings
    for sid in shot_ids:
        try:
            surfaces = shot_lock_surfaces(spec, sid)
            if not surfaces:
                continue
            frame, video, negative = compile_frame_prompt(spec, sid), compile_video_prompt(spec, sid), compile_negative_prompt(spec, sid)
        except Exception:  # noqa: BLE001 - 编译错误由别的检查报
            continue
        for surface in surfaces:
            if not surface_present(frame, surface):
                warnings.append(_missing_reason(frame, surface, "首帧", sid))
            if not surface_present(video, surface):
                warnings.append(_missing_reason(video, surface, "视频", sid))
            if _surface_pattern(surface).search(_flat(negative)):
                warnings.append(f"shot {sid}: 负向词里出现了锁面「{surface}」（neg / neg_extra）——这是在叫模型别画它，与锁的目的相反；从负向词里删掉")
    return warnings


# 锚图文件名的后缀：normalize_spec 已经把存在的项目内相对路径换成了绝对路径，所以归一化后的 spec 里
# 还留着相对路径形状（带目录分隔符 + 图片后缀）的锚图，就是磁盘上找不到那个文件。历史上的远端 asset id
# 不长这样（没有分隔符、没有后缀），不会被误报。
_ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _asset_missing(asset: Any) -> bool:
    """锚图指向的文件在不在。不在时出图会把这个字符串当成服务商的 asset id 原样提交，报错要等提交之后才看得到。"""

    text = _text(asset)
    if not text or text.startswith("local:"):
        return False
    posix = text.replace("\\", "/")
    if posix.startswith("/") or ":" in posix.split("/")[0] or "/" not in posix:   # 绝对路径 / 盘符 / 不是路径形状
        return False
    return "." + posix.rsplit(".", 1)[-1].lower() in _ASSET_SUFFIXES


def check_variants(spec: Mapping[str, Any]) -> list[str]:
    """角色变体（DESIGN §23）：定义了没用上的变体提示；同一成员相邻两镜换了造型、后一镜又没声明
    边界变化（``boundary_change``）→ 「造型在镜外切换」告警。引用不存在的变体在 spec 校验就拒了，这里不重复。"""

    warnings: list[str] = []
    cast = spec.get("cast") or {}
    shots = [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]
    used: dict[tuple[str, str], list[str]] = {}
    for shot in shots:
        for ref in shot.get("cast") or ():
            member_id, variant_key = split_cast_ref(ref)
            if variant_key:
                used.setdefault((member_id, variant_key), []).append(_text(shot.get("id")))
    for cid, member in cast.items():
        if not isinstance(member, Mapping):
            continue
        if _asset_missing(member.get("asset")):
            warnings.append(f"cast.{cid} 的锚图文件不存在：{_text(member.get('asset'))}——重新上传一张，或把 asset 删掉")
        variants = member.get("variants") or {}
        if not isinstance(variants, Mapping):
            continue
        for key, variant in variants.items():
            if isinstance(variant, Mapping) and _asset_missing(variant.get("asset")):
                warnings.append(f"cast.{cid} 的变体「{key}」的锚图文件不存在：{_text(variant.get('asset'))}"
                                "——重新上传一张，或把 asset 删掉")
            if (str(cid), str(key)) in used:
                continue
            label = _text((variant or {}).get("label")) if isinstance(variant, Mapping) else ""
            shown = f"{key}（{label}）" if label and label != key else str(key)
            warnings.append(f"cast.{cid} 的变体「{shown}」没有任何镜用到——在镜头的「出场」里把该角色切到这个造型，或删掉变体")
    # 只看相邻两镜：中间隔了一镜（时间跳跃、别处的戏）换造型是正常剪辑，不报
    prev: dict[str, str | None] = {}   # 上一镜出场的成员 → 变体键
    prev_sid = ""
    for shot in shots:
        sid = _text(shot.get("id"))
        seen_now: dict[str, str | None] = {}
        for ref in shot.get("cast") or ():
            member_id, variant_key = split_cast_ref(ref)
            seen_now.setdefault(member_id, variant_key)
        for member_id, variant_key in seen_now.items():
            if member_id in prev and prev[member_id] != variant_key and not shot.get("boundary_change"):
                name = _text((cast.get(member_id) or {}).get("name")) if isinstance(cast.get(member_id), Mapping) else ""
                who = f"{name}（{member_id}）" if name else member_id
                warnings.append(f"shot {sid}: {who} 的造型从「{prev[member_id] or '基底'}」（{prev_sid}）切成「{variant_key or '基底'}」，"
                                "但这一镜没声明边界变化——造型在镜外切换观众会跳戏；在镜内交代换装/受伤（写进 boundary_change），"
                                "或在两镜之间插一镜过渡")
        prev, prev_sid = seen_now, sid
    return warnings


def check_readable_text(spec: Mapping[str, Any]) -> list[str]:
    """画面文字政策：过长的可读文字、与「无文字」打架、整集一次的渲染风险提醒。"""

    try:
        from .prompts import readable_text_entries
    except Exception:  # noqa: BLE001
        return []
    warnings: list[str] = []
    declared: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping) or not shot.get("readable_text"):
            continue
        sid = _text(shot.get("id"))
        entries = readable_text_entries(shot)
        if not entries:
            warnings.append(f"shot {sid}: readable_text 里没有一条合法条目（每条要有载体 surface 和文字 text）——补全或删掉，否则这镜仍按「画面无文字」出图")
            continue
        declared.append(sid)
        for e in entries:
            if len(e["text"]) > READABLE_TEXT_MAX_CHARS:
                warnings.append(f"shot {sid}: 可读文字《{e['text']}》有 {len(e['text'])} 个字——模型对长文字容易漏字、错字或乱码；拆成几处短文字，或改走后期画面文字")
        blob = " ".join(_text(shot.get(k)) for k in ("art", "neg_extra")).lower()
        hits = [w for w in NO_TEXT_PHRASES if w in blob]
        if hits:
            warnings.append(f"shot {sid}: 美术/负向补充里写了「{'、'.join(hits)}」，与本镜声明的可读文字互相打架（IMG-07）——删掉那句，唯一允许出现的文字已由提示词声明")
    if declared:
        warnings.append(f"shot {'、'.join(declared)}: 可读文字要靠出图模型渲染，很多模型对中文字形不稳，出图后核对，不行就改走后期画面文字")
    return warnings


# ---- 轴线与左右（drama-skills blocking-playbooks §1b，DESIGN §22）
AXIS_SIDES = ("left", "right")
_SIDE_CN = {"left": "左", "right": "右"}
# 裸写的左/右一律指画面左右。只抓方向短语（向左 / 从右侧 / 左边的…）：身体部位（左手、右肩）和「左右（为难）」不算，
# 前面已经带了主体（画面左、她的右、镜头右侧、屏幕左）的不算。保守起见「左前方」这类不抓。
BARE_LR = re.compile(
    r"(?<!画面)(?<!她的)(?<!他的)(?<!它的)(?<!镜头)(?<!屏幕)(?<![左右])"
    r"(?:[向往朝从偏靠][左右](?:侧|边|方)?|[左右](?:侧|边))"
    r"(?![左右手臂肩脸腿脚眼耳膝腕胸颊掌])"
)


def _axis_anchors(axis: Mapping[str, Any]) -> set[str]:
    return {str(a).strip() for a in (axis.get("anchors") or ()) if str(a).strip()}


def _axis_screen(axis: Mapping[str, Any]) -> dict[str, str]:
    screen = axis.get("screen")
    if not isinstance(screen, Mapping):
        return {}
    return {str(k): str(v or "").strip().lower() for k, v in screen.items()}


def check_axis(spec: Mapping[str, Any]) -> list[str]:
    """shot.axis 的形状；相邻镜锚点相同、某人的画面侧翻了又没标 break → 越轴；正文裸写左右 → 提示改成画面左右/她的右手。"""

    warnings: list[str] = []
    people = _person_cast(spec)

    def name(key: str) -> str:
        member = people.get(key)
        return str(member.get("name")) if member else key

    from .continuity import previous_for

    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        sid = _text(shot.get("id"))
        # 轴线的「上一镜」和接缝一样按尾帧续接关系取（DESIGN §30）：接 S1 尾帧的 S3 和 S1 比，不和中间的 S2 比
        previous = previous_for(spec, shot)
        axis = shot.get("axis")
        if isinstance(axis, Mapping):
            side = _text(axis.get("side")).lower()
            if side and side not in AXIS_SIDES:
                warnings.append(f"shot {sid}: axis.side 只能是 left / right，现在是「{side}」")
            screen = _axis_screen(axis)
            bad = [k for k, v in screen.items() if v not in AXIS_SIDES]
            if bad:
                warnings.append(f"shot {sid}: axis.screen 里 {'、'.join(name(k) for k in bad)} 的画面侧只能是 left / right")
            anchors = _axis_anchors(axis)
            prev_axis = previous.get("axis") if previous is not None else None
            if isinstance(prev_axis, Mapping) and axis.get("break") is not True and anchors and anchors == _axis_anchors(prev_axis):
                pid = _text(previous.get("id"))
                prev_screen = _axis_screen(prev_axis)
                flipped = [k for k, v in screen.items() if v in AXIS_SIDES and prev_screen.get(k) in AXIS_SIDES and prev_screen[k] != v]
                for k in flipped:
                    warnings.append(f"shot {sid}: 越轴——{name(k)} 上一镜 {pid} 占画面{_SIDE_CN[prev_screen[k]]}，本镜占画面{_SIDE_CN[screen[k]]}，"
                                    "轴线锚点没变也没标 break；正反打要互补（甲在左看向右，乙就在右看向左），"
                                    "要么回到同侧，要么标 break: true 并从这镜起沿用新的机位侧")
                prev_side = _text(prev_axis.get("side")).lower()
                if not flipped and side in AXIS_SIDES and prev_side in AXIS_SIDES and side != prev_side:
                    warnings.append(f"shot {sid}: 机位从轴线{_SIDE_CN[prev_side]}侧换到了{_SIDE_CN[side]}侧（锚点同上一镜 {pid}）却没标 break——"
                                    "有意越轴就标 break: true，否则机位回到同侧")
        blob = _shot_blob(shot, "shot", "art", "motion")
        if blob and _CJK.search(blob):
            hits = sorted({m.group(0) for m in BARE_LR.finditer(blob)})
            if hits:
                warnings.append(f"shot {sid}: 「{'、'.join(hits)}」——裸写的左右一律指画面左右；指画面就写「画面左/右」，指人物自身写「她的右手」")
    return warnings


def check_overlays(spec: Mapping[str, Any]) -> list[str]:
    """交付面遮挡区（DESIGN §27）：可读文字的 position 落在平台的状态栏/互动栏/文案区里，或者字幕块落在遮挡区里 → 告警。
    位置描述只做粗略映射（「底部」「右下角」「中央偏上」），一个方位词都没有的不判。没配遮挡区的项目什么都不报。"""

    from . import overlays
    from . import subtitles

    regions = overlays.regions_for(spec)
    if not regions:
        return []
    try:
        from .prompts import readable_text_entries
    except Exception:  # noqa: BLE001
        readable_text_entries = None  # type: ignore[assignment]
    warnings: list[str] = []
    has_lines = False
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        sid = _text(shot.get("id"))
        has_lines = has_lines or bool(shot.get("lines"))
        if readable_text_entries is None or not shot.get("readable_text"):
            continue
        for entry in readable_text_entries(shot):
            rect = overlays.position_rect(entry["position"])
            if rect is None:
                continue
            hits = overlays.covering(rect, regions)
            if hits:
                labels = "/".join(r["label"] for r in hits)
                warnings.append(f"shot {sid}: 可读文字「{entry['text']}」放在「{entry['position']}」，会被交付面的「{labels}」挡住"
                                "——挪到安全区或换载体")
    if has_lines:
        style = subtitles.effective_style(spec.get("subtitle") if isinstance(spec.get("subtitle"), Mapping) else None)
        hits = overlays.covering(overlays.subtitle_rect(style), regions)
        if hits:
            labels = "/".join(r["label"] for r in hits)
            bands = [r for r in hits if float(r["w"]) >= 0.5]   # 横条才谈"调边距"；碰到竖栏是字幕太宽
            if not bands:
                fix = "缩窄字幕（减小字号或行宽）"
            elif str(style.get("position") or "bottom") == "top":
                fix = "把字幕改到底部，或调大 top_margin_ratio 避开"
            else:
                needed = max(int(round((1.0 - float(r["y"])) * 100)) for r in bands)
                fix = f"把字幕改到顶部，或把底部安全边距调到 {needed}% 以上（现在 {float(style.get('safe_bottom_percent') or 0):g}%）"
            warnings.append(f"全集：字幕在画面{'顶部' if str(style.get('position') or 'bottom') == 'top' else '底部'}，落在交付面的「{labels}」里"
                            f"——{fix}，或换遮挡区预设")
    return warnings


# ---- 观众可见性 / 揭示保护（DESIGN §28）
# fact 拆成「有辨识度的词」：按标点/空格切，≥2 字的才算（「是」「的」这种一个字的不算）；整句本身也算一个词。
_FACT_SPLIT = re.compile(r"[\s，。、；：！？,.;:!?「」『』《》〈〉()（）\[\]【】\-—–_/\\'\"“”‘’]+")
WITHHOLD_TOKEN_MIN = 2


def _fact_tokens(fact: str) -> list[str]:
    tokens = [tok for tok in _FACT_SPLIT.split(fact) if len(tok) >= WITHHOLD_TOKEN_MIN]
    return list(dict.fromkeys([fact, *tokens]))


def _withhold_blob(shot: Mapping[str, Any]) -> str:
    """揭示检查看的正文：美术、动作、台词（中英）、beats 三栏。shot（景别机位）和 withhold 自己不算。"""

    parts = [_text(shot.get(k)) for k in ("art", "motion")]
    for line in shot.get("lines") or ():
        if isinstance(line, Mapping):
            parts.extend(_text(line.get(k)) for k in ("zh", "en"))
    for beat in shot.get("beats") or ():
        if isinstance(beat, Mapping):
            parts.extend(_text(beat.get(k)) for k in ("picture", "performance", "sound"))
    return " ".join(p for p in parts if p)


def check_withhold(spec: Mapping[str, Any]) -> list[str]:
    """揭示保护：形状；until 指向不存在 / 不在本镜之后的镜；揭示点之前的镜（含声明镜）正文写到了事实本身或它的
    有辨识度的词；泄露载体又被声明成必须可读的文字。到了 until 及之后的镜不查。"""

    try:
        from .prompts import readable_text_entries
    except Exception:  # noqa: BLE001
        return []
    warnings: list[str] = []
    shots = [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]
    ids = [_text(s.get("id")) for s in shots]
    order = {sid: i for i, sid in enumerate(ids)}
    reported: set[tuple[str, str]] = set()   # (镜, 事实) 一镜只报一次
    for w_index, shot in enumerate(shots):
        raw = shot.get("withhold")
        if not raw:
            continue
        sid = ids[w_index]
        if not isinstance(raw, list):
            warnings.append(f"shot {sid}: withhold 必须是列表：[{{\"fact\": \"凶手是陈默\", \"carrier\": \"脸\", \"until\": \"S8\"}}]")
            continue
        for index, item in enumerate(raw):
            fact = _text(item.get("fact")) if isinstance(item, Mapping) else ""
            if not fact:
                warnings.append(f"shot {sid}: withhold[{index}] 写法不对——fact 填观众此刻还不能知道的事实，carrier 填会泄露它的载体（脸 / 戒指 / 台词），"
                                "until 填从哪一镜起可以揭示（留空 = 整集不揭示）")
                continue
            carrier = _text(item.get("carrier"))
            until = _text(item.get("until"))
            end = len(shots)
            where = "这一集都不揭示"
            if until:
                if until not in order:
                    warnings.append(f"shot {sid}: 「{fact}」的揭示点 until 指向不存在的镜 {until}——改成现有镜号，或留空表示整集不揭示")
                elif order[until] <= w_index:
                    warnings.append(f"shot {sid}: 「{fact}」的揭示点 until（{until}）不在本镜之后——揭示要发生在保护开始之后；改成后面的镜号，或留空表示整集不揭示")
                    continue   # 范围说不清，编译器也不会注入；先把揭示点改对
                else:
                    end = order[until]
                    where = f"揭示要等到 {until}"
            tokens = _fact_tokens(fact)
            for target in shots[w_index:end]:
                tid = _text(target.get("id"))
                blob = _withhold_blob(target)
                if blob and (tid, fact) not in reported and any(tok in blob for tok in tokens):
                    reported.add((tid, fact))
                    warnings.append(f"shot {tid}: 正文写到了「{fact}」的内容，但{where}——删掉或改成不露信息的写法")
                if carrier:
                    for entry in readable_text_entries(target):
                        surface = entry["surface"]
                        if surface == carrier or surface in carrier or carrier in surface:
                            warnings.append(f"shot {tid}: 「{surface}」既是「{fact}」的泄露载体（{carrier}），又被声明为画面里必须可读的文字——"
                                            "观众会直接读到；删掉这条可读文字，或换一个载体")
                            break
    return warnings


# ---- 单集契约（DESIGN §32）
CONTRACT_HOOK_SHOTS = 2      # 钩子要在前两镜就看得见：drama-skills「钩子是观众能立即理解的未稳定局面」


def check_contract(spec: Mapping[str, Any]) -> list[str]:
    """契约 vs 正文：钩子没在前两镜出现、集尾没落在最后一镜、承诺兑现的事没有任何一镜写到 → 全集级告警。
    判据是「正文里提到了吗」（``contract.mentioned``：原句 / 有辨识度的词 / 片段命中比例），没有契约的项目什么都不报。"""

    from .contract import is_deferred, mentioned, shot_text

    contract = spec.get("episode_contract")
    if not contract:
        return []
    if not isinstance(contract, Mapping):
        return ["全集：episode_contract 要是对象（premise / hook / cliffhanger / must_pay_off / carry_over）——改成对象，或删掉"]
    shots = [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]
    if not shots:
        return []
    warnings: list[str] = []
    hook = _text(contract.get("hook"))
    if hook:
        head = " ".join(shot_text(s) for s in shots[:CONTRACT_HOOK_SHOTS])
        if not mentioned(hook, head):
            warnings.append(f"全集：契约里的钩子「{hook}」在前 {CONTRACT_HOOK_SHOTS} 镜的正文里找不到——开场要让观众立刻看到这个未稳定局面；"
                            "把它写进前两镜的画面/动作/台词，或改契约")
    cliff = _text(contract.get("cliffhanger"))
    if cliff:
        last = shots[-1]
        if not mentioned(cliff, shot_text(last)):
            warnings.append(f"全集：契约里的集尾「{cliff}」在最后一镜 {_text(last.get('id'))} 的正文里找不到——出去的压力要落在最后一镜上；"
                            "把它写进最后一镜，或改契约")
    blob = " ".join(shot_text(s) for s in shots)
    for item in contract.get("must_pay_off") or ():
        item = _text(item)
        if not item or is_deferred(item):   # 「……（有意留白）」是作者明说这集不兑现，不算没兑现
            continue
        if not mentioned(item, blob):
            warnings.append(f"全集：契约承诺本集兑现的「{item}」在任何一镜的正文里都找不到——本集自己的局部结果不能全推到下一集；"
                            "写进某一镜，或从契约里删掉")
    return warnings


# ---- 形态卡（DESIGN §31）
# 竖屏上认不出人脸的景别；之后两镜内要回到更近的景别
WIDE_WORDS = ("大全景", "远景", "大远景", "全景")
WIDE_WORDS_EN = ("wide shot", "long shot", "extreme wide", "establishing", "full shot")
CLOSE_WORDS = ("特写", "近景", "中近景", "中景", "大特写", "胸口以上", "半身", "自拍", "近拍")
CLOSE_WORDS_EN = ("close-up", "closeup", "close up", "medium shot", "medium close", "mid shot", "bust shot", "waist up", "selfie", "macro")
FORM_CLOSE_WITHIN = 2
# 台词没写字幕时间时按方言的「开头 0.8 秒内开口」估
FORM_LINE_START_DEFAULT = 0.8
# 每分钟镜数只在全集够这么长时才算（短片段外推没有意义）
FORM_RATE_MIN_SECONDS = 30.0


def _aspect_orientation(value: Any) -> str:
    """'9:16' → portrait，'16:9' → landscape，'1:1' → square；读不出（adaptive / 空）→ ''。"""

    text = str(value or "").strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[:x×/]\s*(\d+(?:\.\d+)?)", text)
    if not m:
        return ""
    w, h = float(m.group(1)), float(m.group(2))
    if not w or not h:
        return ""
    return "portrait" if h > w else ("landscape" if w > h else "square")


def _shot_is(shot: Mapping[str, Any], words_zh: tuple[str, ...], words_en: tuple[str, ...]) -> bool:
    text = _text(shot.get("shot"))
    if not text:
        return False
    if _CJK.search(text):
        return any(w in text for w in words_zh)
    low = text.lower()
    return any(w in low for w in words_en)


def _first_event_seconds(shot: Mapping[str, Any]) -> float | None:
    """首镜里第一件观众能感知的事发生在第几秒：台词开口（字幕起点或默认 0.8s）、第一条 beat 的起点。都没有 = None。"""

    candidates: list[float] = []
    for line in shot.get("lines") or ():
        if not isinstance(line, Mapping):
            continue
        start = line.get("subtitle_start_seconds")
        try:
            candidates.append(float(start) if start is not None else FORM_LINE_START_DEFAULT)
        except (TypeError, ValueError):
            candidates.append(FORM_LINE_START_DEFAULT)
    for beat in shot.get("beats") or ():
        if isinstance(beat, Mapping):
            start, _end = _parse_span(beat.get("t"))
            if start is not None:
                candidates.append(float(start))
    return min(candidates) if candidates else None


def check_form(spec: Mapping[str, Any]) -> list[str]:
    """形态卡的必带项里能静态核对的几条（DESIGN §31）。没写 form 的 spec 什么都不报。

    ① 画幅与形态不符（image_params / video_params 的 aspect_ratio、delivery 的宽高）；② 首镜超过钩子窗口且窗口内既没开口
    也没节拍；③ 平均单镜时长跑出卡的区间、每分钟镜数超上限；④ 卡要求字幕而 subtitle 明确关了；⑤ 竖屏形态里用了远景/
    大全景，之后两镜内没回到更近的景别。全是告警，每条给改法。
    """

    from .forms import form_for_spec, subtitles_off

    card = form_for_spec(spec)
    if not card:
        return []
    name = str(card.get("name") or card.get("id"))
    d = card.get("defaults") or {}
    warnings: list[str] = []
    shots = [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]

    # ① 画幅
    want = _aspect_orientation(d.get("aspect_ratio"))
    if want:
        label = {"portrait": "竖屏", "landscape": "横屏", "square": "方形"}
        for block, where in (("image_params", "生图参数"), ("video_params", "视频参数")):
            params = spec.get(block)
            if not isinstance(params, Mapping):
                continue
            got = _aspect_orientation(params.get("aspect_ratio"))
            if got and got != want:
                warnings.append(f"全集：形态「{name}」是{label[want]}（{d.get('aspect_ratio')}），但{where}的画幅是 {params.get('aspect_ratio')}"
                                f"——改画幅，或换形态")
        delivery = spec.get("delivery")
        if isinstance(delivery, Mapping) and delivery.get("width") and delivery.get("height"):
            try:
                got = _aspect_orientation(f"{float(delivery['width'])}:{float(delivery['height'])}")
            except (TypeError, ValueError):
                got = ""
            if got and got != want:
                warnings.append(f"全集：形态「{name}」是{label[want]}（{d.get('aspect_ratio')}），但交付尺寸是 {delivery['width']}×{delivery['height']}"
                                f"——改交付尺寸，或换形态")

    # ② 首镜钩子窗口
    hook = float(d.get("hook_seconds") or 0)
    if hook and shots:
        first = shots[0]
        sid = _text(first.get("id"))
        try:
            dur = float(first.get("dur") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        if dur > hook:
            at = _first_event_seconds(first)
            if at is None or at >= hook:
                what = "既没开口也没节拍" if at is None else f"第一件事在 {at:g}s 才发生"
                warnings.append(f"shot {sid}: 首镜 {dur:g}s，前 {hook:g} 秒{what}——形态「{name}」要求前 {hook:g} 秒有钩子；"
                                f"把开口或动作提前，或把首镜拆短")

    # ③ 单镜时长区间 / 每分钟镜数
    durs: list[float] = []
    for shot in shots:
        try:
            durs.append(float(shot.get("dur") or 0))
        except (TypeError, ValueError):
            continue
    span = list(d.get("shot_seconds") or ())
    if durs and len(span) == 2:
        lo, hi = float(span[0]), float(span[1])
        avg = sum(durs) / len(durs)
        if avg > hi:
            warnings.append(f"全集：平均每镜 {avg:.1f}s，超出形态「{name}」的 {lo:g}–{hi:g}s——把长镜拆开，或换形态")
        elif avg < lo:
            warnings.append(f"全集：平均每镜 {avg:.1f}s，低于形态「{name}」的 {lo:g}–{hi:g}s——合并碎镜，或换形态")
    cap = d.get("max_shots_per_minute")
    total = sum(durs)
    if cap and total >= FORM_RATE_MIN_SECONDS:   # 十几秒的片段外推到一分钟没有意义，够半分钟才算
        per_minute = len(durs) / (total / 60.0)
        if per_minute > float(cap) + 1e-6:
            warnings.append(f"全集：每分钟 {per_minute:.0f} 镜，超过形态「{name}」的上限 {cap} 镜——合并碎镜，或换形态")

    # ④ 字幕
    if d.get("subtitles") and subtitles_off(spec.get("subtitle")):
        warnings.append(f"全集：形态「{name}」要求字幕常开，但字幕样式里关掉了字幕——打开字幕，或换形态")

    # ⑤ 竖屏远景之后两镜内要回近景
    if d.get("portrait"):
        for index, shot in enumerate(shots):
            if not _shot_is(shot, WIDE_WORDS, WIDE_WORDS_EN) or _shot_is(shot, CLOSE_WORDS, CLOSE_WORDS_EN):
                continue
            following = shots[index + 1: index + 1 + FORM_CLOSE_WITHIN]
            if not any(_shot_is(s, CLOSE_WORDS, CLOSE_WORDS_EN) for s in following):
                sid = _text(shot.get("id"))
                warnings.append(f"shot {sid}: 竖屏形态「{name}」里用了远景，之后 {FORM_CLOSE_WITHIN} 镜内没有更近的景别"
                                f"——竖屏上远景里人脸认不出，紧接一个近景或特写")
    return warnings


# ---- 参考槽位用途与尾帧续接（DESIGN §30）
REFERENCE_TARGET_WORDS = ("prev_frame", "tail", "prop")
# 远景尾帧接近景的检查（DESIGN §30）：源镜收在大全景 / 远景、本镜是近景 / 特写时，提示先出一张中景桥接帧
WIDE_WORDS = ("大全景", "大远景", "远景", "全景")
TIGHT_WORDS = ("大特写", "特写", "中近景", "近景")
WIDE_WORDS_EN = ("wide shot", "wide", "long shot", "establishing", "full shot")
TIGHT_WORDS_EN = ("extreme close", "close-up", "closeup", "close up", "medium close", "macro")


def _max_reference_images(spec: Mapping[str, Any], image_adapter: str = "") -> int | None:
    """模型允许的参考图上限：spec 里写了具体的出图模型 id、能力表按 (角色, 来源, 模型) 记了 max_reference_images 才有。

    来源（``image_adapter``）由调用方从「模型设置」里带进来——lint 自己不读设置。没给来源就不报上限：
    同一个模型 id 在不同来源下上限不同（gpt-image 在 OpenAI 是 16 张，有的来源把参考图原样全发、自己不限数，
    能力表也不记），跨来源按前缀猜一个数再以确定口吻写进问题卡是误导（ADP-09）。
    ``@image`` 这种指向设置的写法在这里不解析，能力检查接口那边按设置里的模型再查一次。"""

    if not image_adapter:
        return None
    try:
        from genkit.capabilities import max_reference_images
    except Exception:  # noqa: BLE001
        return None
    model = str((spec.get("image_params") or {}).get("model") or "").strip()
    if not model or model.startswith("@"):
        return None
    try:
        return max_reference_images(model, adapter=image_adapter)
    except Exception:  # noqa: BLE001
        return None


def _tail_of(shot: Mapping[str, Any]) -> tuple[str, str] | None:
    from .tail import tail_ref

    return tail_ref(shot)


def check_reference_roles(spec: Mapping[str, Any], *, image_adapter: str = "") -> list[str]:
    """reference_roles 的形状；control 与 ignore 同一方面；prev_frame / tail 指向本镜没有的依赖槽；同一槽写了两条；
    条数超过本镜实际会带的参考图数，或超过模型允许的参考图上限（只在给了出图来源 ``image_adapter`` 时查）。"""

    try:
        from .prompts import reference_role_entries, reference_slot_plan
    except Exception:  # noqa: BLE001
        return []
    warnings: list[str] = []
    cap = _max_reference_images(spec, image_adapter)
    cap_model = str((spec.get("image_params") or {}).get("model") or "").strip()
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping) or not shot.get("reference_roles"):
            continue
        sid = _text(shot.get("id"))
        raw = shot.get("reference_roles")
        if not isinstance(raw, list):
            warnings.append(f"shot {sid}: reference_roles 必须是列表：[{{\"target\": \"cast:lin\", \"use\": \"只取脸\", \"control\": [\"脸\"], \"ignore\": [\"服装\"]}}]")
            continue
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping) or not _text(item.get("target")):
                warnings.append(f"shot {sid}: reference_roles[{index}] 写法不对——target 填参考图（prev_frame / tail / prop / cast:<id>），use 填取它什么，control / ignore 填控制与不得控制的方面")
        entries = reference_role_entries(shot)
        tail = _tail_of(shot)
        has_dependency = bool(_text(shot.get("ref_frame"))) or (tail is not None and tail[1] == "reference")
        seen_targets: set[str] = set()
        for role in entries:
            target = role["target"]
            both = [a for a in role["control"] if a in set(role["ignore"])]
            if both:
                warnings.append(f"shot {sid}: 参考图 {target} 的「{'、'.join(both)}」既在控制里又在不得控制里——留一边，模型没法同时听两句")
            if target == "prev_frame" and not has_dependency:
                warnings.append(f"shot {sid}: 参考图用途写了 prev_frame，但本镜没有 ref_frame / ref_tail——没有上一镜的参考槽，这条说明会指向不存在的图；删掉它或给本镜选参考镜")
            if target == "tail" and not (tail is not None and tail[1] == "reference"):
                warnings.append(f"shot {sid}: 参考图用途写了 tail，但本镜没有作参考图的尾帧续接——删掉它，或把尾帧续接设成「作参考图」")
            if target in seen_targets:
                warnings.append(f"shot {sid}: 参考图 {target} 写了两条用途——合成一条，后一条会盖住前一条的意思")
            seen_targets.add(target)
        try:
            slots = len({s["index"] for s in reference_slot_plan(spec, sid)})
        except Exception:  # noqa: BLE001
            slots = None
        if slots is not None and len(entries) > slots:
            warnings.append(f"shot {sid}: 写了 {len(entries)} 条参考图用途，本镜实际只带 {slots} 张参考图——多出的说明指向不存在的图；按依赖槽 → 角色定妆 → 道具的顺序核对")
        if cap is not None and slots is not None and slots > cap:
            warnings.append(f"shot {sid}: 本镜要带 {slots} 张参考图，出图模型 {cap_model or '?'} 最多收 {cap} 张——超出的会被丢掉，槽位编号也就不准；减少出场锚图或去掉道具参考")
    return warnings


def _framing_words(shot_text: str, zh_words: tuple[str, ...], en_words: tuple[str, ...]) -> str:
    if _CJK.search(shot_text):
        return next((w for w in zh_words if w in shot_text), "")
    low = shot_text.lower()
    return next((w for w in en_words if w in low), "")


def check_tail_relay(spec: Mapping[str, Any]) -> list[str]:
    """ref_tail 的形状与来源（源镜要在本镜之前；与 ref_frame 指向同一镜）；景别下限：源镜收在大全景 / 远景、本镜是近景 /
    特写 → 尾帧里的脸太小，拿来接近景会走样，提示在两镜中间先出一张中景桥接帧（场景照源镜最后一帧，脸照定妆图）。"""

    warnings: list[str] = []
    shots = [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]
    order = {_text(s.get("id")): i for i, s in enumerate(shots)}
    by_id = {_text(s.get("id")): s for s in shots}
    for index, shot in enumerate(shots):
        if shot.get("ref_tail") is None:
            continue
        sid = _text(shot.get("id"))
        tail = _tail_of(shot)
        if tail is None:
            warnings.append(f"shot {sid}: ref_tail 写法不对——填源镜镜号，或 {{\"shot\": \"S1\", \"mode\": \"reference\" / \"direct\"}}")
            continue
        source, mode = tail
        if source not in order:
            warnings.append(f"shot {sid}: 尾帧续接的源镜 {source} 不存在——改成现有镜号")
            continue
        if order[source] >= index:
            warnings.append(f"shot {sid}: 尾帧续接的源镜 {source} 不在本镜之前——尾帧要等源镜的视频过检，只能接前面的镜")
            continue
        ref_frame = _text(shot.get("ref_frame"))
        if ref_frame and ref_frame != source:
            warnings.append(f"shot {sid}: 尾帧续接接的是 {source}，参考镜（ref_frame）却是 {ref_frame}——两者要指向同一镜，尾帧会取代参考镜的首帧")
        source_shot = by_id[source]
        wide = _framing_words(_text(source_shot.get("shot")), WIDE_WORDS, WIDE_WORDS_EN)
        tight = _framing_words(_text(shot.get("shot")), TIGHT_WORDS, TIGHT_WORDS_EN)
        if wide and tight:
            how = "直接当首帧" if mode == "direct" else "作参考图"
            warnings.append(f"shot {sid}: 源镜 {source} 收在「{wide}」，本镜是「{tight}」，尾帧{how}时脸在画面里太小、五官看不清，长相容易走样——"
                            f"先在两镜中间出一张中景桥接帧再接这一镜：场景照源镜最后一帧，脸照定妆图")
    return warnings


# ---- 题材 × 镜头风格搭配（DESIGN §48）
def check_genre_camera(spec: Mapping[str, Any]) -> list[str]:
    """题材卡与镜头风格 profile 的搭配：只有这档落在该题材的 ``avoid`` 里才给一条软告警（两边的名字 + 理由 + 常配的档）；
    推荐的、既不推荐也不回避的组合都不说（不唠叨）。``spec.genre`` 没有 / 认不出、或没写 ``camera_profile`` → 什么都不做。
    搭配表（genre_camera_pairing.json）是本仓库人工设定的提示（why 里对题材的概括沿用题材卡的说法），没有成片对比，
    所以只告警、不拦截。"""

    raw = spec.get("genre")
    if isinstance(raw, Mapping):   # 宽容：{id, name} 形状也认
        raw = raw.get("id") or raw.get("name")
    genre_key = raw.strip() if isinstance(raw, str) else ""
    profile = _text(spec.get("camera_profile"))
    if not genre_key or not profile:
        return []
    try:
        from .pairing import pairing_for, pairing_warning
        from .scriptgen import find_genre

        card = find_genre(genre_key)
    except Exception:  # noqa: BLE001 - 题材卡 / 搭配表读坏了不该让整个 lint 挂掉
        return []
    if not card:
        return []
    entry = pairing_for(card["id"])
    if profile not in entry["avoid"]:
        return []
    return [pairing_warning(str(card["name"]), profile, entry)]


def check_video_count(spec: Mapping[str, Any]) -> list[str]:
    """``video_params.count`` 写了 1 以外的值：流水线每镜只保留一条结果，适配器提交时一律只要 1 条（ADP-11），
    写几都不起作用。只提醒、不拦——spec 层拒绝的话，老项目（MiniMax-H3 曾放行 2 / 4）连打开都打不开。"""

    params = spec.get("video_params")
    if not isinstance(params, Mapping) or "count" not in params:
        return []
    count = params["count"]
    if not isinstance(count, bool) and isinstance(count, int) and count == 1:
        return []
    shown = json.dumps(count, ensure_ascii=False)
    return [f"video_params.count 写的是 {shown}：流水线每镜只保留一条结果，提交时一律只要 1 条，这一项不起作用，可以删掉"]


def check_all(spec: Mapping[str, Any], *, image_adapter: str = "") -> list[str]:
    """``image_adapter``：「模型设置」里生图角色当前的来源 id，参考图上限按它查；不给就不查上限。"""

    return (
        check_shot_id_case(spec) + check_cast_mentions(spec) + check_generability(spec) + check_prompt_hygiene(spec)
        + check_purpose(spec) + check_camera_columns(spec) + check_beat_coverage(spec) + check_prompt_length(spec)
        + check_locks(spec) + check_readable_text(spec) + check_variants(spec) + check_axis(spec) + check_overlays(spec)
        + check_withhold(spec) + check_contract(spec) + check_form(spec)
        + check_reference_roles(spec, image_adapter=image_adapter) + check_tail_relay(spec) + check_genre_camera(spec)
        + check_text_carriers(spec) + check_camera_motion_single(spec) + check_reaction_after_impact(spec) + check_voice_over(spec)
        + check_shot_duration_ceiling(spec) + check_relation_boundaries(spec) + check_video_count(spec)
    )


__all__ = ["check_shot_id_case", "check_cast_mentions", "check_generability", "check_prompt_hygiene", "check_prompt_length", "check_purpose",
           "check_camera_columns", "check_beat_coverage", "check_locks", "surface_present", "check_readable_text", "check_variants", "check_axis", "check_overlays", "check_withhold", "check_contract", "check_form", "check_reference_roles", "check_tail_relay", "check_genre_camera",
           "check_text_carriers", "check_camera_motion_single", "check_reaction_after_impact", "check_voice_over",
           "check_shot_duration_ceiling", "check_relation_boundaries", "check_video_count", "check_all", "PURPOSES", "PURPOSE_LABELS", "SCENE_TYPES", "SCENE_TYPE_LABELS"]
