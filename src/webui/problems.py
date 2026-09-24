"""把静态检查的告警字符串变成「问题卡」（drama-skills 的 Problem card 四要素：在哪、影响什么、要达到什么、怎么做）。

检查函数仍返回字符串（CLI 和老测试都靠它），这里只在接口边界解析前缀：
``shot S1: …`` / ``cast lin: …`` / ``scene S02: …`` / ``block B01: …`` / ``全集：…`` / ``全篇：…``；
正文里的「——」把"影响"和"怎么做"分开。
分类按关键词猜一个 kind，给界面上色和过滤用；猜不出就 other。不露内部术语。
"""

from __future__ import annotations

import re
from typing import Any

_SHOT = re.compile(r"^shot\s+(\S+?)[:：]\s*(.*)$", re.DOTALL)
_CAST = re.compile(r"^cast\s+(\S+?)[:：]\s*(.*)$", re.DOTALL)
# 绘本（DESIGN §39）：``page P03: …`` 是一页，``全书：…`` 是整本（跟「全集」一样落在 episode 级）
_PAGE = re.compile(r"^page\s+(\S+?)[:：]\s*(.*)$", re.DOTALL)
# 剧本层（DESIGN §32）：``scene S02: …`` 是一场
_SCENE = re.compile(r"^scene\s+(\S+?)[:：]\s*(.*)$", re.DOTALL)
# 出图（DESIGN §41）：``image I01: …`` 是一张图或一张风格卡
_IMAGE = re.compile(r"^image\s+(\S+?)[:：]\s*(.*)$", re.DOTALL)
# 单条视频（DESIGN §43）：``beat B02: …`` 是一个节拍，``全片：…`` 是整条片子
_BEAT = re.compile(r"^beat\s+(\S+?)[:：]\s*(.*)$", re.DOTALL)
# AI 播客（DESIGN §50）：``segment G02: …`` 是一个环节，``全期：…`` 是整期
_SEGMENT = re.compile(r"^segment\s+(\S+?)[:：]\s*(.*)$", re.DOTALL)
# 剧本工作台（docs/scriptlab-spec.md §4）：``block B01: …`` 是一段，``全篇：…`` 跟「全集」一样落在 episode 级
_BLOCK = re.compile(r"^block\s+(\S+?)[:：]\s*(.*)$", re.DOTALL)
_EPISODE = re.compile(r"^(全集|整集|全书|全片|全期|全篇|episode|book|clip|podcast)[:：]\s*(.*)$", re.DOTALL | re.IGNORECASE)
# 上面这些头的名字，顺序跟解析时的 or 链一致。i18n_rules._HEADS 要给每一种头备一条英文规则，
# 不然英文界面上这一类问题卡的正文整句回落中文（tests/test_problem_cards.py 守着）
HEAD_NAMES: tuple[str, ...] = ("shot", "cast", "page", "image", "scene", "beat", "segment", "block")

KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 剧本工艺审稿与按题材审稿（DESIGN §49）排最前：正文是模型写的理由和改法，里面什么分类的关键词都可能出现——
    # 张力组的理由里几乎必然有「分歧」，所以必须排在播客前面；两者的 needle 都是独一无二的句头，别的告警里不会出现
    ("craft", ("按工艺清单审稿",)),
    ("genre", ("按题材卡「",)),
    # AI 播客（DESIGN §50）：告警里有「台词」「时长」「音色」这些别的分类的词，排在它们前面；
    # 「按真人感清单审稿」是播客真人感审稿（podcast/review.py）独一无二的句头，正文是模型写的理由和改法，放最前
    ("podcast", ("按真人感清单审稿", "环节", "书面语", "机械轮流", "分歧", "填充词", "播音腔", "一人独大", "没人插话", "听众问题", "复述问题", "收尾在总结", "冷开场", "播客", "主播")),
    # 题材 × 镜头风格搭配（DESIGN §48）：理由里有「运镜」「表演」「定机位」这些别的分类的词，排在它们前面
    ("pairing", ("配镜头风格「",)),
    # 形态卡（DESIGN §31）排最前：它的告警里会出现「景别」「字幕」「时长」这些别的分类的关键词
    ("form", ("形态「",)),
    # 绘本文本检查（DESIGN §39）也排前面：告警里有「文字」「字幕」这些别的分类的词
    ("book", ("翻页钩", "拟声词", "KB 方向", "插画里", "目标词", "重复母题", "群像页", "分页", "岁每页", "岁的书")),
    # 出图风格卡与图像方言（DESIGN §41）：告警里有「提示词」「文字」这些别的分类的词，排在它们前面
    ("image", ("GPT Image", "Nano Banana", "色板", "图像方言", "风格卡", "否定式描述", "没加引号")),
    # 剧本层（DESIGN §32）：逐场编辑器的告警里有「台词」「时间」「节拍」这些别的分类也在用的词，排在它们前面
    ("screenplay", ("这一场", "场景标题", "开场钩子", "集尾压力", "剧本里", "场次太碎")),
    # 单条视频（DESIGN §43）：告警里有「重叠」「时长」「文字」这些别的分类的词，排在它们前面（色板仍归「出图」）
    ("clip", ("时间轴", "节拍", "最后一帧", "连接词", "文字政策", "首尾相接")),
    # 揭示保护（DESIGN §28）排前面：它的告警里会出现「正文」「台词」「可读文字」这些别的分类的关键词
    ("withhold", ("揭示", "withhold", "泄露载体")),
    # 单集契约（DESIGN §32）也排在前面：告警里会出现「正文」「台词」这些别的分类的词
    ("contract", ("契约", "episode_contract")),
    ("overlay", ("交付面", "遮挡区")),
    # 参考槽位用途 / 尾帧续接（DESIGN §30）：告警里会出现「参考镜」「首帧」这些连续性/正文分类的词，排在它们前面
    ("reference", ("参考图", "尾帧", "reference_roles", "ref_tail")),
    # 「整段没有一个具体例子」「回答只有 N 字」这两句里没有「台词」二字，不点名就落进「其他」——
    # 它们说的都是台词本身（播客与剧本工作台都会出），归「台词」比归「播客」准：工作台的通用稿也判这两条
    ("dialogue", ("台词", "对白", "语速", "发声", "口齿", "偏紧", "装不下", "旁白", "具体例子", "结论加原因")),
    ("continuity", ("entry_state", "exit_state", "入口", "出口", "接缝", "ref_frame", "参考链", "锁", "一字不差", "出场角色", "cast 没有", "cast 列了")),
    ("performance", ("情绪形容词", "表演", "内部状态", "否定式动作", "拦截", "手部动作")),
    ("camera", ("景别", "机位", "运镜", "镜头功能", "功能重复", "camera")),
    ("timing", ("beat", "时长", "留白", "覆盖", "重叠")),
    ("text", ("镜号", "文件名", "版本号", "同上", "提示词", "可读文字", "文字")),
)
KIND_LABELS = {"craft": "工艺", "genre": "题材", "pairing": "搭配", "form": "形态", "book": "绘本", "image": "出图", "clip": "单条视频", "withhold": "揭示", "contract": "契约", "screenplay": "剧本", "dialogue": "台词", "continuity": "连续性", "performance": "表演", "camera": "镜头", "timing": "时长", "text": "正文", "overlay": "遮挡区", "reference": "参考图", "podcast": "播客", "other": "其他"}


def classify(text: str) -> str:
    for kind, needles in KINDS:
        if any(n in text for n in needles):
            return kind
    return "other"


def problem_card(text: str) -> dict[str, Any]:
    scope, target, body = "episode", "", text.strip()
    m = _SHOT.match(body)
    if m:
        scope, target, body = "shot", m.group(1), m.group(2)
    else:
        m = (_CAST.match(body) or _PAGE.match(body) or _IMAGE.match(body) or _SCENE.match(body)
             or _BEAT.match(body) or _SEGMENT.match(body) or _BLOCK.match(body))
        if m:
            head = next(h for h in HEAD_NAMES if body.startswith(h))
            scope, target, body = head, m.group(1), m.group(2)
        else:
            m = _EPISODE.match(body)
            if m:
                body = m.group(2)
    impact, fix = body, ""
    for sep in ("——", "--"):
        if sep in body:
            impact, fix = body.split(sep, 1)
            break
    return {"scope": scope, "target": target, "kind": classify(text), "impact": impact.strip().rstrip("；;"), "fix": fix.strip(), "text": text}


def problem_cards(warnings: list[str]) -> list[dict[str, Any]]:
    return [problem_card(w) for w in warnings if isinstance(w, str) and w.strip()]


def group_by_target(cards: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        key = f"{card['scope']}:{card['target']}" if card["target"] else "episode"
        out.setdefault(key, []).append(card)
    return out


__all__ = ["classify", "problem_card", "problem_cards", "group_by_target", "KIND_LABELS", "HEAD_NAMES"]
