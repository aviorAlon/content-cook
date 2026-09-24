"""问题卡类型标签：前端自己有一份镜像（frontend/src/problems.js 的 PROBLEM_KINDS，各页都引它），
跟后端 problems.KIND_LABELS 对不上时，新类型的卡会在界面上显示成英文键名（2026-09-11：「搭配」卡显示成 pairing）。
这里守住两份表同步。"""

from __future__ import annotations

import re
from pathlib import Path

from webui.problems import KIND_LABELS

ROOT = Path(__file__).resolve().parents[1]


def _front_labels() -> dict[str, str]:
    text = (ROOT / "frontend" / "src" / "problems.js").read_text(encoding="utf-8")
    match = re.search(r"export const PROBLEM_KINDS = \{([^}]*)\}", text)
    assert match, "frontend/src/problems.js 里找不到 PROBLEM_KINDS"
    return dict(re.findall(r"(\w+): '([^']*)'", match.group(1)))


def test_frontend_covers_every_backend_kind():
    front = _front_labels()
    missing = sorted(set(KIND_LABELS) - set(front))
    assert not missing, f"problems.js 的 PROBLEM_KINDS 缺这些类型，界面上会显示英文键名：{missing}"


def test_frontend_labels_match_backend_wording():
    front = _front_labels()
    differ = {k: (front[k], KIND_LABELS[k]) for k in KIND_LABELS if k in front and front[k] != KIND_LABELS[k]}
    assert not differ, f"同一类型前后端中文标签不一致：{differ}"


def test_no_page_keeps_its_own_copy():
    """各页再抄一份就会漏掉后端新加的分类（2026-09-20：工作台抄了五个键，screenplay 卡显示成 screenplay）。"""

    views = (ROOT / "frontend" / "src" / "views").rglob("*.vue")
    offenders = sorted(p.name for p in views if re.search(r"const KIND_LABELS = \{[^}]*(dialogue|screenplay|pairing)", p.read_text(encoding="utf-8")))
    assert not offenders, f"这些页面自己抄了一份问题卡分类表，改成引 problems.js 的 PROBLEM_KINDS：{offenders}"


def test_every_label_has_an_english_translation():
    en = (ROOT / "frontend" / "src" / "locales").glob("en*.js")
    text = "\n".join(p.read_text(encoding="utf-8") for p in en)
    missing = sorted({label for label in KIND_LABELS.values() if f"'{label}':" not in text})
    assert not missing, f"这些类型标签没有英文：{missing}"
